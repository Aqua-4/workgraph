"""FastAPI web server for WorkGraph dashboard."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
import sqlite3

app = FastAPI(title="WorkGraph Dashboard")

# Setup Jinja2
template_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

jinja_env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(["html", "xml"]),
)

# Mount static files
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def get_db_path() -> Path:
    """Get the database path from environment or default."""
    return Path("activity.db")


def query_sessions(
    db_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query activity sessions from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM activity_sessions WHERE 1=1"
    params = []

    if start_date:
        query += " AND start_time >= ?"
        params.append(start_date.isoformat())

    if end_date:
        query += " AND end_time <= ?"
        params.append(end_date.isoformat())

    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_summary_stats(db_path: Path, days: int = 7) -> dict:
    """Get summary statistics for the past N days."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    start_iso = start_date.isoformat()

    # Total time by tag
    cursor.execute(
        """
        SELECT tag, SUM(duration_sec) as total_seconds
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 0
        GROUP BY tag
        ORDER BY total_seconds DESC
        """,
        (start_iso,),
    )
    tag_stats = {row["tag"] or "Untagged": row["total_seconds"] for row in cursor.fetchall()}

    # Total time by app
    cursor.execute(
        """
        SELECT app_name, SUM(duration_sec) as total_seconds
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 0
        GROUP BY app_name
        ORDER BY total_seconds DESC
        LIMIT 10
        """,
        (start_iso,),
    )
    app_stats = {row["app_name"]: row["total_seconds"] for row in cursor.fetchall()}

    # Total active time
    cursor.execute(
        """
        SELECT SUM(duration_sec) as total_seconds
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 0
        """,
        (start_iso,),
    )
    total_seconds = cursor.fetchone()["total_seconds"] or 0

    conn.close()

    return {
        "tag_stats": tag_stats,
        "app_stats": app_stats,
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600, 1),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Dashboard homepage."""
    db_path = get_db_path()

    if not db_path.exists():
        return "<h1>WorkGraph Dashboard</h1><p>No data collected yet. Run the collector first.</p>"

    stats = get_summary_stats(db_path, days=7)

    template = jinja_env.get_template("dashboard.html")
    return template.render(stats=stats)


@app.get("/timeline", response_class=HTMLResponse)
async def timeline(
    days: int = Query(7, ge=1, le=30),
    tag: str | None = Query(None),
    app: str | None = Query(None),
):
    """Timeline view of activities."""
    db_path = get_db_path()

    if not db_path.exists():
        return "<h1>WorkGraph Timeline</h1><p>No data collected yet.</p>"

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = query_sessions(db_path, start_date=start_date, limit=500)

    # Filter by tag/app if provided
    if tag:
        sessions = [s for s in sessions if s.get("tag") == tag]
    if app:
        sessions = [s for s in sessions if s.get("app_name") == app]

    template = jinja_env.get_template("timeline.html")
    return template.render(
        sessions=sessions,
        days=days,
        selected_tag=tag,
        selected_app=app,
    )


@app.get("/api/sessions")
async def api_sessions(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=1000),
):
    """API endpoint for session data."""
    db_path = get_db_path()

    if not db_path.exists():
        return {"sessions": []}

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = query_sessions(db_path, start_date=start_date, limit=limit)

    return {
        "sessions": sessions,
        "count": len(sessions),
    }


@app.get("/api/stats")
async def api_stats(days: int = Query(7, ge=1, le=30)):
    """API endpoint for summary statistics."""
    db_path = get_db_path()

    if not db_path.exists():
        return {
            "tag_stats": {},
            "app_stats": {},
            "total_seconds": 0,
            "total_hours": 0,
        }

    return get_summary_stats(db_path, days=days)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
