from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass

from services.sync_worker import SyncWorker

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncDaemonSettings:
    interval_seconds: float = 300.0
    backoff_base_seconds: float = 10.0
    backoff_max_seconds: float = 300.0


class SyncDaemon:
    def __init__(
        self,
        worker: SyncWorker,
        settings: SyncDaemonSettings | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.worker = worker
        self.settings = settings or SyncDaemonSettings()
        self.sleep_fn = sleep_fn or time.sleep
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        LOGGER.info("Sync daemon started")

        current_backoff = self.settings.backoff_base_seconds
        while self._running:
            try:
                summary = self.worker.run_once()
                LOGGER.info("Sync cycle successful: %s", summary)
                current_backoff = self.settings.backoff_base_seconds
                self.sleep_fn(self.settings.interval_seconds)
            except Exception as exc:  # pragma: no cover - covered via run_cycles tests
                LOGGER.exception("Sync cycle failed: %s", exc)
                self.sleep_fn(current_backoff)
                current_backoff = min(
                    current_backoff * 2, self.settings.backoff_max_seconds
                )

    def run_cycles(self, cycles: int) -> dict[str, int]:
        success = 0
        errors = 0
        current_backoff = self.settings.backoff_base_seconds

        for _ in range(cycles):
            try:
                self.worker.run_once()
                success += 1
                current_backoff = self.settings.backoff_base_seconds
                self.sleep_fn(self.settings.interval_seconds)
            except Exception:
                errors += 1
                self.sleep_fn(current_backoff)
                current_backoff = min(
                    current_backoff * 2, self.settings.backoff_max_seconds
                )

        return {"success": success, "errors": errors}

    def stop(self, *_args) -> None:
        self._running = False
