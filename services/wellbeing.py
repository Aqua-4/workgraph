from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, Field


class WellbeingSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    break_reminders_enabled: bool = False
    break_work_minutes: int = Field(default=50, gt=0)
    break_idle_minutes: int = Field(default=5, gt=0)
    break_snooze_minutes: int = Field(default=10, gt=0)
    time_announcements_enabled: bool = False
    time_announcement_interval_minutes: int = Field(default=30, ge=1, le=1440)
    time_announcement_active_only: bool = True
    wellbeing_start_time: time = time(9, 0)
    wellbeing_end_time: time = time(18, 0)


class WellbeingService:
    def __init__(
        self,
        settings: WellbeingSettings,
        notify: Callable[[str], None],
        speak: Callable[[str], None],
        max_sample_gap: float = 60,
    ) -> None:
        self.settings = settings
        self.notify = notify
        self.speak = speak
        self.max_sample_gap = max_sample_gap
        self.work_seconds = 0.0
        self.next_reminder = settings.break_work_minutes * 60.0
        self._previous_tick: float | None = None
        self._previous_wall: datetime | None = None
        self._previous_available = False
        self._system_idle_since: float | None = None
        self._last_announcement: tuple[object, int] | None = None
        self._suggestion_index = 0

    def take_break(self) -> None:
        self.work_seconds = 0.0
        self.next_reminder = self.settings.break_work_minutes * 60.0
        self._previous_available = False

    def snooze(self) -> None:
        self.next_reminder = self.work_seconds + self.settings.break_snooze_minutes * 60

    def update(
        self,
        tick: float,
        now: datetime,
        idle_seconds: int,
        idle_available: bool,
        is_idle: bool,
        system_idle: bool = False,
    ) -> None:
        elapsed = tick - self._previous_tick if self._previous_tick is not None else 0
        timely = 0 <= elapsed <= self.max_sample_gap
        if system_idle:
            if self._system_idle_since is None or not timely:
                self._system_idle_since = tick
            idle_seconds = max(idle_seconds, int(tick - self._system_idle_since))
            idle_available = True
        else:
            self._system_idle_since = None
        if self.settings.break_reminders_enabled and idle_available:
            if idle_seconds >= self.settings.break_idle_minutes * 60:
                self.take_break()
            elif self._previous_available and timely and not system_idle:
                self.work_seconds += elapsed
                if self.work_seconds >= self.next_reminder:
                    suggestions = (
                        "Take a short walk or stand up for a few minutes.",
                        "Take a break and gently stretch your shoulders and neck.",
                        "Rest your eyes: look into the distance for 20 seconds.",
                    )
                    self.notify(suggestions[self._suggestion_index % len(suggestions)])
                    self._suggestion_index += 1
                    self.snooze()

        if (
            self.settings.time_announcements_enabled
            and self._previous_wall is not None
            and timely
            and (
                not self.settings.time_announcement_active_only
                or (idle_available and not is_idle)
            )
            and self._within_hours(now.time())
        ):
            minutes = now.hour * 60 + now.minute
            interval = self.settings.time_announcement_interval_minutes
            slot = minutes // interval
            boundary = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(minutes=slot * interval)
            key = (now.date(), slot)
            if (
                self._previous_wall < boundary <= now
                and (now - boundary).total_seconds() <= elapsed + 1
                and key != self._last_announcement
            ):
                self._last_announcement = key
                self.speak(now.strftime("It's %I:%M %p.").replace(" 0", " "))

        self._previous_tick = tick
        self._previous_wall = now
        self._previous_available = (
            idle_available
            and not system_idle
            and idle_seconds < self.settings.break_idle_minutes * 60
        )

    def _within_hours(self, current: time) -> bool:
        start = self.settings.wellbeing_start_time
        end = self.settings.wellbeing_end_time
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end
