# Tracking Compatibility and Feature Map

This document tracks WorkGraph's desktop collection capabilities and the work
needed to extend them across operating systems and desktop environments. It is
a source-code-based map, not a claim that every environment has been tested on
real hardware. Update it when a provider, its dependencies, or its verified
platform coverage changes.

For overall feature status, see [FEATURES_STATUS.md](../FEATURES_STATUS.md).

## Identify the Environment

Keep these dimensions separate when identifying a device:

- **OS and version:** Windows, Linux distribution, macOS, or another OS.
- **Session type:** X11, Wayland, a native Windows or macOS desktop, or headless.
- **Desktop and compositor:** for example, GNOME Shell, KDE Plasma/KWin, or a
  wlroots-based compositor. Wayland capabilities vary by compositor and version.
- **Session context:** local or remote desktop, VM, container, or WSL; the
  collector must have access to the desktop session it is meant to observe.
- **Dependencies and permissions:** available executables, desktop integration,
  privacy grants, and access to browser profiles or process information.

On Linux, `XDG_SESSION_TYPE`, `XDG_CURRENT_DESKTOP`, `WAYLAND_DISPLAY`, and
`DISPLAY` can help identify the session, but environment variables alone do not
prove a capability is available. In particular, `DISPLAY` can point to XWayland
within a Wayland session. Probe the actual backend and report failures.

## Current Environment Map

**Implemented** means there is a backend in the current code, not that every
version or configuration is verified. **Partial** means results depend on the
desktop, external tools, or accessible data. **Not implemented** means there is
no native tracking backend for that environment.

| Environment | Foreground app, title and PID | Idle | Current scope and extension |
| --- | --- | --- | --- |
| Windows 10/11 desktop | Implemented | Implemented | Uses Win32 foreground-window and last-input APIs. Validate each supported release, including locked and remote sessions. |
| Windows 8/8.1 | Windows backend exists; unverified | Windows backend exists; unverified | Establish a tested runtime and dependency combination before claiming support. |
| Windows XP/7 | Not supported | Not supported | The project requires Python 3.11+, which does not support these versions. Supporting them would require a separate legacy runtime/collector and a security policy. |
| Linux X11, including GNOME, KDE, XFCE and Cinnamon X11 sessions | Partial | Partial | Uses `xprop` with `xdotool` fallback for windows and `xprintidle` for idle. Availability and window properties depend on the installed tools and desktop. |
| Linux GNOME Wayland | No native backend; XWayland results may be incomplete | No native backend | Investigate a GNOME Shell integration and supported idle mechanism; validate per desktop version. |
| Linux KDE Plasma Wayland | No native backend; XWayland results may be incomplete | No native backend | Investigate KWin integration and supported idle mechanism; validate per Plasma version. |
| Other Wayland compositors, including wlroots-based desktops | No native backend; XWayland results may be incomplete | No native backend | Investigate compositor-supported protocols or integrations separately; title, PID and idle availability can differ. |
| macOS | Not implemented | Not implemented | Add a native provider, assess OS privacy permissions and add browser profile paths. Verify supported macOS versions. |
| BSD and other desktop OSs | Not implemented | Not implemented | Assess runtime, desktop protocol and native APIs before planning support. |
| Headless, SSH, containers and WSL | No dependable desktop tracking by default | No dependable desktop tracking by default | Identify the actual graphical session and run a suitable collector with access to it; otherwise expose capabilities as unavailable. |

The provider factory currently selects Windows or Linux by OS name and falls
back to an unsupported provider for other systems. It does not choose a Linux
backend based on session type. Linux's X11 tools must not be presented as native
Wayland support. See [platforms.py](../collector/platforms.py).

## Feature Dependencies

| Feature | Current behavior | Compatibility considerations |
| --- | --- | --- |
| Active app and process | Windows foreground PID or Linux X11 window PID resolved with `psutil` | Missing window properties, inaccessible processes, desktop isolation or a missing provider can yield `Unknown`. |
| Window title | Win32 window text or X11 properties/`xdotool` | Titles may be unavailable, restricted or empty; access and permissions vary by platform. |
| Idle | Win32 last-input time or Linux `xprintidle` | May be unavailable. Some potential Wayland interfaces provide threshold notifications instead of exact idle seconds, requiring an interface change. |
| Browser domain | Extracts a domain from the title, then tries browser history for recognized browsers | History paths are currently configured for selected browsers on Windows and Linux, not macOS. Results are best-effort, not a reliable active-tab API; private browsing, inaccessible profiles and missing foreground data can limit them. |
| Git context | Resolves a working directory from process/editor information or falls back to the collector's working directory, then invokes Git | Requires Git and accessible repository metadata. The fallback can describe the collector's repository rather than the foreground application. |
| Sessions and attribution | Aggregates and tags collected samples | Quality depends on the underlying app, title, browser, Git and idle signals. Missing data can affect context switches, categorization and reports. |

When idle detection is unavailable, wellbeing receives an explicit unavailable
state, but the normal activity sample currently defaults to `is_idle = false`
and `idle_seconds = 0`, unless a system-idle rule applies. This does **not**
establish that the user is active; downstream activity and reports can be
misleading. Preserving unknown idle state through collection and reporting is a
separate improvement to consider.

See [browser_tracker.py](../collector/browser_tracker.py),
[git_tracker.py](../collector/git_tracker.py), and
[collector_service.py](../services/collector_service.py).

## Extension Priorities

1. Establish real-desktop baselines for Windows 10/11 and Linux X11, including
   installed dependencies, idle, lock/unlock, sleep, and session changes.
2. Add explicit session detection and per-feature capability reporting, with
   unavailable reasons and no assumption that XWayland covers all windows.
3. Investigate and implement KDE Plasma Wayland and GNOME Wayland providers;
   validate each supported desktop version and its permissions separately.
4. Add a macOS provider and browser profile support after validating native API
   access and required privacy permissions.
5. Evaluate additional compositors and OSs based on actual demand. Treat legacy
   Windows as a separate, explicitly scoped project rather than a backend toggle.

For each supported combination, record OS and desktop versions, session type,
backend, required dependencies and permissions, and the test date. Verify app,
title, PID, idle, browser and Git independently, including failure states and
the effects of unavailable data. Automated provider tests should complement,
not replace, these real-desktop checks.