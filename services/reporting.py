from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from api.app import get_sync_health

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
    target_hours: float | None = None
    actual_hours: float = 0.0
    progress_pct: float = 0.0
    matched_intents: tuple[str, ...] = ()
    match_reason: str | None = None


def default_goals_path() -> Path:
    config_dir = Path(__file__).parent.parent / "config"
    custom_path = config_dir / "my-goals.yaml"
    default_path = config_dir / "goals.yaml"
    return custom_path if custom_path.exists() else default_path


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

    target = (
        Path(output_path)
        if output_path is not None
        else _default_export_path(export_format, now=now)
    )
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
    return _generate_activity_report_markdown(
        db_path=db_path,
        title="Week Summary",
        days=days,
        goals_path=goals_path,
        now=now,
    )


def generate_monthly_report_markdown(
    *,
    db_path: str | Path,
    days: int = 30,
    goals_path: str | Path | None = None,
    now: datetime | None = None,
) -> str:
    return _generate_activity_report_markdown(
        db_path=db_path,
        title="Month Summary",
        days=days,
        goals_path=goals_path,
        now=now,
    )


def _generate_activity_report_markdown(
    *,
    db_path: str | Path,
    title: str,
    days: int,
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

        context_switches = _count_context_switches(
            conn=conn, since_iso=since.isoformat()
        )

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
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"Range: {since.date().isoformat()} to {now_utc.date().isoformat()}")
    lines.append(f"Focus Time: {_format_hours(active_seconds)}")
    lines.append(f"Meetings: {_format_hours(meeting_seconds)}")
    lines.append(f"Context Switches: {context_switches}")
    lines.append("")
    lines.append("Top Projects")
    if top_projects:
        for idx, item in enumerate(top_projects, start=1):
            lines.append(
                f"{idx}. {item['name']} ({_format_hours(item['total_seconds'])})"
            )
    else:
        lines.append("1. None")

    lines.append("")
    lines.append("Top Tags")
    if top_tags:
        for idx, item in enumerate(top_tags, start=1):
            lines.append(
                f"{idx}. {item['name']} ({_format_hours(item['total_seconds'])})"
            )
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


def generate_goals_report_markdown(
    *,
    db_path: str | Path,
    days: int = 7,
    goals_path: str | Path | None = None,
    now: datetime | None = None,
) -> str:
    goals_target = Path(goals_path) if goals_path is not None else default_goals_path()
    if not goals_target.exists():
        return "# Goals Report\n\nNo goals configured.\n"

    drifts = analyze_goal_allocation(
        db_path=db_path,
        goals_path=goals_target,
        days=days,
        now=now,
    )
    lines = ["# Goals Report", ""]
    lines.append(f"Range: last {days} days")
    lines.append(f"Goals File: {goals_target}")
    lines.extend(_goal_drift_section(drifts))
    return "\n".join(lines).rstrip() + "\n"


def generate_sync_report_markdown(
    *,
    db_path: str | Path,
) -> str:
    health = get_sync_health(Path(db_path))
    lines = ["# Sync Status Report", ""]
    if not health.get("enabled"):
        lines.append("No sync tokens or devices are configured yet.")
        return "\n".join(lines).rstrip() + "\n"

    lines.append(f"Registered Devices: {int(health.get('registered_devices') or 0)}")
    lines.append(f"Synced Devices: {int(health.get('synced_devices') or 0)}")
    lines.append(f"Active Tokens: {int(health.get('active_tokens') or 0)}")
    lines.append(f"Last Sync: {health.get('last_sync_at') or '-'}")
    lines.append(f"Pending Pull Rows: {int(health.get('pending_pull_rows') or 0)}")
    lines.append(f"Open Sync Failures: {int(health.get('sync_failures') or 0)}")
    lines.append("")
    lines.append("Device Health")
    if health.get("device_statuses"):
        lines.append("| Device | Status | Last Sync | Pending Pull |")
        lines.append("|---|---|---|---:|")
        for item in health["device_statuses"]:
            lines.append(
                f"| {item.get('device_name') or item.get('device_id') or '-'} | {item.get('status') or '-'} | {item.get('last_sync_at') or '-'} | {int(item.get('pending_pull_rows') or 0)} |"
            )
    else:
        lines.append("No device health records available yet.")

    lines.append("")
    lines.append("Recent Sync Errors")
    if health.get("recent_errors"):
        lines.append("| Time | Endpoint | Status | Error | Device |")
        lines.append("|---|---|---:|---|---|")
        for item in health["recent_errors"]:
            lines.append(
                f"| {item.get('created_at') or '-'} | {item.get('endpoint') or '-'} | {item.get('status_code') or '-'} | {item.get('error_type') or '-'}: {item.get('message') or ''} | {item.get('device_id') or '-'} |"
            )
    else:
        lines.append("No sync failures recorded.")

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
            SELECT app_name, browser_domain, window_title, COALESCE(tag, 'Untagged') as tag_name, duration_sec
            FROM activity_sessions
            WHERE start_time >= ?
              AND is_idle = 0
            ORDER BY start_time
            """,
            (since.isoformat(),),
        )
        rows = list(cursor.fetchall())

    tag_intents = _load_tag_intent_metadata()
    total_seconds = sum(int(row["duration_sec"] or 0) for row in rows)
    actual_by_tag = {}
    for row in rows:
        tag_name = str(row["tag_name"] or "Untagged")
        actual_by_tag[tag_name] = actual_by_tag.get(tag_name, 0) + int(
            row["duration_sec"] or 0
        )

    drifts: list[GoalDrift] = []
    for goal, config in goals.items():
        if isinstance(config, dict):
            target_hours = config.get("target_hours")
            intents = config.get("intents") or []
            if target_hours is not None:
                matched_rows = [
                    row
                    for row in rows
                    if _session_matches_goal_intents(row, intents, tag_intents)
                ]
                actual_seconds = sum(
                    int(row["duration_sec"] or 0) for row in matched_rows
                )
                actual_hours = actual_seconds / 3600.0
                progress_pct = (
                    (actual_hours / target_hours * 100.0) if target_hours > 0 else 0.0
                )
                actual_pct = (
                    (actual_seconds / total_seconds * 100.0) if total_seconds else 0.0
                )
                planned_pct = 100.0
                delta = actual_pct - planned_pct
                relative_gap = (
                    ((planned_pct - actual_pct) / planned_pct * 100.0)
                    if planned_pct > 0
                    else 0.0
                )
                matched_intents = tuple(
                    sorted(
                        {
                            normalized
                            for normalized in {
                                str(intent).strip().lower()
                                for intent in intents
                                if intent
                            }
                            if normalized
                        }
                    )
                )
                match_reason = _describe_goal_match(matched_rows, matched_intents)
                drifts.append(
                    GoalDrift(
                        goal=goal,
                        planned_pct=round(planned_pct, 2),
                        actual_pct=round(actual_pct, 2),
                        delta_pct_points=round(delta, 2),
                        relative_gap_pct=round(relative_gap, 2),
                        target_hours=round(float(target_hours), 2),
                        actual_hours=round(actual_hours, 2),
                        progress_pct=round(progress_pct, 2),
                        matched_intents=matched_intents,
                        match_reason=match_reason,
                    )
                )
                continue

            planned_pct = float(config.get("planned_pct", 0.0) or 0.0)
            actual_pct = (
                ((actual_by_tag.get(goal, 0) / total_seconds) * 100.0)
                if total_seconds
                else 0.0
            )
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
            continue

        planned_pct = float(config)
        actual_pct = (
            ((actual_by_tag.get(goal, 0) / total_seconds) * 100.0)
            if total_seconds
            else 0.0
        )
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
    return generate_goals_report_markdown(
        db_path=db_path,
        days=days,
        goals_path=goals_path,
        now=now,
    )


def _goal_drift_section(drifts: list[GoalDrift]) -> list[str]:
    lines = ["", "Goal Allocation Drift"]
    if not drifts:
        lines.append("No goals configured or no tracked data.")
        return lines

    uses_hours = any(drift.target_hours is not None for drift in drifts)
    if uses_hours:
        lines.append(
            "| Goal | Target Hours | Actual Hours | Progress | Status | Match |"
        )
        lines.append("|---|---:|---:|---:|---|---|")

        for drift in drifts:
            status = _status_for_progress(drift.progress_pct)
            match_text = drift.match_reason or ", ".join(drift.matched_intents) or "-"
            lines.append(
                f"| {drift.goal} | {drift.target_hours:.1f} | {drift.actual_hours:.1f} | {drift.progress_pct:.0f}% | {status} | {match_text} |"
            )
    else:
        lines.append("| Goal | Planned % | Actual % | Delta (pp) | Status |")
        lines.append("|---|---:|---:|---:|---|")

        for drift in drifts:
            status = _status_for_drift(drift.delta_pct_points)
            lines.append(
                f"| {drift.goal} | {drift.planned_pct:.1f} | {drift.actual_pct:.1f} | {drift.delta_pct_points:+.1f} | {status} |"
            )

    under_allocated = [
        d for d in drifts if d.delta_pct_points <= -5 and d.planned_pct > 0
    ]
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


def _status_for_progress(progress_pct: float) -> str:
    if progress_pct < 90:
        return "Under"
    if progress_pct > 110:
        return "Over"
    return "On track"


def _describe_goal_match(
    rows: list[sqlite3.Row], matched_intents: tuple[str, ...]
) -> str:
    if not rows:
        return "no matching sessions"

    intents_text = ", ".join(matched_intents) if matched_intents else "intent metadata"
    sample_tags = sorted({str(row["tag_name"] or "Untagged") for row in rows[:3]})
    if sample_tags:
        return f"{intents_text} via {', '.join(sample_tags)}"
    return intents_text


def _session_matches_goal_intents(
    row: sqlite3.Row,
    intents: list[str],
    tag_intents: dict[str, set[str]],
) -> bool:
    if not intents:
        return False

    normalized_intents = {str(intent).strip().lower() for intent in intents if intent}
    if not normalized_intents:
        return False

    session_intents = _infer_session_intents(row, tag_intents)
    if any(intent in session_intents for intent in normalized_intents):
        return True

    fallback_text = " ".join(
        [
            str(row["tag_name"] or "").lower(),
            str(row["app_name"] or "").lower(),
            str(row["window_title"] or "").lower(),
            str(row["browser_domain"] or "").lower(),
        ]
    )

    for intent in normalized_intents:
        if intent in fallback_text:
            return True

    return False


def _infer_session_intents(
    row: sqlite3.Row,
    tag_intents: dict[str, set[str]],
) -> set[str]:
    session_intents: set[str] = set()

    tag_name = str(row["tag_name"] or "").strip().lower()
    if tag_name in tag_intents:
        session_intents.update(tag_intents[tag_name])

    text = " ".join(
        [
            str(row["tag_name"] or "").lower(),
            str(row["app_name"] or "").lower(),
            str(row["window_title"] or "").lower(),
            str(row["browser_domain"] or "").lower(),
        ]
    )

    if any(
        keyword in text
        for keyword in [
            "learning",
            "lesson",
            "course",
            "udemy",
            "certification",
            "study",
        ]
    ):
        session_intents.add("learning")

    if any(keyword in text for keyword in ["delivery", "client", "work", "project"]):
        session_intents.add("delivery_work")

    if any(
        keyword in text
        for keyword in [
            "meeting",
            "standup",
            "huddle",
            "teams",
            "zoom",
            "webex",
            "slack",
        ]
    ):
        session_intents.add("client_meeting")

    return session_intents


def _load_tag_intent_metadata() -> dict[str, set[str]]:
    config_dir = Path(__file__).parent.parent / "config"
    tag_intents: dict[str, set[str]] = {}

    for path in [config_dir / "tags.yaml", config_dir / "my-tags.yaml"]:
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                parsed = yaml.safe_load(handle) or {}
        except Exception:
            continue

        tags_config = parsed.get("tags") if isinstance(parsed, dict) else None
        if not isinstance(tags_config, dict):
            continue

        for tag_name, tag_config in tags_config.items():
            if not isinstance(tag_config, dict):
                continue
            for key in ("intent", "intents"):
                raw_values = tag_config.get(key)
                if not isinstance(raw_values, list):
                    if raw_values is not None:
                        raw_values = [raw_values]
                    else:
                        continue

                normalized_values = {
                    str(value).strip().lower()
                    for value in raw_values
                    if str(value).strip()
                }
                if normalized_values:
                    normalized_tag_name = str(tag_name).strip().lower()
                    tag_intents.setdefault(normalized_tag_name, set()).update(
                        normalized_values
                    )

    return tag_intents


def _load_goals(goals_path: str | Path) -> dict[str, object]:
    path = Path(goals_path)
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw) or {}

    goals_obj = parsed.get("goals") if isinstance(parsed, dict) else parsed
    if not isinstance(goals_obj, dict):
        return {}

    goals: dict[str, object] = {}
    for key, value in goals_obj.items():
        if value is None:
            continue

        if isinstance(value, dict):
            target = (
                value.get("target") if isinstance(value.get("target"), dict) else None
            )
            target_hours = None
            if target is not None:
                target_hours_value = target.get("hours")
                try:
                    target_hours = float(target_hours_value)
                except (TypeError, ValueError):
                    target_hours = None

            raw_activity_intent_ids = value.get("activity_intent_ids")
            intents: list[str] = []
            if isinstance(raw_activity_intent_ids, list):
                intents.extend(
                    str(item) for item in raw_activity_intent_ids if item is not None
                )

            goals[str(key)] = {
                "planned_pct": _coerce_number(value.get("planned_pct")),
                "target_hours": target_hours,
                "intents": intents,
            }
            continue

        try:
            goals[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    return goals


def _coerce_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return (
        Path("exports")
        / f"activity-export-{stamp}.{_file_extension_for_format(export_format)}"
    )


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


def _rows_to_named_seconds(
    conn: sqlite3.Connection, query: str, params: tuple
) -> list[dict[str, int | str]]:
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
