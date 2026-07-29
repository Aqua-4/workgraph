# WorkGraph UI Notes

These notes capture the visible state of the main dashboard modes and timeline views after the stabilization work.

## Standalone Mode

- Header shows mode, source, user, device, and time range.
- Dashboard focuses on local collection, tagging, application mix, repository mix, and goal drift.
- Timeline shows start/end, user, device, app, duration, tag, window/domain, repo, and idle state.

## Sync-Client Mode

- Dashboard keeps the local-first layout but adds registered-device status and pending sync visibility.
- Sync client status shows last synced timestamp when the device is registered.
- Local dashboard sections stay visible so the user can work while sync is unavailable.

## Sync-Server Mode

- Dashboard shows sync health, device health, queue state, and recent sync errors.
- Registered devices and active tokens are visible without opening raw logs.
- Timeline stays available with server-side user/device filters and device-aware columns.

## Visual Conventions

- Metric cards use a shared label/value/subtext pattern.
- Headers keep the current mode and scope visible at the top of the page.
- Timeline tables use explicit columns for start/end, user, device, and source context.

## Bootstrap Responsive Enhancement Plan

This section defines where Bootstrap grid and responsive utilities are applied to improve layout consistency and mobile behavior.

### 1) Shared Layout Foundation

- File: `api/templates/base.html`
- Use Bootstrap containers and responsive spacing tokens as the default shell for every page.
- Navigation:
  - Keep nav links in a wrapping flex row using Bootstrap utility classes.
  - Preserve clear active state while allowing links to stack on narrow screens.
- Header context block:
  - Render mode/source/user/device/time fields as a responsive grid using `row` + `col-*` classes.
  - Collapse from multi-column desktop to single-column mobile cleanly.

### 2) Dashboard Surfaces

- Files: `api/templates/dashboard.html`, `api/templates/sync_server_dashboard.html`
- Convert metric clusters to Bootstrap row/column cards:
  - `row g-3` for spacing.
  - `col-12 col-sm-6 col-xl-4` or `col-12 col-md-6 col-xl-3` depending on card density.
- Forms and filters:
  - Replace custom inline form widths with responsive `col-*` field widths.
  - Keep primary action buttons full-width on mobile and inline on desktop.
- Secondary detail blocks (device status, sync health, errors):
  - Use table wrappers with `table-responsive` and stacked card sections for smaller widths.

### 3) Timeline Views

- Files: `api/templates/timeline.html`, `api/templates/sync_server_timeline.html`
- Filter controls already use Bootstrap form grid; align all remaining controls to the same pattern.
- Keep chart sections inside responsive cards with explicit `overflow-x` only where needed.
- Keep timeline/session tables in `table-responsive` wrappers to avoid clipping on smaller devices.

### 4) Tag Review

- File: `api/templates/tag_review.html`
- Main 2-column layout becomes Bootstrap grid driven:
  - `row g-3` with `col-12 col-xl-8` (review queue) + `col-12 col-xl-4` (rule preview/actions).
  - Maintain current visual accents (repo/domain/app grouping) while improving breakpoint behavior.
- Filter panel and queue actions:
  - Standardize to Bootstrap form rows and button groups for mobile stacking.

### 4b) Browser Tag Review

- File: `api/templates/browser_tag_review.html`
- Keep the same overall preview/actions layout as Tag Review, but limit the queue to browser-only buckets.
- Include the YAML preview, copy, refresh, and download actions in the right-hand panel so browser cleanup can stay local to the browser workflow.

### 5) Journal

- File: `api/templates/journal.html`
- Keep content as stacked responsive cards but move form internals to Bootstrap grid columns where practical.
- Ensure action rows (save/cancel) wrap gracefully with `d-flex flex-wrap gap-*`.
- Keep tag picker usable on mobile by capping dropdown width and preserving touch-friendly spacing.

### 6) Quality and Validation Pass

- Verify these responsive breakpoints: `<576px`, `>=576px`, `>=768px`, `>=1200px`.
- Confirm no horizontal overflow in primary views except intentional chart/table scroll regions.
- Validate keyboard focus order and button/input accessibility after layout updates.
