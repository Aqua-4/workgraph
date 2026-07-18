# WorkGraph Copilot Prompts Guide

This folder contains Copilot workspace instructions and reusable prompt files for
querying and analyzing WorkGraph data.

## Files

- `copilot-instructions.md`
  - Auto-loaded workspace context for GitHub Copilot.
  - Includes schema summary, common query patterns, and coding conventions.

- `prompts/query-activity.prompt.md`
  - Write and explain SQLite queries for activity/session data.

- `prompts/productivity-analysis.prompt.md`
  - Generate evidence-based productivity analysis from activity and reflection data.

- `prompts/journal-insights.prompt.md`
  - Summarize and search journal/reflection content with activity-aware context.

- `prompts/sync-analytics.prompt.md`
  - Analyze sync-mode metrics, cross-device differences, and sync data quality.

## How to use in Copilot Chat

1. Open Copilot Chat.
2. Type `/` and select one of these prompts:
   - `/query-activity`
   - `/productivity-analysis`
   - `/journal-insights`
   - `/sync-analytics`
3. Enter your question when prompted.

## Query execution guidance

When a prompt needs to run SQL against SQLite, prefer executing via `uv` + Python
`sqlite3` in a single command so the environment and dependency context are
consistent with the project.

Example pattern:

```bash
uv run python - <<'PY'
import sqlite3

conn = sqlite3.connect('activity.db')
conn.row_factory = sqlite3.Row

rows = conn.execute(
    """
    SELECT app_name, ROUND(SUM(duration_sec)/3600.0, 2) AS hours
    FROM activity_sessions
    WHERE start_time >= datetime('now', '-7 days')
      AND is_idle = 0
      AND deleted_at IS NULL
    GROUP BY app_name
    ORDER BY hours DESC
    LIMIT ?
    """,
    (10,),
).fetchall()

for row in rows:
    print(dict(row))

conn.close()
PY
```

Notes:
- Use parameterized queries only.
- Default to `deleted_at IS NULL` unless deleted rows are explicitly requested.
- For sync analytics, scope by `user_id` and optionally `device_id` when available.

## Prompt maintenance checklist

When updating prompts, keep them aligned with the current codebase:

- Verify endpoint references against `api/README.md` and `api/app.py`.
- Verify schema fields against `db/schema.sql`.
- Keep slash command examples in sync with available files in `prompts/`.
- Ensure README links use the correct `.github/prompts/...` paths.
- Prefer `uv run python` + `sqlite3` for any executable query examples.