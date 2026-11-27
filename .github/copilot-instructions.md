# Workgraph – Copilot Workspace Instructions

Keep this file focused on stable repository guidance. The README and linked
documentation are the source of truth for feature behavior, schema details, and
operational procedures; consult them when relevant instead of duplicating their
contents here.

## Project Orientation

Workgraph is a local-first activity tracker with a dashboard and optional
multi-device sync. Start with `README.md` for the project overview and setup.

- Dashboard behavior: `DASHBOARD.md` and `docs/DASHBOARD_FEATURES_AND_LOGIC.md`
- Architecture: `docs/architecture-overview.md`
- Configuration and personal overrides: `config/README.md`
- Database model: `db/schema.sql`; check repository and API migration code too
- Sync behavior: `docs/sync/README.md`
- Other documentation: `docs/README.md`

---

## Coding conventions

- Python 3.11+, dependency management via **uv** (`uv run`, `uv sync`).
- SQLite queries use plain `sqlite3` module; parameterised queries only (no f-string SQL).
- Tests live in `tests/` and run with `uv run python -m pytest`.
- Follow `config/README.md` for configuration behavior and personal overrides; never hard-code paths.
- When modifying any configuration file, use comment strings to group related parameters and add concise information about each parameter's purpose, accepted values, units, defaults, or operational effects. Use the format's native comment syntax (for example, `#` in YAML); never add comments to strict formats such as standard JSON if they would make the file invalid.

## Agent execution preference

- If you need to fetch data from SQLite during analysis, prefer running queries via
  `uv run python` using the `sqlite3` module instead of shelling out to ad-hoc SQL tooling.
- Use parameterized queries and filter `deleted_at IS NULL` by default unless deleted rows
  are explicitly requested.
- For sync-focused reads and analytics, scope by `user_id` and, when relevant, `device_id`; do not mix users' records implicitly.
- For sync changes, consult `config/sync.yaml` and `docs/sync/README.md` for current entity contracts and behavior.
- Response default for analytics prompts should be insights-only. Do not return SQL/query text
  unless the user explicitly asks for SQL, query details, or reproducibility steps.
