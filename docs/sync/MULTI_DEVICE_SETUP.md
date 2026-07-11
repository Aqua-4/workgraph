# Multi-Device Sync Setup Guide

This guide explains how to configure one server and multiple devices so WorkGraph data syncs through the sync API.

## 1. Architecture

- Each device keeps its own local SQLite database.
- A central server hosts the WorkGraph sync API and server SQLite database.
- Devices push local changes and pull remote changes through HTTP endpoints.

## 2. Prerequisites

On every machine (server and clients):
- `uv` installed
- WorkGraph repository available
- Network connectivity from devices to server

Recommended:
- Use fixed hostnames or static IP for the server.
- Keep server and clients on reasonably synced system clocks.

## 3. Server Setup (Raspberry Pi or Linux host)

1. Open the project folder.
2. Install dependencies:

```bash
uv sync
```

3. Start the sync API server:

```bash
uv run python api/app.py
```

By default, this binds to `127.0.0.1:8000` (localhost only).

For LAN discoverability across devices, start with:

```bash
WORKGRAPH_API_HOST=0.0.0.0 WORKGRAPH_API_PORT=8000 uv run python api/app.py
```

You can also use Uvicorn directly:

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
```

For internet exposure, prefer a reverse proxy (for example, Nginx) with HTTPS instead of opening the service directly.

4. Verify server health endpoint:

```bash
curl http://<SERVER_HOST>:8000/api/sync/health
```

You should receive a JSON response with sync health counters.

## 4. Device Setup (Repeat per device)

### 4.1 Install and prepare local DB

1. In the device project folder:

```bash
uv sync
```

2. Create a per-user settings override file (recommended):

```bash
cp config/settings.yaml config/my-settings.yaml
```

3. Run local sync migration once:

```bash
uv run python main.py sync migrate
```

This upgrades or backfills local schema metadata required for sync.

### 4.2 Create stable user and device IDs

Choose one `user_id` shared across all your devices, and one unique `device_id` per device.

Example IDs (UUID format recommended):
- `user_id`: `9f5aa674-6e53-4c4e-8a65-92f4f6f2f7a1`
- `device_id` for laptop: `f1b44e95-3f7f-4a24-9e3f-8cf2e8bcdb87`
- `device_id` for desktop: `f0a5198f-62d1-464f-ab11-f0fd5ed4c2c3`

### 4.3 Register device and get sync token

Use the sync register endpoint once per device.

```bash
curl -X POST http://<SERVER_HOST>:8000/api/sync/v1/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "user": {
      "id": "<USER_ID>",
      "name": "<USER_NAME>"
    },
    "device": {
      "id": "<DEVICE_ID>",
      "name": "<DEVICE_NAME>",
      "type": "personal",
      "hostname": "<HOSTNAME>",
      "category": "personal"
    }
  }'
```

Response includes:
- `device_token`
- `server_time`

Save the `device_token` safely for that device.

### 4.4 Configure device sync settings

In `config/my-settings.yaml` on that device, set:

```yaml
database_path: activity.db
identity_path: config/identity.json
sync_base_url: http://<SERVER_HOST>:8000
sync_token: <DEVICE_TOKEN>
sync_batch_size: 1000
sync_pull_limit: 1000
sync_max_pull_pages: 20
sync_timeout_seconds: 10
sync_interval_seconds: 60
sync_backoff_base_seconds: 1
sync_backoff_max_seconds: 60
```

Notes:
- Keep `identity_path` unique per local install (default is fine).
- `sync_token` must match the token from register response.

## 5. First Sync Workflow

Run this sequence on each new device after setup.

1. Run migration:

```bash
uv run python main.py sync migrate
```

2. Run one sync cycle:

```bash
uv run python main.py sync once
```

3. Verify dashboard/health on server:

```bash
curl http://<SERVER_HOST>:8000/api/sync/health
```

## 6. Continuous Sync (Daemon)

To keep device data continuously synced:

```bash
uv run python main.py sync daemon
```

Useful overrides:

```bash
uv run python main.py sync daemon \
  --interval-seconds 30 \
  --backoff-base-seconds 1 \
  --backoff-max-seconds 60
```

## 7. Recommended Rollout Order for Multiple Devices

1. Set up and validate server.
2. Set up primary device and confirm push/pull works.
3. Register second device and run first sync workflow.
4. Repeat for additional devices.
5. Enable daemon mode on each device.

## 8. Troubleshooting

### Missing `sync_base_url` or `sync_token`

Symptoms:
- CLI prints:
  - `sync_base_url missing. Pass --base-url or set sync_base_url in config.`
  - `sync_token missing. Pass --token or set sync_token in config.`

Fix:
- Update `config/my-settings.yaml`, or pass `--base-url` and `--token` explicitly.

### Auth failures during push/pull

Symptoms:
- HTTP 401/403 from sync endpoints.

Fix:
- Re-check token from register response.
- Ensure token belongs to the same `user_id` and `device_id` used by that device.

### Legacy DB records not syncing correctly

Fix:
- Re-run:

```bash
uv run python main.py sync migrate
```

### Server reachable locally but not from other devices

Fix:
- Verify firewall and network routing.
- Expose API through reverse proxy or host networking so clients can access it.

## 9. Operational Notes

- Keep sync optional: devices can still run local-only if sync is disabled.
- Back up server SQLite database regularly.
- Treat `sync_token` as secret credential.
- Use HTTPS when exposing sync over non-local networks.
- Keep secrets and per-device values in local config files (for example, `config/my-settings.yaml`) rather than committed shared files.
