import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from collector.git_tracker import GitActivity
from collector.idle_tracker import IdleState
from main import load_settings
from services.collector_service import CollectorService, CollectorSettings
from services.wellbeing import WellbeingService, WellbeingSettings
from services.wellbeing_delivery import (
    WellbeingDelivery,
    read_control_commands,
    send_control_command,
)
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


def test_invalid_wellbeing_settings_rejected() -> None:
    with pytest.raises(ValidationError):
        WellbeingSettings(break_work_minutes=0)
    with pytest.raises(ValidationError):
        WellbeingSettings(time_announcement_interval_minutes=1441)


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
        with patch.object(collector.wellbeing, "update") as update:
            collector.collect_once()
        assert update.call_count == (0 if mode == "sync-server" else 1)
        assert collector.session_builder.current is not None
    finally:
        collector.repository.close()
        collector.wellbeing_delivery.close()
