---
name: personal-workgraph-settings
description: "Review and tune config/my-settings.yaml using WorkGraph activity, database, wellbeing, and work-pattern evidence. Use when personalizing collector, sync, schedule, break, or reminder settings."
---

# Personal WorkGraph Settings

Use this workflow when reviewing or changing the user's personal WorkGraph configuration. Make requested changes directly when the evidence is adequate; preserve uncertain settings and explain why they were left alone.

## Procedure

1. Read `config/my-settings.yaml`, `config/settings.yaml`, `config/README.md`, and the relevant settings models or service code. The personal file is a complete override, not a merge, so preserve every existing key, value, and comment that is not intentionally changed. Treat comments that state a preference as user intent.
2. Resolve `database_path` from the active personal configuration. Query SQLite read-only and limit analysis to a recent, stated period (normally 30 days). Filter `deleted_at IS NULL`; when sync data may mix users or devices, scope to the configured identity and device where those fields are available.
3. Keep the analysis private. Do not print or include sync tokens, identity values, window titles, journal text, or other identifying activity details in summaries. Prefer aggregate counts, durations, weekday/hour patterns, reminder actions, and reflection scores.
4. Check data coverage before drawing conclusions: number of active days and sessions, timestamp range, session-duration distribution, and presence of wellbeing events/reflections. Convert UTC session timestamps to the machine's local timezone before recommending settings expressed in local time.
5. Treat sparse or anomalous data as low confidence. Session starts are not a reliable measure of how long activity continued in each hour; allocate intervals across time buckets or label the result as start-time-only. Do not infer work quality from long sessions, or distraction from zero-filled `context_switches`. Check reflection scores for values outside the documented 1-5 range.
6. Map evidence to the smallest relevant settings change. Use reminder actions and reflections to tune wellbeing cadence when available. If they are absent, do not claim a cadence is personalized; prefer conservative, valid defaults and leave break settings unchanged unless the user gives a preference. Do not narrow operating hours from sparse or skewed activity alone.
7. Before suggesting an increase to exercise reps or duration, review the requested calendar month's `wellbeing_events` workout actions by action and exercise name, plus the mean of valid 1-5 `daily_reflections.energy` scores and the number of scores. Treat `workout_complete` as an unverified user selection, not proof that exercise was performed; use snoozes/skips only as engagement signals. Summarize the evidence and ask whether the user wants a gradual increase. Do not change `workout_exercises` until the user explicitly approves; if data is absent or sparse, say so and ask rather than implying readiness from the average energy alone.
8. Preserve per-device values and secrets exactly unless explicitly asked to change them. Never copy `sync_token` into output or a shared/default config. Add concise YAML comments for configuration groups and parameters when editing the file.
9. Validate the YAML and relevant settings tests, especially `tests/test_main_settings.py` and `tests/test_wellbeing.py`. Report what changed, the aggregate evidence used, confidence limits, and which settings were deliberately left unchanged.

## WorkGraph Data Notes

- `activity_sessions` is the primary time-series table; include `deleted_at IS NULL` in normal analysis.
- `daily_reflections` contains optional energy/stress scores and free-text fields; summarize scores only unless the user requests reflection content.
- `wellbeing_events` records reminder actions locally and may be empty even when reminders are enabled.
- Workout events use `reminder_type = 'workout'`; `action` and `exercise_name` describe the selected response, and `workout_complete` events have `metadata.verified = false`.
- `wellbeing_start_time` and `wellbeing_end_time` use local `HH:MM` time. A start later than the end represents an overnight window; the end is exclusive.
- `break_reminders_enabled`, `time_announcements_enabled`, and other boolean settings should be preserved unless the requested goal or evidence supports changing them.