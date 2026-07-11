from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml


MEETING_SQL_PREDICATE = """
(
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
"""


@dataclass(frozen=True)
class GoalDrift:
    goal: str
    planned_pct: float
    actual_pct: float
    delta_pct_points: float
    relative_gap_pct: float


def export_activity_sessions(
    *,
    db_path: str | Path,
    export_format: str,
    output_path: str | Path | None = None,
    days: int | None = None,
    include_idle: bool = True,
    now: datetime | None = None,
) -> Path:
    export_format = export_format.lower()
    if export_format not in {"csv", "json", "markdown"}:
        raise ValueError("export_format must be csv, json, or markdown")

    rows = _query_sessions(db_path=db_path, days=days, include_idle=include_idle)

    target = Path(output_path) if output_path is not None else _default_export_path(export_format, now=now)
    target.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "json":
        target.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    elif export_format == "csv":
        _write_csv(target, rows)
    else:
        target.write_text(_sessions_to_markdown(rows), encoding="utf-8")

    return target


def generate_weekly_report_markdown(
    *,
    db_path: str | Path,
    days: int = 7,
    goals_path: str | Path | None = None,
    now: datetime | None = None,
) -> str:
    now_utc = now or datetime.now(UTC)
    since = now_utc - timedelta(days=days)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        active_seconds = _scalar(
            conn,
            """
            SELECT SUM(duration_sec)
            FROM activity_sessions
            WHERE start_time >= ? AND is_idle = 0
            """,
            (since.isoformat(),),
        )

        meeting_seconds = _scalar(
            conn,
            f"""
            SELECT SUM(duration_sec)
            FROM activity_sessions
            WHERE start_time >= ?
              AND is_idle = 0
              AND {MEETING_SQL_PREDICATE}
            """,
            (since.isoformat(),),
        )

        context_switches = _count_context_switches(conn=conn, since_iso=since.isoformat())

        top_projects = _rows_to_named_seconds(
            conn,
            """
            SELECT git_repo as name, SUM(duration_sec) as total_seconds
            FROM activity_sessions
            WHERE start_time >= ?
              AND is_idle = 0
              AND git_repo IS NOT NULL
            GROUP BY git_repo
            ORDER BY total_seconds DESC
            LIMIT 5
            """,
            (since.isoformat(),),
        )

        top_tags = _rows_to_named_seconds(
            conn,
            """
            SELECT COALESCE(tag, 'Untagged') as name, SUM(duration_sec) as total_seconds
            FROM activity_sessions
            WHERE start_time >= ?
              AND is_idle = 0
            GROUP BY COALESCE(tag, 'Untagged')
            ORDER BY total_seconds DESC
            LIMIT 10
            """,
            (since.isoformat(),),
        )

    lines: list[str] = []
    lines.append("# Week Summary")
    lines.append("")
    lines.append(f"Range: {since.date().isoformat()} to {now_utc.date().isoformat()}")
    lines.append(f"Focus Time: {_format_hours(active_seconds)}")
    lines.append(f"Meetings: {_format_hours(meeting_seconds)}")
    lines.append(f"Context Switches: {context_switches}")
    lines.append("")
    lines.append("Top Projects")
    if top_projects:
        for idx, item in enumerate(top_projects, start=1):
            lines.append(f"{idx}. {item['name']} ({_format_hours(item['total_seconds'])})")
    else:
        lines.append("1. None")

    lines.append("")
    lines.append("Top Tags")
    if top_tags:
        for idx, item in enumerate(top_tags, start=1):
            lines.append(f"{idx}. {item['name']} ({_format_hours(item['total_seconds'])})")
    else:
        lines.append("1. None")

    if goals_path is not None:
        goal_drifts = analyze_goal_allocation(
            db_path=db_path,
            goals_path=goals_path,
            days=days,
            now=now_utc,
        )
        lines.extend(_goal_drift_section(goal_drifts))

    return "\n".join(lines).rstrip() + "\n"


def write_weekly_report(
    *,
    db_path: str | Path,
    output_path: str | Path,
    days: int = 7,
    goals_path: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    report = generate_weekly_report_markdown(
        db_path=db_path,
        days=days,
        goals_path=goals_path,
        now=now,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    return target


def analyze_goal_allocation(
    *,
    db_path: str | Path,
    goals_path: str | Path,
    days: int = 7,
    now: datetime | None = None,
) -> list[GoalDrift]:
    goals = _load_goals(goals_path)
    if not goals:
        return []

    now_utc = now or datetime.now(UTC)
    since = now_utc - timedelta(days=days)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT COALESCE(tag, 'Untagged') as tag_name, SUM(duration_sec) as total_seconds
            FROM activity_sessions
            WHERE start_time >= ?
              AND is_idle = 0
            GROUP BY COALESCE(tag, 'Untagged')
            """,
            (since.isoformat(),),
        )
        rows = list(cursor.fetchall())

    total_seconds = sum((int(row["total_seconds"] or 0) for row in rows))
    actual_by_tag = {str(row["tag_name"]): int(row["total_seconds"] or 0) for row in rows}

    drifts: list[GoalDrift] = []
    for goal, planned_pct in goals.items():
        actual_pct = ((actual_by_tag.get(goal, 0) / total_seconds) * 100.0) if total_seconds else 0.0
        delta = actual_pct - planned_pct
        relative_gap = 0.0
        if planned_pct > 0:
            relative_gap = ((planned_pct - actual_pct) / planned_pct) * 100.0

        drifts.append(
            GoalDrift(
                goal=goal,
                planned_pct=round(planned_pct, 2),
                actual_pct=round(actual_pct, 2),
                delta_pct_points=round(delta, 2),
                relative_gap_pct=round(relative_gap, 2),
            )
        )

    drifts.sort(key=lambda item: abs(item.delta_pct_points), reverse=True)
    return drifts


def goal_drift_markdown(
    *,
    db_path: str | Path,
    goals_path: str | Path,
    days: int = 7,
    now: datetime | None = None,
) -> str:
    drifts = analyze_goal_allocation(
        db_path=db_path,
        goals_path=goals_path,
        days=days,
        now=now,
    )
    lines = ["# Goal Allocation Report", ""]
    lines.extend(_goal_drift_section(drifts))
    return "\n".join(lines).rstrip() + "\n"


def _goal_drift_section(drifts: list[GoalDrift]) -> list[str]:
    lines = ["", "Goal Allocation Drift"]
    if not drifts:
        lines.append("No goals configured or no tracked data.")
        return lines

    lines.append("| Goal | Planned % | Actual % | Delta (pp) | Status |")
    lines.append("|---|---:|---:|---:|---|")

    for drift in drifts:
        status = _status_for_drift(drift.delta_pct_points)
        lines.append(
            f"| {drift.goal} | {drift.planned_pct:.1f} | {drift.actual_pct:.1f} | {drift.delta_pct_points:+.1f} | {status} |"
        )

    under_allocated = [d for d in drifts if d.delta_pct_points <= -5 and d.planned_pct > 0]
    if under_allocated:
        lines.append("")
        lines.append("Key Alerts")
        for drift in under_allocated:
            lines.append(
                f"- {drift.goal} is receiving {max(drift.relative_gap_pct, 0):.0f}% less attention than planned."
            )

    return lines


def _status_for_drift(delta_pct_points: float) -> str:
    if delta_pct_points <= -5:
        return "Under"
    if delta_pct_points >= 5:
        return "Over"
    return "On track"


def _load_goals(goals_path: str | Path) -> dict[str, float]:
    raw = Path(goals_path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw) or {}

    goals_obj = parsed.get("goals") if isinstance(parsed, dict) else parsed
    if not isinstance(goals_obj, dict):
        return {}

    goals: dict[str, float] = {}
    for key, value in goals_obj.items():
        if value is None:
            continue
        try:
            goals[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return goals


def _query_sessions(
    *,
    db_path: str | Path,
    days: int | None,
    include_idle: bool,
) -> list[dict]:
    query = "SELECT * FROM activity_sessions WHERE 1=1"
    params: list[str | int] = []

    if days is not None:
        since = datetime.now(UTC) - timedelta(days=days)
        query += " AND start_time >= ?"
        params.append(since.isoformat())

    if not include_idle:
        query += " AND is_idle = 0"

    query += " ORDER BY start_time ASC"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]

    return rows


def _default_export_path(export_format: str, now: datetime | None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return Path("exports") / f"activity-export-{stamp}.{_file_extension_for_format(export_format)}"


def _file_extension_for_format(export_format: str) -> str:
    if export_format == "markdown":
        return "md"
    return export_format


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = [
        "id",
        "start_time",
        "end_time",
        "duration_sec",
        "app_name",
        "process_name",
        "window_title",
        "browser_domain",
        "is_idle",
        "idle_seconds",
        "git_repo",
        "git_branch",
        "context_switches",
        "tag",
        "platform",
        "created_at",
    ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _sessions_to_markdown(rows: list[dict]) -> str:
    lines = [
        "# Activity Export",
        "",
        f"Rows: {len(rows)}",
        "",
        "| Start | End | Duration (min) | Tag | App | Domain | Repo | Idle |",
        "|---|---|---:|---|---|---|---|---|",
    ]

    for row in rows:
        duration_min = round((int(row.get("duration_sec") or 0)) / 60.0, 1)
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(str(row.get("start_time") or "")),
                    _md_cell(str(row.get("end_time") or "")),
                    f"{duration_min:.1f}",
                    _md_cell(str(row.get("tag") or "Untagged")),
                    _md_cell(str(row.get("app_name") or "")),
                    _md_cell(str(row.get("browser_domain") or "")),
                    _md_cell(str(row.get("git_repo") or "")),
                    _md_cell("yes" if int(row.get("is_idle") or 0) else "no"),
                ]
            )
            + " |"
        )

    return "\n".join(lines).rstrip() + "\n"


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _scalar(conn: sqlite3.Connection, query: str, params: tuple) -> int:
    cursor = conn.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        return 0
    value = row[0]
    return int(value or 0)


def _rows_to_named_seconds(conn: sqlite3.Connection, query: str, params: tuple) -> list[dict[str, int | str]]:
    cursor = conn.execute(query, params)
    return [
        {"name": str(row["name"]), "total_seconds": int(row["total_seconds"] or 0)}
        for row in cursor.fetchall()
    ]


def _count_context_switches(*, conn: sqlite3.Connection, since_iso: str) -> int:
    try:
        cursor = conn.execute(
            """
            WITH ordered AS (
                SELECT
                    start_time,
                    app_name,
                    window_title,
                    browser_domain,
                    LAG(app_name) OVER (ORDER BY start_time) as prev_app_name,
                    LAG(window_title) OVER (ORDER BY start_time) as prev_window_title,
                    LAG(browser_domain) OVER (ORDER BY start_time) as prev_browser_domain
                FROM activity_sessions
                WHERE start_time >= ? AND is_idle = 0
            )
            SELECT COUNT(*)
            FROM ordered
            WHERE prev_app_name IS NOT NULL
              AND (
                  COALESCE(app_name, '') != COALESCE(prev_app_name, '')
                  OR COALESCE(window_title, '') != COALESCE(prev_window_title, '')
                  OR COALESCE(browser_domain, '') != COALESCE(prev_browser_domain, '')
              )
            """,
            (since_iso,),
        )
        row = cursor.fetchone()
        return int((row[0] if row is not None else 0) or 0)
    except sqlite3.OperationalError:
        return _scalar(
            conn,
            """
            SELECT SUM(context_switches)
            FROM activity_sessions
            WHERE start_time >= ? AND is_idle = 0
            """,
            (since_iso,),
        )


def _format_hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}h"
