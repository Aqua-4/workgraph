import os
import select
import sqlite3
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from services.collector_supervisor import CollectorSupervisor, _lock_collector
from services.linux_session import DesktopSession, SessionDiscovery


def test_temporary_wayland_failure_does_not_restart_collector(tmp_path):
    supervisor = CollectorSupervisor()
    supervisor._collector = Mock()
    supervisor._collector.poll.return_value = None
    session = DesktopSession("3", ())
    failures = [
        SessionDiscovery(None, "waiting for the Wayland display", identity="3"),
        SessionDiscovery(None, "waiting for the desktop environment", identity="4"),
    ]
    with (
        patch("services.collector_supervisor.discover_session", side_effect=failures),
        patch("services.collector_supervisor.time.monotonic", return_value=10),
        patch.object(supervisor, "_wait") as wait,
    ):
        reason, _ = supervisor._monitor(session, tmp_path / "heartbeat", 120, 0)
    assert reason == "session changed"
    wait.assert_called_once()


def test_stalled_collector_and_heartbeat(tmp_path):
    supervisor = CollectorSupervisor()
    supervisor._collector = Mock()
    supervisor._collector.poll.return_value = None
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("100", encoding="ascii")
    with (
        patch("services.collector_supervisor.time.monotonic", side_effect=[200, 221]),
        patch.object(supervisor, "_wait") as wait,
    ):
        reason, _ = supervisor._monitor(None, heartbeat, 120, 0)
    assert reason == "stalled"
    wait.assert_called_once()


def test_collector_shutdown_waits_then_reaps_on_timeout(caplog):
    supervisor = CollectorSupervisor()
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("collector", 30), 0]
    supervisor._collector = process
    supervisor._stop_collector()
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2
    assert supervisor._collector is None
    assert "may lose" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="Linux collector lock")
def test_supervisors_cannot_share_database_lock(tmp_path):
    descriptor = _lock_collector(str(tmp_path / "activity.db"))
    assert descriptor is not None
    try:
        with pytest.raises(BlockingIOError):
            _lock_collector(str(tmp_path / "activity.db"))
    finally:
        os.close(descriptor)
    descriptor = _lock_collector(str(tmp_path / "activity.db"))
    assert descriptor is not None
    os.close(descriptor)


def test_stop_signal_interrupts_supervisor_wait():
    supervisor = CollectorSupervisor()
    supervisor._handle_stop_signal(0, None)
    assert supervisor._stopping
    assert supervisor._stop_event.is_set()


def test_sync_server_does_not_wait_for_graphical_session(tmp_path):
    supervisor = CollectorSupervisor()
    settings = Mock(
        database_path=str(tmp_path / "activity.db"),
        poll_interval_seconds=5,
        dashboard_mode="sync-server",
    )
    with (
        patch.object(supervisor, "_configure_logging"),
        patch.object(supervisor, "_register_signal_handlers"),
        patch("main.load_settings", return_value=settings),
        patch.object(supervisor, "_run_with_lock", return_value=0) as run,
    ):
        assert supervisor.run_forever() == 0
    run.assert_called_once_with(120.0, discover_desktop=False)


def test_waits_for_login_and_reaps_before_session_restart():
    supervisor = CollectorSupervisor()
    first = Mock(pid=101)
    second = Mock(pid=102)
    first.poll.return_value = None
    second.poll.return_value = None
    first_session = DesktopSession("3", (("DISPLAY", ":1"),))
    next_session = DesktopSession("4", (("DISPLAY", ":2"),))
    discoveries = [
        SessionDiscovery(None, "waiting for a graphical login"),
        SessionDiscovery(first_session, "ready", identity="3"),
        SessionDiscovery(next_session, "ready", identity="4"),
    ]
    launches = []

    def launch(*args, **kwargs):
        if launches:
            first.wait.assert_called_once()
        launches.append(kwargs["env"])
        return first if len(launches) == 1 else second

    def monitor(session, heartbeat, timeout, started):
        if session == first_session:
            return "session changed", None
        supervisor._stopping = True
        return "stopping", None

    with (
        patch(
            "services.collector_supervisor.discover_session", side_effect=discoveries
        ),
        patch("services.collector_supervisor.subprocess.Popen", side_effect=launch),
        patch.object(supervisor, "_monitor", side_effect=monitor),
        patch.object(supervisor, "_wait") as wait,
    ):
        assert supervisor._run_with_lock(120) == 0
    wait.assert_called_once_with(5)
    assert [environment["DISPLAY"] for environment in launches] == [":1", ":2"]
    first.terminate.assert_called_once()
    second.terminate.assert_called_once()


@pytest.mark.skipif(sys.platform != "linux", reason="SIGTERM integration test")
def test_graceful_restart_flushes_two_activity_sessions(tmp_path):
    database = tmp_path / "activity.db"
    script = """
import sys
from collector.git_tracker import GitActivity
from services import collector_service
from workgraph.models import ActiveWindow

class Provider:
    def get_active_window(self):
        return ActiveWindow('code', 'code', 'work', None, 'linux')
    def get_idle_seconds(self):
        return 0

collector_service.create_platform_provider = Provider
settings = collector_service.CollectorSettings(
    database_path=sys.argv[1], identity_path=sys.argv[2], dashboard_mode='sync-server'
)
service = collector_service.CollectorService(settings)
service.git_tracker.get_activity = lambda **kwargs: GitActivity(None, None, None, [])
service.browser_tracker.get_domain = lambda window: None
collect = service.collect_once
def collect_and_signal():
    collect()
    print('sampled', flush=True)
service.collect_once = collect_and_signal
service.run_forever()
"""
    environment = os.environ.copy()
    environment.pop("WORKGRAPH_HEARTBEAT_PATH", None)
    for _ in range(2):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(database),
                str(tmp_path / "identity.json"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready and process.stdout.readline().strip() == "sampled"
            process.terminate()
            _, errors = process.communicate(timeout=10)
            assert process.returncode == 0, errors
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM activity_sessions").fetchone()[0]
            == 2
        )
