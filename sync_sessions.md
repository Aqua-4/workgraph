This is a solid design and I think you're making the correct architectural decision by treating **`sync_sessions` as the canonical aggregation source** rather than trying to replicate everything into `activity_sessions`.

My review:

# Overall Assessment

I'd rate this:

**8.5/10 as-is**
**9.5/10 with a few modifications**

The biggest thing I'd change is preparing for future analytics now, even if you don't implement them immediately.

---

# What I Like

## 1. Single Source of Truth

This is the right choice:

```text
Client SQLite
     ↓
sync_sessions
     ↓
Aggregations
```

instead of:

```text
sync_sessions
     ↓
Transform
     ↓
activity_sessions
     ↓
Aggregations
```

The second architecture eventually creates:

* sync bugs
* duplicate storage
* migration headaches
* reconciliation problems

Keeping synced payloads canonical is cleaner.

---

## 2. User Scoping

Excellent.

Never do:

```sql
SELECT *
FROM sync_sessions
```

Always:

```sql
WHERE user_id = ?
```

Even if WorkGraph remains personal.

The moment:

* spouse
* colleague
* open-source contributors

use the same server, you'll be glad you built isolation early.

---

## 3. Device Filters

This is mandatory for your use case.

You already described:

```text
Office Laptop
Client Laptop
Home Desktop
Android
```

Without device filtering:

```text
VS Code
8h
```

becomes meaningless.

With device filtering:

```text
Office Laptop
VS Code
3h

Client Laptop
VS Code
5h
```

much more useful.

---

# Biggest Change I'd Make

## Stop parsing payload_json for analytics

Right now:

```text
sync_sessions
 └── payload_json
```

and metrics require:

```python
json.loads(...)
```

for every dashboard request.

Works today.

Becomes painful later.

---

### Recommended Addition

Store searchable fields alongside payload.

Example:

```sql
sync_sessions

uuid
user_id
device_id

started_at
ended_at

active_seconds

application_name
tag
repo_name

payload_json
```

Think:

```text
payload_json
=
audit log

columns
=
analytics layer
```

---

Why?

Because later you will want:

```sql
Top tags last 90 days
```

instead of:

```python
load 10000 JSON rows
parse
aggregate
```

---

# Add Daily Rollups Earlier

Your document says:

> Add rollups only when query latency becomes noticeable.

I would do the opposite.

Add rollups early.

---

Example:

```sql
sync_metrics_daily

user_id
device_id
day

active_seconds
focus_seconds

meeting_seconds

context_switches
```

Then:

```sql
Last 365 days
```

becomes:

```sql
365 rows
```

instead of:

```sql
500,000 session rows
```

---

# Add Device Metadata Table

I didn't see it explicitly mentioned.

I'd add:

```sql
sync_devices

device_id
user_id

device_name
device_type

last_seen

platform
hostname
```

Examples:

```text
Office Laptop
Windows

Client Laptop
Windows

Home Desktop
Linux

Pixel 9
Android
```

This will make dashboards much richer.

---

# Future-Proof User Identity

Today:

```text
user_id
```

Tomorrow:

```text
email
auth provider
OIDC
Google login
GitHub login
```

I would define now:

```sql
sync_users

id
display_name
email
created_at
```

even if authentication is not implemented.

---

# One Thing Missing

## Timezone Normalization

This will matter when you move countries.

You are actively exploring:

* NZ
* Singapore
* Dubai

Eventually:

```text
Office Laptop (India)
Home Laptop (NZ)
```

may coexist.

Store:

```sql
utc_start
utc_end
timezone
```

for every synced session.

Never aggregate in local time.

Aggregate in UTC.

Convert only in UI.

---

# Mixed Mode

You listed:

> Whether to support mixed mode (local + sync)

My recommendation:

**No.**

At least not initially.

Support:

```text
source=local
```

or

```text
source=sync
```

Never:

```text
source=mixed
```

because you'll eventually get:

```text
same session
local db

same session
sync db
```

and debugging duplicates becomes miserable.

---

# One Additional Endpoint

I would add:

```http
/api/sync/users
/api/sync/devices
```

Returns:

```json
{
  "users": [...]
}
```

and

```json
{
  "devices": [...]
}
```

This makes future UI work trivial.

---

# Long-Term Architecture

I think WorkGraph is heading toward:

```text
Collectors
--------------------------------
Windows
Linux
Android
MacOS

        ↓

Local SQLite

        ↓

Sync API

        ↓

Raspberry Pi
(PostgreSQL)

        ↓

Analytics Layer
--------------------------------
Dashboard
Reports
Goal Drift
Burnout Detection
AI Coaching

        ↓

LLM Insights
```

And because you're already collecting:

* sessions
* git metadata
* journals
* reflections
* tags
* device identity

you've laid most of the groundwork for the future AI layer.

My only strong recommendation is:

**Don't let `payload_json` become your analytics database.**

Keep it as the authoritative event record, but start exposing important fields as indexed columns now. That's the one decision that will save you the most pain when WorkGraph grows beyond a few thousand sessions.

---

# Planned Approach (for Review)

This section turns the feedback above into an implementation plan for WorkGraph sync.

## Goals

1. Keep `sync_sessions` as canonical synced event storage.
2. Make analytics queryable without repeated `payload_json` parsing.
3. Enforce user and device isolation by design.
4. Keep time handling correct across multiple timezones.
5. Keep rollout low-risk with backward compatibility.

## Canonical Data Model

### 1) `sync_sessions` (canonical event table)

Keep `payload_json` as full audit record, but add extracted analytics fields:

```sql
-- Existing core identifiers (conceptual)
uuid
user_id
device_id

-- Time (UTC first)
utc_start
utc_end
timezone_name

-- Session measures
active_seconds

-- Extracted analytics dimensions
application_name
tag
repo_name

-- Original payload
payload_json
```

### 2) `sync_devices` (device dimension)

```sql
device_id        -- PK scoped by user_id or globally unique with unique(user_id, device_id)
user_id
device_name
device_type      -- laptop/desktop/mobile/tablet
platform         -- windows/linux/macos/android/ios
hostname
last_seen_utc
created_at_utc
updated_at_utc
```

### 3) `sync_users` (user dimension)

```sql
id
display_name
email
created_at_utc
updated_at_utc
```

### 4) `sync_metrics_daily` (rollups)

```sql
user_id
device_id
day_utc

active_seconds
focus_seconds
meeting_seconds
context_switches

updated_at_utc
```

Recommended uniqueness:

```sql
UNIQUE (user_id, device_id, day_utc)
```

## Query and Isolation Rules

1. Every sync analytics query must include `WHERE user_id = ?`.
2. Device-scoped dashboard views must include optional `device_id` filters.
3. Default dashboard scope should be explicit (`source=local` or `source=sync`), not mixed.
4. Aggregations should use UTC storage fields and convert to local time only in presentation.

## API Additions

### New read endpoints

```http
GET /api/sync/users
GET /api/sync/devices
```

Response shape examples:

```json
{
        "users": [
                {
                        "id": "u_123",
                        "display_name": "Pratik",
                        "email": "..."
                }
        ]
}
```

```json
{
        "devices": [
                {
                        "device_id": "dev_1",
                        "device_name": "Office Laptop",
                        "platform": "windows",
                        "last_seen_utc": "2026-07-12T10:22:00Z"
                }
        ]
}
```

## Ingestion Strategy

On sync ingest:

1. Validate required identifiers: `uuid`, `user_id`, `device_id`.
2. Normalize times to UTC (`utc_start`, `utc_end`) and store original timezone name.
3. Extract analytics fields from payload (`application_name`, `tag`, `repo_name`, `active_seconds`).
4. Upsert into `sync_sessions` by `(user_id, uuid)`.
5. Upsert/update `sync_devices.last_seen_utc`.
6. Update affected `sync_metrics_daily` rows for impacted UTC days.

## Rollout Plan

### Phase 1: Schema Prep

1. Add extracted analytics columns to `sync_sessions`.
2. Create `sync_users`, `sync_devices`, `sync_metrics_daily`.
3. Add indexes:
         - `(user_id, utc_start)`
         - `(user_id, device_id, utc_start)`
         - `(user_id, tag, utc_start)`
         - `(user_id, application_name, utc_start)`

### Phase 2: Backfill

1. Backfill new columns from `payload_json` for existing rows.
2. Populate `sync_devices` from distinct `(user_id, device_id)` in `sync_sessions`.
3. Build initial `sync_metrics_daily` for historical range.

### Phase 3: Switch Reads

1. Move dashboard analytics reads to extracted columns and rollups.
2. Keep `payload_json` reads only for drill-down/debug/audit views.

### Phase 4: Guardrails

1. Add test coverage for multi-user query isolation.
2. Add test coverage for multi-device filtering.
3. Add timezone correctness tests for cross-timezone sessions.
4. Enforce non-mixed source mode in UI and API parameters.

## Acceptance Criteria

1. No dashboard metric requires parsing `payload_json` in request path.
2. All sync analytics endpoints require user scope.
3. Device filter works for all major charts and summaries.
4. A 365-day sync report reads from daily rollups, not raw sessions.
5. UTC aggregation remains stable for users with multiple active timezones.

## Risks and Mitigations

1. Risk: Backfill drift between extracted columns and payload.
         Mitigation: Add one-time consistency validator that samples rows and compares extracted values.
2. Risk: Rollup staleness after late-arriving sessions.
         Mitigation: Recompute rollups for affected days on upsert.
3. Risk: Query regressions during transition.
         Mitigation: Dual-run old/new aggregation for a limited period and compare outputs.

## Recommendation

Proceed with this plan in order: schema prep -> backfill -> read switch -> guardrails.

This keeps your canonical sync architecture intact while making analytics fast, scalable, and ready for multi-user/multi-device growth.
