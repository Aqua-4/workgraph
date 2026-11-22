# Configuration Guide

This directory contains WorkGraph configuration files that control how the collector behaves and categorizes your activities.

## Files

### `settings.yaml` — Collector Behavior

Controls the core collection parameters:

| Setting                            | Default              | Description                                                                       |
| ---------------------------------- | -------------------- | --------------------------------------------------------------------------------- |
| `database_path`                    | `activity.db`        | Path to SQLite database where activity sessions are stored                        |
| `poll_interval_seconds`            | `5`                  | How often to sample the active window (in seconds)                                |
| `idle_threshold_seconds`           | `300`                | Time (in seconds) after which you're marked as idle (5 minutes default)           |
| `session_gap_seconds`              | `90`                 | Maximum gap (in seconds) before a new session starts                              |
| `browser_history_lookback_seconds` | `600`                | How far back to look in browser history for domain detection (10 minutes default) |
| `log_path`                         | `logs/workgraph.log` | Where to write debug logs                                                         |

#### Personal Override (recommended)

To match the same pattern as `my-tags.yaml` and `my-goals.yaml`:

- Keep `config/settings.yaml` as shared defaults in the repo.
- Create `config/my-settings.yaml` with personal/per-device values.

Load order used by the app:

1. `config/my-settings.yaml` (if present)
2. `config/settings.yaml` (fallback)

The app selects one file; it does not merge the personal file with the shared file.
Keys omitted from `my-settings.yaml` use application defaults.

This keeps user-specific config and secrets out of shared settings while preserving a simple default.

Example:

```bash
uv run python main.py sync once
```

Explicit override still works:

```bash
uv run python main.py sync once --config config/settings.yaml
```

Identity file precedence (matching personal override pattern):

1. If `identity_path` resolves to `config/identity.json` and `config/my-identity.json` exists, WorkGraph uses `config/my-identity.json`.
2. Otherwise it uses the configured `identity_path` value.

Recommended:

- Use `identity_path: config/my-identity.json` in `config/my-settings.yaml`.
- Keep `config/my-identity.json` per-device and untracked.

**Example: Reduce CPU usage by polling less frequently**

```yaml
poll_interval_seconds: 10  # Sample every 10 seconds instead of 5
```

**Example: Change idle threshold to 10 minutes**

```yaml
idle_threshold_seconds: 600  # 10 minutes
```

#### Wellbeing reminders

Idle, workout, and water prompts are disabled by default. The defaults are a
10-minute idle threshold, 90-minute workout interval, 60-minute water interval,
15-minute water snooze, and a local 09:00-17:00 operating window. Workout and water
intervals continue through meetings, reading, and OS idle; prompts due outside the
window wait until it opens again. Existing break reminders and time announcements
remain controlled by their own settings.

Add personal reminder values to `config/my-settings.yaml`:

```yaml
idle_check_enabled: true
idle_check_minutes: 10
idle_check_snooze_minutes: 5
workout_reminders_enabled: true
workout_interval_minutes: 90
workout_snooze_minutes: 15
water_reminders_enabled: true
water_interval_minutes: 60
water_snooze_minutes: 15
wellbeing_start_time: "09:00"
wellbeing_end_time: "17:00"
workout_exercises:
  - name: Push-ups
    reps: 10
  - name: Squats
    reps: 15
  - name: Jumping jacks
    duration_seconds: 30
  - name: Gyroball wrist workout
    duration_seconds: 60
```

Tkinter popups require a graphical desktop session. If Tk cannot open, WorkGraph
uses desktop notifications; these fallback notifications do not offer interactive
actions. Water and workout prompts can appear independently. The water prompt
retries during the operating window and its Tk popup has only a Snooze action.
Idle prompts require available OS idle data; the existing
detector does not provide it on macOS.

All wellbeing reminders, controls, notifications, Tk UI, and startup wellbeing
speech are disabled in `sync-server` mode. Reminder actions are stored locally in
the `wellbeing_events` table and are not synced or shown in the dashboard.

For Linux idle detection, install `xprintidle`. Desktop notifications and audio
require a working user session; on Debian-based distributions install it with:

```bash
sudo apt install xprintidle
```

Windows uses SAPI5 voices and the Windows notification package. Desktop
notifications and audio require a working user session; a headless service may not
be able to display or play them.

The collector also announces when its first activity sample succeeds, independently
of the wellbeing settings. If startup speech or a supervised process fails, WorkGraph tries
to play an error sound. On Linux this uses `canberra-gtk-play` when available,
otherwise a terminal bell; terminal bells may be muted or unavailable.

Add the desired flat settings to `config/my-settings.yaml`:

```yaml
break_reminders_enabled: true
break_work_minutes: 50
break_snooze_minutes: 10
time_announcements_enabled: true
time_announcement_interval_minutes: 30
time_announcement_active_only: true
wellbeing_start_time: "09:00"
wellbeing_end_time: "18:00"
```

Restart the collector to apply changes. Announcements run on local clock boundaries
within the configured hours, including overnight ranges; equal start and end times
allow announcements all day. They are skipped when the user is idle by default,
but continue when idle detection is unavailable. Break reminders also operate outside
these hours and use elapsed time independently of idle detection and screen locks.
Marking a break taken resets the break timer; the legacy `break_idle_minutes` setting
is retained for compatibility but does not affect scheduled reminders.

Linux notification actions support snoozing or marking a break taken when the
desktop supports them. On any platform, use these commands as a fallback, with
the same configuration and working directory as the collector:

```bash
uv run workgraph wellbeing snooze
uv run workgraph wellbeing break
```

Requests are processed on the collector's next sample. The snooze interval counts
active time; reminder state is local and resets when the collector restarts. If idle
detection fails, the work timer pauses and the collector logs a warning. Failed
notifications or speech also produce warnings without stopping collection.

---

### `tags.yaml` — Activity Categorization

Defines fallback rules to automatically categorize your activities into meaningful tags.

#### Personal Override (recommended)

To keep personal rules safe from git pulls/merges:

- Create `config/my-tags.yaml` with your own tags
- Keep `config/tags.yaml` as the shared sample/default in the repo

Load order used by the app:

1. `config/my-tags.yaml` (if present)
2. `config/tags.yaml` (fallback)

This means users can pull updates without losing personal tagging rules.

---

### `goals.yaml` and `my-goals.yaml` — Goal Allocation Targets

Defines planned percentage allocation used by weekly reports and goal drift analysis.

#### Personal Override (recommended)

To keep personal goals safe from git pulls/merges:

- Create `config/my-goals.yaml` with your own goal targets
- Keep `config/goals.yaml` as the shared sample/default in the repo

Load order used by the app:

1. `config/my-goals.yaml` (if present)
2. `config/goals.yaml` (fallback)

#### Structure

```yaml
goals:
  Client Delivery: 70
  Learning: 10
  Strategic Planning: 10
  Personal Projects: 10
```

Use with CLI:

```bash
uv run workgraph report weekly
uv run workgraph goals analyze
```

You can still override the file explicitly:

```bash
uv run workgraph report weekly --goals config/goals.yaml
uv run workgraph goals analyze --goals config/my-goals.yaml
```

#### Structure

```yaml
tags:
  Tag Name:
    repos:
      - repository-name
    domains:
      - example.com
    keywords:
      - keyword
```

#### Matching Logic

A session matches a tag if **ANY** of these conditions is true:
- Git repo starts with a value in `repos`
- Browser domain ends with a value in `domains`
- Any keyword appears in window title, app name, or browser domain

#### Customization Examples

**1. Add a new project to Client Delivery**

```yaml
Client Delivery:
  repos:
    - compass
    - atlas
    - mcp-platform
    - my-new-project  # ← Add your repo here
```

**2. Create a new tag for a specific goal**

```yaml
NZ Masters:
  repos:
    - nz-research
    - idp-project
  domains:
    - idp.govt.nz
    - immigration.govt.nz
  keywords:
    - nz
    - immigration
```

**3. Track time spent on specific learning platforms**

```yaml
Learning:
  domains:
    - udemy.com
    - coursera.org
    - youtube.com
    - docs.python.org
    - github.com/learning  # specific path pattern
  keywords:
    - tutorial
    - course
    - learn
```

**4. Monitor communication tools**

```yaml
Meetings:
  domains:
    - teams.microsoft.com
    - meet.google.com
    - zoom.us
  keywords:
    - meeting
    - standup
    - review
```

---

## How to Use

1. **Identify your main work categories** — Think about how you want to categorize your time:
   - Client/Project work
   - Learning & development
   - Administrative tasks
   - Personal projects
   - Interviews & preparation

2. **Map your repositories** — Add your Git repos to the relevant tags:
   ```yaml
   tags:
     MCP Platform:
       repos:
         - mcp-platform
     Personal:
       repos:
         - crypto-trader
         - personal-utils
   ```

3. **Add browser domains** — Track what websites you spend time on:
   ```yaml
   tags:
     Interview Prep:
       domains:
         - leetcode.com
         - hackerrank.com
   ```

4. **Use keywords for context** — Catch titles or app names that might indicate a specific type of work:
   ```yaml
   tags:
     Admin:
       keywords:
         - email
         - calendar
         - meeting
   ```

---

## Tips

- **Be specific with repos** — Repository names are matched as prefixes, so `mcp` matches `mcp-platform` and `mcp-utils`
- **Keep domains short** — Use `github.com` instead of `github.com/username/repo`
- **Prioritize tags** — Tag definitions are checked in order; more specific rules should come first
- **Test your tags** — After updating `tags.yaml`, the changes take effect on the next session. Check your database to see how activities are tagged
- **Avoid overlapping keywords** — If `email` matches "Admin" and something else matches another tag, the first matching tag wins

---

## Troubleshooting

**My activities aren't getting tagged**

- Check that the activity matches your rules:
  - For Git: Is the repo name exactly as it appears in the status?
  - For domains: Does the browser domain end with your domain?
  - For keywords: Are they appearing in window title or app name?
- Restart the collector after editing `tags.yaml`

**Too many activities have the same tag**

- Make your keyword rules more specific
- Use multiple tags instead of one catch-all tag
- Check for overlapping rules

**I want to see what was collected**

- Query the database:
  ```bash
  sqlite3 activity.db "SELECT app_name, git_repo, tag, SUM(duration_sec)/3600 as hours FROM activity_sessions GROUP BY app_name, git_repo, tag ORDER BY hours DESC;"
  ```
