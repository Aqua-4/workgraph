# WorkGraph Sync Implementation Spec v1

Status: Draft for implementation
Owner: WorkGraph
Last updated: 2026-07-11

## 1) Purpose

Define an implementation-ready design for multi-device sync using:
- Local SQLite per device as the collection source.
- Raspberry Pi sync API with server-side SQLite as the aggregation layer.
- Device-first behavior (offline-safe, eventually consistent).

This document translates architecture ideas into concrete schema, API, and migration requirements.

## 2) Scope

In scope (v1):
- Sync for sessions, journal entries, daily reflections.
- Device and user identity tables.
- Incremental push/pull protocol with idempotency.
- Conflict handling for mutable records.
- Tombstone-based delete replication.

Out of scope (v1):
- End-to-end encryption.
- Peer-to-peer sync (device-device direct).
- Real-time websockets.
- Advanced merge UX.

## 2.1) Operating Modes

WorkGraph supports two valid operating modes:

- Single-device mode (default): local SQLite only, no sync service required.
- Multi-device mode (optional): local SQLite on each device plus sync to Raspberry Pi sync API and server SQLite.

Single-device mode must remain fully functional for collection, dashboard, journal, and reports. Multi-device sync is an additive capability for users who want aggregated tracking across devices.

## 3) Current State (Codebase Baseline)

Current implementation is single-node SQLite:
- `activity_sessions`, `journal_entries`, `daily_reflections` are local-only.
- Primary IDs are mostly INTEGER AUTOINCREMENT.
- No `device_id`, no per-row UUIDs, no sync metadata.
- Server sync backend is not implemented yet (v1 target: SQLite on server).

Implication: schema and repository APIs must be upgraded before sync API can be reliable.

## 4) Design Principles

- Local first: collection never blocks on network.
- Aggregation, not authority: Pi is global query engine, not the only writable source.
- Idempotent writes: retries must not duplicate records.
- Deterministic conflict policy: same input yields same outcome.
- Append-friendly events with explicit update metadata.

## 5) Canonical Entity Model

All sync entities include these required fields:
- `id` (UUID v7 preferred; UUID v4 acceptable)
- `created_at` (UTC ISO-8601)
- `updated_at` (UTC ISO-8601)
- `device_id` (UUID)
- `user_id` (UUID)
- `deleted_at` (nullable UTC ISO-8601; tombstone marker)

Notes:
- Do not use local integer IDs as cross-device identity.
- `deleted_at IS NOT NULL` means soft-deleted and must sync.

## 6) Schema Changes (SQLite)

### 6.1 users

```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 6.2 devices

```sql
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    hostname TEXT,
    category TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id);
```

### 6.3 activity_sessions

Required additive columns for existing table:

```sql
ALTER TABLE activity_sessions ADD COLUMN uuid TEXT;
ALTER TABLE activity_sessions ADD COLUMN user_id TEXT;
ALTER TABLE activity_sessions ADD COLUMN device_id TEXT;
ALTER TABLE activity_sessions ADD COLUMN updated_at TEXT;
ALTER TABLE activity_sessions ADD COLUMN deleted_at TEXT;
```

Post-migration constraints:
- `uuid` unique, non-null for all rows.
- `(user_id, device_id)` non-null for all rows.
- `updated_at` non-null for all rows.

Indexes:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_sessions_uuid
    ON activity_sessions(uuid);
CREATE INDEX IF NOT EXISTS idx_activity_sessions_device_updated
    ON activity_sessions(device_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_activity_sessions_user_updated
    ON activity_sessions(user_id, updated_at);
```

### 6.4 journal_entries

Add columns:

```sql
ALTER TABLE journal_entries ADD COLUMN uuid TEXT;
ALTER TABLE journal_entries ADD COLUMN user_id TEXT;
ALTER TABLE journal_entries ADD COLUMN device_id TEXT;
ALTER TABLE journal_entries ADD COLUMN updated_at TEXT;
ALTER TABLE journal_entries ADD COLUMN deleted_at TEXT;
```

Indexes:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_entries_uuid
    ON journal_entries(uuid);
CREATE INDEX IF NOT EXISTS idx_journal_entries_user_updated
    ON journal_entries(user_id, updated_at);
```

### 6.5 daily_reflections

Add columns:

```sql
ALTER TABLE daily_reflections ADD COLUMN uuid TEXT;
ALTER TABLE daily_reflections ADD COLUMN user_id TEXT;
ALTER TABLE daily_reflections ADD COLUMN device_id TEXT;
ALTER TABLE daily_reflections ADD COLUMN deleted_at TEXT;
```

Keep `date` unique per user:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_reflections_user_date
    ON daily_reflections(user_id, date)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_reflections_uuid
    ON daily_reflections(uuid);
```

## 7) Sync Metadata Tables

### 7.1 Device local sync state (SQLite)

```sql
CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    last_push_cursor TEXT,
    last_pull_cursor TEXT,
    updated_at TEXT NOT NULL
);
```

### 7.2 Server-side checkpoints (SQLite)

```sql
CREATE TABLE IF NOT EXISTS sync_checkpoints (
    device_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    last_push_cursor TEXT,
    last_pull_cursor TEXT,
  updated_at TEXT NOT NULL
);
```

Cursor format:
- String token from server, opaque to client.
- Recommended payload: monotonic `updated_at` + tie-break `id`.

## 8) API Contract (Sync Service)

Base path: `/api/sync/v1`

Auth (v1 minimum):
- `Authorization: Bearer <device_token>`
- Token binds to one `device_id` and one `user_id`.

### 8.1 Register device

`POST /devices/register`

Request:

```json
{
  "user": {
    "id": "usr_...uuid...",
    "name": "Parashar"
  },
  "device": {
    "id": "dev_...uuid...",
    "name": "Office Laptop",
    "type": "work",
    "hostname": "LAT-001",
    "category": "employer"
  }
}
```

Response:

```json
{
  "device_token": "opaque-token",
  "server_time": "2026-07-11T12:30:00Z"
}
```

### 8.2 Push changes

`POST /push`

Request:

```json
{
  "device_id": "...",
  "user_id": "...",
  "client_cursor": "opaque-cursor-or-null",
  "batch_id": "uuid",
  "changes": {
    "sessions": [ ... ],
    "journal_entries": [ ... ],
    "daily_reflections": [ ... ]
  }
}
```

Response:

```json
{
  "accepted": {
    "sessions": 120,
    "journal_entries": 4,
    "daily_reflections": 2
  },
  "conflicts": [],
  "next_push_cursor": "opaque-cursor",
  "server_time": "2026-07-11T12:31:00Z"
}
```

Rules:
- `batch_id` is idempotency key.
- Replaying same `batch_id` must return same result.
- Server processes by upsert policy and records checkpoint.

### 8.3 Pull changes

`POST /pull`

Request:

```json
{
  "device_id": "...",
  "user_id": "...",
  "cursor": "opaque-cursor-or-null",
  "limit": 1000
}
```

Response:

```json
{
  "changes": {
    "sessions": [ ... ],
    "journal_entries": [ ... ],
    "daily_reflections": [ ... ],
    "tombstones": [
      {"entity": "sessions", "id": "...", "deleted_at": "..."}
    ]
  },
  "next_cursor": "opaque-cursor",
  "has_more": false,
  "server_time": "2026-07-11T12:32:00Z"
}
```

## 9) Conflict Resolution

### 9.1 Immutable records

Recommended: activity sessions are append-only in normal operation.
- If same `uuid` exists and payload differs, accept record with latest `updated_at`.
- Keep audit log entry for collision diagnostics.

### 9.2 Mutable records (journal/reflections)

Use Last-Write-Wins by `(updated_at, id)`:
- Newer `updated_at` wins.
- If equal, lexicographically higher `id` wins (deterministic tie-break).

### 9.3 Deletes

Delete is an update setting `deleted_at`.
- A tombstone with newer `updated_at` beats older non-deleted row.
- Hard delete is never done in v1 sync path.

## 10) Idempotency and Reliability

Server table for processed batches:

```sql
CREATE TABLE IF NOT EXISTS sync_batches (
    batch_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

Rules:
- If same `batch_id` + same `request_hash`: return stored response.
- If same `batch_id` + different hash: return 409 conflict.
- Client retries push/pull with exponential backoff and jitter.

## 11) Local Repository Interface Changes

Add repository methods:
- `save_session` -> generates `uuid` if absent and writes sync fields.
- `list_changes_since(cursor, limit)` for each entity.
- `upsert_session_by_uuid(payload)`.
- `upsert_journal_by_uuid(payload)`.
- `upsert_reflection_by_uuid(payload)`.
- `mark_deleted(entity, id, deleted_at, updated_at)`.
- `get_sync_state()` and `update_sync_state(...)`.

All write methods must set `updated_at = now_utc()`.

## 12) Migration Plan

### Phase A: Local schema preparation

1. Add sync columns and indexes in SQLite.
2. Backfill:
   - Generate UUID for existing rows.
   - Set `user_id` and `device_id` from local identity config.
   - Set `updated_at = created_at` when missing.
3. Add guards in repository to ensure new writes always include sync metadata.

### Phase B: Identity layer

1. Create device identity file (local config) with stable UUID.
2. Add registration flow against server.
3. Persist token and sync cursors.

### Phase C: Sync service (Pi)

1. Implement server SQLite schema.
2. Implement `/devices/register`, `/push`, `/pull`.
3. Implement idempotency (`sync_batches`) and checkpointing.

### Phase D: Client sync worker

1. Add periodic background job (configurable interval).
2. Push local changes in batches.
3. Pull remote changes until `has_more=false`.
4. Update cursors only after successful commit.

### Phase E: Dashboard and reports

1. Add per-device and per-category filters.
2. Surface sync status: last push/pull time, pending changes, last error.

## 13) Testing Strategy

Unit tests:
- UUID generation and backfill.
- Upsert semantics by UUID.
- Conflict policy (older vs newer updates).
- Tombstone handling.
- Idempotent batch replay behavior.

Integration tests:
- Offline create -> reconnect -> sync -> verify server records.
- Two devices editing same journal -> deterministic resolution.
- Partial failure and retry does not duplicate writes.
- Pull pagination with stable cursor progression.

Load/safety tests:
- Batch push of 100k sessions.
- Retry storm with duplicate `batch_id`.

## 14) Operational Considerations

- Server SQLite constraints enforce UUID uniqueness.
- Keep API payload size caps and max batch size.
- Structured logging per request: `device_id`, `user_id`, `batch_id`, counts, latency.
- Metrics: push success rate, conflict rate, lag seconds, pending local changes.

## 15) Implementation Checklist

1. Add UUID + sync metadata columns in local schema.
2. Implement migration/backfill command for existing local DB.
3. Add identity config and device registration.
4. Build server SQLite schema on Pi.
5. Build sync endpoints with idempotency and cursors.
6. Implement client sync worker with push/pull loops.
7. Add deterministic conflict handling and tombstones.
8. Add test coverage (unit + integration).
9. Add sync health section in dashboard.

## 16) Defaults for v1

- UUID format: UUIDv7 preferred.
- Time format: UTC ISO-8601, second precision or better.
- Sync interval: 60 seconds (configurable).
- Push batch size: 1000 rows.
- Pull page size: 1000 rows.
- Backoff: base 1s, max 60s, full jitter.

## 17) Deferred Decisions

- Encryption of payloads at rest and in transit beyond TLS.
- Per-field merge for journal text conflicts.
- PostgreSQL backend as optional v3 upgrade for higher scale analytics and ops.
- Cross-user sharing and team analytics.
- CRDT-based conflict-free edits.

---

This spec is intended to be implemented incrementally while keeping existing local collection stable. If any section conflicts with shipping constraints, preserve these invariants first: stable UUID identity, idempotent push, cursor-based pull, and deterministic conflict resolution.
