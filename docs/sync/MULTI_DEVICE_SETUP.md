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

5. Determine server IP for client devices:

```bash
hostname -I
```

Use one reachable LAN IP from that output as `<SERVER_HOST>` in all client configuration.

6. Confirm port is reachable from another device:

```bash
curl http://<SERVER_HOST>:8000/api/sync/health
```

If this fails, open firewall access for TCP 8000 on the server and verify both devices are on the same network.

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

3. (Recommended) Let identity use per-user override path by default.

If your `config/my-settings.yaml` contains `identity_path: config/identity.json`, either remove that line or change it to `identity_path: config/my-identity.json`.

When identity path resolves to the default `config/identity.json`, WorkGraph now prefers `config/my-identity.json` automatically if that file exists.

4. Run local sync migration once:

```bash
uv run python main.py sync migrate
```

This upgrades or backfills local schema metadata required for sync and creates the identity file if missing.

5. Validate identity file path and device ID:

```bash
cat config/my-identity.json
```

The file should contain a stable device identity JSON object. Do not reuse one device's identity file on another device.

### 4.2 Create stable user and device IDs

Use one shared `user_id` across all your devices and a unique `device_id` per device.

WorkGraph identity file fields (`config/my-identity.json`):

- `user_id`: stable identifier for the person (same across all that user's devices)
- `device_id`: stable identifier for this device only (must be unique per device)
- `user_name`: display name sent during registration
- `device_name`: readable device label (for example, "Office Laptop")
- `device_type`: logical category (`desktop`, `laptop`, `server`, `personal`, `work`)

Example file:

```json
{
  "user_id": "9f5aa674-6e53-4c4e-8a65-92f4f6f2f7a1",
  "device_id": "f1b44e95-3f7f-4a24-9e3f-8cf2e8bcdb87",
  "user_name": "Parashar",
  "device_name": "Office Laptop",
  "device_type": "laptop"
}
```

Maintenance rules:

1. Keep `user_id` identical on all devices belonging to the same person.
2. Keep `device_id` unique per device and never copy one device's file to another.
3. Keep `user_name` and `device_name` human-readable for easier server-side operations.
4. Keep `device_type` stable unless the device role actually changes.
5. Back up this file locally if you rebuild the machine and want to preserve identity continuity.

If file is missing, migration creates defaults. You can edit values after first generation and rerun sync.

### 4.3 Register device and get sync token

Use the sync register endpoint once per device.

Suggested `type` and `category` values:

- `type`: `desktop`, `laptop`, `server`, `work`, `personal`
- `category`: `work`, `personal`, `home-lab`, `shared`, `test`

Recommended convention:

1. Use `type` for machine form factor or role (`laptop`, `desktop`, `server`).
2. Use `category` for ownership/context (`work`, `personal`, `shared`, `test`).

Example pairings:

- Work laptop: `type=laptop`, `category=work`
- Personal desktop: `type=desktop`, `category=personal`
- Raspberry Pi sync node: `type=server`, `category=home-lab`
- CI/test runner: `type=server`, `category=test`

Instead of exporting shell variables, build request data directly from `config/my-identity.json`:

```bash
jq -n --argfile ident config/my-identity.json '{
  user: {
    id: $ident.user_id,
    name: $ident.user_name
  },
  device: {
    id: $ident.device_id,
    name: $ident.device_name,
    type: $ident.device_type,
    hostname: $ident.device_name,
    category: $ident.device_type
  }
}' | curl -X POST http://<SERVER_HOST>:8000/api/sync/v1/devices/register \
  -H "Content-Type: application/json" \
  --data-binary @-
```

Fallback without `jq` (manual payload):

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

Optional: save token back into local settings in one step:

```bash
# Replace <TOKEN> with returned device_token
sed -i 's|^sync_token:.*$|sync_token: "<TOKEN>"|' config/my-settings.yaml
```

### 4.4 Configure device sync settings

In `config/my-settings.yaml` on that device, set:

```yaml
database_path: activity.db
identity_path: config/my-identity.json
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
- Keep `identity_path` unique per local install.
- `sync_token` must match the token from register response.

Example final per-device validation:

```bash
uv run python main.py sync once --base-url http://<SERVER_HOST>:8000 --token <DEVICE_TOKEN>
```

This verifies connectivity and credentials even before you persist values in settings.

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

### Device identity mismatch after cloning or copying config

Symptoms:
- A device appears as another machine in server device listings.
- Sync writes look like they come from the wrong host.

Fix:
- Ensure each device has its own `config/my-identity.json`.
- Do not copy identity files between devices.
- If needed, remove local identity and rerun migration/sync to regenerate:

```bash
rm -f config/my-identity.json
uv run python main.py sync migrate
```

## 9. Operational Notes

- Keep sync optional: devices can still run local-only if sync is disabled.
- Back up server SQLite database regularly.
- Treat `sync_token` as secret credential.
- Use HTTPS when exposing sync over non-local networks.
- Keep secrets and per-device values in local config files (for example, `config/my-settings.yaml`) rather than committed shared files.
- Keep per-device identity in `config/my-identity.json` (gitignored), not in shared tracked files.
