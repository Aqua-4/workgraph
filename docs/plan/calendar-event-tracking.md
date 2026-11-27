# Calendar Event Tracking

Implement opt-in, read-only Google Calendar and Microsoft Outlook integrations. Store scheduled events locally in a dedicated calendar-event model, expose them in Journal with optional correlation to observed activity sessions, and keep calendar data out of server sync. Calendar presence is not evidence of attendance.

## Aligned Decisions

- Sources: Google Calendar and Microsoft Outlook (Microsoft Graph).
- Behavior: show and correlate scheduled events; do not estimate or claim attendance.
- Data residency: local device only; no calendar event sync to WorkGraph server.
- Authentication: OAuth, with refresh tokens stored in the OS keyring.
- Privacy defaults: read only; import event title, start/end, timezone, all-day status, source/calendar identity, and provider status. Do not import attendee lists, descriptions, or locations by default.

## Implementation Plan

### 1. Foundation and Configuration

- Add disabled-by-default provider settings in `CollectorSettings`, `main.load_settings`, and `config/settings.yaml`; document OAuth app setup, read-only scopes, refresh interval, event lookahead/history window, privacy, and local-only storage in `config/README.md`.
- Add explicit `workgraph calendar connect`, `disconnect`, and `status` commands so account authorization is separate from collector startup. Keep provider client IDs/configuration distinct from refresh tokens; store and retrieve refresh tokens through an OS keyring adapter.
- Define a normalized calendar event model and provider interface before implementing either connector. Add the required provider and keyring dependencies to `pyproject.toml` only after confirming supported versions and platform behavior.

### 2. Provider Connectors and Local Persistence

- Implement Google Calendar and Microsoft Graph adapters under `collector/`, each using read-only API scopes and pagination/incremental refresh where supported.
- Normalize stable provider event IDs, calendar/account identity, recurring instances, all-day events, timezone/DST boundaries, updates, moved events, and cancellations.
- Add a dedicated local `calendar_events` table and repository upsert/delete methods in `db/schema.sql` and `db/repository.py`. Use provider + account/calendar + event/instance identity for idempotency; do not route imports through `save_work_event`, which assigns a fresh UUID.
- Keep refresh on its own slower cadence, independent of the high-frequency activity sampling loop. Provider failures or revoked permissions must not stop activity collection.

### 3. Journal Presentation and Correlation

- Add local read/filter API endpoints in `api/app.py` for calendar events and time-range queries.
- Extend the Journal view to show scheduled events and correlate event windows with overlapping activity sessions. Clearly label scheduled duration separately from observed activity; do not add a calendar event as a measured `work_event`.
- Do not add calendar rows to `SyncWorker`, sync API payloads, or server storage, honoring the local-only decision.

### 4. Focused Validation

- Use mocked provider responses and fixtures; no real Google/Microsoft account or network is needed for routine tests.
- Cover OAuth/keyring unavailable or revoked states, settings defaults, event pagination and deduplication, recurring instances, timezone/DST and all-day cases, update/cancellation handling, refresh throttling, privacy-field omission, and Journal correlation.
- Run focused tests first, then `uv run python -m pytest` and `uv run ruff check`.

## Relevant Files

- `workgraph/models.py` — normalized event type, following existing activity dataclass conventions.
- `collector/` — provider adapters and authorization/keyring integration.
- `services/collector_service.py` — `CollectorSettings` and independently throttled refresh lifecycle.
- `main.py` — `load_settings` and CLI connect/disconnect/status commands.
- `config/settings.yaml`, `config/README.md`, `pyproject.toml` — provider settings, setup/privacy guidance, and dependencies.
- `db/schema.sql`, `db/repository.py` — local calendar-event schema, idempotent upsert, and cancellation handling.
- `api/app.py`, `api/templates/journal.html` — local event queries, activity correlation, and Journal presentation.
- `tests/test_main_settings.py`, `tests/test_repository.py`, `tests/test_api_journal.py`, plus focused collector/service tests — configuration, persistence, provider, cadence, and UI/API behavior.

## Boundaries and Assumptions

- No server sync, attendance inference, calendar writing, attendee/description/location import, ICS/native OS calendar connector, or timeline redesign in this scope.
- OAuth requires Google and Microsoft app registrations and user consent for read-only access. The connect command should provide the interactive authorization flow; normal background collection should use refreshed keyring tokens without prompting.
- Before implementation, confirm the platform support of the chosen keyring library and OAuth flows for Linux, macOS, and Windows; if a platform has no usable keyring, fail clearly rather than silently persisting refresh tokens in plaintext.