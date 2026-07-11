"""FastAPI web server for WorkGraph dashboard."""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator
import sqlite3

from services.activity_tagger import ActivityTagger

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


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
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
            created_at TEXT NOT NULL,
            event_time TEXT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            impact TEXT,
            project TEXT,
            notes TEXT,
            metadata TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_work_events_event_time
            ON work_events (event_time);

        CREATE INDEX IF NOT EXISTS idx_work_events_type
            ON work_events (event_type);

        CREATE INDEX IF NOT EXISTS idx_work_events_impact
            ON work_events (impact);
        """
    )
    conn.commit()


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

        CREATE TABLE IF NOT EXISTS sync_sessions (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_journal_entries (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
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
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sync_sessions_user_updated
            ON sync_sessions (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_journal_user_updated
            ON sync_journal_entries (user_id, updated_at, uuid);

        CREATE INDEX IF NOT EXISTS idx_sync_reflections_user_updated
            ON sync_daily_reflections (user_id, updated_at, uuid);

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

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_endpoint_created
            ON sync_request_logs (endpoint, created_at);

        CREATE INDEX IF NOT EXISTS idx_sync_request_logs_device_created
            ON sync_request_logs (device_id, created_at);
        """
    )
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

    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


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
            raise ValueError(f"event_type must be one of: {', '.join(WORK_EVENT_TYPES)}")
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _request_hash(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    if "|" not in cursor:
        return cursor, ""
    updated_at, row_uuid = cursor.split("|", 1)
    return updated_at or None, row_uuid or ""


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
        raise HTTPException(status_code=401, detail="Authorization must be Bearer token")
    token = authorization[len(prefix):].strip()
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

    updated_at = str(payload.get("updated_at") or payload.get("created_at") or _now_iso())
    created_at = str(payload.get("created_at") or updated_at)
    deleted_at = payload.get("deleted_at")
    date_value = payload.get("date") if table_name == "sync_daily_reflections" else None

    cursor = conn.cursor()
    cursor.execute(
        f"SELECT uuid, updated_at FROM {table_name} WHERE uuid = ?",
        (row_uuid,),
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

        cursor.execute(
            f"""
            UPDATE {table_name}
            SET user_id = ?,
                device_id = ?,
                created_at = ?,
                updated_at = ?,
                deleted_at = ?,
                payload_json = ?,
                date = COALESCE(?, date)
            WHERE uuid = ?
            """
            if table_name == "sync_daily_reflections"
            else f"""
            UPDATE {table_name}
            SET user_id = ?,
                device_id = ?,
                created_at = ?,
                updated_at = ?,
                deleted_at = ?,
                payload_json = ?
            WHERE uuid = ?
            """,
            (
                user_id,
                device_id,
                created_at,
                updated_at,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
                date_value,
                row_uuid,
            )
            if table_name == "sync_daily_reflections"
            else (
                user_id,
                device_id,
                created_at,
                updated_at,
                deleted_at,
                json.dumps(payload, separators=(",", ":")),
                row_uuid,
            ),
        )
        return updated_at, row_uuid

    cursor.execute(
        f"""
        INSERT INTO {table_name} (
            uuid,
            user_id,
            device_id,
            date,
            created_at,
            updated_at,
            deleted_at,
            payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        if table_name == "sync_daily_reflections"
        else f"""
        INSERT INTO {table_name} (
            uuid,
            user_id,
            device_id,
            created_at,
            updated_at,
            deleted_at,
            payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_uuid,
            user_id,
            device_id,
            date_value,
            created_at,
            updated_at,
            deleted_at,
            json.dumps(payload, separators=(",", ":")),
        )
        if table_name == "sync_daily_reflections"
        else (
            row_uuid,
            user_id,
            device_id,
            created_at,
            updated_at,
            deleted_at,
            json.dumps(payload, separators=(",", ":")),
        ),
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
    cursor_value: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = """
        SELECT entity, id, updated_at, deleted_at, payload_json
        FROM (
            SELECT 'sessions' AS entity, uuid AS id, updated_at, deleted_at, payload_json
            FROM sync_sessions
            UNION ALL
            SELECT 'journal_entries' AS entity, uuid AS id, updated_at, deleted_at, payload_json
            FROM sync_journal_entries
            UNION ALL
            SELECT 'daily_reflections' AS entity, uuid AS id, updated_at, deleted_at, payload_json
            FROM sync_daily_reflections
        )
        WHERE 1=1
    """
    params: list[str | int] = []
    if updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND id > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])
    query += " ORDER BY updated_at ASC, id ASC LIMIT ?"
    params.append(limit)
    return list(conn.execute(query, params).fetchall())


def _count_sync_union_rows_after_cursor(
    conn: sqlite3.Connection,
    *,
    cursor_value: str | None,
) -> int:
    updated_at_cursor, uuid_cursor = _parse_cursor(cursor_value)
    query = """
        SELECT COUNT(*) AS total_rows
        FROM (
            SELECT uuid AS id, updated_at FROM sync_sessions
            UNION ALL
            SELECT uuid AS id, updated_at FROM sync_journal_entries
            UNION ALL
            SELECT uuid AS id, updated_at FROM sync_daily_reflections
        )
        WHERE 1=1
    """
    params: list[str] = []
    if updated_at_cursor is not None:
        query += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND id > ?)
              )
        """
        params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

    row = conn.execute(query, params).fetchone()
    return int(row["total_rows"] if row is not None else 0)


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
            d.name AS device_name,
            d.type AS device_type,
            d.category AS device_category,
            d.last_seen_at AS last_seen_at,
            c.updated_at AS last_sync_at
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
    conn.close()

    active_tokens = int(token_row["active_tokens"] if token_row is not None else 0)
    registered_devices = int(device_row["registered_devices"] if device_row is not None else 0)
    push_total = int(metrics_row["push_total"] if metrics_row and metrics_row["push_total"] is not None else 0)
    push_success = int(metrics_row["push_success"] if metrics_row and metrics_row["push_success"] is not None else 0)
    push_conflicts = int(metrics_row["push_conflicts"] if metrics_row and metrics_row["push_conflicts"] is not None else 0)
    pull_total = int(metrics_row["pull_total"] if metrics_row and metrics_row["pull_total"] is not None else 0)
    pull_success = int(metrics_row["pull_success"] if metrics_row and metrics_row["pull_success"] is not None else 0)
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
    last_sync_at = checkpoint_row["last_sync_at"] if checkpoint_row is not None else None
    lag_seconds = None
    if last_sync_at:
        try:
            lag_seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(last_sync_at)).total_seconds()))
        except ValueError:
            lag_seconds = None

    device_statuses: list[dict[str, object]] = []
    synced_devices = 0
    for row in device_status_rows:
        device_last_sync = row["last_sync_at"]
        is_synced = bool(device_last_sync)
        if is_synced:
            synced_devices += 1
        device_statuses.append(
            {
                "device_id": row["device_id"],
                "device_name": row["device_name"],
                "device_type": row["device_type"],
                "device_category": row["device_category"],
                "last_seen_at": row["last_seen_at"],
                "last_sync_at": device_last_sync,
                "is_synced": is_synced,
            }
        )

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
        "last_request_at": metrics_row["last_request_at"] if metrics_row is not None else None,
        "synced_devices": synced_devices,
        "unsynced_devices": max(0, registered_devices - synced_devices),
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

    deep_focus_blocks = [block_seconds for block_seconds in focus_blocks if block_seconds >= 1800]
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
    sync_health = get_sync_health(db_path)

    template = jinja_env.get_template("dashboard.html")
    return template.render(stats=stats, sync_health=sync_health)


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
    sessions = query_sessions(db_path, start_date=start_date, limit=2000)

    available_tags = sorted({str(s.get("tag")) for s in sessions if s.get("tag")})
    available_apps = sorted({str(s.get("app_name")) for s in sessions if s.get("app_name")})

    # Filter by tag/app if provided
    if tag:
        sessions = [s for s in sessions if s.get("tag") == tag]
    if app:
        sessions = [s for s in sessions if s.get("app_name") == app]

    chart_sessions = sorted(sessions, key=lambda s: str(s.get("start_time") or ""))[:300]
    table_sessions = sessions[:120]
    sync_health = get_sync_health(db_path)

    template = jinja_env.get_template("timeline.html")
    return template.render(
        sessions=table_sessions,
        filtered_count=len(sessions),
        days=days,
        selected_tag=tag,
        selected_app=app,
        available_tags=available_tags,
        available_apps=available_apps,
        chart_sessions=chart_sessions,
        sync_health=sync_health,
    )


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(saved: str | None = Query(None)):
    """Journal view for adding entries and reflections."""
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
            "device_statuses": [],
        }
    return get_sync_health(db_path)


@app.post("/api/journal")
async def create_journal_entry(payload: JournalCreate):
    """Create a journal entry with optional historical timestamps."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    start_iso = _to_utc_iso(payload.start_time)
    end_iso = _to_utc_iso(payload.end_time)
    if start_iso and end_iso and end_iso < start_iso:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    return {"tags": get_available_tags()}


@app.get("/api/work-events/types")
async def list_work_event_types():
    return {"types": WORK_EVENT_TYPES, "impacts": WORK_EVENT_IMPACTS}


@app.post("/api/work-events")
async def create_work_event(payload: WorkEventCreate):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    event_time_iso = _to_utc_iso(payload.event_time)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO work_events (
            created_at,
            event_time,
            event_type,
            title,
            impact,
            project,
            notes,
            metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_iso,
            event_time_iso,
            payload.event_type,
            payload.title,
            payload.impact,
            payload.project,
            payload.notes,
            json.dumps(payload.metadata) if payload.metadata is not None else None,
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
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    query = "SELECT * FROM work_events WHERE 1=1"
    params: list[str | int] = []

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


@app.get("/api/journal/{journal_id}")
async def get_journal_entry(journal_id: int):
    """Get one journal entry by id."""
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

    # Fresh databases may have journals before any collector sessions.
    # Return an empty correlation instead of surfacing SQL errors.
    try:
        cursor.execute(
            """
            SELECT *
            FROM activity_sessions
            WHERE start_time < ?
              AND end_time > ?
            ORDER BY start_time ASC
            LIMIT ?
            """,
            (end_time, start_time, limit),
        )
        sessions = [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        sessions = []

    active_sessions = [s for s in sessions if not bool(s.get("is_idle"))]
    total_active_seconds = sum(int(s.get("duration_sec") or 0) for s in active_sessions)
    total_switches = sum(int(s.get("context_switches") or 0) for s in active_sessions)
    apps = sorted({str(s.get("app_name")) for s in active_sessions if s.get("app_name")})
    repos = sorted({str(s.get("git_repo")) for s in active_sessions if s.get("git_repo")})

    conn.close()
    return {
        "journal_id": journal_id,
        "range": {"start_time": start_time, "end_time": end_time},
        "summary": {
            "session_count": len(sessions),
            "active_session_count": len(active_sessions),
            "total_active_seconds": total_active_seconds,
            "total_active_hours": round(total_active_seconds / 3600, 2),
            "total_context_switches": total_switches,
            "apps": apps,
            "repos": repos,
        },
        "sessions": sessions,
    }


@app.put("/api/reflections/{date}")
async def upsert_reflection(date: str, payload: ReflectionUpsert):
    """Create or update a reflection record for a date."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_aux_tables(conn)
    cursor = conn.cursor()

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
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


@app.get("/api/reflections")
async def list_reflections(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=500),
):
    """List reflections in date range."""
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

    token = str(uuid4())
    cursor.execute(
        """
        INSERT INTO sync_tokens (token, device_id, user_id, created_at, revoked_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        (token, payload.device.id, payload.user.id, now_iso),
    )
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
async def sync_push_changes(payload: SyncPushRequest, authorization: str | None = Header(default=None)):
    started = time.perf_counter()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
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
            _log_sync_request(
                conn,
                endpoint="/api/sync/v1/push",
                status_code=409,
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                device_id=payload.device_id,
                user_id=payload.user_id,
                batch_id=payload.batch_id,
                conflict_count=1,
            )
            conn.commit()
            conn.close()
            raise HTTPException(status_code=409, detail="batch_id already exists with different payload")
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
            accepted_count=int(sum(int(accepted.get(k, 0)) for k in ["sessions", "journal_entries", "daily_reflections"])),
            conflict_count=int(len(replay_response.get("conflicts", []))),
        )
        conn.commit()
        conn.close()
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
        (payload.device_id, payload.user_id, next_push_cursor, payload.device_id, now_iso),
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
    conn.close()
    return response_body


@app.post("/api/sync/v1/pull")
async def sync_pull_changes(payload: SyncPullRequest, authorization: str | None = Header(default=None)):
    started = time.perf_counter()
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_sync_tables(conn)
    _require_sync_auth(
        conn,
        authorization=authorization,
        device_id=payload.device_id,
        user_id=payload.user_id,
    )

    rows = _list_sync_union_rows(conn, cursor_value=payload.cursor, limit=payload.limit)
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
        next_cursor = _build_cursor(last_row["updated_at"], last_row["id"])

    total_after_cursor = _count_sync_union_rows_after_cursor(conn, cursor_value=payload.cursor)
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
        (payload.device_id, payload.user_id, payload.device_id, next_cursor, now_iso),
    )

    _log_sync_request(
        conn,
        endpoint="/api/sync/v1/pull",
        status_code=200,
        latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        device_id=payload.device_id,
        user_id=payload.user_id,
        accepted_count=len(active_sessions) + len(active_journal_entries) + len(active_daily_reflections),
        has_more=has_more,
    )
    conn.commit()
    conn.close()

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


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("WORKGRAPH_API_HOST", "127.0.0.1")
    port = int(os.getenv("WORKGRAPH_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
