# Server Aggregate Metrics Approach

Status: Implemented (current baseline)
Owner: WorkGraph
Last updated: 2026-07-12

## 1) Goal

Document how server dashboard metrics are computed from synced multi-device data so totals represent all registered devices for a user, not only local `activity_sessions`.

## 2) Current Behavior

- Sync data is persisted on server in:
  - `sync_sessions`
  - `sync_journal_entries`
  - `sync_daily_reflections`
- Sync health and registration indicators are server-backed and working.
- Dashboard summary metrics support explicit sync-backed computation.

Result:
- Sync status is accurate.
- Sync-server mode time allocation metrics are computed as cross-device aggregates.

## 3) Target Behavior

When server is used as sync hub, dashboard/timeline/reporting compute aggregates from `sync_sessions` payloads by user scope.

Implemented outcomes:
- Total active time includes all synced devices for selected user.
- App/tag/repo breakdowns represent cross-device totals.
- Optional filtering supports per-device drilldown.

## 4) Data Source Strategy

### 4.1 Primary Source

Use `sync_sessions.payload_json` as canonical metric source for aggregated dashboards.

Rationale:
- Already includes normalized session payload pushed from clients.
- Preserves UUID identity, user/device IDs, and sync update semantics.
- Avoids dual-write complexity into `activity_sessions`.

### 4.2 Scope

Metrics should be queryable by:
- `user_id` (required in server aggregate mode)
- `device_id` (optional filter)
- date/time window (`days`, `from`, `to`)

### 4.3 Deletion Handling

Ignore rows with non-null `deleted_at` for active metrics.

### 4.4 Deduplication / Conflict

Use table-level UUID uniqueness and upsert semantics already enforced in sync endpoints.
No extra dedupe layer required for standard reads.

## 5) API and Query Design

## 5.1 Aggregate query helpers

Implemented server-side helpers in API layer include:
- `query_sync_sessions(...)`
- `get_sync_summary_stats(...)`

Behavior:
- Parse `payload_json` rows from `sync_sessions`.
- Apply user/device/date filters.
- Build same output structure as current `get_summary_stats(...)` so templates remain compatible.

## 5.2 Source mode switch

Source mode selection is implemented:
- `local`: current `activity_sessions` path
- `sync`: server aggregate path using `sync_sessions`

Triggers currently used:
1. Dashboard runtime mode in config (`dashboard_mode`).
2. Query parameter override paths where supported (`source=sync`).

## 5.3 User selection

Because server may contain multiple users, require explicit user scope in aggregate mode:
- Option A: query parameter `user_id`.
- Option B: default to most recently active user and show selector.

Current baseline:
- `user_id` is supported with selectors sourced from `sync_users` in sync-server flows.

## 6) UI Updates

### 6.1 Dashboard

Add small source badge in Sync Health / summary header:
- `Source: Local DB` or `Source: Synced Devices`.

### 6.2 Filters

Add optional filters when in sync mode:
- user selector
- device selector (All devices default)

### 6.3 Empty states

If user is registered but no synced sessions:
- Show: "Device registered, but no synced activity sessions yet."

## 7) Performance Considerations

- Use indexed columns on `sync_sessions` (`user_id`, `updated_at`, `uuid`).
- For larger scale, consider a materialized summary table refreshed periodically:
  - `sync_metrics_daily(user_id, device_id, day, active_seconds, switches, ... )`
- Start with direct reads; add rollups only when query latency becomes noticeable.

## 8) Security and Isolation

- Never aggregate across users without explicit scope.
- Validate `user_id` exists in `sync_users` before aggregate queries.
- Keep internal server APIs private if server is single-tenant.

## 9) Rollout Status

Completed:
- Read helpers over `sync_sessions` and sync rollups.
- Explicit aggregate mode and user/device-scoped filtering.
- Dashboard selectors and sync-server dashboard template.

In progress / next:
- Expand pre-aggregation and operational tooling as dataset size grows.

## 10) Validation Plan

Functional:
- Two devices with known synthetic hours -> dashboard total equals combined hours.
- Device filter returns correct per-device subset.
- Deleted sessions are excluded.

Regression:
- Existing local dashboard behavior unchanged when `source=local`.
- Sync health indicators remain intact.

Operational:
- Verify response times under expected data volume.
- Add logs for source mode and selected user/device.

## 11) Open Decisions

- Where to source default `user_id` when multiple users exist.
- Whether to support mixed mode (local + sync) in same chart.
- When to introduce summary rollups vs direct JSON parsing.

## 12) Current Readiness

This approach is implemented as the current sync-server analytics baseline.
Further improvements should focus on scale/performance optimizations and operational runbooks.
