from __future__ import annotations

import logging
import queue
import signal
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator

from collector.browser_tracker import BrowserTracker
from collector.git_tracker import GitActivity, GitTracker
from collector.idle_tracker import IdleState, IdleTracker
from collector.platforms import create_platform_provider
from collector.window_tracker import WindowTracker
from db.repository import ActivityRepository
from processor.session_builder import SessionBuilder
from services.activity_tagger import ActivityTagger
from services.logging_config import configure_logging as _configure_logging
from services.wellbeing import WellbeingPrompt, WellbeingService, WellbeingSettings
from services.wellbeing_delivery import WellbeingDelivery, read_control_commands
from services.wellbeing_ui import TkWellbeingUI
from workgraph.models import ActivitySample, utc_now

LOGGER = logging.getLogger(__name__)


class CollectorSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_path: str = "activity.db"
    poll_interval_seconds: float = 5.0
    idle_threshold_seconds: int = 300
    session_gap_seconds: int = 90
    browser_history_lookback_seconds: int = 600
    log_path: str = "logs/workgraph.log"
    log_level: str = "INFO"
    identity_path: str = "config/identity.json"
    dashboard_mode: Literal["standalone", "sync-client", "sync-server"] = "standalone"
    wellbeing: WellbeingSettings = Field(default_factory=WellbeingSettings)

    @field_validator("dashboard_mode", mode="before")
    @classmethod
    def normalize_dashboard_mode(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("poll_interval_seconds")
    @classmethod
    def poll_interval_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        return v

    @field_validator(
        "idle_threshold_seconds",
        "session_gap_seconds",
        "browser_history_lookback_seconds",
    )
    @classmethod
    def int_fields_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("value must be positive")
        return v


class CollectorService:
    def __init__(self, settings: CollectorSettings) -> None:
        self.settings = settings
        platform_provider = create_platform_provider()
        self._platform_provider = platform_provider
        self._platform_provider_started = False
        self.window_tracker = WindowTracker(platform_provider)
        self.browser_tracker = BrowserTracker(settings.browser_history_lookback_seconds)
        self.idle_tracker = IdleTracker(
            settings.idle_threshold_seconds, platform_provider
        )
        self.git_tracker = GitTracker()
        self.activity_tagger = ActivityTagger()
        self.session_builder = SessionBuilder(settings.session_gap_seconds)
        self.repository = ActivityRepository(
            settings.database_path,
            identity_path=settings.identity_path,
        )
        self.wellbeing_delivery = WellbeingDelivery()
        self._break_reminders: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.wellbeing = WellbeingService(
            settings.wellbeing,
            self._break_reminders.put,
            self.wellbeing_delivery.speak,
            max_sample_gap=max(60, settings.poll_interval_seconds * 3),
        )
        self._wellbeing_actions: queue.Queue[tuple[str, str | None]] = queue.Queue(
            maxsize=16
        )
        self._wellbeing_ui: TkWellbeingUI | None = None
        self._tk_root = None
        self._fallback_notified_at: dict[str, float] = {}
        self._idle_unavailable_warned = False
        self._window_unavailable_warned = False
        self._git_unavailable_warned = False
        self._browser_unavailable_warned = False
        self._running = False
        wellbeing = settings.wellbeing
        self._service_status = {
            "Activity collector": "Running",
            "Window detection": "Not checked",
            "Idle detection": "Not checked",
            "Browser tracking": "Enabled",
            "Git tracking": "Enabled",
            "Database": "Enabled",
            "Break reminders": "Enabled"
            if wellbeing.break_reminders_enabled
            else "Disabled",
            "Time announcements": "Enabled"
            if wellbeing.time_announcements_enabled
            else "Disabled",
            "Idle checks": "Enabled" if wellbeing.idle_check_enabled else "Disabled",
            "Workout reminders": "Enabled"
            if wellbeing.workout_reminders_enabled
            else "Disabled",
            "Water reminders": "Enabled"
            if wellbeing.water_reminders_enabled
            else "Disabled",
        }

    def run_forever(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        LOGGER.info("WorkGraph collector started")

        try:
            start_provider = getattr(self._platform_provider, "start", None)
            if callable(start_provider):
                start_provider()
                self._platform_provider_started = True
            if self.settings.dashboard_mode != "sync-server":
                try:
                    self._run_tk_loop()
                except Exception:
                    LOGGER.warning(
                        "Tk wellbeing UI unavailable; using desktop notifications",
                        exc_info=True,
                    )
                    self._close_tk_ui()
                    self._run_collector_loop()
            else:
                self._run_collector_loop()
        finally:
            close_provider = getattr(self._platform_provider, "close", None)
            if callable(close_provider):
                close_provider()
            self._close_tk_ui()
            self.flush()
            self.repository.close()
            self.wellbeing_delivery.close()
            LOGGER.info("WorkGraph collector stopped")

    def _run_collector_loop(self) -> None:
        announced = False
        while self._running:
            self.collect_once()
            if not announced:
                self._announce_started()
                announced = True
            time.sleep(self.settings.poll_interval_seconds)

    def _run_tk_loop(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        self._tk_root = root
        self._wellbeing_ui = TkWellbeingUI(
            root, self._wellbeing_actions, self.settings.wellbeing
        )
        root.after(0, self._collect_tk_tick)
        root.mainloop()

    def _collect_tk_tick(self) -> None:
        if not self._running:
            return
        try:
            self.collect_once()
            self._announce_started()
        except Exception:
            LOGGER.exception("Collector sample failed")
            self.stop()
            return
        if self._running and self._tk_root is not None:
            self._tk_root.after(
                max(1, round(self.settings.poll_interval_seconds * 1000)),
                self._collect_tk_tick,
            )

    def _announce_started(self) -> None:
        if self.settings.dashboard_mode != "sync-server" and not getattr(
            self, "_announced", False
        ):
            self.wellbeing_delivery.speak(
                "WorkGraph activity tracking started.", error_on_failure=True
            )
            self._announced = True
            if self._wellbeing_ui is not None:
                try:
                    self._wellbeing_ui.show_greeting(
                        datetime.now().hour, self._greeting_statuses
                    )
                except Exception:
                    LOGGER.warning("Could not show startup greeting", exc_info=True)

    def _greeting_statuses(self) -> list[tuple[str, str]]:
        statuses = dict(self._service_status)
        statuses["Startup voice"] = self.wellbeing_delivery.startup_speech_status
        statuses["Dashboard"] = "Not detected"
        statuses["Sync daemon"] = "Not detected"
        try:
            for process in psutil.process_iter(["name", "cmdline", "cwd", "status"]):
                try:
                    info = process.info
                    name = (info["name"] or "").lower()
                    args = info["cmdline"] or []
                    cwd = info["cwd"]
                    entrypoint = (
                        any(Path(arg).name == "main.py" for arg in args)
                        or any(
                            args[index : index + 2] == ["-m", "main"]
                            for index in range(len(args))
                        )
                        or bool(
                            args and Path(args[0]).name.lower().startswith("workgraph")
                        )
                    )
                    if (
                        not (name.startswith(("python", "pypy", "workgraph")))
                        or not entrypoint
                        or not cwd
                        or Path(cwd).resolve() != Path.cwd().resolve()
                        or info["status"] == psutil.STATUS_ZOMBIE
                    ):
                        continue
                    if "--web" in args:
                        statuses["Dashboard"] = "Process running"
                    if "sync" in args and "daemon" in args:
                        statuses["Sync daemon"] = "Process running"
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError):
            statuses["Dashboard"] = "Not checked"
            statuses["Sync daemon"] = "Not checked"
        return list(statuses.items())

    def _close_tk_ui(self) -> None:
        if self._wellbeing_ui is not None:
            try:
                self._wellbeing_ui.close()
            except Exception:
                LOGGER.debug("Could not close wellbeing popup", exc_info=True)
            self._wellbeing_ui = None
        if self._tk_root is not None:
            try:
                self._tk_root.destroy()
            except Exception:
                LOGGER.debug("Could not close Tk root", exc_info=True)
            self._tk_root = None

    def collect_once(self) -> None:
        try:
            active_window = self.window_tracker.get_active_window()
            self._window_unavailable_warned = False
        except Exception:
            self._service_status["Window detection"] = "Unavailable"
            if not self._window_unavailable_warned:
                LOGGER.warning(
                    "Window detection failed; skipping this collection sample",
                    exc_info=True,
                )
                self._window_unavailable_warned = True
            if self._platform_provider_started:
                self.flush()
            return
        self._service_status["Window detection"] = (
            "Unavailable" if active_window.app_name == "Unknown" else "Running"
        )
        try:
            idle_state = self.idle_tracker.get_idle_state()
        except Exception:
            idle_state = IdleState(False, 0, available=False)
            if (
                self.settings.wellbeing.idle_check_enabled
                and not self._idle_unavailable_warned
            ):
                LOGGER.warning(
                    "Wellbeing idle detection failed; idle checks are unavailable",
                    exc_info=True,
                )
                self._idle_unavailable_warned = True
        self._service_status["Idle detection"] = (
            "Running" if idle_state.available else "Unavailable"
        )
        if self.settings.wellbeing.idle_check_enabled:
            self._service_status["Idle checks"] = (
                "Enabled" if idle_state.available else "Unavailable"
            )
        try:
            git_activity = self.git_tracker.get_activity(
                pid=active_window.process_id,
                app_name=active_window.app_name,
                window_title=active_window.window_title,
            )
            self._service_status["Git tracking"] = "Enabled"
            self._git_unavailable_warned = False
        except Exception:
            git_activity = GitActivity(None, None, None, [])
            self._service_status["Git tracking"] = "Unavailable"
            if not self._git_unavailable_warned:
                LOGGER.warning(
                    "Git tracking failed; continuing without Git metadata",
                    exc_info=True,
                )
                self._git_unavailable_warned = True

        is_idle = idle_state.is_idle
        idle_seconds = idle_state.idle_seconds
        system_idle = self.activity_tagger.is_system_idle(
            active_window.app_name, active_window.process_name
        )
        if system_idle:
            is_idle = True
            idle_seconds = max(idle_seconds, 1)

        if self.settings.dashboard_mode in {"standalone", "sync-client"}:
            if self.settings.wellbeing.idle_check_enabled:
                if not idle_state.available and not system_idle:
                    if not self._idle_unavailable_warned:
                        LOGGER.warning(
                            "Wellbeing idle detection unavailable; "
                            "idle checks are unavailable"
                        )
                        self._idle_unavailable_warned = True
                else:
                    self._idle_unavailable_warned = False
            if (
                self.settings.wellbeing.break_reminders_enabled
                or self.settings.wellbeing.time_announcements_enabled
                or self.settings.wellbeing.idle_check_enabled
                or self.settings.wellbeing.workout_reminders_enabled
                or self.settings.wellbeing.water_reminders_enabled
            ):
                tick = time.monotonic()
                now = utc_now().astimezone()
            if (
                self.settings.wellbeing.break_reminders_enabled
                or self.settings.wellbeing.idle_check_enabled
                or self.settings.wellbeing.workout_reminders_enabled
                or self.settings.wellbeing.water_reminders_enabled
            ):
                try:
                    self._process_wellbeing_actions(tick, now)
                    prompts = self.wellbeing.next_prompts(
                        tick=tick,
                        now=now,
                        idle_seconds=idle_state.idle_seconds,
                        idle_available=idle_state.available,
                    )
                    if self.settings.wellbeing.workout_reminders_enabled:
                        self._service_status["Workout reminders"] = "Enabled"
                    if self.settings.wellbeing.water_reminders_enabled:
                        self._service_status["Water reminders"] = "Enabled"
                    if self._running:
                        for prompt in prompts:
                            try:
                                self._present_wellbeing_prompt(prompt, tick, now)
                            except Exception:
                                LOGGER.exception(
                                    "Could not deliver %s wellbeing prompt", prompt.kind
                                )
                except Exception:
                    for name, enabled in (
                        ("Idle checks", self.settings.wellbeing.idle_check_enabled),
                        (
                            "Workout reminders",
                            self.settings.wellbeing.workout_reminders_enabled,
                        ),
                        (
                            "Water reminders",
                            self.settings.wellbeing.water_reminders_enabled,
                        ),
                    ):
                        if enabled:
                            self._service_status[name] = "Failed"
                    LOGGER.exception("Wellbeing prompt update failed")
            if self.settings.wellbeing.break_reminders_enabled:
                try:
                    for action in (
                        *self.wellbeing_delivery.read_actions(),
                        *read_control_commands(self.settings.log_path),
                    ):
                        if action == "break":
                            self.wellbeing.take_break()
                        elif action == "snooze":
                            self.wellbeing.snooze()
                    self.wellbeing.update_break_reminders(
                        tick=tick,
                        idle_seconds=idle_seconds,
                        idle_available=idle_state.available,
                        is_idle=is_idle,
                        system_idle=system_idle,
                    )
                    while not self._break_reminders.empty():
                        message = self._break_reminders.get_nowait()
                        if self._running:
                            self._present_wellbeing_prompt(
                                WellbeingPrompt("break", "Time for a break", message),
                                tick,
                                now,
                            )
                    self._service_status["Break reminders"] = "Enabled"
                except Exception:
                    self._service_status["Break reminders"] = "Failed"
                    LOGGER.exception("Wellbeing break reminder update failed")
            if self.settings.wellbeing.time_announcements_enabled:
                try:
                    self.wellbeing.update_time_announcements(
                        tick=tick,
                        now=now,
                        idle_available=idle_state.available,
                        is_idle=is_idle,
                    )
                    self._service_status["Time announcements"] = "Enabled"
                except Exception:
                    self._service_status["Time announcements"] = "Failed"
                    LOGGER.exception("Wellbeing time announcement update failed")

        try:
            browser_domain = self.browser_tracker.get_domain(active_window)
            self._service_status["Browser tracking"] = "Enabled"
            self._browser_unavailable_warned = False
        except Exception:
            browser_domain = None
            self._service_status["Browser tracking"] = "Unavailable"
            if not self._browser_unavailable_warned:
                LOGGER.warning(
                    "Browser tracking failed; continuing without browser metadata",
                    exc_info=True,
                )
                self._browser_unavailable_warned = True

        if (
            self._platform_provider_started
            and getattr(self._platform_provider, "requires_idle_for_collection", False)
            is True
            and not idle_state.available
        ):
            if not self._idle_unavailable_warned:
                LOGGER.warning("Idle detection unavailable; skipping collection sample")
                self._idle_unavailable_warned = True
            self.flush()
            return
        if idle_state.available:
            self._idle_unavailable_warned = False

        sample = ActivitySample(
            observed_at=utc_now(),
            app_name=active_window.app_name,
            process_name=active_window.process_name,
            window_title=active_window.window_title,
            browser_domain=browser_domain,
            is_idle=is_idle,
            idle_seconds=idle_seconds,
            platform=active_window.platform,
            git_repo=git_activity.repo_name,
            git_branch=git_activity.branch,
        )

        completed = self.session_builder.ingest(sample)
        if completed:
            # Tag the completed session
            completed_tagged = replace(
                completed, tag=self.activity_tagger.tag_session(completed)
            )
            self.repository.save_session(completed_tagged)
            LOGGER.debug("Saved activity session: %s", completed_tagged)

    def flush(self) -> None:
        completed = self.session_builder.flush()
        if completed:
            # Tag the completed session
            completed_tagged = replace(
                completed, tag=self.activity_tagger.tag_session(completed)
            )
            self.repository.save_session(completed_tagged)

    def stop(self, *_args) -> None:
        self._running = False
        if self._tk_root is not None:
            try:
                self._tk_root.quit()
            except Exception:
                LOGGER.debug("Could not stop Tk event loop", exc_info=True)

    def _process_wellbeing_actions(self, tick: float, now) -> None:
        while True:
            try:
                action, exercise_name = self._wellbeing_actions.get_nowait()
            except queue.Empty:
                return
            if action == "break_snooze":
                self.wellbeing.snooze()
                continue
            if action == "break_taken":
                self.wellbeing.take_break()
                continue
            prompt = self.wellbeing.prompt_for_action(action)
            if prompt is None or not self.wellbeing.apply_action(
                action, tick, now, exercise_name
            ):
                continue
            metadata = None
            if action.endswith("snooze") or action == "idle_working":
                snooze_minutes = {
                    "idle_snooze": self.settings.wellbeing.idle_check_snooze_minutes,
                    "idle_working": self.settings.wellbeing.idle_check_snooze_minutes,
                    "workout_snooze": self.settings.wellbeing.workout_snooze_minutes,
                    "water_snooze": self.settings.wellbeing.water_snooze_minutes,
                }[action]
                metadata = {"snooze_minutes": snooze_minutes}
            if action == "workout_complete":
                metadata = {"verified": False}
            self.repository.save_wellbeing_event(
                created_at=now,
                reminder_type=prompt.kind,
                action=action,
                exercise_name=exercise_name,
                metadata=metadata,
            )

    def _present_wellbeing_prompt(self, prompt, tick: float, now) -> None:
        if self._wellbeing_ui is not None:
            try:
                self._wellbeing_ui.show(prompt)
                return
            except Exception:
                LOGGER.warning("Could not display wellbeing popup", exc_info=True)
                try:
                    self._wellbeing_ui.close()
                except Exception:
                    LOGGER.debug(
                        "Could not close failed wellbeing popup", exc_info=True
                    )
                self._wellbeing_ui = None

        if prompt.kind == "water" and not self.wellbeing._within_hours(now.time()):
            return
        previous = self._fallback_notified_at.get(prompt.kind)
        retry_seconds = max(60.0, self.settings.poll_interval_seconds)
        if (
            prompt.kind != "break"
            and previous is not None
            and (prompt.kind != "water" or tick - previous < retry_seconds)
        ):
            return
        self._fallback_notified_at[prompt.kind] = tick
        self.wellbeing_delivery.notify_reminder(prompt.title, prompt.message)


def configure_logging(log_path: str, log_level: str = "INFO") -> None:
    _configure_logging(log_path, log_level)
