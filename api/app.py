"""FastAPI web server for WorkGraph dashboard."""

import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import NAMESPACE_DNS, uuid4, uuid5

import yaml
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator

from services.activity_tagger import ActivityTagger
from services.tag_review import (
    build_tag_review_suggestions,
    build_yaml_preview,
    load_custom_tag_rules,
)

app = FastAPI(title="WorkGraph Dashboard")
sync_logger = logging.getLogger("workgraph.sync")

WORK_EVENT_TYPES = [
    "Achievement",
    "Incident",
    "Decision",
    "Risk",
    "Blocker",
    "Promotion",
    "Interview",
    "Offer",
    "Resignation",
    "Release",
    "Production Outage",
]
WORK_EVENT_IMPACTS = ["Low", "Medium", "High", "Critical"]
DASHBOARD_MODES = {"standalone", "sync-client", "sync-server"}
DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
PERSONAL_SETTINGS_PATH = Path("config/my-settings.yaml")

# Setup Jinja2
template_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

jinja_env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(["html", "xml"]),
)


def _human_datetime(value: object) -> str:
    if value is None:
        return "-"

    if isinstance(value, datetime):
        dt = value.astimezone() if value.tzinfo is not None else value
        return dt.strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ")

    if isinstance(value, date):
        return value.strftime("%b %d, %Y").replace(" 0", " ")

    text = str(value).strip()
    if not text:
        return "-"

    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return text
        return parsed_date.strftime("%b %d, %Y").replace(" 0", " ")

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    dt = parsed.astimezone() if parsed.tzinfo is not None else parsed
    return dt.strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ")


jinja_env.filters["human_datetime"] = _human_datetime

# Mount static files
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Serve the dashboard favicon from the static assets directory."""
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


def get_db_path() -> Path:
    """Get the database path from environment or default."""
    return Path("activity.db")


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _ensure_aux_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            title TEXT,
            notes TEXT,
            metadata TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_journal_entries_start_time
            ON journal_entries (start_time);

        CREATE INDEX IF NOT EXISTS idx_journal_entries_end_time
            ON journal_entries (end_time);

        CREATE TABLE IF NOT EXISTS daily_reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            wins TEXT,
            problems TEXT,
            tomorrow TEXT,
            energy INTEGER,
            stress INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_daily_reflections_date
            ON daily_reflections (date);

        CREATE TABLE IF NOT EXISTS work_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT,
            user_id TEXT,
            device_id TEXT,
            created_at TEXT NOT NULL,
            event_time TEXT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            impact TEXT,
            project TEXT,
            notes TEXT,
            metadata TEXT,
            updated_at TEXT,
            deleted_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_work_events_uuid
            ON work_events (uuid);

        CREATE INDEX IF NOT EXISTS idx_work_events_event_time
            ON work_events (event_time);

        CREATE INDEX IF NOT EXISTS idx_work_events_type
            ON work_events (event_type);

        CREATE INDEX IF NOT EXISTS idx_work_events_impact
            ON work_events (impact);
        """
    )
    _backfill_work_events_metadata(conn)
    conn.commit()


def _default_user_id() -> str:
    return str(uuid5(NAMESPACE_DNS, "workgraph.local.user"))


def _default_device_id() -> str:
    return str(uuid5(NAMESPACE_DNS, f"workgraph.local.device.{os.uname().nodename}"))


def _work_event_sync_identity(conn: sqlite3.Connection) -> tuple[str, str]:
    if _sqlite_table_exists(conn, "sync_state"):
        row = conn.execute(
            "SELECT user_id, device_id FROM sync_state WHERE id = 1"
        ).fetchone()
        if row is not None and row["user_id"] and row["device_id"]:
            return str(row["user_id"]), str(row["device_id"])
    return _default_user_id(), _default_device_id()


def _backfill_work_events_metadata(conn: sqlite3.Connection) -> None:
    if not _sqlite_table_exists(conn, "work_events"):
        return

    user_id, device_id = _work_event_sync_identity(conn)
    rows = conn.execute(
        """
        SELECT id, created_at, event_time, updated_at, uuid, user_id, device_id
        FROM work_events
        ORDER BY id ASC
        """
    ).fetchall()
    for row in rows:
        row_uuid = str(
            row["uuid"] or uuid5(NAMESPACE_DNS, f"workgraph.work-event.{row['id']}")
        )
        row_updated_at = str(row["updated_at"] or row["created_at"] or _now_iso())
        conn.execute(
            """
            UPDATE work_events
            SET uuid = ?,
                user_id = COALESCE(user_id, ?),
                device_id = COALESCE(device_id, ?),
                updated_at = COALESCE(updated_at, ?)
            WHERE id = ?
            """,
            (row_uuid, user_id, device_id, row_updated_at, row["id"]),
        )


def _ensure_sync_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_devices (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            hostname TEXT,
            category TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_tokens (
            token TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_batches (
            batch_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_checkpoints (
            device_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            last_push_cursor TEXT,
            last_pull_cursor TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_ingest_sequence (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_sessions (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            utc_start TEXT,
            utc_end TEXT,
            timezone_name TEXT,
            active_seconds INTEGER,
            application_name TEXT,
            tag TEXT,
            repo_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ingest_seq INTEGER,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_journal_entries (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ingest_seq INTEGER,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_daily_reflections (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ingest_seq INTEGER,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_work_events (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ingest_seq INTEGER,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_updated
            ON sync_sessions (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_journal_user_updated
            ON sync_journal_entries (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_reflections_user_updated
            ON sync_daily_reflections (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_work_events_user_updated
            ON sync_work_events (user_id, updated_at, uuid);

        CREATE TABLE IF NOT EXISTS sync_request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            device_id TEXT,
            user_id TEXT,
            batch_id TEXT,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            conflict_count INTEGER NOT NULL DEFAULT 0,
            has_more INTEGER,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            error_type TEXT NOT NULL,
            message TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            status_code INTEGER,
            device_id TEXT,
            user_id TEXT,
            batch_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_endpoint_created
            ON sync_request_logs (endpoint, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_device_created
            ON sync_request_logs (device_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_created
            ON sync_error_logs (created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_status
            ON sync_error_logs (status, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_endpoint
            ON sync_error_logs (endpoint, created_at);

        CREATE TABLE IF NOT EXISTS sync_metrics_daily (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            day_utc TEXT NOT NULL,
            active_seconds INTEGER NOT NULL DEFAULT 0,
            focus_seconds INTEGER NOT NULL DEFAULT 0,
            meeting_seconds INTEGER NOT NULL DEFAULT 0,
            context_switches INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(user_id, device_id, day_utc)
        );

        CREATE INDEX IF NOT EXISTS idx_sync_metrics_daily_user_day
            ON sync_metrics_daily (user_id, day_utc);
        """
    )

    for stmt in [
        "ALTER TABLE sync_sessions ADD COLUMN utc_start TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN utc_end TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN timezone_name TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN active_seconds INTEGER",
        "ALTER TABLE sync_sessions ADD COLUMN application_name TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN tag TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN repo_name TEXT",
        "ALTER TABLE sync_sessions ADD COLUMN ingest_seq INTEGER",
        "ALTER TABLE sync_journal_entries ADD COLUMN ingest_seq INTEGER",
        "ALTER TABLE sync_daily_reflections ADD COLUMN ingest_seq INTEGER",
        "ALTER TABLE sync_work_events ADD COLUMN ingest_seq INTEGER",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass

    _migrate_sync_tables_to_user_scoped_keys(conn)
    _backfill_sync_ingest_sequences(conn)

    _backfill_sync_session_columns(conn)
    _ensure_sync_runtime_indexes(conn)
    _ensure_sync_unique_indexes(conn)
    conn.commit()


def _to_utc_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value

    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)

    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _table_has_uuid_primary_key(conn: sqlite3.Connection, table_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    for row in rows:
        if row["name"] == "uuid" and int(row["pk"] or 0) > 0:
            return True
    return False


def _migrate_sync_tables_to_user_scoped_keys(conn: sqlite3.Connection) -> None:
    if _table_has_uuid_primary_key(conn, "sync_sessions"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_sessions_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                utc_start TEXT,
                utc_end TEXT,
                timezone_name TEXT,
                active_seconds INTEGER,
                application_name TEXT,
                tag TEXT,
                repo_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ingest_seq INTEGER,
                deleted_at TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(user_id, uuid)
            );

            INSERT INTO sync_sessions_v2 (
                uuid,
                user_id,
                device_id,
                utc_start,
                utc_end,
                timezone_name,
                active_seconds,
                application_name,
                tag,
                repo_name,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            SELECT
                uuid,
                user_id,
                device_id,
                utc_start,
                utc_end,
                timezone_name,
                active_seconds,
                application_name,
                tag,
                repo_name,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            FROM sync_sessions;

            DROP TABLE sync_sessions;
            ALTER TABLE sync_sessions_v2 RENAME TO sync_sessions;
            """
        )

    if _table_has_uuid_primary_key(conn, "sync_journal_entries"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_journal_entries_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ingest_seq INTEGER,
                deleted_at TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(user_id, uuid)
            );

            INSERT INTO sync_journal_entries_v2 (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            SELECT
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            FROM sync_journal_entries;

            DROP TABLE sync_journal_entries;
            ALTER TABLE sync_journal_entries_v2 RENAME TO sync_journal_entries;
            """
        )

    if _table_has_uuid_primary_key(conn, "sync_daily_reflections"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_daily_reflections_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ingest_seq INTEGER,
                deleted_at TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(user_id, uuid)
            );

            INSERT INTO sync_daily_reflections_v2 (
                uuid,
                user_id,
                device_id,
                date,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            SELECT
                uuid,
                user_id,
                device_id,
                date,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            FROM sync_daily_reflections;

            DROP TABLE sync_daily_reflections;
            ALTER TABLE sync_daily_reflections_v2 RENAME TO sync_daily_reflections;
            """
        )

    if _table_has_uuid_primary_key(conn, "sync_work_events"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_work_events_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ingest_seq INTEGER,
                deleted_at TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(user_id, uuid)
            );

            INSERT INTO sync_work_events_v2 (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            SELECT
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            FROM sync_work_events;

            DROP TABLE sync_work_events;
            ALTER TABLE sync_work_events_v2 RENAME TO sync_work_events;
            """
        )


def _ensure_sync_unique_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_sessions_user_uuid
            ON sync_sessions (user_id, uuid);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_journal_user_uuid
            ON sync_journal_entries (user_id, uuid);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_reflections_user_uuid
            ON sync_daily_reflections (user_id, uuid);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_work_events_user_uuid
            ON sync_work_events (user_id, uuid);
        """
    )


def _ensure_sync_runtime_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_updated
            ON sync_sessions (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_ingest
            ON sync_sessions (user_id, ingest_seq, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_utc_start
            ON sync_sessions (user_id, utc_start);

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_device_utc_start
            ON sync_sessions (user_id, device_id, utc_start);

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_tag_utc_start
            ON sync_sessions (user_id, tag, utc_start);

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_app_utc_start
            ON sync_sessions (user_id, application_name, utc_start);

        CREATE INDEX IF NOT EXISTS idx_sync_journal_user_updated
            ON sync_journal_entries (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_journal_user_ingest
            ON sync_journal_entries (user_id, ingest_seq, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_reflections_user_updated
            ON sync_daily_reflections (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_reflections_user_ingest
            ON sync_daily_reflections (user_id, ingest_seq, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_work_events_user_ingest
            ON sync_work_events (user_id, ingest_seq, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_endpoint_created
            ON sync_request_logs (endpoint, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_device_created
            ON sync_request_logs (device_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_created
            ON sync_error_logs (created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_status
            ON sync_error_logs (status, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_error_logs_endpoint
            ON sync_error_logs (endpoint, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_metrics_daily_user_day
            ON sync_metrics_daily (user_id, day_utc);
        """
    )


def _next_sync_ingest_seq(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_ingest_sequence (created_at) VALUES (?)",
        (_now_iso(),),
    )
    return int(cursor.lastrowid)


def _backfill_sync_ingest_sequences(conn: sqlite3.Connection) -> None:
    mappings = {
        "sessions": "sync_sessions",
        "journal_entries": "sync_journal_entries",
        "daily_reflections": "sync_daily_reflections",
        "work_events": "sync_work_events",
    }
    rows = conn.execute(
        """
        SELECT entity, row_id
        FROM (
            SELECT 'sessions' AS entity, id AS row_id, COALESCE(updated_at, created_at) AS sort_ts
            FROM sync_sessions
            WHERE COALESCE(ingest_seq, 0) <= 0
            UNION ALL
            SELECT 'journal_entries' AS entity, id AS row_id, COALESCE(updated_at, created_at) AS sort_ts
            FROM sync_journal_entries
            WHERE COALESCE(ingest_seq, 0) <= 0
            UNION ALL
            SELECT 'daily_reflections' AS entity, id AS row_id, COALESCE(updated_at, created_at) AS sort_ts
            FROM sync_daily_reflections
            WHERE COALESCE(ingest_seq, 0) <= 0
            UNION ALL
            SELECT 'work_events' AS entity, id AS row_id, COALESCE(updated_at, created_at) AS sort_ts
            FROM sync_work_events
            WHERE COALESCE(ingest_seq, 0) <= 0
        )
        ORDER BY sort_ts ASC, entity ASC, row_id ASC
        """
    ).fetchall()

    for row in rows:
        table_name = mappings[str(row["entity"])]
        conn.execute(
            f"UPDATE {table_name} SET ingest_seq = ? WHERE id = ?",
            (_next_sync_ingest_seq(conn), int(row["row_id"])),
        )


def _rebuild_sync_rollups(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    device_id: str | None = None,
    start_day: str | None = None,
) -> int:
    query = """
        SELECT DISTINCT device_id, substr(utc_start, 1, 10) AS day_utc
        FROM sync_sessions
        WHERE user_id = ?
          AND deleted_at IS NULL
          AND utc_start IS NOT NULL
    """
    params: list[str] = [user_id]
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if start_day:
        query += " AND substr(utc_start, 1, 10) >= ?"
        params.append(start_day)

    rows = conn.execute(query, params).fetchall()
    for row in rows:
        _recompute_sync_daily_rollup(
            conn,
            user_id=user_id,
            device_id=str(row["device_id"]),
            day_utc=str(row["day_utc"]),
        )
    return len(rows)


def _parse_datetime_safe(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _extract_sync_session_fields(payload: dict) -> dict[str, object | None]:
    start_raw = payload.get("start_time") or payload.get("utc_start")
    end_raw = payload.get("end_time") or payload.get("utc_end")

    start_dt = _parse_datetime_safe(str(start_raw)) if start_raw is not None else None
    end_dt = _parse_datetime_safe(str(end_raw)) if end_raw is not None else None

    timezone_name = None
    if payload.get("timezone_name"):
        timezone_name = str(payload.get("timezone_name"))
    elif isinstance(start_raw, str) and (
        "+" in start_raw[10:] or start_raw.endswith("Z")
    ):
        timezone_name = "offset"

    duration_val = payload.get("duration_sec")
    if duration_val is None:
        duration_val = payload.get("active_seconds")
    try:
        active_seconds = int(duration_val) if duration_val is not None else None
    except (TypeError, ValueError):
        active_seconds = None

    return {
        "utc_start": start_dt.astimezone(UTC).isoformat(timespec="seconds")
        if start_dt
        else None,
        "utc_end": end_dt.astimezone(UTC).isoformat(timespec="seconds")
        if end_dt
        else None,
        "timezone_name": timezone_name,
        "active_seconds": active_seconds,
        "application_name": payload.get("app_name") or payload.get("application_name"),
        "tag": payload.get("tag"),
        "repo_name": payload.get("git_repo") or payload.get("repo_name"),
    }


def _recompute_sync_daily_rollup(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    device_id: str,
    day_utc: str,
) -> None:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS row_count,
            COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS active_seconds,
            COALESCE(SUM(COALESCE(CAST(json_extract(payload_json, '$.focus_seconds') AS INTEGER), 0)), 0) AS focus_seconds,
            COALESCE(
                SUM(
                    CASE
                        WHEN CAST(json_extract(payload_json, '$.meeting_seconds') AS INTEGER) IS NOT NULL
                            THEN COALESCE(CAST(json_extract(payload_json, '$.meeting_seconds') AS INTEGER), 0)
                        WHEN (
                            LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%teams%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%zoom%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%webex%'
                            OR LOWER(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '')) LIKE '%slack%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%meet.google.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%teams.microsoft.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%zoom.us%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '')) LIKE '%webex.com%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%meeting%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%standup%'
                            OR LOWER(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '')) LIKE '%huddle%'
                        ) THEN COALESCE(active_seconds, 0)
                        ELSE 0
                    END
                ),
                0
            ) AS meeting_seconds,
            COALESCE(SUM(COALESCE(CAST(json_extract(payload_json, '$.context_switches') AS INTEGER), 0)), 0) AS context_switches_payload
        FROM sync_sessions
        WHERE user_id = ?
          AND device_id = ?
          AND deleted_at IS NULL
          AND utc_start IS NOT NULL
          AND substr(utc_start, 1, 10) = ?
        """,
        (user_id, device_id, day_utc),
    ).fetchone()

    if row is None or int(row["row_count"]) == 0:
        conn.execute(
            "DELETE FROM sync_metrics_daily WHERE user_id = ? AND device_id = ? AND day_utc = ?",
            (user_id, device_id, day_utc),
        )
        return

    derived_switches_row = conn.execute(
        """
        WITH ordered AS (
            SELECT
                utc_start,
                COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), '') AS app_name,
                COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), '') AS window_title,
                COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), '') AS browser_domain,
                LAG(COALESCE(application_name, CAST(json_extract(payload_json, '$.app_name') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_app_name,
                LAG(COALESCE(CAST(json_extract(payload_json, '$.window_title') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_window_title,
                LAG(COALESCE(CAST(json_extract(payload_json, '$.browser_domain') AS TEXT), ''))
                    OVER (ORDER BY utc_start, uuid) AS prev_browser_domain
            FROM sync_sessions
            WHERE user_id = ?
              AND device_id = ?
              AND deleted_at IS NULL
              AND utc_start IS NOT NULL
              AND substr(utc_start, 1, 10) = ?
        )
        SELECT COUNT(*) AS switch_count
        FROM ordered
        WHERE prev_app_name IS NOT NULL
          AND (
              COALESCE(app_name, '') != COALESCE(prev_app_name, '')
              OR COALESCE(window_title, '') != COALESCE(prev_window_title, '')
              OR COALESCE(browser_domain, '') != COALESCE(prev_browser_domain, '')
          )
        """,
        (user_id, device_id, day_utc),
    ).fetchone()

    derived_switches = int(
        (derived_switches_row["switch_count"] if derived_switches_row else 0) or 0
    )
    payload_switches = int(row["context_switches_payload"] or 0)
    context_switches = max(payload_switches, derived_switches)

    conn.execute(
        """
        INSERT INTO sync_metrics_daily (
            user_id,
            device_id,
            day_utc,
            active_seconds,
            focus_seconds,
            meeting_seconds,
            context_switches,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, device_id, day_utc) DO UPDATE SET
            active_seconds = excluded.active_seconds,
            focus_seconds = excluded.focus_seconds,
            meeting_seconds = excluded.meeting_seconds,
            context_switches = excluded.context_switches,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            user_id,
            device_id,
            day_utc,
            int(row["active_seconds"]),
            int(row["focus_seconds"]),
            int(row["meeting_seconds"]),
            context_switches,
            _now_iso(),
        ),
    )


def _backfill_sync_session_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT user_id, uuid, payload_json
        FROM sync_sessions
        WHERE utc_start IS NULL OR active_seconds IS NULL OR application_name IS NULL OR repo_name IS NULL
        LIMIT 5000
        """
    ).fetchall()

    for row in rows:
        payload_json = row["payload_json"]
        if not payload_json:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue

        fields = _extract_sync_session_fields(payload)
        conn.execute(
            """
            UPDATE sync_sessions
            SET utc_start = COALESCE(utc_start, ?),
                utc_end = COALESCE(utc_end, ?),
                timezone_name = COALESCE(timezone_name, ?),
                active_seconds = COALESCE(active_seconds, ?),
                application_name = COALESCE(application_name, ?),
                tag = COALESCE(tag, ?),
                repo_name = COALESCE(repo_name, ?)
            WHERE user_id = ? AND uuid = ?
            """,
            (
                fields["utc_start"],
                fields["utc_end"],
                fields["timezone_name"],
                fields["active_seconds"],
                fields["application_name"],
                fields["tag"],
                fields["repo_name"],
                row["user_id"],
                row["uuid"],
            ),
        )


class JournalCreate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    title: str = Field(min_length=1, max_length=200)
    notes: str = ""
    metadata: dict | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("title cannot be blank")
        return trimmed


class ReflectionUpsert(BaseModel):
    wins: str | None = None
    problems: str | None = None
    tomorrow: str | None = None
    energy: int | None = Field(default=None, ge=1, le=10)
    stress: int | None = Field(default=None, ge=1, le=10)


class WorkEventCreate(BaseModel):
    event_time: str | None = None
    event_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    impact: str | None = Field(default=None, max_length=32)
    project: str | None = Field(default=None, max_length=200)
    notes: str = ""
    metadata: dict | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        trimmed = value.strip()
        if trimmed not in WORK_EVENT_TYPES:
            raise ValueError(
                f"event_type must be one of: {', '.join(WORK_EVENT_TYPES)}"
            )
        return trimmed

    @field_validator("impact")
    @classmethod
    def validate_impact(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        trimmed = value.strip()
        if trimmed not in WORK_EVENT_IMPACTS:
            raise ValueError(f"impact must be one of: {', '.join(WORK_EVENT_IMPACTS)}")
        return trimmed

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("title cannot be blank")
        return trimmed


class SyncRegisterUser(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)


class SyncRegisterDevice(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=64)
    hostname: str | None = None
    category: str | None = None


class SyncRegisterRequest(BaseModel):
    user: SyncRegisterUser
    device: SyncRegisterDevice


class DeviceRegisterRequest(BaseModel):
    mode: str = Field(min_length=1, max_length=32)
    user: SyncRegisterUser
    device: SyncRegisterDevice
    sync_base_url: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in DASHBOARD_MODES:
            raise ValueError(
                "mode must be one of: standalone, sync-client, sync-server"
            )
        return normalized


class SyncPushRequest(BaseModel):
    device_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    client_cursor: str | None = None
    batch_id: str = Field(min_length=1)
    changes: dict[str, list[dict]] = Field(default_factory=dict)


class SyncPullRequest(BaseModel):
    device_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    cursor: str | None = None
    limit: int = Field(default=1000, ge=1, le=5000)


class TagReviewSuggestionRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    selected_tag: str | None = Field(default=None, max_length=128)
    min_domain_hits: int = Field(default=2, ge=1, le=100)
    min_repo_hits: int = Field(default=2, ge=1, le=100)


class TagReviewYamlRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    selected_tag: str | None = Field(default=None, max_length=128)
    min_domain_hits: int = Field(default=2, ge=1, le=100)
    min_repo_hits: int = Field(default=2, ge=1, le=100)


class TagReviewGroupAssignRequest(BaseModel):
    group_type: str = Field(min_length=1, max_length=32)
    group_value: str = Field(min_length=1, max_length=500)
    selected_tag: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=500)
    source_signal: str | None = Field(default=None, max_length=64)
    days: int = Field(default=7, ge=1, le=3650)
    only_untagged: bool = True

    @field_validator("group_type")
    @classmethod
    def validate_group_type(cls, value: str) -> str:
        trimmed = value.strip().lower()
        if trimmed not in {"repo", "domain", "browser_context", "app"}:
            raise ValueError(
                "group_type must be one of: repo, domain, browser_context, app"
            )
        return trimmed

    @field_validator("group_value", "selected_tag")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("value cannot be blank")
        return trimmed


def _decode_metadata(raw_value: str | None) -> dict | None:
    if raw_value is None:
        return None
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def _serialize_journal_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["metadata"] = _decode_metadata(item.get("metadata"))
    return item


def _serialize_work_event_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["metadata"] = _decode_metadata(item.get("metadata"))
    return item


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _query_overlapping_activity_sessions(
    conn: sqlite3.Connection,
    *,
    start_time: str,
    end_time: str,
    limit: int,
) -> list[dict]:
    # Fresh databases may not have collector sessions yet.
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM activity_sessions
            WHERE start_time < ?
              AND end_time > ?
            ORDER BY start_time ASC
            LIMIT ?
            """,
            (end_time, start_time, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [dict(row) for row in rows]


def _build_correlation_summary(sessions: list[dict]) -> dict:
    active_sessions = [s for s in sessions if not bool(s.get("is_idle"))]
    total_active_seconds = sum(int(s.get("duration_sec") or 0) for s in active_sessions)
    total_switches = sum(int(s.get("context_switches") or 0) for s in active_sessions)
    apps = sorted(
        {str(s.get("app_name")) for s in active_sessions if s.get("app_name")}
    )
    repos = sorted(
        {str(s.get("git_repo")) for s in active_sessions if s.get("git_repo")}
    )
    return {
        "session_count": len(sessions),
        "active_session_count": len(active_sessions),
        "total_active_seconds": total_active_seconds,
        "total_active_hours": round(total_active_seconds / 3600, 2),
        "total_context_switches": total_switches,
        "apps": apps,
        "repos": repos,
    }


def _log_sync_request(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    status_code: int,
    latency_ms: int,
    device_id: str | None,
    user_id: str | None,
    batch_id: str | None = None,
    accepted_count: int = 0,
    conflict_count: int = 0,
    has_more: bool | None = None,
) -> None:
    now_iso = _now_iso()
    conn.execute(
        """
        INSERT INTO sync_request_logs (
            endpoint,
            status_code,
            device_id,
            user_id,
            batch_id,
            accepted_count,
            conflict_count,
            has_more,
            latency_ms,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            endpoint,
            status_code,
            device_id,
            user_id,
            batch_id,
            accepted_count,
            conflict_count,
            None if has_more is None else int(has_more),
            latency_ms,
            now_iso,
        ),
    )
    sync_logger.info(
        json.dumps(
            {
                "kind": "sync_request",
                "endpoint": endpoint,
                "status_code": status_code,
                "device_id": device_id,
                "user_id": user_id,
                "batch_id": batch_id,
                "accepted_count": accepted_count,
                "conflict_count": conflict_count,
                "has_more": has_more,
                "latency_ms": latency_ms,
                "created_at": now_iso,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )


def _log_sync_error(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    error_type: str,
    message: str,
    status_code: int | None,
    device_id: str | None,
    user_id: str | None,
    batch_id: str | None = None,
    retry_count: int = 0,
    status: str = "open",
) -> None:
    now_iso = _now_iso()
    normalized_message = (
        message or "unknown sync error"
    ).strip() or "unknown sync error"
    conn.execute(
        """
        INSERT INTO sync_error_logs (
            endpoint,
            error_type,
            message,
            retry_count,
            status,
            status_code,
            device_id,
            user_id,
            batch_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            endpoint,
            error_type,
            normalized_message,
            max(0, int(retry_count)),
            status,
            status_code,
            device_id,
            user_id,
            batch_id,
            now_iso,
            now_iso,
        ),
    )


def _request_hash(payload: dict) -> str:
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    if "|" not in cursor:
        return cursor, ""
    updated_at, row_uuid = cursor.split("|", 1)
    return updated_at or None, row_uuid or ""


def _parse_ingest_seq_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = str(cursor).strip()
    if not raw:
        return None
    if raw.startswith("seq:"):
        raw = raw[4:]
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value >= 0 else None


def _build_ingest_seq_cursor(ingest_seq: int | None) -> str | None:
    if ingest_seq is None or ingest_seq < 0:
        return None
    return f"seq:{ingest_seq}"


def _build_cursor(updated_at: str | None, row_uuid: str | None) -> str | None:
    if not updated_at or not row_uuid:
        return None
    return f"{updated_at}|{row_uuid}"


def _is_incoming_newer(
    *,
    incoming_updated_at: str,
    incoming_uuid: str,
    existing_updated_at: str,
    existing_uuid: str,
) -> bool:
    if incoming_updated_at > existing_updated_at:
        return True
    if incoming_updated_at < existing_updated_at:
        return False
    return incoming_uuid >= existing_uuid


def _auth_token_from_header(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=401, detail="Authorization must be Bearer token"
        )
    token = authorization[len(prefix) :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty Bearer token")
    return token


def _require_sync_auth(
    conn: sqlite3.Connection,
    *,
    authorization: str | None,
    device_id: str,
    user_id: str,
) -> None:
    token = _auth_token_from_header(authorization)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT device_id, user_id
        FROM sync_tokens
        WHERE token = ? AND revoked_at IS NULL
        """,
        (token,),
    )
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if row["device_id"] != device_id or row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Token does not match device/user")


def _upsert_sync_payload_row(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    user_id: str,
    device_id: str,
    payload: dict,
) -> tuple[str | None, str | None]:
    row_uuid = str(payload.get("uuid") or payload.get("id") or "").strip()
    if not row_uuid:
        return None, None

    updated_at = str(
        payload.get("updated_at") or payload.get("created_at") or _now_iso()
    )
    created_at = str(payload.get("created_at") or updated_at)
    deleted_at = payload.get("deleted_at")
    date_value = payload.get("date") if table_name == "sync_daily_reflections" else None
    is_session_table = table_name == "sync_sessions"
    is_work_event_table = table_name == "sync_work_events"
    extracted_fields: dict[str, object | None] = (
        _extract_sync_session_fields(payload) if is_session_table else {}
    )

    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM {table_name} WHERE user_id = ? AND uuid = ?",
        (user_id, row_uuid),
    )
    existing = cursor.fetchone()

    if existing is not None:
        if not _is_incoming_newer(
            incoming_updated_at=updated_at,
            incoming_uuid=row_uuid,
            existing_updated_at=str(existing["updated_at"]),
            existing_uuid=str(existing["uuid"]),
        ):
            return str(existing["updated_at"]), str(existing["uuid"])

        if table_name == "sync_daily_reflections":
            ingest_seq = _next_sync_ingest_seq(conn)
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET user_id = ?,
                    device_id = ?,
                    created_at = ?,
                    updated_at = ?,
                    ingest_seq = ?,
                    deleted_at = ?,
                    payload_json = ?,
                    date = COALESCE(?, date)
                WHERE user_id = ? AND uuid = ?
                """,
                (
                    user_id,
                    device_id,
                    created_at,
                    updated_at,
                    ingest_seq,
                    deleted_at,
                    json.dumps(payload, separators=(",", ":")),
                    date_value,
                    user_id,
                    row_uuid,
                ),
            )
        elif is_work_event_table:
            ingest_seq = _next_sync_ingest_seq(conn)
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET user_id = ?,
                    device_id = ?,
                    created_at = ?,
                    updated_at = ?,
                    ingest_seq = ?,
                    deleted_at = ?,
                    payload_json = ?
                WHERE user_id = ? AND uuid = ?
                """,
                (
                    user_id,
                    device_id,
                    created_at,
                    updated_at,
                    ingest_seq,
                    deleted_at,
                    json.dumps(payload, separators=(",", ":")),
                    user_id,
                    row_uuid,
                ),
            )
        elif is_session_table:
            ingest_seq = _next_sync_ingest_seq(conn)
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET user_id = ?,
                    device_id = ?,
                    created_at = ?,
                    updated_at = ?,
                    ingest_seq = ?,
                    deleted_at = ?,
                    payload_json = ?,
                    utc_start = ?,
                    utc_end = ?,
                    timezone_name = ?,
                    active_seconds = ?,
                    application_name = ?,
                    tag = ?,
                    repo_name = ?
                WHERE user_id = ? AND uuid = ?
                """,
                (
                    user_id,
                    device_id,
                    created_at,
                    updated_at,
                    ingest_seq,
                    deleted_at,
                    json.dumps(payload, separators=(",", ":")),
                    extracted_fields.get("utc_start"),
                    extracted_fields.get("utc_end"),
                    extracted_fields.get("timezone_name"),
                    extracted_fields.get("active_seconds"),
                    extracted_fields.get("application_name"),
                    extracted_fields.get("tag"),
                    extracted_fields.get("repo_name"),
                    user_id,
                    row_uuid,
                ),
            )
        else:
            ingest_seq = _next_sync_ingest_seq(conn)
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET user_id = ?,
                    device_id = ?,
                    created_at = ?,
                    updated_at = ?,
                    ingest_seq = ?,
                    deleted_at = ?,
                    payload_json = ?
                WHERE user_id = ? AND uuid = ?
                """,
                (
                    user_id,
                    device_id,
                    created_at,
                    updated_at,
                    ingest_seq,
                    deleted_at,
                    json.dumps(payload, separators=(",", ":")),
                    user_id,
                    row_uuid,
                ),
            )

        if is_session_table:
            day_candidates: set[str] = set()
            if "utc_start" in existing.keys() and existing["utc_start"]:
                day_candidates.add(str(existing["utc_start"])[:10])
            if extracted_fields.get("utc_start"):
                day_candidates.add(str(extracted_fields["utc_start"])[:10])
            for day_utc in day_candidates:
                _recompute_sync_daily_rollup(
                    conn, user_id=user_id, device_id=device_id, day_utc=day_utc
                )

        return updated_at, row_uuid

    if table_name == "sync_daily_reflections":
        ingest_seq = _next_sync_ingest_seq(conn)
        cursor.execute(
            f"""
            INSERT INTO {table_name} (
                uuid,
                user_id,
                device_id,
                date,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_uuid,
                user_id,
                device_id,
                date_value,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
    elif is_work_event_table:
        ingest_seq = _next_sync_ingest_seq(conn)
        cursor.execute(
            f"""
            INSERT INTO {table_name} (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
    elif is_session_table:
        ingest_seq = _next_sync_ingest_seq(conn)
        cursor.execute(
            f"""
            INSERT INTO {table_name} (
                uuid,
                user_id,
                device_id,
                utc_start,
                utc_end,
                timezone_name,
                active_seconds,
                application_name,
                tag,
                repo_name,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_uuid,
                user_id,
                device_id,
                extracted_fields.get("utc_start"),
                extracted_fields.get("utc_end"),
                extracted_fields.get("timezone_name"),
                extracted_fields.get("active_seconds"),
                extracted_fields.get("application_name"),
                extracted_fields.get("tag"),
                extracted_fields.get("repo_name"),
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
    else:
        ingest_seq = _next_sync_ingest_seq(conn)
        cursor.execute(
            f"""
            INSERT INTO {table_name} (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                ingest_seq,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
            ),
        )

    if is_session_table and extracted_fields.get("utc_start"):
        _recompute_sync_daily_rollup(
            conn,
            user_id=user_id,
            device_id=device_id,
            day_utc=str(extracted_fields["utc_start"])[:10],
        )

    return updated_at, row_uuid


def _list_sync_changes(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    cursor_value: str | None,
    limit: int,
) -> list[dict]:
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = f"""
        SELECT payload_json, updated_at, uuid
        FROM {table_name}
        WHERE 1=1
    """
    params: list[str | int] = []
    if updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND uuid > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

    query += " ORDER BY updated_at ASC, uuid ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows if row["payload_json"]]


def _list_sync_tombstones(
    conn: sqlite3.Connection,
    *,
    cursor_value: str | None,
    limit: int,
) -> list[dict]:
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    mappings = [
        ("sync_sessions", "sessions"),
        ("sync_journal_entries", "journal_entries"),
        ("sync_daily_reflections", "daily_reflections"),
        ("sync_work_events", "work_events"),
    ]
    tombstones: list[dict] = []
    for table_name, entity_name in mappings:
        query = f"""
            SELECT uuid, deleted_at, updated_at
            FROM {table_name}
            WHERE deleted_at IS NOT NULL
        """
        params: list[str | int] = []
        if updated_at_cursor is not None:
            query += """
              AND (
                    updated_at > ?
                    OR (updated_at = ? AND uuid > ?)
                  )
            """
            params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])
        query += " ORDER BY updated_at ASC, uuid ASC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        for row in rows:
            tombstones.append(
                {
                    "entity": entity_name,
                    "id": row["uuid"],
                    "deleted_at": row["deleted_at"],
                    "updated_at": row["updated_at"],
                }
            )
    tombstones.sort(key=lambda x: (x.get("updated_at") or "", x.get("id") or ""))
    return tombstones[:limit]


def _max_cursor_from_changes(changes: list[dict]) -> str | None:
    candidates: list[tuple[str, str]] = []
    for row in changes:
        updated_at = row.get("updated_at") or row.get("created_at")
        row_uuid = row.get("uuid") or row.get("id")
        if not updated_at or not row_uuid:
            continue
        candidates.append((str(updated_at), str(row_uuid)))
    if not candidates:
        return None
    top = max(candidates)
    return f"{top[0]}|{top[1]}"


def _list_sync_union_rows(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    cursor_value: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    ingest_seq_cursor = _parse_ingest_seq_cursor(cursor_value)
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = """
        SELECT entity, id, updated_at, ingest_seq, deleted_at, payload_json
        FROM (
            SELECT 'sessions' AS entity, uuid AS id, user_id, updated_at, ingest_seq, deleted_at, payload_json
            FROM sync_sessions
            UNION ALL
            SELECT 'journal_entries' AS entity, uuid AS id, user_id, updated_at, ingest_seq, deleted_at, payload_json
            FROM sync_journal_entries
            UNION ALL
            SELECT 'daily_reflections' AS entity, uuid AS id, user_id, updated_at, ingest_seq, deleted_at, payload_json
            FROM sync_daily_reflections
            UNION ALL
            SELECT 'work_events' AS entity, uuid AS id, user_id, updated_at, ingest_seq, deleted_at, payload_json
            FROM sync_work_events
        )
        WHERE user_id = ?
    """
    params: list[str | int] = [user_id]
    if ingest_seq_cursor is not None:
        query += " AND COALESCE(ingest_seq, 0) > ?"
        params.append(ingest_seq_cursor)
        query += " ORDER BY COALESCE(ingest_seq, 0) ASC, id ASC LIMIT ?"
    elif updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND id > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])
        query += " ORDER BY updated_at ASC, id ASC LIMIT ?"
    else:
        query += " ORDER BY COALESCE(ingest_seq, 0) ASC, id ASC LIMIT ?"
    params.append(limit)
    return list(conn.execute(query, params).fetchall())


def _count_sync_union_rows_after_cursor(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    cursor_value: str | None,
) -> int:
    ingest_seq_cursor = _parse_ingest_seq_cursor(cursor_value)
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = """
        SELECT COUNT(*) AS total_rows
        FROM (
            SELECT uuid AS id, user_id, updated_at, ingest_seq FROM sync_sessions
            UNION ALL
            SELECT uuid AS id, user_id, updated_at, ingest_seq FROM sync_journal_entries
            UNION ALL
            SELECT uuid AS id, user_id, updated_at, ingest_seq FROM sync_daily_reflections
            UNION ALL
            SELECT uuid AS id, user_id, updated_at, ingest_seq FROM sync_work_events
        )
        WHERE user_id = ?
    """
    params: list[str] = [user_id]
    if ingest_seq_cursor is not None:
        query += " AND COALESCE(ingest_seq, 0) > ?"
        params.append(str(ingest_seq_cursor))
    elif updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND id > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

    row = conn.execute(query, params).fetchone()
    return int(row["total_rows"] if row is not None else 0)


def _count_sync_table_rows_after_cursor(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    user_id: str,
    cursor_value: str | None,
) -> int:
    ingest_seq_cursor = _parse_ingest_seq_cursor(cursor_value)
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = f"SELECT COUNT(*) AS total_rows FROM {table_name} WHERE user_id = ?"
    params: list[str] = [user_id]
    if ingest_seq_cursor is not None:
        query += " AND COALESCE(ingest_seq, 0) > ?"
        params.append(str(ingest_seq_cursor))
    elif updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND uuid > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

    row = conn.execute(query, params).fetchone()
    return int(row["total_rows"] if row is not None else 0)


def _recent_sync_errors(
    conn: sqlite3.Connection, *, limit: int = 20
) -> list[dict[str, object | None]]:
    rows = conn.execute(
        """
        SELECT
            id,
            endpoint,
            error_type,
            message,
            retry_count,
            status,
            status_code,
            device_id,
            user_id,
            batch_id,
            created_at,
            updated_at
        FROM sync_error_logs
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def get_sync_health(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    token_row = conn.execute(
        """
        SELECT COUNT(*) AS active_tokens
        FROM sync_tokens
        WHERE revoked_at IS NULL
        """
    ).fetchone()
    device_row = conn.execute(
        """
        SELECT COUNT(*) AS registered_devices,
               MAX(last_seen_at) AS last_seen_at
        FROM sync_devices
        """
    ).fetchone()
    checkpoint_row = conn.execute(
        """
        SELECT MAX(updated_at) AS last_sync_at
        FROM sync_checkpoints
        """
    ).fetchone()
    device_status_rows = conn.execute(
        """
        SELECT
            d.id AS device_id,
            d.user_id AS user_id,
            d.name AS device_name,
            d.type AS device_type,
            d.category AS device_category,
            d.last_seen_at AS last_seen_at,
            c.updated_at AS last_sync_at,
            c.last_pull_cursor AS last_pull_cursor
        FROM sync_devices d
        LEFT JOIN sync_checkpoints c
            ON c.device_id = d.id
        ORDER BY COALESCE(c.updated_at, d.last_seen_at) DESC, d.id ASC
        """
    ).fetchall()
    metrics_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN endpoint = '/api/sync/v1/push' THEN 1 ELSE 0 END) AS push_total,
            SUM(CASE WHEN endpoint = '/api/sync/v1/push' AND status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS push_success,
            SUM(CASE WHEN endpoint = '/api/sync/v1/push' AND status_code = 409 THEN 1 ELSE 0 END) AS push_conflicts,
            SUM(CASE WHEN endpoint = '/api/sync/v1/pull' THEN 1 ELSE 0 END) AS pull_total,
            SUM(CASE WHEN endpoint = '/api/sync/v1/pull' AND status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS pull_success,
            AVG(CASE WHEN endpoint = '/api/sync/v1/push' THEN latency_ms END) AS avg_push_latency_ms,
            AVG(CASE WHEN endpoint = '/api/sync/v1/pull' THEN latency_ms END) AS avg_pull_latency_ms,
            MAX(created_at) AS last_request_at
        FROM sync_request_logs
        """
    ).fetchone()
    error_count_row = conn.execute(
        """
        SELECT COUNT(*) AS open_errors, MAX(created_at) AS last_error_at
        FROM sync_error_logs
        WHERE status = 'open'
        """
    ).fetchone()
    recent_errors = _recent_sync_errors(conn, limit=20)

    active_tokens = int(token_row["active_tokens"] if token_row is not None else 0)
    registered_devices = int(
        device_row["registered_devices"] if device_row is not None else 0
    )
    push_total = int(
        metrics_row["push_total"]
        if metrics_row and metrics_row["push_total"] is not None
        else 0
    )
    push_success = int(
        metrics_row["push_success"]
        if metrics_row and metrics_row["push_success"] is not None
        else 0
    )
    push_conflicts = int(
        metrics_row["push_conflicts"]
        if metrics_row and metrics_row["push_conflicts"] is not None
        else 0
    )
    pull_total = int(
        metrics_row["pull_total"]
        if metrics_row and metrics_row["pull_total"] is not None
        else 0
    )
    pull_success = int(
        metrics_row["pull_success"]
        if metrics_row and metrics_row["pull_success"] is not None
        else 0
    )
    avg_push_latency = (
        int(round(float(metrics_row["avg_push_latency_ms"])))
        if metrics_row and metrics_row["avg_push_latency_ms"] is not None
        else 0
    )
    avg_pull_latency = (
        int(round(float(metrics_row["avg_pull_latency_ms"])))
        if metrics_row and metrics_row["avg_pull_latency_ms"] is not None
        else 0
    )
    last_sync_at = (
        checkpoint_row["last_sync_at"] if checkpoint_row is not None else None
    )
    lag_seconds = None
    if last_sync_at:
        try:
            lag_seconds = max(
                0,
                int(
                    (
                        datetime.now(UTC) - datetime.fromisoformat(last_sync_at)
                    ).total_seconds()
                ),
            )
        except ValueError:
            lag_seconds = None

    device_statuses: list[dict[str, object]] = []
    synced_devices = 0
    pending_pull_rows_total = 0
    pending_sessions_total = 0
    pending_journal_entries_total = 0
    pending_reflections_total = 0
    pending_work_events_total = 0
    for row in device_status_rows:
        device_last_sync = row["last_sync_at"]
        last_pull_cursor = row["last_pull_cursor"]
        row_user_id = str(row["user_id"] or "").strip()
        pending_sessions = 0
        pending_journal_entries = 0
        pending_reflections = 0
        pending_work_events = 0
        pending_pull_rows = 0
        if row_user_id:
            pending_sessions = _count_sync_table_rows_after_cursor(
                conn,
                table_name="sync_sessions",
                user_id=row_user_id,
                cursor_value=last_pull_cursor,
            )
            pending_journal_entries = _count_sync_table_rows_after_cursor(
                conn,
                table_name="sync_journal_entries",
                user_id=row_user_id,
                cursor_value=last_pull_cursor,
            )
            pending_reflections = _count_sync_table_rows_after_cursor(
                conn,
                table_name="sync_daily_reflections",
                user_id=row_user_id,
                cursor_value=last_pull_cursor,
            )
            pending_work_events = _count_sync_table_rows_after_cursor(
                conn,
                table_name="sync_work_events",
                user_id=row_user_id,
                cursor_value=last_pull_cursor,
            )
            pending_pull_rows = (
                pending_sessions
                + pending_journal_entries
                + pending_reflections
                + pending_work_events
            )

        pending_pull_rows_total += pending_pull_rows
        pending_sessions_total += pending_sessions
        pending_journal_entries_total += pending_journal_entries
        pending_reflections_total += pending_reflections
        pending_work_events_total += pending_work_events

        is_synced = bool(device_last_sync)
        if is_synced:
            synced_devices += 1
        health_status = "healthy"
        if not is_synced:
            health_status = "not-synced"
        elif pending_pull_rows > 0:
            health_status = "pending-pull"
        device_statuses.append(
            {
                "device_id": row["device_id"],
                "user_id": row["user_id"],
                "device_name": row["device_name"],
                "device_type": row["device_type"],
                "device_category": row["device_category"],
                "last_seen_at": row["last_seen_at"],
                "last_sync_at": device_last_sync,
                "is_synced": is_synced,
                "status": health_status,
                "last_pull_cursor": last_pull_cursor,
                "pending_pull_rows": pending_pull_rows,
                "pending_sessions": pending_sessions,
                "pending_journal_entries": pending_journal_entries,
                "pending_reflections": pending_reflections,
                "pending_work_events": pending_work_events,
            }
        )

    open_errors = int(
        error_count_row["open_errors"] if error_count_row is not None else 0
    )
    pending_upload_failures = sum(
        1
        for item in recent_errors
        if str(item.get("endpoint") or "") == "/api/sync/v1/push"
    )
    conn.close()

    return {
        "enabled": active_tokens > 0,
        "active_tokens": active_tokens,
        "registered_devices": registered_devices,
        "last_seen_at": device_row["last_seen_at"] if device_row is not None else None,
        "last_sync_at": last_sync_at,
        "lag_seconds": lag_seconds,
        "push_requests_total": push_total,
        "push_success_rate": (push_success / push_total) if push_total else None,
        "push_conflict_rate": (push_conflicts / push_total) if push_total else None,
        "pull_requests_total": pull_total,
        "pull_success_rate": (pull_success / pull_total) if pull_total else None,
        "avg_push_latency_ms": avg_push_latency,
        "avg_pull_latency_ms": avg_pull_latency,
        "last_request_at": metrics_row["last_request_at"]
        if metrics_row is not None
        else None,
        "synced_devices": synced_devices,
        "unsynced_devices": max(0, registered_devices - synced_devices),
        "pending_pull_rows": pending_pull_rows_total,
        "pending_sessions": pending_sessions_total,
        "pending_journal_entries": pending_journal_entries_total,
        "pending_reflections": pending_reflections_total,
        "pending_work_events": pending_work_events_total,
        "pending_upload_failures": pending_upload_failures,
        "sync_failures": open_errors,
        "last_error_at": error_count_row["last_error_at"]
        if error_count_row is not None
        else None,
        "recent_errors": recent_errors,
        "device_statuses": device_statuses,
    }


def get_available_tags() -> list[str]:
    return sorted(ActivityTagger().rules.keys())


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

    activity_table = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_sessions'"
    ).fetchone()
    if activity_table is None:
        conn.close()
        return []

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


def query_sync_sessions(
    db_path: Path,
    *,
    user_id: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    device_id: str | None = None,
    limit: int = 2000,
) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    query = """
        SELECT payload_json
        FROM sync_sessions
        WHERE user_id = ?
          AND deleted_at IS NULL
    """
    params: list[str | int] = [user_id]

    if start_date:
        query += " AND utc_start >= ?"
        params.append(start_date.isoformat(timespec="seconds"))

    if end_date:
        query += " AND utc_end <= ?"
        params.append(end_date.isoformat(timespec="seconds"))

    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)

    query += " ORDER BY utc_start DESC, uuid DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    sessions: list[dict] = []
    for row in rows:
        raw_payload = row["payload_json"]
        if not raw_payload:
            continue
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            sessions.append(payload)
    return sessions


def query_recent_journal_entries(db_path: Path, limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM journal_entries
        ORDER BY COALESCE(start_time, created_at) DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [_serialize_journal_row(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def query_recent_reflections(db_path: Path, limit: int = 30) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM daily_reflections
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def query_recent_work_events(db_path: Path, limit: int = 30) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM work_events
        ORDER BY COALESCE(event_time, created_at) DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [_serialize_work_event_row(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def _normalize_source(source: str) -> str:
    normalized = source.strip().lower()
    if normalized not in {"local", "sync"}:
        raise HTTPException(status_code=400, detail="source must be 'local' or 'sync'")
    return normalized


def _normalize_dashboard_mode(mode: str | None, source: str | None = None) -> str:
    if mode:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in DASHBOARD_MODES:
            raise HTTPException(
                status_code=400,
                detail="mode must be one of: standalone, sync-client, sync-server",
            )
        return normalized_mode

    if source:
        return "sync-server" if _normalize_source(source) == "sync" else "standalone"
    return "standalone"


def _resolve_settings_path() -> Path:
    if PERSONAL_SETTINGS_PATH.exists():
        return PERSONAL_SETTINGS_PATH
    return DEFAULT_SETTINGS_PATH


def _resolve_goals_path() -> Path:
    personal_path = Path("config/my-goals.yaml")
    default_path = Path("config/goals.yaml")
    if personal_path.exists():
        return personal_path
    return default_path


def _load_goal_targets(path: Path | None = None) -> dict[str, float]:
    goals_path = path or _resolve_goals_path()
    if not goals_path.exists():
        return {}

    raw = yaml.safe_load(goals_path.read_text(encoding="utf-8")) or {}
    goals_node = raw.get("goals", raw)
    if not isinstance(goals_node, dict):
        return {}

    parsed: dict[str, float] = {}
    for name, value in goals_node.items():
        try:
            parsed[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return parsed


def _build_goal_drift_summary(
    tag_stats: dict[str, object],
    total_seconds: int,
) -> dict[str, object] | None:
    goals = _load_goal_targets()
    if not goals or total_seconds <= 0:
        return None

    actual_by_goal: dict[str, float] = {}
    configured_seconds = 0
    for goal_name in goals:
        seconds = int(tag_stats.get(goal_name, 0) or 0)
        actual_by_goal[goal_name] = (seconds / total_seconds) * 100.0
        configured_seconds += seconds

    unmapped_seconds = max(total_seconds - configured_seconds, 0)
    actual_by_goal["Unmapped"] = (unmapped_seconds / total_seconds) * 100.0

    planned_distribution = dict(goals)
    planned_distribution["Unmapped"] = 0.0

    drift_items: list[dict[str, object]] = []
    for goal_name, planned_pct in planned_distribution.items():
        actual_pct = actual_by_goal.get(goal_name, 0.0)
        delta_pct_points = actual_pct - planned_pct
        drift_items.append(
            {
                "goal": goal_name,
                "planned_pct": round(float(planned_pct), 2),
                "actual_pct": round(actual_pct, 2),
                "delta_pct_points": round(delta_pct_points, 2),
                "relative_gap_pct": round(
                    ((planned_pct - actual_pct) / planned_pct) * 100.0, 2
                )
                if planned_pct > 0
                else 0.0,
            }
        )

    drift_items.sort(
        key=lambda item: abs(float(item["delta_pct_points"])), reverse=True
    )
    score = sum(abs(float(item["delta_pct_points"])) for item in drift_items) / 2.0

    largest_gap = drift_items[0] if drift_items else None
    return {
        "goals_path": str(_resolve_goals_path()),
        "goal_targets": planned_distribution,
        "goal_drift_items": drift_items,
        "goal_drift_score_pct_points": round(score, 2),
        "goal_coverage_pct": round(
            sum(actual_by_goal.get(goal_name, 0.0) for goal_name in goals), 2
        ),
        "unmapped_pct": round(actual_by_goal.get("Unmapped", 0.0), 2),
        "largest_gap": largest_gap,
    }


def _build_dashboard_header_context(
    *,
    dashboard_mode: str,
    source: str,
    days: int,
    selected_user_id: str | None = None,
    selected_device_id: str | None = None,
    selected_user_name: str | None = None,
    selected_device_name: str | None = None,
) -> dict[str, object]:
    return {
        "mode": dashboard_mode,
        "source": source,
        "time_range": f"Last {days} day{'s' if days != 1 else ''}",
        "user": selected_user_name or selected_user_id or "All users",
        "device": selected_device_name or selected_device_id or "All devices",
    }


def _read_simple_yaml(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


def _configured_dashboard_mode() -> str:
    settings_path = _resolve_settings_path()
    raw_values = _read_simple_yaml(settings_path)
    configured_mode = raw_values.get("dashboard_mode", "standalone")
    return _normalize_dashboard_mode(configured_mode)


def _configured_sync_client_status(db_path: Path) -> dict[str, object]:
    settings_path = _resolve_settings_path()
    raw_values = _read_simple_yaml(settings_path)
    sync_token = str(raw_values.get("sync_token", "") or "").strip()
    sync_base_url = str(raw_values.get("sync_base_url", "") or "").strip()

    status: dict[str, object] = {
        "registered": bool(sync_token),
        "sync_base_url": sync_base_url,
        "last_synced_at": None,
        "device_id": None,
        "user_id": None,
    }

    if not db_path.exists():
        return status

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sync_state_table = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'sync_state'
            """
        ).fetchone()
        if sync_state_table is None:
            return status

        state = conn.execute(
            """
            SELECT device_id, user_id, last_push_cursor, last_pull_cursor, updated_at
            FROM sync_state
            WHERE id = 1
            """
        ).fetchone()
        if state is None:
            return status

        status["device_id"] = state["device_id"]
        status["user_id"] = state["user_id"]
        if state["last_push_cursor"] or state["last_pull_cursor"]:
            status["last_synced_at"] = state["updated_at"]
        return status
    finally:
        conn.close()


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _count_pending_local_rows(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    updated_at_cursor: str | None,
    uuid_cursor: str | None,
) -> int:
    if not _sqlite_table_exists(conn, table_name):
        return 0

    columns = _sqlite_table_columns(conn, table_name)
    if "updated_at" not in columns or "uuid" not in columns:
        return 0

    query = f"SELECT COUNT(*) AS pending_count FROM {table_name} WHERE 1=1"
    params: list[str] = []
    if updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND uuid > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

    row = conn.execute(query, params).fetchone()
    return int(row["pending_count"] if row is not None else 0)


def get_sync_client_debug(
    db_path: Path,
    *,
    user_id: str | None,
    device_id: str | None,
) -> dict[str, object]:
    debug: dict[str, object] = {
        "session_count": 0,
        "distinct_days": 0,
        "earliest_synced_at": None,
        "latest_synced_at": None,
        "days": [],
        "pending_sessions": 0,
        "pending_journal_entries": 0,
        "pending_reflections": 0,
    }

    if not db_path.exists():
        return debug

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    try:
        where_clauses = ["deleted_at IS NULL"]
        params: list[str] = []
        if user_id:
            where_clauses.append("user_id = ?")
            params.append(user_id)
        if device_id:
            where_clauses.append("device_id = ?")
            params.append(device_id)

        where_sql = " AND ".join(where_clauses)

        coverage_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS session_count,
                COUNT(DISTINCT substr(utc_start, 1, 10)) AS distinct_days,
                MIN(utc_start) AS earliest_synced_at,
                MAX(utc_start) AS latest_synced_at
            FROM sync_sessions
            WHERE {where_sql}
            """,
            params,
        ).fetchone()

        if coverage_row is not None:
            debug["session_count"] = int(coverage_row["session_count"] or 0)
            debug["distinct_days"] = int(coverage_row["distinct_days"] or 0)
            debug["earliest_synced_at"] = coverage_row["earliest_synced_at"]
            debug["latest_synced_at"] = coverage_row["latest_synced_at"]

        day_rows = conn.execute(
            f"""
            SELECT substr(utc_start, 1, 10) AS day_utc, COUNT(*) AS sessions
            FROM sync_sessions
            WHERE {where_sql}
              AND utc_start IS NOT NULL
            GROUP BY day_utc
            ORDER BY day_utc DESC
            LIMIT 10
            """,
            params,
        ).fetchall()
        debug["days"] = [
            {
                "day_utc": str(row["day_utc"]),
                "sessions": int(row["sessions"] or 0),
            }
            for row in day_rows
        ]

        if _sqlite_table_exists(conn, "sync_state"):
            state_row = conn.execute(
                "SELECT last_push_cursor FROM sync_state WHERE id = 1"
            ).fetchone()
            if state_row is not None:
                updated_at_cursor, uuid_cursor = _parse_cursor(
                    state_row["last_push_cursor"]
                )
                debug["pending_sessions"] = _count_pending_local_rows(
                    conn,
                    table_name="activity_sessions",
                    updated_at_cursor=updated_at_cursor,
                    uuid_cursor=uuid_cursor,
                )
                debug["pending_journal_entries"] = _count_pending_local_rows(
                    conn,
                    table_name="journal_entries",
                    updated_at_cursor=updated_at_cursor,
                    uuid_cursor=uuid_cursor,
                )
                debug["pending_reflections"] = _count_pending_local_rows(
                    conn,
                    table_name="daily_reflections",
                    updated_at_cursor=updated_at_cursor,
                    uuid_cursor=uuid_cursor,
                )
    finally:
        conn.close()

    return debug


def _source_for_dashboard_mode(mode: str) -> str:
    return "sync" if mode == "sync-server" else "local"


def _ensure_journal_features_enabled() -> None:
    if _configured_dashboard_mode() == "sync-server":
        raise HTTPException(
            status_code=404,
            detail="Journal features are unavailable in sync-server mode",
        )


def _ensure_tag_review_features_enabled() -> None:
    if _configured_dashboard_mode() == "sync-server":
        raise HTTPException(
            status_code=404, detail="Tag review is unavailable in sync-server mode"
        )


def _build_tag_review_payload(
    *,
    db_path: Path,
    days: int | None,
    selected_tag: str | None,
    min_domain_hits: int,
    min_repo_hits: int,
) -> tuple[dict[str, dict[str, object]], str, list[dict[str, str]]]:
    from db.repository import ActivityRepository

    existing_rules = load_custom_tag_rules()
    with ActivityRepository(db_path) as repository:
        review_actions = [
            dict(row)
            for row in repository.list_review_actions_for_suggestions(
                days=days,
                selected_tag=selected_tag,
            )
        ]

    suggestions = build_tag_review_suggestions(
        review_actions,
        existing_rules,
        min_domain_hits=min_domain_hits,
        min_repo_hits=min_repo_hits,
    )
    if selected_tag:
        suggestions = {
            selected_tag: suggestions.get(
                selected_tag,
                {
                    "tag_name": selected_tag,
                    "domains": [],
                    "repos": [],
                    "keywords": list(
                        existing_rules.get(selected_tag, {}).get("keywords", [])
                    ),
                    "total_reviewed": 0,
                },
            )
        }
    preview_text, warnings = build_yaml_preview(existing_rules, suggestions)
    return suggestions, preview_text, warnings


def _upsert_sync_user_and_device(
    conn: sqlite3.Connection,
    payload: SyncRegisterRequest,
    *,
    issue_token: bool,
) -> tuple[str | None, str]:
    cursor = conn.cursor()
    now_iso = _now_iso()

    cursor.execute(
        """
        INSERT INTO sync_users (id, name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            updated_at = excluded.updated_at
        """,
        (payload.user.id, payload.user.name, now_iso, now_iso),
    )

    cursor.execute(
        """
        INSERT INTO sync_devices (
            id,
            user_id,
            name,
            type,
            hostname,
            category,
            created_at,
            updated_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            user_id = excluded.user_id,
            name = excluded.name,
            type = excluded.type,
            hostname = excluded.hostname,
            category = excluded.category,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at
        """,
        (
            payload.device.id,
            payload.user.id,
            payload.device.name,
            payload.device.type,
            payload.device.hostname,
            payload.device.category,
            now_iso,
            now_iso,
            now_iso,
        ),
    )

    token: str | None = None
    if issue_token:
        token = str(uuid4())
        cursor.execute(
            """
            INSERT INTO sync_tokens (token, device_id, user_id, created_at, revoked_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (token, payload.device.id, payload.user.id, now_iso),
        )

    return token, now_iso


def _register_on_sync_server(base_url: str, payload: SyncRegisterRequest) -> dict:
    normalized_base = base_url.strip().rstrip("/")
    if not normalized_base:
        raise HTTPException(
            status_code=400, detail="sync_base_url is required for sync-client mode"
        )

    request_url = f"{normalized_base}/api/sync/v1/devices/register"
    request_body = json.dumps(
        payload.model_dump(mode="python"), separators=(",", ":")
    ).encode("utf-8")
    request_obj = urllib_request.Request(
        request_url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(request_obj, timeout=10) as response:
            response_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"sync server registration failed ({exc.code}): {detail}",
        ) from exc
    except urllib_error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"sync server registration failed: {exc.reason}",
        ) from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="sync server returned invalid JSON"
        ) from exc

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=502, detail="sync server returned unexpected response"
        )
    return parsed


def _resolve_sync_user_id(conn: sqlite3.Connection, user_id: str | None) -> str:
    if user_id:
        return user_id
    row = conn.execute(
        "SELECT id FROM sync_users ORDER BY updated_at DESC, id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=400, detail="No sync users found. Register a device first."
        )
    return str(row["id"])


def get_sync_summary_stats(
    db_path: Path,
    *,
    user_id: str,
    days: int = 7,
    device_id: str | None = None,
) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    cursor = conn.cursor()

    start_date = datetime.now(UTC) - timedelta(days=days)
    start_iso = start_date.isoformat(timespec="seconds")
    start_day = start_date.strftime("%Y-%m-%d")

    _rebuild_sync_rollups(
        conn,
        user_id=user_id,
        device_id=device_id,
        start_day=start_day,
    )

    device_filter_rollup = ""
    device_filter_sessions = ""
    params_rollup: list[str] = [user_id, start_day]
    params_sessions: list[str] = [user_id, start_iso]
    if device_id:
        device_filter_rollup = " AND device_id = ?"
        device_filter_sessions = " AND device_id = ?"
        params_rollup.append(device_id)
        params_sessions.append(device_id)

    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(active_seconds), 0) AS total_seconds,
            COALESCE(SUM(focus_seconds), 0) AS focus_seconds,
            COALESCE(SUM(meeting_seconds), 0) AS meeting_seconds,
            COALESCE(SUM(context_switches), 0) AS total_switches
        FROM sync_metrics_daily
        WHERE user_id = ?
          AND day_utc >= ?
          {device_filter_rollup}
        """,
        params_rollup,
    )
    rollup = cursor.fetchone()
    total_seconds = int((rollup["total_seconds"] if rollup else 0) or 0)
    focus_seconds = int((rollup["focus_seconds"] if rollup else 0) or 0)
    meeting_seconds = int((rollup["meeting_seconds"] if rollup else 0) or 0)
    total_switches = int((rollup["total_switches"] if rollup else 0) or 0)

    cursor.execute(
        f"""
        SELECT COALESCE(tag, 'Untagged') AS tag_name, COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS total_seconds
        FROM sync_sessions
        WHERE user_id = ?
          AND utc_start >= ?
          AND deleted_at IS NULL
          {device_filter_sessions}
        GROUP BY tag_name
        ORDER BY total_seconds DESC
        """,
        params_sessions,
    )
    tag_stats = {
        str(row["tag_name"]): int(row["total_seconds"] or 0)
        for row in cursor.fetchall()
    }

    cursor.execute(
        f"""
        SELECT COALESCE(application_name, 'Unknown') AS app_name, COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS total_seconds
        FROM sync_sessions
        WHERE user_id = ?
          AND utc_start >= ?
          AND deleted_at IS NULL
          {device_filter_sessions}
        GROUP BY app_name
        ORDER BY total_seconds DESC
        LIMIT 10
        """,
        params_sessions,
    )
    app_stats = {
        str(row["app_name"]): int(row["total_seconds"] or 0)
        for row in cursor.fetchall()
    }

    cursor.execute(
        f"""
        SELECT repo_name, COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS total_seconds
        FROM sync_sessions
        WHERE user_id = ?
          AND utc_start >= ?
          AND deleted_at IS NULL
          AND repo_name IS NOT NULL
          {device_filter_sessions}
        GROUP BY repo_name
        ORDER BY total_seconds DESC
        LIMIT 5
        """,
        params_sessions,
    )
    repo_stats = {
        str(row["repo_name"]): int(row["total_seconds"] or 0)
        for row in cursor.fetchall()
    }

    cursor.execute(
        f"""
        SELECT COALESCE(SUM(COALESCE(active_seconds, 0)), 0) AS tagged_active_seconds
        FROM sync_sessions
        WHERE user_id = ?
          AND utc_start >= ?
          AND deleted_at IS NULL
          AND tag IS NOT NULL
          {device_filter_sessions}
        """,
        params_sessions,
    )
    tagged_row = cursor.fetchone()
    tagged_active_seconds = int(
        (tagged_row["tagged_active_seconds"] if tagged_row else 0) or 0
    )

    cursor.execute(
        f"""
        SELECT day_utc, active_seconds, meeting_seconds, context_switches
        FROM sync_metrics_daily
        WHERE user_id = ?
          AND day_utc >= ?
          {device_filter_rollup}
        ORDER BY day_utc DESC
        LIMIT 7
        """,
        params_rollup,
    )
    daily_trend = []
    for row in cursor.fetchall():
        day_value = str(row["day_utc"])
        day_active = int(row["active_seconds"] or 0)
        day_meeting = int(row["meeting_seconds"] or 0)
        day_switches = int(row["context_switches"] or 0)
        daily_trend.append(
            {
                "day": day_value,
                "day_label": datetime.strptime(day_value, "%Y-%m-%d").strftime("%a"),
                "active_hours": round(day_active / 3600, 2),
                "meeting_hours": round(day_meeting / 3600, 2),
                "switches_per_hour": round(
                    day_switches / max(day_active / 3600, 0.001), 2
                ),
            }
        )

    # Deep-work metrics still need ordered session rows.
    cursor.execute(
        f"""
        SELECT utc_start, utc_end, COALESCE(active_seconds, 0) AS active_seconds
        FROM sync_sessions
        WHERE user_id = ?
          AND utc_start >= ?
          AND deleted_at IS NULL
          {device_filter_sessions}
          AND utc_start IS NOT NULL
        ORDER BY utc_start ASC, uuid ASC
        """,
        params_sessions,
    )
    focus_blocks: list[int] = []
    current_block_end: datetime | None = None
    current_block_seconds = 0
    for row in cursor.fetchall():
        start_time = _parse_iso_datetime(str(row["utc_start"]))
        end_raw = row["utc_end"] if row["utc_end"] else row["utc_start"]
        end_time = _parse_iso_datetime(str(end_raw))
        duration_sec = int(row["active_seconds"] or 0)

        if current_block_end is None:
            current_block_end = end_time
            current_block_seconds = duration_sec
            continue

        gap_seconds = (start_time - current_block_end).total_seconds()
        if gap_seconds > 90:
            focus_blocks.append(current_block_seconds)
            current_block_seconds = duration_sec
        else:
            current_block_seconds += duration_sec

        if end_time > current_block_end:
            current_block_end = end_time

    if current_block_end is not None:
        focus_blocks.append(current_block_seconds)

    deep_focus_blocks = [block for block in focus_blocks if block >= 1800]
    deep_work_blocks = len(deep_focus_blocks)
    longest_focus_sec = max(focus_blocks) if focus_blocks else 0
    avg_focus_sec = (
        (sum(deep_focus_blocks) / len(deep_focus_blocks)) if deep_focus_blocks else 0
    )

    conn.close()

    active_hours = total_seconds / 3600 if total_seconds else 0
    tagged_ratio = (tagged_active_seconds / total_seconds) if total_seconds else 0
    meeting_ratio = (meeting_seconds / total_seconds) if total_seconds else 0
    switch_rate_per_hour = total_switches / active_hours if active_hours else 0
    goal_drift = _build_goal_drift_summary(tag_stats, total_seconds)

    return {
        "tag_stats": tag_stats,
        "app_stats": app_stats,
        "repo_stats": repo_stats,
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600, 1),
        "total_idle_seconds": 0,
        "total_idle_hours": 0.0,
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
        "goal_drift": goal_drift,
    }


def get_sync_server_overview(
    db_path: Path,
    *,
    days: int = 7,
    user_id: str | None = None,
    device_id: str | None = None,
) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    users = conn.execute(
        """
        SELECT id, name, created_at, updated_at
        FROM sync_users
        ORDER BY updated_at DESC, id ASC
        """
    ).fetchall()

    selected_user_id = user_id
    if selected_user_id is None and users:
        selected_user_id = str(users[0]["id"])

    devices_query = """
        SELECT
            d.id,
            d.user_id,
            u.name AS user_name,
            d.name,
            d.type,
            d.hostname,
            d.category,
            d.last_seen_at,
            d.created_at,
            d.updated_at
        FROM sync_devices d
        LEFT JOIN sync_users u ON u.id = d.user_id
    """
    devices_params: list[str] = []
    if selected_user_id:
        devices_query += " WHERE user_id = ?"
        devices_params.append(selected_user_id)
    devices_query += " ORDER BY COALESCE(d.last_seen_at, d.updated_at) DESC, d.id ASC"
    devices = conn.execute(devices_query, devices_params).fetchall()

    start_date = datetime.now(UTC) - timedelta(days=days)
    start_iso = start_date.isoformat(timespec="seconds")
    start_day = start_date.strftime("%Y-%m-%d")

    if selected_user_id:
        _rebuild_sync_rollups(
            conn,
            user_id=selected_user_id,
            device_id=device_id,
            start_day=start_day,
        )

    global_query = """
        SELECT
            COALESCE(SUM(active_seconds), 0) AS total_seconds,
            COALESCE(SUM(focus_seconds), 0) AS focus_seconds,
            COALESCE(SUM(meeting_seconds), 0) AS meeting_seconds,
            COALESCE(SUM(context_switches), 0) AS total_switches
        FROM sync_metrics_daily
        WHERE day_utc >= ?
    """
    global_params: list[str] = [start_day]
    if selected_user_id:
        global_query += " AND user_id = ?"
        global_params.append(selected_user_id)
    if device_id:
        global_query += " AND device_id = ?"
        global_params.append(device_id)
    global_row = conn.execute(global_query, global_params).fetchone()

    trend_query = """
        SELECT
            day_utc,
            COALESCE(SUM(active_seconds), 0) AS active_seconds,
            COALESCE(SUM(meeting_seconds), 0) AS meeting_seconds,
            COALESCE(SUM(context_switches), 0) AS context_switches
        FROM sync_metrics_daily
        WHERE day_utc >= ?
    """
    trend_params: list[str] = [start_day]
    if selected_user_id:
        trend_query += " AND user_id = ?"
        trend_params.append(selected_user_id)
    if device_id:
        trend_query += " AND device_id = ?"
        trend_params.append(device_id)
    trend_query += " GROUP BY day_utc ORDER BY day_utc DESC LIMIT 7"

    daily_trend: list[dict[str, object]] = []
    for row in conn.execute(trend_query, trend_params).fetchall():
        day_value = str(row["day_utc"])
        active_seconds = int(row["active_seconds"] or 0)
        meeting_seconds = int(row["meeting_seconds"] or 0)
        switches = int(row["context_switches"] or 0)
        daily_trend.append(
            {
                "day": day_value,
                "day_label": datetime.strptime(day_value, "%Y-%m-%d").strftime("%a"),
                "active_hours": round(active_seconds / 3600, 2),
                "meeting_hours": round(meeting_seconds / 3600, 2),
                "switches_per_hour": round(
                    switches / max(active_seconds / 3600, 0.001), 2
                ),
            }
        )

    user_stats: dict | None = None
    if selected_user_id:
        conn.close()
        user_stats = get_sync_summary_stats(
            db_path,
            user_id=selected_user_id,
            days=days,
            device_id=device_id,
        )
    else:
        conn.close()

    total_seconds = int((global_row["total_seconds"] if global_row else 0) or 0)
    focus_seconds = int((global_row["focus_seconds"] if global_row else 0) or 0)
    meeting_seconds = int((global_row["meeting_seconds"] if global_row else 0) or 0)
    total_switches = int((global_row["total_switches"] if global_row else 0) or 0)

    return {
        "users": [dict(row) for row in users],
        "devices": [dict(row) for row in devices],
        "selected_user_id": selected_user_id,
        "selected_device_id": device_id,
        "global_stats": {
            "total_seconds": total_seconds,
            "total_hours": round(total_seconds / 3600, 1),
            "focus_seconds": focus_seconds,
            "focus_hours": round(focus_seconds / 3600, 1),
            "meeting_seconds": meeting_seconds,
            "meeting_hours": round(meeting_seconds / 3600, 1),
            "total_switches": total_switches,
            "daily_trend": daily_trend,
        },
        "user_stats": user_stats,
    }


def get_summary_stats(db_path: Path, days: int = 7) -> dict:
    """Get summary statistics for the past N days."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    activity_table = cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = 'activity_sessions'
        """
    ).fetchone()
    if activity_table is None:
        conn.close()
        return {
            "tag_stats": {},
            "app_stats": {},
            "repo_stats": {},
            "total_seconds": 0,
            "total_hours": 0,
            "total_idle_seconds": 0,
            "total_idle_hours": 0,
            "tagged_active_seconds": 0,
            "tagged_ratio": 0,
            "meeting_seconds": 0,
            "meeting_hours": 0,
            "meeting_ratio": 0,
            "total_switches": 0,
            "switch_rate_per_hour": 0,
            "deep_work_blocks": 0,
            "longest_focus_sec": 0,
            "longest_focus_hours": 0,
            "avg_focus_sec": 0,
            "avg_focus_minutes": 0,
            "daily_trend": [],
        }

    start_date = datetime.now(UTC) - timedelta(days=days)
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
    tag_stats = {
        row["tag"] or "Untagged": row["total_seconds"] for row in cursor.fetchall()
    }

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

    # Context switching metrics (derived from transitions between active sessions).
    try:
        cursor.execute(
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
            SELECT COUNT(*) as total_switches
            FROM ordered
            WHERE prev_app_name IS NOT NULL
              AND (
                  COALESCE(app_name, '') != COALESCE(prev_app_name, '')
                  OR COALESCE(window_title, '') != COALESCE(prev_window_title, '')
                  OR COALESCE(browser_domain, '') != COALESCE(prev_browser_domain, '')
              )
            """,
            (start_iso,),
        )
        total_switches = cursor.fetchone()["total_switches"] or 0
    except sqlite3.OperationalError:
        # Fallback for SQLite builds without window function support.
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
        SELECT start_time, end_time, duration_sec
        FROM activity_sessions
        WHERE start_time >= ?
          AND is_idle = 0
        ORDER BY start_time, id
        """,
        (start_iso,),
    )
    focus_blocks: list[int] = []
    current_block_end: datetime | None = None
    current_block_seconds = 0

    for row in cursor.fetchall():
        start_time = _parse_iso_datetime(row["start_time"])
        end_time_value = row["end_time"] or row["start_time"]
        end_time = _parse_iso_datetime(end_time_value)
        duration_sec = int(row["duration_sec"] or 0)

        if current_block_end is None:
            current_block_end = end_time
            current_block_seconds = duration_sec
            continue

        gap_seconds = (start_time - current_block_end).total_seconds()
        if gap_seconds > 90:
            focus_blocks.append(current_block_seconds)
            current_block_seconds = duration_sec
        else:
            current_block_seconds += duration_sec

        if end_time > current_block_end:
            current_block_end = end_time

    if current_block_end is not None:
        focus_blocks.append(current_block_seconds)

    deep_focus_blocks = [
        block_seconds for block_seconds in focus_blocks if block_seconds >= 1800
    ]
    deep_work_blocks = len(deep_focus_blocks)
    longest_focus_sec = max(focus_blocks) if focus_blocks else 0
    avg_focus_sec = (
        sum(deep_focus_blocks) / len(deep_focus_blocks) if deep_focus_blocks else 0
    )

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

    # Daily trend (active time and meeting proxy)
    cursor.execute(
        """
        SELECT
            substr(start_time, 1, 10) as day,
            SUM(CASE WHEN is_idle = 0 THEN duration_sec ELSE 0 END) as active_seconds,
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
    daily_rows = list(cursor.fetchall())

    daily_switches: dict[str, int] = {}
    try:
        cursor.execute(
            """
            WITH ordered AS (
                SELECT
                    substr(start_time, 1, 10) as day,
                    start_time,
                    app_name,
                    window_title,
                    browser_domain,
                    LAG(app_name) OVER (
                        PARTITION BY substr(start_time, 1, 10)
                        ORDER BY start_time
                    ) as prev_app_name,
                    LAG(window_title) OVER (
                        PARTITION BY substr(start_time, 1, 10)
                        ORDER BY start_time
                    ) as prev_window_title,
                    LAG(browser_domain) OVER (
                        PARTITION BY substr(start_time, 1, 10)
                        ORDER BY start_time
                    ) as prev_browser_domain
                FROM activity_sessions
                WHERE start_time >= ? AND is_idle = 0
            )
            SELECT day, COUNT(*) as switches
            FROM ordered
            WHERE prev_app_name IS NOT NULL
              AND (
                  COALESCE(app_name, '') != COALESCE(prev_app_name, '')
                  OR COALESCE(window_title, '') != COALESCE(prev_window_title, '')
                  OR COALESCE(browser_domain, '') != COALESCE(prev_browser_domain, '')
              )
            GROUP BY day
            """,
            (start_iso,),
        )
        daily_switches = {row["day"]: row["switches"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        cursor.execute(
            """
            SELECT substr(start_time, 1, 10) as day,
                   SUM(CASE WHEN is_idle = 0 THEN context_switches ELSE 0 END) as switches
            FROM activity_sessions
            WHERE start_time >= ?
            GROUP BY day
            """,
            (start_iso,),
        )
        daily_switches = {row["day"]: row["switches"] or 0 for row in cursor.fetchall()}

    daily_trend = []
    for row in daily_rows:
        day_value = row["day"]
        active_seconds = row["active_seconds"] or 0
        switches = daily_switches.get(day_value, 0)
        meeting_day_seconds = row["meeting_seconds"] or 0
        daily_trend.append(
            {
                "day": day_value,
                "day_label": datetime.strptime(day_value, "%Y-%m-%d").strftime("%a"),
                "active_hours": round(active_seconds / 3600, 2),
                "meeting_hours": round(meeting_day_seconds / 3600, 2),
                "switches_per_hour": round(
                    switches / max(active_seconds / 3600, 0.001), 2
                ),
            }
        )

    conn.close()

    active_hours = total_seconds / 3600 if total_seconds else 0
    tagged_ratio = (tagged_active_seconds / total_seconds) if total_seconds else 0
    meeting_ratio = (meeting_seconds / total_seconds) if total_seconds else 0
    switch_rate_per_hour = total_switches / active_hours if active_hours else 0
    goal_drift = _build_goal_drift_summary(tag_stats, total_seconds)

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
        "goal_drift": goal_drift,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    source: str = Query("local"),
    user_id: str | None = Query(None),
    device_id: str | None = Query(None),
):
    """Dashboard homepage."""
    db_path = get_db_path()

    if not db_path.exists():
        return "<h1>WorkGraph Dashboard</h1><p>No data collected yet. Run the collector first.</p>"

    dashboard_mode = _configured_dashboard_mode()
    if dashboard_mode == "sync-server":
        overview = get_sync_server_overview(
            db_path,
            user_id=user_id,
            device_id=device_id,
            days=7,
        )
        sync_health = get_sync_health(db_path)
        selected_user = next(
            (
                item
                for item in overview.get("users", [])
                if str(item.get("id")) == str(overview.get("selected_user_id") or "")
            ),
            None,
        )
        selected_device = next(
            (
                item
                for item in overview.get("devices", [])
                if str(item.get("id")) == str(overview.get("selected_device_id") or "")
            ),
            None,
        )
        template = jinja_env.get_template("sync_server_dashboard.html")
        return template.render(
            dashboard_mode=dashboard_mode,
            overview=overview,
            sync_health=sync_health,
            page_context=_build_dashboard_header_context(
                dashboard_mode=dashboard_mode,
                source="sync",
                days=7,
                selected_user_id=str(overview.get("selected_user_id") or "") or None,
                selected_device_id=str(overview.get("selected_device_id") or "")
                or None,
                selected_user_name=str(
                    selected_user.get("name") or selected_user.get("id") or ""
                )
                if selected_user
                else None,
                selected_device_name=str(
                    selected_device.get("name") or selected_device.get("id") or ""
                )
                if selected_device
                else None,
            ),
        )

    normalized_source = _source_for_dashboard_mode(dashboard_mode)
    resolved_user_id = user_id
    client_sync_status: dict[str, object] | None = None
    client_sync_debug: dict[str, object] | None = None
    if dashboard_mode == "sync-client":
        client_sync_status = _configured_sync_client_status(db_path)
        if resolved_user_id is None and client_sync_status.get("user_id"):
            resolved_user_id = str(client_sync_status["user_id"])
        client_sync_debug = get_sync_client_debug(
            db_path,
            user_id=resolved_user_id,
            device_id=str(client_sync_status.get("device_id") or "").strip() or None,
        )

    if normalized_source == "sync":
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _ensure_sync_tables(conn)
        resolved_user_id = _resolve_sync_user_id(conn, user_id)
        conn.close()
        stats = get_sync_summary_stats(
            db_path,
            user_id=resolved_user_id,
            device_id=device_id,
            days=7,
        )
    else:
        stats = get_summary_stats(db_path, days=7)
    sync_health = get_sync_health(db_path)

    template = jinja_env.get_template("dashboard.html")
    return template.render(
        stats=stats,
        sync_health=sync_health,
        client_sync=client_sync_status,
        client_sync_debug=client_sync_debug,
        dashboard_mode=dashboard_mode,
        source=normalized_source,
        selected_user_id=resolved_user_id,
        selected_device_id=device_id,
        page_context=_build_dashboard_header_context(
            dashboard_mode=dashboard_mode,
            source=normalized_source,
            days=7,
            selected_user_id=resolved_user_id,
            selected_device_id=device_id,
            selected_user_name=str(client_sync_status.get("user_id") or "")
            if client_sync_status
            else resolved_user_id,
            selected_device_name=str(client_sync_status.get("device_id") or "")
            if client_sync_status
            else device_id,
        ),
    )


@app.get("/timeline", response_class=HTMLResponse)
async def timeline(
    days: int = Query(7, ge=1, le=30),
    tag: str | None = Query(None),
    app: str | None = Query(None),
    source: str | None = Query(None),
    user_id: str | None = Query(None),
    device_id: str | None = Query(None),
):
    """Timeline view of activities."""
    db_path = get_db_path()

    if not db_path.exists():
        return "<h1>WorkGraph Timeline</h1><p>No data collected yet.</p>"

    dashboard_mode = _configured_dashboard_mode()

    if dashboard_mode == "sync-server":
        overview = get_sync_server_overview(
            db_path,
            user_id=user_id,
            device_id=device_id,
            days=days,
        )
        resolved_user_id = str(overview.get("selected_user_id") or "").strip() or None
        resolved_device_id = (
            str(overview.get("selected_device_id") or "").strip() or None
        )

        start_date = datetime.now(UTC) - timedelta(days=days)
        if resolved_user_id:
            sessions = query_sync_sessions(
                db_path,
                user_id=resolved_user_id,
                start_date=start_date,
                device_id=resolved_device_id,
                limit=2000,
            )
        else:
            sessions = []

        available_tags = sorted({str(s.get("tag")) for s in sessions if s.get("tag")})
        available_apps = sorted(
            {str(s.get("app_name")) for s in sessions if s.get("app_name")}
        )

        if tag:
            sessions = [s for s in sessions if s.get("tag") == tag]
        if app:
            sessions = [s for s in sessions if s.get("app_name") == app]

        server_devices = overview.get("devices", [])
        device_name_map = {
            str(item.get("id")): str(
                item.get("name") or item.get("id") or "unknown-device"
            )
            for item in server_devices
            if item.get("id")
        }

        device_totals: dict[str, dict[str, int]] = {}
        app_totals: dict[str, int] = {}
        for session in sessions:
            raw_duration = session.get("duration_sec")
            try:
                duration_sec = int(raw_duration or 0)
            except (TypeError, ValueError):
                duration_sec = 0

            device_key = str(session.get("device_id") or "unknown-device")
            if device_key not in device_totals:
                device_totals[device_key] = {"total_seconds": 0, "session_count": 0}
            device_totals[device_key]["total_seconds"] += duration_sec
            device_totals[device_key]["session_count"] += 1

            app_key = str(session.get("app_name") or "Unknown")
            app_totals[app_key] = app_totals.get(app_key, 0) + duration_sec

        device_activity = [
            {
                "device_id": device_id,
                "device_name": device_name_map.get(device_id, device_id),
                "total_seconds": values["total_seconds"],
                "session_count": values["session_count"],
            }
            for device_id, values in device_totals.items()
        ]
        device_activity.sort(
            key=lambda item: (int(item["total_seconds"]), int(item["session_count"])),
            reverse=True,
        )

        app_activity = [
            {"app_name": app_name, "total_seconds": total_seconds}
            for app_name, total_seconds in app_totals.items()
        ]
        app_activity.sort(key=lambda item: int(item["total_seconds"]), reverse=True)

        chart_sessions = sorted(
            sessions,
            key=lambda s: str(s.get("start_time") or s.get("utc_start") or ""),
        )[:300]
        table_sessions = sessions[:200]
        sync_health = get_sync_health(db_path)

        template = jinja_env.get_template("sync_server_timeline.html")
        return template.render(
            sessions=table_sessions,
            filtered_count=len(sessions),
            days=days,
            selected_tag=tag,
            selected_app=app,
            selected_user_id=resolved_user_id,
            selected_device_id=resolved_device_id,
            available_tags=available_tags,
            available_apps=available_apps,
            chart_sessions=chart_sessions,
            sync_health=sync_health,
            dashboard_mode=dashboard_mode,
            server_users=overview.get("users", []),
            server_devices=server_devices,
            device_name_map=device_name_map,
            device_activity=device_activity[:10],
            app_activity=app_activity[:10],
            page_context=_build_dashboard_header_context(
                dashboard_mode=dashboard_mode,
                source="sync",
                days=days,
                selected_user_id=resolved_user_id,
                selected_device_id=resolved_device_id,
                selected_user_name=next(
                    (
                        str(item.get("name") or item.get("id") or "")
                        for item in overview.get("users", [])
                        if str(item.get("id")) == str(resolved_user_id or "")
                    ),
                    None,
                ),
                selected_device_name=device_name_map.get(resolved_device_id)
                if resolved_device_id
                else None,
            ),
        )

    default_source = _source_for_dashboard_mode(dashboard_mode)
    normalized_source = _normalize_source(source or default_source)
    start_date = datetime.now(UTC) - timedelta(days=days)
    if normalized_source == "sync":
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _ensure_sync_tables(conn)
        resolved_user_id = _resolve_sync_user_id(conn, user_id)
        conn.close()
        sessions = query_sync_sessions(
            db_path,
            user_id=resolved_user_id,
            start_date=start_date,
            device_id=device_id,
            limit=2000,
        )
    else:
        sessions = query_sessions(db_path, start_date=start_date, limit=2000)

    available_tags = sorted({str(s.get("tag")) for s in sessions if s.get("tag")})
    available_apps = sorted(
        {str(s.get("app_name")) for s in sessions if s.get("app_name")}
    )

    # Filter by tag/app if provided
    if tag:
        sessions = [s for s in sessions if s.get("tag") == tag]
    if app:
        sessions = [s for s in sessions if s.get("app_name") == app]

    chart_sessions = sorted(sessions, key=lambda s: str(s.get("start_time") or ""))[
        :300
    ]
    table_sessions = sessions[:120]
    sync_health = get_sync_health(db_path)

    template = jinja_env.get_template("timeline.html")
    return template.render(
        sessions=table_sessions,
        filtered_count=len(sessions),
        days=days,
        selected_tag=tag,
        selected_app=app,
        source=normalized_source,
        selected_user_id=user_id,
        selected_device_id=device_id,
        available_tags=available_tags,
        available_apps=available_apps,
        chart_sessions=chart_sessions,
        sync_health=sync_health,
        dashboard_mode=dashboard_mode,
        page_context=_build_dashboard_header_context(
            dashboard_mode=dashboard_mode,
            source=normalized_source,
            days=days,
            selected_user_id=user_id,
            selected_device_id=device_id,
            selected_user_name=user_id,
            selected_device_name=device_id,
        ),
    )


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(saved: str | None = Query(None)):
    """Journal view for adding entries and reflections."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()

    if not db_path.exists():
        return "<h1>WorkGraph Journal</h1><p>No database found yet. Run collector once first.</p>"

    entries = query_recent_journal_entries(db_path, limit=50)
    reflections = query_recent_reflections(db_path, limit=30)
    work_events = query_recent_work_events(db_path, limit=30)
    available_tags = get_available_tags()
    sync_health = get_sync_health(db_path)

    message = None
    if saved == "entry":
        message = "Journal entry saved."
    elif saved == "reflection":
        message = "Reflection saved."
    elif saved == "work-event":
        message = "Work event saved."

    template = jinja_env.get_template("journal.html")
    return template.render(
        entries=entries,
        reflections=reflections,
        work_events=work_events,
        available_tags=available_tags,
        work_event_types=WORK_EVENT_TYPES,
        work_event_impacts=WORK_EVENT_IMPACTS,
        sync_health=sync_health,
        message=message,
        error=None,
        dashboard_mode=_configured_dashboard_mode(),
    )


@app.get("/tag-review", response_class=HTMLResponse)
async def tag_review_page(
    days: int = Query(7, ge=1, le=3650),
    only_untagged: bool = Query(True),
    app_name: str | None = Query(None),
    domain: str | None = Query(None),
    repo: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    dashboard_mode = _configured_dashboard_mode()
    available_tags = get_available_tags()
    sync_health = get_sync_health(db_path) if db_path.exists() else None

    groups: list[dict[str, object]] = []
    group_count = 0
    if db_path.exists():
        from db.repository import ActivityRepository

        with ActivityRepository(db_path) as repository:
            groups = repository.list_tag_review_groups(
                days=days,
                only_untagged=only_untagged,
                app_name=app_name,
                domain=domain,
                repo=repo,
                limit=limit,
                offset=0,
            )
            group_count = repository.count_tag_review_groups(
                days=days,
                only_untagged=only_untagged,
                app_name=app_name,
                domain=domain,
                repo=repo,
            )

    suggestions, preview_text, warnings = _build_tag_review_payload(
        db_path=db_path,
        days=days,
        selected_tag=None,
        min_domain_hits=2,
        min_repo_hits=2,
    )

    template = jinja_env.get_template("tag_review.html")
    return template.render(
        groups=groups,
        group_count=group_count,
        days=days,
        only_untagged=only_untagged,
        filter_app_name=app_name,
        filter_domain=domain,
        filter_repo=repo,
        limit=limit,
        available_tags=available_tags,
        suggestions=suggestions,
        yaml_preview=preview_text,
        warnings=warnings,
        sync_health=sync_health,
        dashboard_mode=dashboard_mode,
        page_context=_build_dashboard_header_context(
            dashboard_mode=dashboard_mode,
            source="local",
            days=days,
        ),
    )


@app.get("/api/tag-review/groups")
async def api_tag_review_groups(
    days: int = Query(7, ge=1, le=3650),
    only_untagged: bool = Query(True),
    app_name: str | None = Query(None),
    domain: str | None = Query(None),
    repo: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()

    if not db_path.exists():
        return {
            "groups": [],
            "count": 0,
            "filters": {
                "days": days,
                "only_untagged": only_untagged,
                "app_name": app_name,
                "domain": domain,
                "repo": repo,
                "limit": limit,
                "offset": offset,
            },
        }

    from db.repository import ActivityRepository

    with ActivityRepository(db_path) as repository:
        groups = repository.list_tag_review_groups(
            days=days,
            only_untagged=only_untagged,
            app_name=app_name,
            domain=domain,
            repo=repo,
            limit=limit,
            offset=offset,
        )
        count = repository.count_tag_review_groups(
            days=days,
            only_untagged=only_untagged,
            app_name=app_name,
            domain=domain,
            repo=repo,
        )

    return {
        "groups": groups,
        "count": count,
        "filters": {
            "days": days,
            "only_untagged": only_untagged,
            "app_name": app_name,
            "domain": domain,
            "repo": repo,
            "limit": limit,
            "offset": offset,
        },
    }


@app.get("/api/tag-review/groups/{group_type}/{group_value}")
async def api_tag_review_group_detail(
    group_type: str,
    group_value: str,
    days: int = Query(7, ge=1, le=3650),
    only_untagged: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    if not db_path.exists():
        return {
            "group_type": group_type,
            "group_value": group_value,
            "sessions": [],
            "count": 0,
        }

    if group_type not in {"repo", "domain", "browser_context", "app"}:
        raise HTTPException(status_code=400, detail="Invalid group_type")

    from db.repository import ActivityRepository

    with ActivityRepository(db_path) as repository:
        sessions = [
            dict(row)
            for row in repository.list_tag_review_group_sessions(
                group_type=group_type,
                group_value=group_value,
                days=days,
                only_untagged=only_untagged,
                limit=limit,
            )
        ]

    total_seconds = sum(int(item.get("duration_sec") or 0) for item in sessions)
    return {
        "group_type": group_type,
        "group_value": group_value,
        "sessions": sessions,
        "count": len(sessions),
        "total_seconds": total_seconds,
    }


@app.post("/api/tag-review/assign-group")
async def api_tag_review_assign_group(payload: TagReviewGroupAssignRequest):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="No activity database found")

    from db.repository import ActivityRepository

    with ActivityRepository(db_path) as repository:
        sessions = repository.list_tag_review_group_sessions(
            group_type=payload.group_type,
            group_value=payload.group_value,
            days=payload.days,
            only_untagged=payload.only_untagged,
            limit=5000,
        )
        if not sessions:
            raise HTTPException(
                status_code=404, detail="No matching sessions found for group"
            )

        session_ids: list[int] = []
        for session in sessions:
            session_ids.append(int(session["id"]))
            repository.create_tag_review_action(
                session_id=int(session["id"]),
                original_tag=session["tag"],
                selected_tag=payload.selected_tag,
                reason=payload.reason,
                source_signal=payload.source_signal or payload.group_type,
            )
        affected_count = repository.update_session_tags(
            session_ids, payload.selected_tag
        )
        updated_sessions = [
            dict(repository.get_session(session_id))
            for session_id in session_ids[:20]
            if repository.get_session(session_id) is not None
        ]

    return {
        "ok": True,
        "group_type": payload.group_type,
        "group_value": payload.group_value,
        "selected_tag": payload.selected_tag,
        "affected_count": affected_count,
        "sessions": updated_sessions,
    }


@app.post("/api/tag-review/suggestions")
async def api_tag_review_suggestions(payload: TagReviewSuggestionRequest):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    suggestions, _, _ = _build_tag_review_payload(
        db_path=db_path,
        days=payload.days,
        selected_tag=payload.selected_tag,
        min_domain_hits=payload.min_domain_hits,
        min_repo_hits=payload.min_repo_hits,
    )
    return {
        "suggestions": suggestions,
        "count": len(suggestions),
    }


@app.get("/api/tag-review/yaml-preview")
async def api_tag_review_yaml_preview(
    days: int | None = Query(None, ge=1, le=3650),
    selected_tag: str | None = Query(None),
    min_domain_hits: int = Query(2, ge=1, le=100),
    min_repo_hits: int = Query(2, ge=1, le=100),
):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    suggestions, preview_text, warnings = _build_tag_review_payload(
        db_path=db_path,
        days=days,
        selected_tag=selected_tag,
        min_domain_hits=min_domain_hits,
        min_repo_hits=min_repo_hits,
    )
    return {
        "yaml": preview_text,
        "warnings": warnings,
        "suggestions": suggestions,
    }


@app.post("/api/tag-review/yaml-download")
async def api_tag_review_yaml_download(payload: TagReviewYamlRequest):
    _ensure_tag_review_features_enabled()
    db_path = get_db_path()
    _, preview_text, _ = _build_tag_review_payload(
        db_path=db_path,
        days=payload.days,
        selected_tag=payload.selected_tag,
        min_domain_hits=payload.min_domain_hits,
        min_repo_hits=payload.min_repo_hits,
    )
    return Response(
        content=preview_text,
        media_type="application/x-yaml",
        headers={
            "Content-Disposition": 'attachment; filename="tag-review-preview.yaml"'
        },
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

    start_date = datetime.now(UTC) - timedelta(days=days)
    sessions = query_sessions(db_path, start_date=start_date, limit=limit)

    return {
        "sessions": sessions,
        "count": len(sessions),
    }


@app.get("/api/stats")
async def api_stats(
    days: int = Query(7, ge=1, le=30),
    source: str | None = Query(None),
    user_id: str | None = Query(None),
    device_id: str | None = Query(None),
):
    """API endpoint for summary statistics."""
    db_path = get_db_path()

    if not db_path.exists():
        return {
            "tag_stats": {},
            "app_stats": {},
            "total_seconds": 0,
            "total_hours": 0,
        }

    dashboard_mode = _configured_dashboard_mode()
    if source is not None:
        dashboard_mode = _normalize_dashboard_mode(None, source)
    normalized_source = _source_for_dashboard_mode(dashboard_mode)
    if normalized_source == "local":
        return get_summary_stats(db_path, days=days)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    resolved_user_id = _resolve_sync_user_id(conn, user_id)
    conn.close()

    return get_sync_summary_stats(
        db_path,
        user_id=resolved_user_id,
        device_id=device_id,
        days=days,
    )


@app.post("/api/device/register")
async def register_device_for_mode(payload: DeviceRegisterRequest):
    mode = _normalize_dashboard_mode(payload.mode)
    register_payload = SyncRegisterRequest(user=payload.user, device=payload.device)

    if mode == "sync-client":
        server_response = _register_on_sync_server(
            str(payload.sync_base_url or ""), register_payload
        )
        return {
            "mode": mode,
            "registered": True,
            "sync_base_url": str(payload.sync_base_url or "").strip(),
            "device_token": server_response.get("device_token"),
            "server_time": server_response.get("server_time"),
            "message": "Device registered on sync server. Save device_token as sync_token in client config.",
        }

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    started = time.perf_counter()

    issue_token = mode == "sync-server"
    token, now_iso = _upsert_sync_user_and_device(
        conn, register_payload, issue_token=issue_token
    )

    _log_sync_request(
        conn,
        endpoint="/api/device/register",
        status_code=200,
        latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        device_id=payload.device.id,
        user_id=payload.user.id,
    )
    conn.commit()
    conn.close()

    return {
        "mode": mode,
        "registered": True,
        "device_token": token,
        "server_time": now_iso,
        "message": (
            "Device registered for local standalone tracking."
            if mode == "standalone"
            else "Device registered on this sync server."
        ),
    }


@app.get("/api/sync/health")
async def api_sync_health():
    db_path = get_db_path()
    if not db_path.exists():
        return {
            "enabled": False,
            "active_tokens": 0,
            "registered_devices": 0,
            "last_seen_at": None,
            "last_sync_at": None,
            "synced_devices": 0,
            "unsynced_devices": 0,
            "pending_pull_rows": 0,
            "pending_sessions": 0,
            "pending_journal_entries": 0,
            "pending_reflections": 0,
            "pending_upload_failures": 0,
            "sync_failures": 0,
            "last_error_at": None,
            "recent_errors": [],
            "device_statuses": [],
        }
    return get_sync_health(db_path)


@app.get("/api/sync/errors")
async def api_sync_errors(limit: int = Query(20, ge=1, le=200)):
    db_path = get_db_path()
    if not db_path.exists():
        return {"errors": [], "count": 0}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    try:
        errors = _recent_sync_errors(conn, limit=limit)
    finally:
        conn.close()

    return {
        "errors": errors,
        "count": len(errors),
    }


@app.get("/api/sync/users")
async def api_sync_users(user_id: str | None = Query(None)):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    query = "SELECT id, name, created_at, updated_at FROM sync_users"
    params: list[str] = []
    if user_id:
        query += " WHERE id = ?"
        params.append(user_id)
    query += " ORDER BY updated_at DESC, id ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "users": [
            {
                "id": row["id"],
                "display_name": row["name"],
                "created_at_utc": row["created_at"],
                "updated_at_utc": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.get("/api/sync/devices")
async def api_sync_devices(user_id: str = Query(..., min_length=1)):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    rows = conn.execute(
        """
        SELECT id, user_id, name, type, hostname, category, last_seen_at, created_at, updated_at
        FROM sync_devices
        WHERE user_id = ?
        ORDER BY COALESCE(last_seen_at, updated_at) DESC, id ASC
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    return {
        "devices": [
            {
                "device_id": row["id"],
                "user_id": row["user_id"],
                "device_name": row["name"],
                "device_type": row["type"],
                "platform": row["category"],
                "hostname": row["hostname"],
                "last_seen_utc": row["last_seen_at"],
                "created_at_utc": row["created_at"],
                "updated_at_utc": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.get("/api/sync/stats")
async def api_sync_stats(
    user_id: str = Query(..., min_length=1),
    days: int = Query(7, ge=1, le=3650),
    device_id: str | None = Query(None),
):
    db_path = get_db_path()
    if not db_path.exists():
        return {
            "tag_stats": {},
            "app_stats": {},
            "total_seconds": 0,
            "total_hours": 0,
        }

    return get_sync_summary_stats(
        db_path,
        user_id=user_id,
        device_id=device_id,
        days=days,
    )


@app.post("/api/sync/rollups/rebuild")
async def api_sync_rollups_rebuild(
    user_id: str = Query(..., min_length=1),
    device_id: str | None = Query(None),
    start_day: str | None = Query(None),
):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)

    rebuilt_rows = _rebuild_sync_rollups(
        conn,
        user_id=user_id,
        device_id=device_id,
        start_day=start_day,
    )
    conn.commit()
    conn.close()

    return {
        "user_id": user_id,
        "device_id": device_id,
        "start_day": start_day,
        "rebuilt_rows": rebuilt_rows,
    }


@app.post("/api/journal")
async def create_journal_entry(payload: JournalCreate):
    """Create a journal entry with optional historical timestamps."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    start_iso = _to_utc_iso(payload.start_time)
    end_iso = _to_utc_iso(payload.end_time)
    if start_iso and end_iso and end_iso < start_iso:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO journal_entries (
            created_at,
            start_time,
            end_time,
            title,
            notes,
            metadata
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now_iso,
            start_iso,
            end_iso,
            payload.title,
            payload.notes,
            json.dumps(payload.metadata) if payload.metadata is not None else None,
        ),
    )
    conn.commit()
    entry_id = int(cursor.lastrowid)
    conn.close()

    return {"id": entry_id}


@app.get("/api/journal")
async def list_journal_entries(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=500),
):
    """List journal entries with optional range filtering."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    query = "SELECT * FROM journal_entries WHERE 1=1"
    params: list[str | int] = []

    if from_ts:
        query += " AND COALESCE(start_time, created_at) >= ?"
        params.append(_to_utc_iso(from_ts) or from_ts)
    if to_ts:
        query += " AND COALESCE(end_time, start_time, created_at) <= ?"
        params.append(_to_utc_iso(to_ts) or to_ts)

    query += " ORDER BY COALESCE(start_time, created_at) DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = [_serialize_journal_row(row) for row in cursor.fetchall()]
    conn.close()
    return {"entries": rows, "count": len(rows)}


@app.get("/api/journal/tags")
async def list_journal_tags():
    """List configured tags available for journal tagging."""
    _ensure_journal_features_enabled()
    return {"tags": get_available_tags()}


@app.get("/api/work-events/types")
async def list_work_event_types():
    _ensure_journal_features_enabled()
    return {"types": WORK_EVENT_TYPES, "impacts": WORK_EVENT_IMPACTS}


@app.post("/api/work-events")
async def create_work_event(payload: WorkEventCreate):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()
    user_id, device_id = _work_event_sync_identity(conn)

    event_time_iso = _to_utc_iso(payload.event_time)
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO work_events (
            uuid,
            user_id,
            device_id,
            created_at,
            event_time,
            event_type,
            title,
            impact,
            project,
            notes,
            metadata,
            updated_at,
            deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            user_id,
            device_id,
            now_iso,
            event_time_iso,
            payload.event_type,
            payload.title,
            payload.impact,
            payload.project,
            payload.notes,
            json.dumps(payload.metadata) if payload.metadata is not None else None,
            now_iso,
            None,
        ),
    )
    conn.commit()
    event_id = int(cursor.lastrowid)
    conn.close()
    return {"id": event_id}


@app.get("/api/work-events")
async def list_work_events(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    event_type: str | None = Query(None),
    impact: str | None = Query(None),
    project: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    query = "SELECT * FROM work_events WHERE 1=1"
    params: list[str | int] = []
    query += " AND deleted_at IS NULL"

    if from_ts:
        query += " AND COALESCE(event_time, created_at) >= ?"
        params.append(_to_utc_iso(from_ts) or from_ts)
    if to_ts:
        query += " AND COALESCE(event_time, created_at) <= ?"
        params.append(_to_utc_iso(to_ts) or to_ts)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if impact:
        query += " AND impact = ?"
        params.append(impact)
    if project:
        query += " AND project = ?"
        params.append(project)

    query += " ORDER BY COALESCE(event_time, created_at) DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = [_serialize_work_event_row(row) for row in cursor.fetchall()]
    conn.close()
    return {"events": rows, "count": len(rows)}


@app.get("/api/work-events/{event_id}")
async def get_work_event(event_id: int):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)

    row = conn.execute(
        "SELECT * FROM work_events WHERE id = ? AND deleted_at IS NULL",
        (event_id,),
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Work event not found")

    return {"event": _serialize_work_event_row(row)}


@app.put("/api/work-events/{event_id}")
async def update_work_event(event_id: int, payload: WorkEventCreate):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM work_events WHERE id = ?", (event_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Work event not found")

    event_time_iso = _to_utc_iso(payload.event_time)
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    cursor.execute(
        """
        UPDATE work_events
        SET event_time = ?,
            event_type = ?,
            title = ?,
            impact = ?,
            project = ?,
            notes = ?,
            metadata = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            event_time_iso,
            payload.event_type,
            payload.title,
            payload.impact,
            payload.project,
            payload.notes,
            json.dumps(payload.metadata) if payload.metadata is not None else None,
            now_iso,
            event_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"id": event_id, "updated": True}


@app.delete("/api/work-events/{event_id}")
async def delete_work_event(event_id: int):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    row = cursor.execute(
        "SELECT id FROM work_events WHERE id = ? AND deleted_at IS NULL",
        (event_id,),
    ).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Work event not found")

    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    cursor.execute(
        """
        UPDATE work_events
        SET deleted_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (now_iso, now_iso, event_id),
    )
    conn.commit()
    conn.close()
    return {"id": event_id, "deleted": True}


@app.get("/api/work-events/{event_id}/correlated-sessions")
async def correlated_work_event_sessions(
    event_id: int, limit: int = Query(500, ge=1, le=1000)
):
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)

    event_row = conn.execute(
        "SELECT * FROM work_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if event_row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Work event not found")

    event = _serialize_work_event_row(event_row)
    point_time = event.get("event_time") or event.get("created_at")
    if not point_time:
        conn.close()
        raise HTTPException(status_code=400, detail="Work event has no timestamp")

    point_dt = datetime.fromisoformat(str(point_time).replace("Z", "+00:00"))
    end_dt = point_dt + timedelta(seconds=1)
    start_time = point_dt.isoformat(timespec="seconds")
    end_time = end_dt.isoformat(timespec="seconds")

    sessions = _query_overlapping_activity_sessions(
        conn,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    summary = _build_correlation_summary(sessions)
    conn.close()

    return {
        "event_id": event_id,
        "range": {"start_time": start_time, "end_time": start_time},
        "summary": summary,
        "sessions": sessions,
    }


@app.get("/api/journal/{journal_id}")
async def get_journal_entry(journal_id: int):
    """Get one journal entry by id."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM journal_entries WHERE id = ?",
        (journal_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    return {"entry": _serialize_journal_row(row)}


@app.put("/api/journal/{journal_id}")
async def update_journal_entry(journal_id: int, payload: JournalCreate):
    """Update an existing journal entry."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM journal_entries WHERE id = ?", (journal_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Journal entry not found")

    start_iso = _to_utc_iso(payload.start_time)
    end_iso = _to_utc_iso(payload.end_time)
    if start_iso and end_iso and end_iso < start_iso:
        conn.close()
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    cursor.execute(
        """
        UPDATE journal_entries
        SET start_time = ?,
            end_time = ?,
            title = ?,
            notes = ?,
            metadata = ?
        WHERE id = ?
        """,
        (
            start_iso,
            end_iso,
            payload.title,
            payload.notes,
            json.dumps(payload.metadata) if payload.metadata is not None else None,
            journal_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"id": journal_id, "updated": True}


@app.get("/api/journal/{journal_id}/correlated-sessions")
async def correlated_sessions(journal_id: int, limit: int = Query(500, ge=1, le=1000)):
    """Fetch sessions overlapping a journal entry time range and a simple summary."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM journal_entries WHERE id = ?",
        (journal_id,),
    )
    entry = cursor.fetchone()
    if entry is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Journal entry not found")

    start_time = entry["start_time"]
    end_time = entry["end_time"]
    if not start_time:
        conn.close()
        raise HTTPException(status_code=400, detail="Journal entry has no start_time")
    if not end_time:
        end_time = start_time

    sessions = _query_overlapping_activity_sessions(
        conn,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    summary = _build_correlation_summary(sessions)

    conn.close()
    return {
        "journal_id": journal_id,
        "range": {"start_time": start_time, "end_time": end_time},
        "summary": summary,
        "sessions": sessions,
    }


@app.put("/api/reflections/{date}")
async def upsert_reflection(date: str, payload: ReflectionUpsert):
    """Create or update a reflection record for a date."""
    _ensure_journal_features_enabled()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO daily_reflections (
            date,
            wins,
            problems,
            tomorrow,
            energy,
            stress,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            wins = excluded.wins,
            problems = excluded.problems,
            tomorrow = excluded.tomorrow,
            energy = excluded.energy,
            stress = excluded.stress,
            updated_at = excluded.updated_at
        """,
        (
            date,
            payload.wins,
            payload.problems,
            payload.tomorrow,
            payload.energy,
            payload.stress,
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    conn.close()
    return {"date": date, "saved": True}


@app.get("/api/reflections/{date}")
async def get_reflection(date: str):
    _ensure_journal_features_enabled()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)

    row = conn.execute(
        "SELECT * FROM daily_reflections WHERE date = ?",
        (date,),
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Reflection not found")

    return {"reflection": dict(row)}


@app.get("/api/reflections/{date}/correlated-sessions")
async def correlated_reflection_sessions(
    date: str, limit: int = Query(500, ge=1, le=1000)
):
    _ensure_journal_features_enabled()
    try:
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)

    row = conn.execute(
        "SELECT id FROM daily_reflections WHERE date = ?",
        (date,),
    ).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Reflection not found")

    day_end = day_start + timedelta(days=1)
    start_time = day_start.isoformat(timespec="seconds")
    end_time = day_end.isoformat(timespec="seconds")

    sessions = _query_overlapping_activity_sessions(
        conn,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    summary = _build_correlation_summary(sessions)
    conn.close()

    return {
        "date": date,
        "range": {
            "start_time": start_time,
            "end_time": (day_end - timedelta(seconds=1)).isoformat(timespec="seconds"),
        },
        "summary": summary,
        "sessions": sessions,
    }


@app.get("/api/reflections")
async def list_reflections(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=500),
):
    """List reflections in date range."""
    _ensure_journal_features_enabled()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    query = "SELECT * FROM daily_reflections WHERE 1=1"
    params: list[str | int] = []

    if from_date:
        query += " AND date >= ?"
        params.append(from_date)
    if to_date:
        query += " AND date <= ?"
        params.append(to_date)

    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"reflections": rows, "count": len(rows)}


@app.post("/api/sync/v1/devices/register")
async def sync_register_device(payload: SyncRegisterRequest):
    started = time.perf_counter()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    token, now_iso = _upsert_sync_user_and_device(conn, payload, issue_token=True)
    _log_sync_request(
        conn,
        endpoint="/api/sync/v1/devices/register",
        status_code=200,
        latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        device_id=payload.device.id,
        user_id=payload.user.id,
    )
    conn.commit()
    conn.close()
    return {"device_token": token, "server_time": now_iso}


@app.post("/api/sync/v1/push")
async def sync_push_changes(
    payload: SyncPushRequest, authorization: str | None = Header(default=None)
):
    started = time.perf_counter()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    try:
        _require_sync_auth(
            conn,
            authorization=authorization,
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
        cursor = conn.cursor()

        raw_payload = payload.model_dump(mode="python")
        req_hash = _request_hash(raw_payload)

        cursor.execute(
            "SELECT request_hash, response_json FROM sync_batches WHERE batch_id = ?",
            (payload.batch_id,),
        )
        existing_batch = cursor.fetchone()
        if existing_batch is not None:
            if existing_batch["request_hash"] != req_hash:
                raise HTTPException(
                    status_code=409,
                    detail="batch_id already exists with different payload",
                )
            response_json = existing_batch["response_json"]
            replay_response = json.loads(response_json)
            accepted = replay_response.get("accepted", {})
            _log_sync_request(
                conn,
                endpoint="/api/sync/v1/push",
                status_code=200,
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                device_id=payload.device_id,
                user_id=payload.user_id,
                batch_id=payload.batch_id,
                accepted_count=int(
                    sum(
                        int(accepted.get(k, 0))
                        for k in ["sessions", "journal_entries", "daily_reflections"]
                    )
                ),
                conflict_count=int(len(replay_response.get("conflicts", []))),
            )
            conn.commit()
            return replay_response

        sessions = payload.changes.get("sessions") or []
        journal_entries = payload.changes.get("journal_entries") or []
        reflections = payload.changes.get("daily_reflections") or []

        cursors: list[tuple[str, str]] = []
        for row in sessions:
            updated_at, row_uuid = _upsert_sync_payload_row(
                conn,
                table_name="sync_sessions",
                user_id=payload.user_id,
                device_id=payload.device_id,
                payload=row,
            )
            if updated_at and row_uuid:
                cursors.append((updated_at, row_uuid))
        for row in journal_entries:
            updated_at, row_uuid = _upsert_sync_payload_row(
                conn,
                table_name="sync_journal_entries",
                user_id=payload.user_id,
                device_id=payload.device_id,
                payload=row,
            )
            if updated_at and row_uuid:
                cursors.append((updated_at, row_uuid))
        for row in reflections:
            updated_at, row_uuid = _upsert_sync_payload_row(
                conn,
                table_name="sync_daily_reflections",
                user_id=payload.user_id,
                device_id=payload.device_id,
                payload=row,
            )
            if updated_at and row_uuid:
                cursors.append((updated_at, row_uuid))

        now_iso = _now_iso()
        next_push_cursor = payload.client_cursor
        if cursors:
            max_updated_at, max_uuid = max(cursors)
            next_push_cursor = _build_cursor(max_updated_at, max_uuid)

        response_body = {
            "accepted": {
                "sessions": len(sessions),
                "journal_entries": len(journal_entries),
                "daily_reflections": len(reflections),
            },
            "conflicts": [],
            "next_push_cursor": next_push_cursor,
            "server_time": now_iso,
        }

        cursor.execute(
            """
            INSERT INTO sync_checkpoints (
                device_id,
                user_id,
                last_push_cursor,
                last_pull_cursor,
                updated_at
            )
            VALUES (
                ?,
                ?,
                ?,
                COALESCE((SELECT last_pull_cursor FROM sync_checkpoints WHERE device_id = ?), NULL),
                ?
            )
            ON CONFLICT(device_id) DO UPDATE SET
                user_id = excluded.user_id,
                last_push_cursor = excluded.last_push_cursor,
                updated_at = excluded.updated_at
            """,
            (
                payload.device_id,
                payload.user_id,
                next_push_cursor,
                payload.device_id,
                now_iso,
            ),
        )

        cursor.execute(
            """
            INSERT INTO sync_batches (
                batch_id,
                device_id,
                user_id,
                request_hash,
                response_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.batch_id,
                payload.device_id,
                payload.user_id,
                req_hash,
                json.dumps(response_body, separators=(",", ":")),
                now_iso,
            ),
        )

        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/push",
            status_code=200,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
            batch_id=payload.batch_id,
            accepted_count=len(sessions) + len(journal_entries) + len(reflections),
            conflict_count=0,
        )

        conn.commit()
        return response_body
    except HTTPException as exc:
        _log_sync_error(
            conn,
            endpoint="/api/sync/v1/push",
            error_type="http_error",
            message=str(exc.detail),
            status_code=exc.status_code,
            device_id=payload.device_id,
            user_id=payload.user_id,
            batch_id=payload.batch_id,
        )
        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/push",
            status_code=exc.status_code,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
            batch_id=payload.batch_id,
            conflict_count=1 if exc.status_code == 409 else 0,
        )
        conn.commit()
        raise
    except Exception as exc:
        _log_sync_error(
            conn,
            endpoint="/api/sync/v1/push",
            error_type=exc.__class__.__name__,
            message=str(exc),
            status_code=500,
            device_id=payload.device_id,
            user_id=payload.user_id,
            batch_id=payload.batch_id,
        )
        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/push",
            status_code=500,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
            batch_id=payload.batch_id,
        )
        conn.commit()
        raise
    finally:
        conn.close()


@app.post("/api/sync/v1/pull")
async def sync_pull_changes(
    payload: SyncPullRequest, authorization: str | None = Header(default=None)
):
    started = time.perf_counter()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    try:
        _require_sync_auth(
            conn,
            authorization=authorization,
            device_id=payload.device_id,
            user_id=payload.user_id,
        )

        rows = _list_sync_union_rows(
            conn,
            user_id=payload.user_id,
            cursor_value=payload.cursor,
            limit=payload.limit,
        )
        active_sessions: list[dict] = []
        active_journal_entries: list[dict] = []
        active_daily_reflections: list[dict] = []
        tombstones: list[dict] = []

        for row in rows:
            entity = str(row["entity"])
            row_id = str(row["id"])
            updated_at = row["updated_at"]
            deleted_at = row["deleted_at"]
            if deleted_at:
                tombstones.append(
                    {
                        "entity": entity,
                        "id": row_id,
                        "deleted_at": deleted_at,
                        "updated_at": updated_at,
                    }
                )
                continue

            payload_json = row["payload_json"]
            if not payload_json:
                continue
            item = json.loads(payload_json)
            if entity == "sessions":
                active_sessions.append(item)
            elif entity == "journal_entries":
                active_journal_entries.append(item)
            elif entity == "daily_reflections":
                active_daily_reflections.append(item)

        next_cursor = payload.cursor
        if rows:
            last_row = rows[-1]
            next_cursor = _build_ingest_seq_cursor(int(last_row["ingest_seq"] or 0))
            if next_cursor is None:
                next_cursor = _build_cursor(last_row["updated_at"], last_row["id"])

        total_after_cursor = _count_sync_union_rows_after_cursor(
            conn,
            user_id=payload.user_id,
            cursor_value=payload.cursor,
        )
        has_more = total_after_cursor > len(rows)

        now_iso = _now_iso()
        conn.execute(
            """
            INSERT INTO sync_checkpoints (
                device_id,
                user_id,
                last_push_cursor,
                last_pull_cursor,
                updated_at
            )
            VALUES (
                ?,
                ?,
                COALESCE((SELECT last_push_cursor FROM sync_checkpoints WHERE device_id = ?), NULL),
                ?,
                ?
            )
            ON CONFLICT(device_id) DO UPDATE SET
                user_id = excluded.user_id,
                last_pull_cursor = excluded.last_pull_cursor,
                updated_at = excluded.updated_at
            """,
            (
                payload.device_id,
                payload.user_id,
                payload.device_id,
                next_cursor,
                now_iso,
            ),
        )

        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/pull",
            status_code=200,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
            accepted_count=len(active_sessions)
            + len(active_journal_entries)
            + len(active_daily_reflections),
            has_more=has_more,
        )
        conn.commit()

        return {
            "changes": {
                "sessions": active_sessions,
                "journal_entries": active_journal_entries,
                "daily_reflections": active_daily_reflections,
                "tombstones": tombstones,
            },
            "next_cursor": next_cursor,
            "has_more": has_more,
            "server_time": now_iso,
        }
    except HTTPException as exc:
        _log_sync_error(
            conn,
            endpoint="/api/sync/v1/pull",
            error_type="http_error",
            message=str(exc.detail),
            status_code=exc.status_code,
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/pull",
            status_code=exc.status_code,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
        conn.commit()
        raise
    except Exception as exc:
        _log_sync_error(
            conn,
            endpoint="/api/sync/v1/pull",
            error_type=exc.__class__.__name__,
            message=str(exc),
            status_code=500,
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
        _log_sync_request(
            conn,
            endpoint="/api/sync/v1/pull",
            status_code=500,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("WORKGRAPH_API_HOST", "127.0.0.1")
    port = int(os.getenv("WORKGRAPH_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
