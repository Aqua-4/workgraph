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

### 4.1) Dashboard Runtime Modes (New)

Dashboard now supports three runtime modes via `dashboard_mode` in settings:

- `dashboard_mode: standalone`: local-only dashboard and local device registration
- `dashboard_mode: sync-client`: local dashboard with remote sync-server device registration flow
- `dashboard_mode: sync-server`: synced analytics/dashboard with user/device filtering

Backward compatibility remains:

- UI mode selector is intentionally not provided, so device roles are enforced by config.

Example:

```yaml
dashboard_mode: sync-server
```

### 4.2) Mode-Aware Device Registration (New)

Use one endpoint for mode-specific registration from dashboard/API clients:

- `POST /api/device/register`

Behavior by mode:

- `standalone`: upserts user/device locally without issuing sync token
- `sync-client`: forwards registration to remote sync server (`sync_base_url`) and returns remote `device_token`
- `sync-server`: registers user/device on current server and issues sync `device_token`

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

## Deployment Steps (Server and Clients)

### A) Server Setup (aggregation node)

Use these steps on the machine that will host sync APIs and aggregated metrics.

1. Prepare code and dependencies:

```bash
cd /home/pi/projects/workgraph
uv sync
```

2. Run sync tests:

```bash
uv run python -m unittest tests.test_sync_api tests.test_sync_worker tests.test_sync_daemon
```

3. Start API server bound to all interfaces:

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
```

4. Confirm server health from another machine:

```bash
curl "http://<server-ip>:8000/api/sync/health"
```

5. Keep this process running (tmux/screen/systemd recommended for long-running use).

### B) Client Setup (each device)

Do this on every laptop/desktop that should contribute data.

1. Configure sync target in your settings file (`config/my-settings.yaml` recommended):

```yaml
sync_base_url: http://<server-ip>:8000
sync_token: <device-token-from-register>
sync_interval_seconds: 60
sync_batch_size: 1000
sync_pull_limit: 1000
sync_max_pull_pages: 20
```

2. Ensure local schema/backfill is applied:

```bash
uv run python -m main --config config/my-settings.yaml sync migrate
```

3. Register user/device with server (one-time per device):

```bash
curl -X POST "http://<server-ip>:8000/api/sync/v1/devices/register" \
	-H "Content-Type: application/json" \
	-d '{
		"user": {"id": "user-1", "name": "Your Name"},
		"device": {
			"id": "device-office-laptop",
			"name": "Office Laptop",
			"type": "work",
			"hostname": "OFFICE-01",
			"category": "windows"
		}
	}'
```

4. Copy `device_token` from response into `sync_token` in `config/my-settings.yaml`.

5. Run one sync cycle to validate credentials and connectivity:

```bash
uv run python -m main --config config/my-settings.yaml sync once
```

6. Start continuous sync daemon:

```bash
uv run python -m main --config config/my-settings.yaml sync daemon
```

7. Start local collector + dashboard as usual (single-device UX remains local-first):

```bash
uv run python -m services.unified_launcher --config config/my-settings.yaml --port 4000
```

## Pre-Launch Checklist

Run these in order.

1. Unit tests (run on **Server** and optional on each **Client** checkout):

```bash
uv run python -m unittest tests.test_sync_api tests.test_sync_worker tests.test_sync_daemon
```

2. Start server API on the aggregation node (**Server**):

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
```

3. Start at least one data-producing device (**Client**) with collector + sync daemon running.

4. Register two devices for one user and push data from both (**Client**).

5. Open dashboard in sync mode (**Server UI**):

- `http://<server-ip>:8000/?source=sync&user_id=<user_id>`

6. Validate device filtering (**Server UI**):

- `http://<server-ip>:8000/?source=sync&user_id=<user_id>&device_id=<device_id>`

7. Validate stats APIs (**Server** or any machine with network access):

```bash
curl "http://<server-ip>:8000/api/sync/stats?user_id=<user_id>&days=30"
curl "http://<server-ip>:8000/api/stats?source=sync&user_id=<user_id>&days=30"
```

8. Rebuild rollups and re-check stats (**Server** or any machine with network access):

```bash
curl -X POST "http://<server-ip>:8000/api/sync/rollups/rebuild?user_id=<user_id>"
```

9. Offline resilience check (stop/start on **Server**, observe behavior on **Client**):

1. Stop sync server.
2. Keep collector and dashboard running.
3. Confirm local sessions continue accumulating.
4. Restart sync server and confirm sync resumes.

## Client -> Server Sync Flow (Detailed)

Use this sequence when onboarding each new device.

### Step 1: Register Device (Client calling Server)

Run on **Client**:

```bash
curl -X POST "http://<server-ip>:8000/api/sync/v1/devices/register" \
	-H "Content-Type: application/json" \
	-d '{
		"user": {"id": "user-1", "name": "Your Name"},
		"device": {
			"id": "device-office-laptop",
			"name": "Office Laptop",
			"type": "work",
			"hostname": "OFFICE-01",
			"category": "windows"
		}
	}'
```

Expected response from **Server**:

```json
{
	"device_token": "...",
	"server_time": "..."
}
```

### Step 2: Store Sync Config (Client)

Set these values on **Client** in `config/my-settings.yaml`:

```yaml
sync_base_url: http://<server-ip>:8000
sync_token: <device_token>
sync_interval_seconds: 60
sync_batch_size: 1000
sync_pull_limit: 1000
sync_max_pull_pages: 20
```

### Step 3: Migrate Local Schema (Client)

Run on **Client**:

```bash
uv run python -m main --config config/my-settings.yaml sync migrate
```

This ensures local rows have sync metadata required for push.

### Step 4: Send Initial Batch (Client -> Server)

Run one cycle on **Client**:

```bash
uv run python -m main --config config/my-settings.yaml sync once
```

What happens:

1. Client reads unsynced local changes.
2. Client sends `/api/sync/v1/push` to Server.
3. Server upserts and returns `next_push_cursor`.
4. Client calls `/api/sync/v1/pull` to fetch remote changes.
5. Client stores new cursors in local sync state.

### Step 5: Run Continuous Sync (Client)

Run on **Client**:

```bash
uv run python -m main --config config/my-settings.yaml sync daemon
```

This keeps pushing new local records and pulling remote updates continuously.

### Step 6: Verify Data Arrived (Server)

Run on **Server** (or any networked machine):

```bash
curl "http://<server-ip>:8000/api/sync/stats?user_id=<user_id>&days=30"
curl "http://<server-ip>:8000/api/sync/devices?user_id=<user_id>"
```

Open dashboard on **Server**:

- `http://<server-ip>:8000/?source=sync&user_id=<user_id>`

If values are present per device/user, client-to-server sync is working.

## Launch Decision Gate

Feature is launch-ready when all are true:

1. Test suite above is green.
2. Sync-mode dashboard renders metrics from synced data.
3. Device filter returns expected split.
4. Rollup rebuild endpoint works for historical range.
5. Offline/online recovery works without local data loss.