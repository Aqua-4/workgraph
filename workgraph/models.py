from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ActiveWindow:
    app_name: str
    process_name: str | None
    window_title: str | None
    process_id: int | None
    platform: str


@dataclass(frozen=True)
class ActivitySample:
    observed_at: datetime
    app_name: str
    process_name: str | None
    window_title: str | None
    browser_domain: str | None
    is_idle: bool
    idle_seconds: int
    platform: str
    git_repo: str | None = None
    git_branch: str | None = None


@dataclass(frozen=True)
class ActivitySession:
    start_time: datetime
    end_time: datetime
    duration_sec: int
    app_name: str
    process_name: str | None
    window_title: str | None
    browser_domain: str | None
    is_idle: bool
    idle_seconds: int
    platform: str
    git_repo: str | None = None
    git_branch: str | None = None
    context_switches: int = 0
    tag: str | None = None


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None
