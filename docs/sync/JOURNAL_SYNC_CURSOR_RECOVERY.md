# Journal Sync Cursor Recovery

Status: Draft runbook for review
Owner: WorkGraph
Last updated: 2026-07-20

## 1) Problem Summary

In rare cases, a device can miss journal entries from another device even though sync health is green.

Observed behavior:

- Entry exists on sync server.
- Entry does not exist in the target device local `journal_entries` table.
- Target device `last_pull_cursor` is newer than the missing journal row `updated_at`.

This is a cursor ordering gap, not an auth/network outage.

Scope of this runbook:

- Applies to missing `Recent Journal Entries` and `Recent Reflections` that are part of sync payload pull pagination.
- Does not apply to `Recent Work Events` because `work_events` are currently local journal features and are not included in the sync push/pull payload.

## 2) Why It Happens

Current pull pagination is ordered by `updated_at` and UUID across the union stream.
If a row is uploaded later but carries an older `updated_at` value, it can fall behind another device's already-advanced cursor.

Result: normal pull cycles skip that row forever.

## 3) Safe Manual Recovery (No Code Change)

Use this on the affected target device (example: Linux-PC).

### 3.1 Create a backup first

```bash
uv run python -m main --config config/my-settings.yaml backup create
```

### 3.2 Inspect current local sync cursor

```bash
uv run python - <<'PY'
import sqlite3
conn = sqlite3.connect('activity.db')
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT last_pull_cursor, last_push_cursor, updated_at FROM sync_state WHERE id=1").fetchone()
print(dict(row) if row else None)
conn.close()
PY
```

### 3.3 Rewind only `last_pull_cursor`

Pick a rewind point before the missing entry time. Example uses `2026-07-16T00:00:00+00:00|`.

```bash
uv run python - <<'PY'
import sqlite3
conn = sqlite3.connect('activity.db')
conn.execute(
    "UPDATE sync_state SET last_pull_cursor = ?, updated_at = datetime('now') WHERE id = 1",
    ("2026-07-16T00:00:00+00:00|",),
)
conn.commit()
conn.close()
print('last_pull_cursor rewound')
PY
```

Notes:

- Do not change `last_push_cursor` unless you are intentionally replaying uploads.
- Re-pulled rows are safe because upserts are UUID-based.

### 3.4 Run catchup sync

```bash
uv run python -m main --config config/my-settings.yaml sync catchup --max-cycles 50 --settle-cycles 3
```

### 3.5 Verify the journal row now exists locally

```bash
uv run python - <<'PY'
import sqlite3
conn = sqlite3.connect('activity.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT uuid, device_id, created_at, updated_at, start_time, title
    FROM journal_entries
    WHERE COALESCE(start_time, created_at) >= '2026-07-14'
    ORDER BY COALESCE(start_time, created_at) DESC
    """
).fetchall()
for r in rows:
    print(dict(r))
conn.close()
PY
```

### 3.6 Optional consistency check

```bash
uv run python -m main --config config/my-settings.yaml sync verify --max-pages 500
```

## 4) One-Line Manual Command Set

If you want a compact sequence after backup:

```bash
uv run python - <<'PY'
import sqlite3
conn = sqlite3.connect('activity.db')
conn.execute("UPDATE sync_state SET last_pull_cursor=?, updated_at=datetime('now') WHERE id=1", ("2026-07-16T00:00:00+00:00|",))
conn.commit()
conn.close()
print('rewind complete')
PY
uv run python -m main --config config/my-settings.yaml sync catchup --max-cycles 50 --settle-cycles 3
```

## 5) Recommended Product Fix (Code)

The robust fix is to paginate pull streams by server ingest order, not payload event time.

### 5.1 Option A (Preferred): server monotonic sequence cursor

Add a monotonically increasing ingest key to sync tables and cursor by that key.

Suggested schema additions:

- `sync_sessions.ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT` (or separate central stream table)
- Same for `sync_journal_entries` and `sync_daily_reflections`, or unified stream table.

Then:

- `next_cursor` uses `ingest_seq`.
- Pull query becomes `WHERE ingest_seq > ? ORDER BY ingest_seq ASC`.

Benefits:

- Late-arriving historical rows are never skipped.
- Pagination is deterministic and independent of client-provided timestamps.

### 5.2 Option B: hybrid lookback window

Keep timestamp cursor but always include a trailing lookback window (for example 7 days) and rely on upsert idempotency.

Trade-offs:

- Simpler migration.
- Higher repeated payload volume.
- Still heuristic, not perfect at very old late arrivals.

## 6) Operational Guardrail

Add an automated alert if a device has:

- `pending_journal_entries == 0`, and
- server has journal rows for the same user from other devices newer than the device's oldest missing local range.

This can detect silent skips early.

## 7) Decision Checklist

Before applying manual rewind:

1. Confirm missing row is present on server for the same `user_id`.
2. Confirm target device `last_pull_cursor` is ahead of missing row `updated_at`.
3. Take backup.
4. Rewind pull cursor and run catchup.
5. Verify local presence and dashboard/journal rendering.

## 8) Follow-Up Plan: Sync Work Events End-to-End

This section describes the implementation needed so `Recent Work Events` syncs across devices like journal entries and reflections.

### 8.1 Current Gap

- `work_events` are stored and rendered locally in journal features.
- Sync push/pull payload currently includes only sessions, journal entries, and daily reflections.
- Result: work events created on one device do not appear on other devices.

### 8.2 Target Behavior

- Work events are included in sync push/pull payloads.
- Server persists synced work events in a dedicated sync table.
- Clients upsert pulled work events into local `work_events`.
- Journal page shows cross-device work events after sync.

### 8.3 Data Model Changes

Local table (`work_events`):

- Ensure fields required for sync parity are present:
  - `uuid` (stable row id)
  - `user_id`
  - `device_id`
  - `created_at`
  - `updated_at`
  - `deleted_at` (for tombstones)

Server sync table (new):

- Add `sync_work_events` analogous to `sync_journal_entries`:
  - `uuid`, `user_id`, `device_id`, `created_at`, `updated_at`, `deleted_at`, `payload_json`
  - user-scoped uniqueness (`UNIQUE(user_id, uuid)`)
  - runtime index (`user_id, updated_at, uuid`)

### 8.4 API Contract Changes

Extend sync payload schema:

- Push request changes:
  - add `changes.work_events: list[object]`
- Pull response changes:
  - add `changes.work_events: list[object]`

Server handlers:

- Push (`/api/sync/v1/push`):
  - ingest `work_events` rows into `sync_work_events`
  - include count in accepted stats
- Pull (`/api/sync/v1/pull`):
  - include `sync_work_events` in union stream ordering
  - emit active rows and work-event tombstones

### 8.5 Client Sync Changes

Repository layer:

- Add `list_work_event_changes_since(cursor, limit)`
- Add `upsert_work_event_by_uuid(payload)`
- Extend delete-marking helper to support work events entity mapping

Worker layer:

- Push path:
  - include local work event changes in `changes.work_events`
- Pull path:
  - apply pulled work events via upsert
  - apply work event tombstones

### 8.6 Migration and Backfill

Local DB migration:

- Add sync columns/indexes to `work_events` if missing.
- Backfill `uuid` and `updated_at` for existing rows.
- Set `user_id` and `device_id` defaults from local identity where missing.

Server DB migration:

- Create `sync_work_events`.
- Add user-scoped uniqueness/indexes in the same style as other sync entities.

Backfill policy:

- Existing local work events are eligible for next push after migration.
- Do not overwrite newer server rows with older local updates.

### 8.7 Cursor Safety

If timestamp cursoring remains in place:

- work events inherit the same late-arrival risk documented in this runbook.

Recommended:

- implement monotonic server ingest-order cursors for all entities (sessions, journals, reflections, work events) in one change set.

### 8.8 Rollout Plan

1. Ship schema migrations (server first, then clients).
2. Deploy API payload support guarded by backward-compatible defaults (`work_events` optional).
3. Deploy client worker/repository changes.
4. Run catchup on clients.
5. Verify work event parity across devices.

Compatibility rule:

- If one side is older, ignore unknown `work_events` key safely and keep other entities syncing.

### 8.9 Test Plan

Unit tests:

- repository upsert/list changes for work events
- worker push/pull includes work events
- tombstone delete handling for work events

API tests:

- push accepts work events and reports accepted counts
- pull returns work events with paging
- conflict resolution by `updated_at` for same `uuid`

Integration tests:

- Device A creates work event, Device B receives it after sync
- offline A creates old-timestamp event, then reconnects; B still receives event (after cursor fix)

Regression checks:

- existing journal/reflection/session sync behavior unchanged
- sync health counters remain accurate

### 8.10 Operational Verification Commands

After rollout, verify local and server counts include work events.

Example local check:

```bash
uv run python - <<'PY'
import sqlite3
conn = sqlite3.connect('activity.db')
row = conn.execute("SELECT COUNT(*) FROM work_events").fetchone()
print('local_work_events', row[0])
conn.close()
PY
```

Server check (after endpoint support exists):

- extend sync verify output to include `work_events` parity.
