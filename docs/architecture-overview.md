# WorkGraph Architecture Overview

This note summarizes which parts of WorkGraph are shared across modes and which parts are mode-specific.

## Shared Core

These components are shared across standalone, sync-client, and sync-server modes:

- `db/schema.sql` for the local activity schema
- `db/repository.py` for data access and migration helpers
- `services/activity_tagger.py` for tag attribution
- `services/reporting.py` for deterministic reports and goal drift analysis
- `main.py` for CLI entry points and backup/restore workflows

## Standalone Mode

Standalone mode is the local-first experience.

- Collector writes activity data into the local SQLite database.
- Dashboard focuses on local sessions, goals, application mix, repository mix, and journal workflows.
- Timeline shows the local activity history with explicit user/device context.

## Sync-Client Mode

Sync-client mode adds remote synchronization to the local-first experience.

- Local collector remains primary.
- Sync daemon pushes and pulls through the sync API.
- Dashboard shows local sync status, pending counts, and last sync time.

## Sync-Server Mode

Sync-server mode is the multi-device aggregation surface.

- API hosts sync registration, push/pull, and health endpoints.
- Dashboard shows device health, queue state, sync failures, and aggregate metrics.
- Reports summarize monthly activity, sync health, and goal drift from server-side data.

## Practical Rule

When adding a new feature, decide first whether it belongs to:

- the shared core
- the local standalone surface
- the sync client surface
- the sync server surface

That keeps the product readable and prevents server-only behavior from leaking into local workflows.
