from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from collector.browser_tracker import BrowserTracker
from collector.idle_tracker import IdleTracker
from collector.window_tracker import WindowTracker
from db.repository import ActivityRepository
from processor.session_builder import SessionBuilder
from workgraph.models import ActivitySample, utc_now


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectorSettings:
    database_path: str = "activity.db"
    poll_interval_seconds: float = 5.0
    idle_threshold_seconds: int = 300
    session_gap_seconds: int = 90
    browser_history_lookback_seconds: int = 600
    log_path: str = "logs/workgraph.log"


class CollectorService:
    def __init__(self, settings: CollectorSettings) -> None:
        self.settings = settings
        self.window_tracker = WindowTracker()
        self.browser_tracker = BrowserTracker(settings.browser_history_lookback_seconds)
        self.idle_tracker = IdleTracker(settings.idle_threshold_seconds)
        self.session_builder = SessionBuilder(settings.session_gap_seconds)
        self.repository = ActivityRepository(settings.database_path)
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        LOGGER.info("WorkGraph collector started")

        try:
            while self._running:
                self.collect_once()
                time.sleep(self.settings.poll_interval_seconds)
        finally:
            self.flush()
            self.repository.close()
            LOGGER.info("WorkGraph collector stopped")

    def collect_once(self) -> None:
        active_window = self.window_tracker.get_active_window()
        idle_state = self.idle_tracker.get_idle_state()
        sample = ActivitySample(
            observed_at=utc_now(),
            app_name=active_window.app_name,
            process_name=active_window.process_name,
            window_title=active_window.window_title,
            browser_domain=self.browser_tracker.get_domain(active_window),
            is_idle=idle_state.is_idle,
            idle_seconds=idle_state.idle_seconds,
            platform=active_window.platform,
        )

        completed = self.session_builder.ingest(sample)
        if completed:
            self.repository.save_session(completed)
            LOGGER.debug("Saved activity session: %s", completed)

    def flush(self) -> None:
        completed = self.session_builder.flush()
        if completed:
            self.repository.save_session(completed)

    def stop(self, *_args) -> None:
        self._running = False


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
