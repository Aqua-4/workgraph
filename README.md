# WorkGraph

> Local-first work telemetry and goal attribution for engineers.

WorkGraph is a privacy-first activity intelligence platform that helps engineers understand how their workday is spent and whether their time aligns with their goals. Unlike generic time trackers, WorkGraph attributes activities to meaningful work categories (Client Delivery, Learning, Interview Prep, Personal Projects, etc.) so you can see if you're spending enough time on what matters.

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

## Goal Attribution

WorkGraph's core innovation is **automatic categorization** of activities into meaningful goals and projects. Instead of just tracking time in applications, WorkGraph answers:

**This Week:**

```text
Client Delivery       38 hours
Meetings             12 hours
Interview Prep        2 hours
Learning              1 hour
Personal Projects    0.5 hours
```

You can configure custom tags based on:

* **Git repositories** — Map projects to goals
* **Browser domains** — Track learning sites separately from entertainment
* **Window titles & keywords** — Catch ad-hoc work patterns

This enables powerful insights:

* "I intended to spend 5 hours on NZ Masters this week, but only managed 0.5 hours"
* "I'm in 8 hours of meetings per day—that's 40% of my workday"
* "I'm neglecting professional development"

---

## Goal Drift Detection

WorkGraph's future **Goal Drift Analysis** feature will alert you when your actual time allocation diverges from your intended priorities.

**Example Warning:**

```
This Week
─────────────────────────────────────
Client Delivery      42h  ✓ On track
Meetings            11h  ⚠ Above target (5h)
Learning             1h  🚨 Way below target (5h)
NZ Masters           0h  🚨 Neglected (5h planned)
Personal Projects   0.5h ✓ On track
─────────────────────────────────────

⚠️ Alert: You spent only 1.5 hours on long-term
   goals this week. That's 3% of your time.
   
Recommendation: Block 1-2 hours daily for 
learning and NZ Masters work.
```

This kind of insight isn't commonly available in time tracking tools, but it's exactly what busy engineers need to stay aligned with their goals.

---

## Features

For a code-verified implementation matrix, see [FEATURES_STATUS.md](FEATURES_STATUS.md).
For multi-device sync setup steps (server + device configuration), see [docs/sync/MULTI_DEVICE_SETUP.md](docs/sync/MULTI_DEVICE_SETUP.md).
For sync launch validation and pre-flight checks, see [docs/sync/SYNC_HARDENING_LAUNCH.md](docs/sync/SYNC_HARDENING_LAUNCH.md).

### Version 1.0 — Collection Foundation

* Active application tracking
* Window title tracking
* Browser domain tracking
* Idle time detection
* Session aggregation
* Local SQLite storage
* Lightweight background service

### Version 1.1 — Work Attribution

* Git repository tracking (branch detection, modified file metadata)
* Activity tagging (automatic categorization by rules)
* Context-switch counting
* Advanced session splitting

### Version 1.2 — Visualization & Multi-Device

* Local web dashboard
* Timeline view
* Journal view and APIs (entries, correlation, reflections)
* Structured work events (Achievement, Incident, Decision, Risk, Blocker, etc.)
* Weekly report generator (`workgraph report weekly`)
* Goal allocation drift analysis (`workgraph goals analyze`)
* Activity export (`workgraph export csv|json|markdown`)
* Server SQLite backend for multi-device sync via sync API
* Sync analytics from extracted `sync_sessions` columns + daily rollups
* Explicit dashboard/API source mode (`local` or `sync`)
* Sync metadata endpoints (`/api/sync/users`, `/api/sync/devices`)
* Sync rollup controls (`/api/sync/stats`, `/api/sync/rollups/rebuild`)

### Version 2.0 — Analytics Engine

* Goal drift detection (alerts when time allocation diverges from priorities)
* Focus block analysis
* Weekly/monthly reports
* Burnout risk indicators
* Trend analysis

### Version 3.0 — Intelligence

* AI-powered coaching
* Productivity recommendations
* Goal alignment analysis
* Team insights (voluntary sharing)

### Future

* **Meeting detection** — Automatically detect meeting time from Teams, Zoom, Google Meet, and Webex windows
* PostgreSQL backend as optional v3 storage upgrade
* Mobile companion app

---

## Launch Test Quickstart (Sync)

Before enabling sync for daily use, run:

```bash
uv run python -m unittest tests.test_sync_api tests.test_sync_worker tests.test_sync_daemon
```

Then validate sync-mode dashboard/API flows using the checklist in [docs/sync/SYNC_HARDENING_LAUNCH.md](docs/sync/SYNC_HARDENING_LAUNCH.md).

---

## Design Principles

**Local First** — All data is collected and stored on your machine. No cloud required.

**Privacy First** — No screenshots, keystrokes, or personal data collection. You control what you share.

**Vendor Neutral** — Works with any editor, browser, or tools. No lock-in.

**Low Resource Usage** — Minimal CPU, memory, and disk impact. Designed to run continuously.

**LLM Optional** — AI coaching is opt-in. Core features work without external services.

---

## Privacy

WorkGraph is designed with privacy as a core principle.

### What WorkGraph Collects

* Application names
* Process names
* Window titles
* Browser domains
* Idle time
* Session duration
* **Git repository and branch** (v1.1+)
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

### Re-tag Existing Sessions After Rule Changes

When you update `config/tags.yaml`, existing rows in `activity.db` keep their old tags until re-tagged.

Run a full re-tag migration:

```bash
uv run python main.py --retag-existing
```

What this does:
- Creates a timestamped backup in `backups/` first
- Recomputes tags for all stored sessions using current rules
- Updates only sessions where the tag changed

Current CLI support:

```bash
uv run python main.py --retag-existing --config config/settings.yaml

# Export activity sessions
uv run workgraph export csv
uv run workgraph export json --days 7
uv run workgraph export markdown --output exports/week.md

# Deterministic weekly summary
uv run workgraph report weekly
uv run workgraph report weekly --days 7 --output reports/week.md

# Goal allocation drift detection
uv run workgraph goals analyze --goals config/goals.yaml --days 7
```

To roll back, copy the backup `.db` file returned by the command over your active database file.

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

### Option 1: Unified (Collector + Dashboard)

Run everything with a single command:

```bash
# Start both collector and dashboard together
./run-dashboard.sh                    # Linux/macOS
# or
run-dashboard.bat                     # Windows
```

Then open your browser to: **http://127.0.0.1:4000**

The collector runs in self-healing mode (auto-restarts on crashes), while the dashboard displays data at the same time.

Unified mode also auto-starts the sync daemon when `sync_base_url` and `sync_token` are present in the resolved config (prefers `config/my-settings.yaml` when using default config path).

```bash
# Disable sync daemon startup in unified mode
./run-dashboard.sh --no-sync
```

### Option 2: Collector Only

If you only want to collect data without viewing it:

```bash
# Run the collector continuously
./run.sh                              # Linux/macOS
# or
run.bat                               # Windows
```

`run.sh` and `run.bat` use a self-healing supervisor by default in continuous mode, so collector crashes are automatically restarted.

Collect one sample and exit:

```bash
uv run python main.py --once
```

### Option 3: Manual Commands

For more control, use uv directly:

```bash
# Install dependencies
uv sync

# Run collector
uv run python main.py

# Run just the dashboard (in another terminal)
uv run python main.py --web --port 4000

# Run both together
uv run python -m services.unified_launcher

# Run both together without sync daemon
uv run python -m services.unified_launcher --no-sync
```

### Web Dashboard (V1.1+)

Features:
- **Dashboard**: See your time allocation by goal and app
- **Timeline**: Chronological view of all activities with filtering
- **API**: Access raw data via REST endpoints

For more details, see [DASHBOARD.md](DASHBOARD.md).

### Run Tests

Run the v1 tests:

```bash
# Install dev dependencies (includes pytest, ruff, and httpx2 required by FastAPI TestClient)
uv sync --extra dev

# Run tests
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

#### Windows — Launch Hidden Task

Use Task Scheduler to launch WorkGraph silently (no visible cmd window).

1. Open **Task Scheduler** and choose **Create Task**.
2. **General** tab: give it a name (e.g. `WorkGraph Collector (Hidden)`).
3. **Triggers** tab: click *New* → *At log on* (or *At startup*).
4. **Actions** tab: click *New* → *Start a program*.
5. Set **Program/script** to:

```text
wscript.exe
```

6. Set **Add arguments (optional)** to:

```text
//B //NoLogo "C:\path\to\workgraph\run-hidden.vbs"
```

7. Set **Start in (optional)** to your project folder:

```text
C:\path\to\workgraph
```

8. **Settings** tab: uncheck *Stop the task if it runs longer than*.
9. Click *OK*.

To run a one-shot snapshot on a fixed schedule instead of running continuously, keep the same program and use this argument instead:

```text
//B //NoLogo "C:\path\to\workgraph\run-hidden.vbs" --once
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
* [x] Focus time analytics

### v1.2

* [x] Local dashboard
* [x] Timeline view
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
