"""FastAPI web server for WorkGraph dashboard."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
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

    # Total idle time (from idle sessions)
    cursor.execute(
        """
        SELECT SUM(duration_sec) as total_idle_seconds
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 1
        """,
        (start_iso,),
    )
    total_idle_seconds = cursor.fetchone()["total_idle_seconds"] or 0

    # Tagged active time for goal adherence
    cursor.execute(
        """
        SELECT SUM(duration_sec) as tagged_active_seconds
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 0 AND tag IS NOT NULL
        """,
        (start_iso,),
    )
    tagged_active_seconds = cursor.fetchone()["tagged_active_seconds"] or 0

    # Context switching metrics
    cursor.execute(
        """
        SELECT SUM(context_switches) as total_switches
        FROM activity_sessions
        WHERE start_time >= ? AND is_idle = 0
        """,
        (start_iso,),
    )
    total_switches = cursor.fetchone()["total_switches"] or 0

    # Deep work / focus metrics
    cursor.execute(
        """
        SELECT
            COUNT(*) as deep_work_blocks,
            MAX(duration_sec) as longest_focus_sec,
            AVG(duration_sec) as avg_focus_sec
        FROM activity_sessions
        WHERE start_time >= ?
          AND is_idle = 0
          AND duration_sec >= 1800
        """,
        (start_iso,),
    )
    focus_row = cursor.fetchone()
    deep_work_blocks = focus_row["deep_work_blocks"] or 0
    longest_focus_sec = focus_row["longest_focus_sec"] or 0
    avg_focus_sec = focus_row["avg_focus_sec"] or 0

    # Meeting proxy from app name / domain / title patterns
    cursor.execute(
        """
        SELECT SUM(duration_sec) as meeting_seconds
        FROM activity_sessions
        WHERE start_time >= ?
          AND is_idle = 0
          AND (
            LOWER(COALESCE(app_name, '')) LIKE '%teams%'
            OR LOWER(COALESCE(app_name, '')) LIKE '%zoom%'
            OR LOWER(COALESCE(app_name, '')) LIKE '%webex%'
            OR LOWER(COALESCE(app_name, '')) LIKE '%slack%'
            OR LOWER(COALESCE(browser_domain, '')) LIKE '%meet.google.com%'
            OR LOWER(COALESCE(browser_domain, '')) LIKE '%teams.microsoft.com%'
            OR LOWER(COALESCE(browser_domain, '')) LIKE '%zoom.us%'
            OR LOWER(COALESCE(browser_domain, '')) LIKE '%webex.com%'
            OR LOWER(COALESCE(window_title, '')) LIKE '%meeting%'
            OR LOWER(COALESCE(window_title, '')) LIKE '%standup%'
            OR LOWER(COALESCE(window_title, '')) LIKE '%huddle%'
          )
        """,
        (start_iso,),
    )
    meeting_seconds = cursor.fetchone()["meeting_seconds"] or 0

    # Top repositories by active time
    cursor.execute(
        """
        SELECT git_repo, SUM(duration_sec) as total_seconds
        FROM activity_sessions
        WHERE start_time >= ?
          AND is_idle = 0
          AND git_repo IS NOT NULL
        GROUP BY git_repo
        ORDER BY total_seconds DESC
        LIMIT 5
        """,
        (start_iso,),
    )
    repo_stats = {row["git_repo"]: row["total_seconds"] for row in cursor.fetchall()}

    # Daily trend (active time, meeting proxy, switches)
    cursor.execute(
        """
        SELECT
            substr(start_time, 1, 10) as day,
            SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) as active_seconds,
            SUM(CASE WHEN is_idle = 0 THEN context_switches ELSE 0 END) as switches,
            SUM(
                CASE
                    WHEN is_idle = 0 AND (
                        LOWER(COALESCE(app_name, '')) LIKE '%teams%'
                        OR LOWER(COALESCE(app_name, '')) LIKE '%zoom%'
                        OR LOWER(COALESCE(app_name, '')) LIKE '%webex%'
                        OR LOWER(COALESCE(app_name, '')) LIKE '%slack%'
                        OR LOWER(COALESCE(browser_domain, '')) LIKE '%meet.google.com%'
                        OR LOWER(COALESCE(browser_domain, '')) LIKE '%teams.microsoft.com%'
                        OR LOWER(COALESCE(browser_domain, '')) LIKE '%zoom.us%'
                        OR LOWER(COALESCE(browser_domain, '')) LIKE '%webex.com%'
                        OR LOWER(COALESCE(window_title, '')) LIKE '%meeting%'
                        OR LOWER(COALESCE(window_title, '')) LIKE '%standup%'
                        OR LOWER(COALESCE(window_title, '')) LIKE '%huddle%'
                    ) THEN duration_sec
                    ELSE 0
                END
            ) as meeting_seconds
        FROM activity_sessions
        WHERE start_time >= ?
        GROUP BY day
        ORDER BY day DESC
        LIMIT 7
        """,
        (start_iso,),
    )
    daily_trend = []
    for row in cursor.fetchall():
        active_seconds = row["active_seconds"] or 0
        switches = row["switches"] or 0
        meeting_day_seconds = row["meeting_seconds"] or 0
        daily_trend.append(
            {
                "day": row["day"],
                "active_hours": round(active_seconds / 3600, 2),
                "meeting_hours": round(meeting_day_seconds / 3600, 2),
                "switches_per_hour": round(switches / max(active_seconds / 3600, 0.001), 2),
            }
        )

    conn.close()

    active_hours = total_seconds / 3600 if total_seconds else 0
    tagged_ratio = (tagged_active_seconds / total_seconds) if total_seconds else 0
    meeting_ratio = (meeting_seconds / total_seconds) if total_seconds else 0
    switch_rate_per_hour = total_switches / active_hours if active_hours else 0

    return {
        "tag_stats": tag_stats,
        "app_stats": app_stats,
        "repo_stats": repo_stats,
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600, 1),
        "total_idle_seconds": total_idle_seconds,
        "total_idle_hours": round(total_idle_seconds / 3600, 1),
        "tagged_active_seconds": tagged_active_seconds,
        "tagged_ratio": round(tagged_ratio, 3),
        "meeting_seconds": meeting_seconds,
        "meeting_hours": round(meeting_seconds / 3600, 1),
        "meeting_ratio": round(meeting_ratio, 3),
        "total_switches": int(total_switches),
        "switch_rate_per_hour": round(switch_rate_per_hour, 2),
        "deep_work_blocks": int(deep_work_blocks),
        "longest_focus_sec": int(longest_focus_sec),
        "longest_focus_hours": round((longest_focus_sec or 0) / 3600, 2),
        "avg_focus_sec": int(avg_focus_sec or 0),
        "avg_focus_minutes": round((avg_focus_sec or 0) / 60, 1),
        "daily_trend": daily_trend,
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
