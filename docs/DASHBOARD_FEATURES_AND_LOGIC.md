# Dashboard Features and Logic

This document describes how the WorkGraph dashboard is rendered, which data is used in each mode, and what sections are visible to users.

## Entry Point and Mode Routing

Main route:

- `GET /` in `api/app.py` (`dashboard` handler)

Routing behavior:

1. Reads `dashboard_mode` from configuration.
2. If mode is `sync-server`, renders `api/templates/sync_server_dashboard.html`.
3. Otherwise (`standalone` or `sync-client`), renders `api/templates/dashboard.html`.

Key consequence:

- `sync-server` has a dedicated template and does not share the main local dashboard template.

## Modes and Data Sources

### 1) standalone

Template:

- `api/templates/dashboard.html`

Stats source:

- `get_summary_stats(db_path, days=7)` using local activity tables.

Registration behavior:

- Shows device registration form for local identity registration.

Sync health behavior:

- Sync Health block on the main dashboard is hidden.

### 2) sync-client

Template:

- `api/templates/dashboard.html`

Stats source:

- Sync-backed summary (`get_sync_summary_stats`) via user/device scope when sync source is active.

Registration and status behavior:

- If device is already registered (`client_sync.registered == true`), show `Sync Client Status` card.
- If device is not registered, show the `Device Registration` form with `sync_base_url` input.

Sync health behavior:

- Sync Health block on the main dashboard is hidden.

### 3) sync-server

Template:

- `api/templates/sync_server_dashboard.html`

Stats source:

- `get_sync_server_overview(db_path, user_id, device_id, days=7)`
- `get_sync_health(db_path)`

Scope behavior:

- `user_id` and `device_id` can be passed as filters in the top form.
- If `user_id` is omitted and users exist, the latest-updated user is selected by default.

## Sync Server Dashboard Sections

Rendered in `api/templates/sync_server_dashboard.html`:

1. **Configured Mode + Scope Filters**
   - Displays active mode badge.
   - Filter fields: `user_id`, `device_id`.

2. **Server Aggregation Scope**
   - Clarifies this view is server-only and aggregated.

3. **Sync Health**
   - Registered Devices (`sync_health.registered_devices`)
   - Synced Devices (`sync_health.synced_devices`)
   - Active Tokens (`sync_health.active_tokens`)
   - Last Sync (`sync_health.last_sync_at`)

4. **Aggregated Metrics (7 Days)**
   - Total Active Time
   - Focus Time
   - Meeting Time (Proxy)
   - Context Switches

5. **Selected User Breakdown** (conditional)
   - Only rendered when `overview.user_stats` exists.
   - Includes user-level summary metrics and distribution counts.

6. **Registered Users**
   - User ID, Name, Updated timestamp.

7. **Registered Devices**
   - Device ID, Name, User, Type, Last Seen.

8. **Daily Trend (Aggregated)**
   - UTC date, weekday label, active hours, meeting hours, switches/hour.

## How `get_sync_health` is Computed

`get_sync_health(db_path)` aggregates from sync tables:

- `sync_tokens` -> active token count (non-revoked)
- `sync_devices` -> registered device count and last seen
- `sync_checkpoints` -> last sync checkpoint timestamp
- `sync_devices LEFT JOIN sync_checkpoints` -> per-device sync status list
- `sync_request_logs` -> push/pull totals, success counts, conflicts, average latency, latest request

Returned object includes:

- `registered_devices`, `synced_devices`, `unsynced_devices`
- `active_tokens`
- `last_seen_at`, `last_sync_at`, `lag_seconds`
- `device_statuses`
- request/latency health metrics

## Current Visibility Rules (Important)

Main dashboard (`api/templates/dashboard.html`):

- Sync Health section is shown only when `dashboard_mode == "sync-server"`.
- In `sync-client` mode:
  - If registered: show sync client status (last synced time).
  - If not registered: show registration form.

Sync-server dashboard (`api/templates/sync_server_dashboard.html`):

- Always includes Sync Health, because this template is used only in `sync-server` mode.

## Files to Review Together

- `api/app.py` (mode routing and data shaping)
- `api/templates/dashboard.html` (standalone + sync-client view)
- `api/templates/sync_server_dashboard.html` (sync-server view)
- `tests/test_sync_api.py` (mode-aware dashboard assertions)
- `tests/test_api_journal.py` (standalone dashboard assertions)

## Suggested Review Checklist

1. Confirm mode routing always picks the expected template.
2. Confirm `sync-server` filters (`user_id`, `device_id`) match the expected aggregation scope.
3. Confirm sync-client registration/status transition behaves correctly after first registration.
4. Confirm no standalone-only data appears in sync-client mode.
5. Confirm Sync Health is only visible where intended.