# Sync Hardening and Launch Readiness

This document captures what has been implemented for WorkGraph sync hardening and how to validate it before launch.

## Scope

The sync system now follows this model:

1. Local collection is always primary and continuous.
2. Sync runs as a separate daemon and retries with exponential backoff.
3. Synced session payloads remain canonical audit records.
4. Analytics are served from extracted columns and daily rollups.

## Implemented Changes

### 1) Canonical Sync Session Enrichment

`sync_sessions` now stores extracted analytics fields alongside `payload_json`:

- `utc_start`
- `utc_end`
- `timezone_name`
- `active_seconds`
- `application_name`
- `tag`
- `repo_name`

`payload_json` remains the full event record for audit/debug.

### 2) Rollups

`sync_metrics_daily` is implemented with:

- `user_id`
- `device_id`
- `day_utc`
- `active_seconds`
- `focus_seconds`
- `meeting_seconds`
- `context_switches`
- `updated_at_utc`

Uniqueness:

- `UNIQUE(user_id, device_id, day_utc)`

Rollups are maintained on ingest and can be rebuilt via endpoint.

### 3) User Isolation

All sync pull and sync analytics paths are user-scoped.

The schema migration now supports duplicate `uuid` values across different users by enforcing user-scoped uniqueness.

### 4) Source Mode

Dashboard and stats use explicit source selection:

- `source=local`
- `source=sync`

Mixed local+sync mode is intentionally not provided.

### 5) Metadata and Analytics Endpoints

Implemented endpoints:

- `GET /api/sync/users`
- `GET /api/sync/devices?user_id=...`
- `GET /api/sync/stats?user_id=...&days=...&device_id=...`
- `POST /api/sync/rollups/rebuild?user_id=...&device_id=...&start_day=YYYY-MM-DD`

## Runtime Resilience

When sync server is unavailable:

1. Collector continues writing local data.
2. Dashboard remains available.
3. Sync daemon retries with backoff.
4. Unified launcher keeps collector/dashboard alive independently of sync.

## Pre-Launch Checklist

Run these in order.

1. Unit tests:

```bash
uv run python -m unittest tests.test_sync_api tests.test_sync_worker tests.test_sync_daemon
```

2. Start stack:

```bash
uv run python -m main --config config/settings.yaml unified --port 4000
```

3. Register two devices for one user and push data from both.

4. Open dashboard in sync mode:

- `http://127.0.0.1:4000/?source=sync&user_id=<user_id>`

5. Validate device filtering:

- `http://127.0.0.1:4000/?source=sync&user_id=<user_id>&device_id=<device_id>`

6. Validate stats APIs:

```bash
curl "http://127.0.0.1:4000/api/sync/stats?user_id=<user_id>&days=30"
curl "http://127.0.0.1:4000/api/stats?source=sync&user_id=<user_id>&days=30"
```

7. Rebuild rollups and re-check stats:

```bash
curl -X POST "http://127.0.0.1:4000/api/sync/rollups/rebuild?user_id=<user_id>"
```

8. Offline resilience check:

1. Stop sync server.
2. Keep collector and dashboard running.
3. Confirm local sessions continue accumulating.
4. Restart sync server and confirm sync resumes.

## Launch Decision Gate

Feature is launch-ready when all are true:

1. Test suite above is green.
2. Sync-mode dashboard renders metrics from synced data.
3. Device filter returns expected split.
4. Rollup rebuild endpoint works for historical range.
5. Offline/online recovery works without local data loss.