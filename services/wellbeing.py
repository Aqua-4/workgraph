from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkoutExercise(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    reps: int | None = Field(default=None, gt=0)
    duration_seconds: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_reps_or_duration(self) -> WorkoutExercise:
        if self.reps is None and self.duration_seconds is None:
            raise ValueError("an exercise must define reps or duration_seconds")
        if self.reps is not None and self.duration_seconds is not None:
            raise ValueError("an exercise cannot define both reps and duration_seconds")
        return self


@dataclass(frozen=True)
class WellbeingPrompt:
    kind: Literal["water", "idle", "workout"]
    title: str
    message: str
    exercises: tuple[WorkoutExercise, ...] = ()


class WellbeingSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    break_reminders_enabled: bool = False
    break_work_minutes: int = Field(default=50, gt=0)
    break_idle_minutes: int = Field(default=5, gt=0)
    break_snooze_minutes: int = Field(default=10, gt=0)
    time_announcements_enabled: bool = False
    time_announcement_interval_minutes: int = Field(default=30, ge=1, le=1440)
    time_announcement_active_only: bool = True
    idle_check_enabled: bool = False
    idle_check_minutes: int = Field(default=10, ge=1, le=1440)
    idle_check_snooze_minutes: int = Field(default=5, ge=1, le=1440)
    workout_reminders_enabled: bool = False
    workout_interval_minutes: int = Field(default=90, ge=1, le=1440)
    workout_snooze_minutes: int = Field(default=15, ge=1, le=1440)
    water_reminders_enabled: bool = False
    water_interval_minutes: int = Field(default=60, ge=1, le=1440)
    water_snooze_minutes: int = Field(default=15, ge=1, le=1440)
    workout_exercises: tuple[WorkoutExercise, ...] = (
        WorkoutExercise(name="Push-ups", reps=10),
        WorkoutExercise(name="Squats", reps=15),
        WorkoutExercise(name="Jumping jacks", duration_seconds=30),
        WorkoutExercise(name="Gyroball wrist workout", duration_seconds=60),
    )
    wellbeing_start_time: time = time(9, 0)
    wellbeing_end_time: time = time(17, 0)


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
        self._previous_break_tick: float | None = None
        self._previous_announcement_tick: float | None = None
        self._previous_wall: datetime | None = None
        self._previous_available = False
        self._system_idle_since: float | None = None
        self._last_announcement: tuple[object, int] | None = None
        self._suggestion_index = 0
        self._workout_deadline: float | None = None
        self._water_deadline: float | None = None
        self._idle_snoozed_until = 0.0
        self._idle_suppressed = False
        self._workout_skipped_day = None
        self._active_prompt: WellbeingPrompt | None = None

    def take_break(self, *, reset_workout: bool = True) -> None:
        self.work_seconds = 0.0
        self.next_reminder = self.settings.break_work_minutes * 60.0
        self._previous_available = False
        if reset_workout:
            self._workout_deadline = None

    def snooze(self) -> None:
        self.next_reminder = self.work_seconds + self.settings.break_snooze_minutes * 60

    @property
    def active_prompt(self) -> WellbeingPrompt | None:
        return self._active_prompt

    def next_prompt(
        self,
        tick: float,
        now: datetime,
        idle_seconds: int,
        idle_available: bool,
    ) -> WellbeingPrompt | None:
        if self._active_prompt is not None:
            return self._active_prompt

        if self.settings.workout_reminders_enabled and self._workout_deadline is None:
            self._workout_deadline = tick + self.settings.workout_interval_minutes * 60
        if self.settings.water_reminders_enabled and self._water_deadline is None:
            self._water_deadline = tick + self.settings.water_interval_minutes * 60

        if idle_seconds < self.settings.idle_check_minutes * 60:
            self._idle_suppressed = False
            self._idle_snoozed_until = 0.0
        idle_due = (
            self.settings.idle_check_enabled
            and idle_available
            and not self._idle_suppressed
            and idle_seconds >= self.settings.idle_check_minutes * 60
            and tick >= self._idle_snoozed_until
        )
        water_due = (
            self.settings.water_reminders_enabled
            and self._water_deadline is not None
            and tick >= self._water_deadline
        )
        workout_due = (
            self.settings.workout_reminders_enabled
            and self._workout_deadline is not None
            and tick >= self._workout_deadline
            and self._workout_skipped_day != self._operating_day(now)
        )
        if not self._within_hours(now.time()):
            return None

        if water_due:
            prompt = WellbeingPrompt(
                "water", "Stay hydrated", "Take a few sips of water."
            )
        elif idle_due:
            minutes = max(1, idle_seconds // 60)
            prompt = WellbeingPrompt(
                "idle",
                "Still here?",
                f"You have been inactive for {minutes} minutes.",
            )
        elif workout_due:
            prompt = WellbeingPrompt(
                "workout",
                "Time to move",
                "Choose a workout:",
                self.settings.workout_exercises,
            )
        else:
            return None
        self._active_prompt = prompt
        return prompt

    def apply_action(
        self,
        action: str,
        tick: float,
        now: datetime,
        exercise_name: str | None = None,
    ) -> bool:
        prompt = self._active_prompt
        if prompt is None:
            return False

        if prompt.kind == "idle":
            if action in {"idle_working", "idle_snooze"}:
                self._idle_snoozed_until = (
                    tick + self.settings.idle_check_snooze_minutes * 60
                )
            elif action == "idle_break":
                self._idle_suppressed = True
                self.take_break()
            else:
                return False
        elif prompt.kind == "workout":
            if action == "workout_complete":
                if exercise_name not in {
                    exercise.name for exercise in self.settings.workout_exercises
                }:
                    return False
                self._workout_deadline = (
                    tick + self.settings.workout_interval_minutes * 60
                )
            elif action == "workout_snooze":
                self._workout_deadline = (
                    tick + self.settings.workout_snooze_minutes * 60
                )
            elif action == "workout_skip":
                self._workout_skipped_day = self._operating_day(now)
            else:
                return False
        elif prompt.kind == "water":
            if action != "water_snooze":
                return False
            self._water_deadline = tick + self.settings.water_snooze_minutes * 60
        else:
            return False

        self._active_prompt = None
        return True

    def _operating_day(self, now: datetime):
        if (
            self.settings.wellbeing_start_time > self.settings.wellbeing_end_time
            and now.time() < self.settings.wellbeing_start_time
        ):
            return now.date() - timedelta(days=1)
        return now.date()

    def update(
        self,
        tick: float,
        now: datetime,
        idle_seconds: int,
        idle_available: bool,
        is_idle: bool,
        system_idle: bool = False,
    ) -> None:
        try:
            self.update_break_reminders(
                tick, idle_seconds, idle_available, is_idle, system_idle
            )
        finally:
            self.update_time_announcements(tick, now, idle_available, is_idle)

    def update_break_reminders(
        self,
        tick: float,
        idle_seconds: int,
        idle_available: bool,
        is_idle: bool,
        system_idle: bool = False,
    ) -> None:
        elapsed = (
            tick - self._previous_break_tick
            if self._previous_break_tick is not None
            else 0
        )
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
                self.take_break(reset_workout=False)
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

        self._previous_break_tick = tick
        self._previous_available = (
            idle_available
            and not system_idle
            and idle_seconds < self.settings.break_idle_minutes * 60
        )

    def update_time_announcements(
        self,
        tick: float,
        now: datetime,
        idle_available: bool,
        is_idle: bool,
    ) -> None:
        elapsed = (
            tick - self._previous_announcement_tick
            if self._previous_announcement_tick is not None
            else 0
        )
        timely = 0 <= elapsed <= self.max_sample_gap
        if (
            self.settings.time_announcements_enabled
            and self._previous_wall is not None
            and timely
            and (
                not self.settings.time_announcement_active_only
                or not idle_available
                or not is_idle
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

        self._previous_announcement_tick = tick
        self._previous_wall = now

    def _within_hours(self, current: time) -> bool:
        start = self.settings.wellbeing_start_time
        end = self.settings.wellbeing_end_time
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end
