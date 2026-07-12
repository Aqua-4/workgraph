# Workgraph – Copilot Workspace Instructions

This file gives GitHub Copilot context about the **workgraph** project so that AI
suggestions are relevant to the codebase and data model.

---

## What is workgraph?

Workgraph is a local-first activity tracker that runs as a background service on
Linux/macOS/Windows. It records window focus, browser domains, git activity, idle
time, and user-written journal entries into a local SQLite database.

---

## Database

**Default path:** `activity.db` (relative to the project root, configurable via
`config/settings.yaml → database_path`).

The database uses **SQLite** with WAL journal mode. Use `sqlite3` CLI or Python's
`sqlite3` / `sqlalchemy` to query it directly.

### Table: `activity_sessions`

The primary time-series table. Each row is one continuous window-focus session.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | auto-increment |
| `uuid` | TEXT | cross-device identifier |
| `user_id` | TEXT | from `config/identity.json` |
| `device_id` | TEXT | from `config/identity.json` |
| `start_time` | TEXT | ISO-8601 UTC |
| `end_time` | TEXT | ISO-8601 UTC |
| `duration_sec` | INTEGER | end − start in seconds |
| `app_name` | TEXT | e.g. `"code"`, `"firefox"` |
| `process_name` | TEXT | raw process name |
| `window_title` | TEXT | full window title |
| `browser_domain` | TEXT | hostname when app is a browser |
| `is_idle` | INTEGER | 1 if user was idle |
| `idle_seconds` | INTEGER | seconds of idle within session |
| `git_repo` | TEXT | repo path if detected |
| `git_branch` | TEXT | branch name if detected |
| `context_switches` | INTEGER | focus switches within session |
| `tag` | TEXT | auto-tag from `config/tags.yaml` |
| `platform` | TEXT | `"linux"`, `"darwin"`, `"windows"` |
| `created_at` | TEXT | row insertion time |
| `updated_at` | TEXT | last sync update |
| `deleted_at` | TEXT | soft-delete timestamp |

Active (non-deleted) sessions: `WHERE deleted_at IS NULL`

### Table: `git_activity`

File-level git events linked to a session.

| Column | Notes |
|--------|-------|
| `session_id` | FK → `activity_sessions.id` |
| `repo` | repo path |
| `branch` | branch name |
| `commit_hash` | SHA if available |
| `file_name` | changed file |
| `event_type` | e.g. `"modify"`, `"commit"` |

### Table: `journal_entries`

User-written notes tied to a time range.

| Column | Notes |
|--------|-------|
| `created_at` | ISO-8601 UTC |
| `start_time` | optional range start |
| `end_time` | optional range end |
| `title` | short heading |
| `notes` | free-text body |
| `metadata` | JSON blob for extra fields |

### Table: `daily_reflections`

End-of-day structured reflection entries.

| Column | Notes |
|--------|-------|
| `date` | `YYYY-MM-DD` (UNIQUE) |
| `wins` | free text |
| `problems` | free text |
| `tomorrow` | free text |
| `energy` | 1–5 scale |
| `stress` | 1–5 scale |

---

## Key source files

| Path | Purpose |
|------|---------|
| `db/schema.sql` | Full DDL for all tables |
| `db/repository.py` | All DB read/write helpers |
| `workgraph/models.py` | Python dataclasses (`ActivitySession`, etc.) |
| `processor/session_builder.py` | Converts raw samples → sessions |
| `services/activity_tagger.py` | Rule-based tag assignment |
| `config/tags.yaml` | Tag rules (app/domain/title patterns) |
| `config/settings.yaml` | Runtime configuration |
| `api/app.py` | Flask dashboard API |
| `api/templates/dashboard.html` | Main dashboard UI |

---

## Common query patterns

```sql
-- Active time per app today
SELECT app_name,
       ROUND(SUM(duration_sec) / 60.0, 1) AS minutes
FROM activity_sessions
WHERE date(start_time) = date('now')
  AND is_idle = 0
  AND deleted_at IS NULL
GROUP BY app_name
ORDER BY minutes DESC;

-- Deep-work blocks (≥25 min uninterrupted coding)
SELECT start_time, end_time, ROUND(duration_sec/60.0,1) AS minutes, app_name, git_repo
FROM activity_sessions
WHERE duration_sec >= 1500
  AND tag LIKE '%code%'
  AND deleted_at IS NULL
ORDER BY start_time DESC;

-- Hourly focus heatmap for this week
SELECT strftime('%H', start_time) AS hour,
       ROUND(SUM(duration_sec)/3600.0, 2) AS hours
FROM activity_sessions
WHERE start_time >= date('now', '-7 days')
  AND is_idle = 0
  AND deleted_at IS NULL
GROUP BY hour
ORDER BY hour;

-- Browser time by domain today
SELECT browser_domain,
       ROUND(SUM(duration_sec)/60.0,1) AS minutes
FROM activity_sessions
WHERE date(start_time) = date('now')
  AND browser_domain IS NOT NULL
  AND deleted_at IS NULL
GROUP BY browser_domain
ORDER BY minutes DESC;
```

---

## Coding conventions

- Python 3.11+, dependency management via **uv** (`uv run`, `uv sync`).
- SQLite queries use plain `sqlite3` module; parameterised queries only (no f-string SQL).
- Tests live in `tests/` and run with `uv run python -m pytest`.
- Config is read from `config/settings.yaml`; never hard-code paths.
