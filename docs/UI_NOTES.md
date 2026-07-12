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
