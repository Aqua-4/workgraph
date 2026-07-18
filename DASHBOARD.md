# WorkGraph Dashboard, Timeline, and Journal

The WorkGraph web app provides local and sync-backed visibility into activity sessions, trends, journaling, and structured work events.

Detailed mode-by-mode dashboard rendering and feature logic is documented in `docs/DASHBOARD_FEATURES_AND_LOGIC.md`.

## Quick Start

### Unified (Recommended)

Start both the collector and dashboard together with one command:

```bash
# Linux/macOS
./run-dashboard.sh

# Windows
run-dashboard.bat
```

Then open your browser to: **http://127.0.0.1:4000**

The collector runs in self-healing mode (auto-restarts if it crashes), and the dashboard displays data in real-time.

### Manual Start

Start the dashboard server directly:

```bash
uv run python main.py --web --port 4000
```

Then open your browser to: **http://127.0.0.1:4000**

Customize the port:

```bash
uv run python main.py --web --port 3000
```

### Running Collector and Dashboard Separately

If you want to run them in separate terminals:

**Terminal 1 - Collector:**
```bash
./run.sh          # Linux/macOS
# or
run.bat           # Windows
```

`run.sh` / `run.bat` are self-healing in continuous mode.

**Terminal 2 - Dashboard:**
```bash
uv run python main.py --web --port 4000
```

## Dashboard Modes

Dashboard behavior is controlled by `dashboard_mode` in settings.

- `standalone`: local-only dashboard and local registration
- `sync-client`: local dashboard with remote sync-server registration flow
- `sync-server`: server-side aggregated dashboard for multi-device sync data

## Features

### Dashboard

The dashboard homepage shows:

- **Total Active Time** — Active hours in selected mode/source
- **Time by Goal/Tag** — Allocation by tag
- **Time by Application** — Top applications by active time
- **Repository breakdown** — Top repos by active time
- **Meeting proxy and switch metrics** — Meeting-time proxy and context-switch rate
- **Daily trend row** — 7-day active, meeting, and switch density trend
- **Sync health summary** — Available in sync-server mode

When running in sync-server mode, overview values are aggregated from synced multi-device records.

### Timeline

The timeline view shows a detailed chronological breakdown of activity sessions:

- **Chronological sessions** — Ordered sessions with optional sync-source backing
- **Rich metadata** — App, title, domain, repo, branch, tag, idle/active state
- **Filtering** — Days, tag, app, and sync dimensions (user/device where applicable)
- **Chart + table view** — Trend visualization plus recent rows

### Journal and Work Events

The journal page supports:

- Free-form journal entries with optional time windows
- Correlated activity session summaries for each entry window
- Daily reflections (wins, problems, tomorrow, energy, stress)
- Structured work events (achievement, incident, decision, risk, blocker, etc.)

### API Endpoints

Access raw data via REST API:

- `GET /api/sessions?days=7&limit=100` — Get raw session data
- `GET /api/stats?days=7` — Get summary statistics
- `GET /api/stats?source=sync&user_id=<id>&days=7` — Sync-backed summary stats
- `POST /api/device/register` — Mode-aware device registration
- `GET /api/sync/users` — List sync users
- `GET /api/sync/devices?user_id=<id>` — List user devices
- `GET /api/sync/stats?user_id=<id>&days=30` — Rollup stats from sync tables
- `POST /api/sync/rollups/rebuild?user_id=<id>` — Rebuild sync daily rollups
- `POST /api/journal` / `GET /api/journal` / `PUT /api/journal/{id}` — Journal CRUD
- `GET /api/journal/{id}/correlated-sessions` — Time-overlap session summary
- `PUT /api/reflections/{date}` / `GET /api/reflections` — Reflection APIs

### HTML/CSS

The dashboard uses:

- Clean, modern design with responsive layout
- Simple inline CSS (no external dependencies)
- Jinja2 templates for server-side rendering
- Minimal JavaScript (optional for interactivity)

## Example Usage

### View last 7 days of activity:

```bash
uv run python main.py --web --port 4000
# Open http://127.0.0.1:4000
```

### Filter by tag:

Visit: `http://127.0.0.1:4000/timeline?tag=Client%20Delivery&days=14`

### Get JSON stats:

```bash
curl http://127.0.0.1:4000/api/stats?days=7 | jq .
```

## Architecture

The dashboard consists of:

- **FastAPI** — Web framework and REST API
- **Uvicorn** — ASGI server
- **Jinja2** — Template rendering
- **SQLite** — Data source

No external CSS frameworks, no JavaScript bundling, no frontend build step. Designed for simplicity and minimal resource usage.

## Future Enhancements

- Monthly reports
- Burnout indicators and alerts
- Deeper focus-block heuristics
- Additional sync operations tooling

## Troubleshooting

### Port already in use

```bash
# Use a different port
uv run python main.py --web --port 8001
```

### No data appears

1. Run unified mode: `./run-dashboard.sh` (or `run-dashboard.bat` on Windows)
2. Or run collector self-healing mode: `./run.sh` and dashboard in another terminal
3. For a quick sample, use: `uv run python main.py --once`
4. In sync-server mode, ensure at least one device has pushed data

### Server won't start

Ensure FastAPI and Uvicorn are installed:

```bash
uv sync
```

If port 8000 is already in use, choose another port:

```bash
uv run python main.py --web --port 8001
```

## Performance

The dashboard is designed to be lightweight:

- **Minimal memory** — Loads data on-demand from SQLite
- **Fast queries** — Indexed database for quick lookups
- **No background jobs** — Stateless request-response only
- **Can run alongside collector** — Separate processes on different ports

Example:

```bash
# Terminal 1: Run collector
./run.sh

# Terminal 2: Run dashboard
uv run python main.py --web --port 4000
```
