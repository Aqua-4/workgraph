"""Self-healing supervisor for the WorkGraph collector."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from services.linux_session import SESSION_VARIABLES, DesktopSession, discover_session
from services.logging_config import configure_logging
from services.wellbeing_delivery import error_beep

LOGGER = logging.getLogger(__name__)
POLL_SECONDS = 5
SHUTDOWN_SECONDS = 30


def _lock_collector(database_path: str) -> int | None:
    if not sys.platform.startswith("linux"):
        return None
    import fcntl

    database = str(Path(database_path).resolve()).encode("utf-8")
    digest = hashlib.sha256(database).hexdigest()[:20]
    path = Path(tempfile.gettempdir()) / f"workgraph-{os.getuid()}-{digest}.lock"
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("collector lock file is not owned by the current user")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


class CollectorSupervisor:
    """Runs the collector and restarts it when it exits unexpectedly."""

    def __init__(self, config_path: str = "config/settings.yaml") -> None:
        self.config_path = config_path
        self._stopping = False
        self._stop_event = threading.Event()
        self._collector: subprocess.Popen[None] | None = None

    def run_forever(self) -> int:
        self._configure_logging()
        self._register_signal_handlers()

        LOGGER.info("Starting self-healing collector supervisor")
        LOGGER.info("Collector config: %s", self.config_path)

        from main import load_settings

        settings = load_settings(self.config_path)
        heartbeat_timeout = max(120.0, settings.poll_interval_seconds * 3 + 30)
        try:
            lock_descriptor = _lock_collector(settings.database_path)
        except (OSError, RuntimeError) as error:
            LOGGER.error("Cannot start collector supervisor: %s", error)
            return 1
        try:
            return self._run_with_lock(
                heartbeat_timeout,
                discover_desktop=settings.dashboard_mode != "sync-server",
            )
        finally:
            if lock_descriptor is not None:
                os.close(lock_descriptor)

    def _run_with_lock(
        self, heartbeat_timeout: float, discover_desktop: bool = True
    ) -> int:
        restart_count = 0
        backoff_seconds = 2
        error_signaled = False
        last_reason = ""
        with tempfile.TemporaryDirectory(prefix="workgraph-collector-") as temp_dir:
            heartbeat = Path(temp_dir) / "heartbeat"
            try:
                while not self._stopping:
                    session = None
                    if discover_desktop and sys.platform.startswith("linux"):
                        discovery = discover_session()
                        if not discovery.available and discovery.reason != last_reason:
                            LOGGER.warning("%s", discovery.reason)
                            last_reason = discovery.reason
                        if discovery.session is None:
                            if discovery.reason != last_reason:
                                LOGGER.warning(
                                    "Collector waiting: %s", discovery.reason
                                )
                                last_reason = discovery.reason
                            self._wait(POLL_SECONDS)
                            continue
                        session = discovery.session
                    last_reason = ""
                    environment = os.environ.copy()
                    if session is not None:
                        for name in SESSION_VARIABLES:
                            environment.pop(name, None)
                        environment.update(dict(session.environment))
                    environment["WORKGRAPH_HEARTBEAT_PATH"] = str(heartbeat)
                    heartbeat.unlink(missing_ok=True)
                    started_at = time.monotonic()
                    self._collector = subprocess.Popen(
                        [sys.executable, "-m", "main", "--config", self.config_path],
                        env=environment,
                    )
                    LOGGER.info("Collector started (pid=%s)", self._collector.pid)
                    reason, exit_code = self._monitor(
                        session, heartbeat, heartbeat_timeout, started_at
                    )
                    if reason in {"session changed", "stalled", "stopping"}:
                        self._stop_collector()
                    else:
                        self._collector.wait()
                        self._collector = None
                    if self._stopping:
                        break
                    if reason == "session changed":
                        LOGGER.info(
                            "Desktop session changed; waiting for tracking to resume"
                        )
                        backoff_seconds = 2
                        error_signaled = False
                        continue
                    if time.monotonic() - started_at >= 30:
                        error_signaled = False
                        backoff_seconds = 2
                    if not error_signaled:
                        error_beep()
                        error_signaled = True
                    restart_count += 1
                    LOGGER.error(
                        "Collector %s (code=%s). Restart #%s in %ss.",
                        reason,
                        exit_code,
                        restart_count,
                        backoff_seconds,
                    )
                    self._wait(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 30)
            finally:
                self._stop_collector()

        LOGGER.info("Collector supervisor stopped")
        return 0

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop_signal)
        signal.signal(signal.SIGTERM, self._handle_stop_signal)

    def _handle_stop_signal(self, _signum: int, _frame: object) -> None:
        self._stopping = True
        self._stop_event.set()

    def _wait(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _monitor(
        self,
        session: DesktopSession | None,
        heartbeat: Path,
        heartbeat_timeout: float,
        started_at: float,
    ) -> tuple[str, int | None]:
        assert self._collector is not None
        last_heartbeat = started_at
        while not self._stopping:
            exit_code = self._collector.poll()
            if exit_code is not None:
                return "exited unexpectedly", exit_code
            if session is not None:
                discovery = discover_session()
                session_missing = discovery.reason in {
                    "waiting for a graphical login",
                    "multiple graphical sessions cannot be attributed safely",
                }
                if discovery.available and (
                    session_missing
                    or (
                        discovery.identity is not None
                        and discovery.identity != session.session_id
                    )
                    or (discovery.session is not None and discovery.session != session)
                ):
                    return "session changed", None
            try:
                last_heartbeat = max(
                    last_heartbeat, float(heartbeat.read_text(encoding="ascii"))
                )
            except (OSError, ValueError):
                pass
            if time.monotonic() - last_heartbeat > heartbeat_timeout:
                return "stalled", None
            self._wait(POLL_SECONDS)
        return "stopping", None

    def _stop_collector(self) -> None:
        process = self._collector
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_SECONDS)
            except subprocess.TimeoutExpired:
                LOGGER.error(
                    "Collector did not stop; forcing termination may lose "
                    "the current activity session"
                )
                process.kill()
                process.wait()
        else:
            process.wait()
        self._collector = None

    @staticmethod
    def _configure_logging() -> None:
        configure_logging()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run WorkGraph collector with auto-restart."
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to collector settings YAML file (default: config/settings.yaml)",
    )
    args = parser.parse_args()

    supervisor = CollectorSupervisor(config_path=args.config)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
