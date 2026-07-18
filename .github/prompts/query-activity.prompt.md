---
agent: agent
description: Write and explain SQLite queries against the workgraph activity database.
---

Use the workgraph activity database schema below to answer questions or generate
SQLite queries. Prefer source-aware analysis (local or sync). Always use
parameterized queries in Python code and always filter `deleted_at IS NULL`
unless the user explicitly asks for deleted rows.

Default response mode is insights-only. Do not return SQL/query text unless the
user explicitly asks for SQL, query details, or step-by-step verification output.

## Database path
`activity.db` (project root — override via `config/settings.yaml → database_path`)

## Schema summary

### activity_sessions
Primary time-series table. Each row = one window-focus session.
- `start_time`, `end_time` — ISO-8601 UTC strings
- `duration_sec` — integer seconds
- `app_name` — focused application (e.g. `"code"`, `"firefox"`)
- `browser_domain` — hostname when a browser is focused
- `is_idle` — 1 when user was idle
- `idle_seconds` — idle seconds within the session
- `git_repo`, `git_branch` — coding context
- `tag` — auto-assigned label from `config/tags.yaml`
- `context_switches` — focus-switch count
- `user_id`, `device_id` — identity/scope dimensions for multi-device analysis

### git_activity
File-level git events; FK `session_id → activity_sessions.id`.
Columns: `repo`, `branch`, `commit_hash`, `file_name`, `event_type`

### work_events
Structured point-in-time events (achievement, incident, decision, blocker, etc.).
Useful columns: `event_time`, `event_type`, `impact`, `project`, `notes`.

### journal_entries
User notes with optional `start_time`/`end_time` range and free-text `notes`.

### daily_reflections
Daily structured reflection. `date` is UNIQUE (`YYYY-MM-DD`).
`wins`, `problems`, `tomorrow` are free text; `energy` and `stress` are 1–5.

## API-aware query guidance

When the user asks for data that mirrors dashboard/API outputs, align query output
with these API contracts and fields:

- `GET /api/sessions`
- `GET /api/stats` (supports `source=local|sync`, `user_id`, `device_id`)
- `GET /api/sync/stats` (sync-scoped analytics)
- `GET /api/sync/users` and `GET /api/sync/devices` (analytics segmentation)

If the question is sync-server scoped, apply `user_id` and optional `device_id`
scoping in the analysis.

## Query quality checklist

- Use explicit time windows (`date('now', '-N days')` or start/end ISO range).
- Separate active vs idle analysis (`is_idle = 0` for focus metrics).
- Include both totals and rates when useful (e.g., switches/hour).
- For correlations, return both detail rows and an aggregated summary.
- If assumptions are made (timezone, deep-work threshold), state them.

## Useful insight templates to offer

- Tag drift: compare recent tag distribution vs prior period.
- Context-switch density by app/repo/hour.
- Device split: active time by `user_id`/`device_id`.
- Repo neglect: repos with declining active minutes week-over-week.
- Event impact: relationship between `work_events.impact` and focus metrics.

---

## User request

${input:What do you want to query? e.g. "total coding time per day this week", "top browser domains yesterday"}
