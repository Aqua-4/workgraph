from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from workgraph.models import ActivitySample, ActivitySession


class SessionBuilder:
    def __init__(self, max_gap_seconds: int = 90) -> None:
        self.max_gap_seconds = max_gap_seconds
        self._current: ActivitySession | None = None

    def ingest(self, sample: ActivitySample) -> ActivitySession | None:
        if self._current is None:
            self._current = _session_from_sample(sample)
            return None

        if self._belongs_to_current(sample):
            self._current = replace(
                self._current,
                end_time=sample.observed_at,
                duration_sec=_duration_seconds(self._current.start_time, sample.observed_at),
                idle_seconds=sample.idle_seconds,
            )
            return None

        completed = self._current
        self._current = _session_from_sample(sample)
        return completed

    def flush(self) -> ActivitySession | None:
        completed = self._current
        self._current = None
        return completed

    @property
    def current(self) -> ActivitySession | None:
        return self._current

    def _belongs_to_current(self, sample: ActivitySample) -> bool:
        assert self._current is not None
        gap = sample.observed_at - self._current.end_time
        if gap > timedelta(seconds=self.max_gap_seconds):
            return False
        return (
            self._current.app_name == sample.app_name
            and self._current.process_name == sample.process_name
            and self._current.window_title == sample.window_title
            and self._current.browser_domain == sample.browser_domain
            and self._current.is_idle == sample.is_idle
            and self._current.platform == sample.platform
        )


def _session_from_sample(sample: ActivitySample) -> ActivitySession:
    return ActivitySession(
        start_time=sample.observed_at,
        end_time=sample.observed_at,
        duration_sec=0,
        app_name=sample.app_name,
        process_name=sample.process_name,
        window_title=sample.window_title,
        browser_domain=sample.browser_domain,
        is_idle=sample.is_idle,
        idle_seconds=sample.idle_seconds,
        platform=sample.platform,
    )


def _duration_seconds(start, end) -> int:
    return max(0, int((end - start).total_seconds()))
