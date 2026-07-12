# WorkGraph v1.x Stabilization Implementation Roadmap

## Purpose

This document converts the stabilization plan into a practical implementation roadmap that can be reviewed, verified, and executed incrementally.

The focus is on reliability, trustworthiness, observability, and recovery so that future analytics and AI features build on a solid foundation.

---

## Guiding Principle

The primary risk is not missing features.
The primary risk is losing user trust in the data.

If sync data is inconsistent, delayed, or invisible, later insights will be unreliable.

---

## Release Goal

Before starting v2 work, WorkGraph should be able to demonstrate:

- data survives outages
- sync recovers automatically
- rollups remain accurate
- dashboard state is understandable
- device health is observable
- backup and restore are tested
- documentation matches implementation

---

## Architecture Direction: Separate Client, Server, and Shared Concerns

### Recommendation

Yes — the implementation should treat the three operating modes as distinct product surfaces, even if they share a common underlying data model.

### Why this is worth doing

Separating concerns by mode makes the system easier to reason about, test, and evolve:

- the standalone/local mode should focus on local collection, local storage, and local reporting
- the sync-client mode should focus on local capture plus push/pull coordination and pending-state visibility
- the sync-server mode should focus on aggregation, device health, multi-device visibility, and operational monitoring

This reduces the risk of accidentally coupling server-only behavior into the client experience or vice versa.

### Recommended approach

Use a hybrid model:

- keep a shared core schema for the fundamental activity data
- add mode-specific schema extensions for client-only or server-only concerns
- keep the dashboards separate by mode, but share reusable UI components where possible

### Database guidance

Do not treat the client and server as fully independent products with completely separate databases in the first version. A simpler and more practical approach is:

- one shared core schema for sessions, journal entries, reflections, and activity data
- client-specific tables or logic for local sync state, pending queues, and local-only metadata
- server-specific tables or logic for sync users, sync devices, sync tokens, sync error logs, and aggregated operational state

This keeps the architecture understandable while still making the responsibilities explicit.

### Dashboard guidance

Yes — the dashboards should be separated by mode, but they should also share a common design language.

Recommended split:

- standalone dashboard: local collection, local focus metrics, and local journaling
- sync-client dashboard: local status, pending sync state, last sync, and basic health indicators
- sync-server dashboard: multi-device overview, device health, sync failures, and operational summaries

### Implementation note

The first version should avoid over-engineering the split. A good target is:

- one shared data access layer for common analytics
- one mode-specific view layer per dashboard
- one mode-specific sync/operations layer per deployment type

### Acceptance criteria

- client and server responsibilities are clearly separated in code and data access
- each dashboard mode exposes only the state relevant to that mode
- shared analytics logic can still be reused across modes

---

## Phase 0 — Scope Freeze and Definition

### Objective

Lock the stabilization scope and prevent feature creep.

### Deliverables

- confirm the stabilization scope is limited to reliability, sync correctness, dashboard clarity, and operations
- explicitly defer analytics intelligence features to later releases

### Exit Criteria

- roadmap is agreed
- v2/v3 features are clearly out of scope for this phase

---

## Phase 1 — Sync Health and Visibility

### Objective

Make sync state visible and understandable to users.

### Scope

- implement server-mode visibility first
- add client-mode and standalone-mode status surfaces as appropriate

- add a sync health view for the server dashboard
- show registered users and devices
- show active tokens
- show last sync time
- show pending uploads and pulls
- show sync failures

### Implementation Notes

- add a dedicated server-side sync status page or section
- surface per-device health status
- include basic summary cards and a device table

### Acceptance Criteria

- a user can see whether sync is healthy or degraded
- a user can see which devices are active and when they last synced
- a user can identify obvious sync issues without inspecting logs

### Verification Checklist

- [x] sync summary visible on server dashboard
- [x] client dashboard exposes local sync status when applicable
- [x] standalone dashboard remains focused on local collection state
- [x] device table rendered with last seen and last sync data
- [x] status states are understandable

---

## Phase 2 — Sync Queue and Pending State Visibility

### Objective

Make it obvious when data is pending synchronization.

### Scope

- show pending sessions
- show pending journals
- show pending reflections
- show per-device pending counts

### Implementation Notes

- expose pending counts from the sync system or sync metadata tables
- include a simple per-device breakdown

### Acceptance Criteria

- users can tell whether a device is caught up or waiting to sync
- pending counts are accurate and update after sync cycles

### Verification Checklist

- [x] pending session count shown
- [x] pending journal count shown
- [x] pending reflection count shown
- [x] counts update after sync activity

---

## Phase 3 — Sync Failure Observability

### Objective

Make sync failures actionable and visible.

### Scope

- add a sync error log table
- record endpoint, error type, message, retry count, timestamp, and status
- expose the last 20 sync errors in the dashboard or API

### Implementation Notes

- create a dedicated error log storage model
- ensure failed sync attempts are logged consistently
- preserve enough context to debug root causes

### Acceptance Criteria

- sync failures are logged automatically
- errors can be reviewed from an operational view
- recent failures are easy to inspect

### Verification Checklist

- [x] error log table exists
- [x] failed requests are written to the log
- [x] dashboard/API shows recent errors

---

## Phase 4 — Sync Validation Commands

### Objective

Provide tools to validate the integrity of sync data.

### Scope

Implement:

- `workgraph sync validate`
- `workgraph sync verify`

### `workgraph sync validate`

Checks for:

- missing sessions
- corrupt payloads
- duplicate UUIDs
- broken references

### `workgraph sync verify`

Compares:

- client totals vs server totals

### Acceptance Criteria

- commands run successfully against local and sync-backed data
- results clearly indicate pass/fail
- validation output can be used for troubleshooting

### Verification Checklist

- [x] validate command reports missing data issues
- [x] validate command detects duplicate UUIDs
- [x] verify command compares counts across client/server

---

## Phase 5 — Database and Data Integrity Checks

### Objective

Increase confidence that stored activity data is trustworthy.

### Scope

Implement:

- `workgraph doctor`
- UUID validation
- rollup validation
- timezone validation

### `workgraph doctor`

Checks for:

- SQLite integrity issues
- missing indexes
- corrupt records
- invalid timestamps

### UUID Validation

Ensures:

- no duplicate UUIDs
- no empty UUIDs

Across relevant record types such as sessions, journals, reflections, and events.

### Rollup Validation

Compares raw sessions with daily rollups and ensures they match.

### Timezone Validation

Ensures synced records contain valid timezone metadata and timestamps.

### Acceptance Criteria

- integrity issues are detected and reported
- invalid records can be identified quickly
- rollups and raw data remain consistent

### Verification Checklist

- [ ] doctor command runs successfully
- [ ] invalid timestamps are flagged
- [ ] UUID issues are surfaced
- [ ] rollup mismatches are detected

---

## Phase 6 — Recovery and Backup Workflows

### Objective

Make data recovery a standard workflow, not a manual operation.

### Scope

Implement:

- `workgraph backup create`
- `workgraph backup restore`
- `workgraph sync snapshot`

### Acceptance Criteria

- database backups can be created from the CLI
- backups include SQLite data and relevant configuration artifacts
- restoration is documented and tested
- server snapshot flow captures core state needed for recovery

### Verification Checklist

- [ ] backup command creates an archive or backup artifact
- [ ] restore command can recover from backup
- [ ] server snapshot workflow creates a recoverable backup set

---

## Phase 7 — Dashboard Polish and Trust UX

### Objective

Make the dashboard explain system state clearly.

### Scope

- separate dashboard behavior by mode: standalone, sync-client, and sync-server
- keep shared layout patterns while allowing mode-specific panels and actions

- add a richer dashboard header with mode, user, device, and time range
- add device health widget
- surface goal drift visually
- add a sync status widget for client dashboards
- improve timeline columns to show relevant context
- standardize card presentation for time, trend, and subtext

### Implementation Notes

- prioritize clarity over novelty
- make status obvious even for non-technical users

### Acceptance Criteria

- dashboard state is easy to understand at a glance
- sync status and device health are visible without deep navigation
- important metrics follow a consistent layout

### Verification Checklist

- [ ] header shows mode/user/device/time range
- [ ] standalone dashboard remains focused on local collection and journaling
- [ ] sync-client dashboard shows local sync health and pending state
- [ ] sync-server dashboard shows device health, queue state, and failures
- [ ] goal drift appears visually

---

## Phase 8 — Reporting Enhancements

### Objective

Extend reporting so operational and planning views are available.

### Scope

Implement:

- `workgraph report monthly`
- `workgraph report sync`
- `workgraph report goals`

### Acceptance Criteria

- monthly reporting works for a broader time window
- sync reporting summarizes device health and pending activity
- goal reporting summarizes drift against planned allocations

### Verification Checklist

- [ ] monthly report generates successfully
- [ ] sync report includes devices, last sync, pending state, and failures
- [ ] goals report surfaces drift summary clearly

---

## Phase 9 — Documentation and Operations

### Objective

Ensure implementation is understandable and supportable.

### Scope

- architecture diagrams for standalone, sync client, and sync server
- sync lifecycle diagram
- troubleshooting guide for common failures
- deployment and upgrade guidance

### Acceptance Criteria

- the documentation reflects live functionality
- common operational issues are documented with expected recovery steps
- new contributors can follow the architecture without guesswork

### Verification Checklist

- [ ] architecture diagrams exist
- [ ] sync lifecycle diagram exists
- [ ] troubleshooting guide covers key issues
- [ ] deployment/upgrade docs are present

---

## Phase 10 — Release Readiness and Exit Criteria

### Objective

Determine whether the stabilization work is complete enough to begin v2.

### Release Checklist

#### Sync

- [ ] multi-device sync validated
- [ ] replay validation implemented
- [ ] sync verification implemented
- [ ] failure logs implemented

#### Database

- [ ] integrity checks pass
- [ ] rollup validation passes
- [ ] UUID validation passes

#### Dashboard

- [ ] device health page or view exists
- [ ] goal drift widget is visible
- [ ] sync status widget exists
- [ ] timeline polish is complete

#### Operations

- [ ] backup implemented
- [ ] restore implemented
- [ ] troubleshooting guide written

#### Documentation

- [ ] architecture diagrams present
- [ ] deployment guide present
- [ ] upgrade guide present
- [ ] screenshots or UI notes available for major modes

### Exit Criteria

WorkGraph is ready to move into v2 when:

- data survives outages
- sync recovers automatically
- rollups remain accurate
- dashboards clearly explain system state
- device health is observable
- backup/restore is tested
- documentation matches implementation

---

## Suggested Delivery Order

For practical execution, this roadmap should be delivered in the following order:

1. sync visibility
2. sync failure logging
3. validation commands
4. integrity checks
5. backup and restore
6. dashboard polish
7. reporting enhancements
8. documentation and operations

This order prioritizes trust and operability before polish.

---

## Suggested Verification Method

Each phase should be considered complete only when:

- implementation exists in code
- tests or verification commands pass
- the user-facing behavior is observable
- the documentation reflects the current state

A good rule is: if the feature cannot be demonstrated or verified, it is not complete.

---

## Concrete Implementation Checklist

Use this checklist as the execution backlog for the roadmap.

### A. Architecture and Separation

- [ ] define the shared core schema for sessions, journals, reflections, and activity events
- [ ] define client-only sync state tables or metadata for pending queue tracking
- [ ] define server-only operational tables for users, devices, tokens, errors, and summaries
- [ ] document which components are shared vs mode-specific

### B. Sync Visibility and Health

- [x] add server dashboard summary cards for users, devices, tokens, last sync, pending uploads, pending pulls, and failures
- [x] add a device table with status, last seen, and last sync information
- [x] add client dashboard sync status card with last sync and pending counts
- [x] add standalone dashboard local-state summary without server-only noise

### C. Sync Queue and Pending State

- [x] expose pending session, journal, and reflection counts per device
- [x] add an API response or dashboard panel for current pending state
- [x] ensure pending counts reflect the latest successful sync cycle

### D. Sync Failure Logging and Observability

- [x] create a sync error log table
- [x] record endpoint, error type, message, retry count, timestamp, and status
- [x] expose recent errors in an API endpoint or dashboard panel
- [x] ensure failures are visible without needing direct database inspection

### E. Validation and Integrity Commands

- [x] implement `workgraph sync validate`
- [x] implement `workgraph sync verify`
- [ ] implement `workgraph doctor`
- [ ] implement UUID validation checks
- [ ] implement rollup validation checks
- [ ] implement timezone validation checks
- [ ] ensure each command returns clear pass/fail output

### F. Backup and Recovery

- [ ] implement `workgraph backup create`
- [ ] implement `workgraph backup restore`
- [ ] implement `workgraph sync snapshot`
- [ ] verify backup artifacts include database and relevant config files
- [ ] document recovery steps for each workflow

### G. Dashboard UX and Presentation

- [ ] add a consistent header with mode, user, device, and time range
- [ ] add a device health widget to the server dashboard
- [ ] add a goal drift widget to the dashboard
- [ ] add a sync status widget to the client dashboard
- [ ] improve timeline columns for local and server views
- [ ] standardize dashboard card layout for value, subtext, and trend

### H. Reporting

- [ ] implement `workgraph report monthly`
- [ ] implement `workgraph report sync`
- [ ] implement `workgraph report goals`
- [ ] ensure reports are deterministic and easy to verify

### I. Documentation and Operations

- [ ] create standalone architecture diagram
- [ ] create sync-client architecture diagram
- [ ] create sync-server architecture diagram
- [ ] create sync lifecycle diagram
- [ ] create troubleshooting guide for sync and data issues
- [ ] create deployment and upgrade documentation

### J. Testing and Verification

- [ ] add unit tests for new validation logic
- [ ] add API tests for new sync health and error endpoints
- [ ] add CLI tests for backup, restore, and validation commands
- [ ] verify behavior for standalone, sync-client, and sync-server modes
- [ ] run regression tests after each major milestone
