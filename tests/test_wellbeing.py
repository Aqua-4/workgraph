import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from collector.git_tracker import GitActivity
from collector.idle_tracker import IdleState
from main import load_settings
from services.collector_service import CollectorService, CollectorSettings
from services.wellbeing import WellbeingPrompt, WellbeingService, WellbeingSettings
from services.wellbeing_delivery import (
    WellbeingDelivery,
    read_control_commands,
    send_control_command,
)
from services.wellbeing_ui import TkWellbeingUI
from workgraph.models import ActiveWindow


def test_breaks_snooze_and_unavailable_idle() -> None:
    notifications: list[str] = []
    settings = WellbeingSettings(
        break_reminders_enabled=True,
        break_work_minutes=1,
        break_idle_minutes=1,
        break_snooze_minutes=1,
    )
    service = WellbeingService(settings, notifications.append, lambda text: None)
    now = datetime(2026, 11, 15, 10)
    service.update(0, now, 0, True, False)
    service.update(30, now + timedelta(seconds=30), 0, True, False)
    service.update(60, now + timedelta(seconds=60), 0, False, False)
    service.update(90, now + timedelta(seconds=90), 0, True, False)
    assert notifications == []
    service.update(120, now + timedelta(seconds=120), 0, True, False)
    assert len(notifications) == 1
    service.update(150, now + timedelta(seconds=150), 60, True, True)
    assert service.work_seconds == 0
    service.update(180, now + timedelta(seconds=180), 0, True, False)
    service.update(240, now + timedelta(seconds=240), 0, True, False)
    assert len(notifications) == 2
    service.snooze()
    service.update(300, now + timedelta(seconds=300), 0, True, False)
    assert len(notifications) == 3
    service.take_break()
    assert service.work_seconds == 0


def test_time_announcements_respect_activity_and_hours() -> None:
    spoken: list[str] = []
    settings = WellbeingSettings(time_announcements_enabled=True)
    service = WellbeingService(settings, lambda text: None, spoken.append)
    now = datetime(2026, 11, 15, 9, 29, 55)
    service.update(0, now, 0, True, False)
    service.update(5, now + timedelta(seconds=5), 0, True, False)
    service.update(10, now + timedelta(seconds=10), 0, True, False)
    assert spoken == ["It's 9:30 AM."]
    service.update(20, datetime(2026, 11, 15, 9, 59, 55), 0, True, False)
    service.update(25, datetime(2026, 11, 15, 10), 300, True, True)
    assert len(spoken) == 1
    service.update(35, datetime(2026, 11, 15, 17, 59, 55), 0, True, False)
    service.update(40, datetime(2026, 11, 15, 18), 0, True, False)
    assert len(spoken) == 1


def test_time_announcements_continue_when_idle_detection_is_unavailable() -> None:
    spoken: list[str] = []
    service = WellbeingService(
        WellbeingSettings(time_announcements_enabled=True),
        lambda text: None,
        spoken.append,
    )
    now = datetime(2026, 11, 15, 9, 29, 55)
    service.update(0, now, 0, False, False)
    service.update(5, now + timedelta(seconds=5), 0, False, False)
    assert spoken == ["It's 9:30 AM."]


def test_sleep_gap_does_not_count_as_work_or_announce() -> None:
    notifications: list[str] = []
    spoken: list[str] = []
    settings = WellbeingSettings(
        break_reminders_enabled=True,
        break_work_minutes=1,
        time_announcements_enabled=True,
    )
    service = WellbeingService(settings, notifications.append, spoken.append)
    service.update(0, datetime(2026, 11, 15, 9), 0, True, False)
    service.update(3600, datetime(2026, 11, 15, 10), 0, True, False)
    assert service.work_seconds == 0
    assert not notifications
    assert not spoken


def test_locked_screen_pauses_work_and_can_complete_a_break() -> None:
    settings = WellbeingSettings(
        break_reminders_enabled=True,
        break_work_minutes=2,
        break_idle_minutes=1,
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    now = datetime(2026, 11, 15, 10)
    service.update(0, now, 0, True, False)
    service.update(30, now + timedelta(seconds=30), 0, True, False)
    service.update(60, now + timedelta(seconds=60), 0, False, True, True)
    assert service.work_seconds == 30
    service.update(120, now + timedelta(seconds=120), 0, False, True, True)
    assert service.work_seconds == 0


def test_control_commands_are_consumed_once(tmp_path: Path) -> None:
    log_path = str(tmp_path / "workgraph.log")
    send_control_command(log_path, "snooze")
    assert read_control_commands(log_path) == ["snooze"]
    assert read_control_commands(log_path) == []
    with pytest.raises(ValueError):
        send_control_command(log_path, "unknown")


def test_linux_notification_actions_and_fallback() -> None:
    delivery = WellbeingDelivery()
    result = subprocess.CompletedProcess([], 0, stdout="snooze\n", stderr="")
    with (
        patch("services.wellbeing_delivery.platform.system", return_value="Linux"),
        patch("services.wellbeing_delivery.shutil.which", return_value="notify-send"),
        patch("services.wellbeing_delivery.subprocess.run", return_value=result),
    ):
        delivery._notify("Stretch")
    assert delivery.read_actions() == ["snooze"]

    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="unsupported")
    fallback = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with (
        patch("services.wellbeing_delivery.platform.system", return_value="Linux"),
        patch("services.wellbeing_delivery.shutil.which", return_value="notify-send"),
        patch(
            "services.wellbeing_delivery.subprocess.run", side_effect=[failed, fallback]
        ) as run,
    ):
        delivery._notify("Stretch")
    assert run.call_count == 2
    assert "--action=snooze=Snooze" not in run.call_args.args[0]


def test_macos_reminder_notification_fallback() -> None:
    delivery = WellbeingDelivery()
    result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with (
        patch("services.wellbeing_delivery.platform.system", return_value="Darwin"),
        patch(
            "services.wellbeing_delivery.shutil.which",
            return_value="/usr/bin/osascript",
        ),
        patch("services.wellbeing_delivery.subprocess.run", return_value=result) as run,
    ):
        delivery._notify_reminder("Stay hydrated", "Take a few sips of water.")

    args = run.call_args.args[0]
    assert args[0] == "/usr/bin/osascript"
    assert args[-2:] == ["Stay hydrated", "Take a few sips of water."]


def test_invalid_wellbeing_settings_rejected() -> None:
    with pytest.raises(ValidationError):
        WellbeingSettings(break_work_minutes=0)
    with pytest.raises(ValidationError):
        WellbeingSettings(time_announcement_interval_minutes=1441)
    with pytest.raises(ValidationError):
        WellbeingSettings(idle_check_minutes=0)
    with pytest.raises(ValidationError):
        WellbeingSettings(workout_exercises=({"name": "No prescription"},))


def test_new_wellbeing_settings_defaults() -> None:
    settings = WellbeingSettings()
    assert not settings.idle_check_enabled
    assert settings.idle_check_minutes == 10
    assert settings.idle_check_snooze_minutes == 5
    assert not settings.workout_reminders_enabled
    assert settings.workout_interval_minutes == 90
    assert settings.workout_snooze_minutes == 15
    assert not settings.water_reminders_enabled
    assert settings.water_interval_minutes == 60
    assert settings.water_snooze_minutes == 15
    assert settings.wellbeing_start_time.isoformat() == "09:00:00"
    assert settings.wellbeing_end_time.isoformat() == "17:00:00"
    assert len(settings.workout_exercises) == 4


def test_idle_check_requires_available_idle_measurement() -> None:
    settings = WellbeingSettings(idle_check_enabled=True, idle_check_minutes=10)
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    now = datetime(2026, 11, 15, 10)

    assert service.next_prompt(0, now, 600, False) is None
    prompt = service.next_prompt(5, now, 600, True)
    assert prompt is not None
    assert prompt.kind == "idle"

    assert service.apply_action("idle_working", 5, now)
    assert service.next_prompt(6, now, 600, True) is None
    assert service.next_prompt(305, now, 600, True) is not None


def test_workout_timer_runs_through_os_idle_and_actions_reset_it() -> None:
    settings = WellbeingSettings(
        workout_reminders_enabled=True, workout_interval_minutes=1
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    now = datetime(2026, 11, 15, 10)

    assert service.next_prompt(0, now, 0, True) is None
    prompt = service.next_prompt(60, now, 3600, True)
    assert prompt is not None
    assert prompt.kind == "workout"
    assert len(prompt.exercises) == 4
    assert not service.apply_action("workout_complete", 60, now)
    assert service.apply_action("workout_complete", 60, now, exercise_name="Push-ups")
    assert service.next_prompt(119, now, 3600, True) is None
    assert service.next_prompt(120, now, 3600, True) is not None


def test_legacy_idle_break_does_not_reset_scheduled_workout() -> None:
    settings = WellbeingSettings(
        break_reminders_enabled=True,
        break_idle_minutes=1,
        workout_reminders_enabled=True,
        workout_interval_minutes=1,
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    now = datetime(2026, 11, 15, 10)
    service.next_prompt(0, now, 60, True)
    service.update_break_reminders(0, 60, True, True)
    service.update_break_reminders(60, 60, True, True)

    prompt = service.next_prompt(60, now, 60, True)
    assert prompt is not None and prompt.kind == "workout"


def test_due_prompts_are_prioritized_and_snoozes_recur() -> None:
    settings = WellbeingSettings(
        workout_reminders_enabled=True,
        workout_interval_minutes=1,
        workout_snooze_minutes=1,
        water_reminders_enabled=True,
        water_interval_minutes=1,
        water_snooze_minutes=1,
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    now = datetime(2026, 11, 15, 10)
    service.next_prompt(0, now, 0, True)

    prompt = service.next_prompt(60, now, 0, True)
    assert prompt is not None and prompt.kind == "water"
    assert service.apply_action("water_snooze", 60, now)
    prompt = service.next_prompt(60, now, 0, True)
    assert prompt is not None and prompt.kind == "workout"
    assert service.apply_action("workout_snooze", 60, now)
    assert service.next_prompt(119, now, 0, True) is None
    prompt = service.next_prompt(120, now, 0, True)
    assert prompt is not None and prompt.kind == "water"


def test_scheduled_reminders_defer_until_wellbeing_hours() -> None:
    settings = WellbeingSettings(
        water_reminders_enabled=True,
        water_interval_minutes=1,
        wellbeing_start_time="09:00",
        wellbeing_end_time="17:00",
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    service.next_prompt(0, datetime(2026, 11, 15, 8), 0, True)

    assert service.next_prompt(60, datetime(2026, 11, 15, 8, 1), 0, True) is None
    prompt = service.next_prompt(61, datetime(2026, 11, 15, 9), 0, True)
    assert prompt is not None and prompt.kind == "water"
    assert service.apply_action("water_snooze", 61, datetime(2026, 11, 15, 9))
    assert service.next_prompt(62, datetime(2026, 11, 15, 17), 0, True) is None


def test_wellbeing_ui_hands_actions_off_and_ignores_close() -> None:
    ui = object.__new__(TkWellbeingUI)
    ui.action_queue = Queue(maxsize=1)
    ui._window = None
    ui._prompt = None
    ui._selected_exercise = None

    assert ui._ignore_close() == "break"
    assert ui._submit_action("water_snooze")
    assert ui.action_queue.get_nowait() == ("water_snooze", None)

    ui.action_queue.put_nowait(("idle_break", None))
    assert not ui._submit_action("workout_snooze")


def test_water_popup_has_only_snooze_and_ignores_close_controls() -> None:
    window = MagicMock()
    with (
        patch("tkinter.Toplevel", return_value=window),
        patch("tkinter.ttk.Frame", return_value=MagicMock()),
        patch("tkinter.ttk.Label"),
        patch("tkinter.ttk.Button") as button,
    ):
        actions: Queue[tuple[str, str | None]] = Queue(maxsize=2)
        ui = TkWellbeingUI(MagicMock(), actions, WellbeingSettings())
        ui.show(WellbeingPrompt("water", "Stay hydrated", "Take a few sips."))

    window.protocol.assert_called_once_with("WM_DELETE_WINDOW", ui._ignore_close)
    window.bind.assert_any_call("<Escape>", ui._ignore_close)
    assert ui._ignore_close() == "break"
    button.assert_called_once()
    assert button.call_args.kwargs["text"] == "Snooze 15m"
    button.call_args.kwargs["command"]()
    assert actions.get_nowait() == ("water_snooze", None)


def test_speech_process_failure_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    delivery = WellbeingDelivery()
    with patch("services.wellbeing_delivery.subprocess.Popen", side_effect=OSError):
        delivery._speak("It is noon")
    assert "Could not start speech engine" in caplog.text
    assert not delivery._speaking
    delivery.close()


def test_startup_speech_failure_signals_error() -> None:
    delivery = WellbeingDelivery()
    with (
        patch("services.wellbeing_delivery.subprocess.Popen", side_effect=OSError),
        patch("services.wellbeing_delivery.error_beep") as beep,
    ):
        delivery._speak("WorkGraph started", error_on_failure=True)
    beep.assert_called_once()


def test_error_beep_uses_terminal_fallback() -> None:
    from services.wellbeing_delivery import error_beep

    with (
        patch("services.wellbeing_delivery.platform.system", return_value="Linux"),
        patch("services.wellbeing_delivery.shutil.which", return_value=None),
        patch("services.wellbeing_delivery.sys.stderr") as stderr,
    ):
        error_beep()
    stderr.write.assert_called_once_with("\a")
    stderr.flush.assert_called_once()


@pytest.mark.parametrize("sample_fails", [False, True])
def test_collector_announces_only_after_successful_first_sample(
    tmp_path: Path, sample_fails: bool
) -> None:
    collector = CollectorService(
        CollectorSettings(
            database_path=str(tmp_path / "activity.db"),
            identity_path=str(tmp_path / "identity.json"),
        )
    )

    def collect() -> None:
        collector._running = False
        if sample_fails:
            raise RuntimeError("sampling failed")

    with (
        patch.object(collector, "collect_once", side_effect=collect),
        patch.object(collector.wellbeing_delivery, "speak") as speak,
        patch("services.collector_service.signal.signal"),
        patch("services.collector_service.time.sleep"),
    ):
        if sample_fails:
            with pytest.raises(RuntimeError, match="sampling failed"):
                collector.run_forever()
            speak.assert_not_called()
        else:
            collector.run_forever()
            speak.assert_called_once_with(
                "WorkGraph activity tracking started.", error_on_failure=True
            )


def test_overnight_announcement_hours() -> None:
    settings = WellbeingSettings(
        wellbeing_start_time="22:00", wellbeing_end_time="06:00"
    )
    service = WellbeingService(settings, lambda text: None, lambda text: None)
    assert service._within_hours(datetime(2026, 11, 15, 23).time())
    assert service._within_hours(datetime(2026, 11, 16, 5).time())
    assert not service._within_hours(datetime(2026, 11, 16, 12).time())


def test_collector_wires_samples_and_controls(tmp_path: Path) -> None:
    settings = CollectorSettings(
        database_path=str(tmp_path / "activity.db"),
        identity_path=str(tmp_path / "identity.json"),
        log_path=str(tmp_path / "workgraph.log"),
        wellbeing=WellbeingSettings(break_reminders_enabled=True, break_work_minutes=1),
    )
    collector = CollectorService(settings)
    collector.window_tracker.get_active_window = lambda: ActiveWindow(
        "code", "code", "work", None, "linux"
    )
    collector.idle_tracker.get_idle_state = lambda: IdleState(False, 0)
    collector.git_tracker.get_activity = lambda **kwargs: GitActivity(
        None, None, None, []
    )
    collector.browser_tracker.get_domain = lambda window: None
    with (
        patch("services.collector_service.time.monotonic", side_effect=[0, 60]),
        patch.object(collector.wellbeing, "notify") as notify,
    ):
        collector.collect_once()
        collector.collect_once()
    notify.assert_called_once()
    send_control_command(settings.log_path, "break")
    collector.collect_once()
    assert collector.wellbeing.work_seconds == 0
    collector.repository.close()
    collector.wellbeing_delivery.close()


def test_collector_announcements_survive_idle_and_break_failures(
    tmp_path: Path,
) -> None:
    settings = CollectorSettings(
        database_path=str(tmp_path / "activity.db"),
        identity_path=str(tmp_path / "identity.json"),
        log_path=str(tmp_path / "workgraph.log"),
        wellbeing=WellbeingSettings(
            break_reminders_enabled=True,
            time_announcements_enabled=True,
        ),
    )
    collector = CollectorService(settings)
    collector.window_tracker.get_active_window = lambda: ActiveWindow(
        "code", "code", "work", None, "linux"
    )

    def fail_idle_detection() -> IdleState:
        raise RuntimeError("idle tracking failed")

    collector.idle_tracker.get_idle_state = fail_idle_detection
    collector.git_tracker.get_activity = lambda **kwargs: GitActivity(
        None, None, None, []
    )
    collector.browser_tracker.get_domain = lambda window: None
    now = (
        datetime.now().astimezone().replace(hour=9, minute=29, second=55, microsecond=0)
    )
    try:
        with (
            patch("services.collector_service.time.monotonic", side_effect=[0, 5]),
            patch(
                "services.collector_service.utc_now",
                side_effect=[
                    now,
                    now,
                    now + timedelta(seconds=5),
                    now + timedelta(seconds=5),
                ],
            ),
            patch.object(
                collector.wellbeing,
                "update_break_reminders",
                side_effect=RuntimeError("break reminders failed"),
            ),
            patch.object(collector.wellbeing, "speak") as speak,
        ):
            collector.collect_once()
            collector.collect_once()
        speak.assert_called_once_with("It's 9:30 AM.")
        assert collector.session_builder.current is not None
    finally:
        collector.repository.close()
        collector.wellbeing_delivery.close()


@pytest.mark.parametrize("mode", ["standalone", "sync-client", "sync-server"])
@pytest.mark.parametrize(
    "feature", ["break_reminders_enabled", "time_announcements_enabled"]
)
def test_collector_wellbeing_is_only_available_on_clients(
    tmp_path: Path, mode: str, feature: str
) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        f"database_path: {tmp_path / 'activity.db'}\n"
        f"identity_path: {tmp_path / 'identity.json'}\n"
        f"log_path: {tmp_path / 'workgraph.log'}\n"
        f"dashboard_mode: {mode.upper()}\n"
        f"{feature}: true\n",
        encoding="utf-8",
    )
    collector = CollectorService(load_settings(str(config_path)))
    collector.window_tracker.get_active_window = lambda: ActiveWindow(
        "code", "code", "work", None, "linux"
    )
    collector.idle_tracker.get_idle_state = lambda: IdleState(False, 0)
    collector.git_tracker.get_activity = lambda **kwargs: GitActivity(
        None, None, None, []
    )
    collector.browser_tracker.get_domain = lambda window: None
    try:
        with (
            patch.object(collector.wellbeing, "update_break_reminders") as breaks,
            patch.object(
                collector.wellbeing, "update_time_announcements"
            ) as announcements,
        ):
            collector.collect_once()
        expected_calls = 0 if mode == "sync-server" else 1
        assert breaks.call_count == (
            expected_calls if feature == "break_reminders_enabled" else 0
        )
        assert announcements.call_count == (
            expected_calls if feature == "time_announcements_enabled" else 0
        )
        assert collector.session_builder.current is not None
    finally:
        collector.repository.close()
        collector.wellbeing_delivery.close()


def test_server_mode_disables_new_wellbeing_prompts_and_startup_speech(
    tmp_path: Path,
) -> None:
    collector = CollectorService(
        CollectorSettings(
            database_path=str(tmp_path / "activity.db"),
            identity_path=str(tmp_path / "identity.json"),
            dashboard_mode="sync-server",
            wellbeing=WellbeingSettings(
                idle_check_enabled=True,
                workout_reminders_enabled=True,
                water_reminders_enabled=True,
            ),
        )
    )
    collector.window_tracker.get_active_window = lambda: ActiveWindow(
        "code", "code", "work", None, "linux"
    )
    collector.idle_tracker.get_idle_state = lambda: IdleState(False, 0)
    collector.git_tracker.get_activity = lambda **kwargs: GitActivity(
        None, None, None, []
    )
    collector.browser_tracker.get_domain = lambda window: None
    try:
        with (
            patch.object(collector.wellbeing, "next_prompt") as next_prompt,
            patch.object(collector.wellbeing_delivery, "notify_reminder") as notify,
            patch.object(collector.wellbeing_delivery, "speak") as speak,
            patch.object(collector, "collect_once", side_effect=collector.stop),
            patch("services.collector_service.signal.signal"),
            patch("services.collector_service.time.sleep"),
        ):
            collector.collect_once()
            collector.run_forever()
        next_prompt.assert_not_called()
        notify.assert_not_called()
        speak.assert_not_called()
    finally:
        collector.repository.close()
        collector.wellbeing_delivery.close()


def test_collector_persists_water_snooze_action(tmp_path: Path) -> None:
    now = datetime(2026, 11, 15, 10)
    collector = CollectorService(
        CollectorSettings(
            database_path=str(tmp_path / "activity.db"),
            identity_path=str(tmp_path / "identity.json"),
            wellbeing=WellbeingSettings(
                water_reminders_enabled=True,
                water_interval_minutes=1,
                water_snooze_minutes=15,
            ),
        )
    )
    collector.window_tracker.get_active_window = lambda: ActiveWindow(
        "code", "code", "work", None, "linux"
    )
    collector.idle_tracker.get_idle_state = lambda: IdleState(False, 0)
    collector.git_tracker.get_activity = lambda **kwargs: GitActivity(
        None, None, None, []
    )
    collector.browser_tracker.get_domain = lambda window: None
    collector._running = True
    try:
        with (
            patch(
                "services.collector_service.time.monotonic",
                side_effect=[0, 60, 61],
            ),
            patch("services.collector_service.utc_now", return_value=now),
            patch.object(collector.wellbeing_delivery, "notify_reminder") as notify,
        ):
            collector.collect_once()
            collector.collect_once()
            assert collector.wellbeing.active_prompt is not None
            assert collector.wellbeing.active_prompt.kind == "water"
            collector._wellbeing_actions.put_nowait(("water_snooze", None))
            collector.collect_once()

        events = collector.repository.recent_wellbeing_events()
        assert len(events) == 1
        assert events[0]["reminder_type"] == "water"
        assert events[0]["action"] == "water_snooze"
        assert json.loads(events[0]["metadata"]) == {"snooze_minutes": 15}
        notify.assert_called_once_with("Stay hydrated", "Take a few sips of water.")
    finally:
        collector._running = False
        collector.repository.close()
        collector.wellbeing_delivery.close()


def test_failed_water_popup_retries_notification_without_closing_tk_loop(
    tmp_path: Path,
) -> None:
    collector = CollectorService(
        CollectorSettings(
            database_path=str(tmp_path / "activity.db"),
            identity_path=str(tmp_path / "identity.json"),
            wellbeing=WellbeingSettings(
                water_reminders_enabled=True, water_interval_minutes=1
            ),
        )
    )
    now = datetime(2026, 11, 15, 10)
    prompt = collector.wellbeing.next_prompt(0, now, 0, True)
    assert prompt is None
    prompt = collector.wellbeing.next_prompt(60, now, 0, True)
    assert prompt is not None and prompt.kind == "water"
    failing_ui = MagicMock()
    failing_ui.show.side_effect = RuntimeError("popup failed")
    collector._wellbeing_ui = failing_ui
    collector._tk_root = MagicMock()
    try:
        with patch.object(collector.wellbeing_delivery, "notify_reminder") as notify:
            collector._present_wellbeing_prompt(prompt, 60, now)
            collector._present_wellbeing_prompt(prompt, 90, now)
            collector._present_wellbeing_prompt(prompt, 120, now)
        assert notify.call_count == 2
        failing_ui.close.assert_called_once()
        collector._tk_root.destroy.assert_not_called()
    finally:
        collector.repository.close()
        collector.wellbeing_delivery.close()
