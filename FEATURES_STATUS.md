# WorkGraph Feature Status

Code-verified feature matrix for the current repository state.

Last verified: 2026-07-11
Verification basis:
- Unit tests: `uv run python -m unittest discover -s tests`
- Source inspection of collector, API, services, and repository layers

## Legend

- Implemented: Available in code now
- Partial: Implemented with limited scope or proxy logic
- Planned: Not implemented yet

## v1.0 Collection Foundation

| Feature | Status | Notes |
|---|---|---|
| Active application tracking | Implemented | Windows and Linux active window capture in collector |
| Window title tracking | Implemented | Collected as part of each activity sample |
| Browser domain tracking | Implemented | Title extraction first, then browser history fallback |
| Idle time detection | Implemented | Idle tracker integrated into collector pipeline |
| Session aggregation | Implemented | Session builder groups contiguous samples |
| Local SQLite storage | Implemented | SQLite repository and schema are active |
| Lightweight background service | Implemented | Supervisor and run scripts included |

## v1.1 Work Attribution

| Feature | Status | Notes |
|---|---|---|
| Git repository tracking | Implemented | Repo, branch, commit, modified files are collected |
| Activity tagging | Implemented | Rule-based tags from `config/tags.yaml` |
| Context-switch counting | Implemented | Computed during session updates |
| Advanced session splitting | Implemented | Gap- and context-based session boundaries |
| Focus time analytics | Implemented | Dashboard stats include deep-work and focus metrics |
| Calendar event tracking | Planned | No calendar collector in current code |

## v1.2 Visualization

| Feature | Status | Notes |
|---|---|---|
| Local dashboard | Implemented | HTML dashboard route with summary stats |
| Timeline view | Implemented | Timeline route with filters |
| REST API for sessions and stats | Implemented | `/api/sessions` and `/api/stats` |
| Journal entries API | Implemented | Create, list, get, update endpoints |
| Correlated sessions for journal windows | Implemented | Correlation endpoint with overlap summary |
| Daily reflections API | Implemented | Upsert and list reflections |
| Weekly reports | Planned | No report generation module yet |
| PostgreSQL backend for multi-device sync | Planned | SQLite is the active backend |
| Activity export (JSON, CSV) | Planned | No export command/endpoint yet |

## v2.0 Analytics Engine

| Feature | Status | Notes |
|---|---|---|
| Goal drift detection | Planned | Mentioned in docs, no detection engine yet |
| Focus block analysis | Partial | Core focus metrics exist in dashboard stats |
| Weekly/monthly reports | Planned | Not present in current code |
| Burnout risk indicators | Planned | No burnout model/heuristics yet |
| Trend analysis | Partial | 7-day trend rows implemented in dashboard stats |

## v3.0 Intelligence

| Feature | Status | Notes |
|---|---|---|
| AI-powered coaching | Planned | No LLM coaching flow in current code |
| Productivity recommendations | Planned | No recommendation engine yet |
| Goal alignment analysis | Planned | No goal-vs-target analyzer yet |
| Team insights | Planned | Depends on future multi-device/team backend |

## Notes

- CLI currently supports `--config`, `--once`, `--web`, `--port`, and `--retag-existing`.
- Retag flow creates a backup before rewriting tags for existing sessions.
- This file should be updated alongside roadmap changes in README.
