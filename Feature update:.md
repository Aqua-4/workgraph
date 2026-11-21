# Feature update:

## Python UI popup

Use Python `tkinter` for the wellbeing popup UI. Tkinter will provide the cross-platform Linux, macOS, and Windows window and controls; it requires an available graphical desktop session and is not suitable for headless or sync-server-only modes.

Add three related wellbeing prompts to the existing wellbeing subsystem:

1. **Idle check**
   - Trigger after a configured period of system inactivity.
   - Ask whether the user is still working or taking a break.
   - Do not infer that inactivity means daydreaming; idle time may include reading, waiting, thinking, or being away.

2. **Movement reminder**
   - Trigger after a scheduled interval of active work.
   - Present a configured exercise list in the popup and let the user choose which workout to perform.
   - Use a default interval of 90 minutes, with a user override in `config/my-settings.yaml`.
   - Keep the scheduled reminder active during meetings and reading periods.

3. **Water reminder**
   - Trigger on a scheduled interval such as every 60 minutes.
   - Prompt the user to drink water and stay hydrated.
   - Use a default interval of 60 minutes, with a user override in `config/my-settings.yaml`.
   - Keep scheduled during meetings and reading periods.


### Idle check popup

Example:

```text
Still here?

You have been inactive for 12 minutes.

[ Taking a break ]  [ Still working ]  [ Snooze ]
```

Options:

- **Still working**: resume monitoring and optionally delay the next idle check.
- **Taking a break**: reset active-work and workout timers.
- **Snooze**: ask again after the configured snooze interval.

The idle check should use the existing system idle state. A user response can record intent, but OS idle data cannot prove what the user was doing.

### Movement reminder popup

Example:

```text
Time to move

Choose a workout:
[ Push-ups ] [ Squats ] [ Jumping jacks ] [ Gyroball wrist workout ]

[ Snooze ] [ Workout complete ] [ Skip for today ]
```

Options:

- **Workout complete**: mark the prompt completed and reset the movement timer.
- **Snooze**: delay the reminder by the configured interval. Continue recurring until completed or dismissed.
- **Skip for today**: dismiss movement reminders for the rest of the configured day.

“Workout complete” means the user marked the activity complete. WorkGraph should not claim that it verified the workout.

### Water reminder popup

Example:

```text
Stay hydrated

Take a few sips of water.

[ Snooze 15m ]
```

The hydration popup is non-dismissable:

- It must not provide Close, Skip, or Complete controls.
- The popup close button, window-menu close action, and Escape key must be disabled or ignored while open.
- The only user action is **Snooze**.
- Snoozing schedules the next hydration check after the configured snooze interval and records the action.
- If the popup cannot be displayed, the reminder should retry during the same operating window rather than disappear silently.

### Configuration

Add or extend wellbeing settings for:

- `idle_check_enabled`
- `idle_check_minutes` (default: 10)
- `idle_check_snooze_minutes`
- `workout_reminders_enabled`
- `workout_interval_minutes` (default: 90)
- `workout_snooze_minutes`
- `water_reminders_enabled`
- `water_interval_minutes` (default: 60)
- `water_snooze_minutes` (default: 15)
- `wellbeing_start_time`
- `wellbeing_end_time`
- `workout_exercises`

The default values belong in `config/settings.yaml`. Per-user overrides belong in `config/my-settings.yaml` and should be applied through the existing my-config override mechanism.

Example exercise configuration:

```yaml
workout_exercises:
  - name: "Push-ups"
    reps: 10
  - name: "Squats"
    reps: 15
  - name: "Jumping jacks"
    duration_seconds: 30
  - name: "Gyroball wrist workout"
    duration_seconds: 60
```

### Suggested implementation

- Use `tkinter` for the popup window, exercise chooser, buttons, window state, and local user actions.
- Run the Tkinter event loop on the desktop collector's UI thread and use a bounded, thread-safe action handoff back to the wellbeing service.
- Keep OS desktop notifications as a fallback where Tkinter cannot create a window or when a graphical session is unavailable.
- Reuse `WellbeingService` for reminder timing and active-work accumulation.
- Reuse `WellbeingDelivery` for desktop notifications and platform-specific actions.
- Use existing idle detection rather than creating a second idle tracker.
- Add explicit actions such as `idle_break`, `idle_working`, `idle_snooze`, `workout_complete`, `workout_snooze`, `workout_skip`, and `water_snooze`.
- A snoozed workout or idle reminder should continue recurring until it is completed or dismissed for the day.
- A snoozed hydration reminder should reappear after its snooze interval and should remain non-dismissable.
- Reset workout timers after completion or a break response.
- Persist reminder decisions, workout completions, and hydration snoozes in a new wellbeing-events database table for later dashboard reporting.
- Keep dashboard reporting notification-only for the MVP: do not add wellbeing views yet.
- Restrict idle, workout, and hydration reminder types to configurable start and end hours.
- Keep desktop notifications non-blocking and avoid speech for every wellbeing prompt.
- all the  well-being and reminder features are disabled in `server-mode`

### MVP

Build the first version with:

1. A default 90-minute workout interval, with a per-user override.
2. A popup exercise chooser populated from the configured workout list.
3. Snooze, workout-complete, workout-skip, still-working, and taking-a-break actions.
4. A separate idle check with a default 10-minute inactivity timeout, with a per-user override.
5. Configurable wellbeing start and end hours.
6. Local database persistence for reminder decisions, workout completions, and hydration snoozes.
7. Recurring snoozed reminders until completed or dismissed for the day.
8. Scheduled movement reminders that continue through meetings and reading periods.
9. A default 60-minute hydration reminder with a per-user interval and 15-minute snooze override.
10. A non-dismissable hydration popup that has only a Snooze control.
11. Hydration snoozes recorded in the wellbeing-events database table.
12. Scheduled water reminders that continue through meetings and reading periods.
13. No automatic verification, fitness scoring, dashboard views, or AI-generated coaching.

This MVP creates an honest accountability loop: WorkGraph tracks active time, prompts the user, applies their selected action, and resets or snoozes the relevant timer.

### Resolved requirements

- Idle check: default to 10 minutes of inactivity, configurable by the user in `config/my-settings.yaml`.
- Workout schedule: default to every 90 minutes, configurable by the user in `config/my-settings.yaml`.
- Exercise selection: let the user choose an exercise from the configured workout list in the popup.
- MVP actions: include Snooze, Workout complete, Skip, Still working, and Taking a break.
- Snoozed reminders: continue recurring until completed or dismissed for the day.
- Reporting: keep notifications notification-only in the MVP, but save events to a new database table for later dashboards.
- Operating hours: restrict reminders to configurable start and end times.
- Movement schedule: keep reminders scheduled; include meetings and reading periods rather than pausing the workout schedule.
- Hydration schedule: default to every 60 minutes, configurable in `config/my-settings.yaml`.
- Hydration snooze: default to 15 minutes, configurable in `config/my-settings.yaml`.
- Hydration actions: Snooze only; no Close, Skip, or Complete control.
- Hydration lifecycle: continue reappearing after snooze until the user snoozes it again.
