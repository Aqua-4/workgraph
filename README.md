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

### Version 1 — Collection Foundation

* Active application tracking
* Window title tracking
* Browser domain tracking
* Idle time detection
* Session aggregation
* Local SQLite storage
* Lightweight background service

### Version 1.1 — Work Attribution

* Git repository tracking (branch, commits, modified files)
* Activity tagging (automatic categorization by rules)
* Context-switch counting
* Advanced session splitting

### Planned

* Calendar integration (Outlook/Teams meetings)
* Focus session analytics
* Local dashboard
* Timeline view
* AI-powered coaching
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
* **Git repository, branch, and recent commits** (v1.1+)
* **Activity tags** (based on configurable rules)
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
│   ├── idle_tracker.py
│   └── git_tracker.py
│
├── processor/
│   └── session_builder.py
│
├── services/
│   ├── collector_service.py
│   └── activity_tagger.py
│
├── db/
│   ├── repository.py
│   └── schema.sql
│
├── config/
│   ├── settings.yaml
│   └── tags.yaml
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
  "window_title": "rbac_service.py",
  "browser_domain": null,
  "is_idle": false,
  "idle_seconds": 0,
  "git_repo": "mcp-platform",
  "git_branch": "feature/rbac",
  "git_commit_hash": "a1b2c3d",
  "git_modified_files": "rbac_service.py,auth.py",
  "context_switches": 3,
  "tag": "Client Delivery"
}
```

---

## Activity Tagging (v1.1+)

WorkGraph can automatically categorize your work using configurable rules in `config/tags.yaml`.

### Example Configuration

```yaml
tags:
  Client Delivery:
    repos:
      - compass
      - tenet

  Interview Prep:
    domains:
      - leetcode.com
      - hackerrank.com
    keywords:
      - interview

  Learning:
    domains:
      - udemy.com
      - coursera.org

  Personal Projects:
    repos:
      - crypto-trader
```

### How It Works

Sessions are tagged by matching:
- **Git repositories** — if `git_repo` matches a rule's `repos` list
- **Browser domains** — if `browser_domain` matches a rule's `domains` list
- **Keywords** — if any keyword appears in window title, app name, or domain

Each session gets exactly one tag (first matching rule wins).

### Use Case

After a week, WorkGraph can show:

```
Client Delivery    32h
Meetings           11h
Learning            2h
Personal Projects   1h
Interview Prep      0h
```

Compare this against how you *felt* you spent your time—often revealing unexpected patterns.

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

Introduce AI-powered coaching and optional centralized storage.

**Storage options:**
* Local SQLite (default) — all data stays on your machine
* PostgreSQL (opt-in) — push logs to your own database server for team analytics or compliance

Potential capabilities:

* Daily work summaries
* Weekly productivity reviews
* Burnout risk detection
* Personalized improvement plans
* Goal alignment analysis
* Team-level analytics (when using PostgreSQL backend)

---

## Technology Stack

### Core

* Python 3.11+
* uv
* SQLite
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

### Running as a Background Service

Use the included launcher scripts to run WorkGraph automatically on login or startup.

#### Windows — Task Scheduler

1. Open **Task Scheduler** and choose **Create Task**.
2. **General** tab: give it a name (e.g. `WorkGraph Collector`). Optionally tick *Run whether user is logged on or not*.
3. **Triggers** tab: click *New* → *At log on* (or *At startup*).
4. **Actions** tab: click *New* → *Start a program* → browse to `run.bat` in the project folder.
5. **Settings** tab: uncheck *Stop the task if it runs longer than*.
6. Click *OK* and enter your password if prompted.

To run a one-shot snapshot on a fixed schedule instead of running continuously, set the trigger interval you want and point the action to:

```
run.bat --once
```

#### Linux — cron

Make the script executable once:

```bash
chmod +x run.sh
```

Open your crontab:

```bash
crontab -e
```

Run at boot (continuous collector):

```cron
@reboot /absolute/path/to/workgraph/run.sh >> /absolute/path/to/workgraph/logs/cron.log 2>&1
```

Run a one-shot snapshot every hour:

```cron
0 * * * * /absolute/path/to/workgraph/run.sh --once >> /absolute/path/to/workgraph/logs/cron.log 2>&1
```

### Future

* FastAPI
* SQLAlchemy
* PostgreSQL
* Redis
* Ollama
* OpenAI-compatible LLMs

---

## Roadmap

### v1.0

* [x] Active window tracking
* [x] Idle detection
* [x] Browser domain tracking
* [x] SQLite persistence
* [x] Session aggregation

### v1.1

* [x] Git repository tracking
* [x] Activity tagging (rules-based categorization)
* [x] Context-switch counting
* [ ] Calendar event tracking
* [ ] Focus time analytics

### v1.2

* [ ] Local dashboard
* [ ] Timeline view
* [ ] Weekly reports

### v2.0

* [ ] Analytics engine
* [ ] Focus session detection
* [ ] Burnout risk detection

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
