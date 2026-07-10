# WorkGraph Dashboard & Timeline

The WorkGraph web dashboard provides a visual interface to view your activity data and analyze patterns.

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

## Features

### Dashboard

The dashboard homepage shows:

- **Total Active Time** — How many hours you've been actively working (not idle)
- **Categories Tagged** — How many different project tags have been detected
- **Apps Used** — Number of distinct applications tracked
- **Time by Goal** — Stacked bar chart showing hours spent on each tagged project
- **Time by Application** — Stacked bar chart showing hours in each app

### Timeline

The timeline view shows a detailed chronological breakdown of your activity:

- **Chronological sessions** — All tracked sessions in order
- **Rich metadata** — App name, window title, domain, git repo, branch
- **Filtering** — Filter by date range, tag, or application
- **Status indicator** — Visual indicator for active vs idle time
- **Duration display** — Hours and minutes for each session

### API Endpoints

Access raw data via REST API:

- `GET /api/sessions?days=7&limit=100` — Get raw session data
- `GET /api/stats?days=7` — Get summary statistics

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

## Future Enhancements (V1.2+)

- Real-time updates (WebSocket)
- Interactive charts and graphs
- Goal drift alerts
- Weekly reports
- Export to CSV/JSON
- Dark mode toggle
- Mobile-responsive improvements

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

### Server won't start

Ensure FastAPI and Uvicorn are installed:

```bash
uv sync
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
