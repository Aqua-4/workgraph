# WorkGraph API Reference

This document describes the FastAPI service in WorkGraph, including runtime behavior, dashboard modes, and the full API endpoint catalog.

## Service Overview

- Framework: FastAPI
- App entrypoint: api/app.py
- App title: WorkGraph Dashboard
- Templating: Jinja2 (server-rendered HTML pages)
- Data store: SQLite (activity.db by default)

The API serves two broad concerns:

1. Product data APIs for sessions, stats, journal, reflections, work events, and tag review.
2. Sync APIs for multi-device registration, push/pull replication, health, rollups, and diagnostics.

## Dashboard Modes and Data Source Behavior

Configured mode controls what data is shown and which features are enabled:

- standalone: Local-only dashboard and APIs against local activity tables.
- sync-client: Local UX with sync registration/status and sync-capable data flows.
- sync-server: Aggregated server-side analytics over sync tables.

Source selection for summary stats:

- GET /api/stats defaults to mode-derived source.
- You can override with source=local or source=sync where supported.

Important limitation:

- In sync-server mode, journal and related APIs are disabled (404 behavior for journal/reflection/work-event API surfaces).

## Authentication and Security Notes

- Sync transport endpoints use bearer token validation:
  - POST /api/sync/v1/push
  - POST /api/sync/v1/pull
- Device registration issues tokens for sync-server registration flows.
- Analytics reads in sync mode are user-scoped and support optional device filtering.

## API Conventions

- All responses are JSON unless noted otherwise.
- Query parameter constraints are enforced by FastAPI validation.
- Time windows are generally driven by days or ISO timestamps/date strings depending on endpoint.
- Count fields are returned on list-style endpoints where useful.

## Endpoint Catalog

## 1) Session and Summary Analytics

### GET /api/sessions
Returns recent activity sessions.

Query params:
- days (default 7, min 1, max 30)
- limit (default 100, min 1, max 1000)

Response shape:
- sessions: list
- count: integer

### GET /api/stats
Returns summary analytics including totals and distributions.

Query params:
- days (default 7, min 1, max 30)
- source (optional, local or sync)
- user_id (optional, used for sync source)
- device_id (optional, sync per-device drilldown)

Notes:
- local source path uses local activity sessions.
- sync source path uses synced multi-device session payloads.

## 2) Device Registration and Sync Analytics Metadata

### POST /api/device/register
Mode-aware registration endpoint for standalone, sync-client, or sync-server registration behavior.

### GET /api/sync/users
Returns registered sync users (optionally filtered by user_id).

Query params:
- user_id (optional)

### GET /api/sync/devices
Returns registered devices for a user.

Query params:
- user_id (required)

## 3) Sync Health, Diagnostics, and Rollups

### GET /api/sync/health
Returns server sync health and operational counters, including device/token/checkpoint status and recent error metadata.

### GET /api/sync/errors
Returns recent sync errors.

Query params:
- limit (default 20, min 1, max 200)

### GET /api/sync/stats
Returns sync-backed summary analytics for a user/device scope.

Query params:
- user_id (required)
- days (default 7, min 1, max 3650)
- device_id (optional)

### POST /api/sync/rollups/rebuild
Rebuilds sync rollups for analytics.

Query params:
- user_id (required)
- device_id (optional)
- start_day (optional, YYYY-MM-DD)

Response includes:
- rebuilt_rows

## 4) Journal APIs

### POST /api/journal
Creates a journal entry.

### GET /api/journal
Lists journal entries.

Query params:
- from (optional)
- to (optional)
- limit (default 100, min 1, max 500)

### GET /api/journal/{journal_id}
Returns one journal entry.

### PUT /api/journal/{journal_id}
Updates one journal entry.

### GET /api/journal/tags
Returns configured tags available for journal tagging.

### GET /api/journal/{journal_id}/correlated-sessions
Returns overlapping activity sessions and a summary for the entry time window.

Query params:
- limit (default 500, min 1, max 1000)

## 5) Work Event APIs

### GET /api/work-events/types
Returns available work event types and impact levels.

### POST /api/work-events
Creates a structured work event.

### GET /api/work-events
Lists work events.

Query params:
- from (optional)
- to (optional)
- event_type (optional)
- impact (optional)
- project (optional)
- limit (default 100, min 1, max 500)

### GET /api/work-events/{event_id}
Returns one work event.

### PUT /api/work-events/{event_id}
Updates one work event.

### GET /api/work-events/{event_id}/correlated-sessions
Returns overlapping activity sessions and summary around the work event timestamp.

Query params:
- limit (default 500, min 1, max 1000)

## 6) Daily Reflection APIs

### PUT /api/reflections/{date}
Creates or updates reflection for date (YYYY-MM-DD).

### GET /api/reflections/{date}
Returns reflection for date.

### GET /api/reflections
Lists reflections in date range.

Query params:
- from (optional date)
- to (optional date)
- limit (default 100, min 1, max 500)

### GET /api/reflections/{date}/correlated-sessions
Returns overlapping activity sessions and summary for the reflection date window.

Query params:
- limit (default 500, min 1, max 1000)

## 7) Tag Review APIs

Grouped workflow endpoints:

### GET /api/tag-review/groups
Returns grouped tag review candidates.

### GET /api/tag-review/groups/{group_type}/{group_value}
Returns sessions in a specific review group.

### POST /api/tag-review/assign-group
Assigns a tag to an entire group.

Suggestion/export helpers:

### POST /api/tag-review/suggestions
Builds tag rule suggestions from review outcomes.

### GET /api/tag-review/yaml-preview
Returns generated YAML preview.

### POST /api/tag-review/yaml-download
Returns YAML payload as downloadable file content.

Note:
- Legacy per-session tag-review endpoints were removed in favor of grouped-only workflows.

## 8) Sync Transport APIs (Replication)

### POST /api/sync/v1/devices/register
Registers a device and returns a device token.

### POST /api/sync/v1/push
Pushes changed entities from client to server.

Expected entities in payload changes:
- sessions
- journal_entries
- daily_reflections

Behavior:
- Idempotent batch handling using batch_id and payload hash.
- Returns accepted counts, conflicts, next_push_cursor, and server_time.

### POST /api/sync/v1/pull
Pulls changed entities from server using cursor pagination.

Response includes active entities plus tombstones and next cursor state.

## Related Documentation

- Root product and feature overview: ../README.md
- Dashboard usage and endpoint examples: ../DASHBOARD.md
- Feature implementation status: ../FEATURES_STATUS.md
- Dashboard rendering and mode logic: ../docs/DASHBOARD_FEATURES_AND_LOGIC.md
- Sync launch hardening checklist: ../docs/sync/SYNC_HARDENING_LAUNCH.md
- Sync aggregate analytics approach: ../docs/sync/SERVER_AGGREGATE_METRICS_APPROACH.md
