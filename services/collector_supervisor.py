"""Self-healing supervisor for the WorkGraph collector."""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time

LOGGER = logging.getLogger(__name__)


class CollectorSupervisor:
    """Runs the collector and restarts it when it exits unexpectedly."""

    def __init__(self, config_path: str = "config/settings.yaml") -> None:
        self.config_path = config_path
        self._stopping = False
        self._collector: subprocess.Popen[None] | None = None

    def run_forever(self) -> int:
        self._configure_logging()
        self._register_signal_handlers()

        LOGGER.info("Starting self-healing collector supervisor")
        LOGGER.info("Collector config: %s", self.config_path)

        restart_count = 0
        backoff_seconds = 2

        while not self._stopping:
            self._collector = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "main",
                    "--config",
                    self.config_path,
                ]
            )
            LOGGER.info("Collector started (pid=%s)", self._collector.pid)

            exit_code = self._collector.wait()
            self._collector = None

            if self._stopping:
                break

            restart_count += 1
            LOGGER.error(
                "Collector exited unexpectedly (code=%s). Restart #%s in %ss.",
                exit_code,
                restart_count,
                backoff_seconds,
            )
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 30)

        LOGGER.info("Collector supervisor stopped")
        return 0

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop_signal)
        signal.signal(signal.SIGTERM, self._handle_stop_signal)

    def _handle_stop_signal(self, _signum: int, _frame: object) -> None:
        self._stopping = True
        if self._collector and self._collector.poll() is None:
            self._collector.terminate()
            try:
                self._collector.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._collector.kill()

    @staticmethod
    def _configure_logging() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(name)s: %(message)s",
        )


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
