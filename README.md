# WorkGraph

> Privacy-first work telemetry for engineers.

WorkGraph is a local-first activity intelligence platform that helps engineers understand how their workday is spent across coding, meetings, research, documentation, and administrative tasks.

Unlike traditional productivity trackers, WorkGraph focuses on collecting structured activity metadata that can later be analyzed to generate actionable insights about focus, context switching, workload distribution, and work habits.

All data remains under your control.

No screenshots. No keystrokes. No cloud dependency.

---

## Why WorkGraph?

Most time-tracking tools answer:

* How much time was spent in VS Code?
* How much time was spent in Chrome?
* How much time was spent in meetings?

WorkGraph aims to answer deeper questions:

* Where does my time actually go?
* How often am I context-switching?
* How much uninterrupted focus time do I get?
* Am I spending enough time on high-value work?
* What patterns are reducing my productivity?
* What habits should I improve?

Version 1 focuses exclusively on collecting high-quality activity data.

Future versions will introduce analytics, dashboards, and AI-powered coaching.

---

## Features

### Version 1

* Active application tracking
* Window title tracking
* Browser domain tracking
* Idle time detection
* Session aggregation
* Local SQLite storage
* Lightweight background service

### Planned

* Git activity tracking
* Calendar integration
* Work categorization
* Context-switch analytics
* Focus session detection
* Local dashboard
* AI-powered work coaching
* Raspberry Pi synchronization

---

## Privacy First

WorkGraph is designed with privacy as a core principle.

### What WorkGraph Collects

* Application names
* Process names
* Window titles
* Browser domains
* Idle time
* Session duration
* System events

### What WorkGraph Does NOT Collect

* Screenshots
* Keystrokes
* Clipboard contents
* Email contents
* Chat messages
* Browser page content
* Audio recordings
* Webcam data
* Passwords
* Source code

The goal is to understand activity patterns, not inspect personal or confidential information.

---

## Architecture

```text
+---------------------+
| Activity Collector  |
+----------+----------+
           |
           v
+---------------------+
| Event Processor     |
+----------+----------+
           |
           v
+---------------------+
| SQLite Database     |
+----------+----------+
           |
           v
+---------------------+
| Analytics (Future)  |
+---------------------+
```

---

## Data Flow

```text
Active Window
      |
Browser Activity
      |
Idle Detection
      |
      v
Activity Events
      |
      v
Session Aggregation
      |
      v
SQLite Storage
      |
      v
Future Analytics Engine
```

---

## Project Structure

```text
workgraph/
│
├── collector/
│   ├── window_tracker.py
│   ├── browser_tracker.py
│   └── idle_tracker.py
│
├── processor/
│   └── session_builder.py
│
├── db/
│   ├── repository.py
│   └── schema.sql
│
├── services/
│   └── collector_service.py
│
├── config/
│   └── settings.yaml
│
├── logs/
│
├── tests/
│
├── activity.db
│
├── main.py
│
└── README.md
```

---

## Example Activity Session

```json
{
  "start_time": "2026-07-09T10:05:00",
  "end_time": "2026-07-09T11:17:00",
  "duration_sec": 4320,
  "app_name": "VSCode",
  "window_title": "mcp_server.py",
  "browser_domain": null,
  "is_idle": false
}
```

---

## Goals

### Version 1

Create a reliable and efficient activity collection platform.

Success criteria:

* Less than 1% CPU usage
* Less than 100 MB RAM
* Runs continuously in the background
* Accurate activity timeline
* Local-only storage

### Version 2

Introduce analytics and reporting.

Potential insights:

* Focus time
* Context switching
* Workload distribution
* Meeting impact
* Productivity trends

### Version 3

Introduce AI-powered coaching.

Potential capabilities:

* Daily work summaries
* Weekly productivity reviews
* Burnout risk detection
* Personalized improvement plans
* Goal alignment analysis

---

## Technology Stack

### Core

* Python 3.11+
* uv
* SQLite
* SQLAlchemy
* Pydantic

### Activity Collection

* psutil
* pywin32 (Windows)
* browser-history integration

---

## Version 1 Quick Start

Install dependencies and run the collector with uv:

```bash
uv sync
uv run python main.py
```

Collect one sample and exit:

```bash
uv run python main.py --once
```

Run the v1 tests:

```bash
uv run python -m unittest discover -s tests
```

### Platform Notes

Windows is the primary target for v1 active application tracking. WorkGraph uses the Win32 foreground-window and last-input APIs when running on Windows. Install the optional Windows dependency group if you extend the collector with pywin32 integrations:

```bash
uv sync --extra windows
```

Linux support is included through common X11 tools. Active-window tracking uses `xprop` or `xdotool` when available, and idle detection uses `xprintidle` when available.

Browser domain tracking is best-effort and privacy-preserving. WorkGraph first checks the active browser window title for a visible URL/domain. If the title does not contain one, it copies the browser History SQLite database, reads recent URL metadata, extracts only the domain, and stores only that domain in `activity.db`.

Supported v1 browser history paths include Brave, Chrome/Chromium, Edge, and Firefox on Windows and Linux. Brave usually does not expose the URL in the window title, so the History fallback is the expected path for Brave domain detection.

### Future

* FastAPI
* PostgreSQL
* Redis
* Ollama
* OpenAI-compatible LLMs

---

## Roadmap

### v1.0

* [ ] Active window tracking
* [ ] Idle detection
* [ ] Browser domain tracking
* [ ] SQLite persistence
* [ ] Session aggregation

### v1.1

* [ ] Git activity tracking
* [ ] Calendar event tracking
* [ ] Configuration management

### v2.0

* [ ] Analytics dashboard
* [ ] Focus time analysis
* [ ] Context-switch analysis
* [ ] Work categorization

### v3.0

* [ ] AI coaching engine
* [ ] Daily summaries
* [ ] Weekly reviews
* [ ] Burnout detection

---

## Contributing

Contributions, ideas, bug reports, and feature requests are welcome.

Please open an issue before starting large feature implementations.

---

## License

Apache License 2.0

Copyright © 2026

---

## Philosophy

Measure first.

Understand second.

Optimize third.

WorkGraph exists to help engineers make better decisions using objective data rather than assumptions about how time is spent.
