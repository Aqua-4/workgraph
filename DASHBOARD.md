# WorkGraph Dashboard & Timeline

The WorkGraph web dashboard provides a visual interface to view your activity data and analyze patterns.

## Quick Start

Start the dashboard server with the launcher scripts:

**Linux/macOS:**
```bash
./run.sh
```

**Windows:**
```cmd
run.bat
```

Then open your browser to: **http://127.0.0.1:3000**

### Manual Start

Or start manually on any port:

```bash
uv run python main.py --web
```

Customize the port:

```bash
uv run python main.py --web --port 8000
```

### Running Collector and Dashboard Together

To run the collector and dashboard simultaneously on different ports:

**Terminal 1 - Collector (collects background data):**
```bash
uv run python main.py
```

**Terminal 2 - Dashboard (web server on port 3000):**
```bash
./run.sh                    # Linux/macOS
# or
uv run python main.py --web --port 3000
```

Both will run continuously and share the same SQLite database.

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
uv run python main.py --web --port 8000
# Open http://127.0.0.1:8000
```

### Filter by tag:

Visit: `http://127.0.0.1:8000/timeline?tag=Client%20Delivery&days=14`

### Get JSON stats:

```bash
curl http://127.0.0.1:8000/api/stats?days=7 | jq .
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

Or edit the launcher script to use a different default port.

### No data appears

1. Run the collector first: `uv run python main.py --once`
2. Let it collect for a few minutes: `uv run python main.py`
3. Then start the dashboard: `uv run python main.py --web`

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
uv run python main.py

# Terminal 2: Run dashboard
uv run python main.py --web --port 8000
```

## Launcher Scripts

WorkGraph includes launcher scripts (`run.sh` for Linux/macOS, `run.bat` for Windows) that start the web dashboard on **port 3000**:

- They avoid interfering with development environments
- Can be added to Task Scheduler (Windows) or cron (Linux/macOS)
- Edit the scripts to change the default port

**Edit launcher port:**
- `run.sh`: Change `--port 3000` to desired port
- `run.bat`: Change `--port 3000` to desired port
