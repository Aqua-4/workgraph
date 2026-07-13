---
agent: ask
description: Write and explain SQLite queries against the workgraph activity database.
---

Use the workgraph activity database schema below to answer questions or generate
SQLite queries. Always use parameterised queries in Python code and always filter
`deleted_at IS NULL` unless the user explicitly asks for deleted rows.

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

### git_activity
File-level git events; FK `session_id → activity_sessions.id`.
Columns: `repo`, `branch`, `commit_hash`, `file_name`, `event_type`

### journal_entries
User notes with optional `start_time`/`end_time` range and free-text `notes`.

### daily_reflections
Daily structured reflection. `date` is UNIQUE (`YYYY-MM-DD`).
`wins`, `problems`, `tomorrow` are free text; `energy` and `stress` are 1–5.

---

## User request

${input:What do you want to query? e.g. "total coding time per day this week", "top browser domains yesterday"}
