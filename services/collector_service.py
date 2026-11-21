from __future__ import annotations

import logging
import queue
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from collector.browser_tracker import BrowserTracker
from collector.git_tracker import GitTracker
from collector.idle_tracker import IdleState, IdleTracker
from collector.window_tracker import WindowTracker
from db.repository import ActivityRepository
from processor.session_builder import SessionBuilder
from services.activity_tagger import ActivityTagger
from services.wellbeing import WellbeingService, WellbeingSettings
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
        self.window_tracker = WindowTracker()
        self.browser_tracker = BrowserTracker(settings.browser_history_lookback_seconds)
        self.idle_tracker = IdleTracker(settings.idle_threshold_seconds)
        self.git_tracker = GitTracker()
        self.activity_tagger = ActivityTagger()
        self.session_builder = SessionBuilder(settings.session_gap_seconds)
        self.repository = ActivityRepository(
            settings.database_path,
            identity_path=settings.identity_path,
        )
        self.wellbeing_delivery = WellbeingDelivery()
        self.wellbeing = WellbeingService(
            settings.wellbeing,
            self.wellbeing_delivery.notify,
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
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        LOGGER.info("WorkGraph collector started")

        try:
            if self._interactive_reminders_enabled():
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
            self._close_tk_ui()
            self.flush()
            self.repository.close()
            self.wellbeing_delivery.close()
            LOGGER.info("WorkGraph collector stopped")

    def _interactive_reminders_enabled(self) -> bool:
        return self.settings.dashboard_mode != "sync-server" and (
            self.settings.wellbeing.idle_check_enabled
            or self.settings.wellbeing.workout_reminders_enabled
            or self.settings.wellbeing.water_reminders_enabled
        )

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
        active_window = self.window_tracker.get_active_window()
        try:
            idle_state = self.idle_tracker.get_idle_state()
        except Exception:
            idle_state = IdleState(False, 0, available=False)
            if (
                self.settings.wellbeing.break_reminders_enabled
                or self.settings.wellbeing.idle_check_enabled
            ) and not self._idle_unavailable_warned:
                LOGGER.warning(
                    "Wellbeing idle detection failed; break timing is paused",
                    exc_info=True,
                )
                self._idle_unavailable_warned = True
        git_activity = self.git_tracker.get_activity(
            pid=active_window.process_id,
            app_name=active_window.app_name,
            window_title=active_window.window_title,
        )

        is_idle = idle_state.is_idle
        idle_seconds = idle_state.idle_seconds
        system_idle = self.activity_tagger.is_system_idle(
            active_window.app_name, active_window.process_name
        )
        if system_idle:
            is_idle = True
            idle_seconds = max(idle_seconds, 1)

        if self.settings.dashboard_mode in {"standalone", "sync-client"}:
            if (
                self.settings.wellbeing.break_reminders_enabled
                or self.settings.wellbeing.idle_check_enabled
            ):
                if not idle_state.available and not system_idle:
                    if not self._idle_unavailable_warned:
                        LOGGER.warning(
                            "Wellbeing idle detection unavailable; "
                            "break timing is paused"
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
                self.settings.wellbeing.idle_check_enabled
                or self.settings.wellbeing.workout_reminders_enabled
                or self.settings.wellbeing.water_reminders_enabled
            ):
                try:
                    self._process_wellbeing_actions(tick, now)
                    prompt = self.wellbeing.next_prompt(
                        tick=tick,
                        now=now,
                        idle_seconds=idle_state.idle_seconds,
                        idle_available=idle_state.available,
                    )
                    if self._running and prompt is not None:
                        self._present_wellbeing_prompt(prompt, tick, now)
                except Exception:
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
                except Exception:
                    LOGGER.exception("Wellbeing break reminder update failed")
            if self.settings.wellbeing.time_announcements_enabled:
                try:
                    self.wellbeing.update_time_announcements(
                        tick=tick,
                        now=now,
                        idle_available=idle_state.available,
                        is_idle=is_idle,
                    )
                except Exception:
                    LOGGER.exception("Wellbeing time announcement update failed")

        sample = ActivitySample(
            observed_at=utc_now(),
            app_name=active_window.app_name,
            process_name=active_window.process_name,
            window_title=active_window.window_title,
            browser_domain=self.browser_tracker.get_domain(active_window),
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
            prompt = self.wellbeing.active_prompt
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
        if previous is not None and (
            prompt.kind != "water" or tick - previous < retry_seconds
        ):
            return
        self._fallback_notified_at[prompt.kind] = tick
        self.wellbeing_delivery.notify_reminder(prompt.title, prompt.message)


def configure_logging(log_path: str) -> None:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )
