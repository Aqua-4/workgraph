from __future__ import annotations

import json
import platform
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid4, uuid5

from services.tag_review import (
    resolve_browser_tag_review_group,
    resolve_tag_review_group,
)
from workgraph.models import ActivitySession


class ActivityRepository:
    def __init__(
        self,
        db_path: str | Path = "activity.db",
        *,
        user_id: str | None = None,
        device_id: str | None = None,
        user_name: str | None = None,
        device_name: str | None = None,
        device_type: str | None = None,
        identity_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        identity = _load_or_create_identity(identity_path)
        self._user_id = user_id or identity["user_id"]
        self._device_id = device_id or identity["device_id"]
        self._user_name = user_name or identity["user_name"]
        self._device_name = device_name or identity["device_name"]
        self._device_type = device_type or identity["device_type"]
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        self._connection.executescript(schema_path.read_text(encoding="utf-8"))
        self._connection.commit()
        self._migrate()

    def _migrate(self) -> None:
        migrations = [
            "ALTER TABLE activity_sessions ADD COLUMN idle_seconds INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE activity_sessions ADD COLUMN git_repo TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_branch TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN context_switches INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE activity_sessions ADD COLUMN tag TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_commit_hash TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_modified_files TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN uuid TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN user_id TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN device_id TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN updated_at TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN deleted_at TEXT",
            "ALTER TABLE journal_entries ADD COLUMN uuid TEXT",
            "ALTER TABLE journal_entries ADD COLUMN user_id TEXT",
            "ALTER TABLE journal_entries ADD COLUMN device_id TEXT",
            "ALTER TABLE journal_entries ADD COLUMN updated_at TEXT",
            "ALTER TABLE journal_entries ADD COLUMN deleted_at TEXT",
            "ALTER TABLE daily_reflections ADD COLUMN uuid TEXT",
            "ALTER TABLE daily_reflections ADD COLUMN user_id TEXT",
            "ALTER TABLE daily_reflections ADD COLUMN device_id TEXT",
            "ALTER TABLE daily_reflections ADD COLUMN updated_at TEXT",
            "ALTER TABLE daily_reflections ADD COLUMN deleted_at TEXT",
            "ALTER TABLE work_events ADD COLUMN uuid TEXT",
            "ALTER TABLE work_events ADD COLUMN user_id TEXT",
            "ALTER TABLE work_events ADD COLUMN device_id TEXT",
            "ALTER TABLE work_events ADD COLUMN updated_at TEXT",
            "ALTER TABLE work_events ADD COLUMN deleted_at TEXT",
        ]
        for migration in migrations:
            try:
                self._connection.execute(migration)
                self._connection.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # Create git_activity table if it doesn't exist
        try:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS git_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    repo TEXT NOT NULL,
                    branch TEXT,
                    commit_hash TEXT,
                    file_name TEXT,
                    event_type TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES activity_sessions(id) ON DELETE CASCADE
                )
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_git_activity_session_id
                    ON git_activity (session_id)
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_git_activity_repo
                    ON git_activity (repo)
            """)
            self._connection.commit()
        except sqlite3.OperationalError:
            pass

        try:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    hostname TEXT,
                    category TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_devices_user_id
                    ON devices (user_id)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    device_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    last_push_cursor TEXT,
                    last_pull_cursor TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_sessions_uuid
                    ON activity_sessions (uuid)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_sessions_device_updated
                    ON activity_sessions (device_id, updated_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_sessions_user_updated
                    ON activity_sessions (user_id, updated_at)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_review_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    session_uuid TEXT,
                    original_tag TEXT,
                    selected_tag TEXT NOT NULL,
                    reason TEXT,
                    source_signal TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_to_rules INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES activity_sessions(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tag_review_actions_session_id
                    ON tag_review_actions (session_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tag_review_actions_selected_tag_created_at
                    ON tag_review_actions (selected_tag, created_at)
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_entries_uuid
                    ON journal_entries (uuid)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_journal_entries_user_updated
                    ON journal_entries (user_id, updated_at)
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_reflections_uuid
                    ON daily_reflections (uuid)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_reflections_user_date
                    ON daily_reflections (user_id, date)
                """
            )
            self._connection.commit()
        except sqlite3.OperationalError:
            pass

        try:
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_work_events_uuid
                    ON work_events (uuid)
                """
            )
            self._connection.commit()
        except sqlite3.OperationalError:
            pass

        self._initialize_identity()
        self._backfill_sync_metadata()

        # Create journal and reflection tables used by v1.2 features.
        try:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    title TEXT,
                    notes TEXT,
                    metadata TEXT
                )
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_entries_start_time
                    ON journal_entries (start_time)
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_entries_end_time
                    ON journal_entries (end_time)
            """)
            self._connection.execute("""
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
                )
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_reflections_date
                    ON daily_reflections (date)
            """)
            self._connection.execute("""
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
                )
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_work_events_event_time
                    ON work_events (event_time)
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_work_events_type
                    ON work_events (event_type)
            """)
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_work_events_impact
                    ON work_events (impact)
            """)
            self._connection.commit()
        except sqlite3.OperationalError:
            pass

    def save_session(self, session: ActivitySession) -> int:
        now = _format_datetime(datetime.now(UTC))
        cursor = self._connection.execute(
            """
            INSERT INTO activity_sessions (
                uuid,
                user_id,
                device_id,
                start_time,
                end_time,
                duration_sec,
                app_name,
                process_name,
                window_title,
                browser_domain,
                is_idle,
                idle_seconds,
                git_repo,
                git_branch,
                context_switches,
                tag,
                platform,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                self._user_id,
                self._device_id,
                _format_datetime(session.start_time),
                _format_datetime(session.end_time),
                session.duration_sec,
                session.app_name,
                session.process_name,
                session.window_title,
                session.browser_domain,
                int(session.is_idle),
                session.idle_seconds,
                session.git_repo,
                session.git_branch,
                session.context_switches,
                session.tag,
                session.platform,
                now,
                now,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def recent_sessions(self, limit: int = 20) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM activity_sessions
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        return list(cursor.fetchall())

    def save_sessions(self, sessions: Iterable[ActivitySession]) -> None:
        for session in sessions:
            self.save_session(session)

    def all_sessions(self) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM activity_sessions
            ORDER BY start_time ASC
            """
        )
        return list(cursor.fetchall())

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM activity_sessions
            WHERE id = ?
            """,
            (session_id,),
        )
        return cursor.fetchone()

    def update_session_tag(self, session_id: int, tag: str | None) -> None:
        current = self.get_session(session_id)
        now_dt = datetime.now(UTC)
        if current is not None and current["updated_at"]:
            current_updated_at = datetime.fromisoformat(current["updated_at"])
            if _format_datetime(now_dt) <= current["updated_at"]:
                now_dt = current_updated_at + timedelta(seconds=1)
        now = _format_datetime(now_dt)
        self._connection.execute(
            """
            UPDATE activity_sessions
            SET tag = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (tag, now, session_id),
        )
        self._connection.commit()

    def list_tag_review_candidates(
        self,
        *,
        days: int = 7,
        only_untagged: bool = True,
        app_name: str | None = None,
        domain: str | None = None,
        repo: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT *
            FROM activity_sessions
            WHERE start_time >= ?
        """
        params: list[object] = [
            _format_datetime(datetime.now(UTC) - timedelta(days=days))
        ]

        if only_untagged:
            query += " AND COALESCE(tag, '') = ''"
        if app_name:
            query += " AND app_name = ?"
            params.append(app_name)
        if domain:
            query += " AND browser_domain = ?"
            params.append(domain)
        if repo:
            query += " AND git_repo = ?"
            params.append(repo)

        query += " ORDER BY start_time DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def count_tag_review_candidates(
        self,
        *,
        days: int = 7,
        only_untagged: bool = True,
        app_name: str | None = None,
        domain: str | None = None,
        repo: str | None = None,
    ) -> int:
        query = """
            SELECT COUNT(*) AS total
            FROM activity_sessions
            WHERE start_time >= ?
        """
        params: list[object] = [
            _format_datetime(datetime.now(UTC) - timedelta(days=days))
        ]

        if only_untagged:
            query += " AND COALESCE(tag, '') = ''"
        if app_name:
            query += " AND app_name = ?"
            params.append(app_name)
        if domain:
            query += " AND browser_domain = ?"
            params.append(domain)
        if repo:
            query += " AND git_repo = ?"
            params.append(repo)

        cursor = self._connection.execute(query, params)
        row = cursor.fetchone()
        return int(row["total"] if row is not None else 0)

    def list_tag_review_groups(
        self,
        *,
        days: int = 7,
        only_untagged: bool = True,
        app_name: str | None = None,
        domain: str | None = None,
        repo: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        sessions = self.list_tag_review_candidates(
            days=days,
            only_untagged=only_untagged,
            app_name=app_name,
            domain=domain,
            repo=repo,
            limit=5000,
            offset=0,
        )

        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for row in sessions:
            if resolve_browser_tag_review_group(dict(row)) is not None:
                continue

            group = _resolve_tag_review_group(dict(row))
            if group is None:
                continue

            group_key = (group["group_type"], group["group_value"])
            if group_key not in grouped:
                grouped[group_key] = {
                    "group_type": group["group_type"],
                    "group_value": group["group_value"],
                    "session_count": 0,
                    "total_seconds": 0,
                    "sample_sessions": [],
                    "dominant_apps": set(),
                    "current_tags": set(),
                    "latest_start_time": None,
                }

            item = grouped[group_key]
            item["session_count"] = int(item["session_count"]) + 1
            item["total_seconds"] = int(item["total_seconds"]) + int(
                row["duration_sec"] or 0
            )
            if row["app_name"]:
                item["dominant_apps"].add(str(row["app_name"]))
            if row["tag"]:
                item["current_tags"].add(str(row["tag"]))
            latest = item["latest_start_time"]
            start_time = str(row["start_time"] or "")
            if latest is None or start_time > latest:
                item["latest_start_time"] = start_time
            if len(item["sample_sessions"]) < 3:
                item["sample_sessions"].append(
                    {
                        "id": row["id"],
                        "start_time": row["start_time"],
                        "app_name": row["app_name"],
                        "window_title": row["window_title"],
                        "browser_domain": row["browser_domain"],
                        "git_repo": row["git_repo"],
                    }
                )

        groups = []
        for item in grouped.values():
            groups.append(
                {
                    "group_type": item["group_type"],
                    "group_value": item["group_value"],
                    "session_count": item["session_count"],
                    "total_seconds": item["total_seconds"],
                    "sample_sessions": item["sample_sessions"],
                    "dominant_apps": sorted(item["dominant_apps"]),
                    "current_tags": sorted(item["current_tags"]),
                    "latest_start_time": item["latest_start_time"],
                }
            )

        groups.sort(
            key=lambda item: (
                -int(item["session_count"]),
                -int(item["total_seconds"]),
                str(item["group_type"]),
                str(item["group_value"]),
            )
        )
        return groups[offset : offset + limit]

    def count_tag_review_groups(
        self,
        *,
        days: int = 7,
        only_untagged: bool = True,
        app_name: str | None = None,
        domain: str | None = None,
        repo: str | None = None,
    ) -> int:
        return len(
            self.list_tag_review_groups(
                days=days,
                only_untagged=only_untagged,
                app_name=app_name,
                domain=domain,
                repo=repo,
                limit=5000,
                offset=0,
            )
        )

    def list_tag_review_group_sessions(
        self,
        *,
        group_type: str,
        group_value: str,
        days: int = 7,
        only_untagged: bool = True,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        sessions = self.list_tag_review_candidates(
            days=days,
            only_untagged=only_untagged,
            limit=5000,
            offset=0,
        )
        matched: list[sqlite3.Row] = []
        for row in sessions:
            if resolve_browser_tag_review_group(dict(row)) is not None:
                continue

            group = _resolve_tag_review_group(dict(row))
            if group is None:
                continue
            if (
                group["group_type"] == group_type
                and group["group_value"] == group_value
            ):
                matched.append(row)
            if len(matched) >= limit:
                break
        return matched

    def list_browser_tag_review_groups(
        self,
        *,
        days: int = 7,
        app_name: str | None = None,
        domain: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        sessions = self.list_tag_review_candidates(
            days=days,
            only_untagged=True,
            app_name=app_name,
            domain=domain,
            limit=5000,
            offset=0,
        )

        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for row in sessions:
            group = _resolve_browser_tag_review_group(dict(row))
            if group is None:
                continue

            group_key = (group["group_type"], group["group_value"])
            if group_key not in grouped:
                grouped[group_key] = {
                    "group_type": group["group_type"],
                    "group_value": group["group_value"],
                    "session_count": 0,
                    "total_seconds": 0,
                    "sample_sessions": [],
                    "dominant_apps": set(),
                    "current_tags": set(),
                    "latest_start_time": None,
                }

            item = grouped[group_key]
            item["session_count"] = int(item["session_count"]) + 1
            item["total_seconds"] = int(item["total_seconds"]) + int(
                row["duration_sec"] or 0
            )
            if row["app_name"]:
                item["dominant_apps"].add(str(row["app_name"]))
            if row["tag"]:
                item["current_tags"].add(str(row["tag"]))
            latest = item["latest_start_time"]
            start_time = str(row["start_time"] or "")
            if latest is None or start_time > latest:
                item["latest_start_time"] = start_time
            if len(item["sample_sessions"]) < 3:
                item["sample_sessions"].append(
                    {
                        "id": row["id"],
                        "start_time": row["start_time"],
                        "app_name": row["app_name"],
                        "window_title": row["window_title"],
                        "browser_domain": row["browser_domain"],
                        "git_repo": row["git_repo"],
                    }
                )

        groups = []
        for item in grouped.values():
            groups.append(
                {
                    "group_type": item["group_type"],
                    "group_value": item["group_value"],
                    "session_count": item["session_count"],
                    "total_seconds": item["total_seconds"],
                    "sample_sessions": item["sample_sessions"],
                    "dominant_apps": sorted(item["dominant_apps"]),
                    "current_tags": sorted(item["current_tags"]),
                    "latest_start_time": item["latest_start_time"],
                }
            )

        groups.sort(
            key=lambda item: (
                -int(item["session_count"]),
                -int(item["total_seconds"]),
                str(item["group_type"]),
                str(item["group_value"]),
            )
        )
        return groups[offset : offset + limit]

    def count_browser_tag_review_groups(
        self,
        *,
        days: int = 7,
        app_name: str | None = None,
        domain: str | None = None,
    ) -> int:
        return len(
            self.list_browser_tag_review_groups(
                days=days,
                app_name=app_name,
                domain=domain,
                limit=5000,
                offset=0,
            )
        )

    def list_browser_tag_review_group_sessions(
        self,
        *,
        group_type: str,
        group_value: str,
        days: int = 7,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        sessions = self.list_tag_review_candidates(
            days=days,
            only_untagged=True,
            limit=5000,
            offset=0,
        )
        matched: list[sqlite3.Row] = []
        for row in sessions:
            group = _resolve_browser_tag_review_group(dict(row))
            if group is None:
                continue
            if (
                group["group_type"] == group_type
                and group["group_value"] == group_value
            ):
                matched.append(row)
            if len(matched) >= limit:
                break
        return matched

    def update_session_tags(self, session_ids: Iterable[int], tag: str | None) -> int:
        updated = 0
        for session_id in session_ids:
            self.update_session_tag(int(session_id), tag)
            updated += 1
        return updated

    def create_tag_review_action(
        self,
        *,
        session_id: int,
        original_tag: str | None,
        selected_tag: str,
        reason: str | None = None,
        source_signal: str | None = None,
        applied_to_rules: bool = False,
    ) -> int:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session_id: {session_id}")

        cursor = self._connection.execute(
            """
            INSERT INTO tag_review_actions (
                session_id,
                session_uuid,
                original_tag,
                selected_tag,
                reason,
                source_signal,
                created_at,
                applied_to_rules
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                session["uuid"],
                original_tag,
                selected_tag,
                reason,
                source_signal,
                _format_datetime(datetime.now(UTC)),
                int(applied_to_rules),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_review_actions_for_suggestions(
        self,
        *,
        days: int | None = None,
        selected_tag: str | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT
                review.id,
                review.session_id,
                review.session_uuid,
                review.original_tag,
                review.selected_tag,
                review.reason,
                review.source_signal,
                review.created_at,
                review.applied_to_rules,
                session.app_name,
                session.window_title,
                session.browser_domain,
                session.git_repo,
                session.git_branch,
                session.start_time,
                session.end_time
            FROM tag_review_actions AS review
            INNER JOIN activity_sessions AS session
                ON session.id = review.session_id
            WHERE 1=1
        """
        params: list[object] = []

        if days is not None:
            query += " AND review.created_at >= ?"
            params.append(_format_datetime(datetime.now(UTC) - timedelta(days=days)))
        if selected_tag:
            query += " AND review.selected_tag = ?"
            params.append(selected_tag)

        query += " ORDER BY review.created_at DESC, review.id DESC"
        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def save_journal_entry(
        self,
        *,
        created_at: datetime,
        start_time: datetime | None,
        end_time: datetime | None,
        title: str,
        notes: str,
        metadata: dict | None = None,
    ) -> int:
        updated_at = _format_datetime(datetime.now(UTC))
        cursor = self._connection.execute(
            """
            INSERT INTO journal_entries (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                start_time,
                end_time,
                title,
                notes,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                self._user_id,
                self._device_id,
                _format_datetime(created_at),
                updated_at,
                _format_datetime(start_time) if start_time else None,
                _format_datetime(end_time) if end_time else None,
                title,
                notes,
                json.dumps(metadata) if metadata is not None else None,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def recent_journal_entries(self, limit: int = 50) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM journal_entries
            ORDER BY COALESCE(start_time, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        return list(cursor.fetchall())

    def journal_entries_between(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT *
            FROM journal_entries
            WHERE 1=1
        """
        params: list = []

        if start_time:
            query += " AND COALESCE(start_time, created_at) >= ?"
            params.append(_format_datetime(start_time))
        if end_time:
            query += " AND COALESCE(end_time, start_time, created_at) <= ?"
            params.append(_format_datetime(end_time))

        query += " ORDER BY COALESCE(start_time, created_at) DESC LIMIT ?"
        params.append(limit)

        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def get_journal_entry(self, journal_id: int) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM journal_entries
            WHERE id = ?
            """,
            (journal_id,),
        )
        return cursor.fetchone()

    def correlated_sessions(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM activity_sessions
            WHERE start_time < ?
              AND end_time > ?
            ORDER BY start_time ASC
            LIMIT ?
            """,
            (_format_datetime(end_time), _format_datetime(start_time), limit),
        )
        return list(cursor.fetchall())

    def upsert_daily_reflection(
        self,
        *,
        date: str,
        wins: str | None,
        problems: str | None,
        tomorrow: str | None,
        energy: int | None,
        stress: int | None,
    ) -> None:
        now = _format_datetime(datetime.now(UTC))
        self._connection.execute(
            """
            INSERT INTO daily_reflections (
                uuid,
                user_id,
                device_id,
                date,
                wins,
                problems,
                tomorrow,
                energy,
                stress,
                created_at,
                updated_at,
                deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                wins = excluded.wins,
                problems = excluded.problems,
                tomorrow = excluded.tomorrow,
                energy = excluded.energy,
                stress = excluded.stress,
                user_id = excluded.user_id,
                device_id = excluded.device_id,
                updated_at = excluded.updated_at
            """,
            (
                str(uuid4()),
                self._user_id,
                self._device_id,
                date,
                wins,
                problems,
                tomorrow,
                energy,
                stress,
                now,
                now,
                None,
            ),
        )
        self._connection.commit()

    def get_sync_state(self) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM sync_state
            WHERE id = 1
            """
        )
        return cursor.fetchone()

    def update_sync_state(
        self,
        *,
        last_push_cursor: str | None,
        last_pull_cursor: str | None,
    ) -> None:
        now = _format_datetime(datetime.now(UTC))
        self._connection.execute(
            """
            INSERT INTO sync_state (
                id,
                device_id,
                user_id,
                last_push_cursor,
                last_pull_cursor,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                device_id = excluded.device_id,
                user_id = excluded.user_id,
                last_push_cursor = excluded.last_push_cursor,
                last_pull_cursor = excluded.last_pull_cursor,
                updated_at = excluded.updated_at
            """,
            (
                self._device_id,
                self._user_id,
                last_push_cursor,
                last_pull_cursor,
                now,
            ),
        )
        self._connection.commit()

    def list_changes_since(
        self,
        *,
        entity: str,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        table_name = _resolve_entity_table(entity)
        return self._list_entity_changes(
            table_name=table_name, cursor=cursor, limit=limit
        )

    def list_session_changes_since(
        self,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        return self._list_entity_changes(
            table_name="activity_sessions", cursor=cursor, limit=limit
        )

    def list_journal_changes_since(
        self,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        return self._list_entity_changes(
            table_name="journal_entries", cursor=cursor, limit=limit
        )

    def list_reflection_changes_since(
        self,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        return self._list_entity_changes(
            table_name="daily_reflections", cursor=cursor, limit=limit
        )

    def list_work_event_changes_since(
        self,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        return self._list_entity_changes(
            table_name="work_events", cursor=cursor, limit=limit
        )

    def upsert_session_by_uuid(self, payload: dict) -> None:
        now = _format_datetime(datetime.now(UTC))
        row_uuid = str(payload.get("uuid") or uuid4())
        row_updated_at = payload.get("updated_at") or now
        row_created_at = payload.get("created_at") or row_updated_at

        self._connection.execute(
            """
            INSERT INTO activity_sessions (
                uuid,
                user_id,
                device_id,
                start_time,
                end_time,
                duration_sec,
                app_name,
                process_name,
                window_title,
                browser_domain,
                is_idle,
                idle_seconds,
                git_repo,
                git_branch,
                context_switches,
                tag,
                platform,
                created_at,
                updated_at,
                deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                user_id = excluded.user_id,
                device_id = excluded.device_id,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                duration_sec = excluded.duration_sec,
                app_name = excluded.app_name,
                process_name = excluded.process_name,
                window_title = excluded.window_title,
                browser_domain = excluded.browser_domain,
                is_idle = excluded.is_idle,
                idle_seconds = excluded.idle_seconds,
                git_repo = excluded.git_repo,
                git_branch = excluded.git_branch,
                context_switches = excluded.context_switches,
                tag = excluded.tag,
                platform = excluded.platform,
                updated_at = excluded.updated_at,
                deleted_at = excluded.deleted_at
            WHERE excluded.updated_at >= COALESCE(activity_sessions.updated_at, activity_sessions.created_at)
            """,
            (
                row_uuid,
                payload.get("user_id") or self._user_id,
                payload.get("device_id") or self._device_id,
                payload["start_time"],
                payload["end_time"],
                payload["duration_sec"],
                payload["app_name"],
                payload.get("process_name"),
                payload.get("window_title"),
                payload.get("browser_domain"),
                int(payload.get("is_idle", 0)),
                payload.get("idle_seconds", 0),
                payload.get("git_repo"),
                payload.get("git_branch"),
                payload.get("context_switches", 0),
                payload.get("tag"),
                payload.get("platform", "unknown"),
                row_created_at,
                row_updated_at,
                payload.get("deleted_at"),
            ),
        )
        self._connection.commit()

    def upsert_journal_by_uuid(self, payload: dict) -> None:
        now = _format_datetime(datetime.now(UTC))
        row_uuid = str(payload.get("uuid") or uuid4())
        row_updated_at = payload.get("updated_at") or now
        row_created_at = payload.get("created_at") or row_updated_at

        self._connection.execute(
            """
            INSERT INTO journal_entries (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                start_time,
                end_time,
                title,
                notes,
                metadata,
                deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                user_id = excluded.user_id,
                device_id = excluded.device_id,
                updated_at = excluded.updated_at,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                title = excluded.title,
                notes = excluded.notes,
                metadata = excluded.metadata,
                deleted_at = excluded.deleted_at
            WHERE excluded.updated_at >= COALESCE(journal_entries.updated_at, journal_entries.created_at)
            """,
            (
                row_uuid,
                payload.get("user_id") or self._user_id,
                payload.get("device_id") or self._device_id,
                row_created_at,
                row_updated_at,
                payload.get("start_time"),
                payload.get("end_time"),
                payload.get("title"),
                payload.get("notes", ""),
                _to_json_text(payload.get("metadata")),
                payload.get("deleted_at"),
            ),
        )
        self._connection.commit()

    def upsert_reflection_by_uuid(self, payload: dict) -> None:
        now = _format_datetime(datetime.now(UTC))
        row_uuid = str(payload.get("uuid") or uuid4())
        row_user_id = payload.get("user_id") or self._user_id
        row_device_id = payload.get("device_id") or self._device_id
        row_updated_at = payload.get("updated_at") or now
        row_created_at = payload.get("created_at") or row_updated_at
        row_date = payload["date"]

        existing = self._connection.execute(
            """
            SELECT *
            FROM daily_reflections
            WHERE uuid = ?
               OR (user_id = ? AND date = ?)
            ORDER BY CASE WHEN uuid = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (row_uuid, row_user_id, row_date, row_uuid),
        ).fetchone()

        if existing is None:
            self._connection.execute(
                """
                INSERT INTO daily_reflections (
                    uuid,
                    user_id,
                    device_id,
                    date,
                    wins,
                    problems,
                    tomorrow,
                    energy,
                    stress,
                    created_at,
                    updated_at,
                    deleted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_uuid,
                    row_user_id,
                    row_device_id,
                    row_date,
                    payload.get("wins"),
                    payload.get("problems"),
                    payload.get("tomorrow"),
                    payload.get("energy"),
                    payload.get("stress"),
                    row_created_at,
                    row_updated_at,
                    payload.get("deleted_at"),
                ),
            )
            self._connection.commit()
            return

        existing_updated_at = existing["updated_at"] or existing["created_at"]
        existing_uuid = existing["uuid"] or ""
        should_apply = _is_incoming_newer(
            incoming_updated_at=row_updated_at,
            incoming_id=row_uuid,
            existing_updated_at=existing_updated_at,
            existing_id=existing_uuid,
        )
        if not should_apply:
            return

        self._connection.execute(
            """
            UPDATE daily_reflections
            SET uuid = ?,
                user_id = ?,
                device_id = ?,
                date = ?,
                wins = ?,
                problems = ?,
                tomorrow = ?,
                energy = ?,
                stress = ?,
                updated_at = ?,
                deleted_at = ?
            WHERE id = ?
            """,
            (
                row_uuid,
                row_user_id,
                row_device_id,
                row_date,
                payload.get("wins"),
                payload.get("problems"),
                payload.get("tomorrow"),
                payload.get("energy"),
                payload.get("stress"),
                row_updated_at,
                payload.get("deleted_at"),
                existing["id"],
            ),
        )
        self._connection.commit()

    def upsert_work_event_by_uuid(self, payload: dict) -> None:
        now = _format_datetime(datetime.now(UTC))
        row_uuid = str(payload.get("uuid") or uuid4())
        row_updated_at = payload.get("updated_at") or payload.get("created_at") or now
        row_created_at = payload.get("created_at") or row_updated_at

        self._connection.execute(
            """
            INSERT INTO work_events (
                uuid,
                user_id,
                device_id,
                created_at,
                updated_at,
                event_time,
                event_type,
                title,
                impact,
                project,
                notes,
                metadata,
                deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                user_id = excluded.user_id,
                device_id = excluded.device_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                event_time = excluded.event_time,
                event_type = excluded.event_type,
                title = excluded.title,
                impact = excluded.impact,
                project = excluded.project,
                notes = excluded.notes,
                metadata = excluded.metadata,
                deleted_at = excluded.deleted_at
            WHERE excluded.updated_at >= COALESCE(work_events.updated_at, work_events.created_at)
            """,
            (
                row_uuid,
                payload.get("user_id") or self._user_id,
                payload.get("device_id") or self._device_id,
                row_created_at,
                row_updated_at,
                payload.get("event_time"),
                payload.get("event_type"),
                payload.get("title"),
                payload.get("impact"),
                payload.get("project"),
                payload.get("notes"),
                _to_json_text(payload.get("metadata")),
                payload.get("deleted_at"),
            ),
        )
        self._connection.commit()

    def mark_deleted(
        self,
        *,
        entity: str,
        row_uuid: str,
        deleted_at: str,
        updated_at: str | None = None,
    ) -> None:
        table_name = _resolve_entity_table(entity)
        effective_updated_at = updated_at or deleted_at
        self._connection.execute(
            f"""
            UPDATE {table_name}
            SET deleted_at = ?,
                updated_at = ?
            WHERE uuid = ?
              AND (
                    COALESCE(updated_at, created_at) IS NULL
                    OR COALESCE(updated_at, created_at) < ?
                    OR (
                        COALESCE(updated_at, created_at) = ?
                        AND uuid <= ?
                    )
              )
            """,
            (
                deleted_at,
                effective_updated_at,
                row_uuid,
                effective_updated_at,
                effective_updated_at,
                row_uuid,
            ),
        )
        self._connection.commit()

    def reflections_between(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT *
            FROM daily_reflections
            WHERE 1=1
        """
        params: list = []

        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)

        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def save_work_event(
        self,
        *,
        created_at: datetime,
        event_type: str,
        title: str,
        event_time: datetime | None = None,
        impact: str | None = None,
        project: str | None = None,
        notes: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        row_uuid = str(uuid4())
        row_created_at = _format_datetime(created_at)
        row_updated_at = row_created_at
        cursor = self._connection.execute(
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_uuid,
                self._user_id,
                self._device_id,
                row_created_at,
                _format_datetime(event_time) if event_time else None,
                event_type,
                title,
                impact,
                project,
                notes,
                json.dumps(metadata) if metadata is not None else None,
                row_updated_at,
                None,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def recent_work_events(self, limit: int = 50) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM work_events
            WHERE deleted_at IS NULL
            ORDER BY COALESCE(event_time, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        return list(cursor.fetchall())

    def backup_database(self, backup_dir: str | Path = "backups") -> Path:
        backup_directory = Path(backup_dir)
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_directory / f"activity-{timestamp}.db"

        backup_connection = sqlite3.connect(backup_path)
        try:
            self._connection.backup(backup_connection)
            backup_connection.commit()
        finally:
            backup_connection.close()

        return backup_path

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ActivityRepository:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _initialize_identity(self) -> None:
        now = _format_datetime(datetime.now(UTC))
        self._connection.execute(
            """
            INSERT INTO users (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (self._user_id, self._user_name, now, now),
        )
        self._connection.execute(
            """
            INSERT INTO devices (
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
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at
            """,
            (
                self._device_id,
                self._user_id,
                self._device_name,
                self._device_type,
                platform.node() or None,
                None,
                now,
                now,
                now,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO sync_state (
                id,
                device_id,
                user_id,
                last_push_cursor,
                last_pull_cursor,
                updated_at
            )
            VALUES (1, ?, ?, NULL, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                device_id = excluded.device_id,
                user_id = excluded.user_id,
                updated_at = excluded.updated_at
            """,
            (self._device_id, self._user_id, now),
        )
        self._connection.commit()

    def _backfill_sync_metadata(self) -> None:
        now = _format_datetime(datetime.now(UTC))
        self._backfill_table(
            table_name="activity_sessions",
            created_column="created_at",
            date_key_column=None,
        )
        self._backfill_table(
            table_name="journal_entries",
            created_column="created_at",
            date_key_column=None,
        )
        self._backfill_table(
            table_name="daily_reflections",
            created_column="created_at",
            date_key_column="date",
        )
        self._connection.execute(
            """
            UPDATE devices
            SET last_seen_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, self._device_id),
        )
        self._connection.commit()

    def _backfill_table(
        self,
        *,
        table_name: str,
        created_column: str,
        date_key_column: str | None,
    ) -> None:
        rows = self._connection.execute(
            f"""
            SELECT id, uuid, user_id, device_id, updated_at, deleted_at, {created_column}{", " + date_key_column if date_key_column else ""}
            FROM {table_name}
            """
        ).fetchall()

        for row in rows:
            row_uuid = row["uuid"] or str(uuid4())
            created_at_value = row[created_column]
            if not created_at_value and date_key_column:
                created_at_value = f"{row[date_key_column]}T00:00:00+00:00"
            updated_at_value = (
                row["updated_at"]
                or created_at_value
                or _format_datetime(datetime.now(UTC))
            )
            self._connection.execute(
                f"""
                UPDATE {table_name}
                SET uuid = ?,
                    user_id = COALESCE(user_id, ?),
                    device_id = COALESCE(device_id, ?),
                    updated_at = COALESCE(updated_at, ?),
                    deleted_at = COALESCE(deleted_at, NULL)
                WHERE id = ?
                """,
                (row_uuid, self._user_id, self._device_id, updated_at_value, row["id"]),
            )

    def _list_entity_changes(
        self,
        *,
        table_name: str,
        cursor: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        updated_at_cursor, uuid_cursor = _parse_sync_cursor(cursor)
        query = f"""
            SELECT *
            FROM {table_name}
            WHERE 1=1
        """
        params: list = []

        if updated_at_cursor is not None:
            query += """
              AND (
                  COALESCE(updated_at, created_at) > ?
                  OR (
                      COALESCE(updated_at, created_at) = ?
                      AND COALESCE(uuid, '') > ?
                  )
              )
            """
            params.extend([updated_at_cursor, updated_at_cursor, uuid_cursor or ""])

        query += """
            ORDER BY COALESCE(updated_at, created_at) ASC, COALESCE(uuid, '') ASC
            LIMIT ?
        """
        params.append(limit)

        cursor_obj = self._connection.execute(query, params)
        return list(cursor_obj.fetchall())


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _resolve_tag_review_group(session: dict[str, object]) -> dict[str, str] | None:
    resolved = resolve_tag_review_group(session)
    return dict(resolved) if resolved is not None else None


def _resolve_browser_tag_review_group(
    session: dict[str, object],
) -> dict[str, str] | None:
    resolved = resolve_browser_tag_review_group(session)
    return dict(resolved) if resolved is not None else None


def _normalize_tag_review_repo_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().rstrip("/\\")
    if not text:
        return None
    normalized = text.replace("\\", "/")
    if normalized.endswith("/.git"):
        normalized = normalized[: -len("/.git")]
    repo_name = normalized.split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    repo_name = repo_name.strip()
    return repo_name or None


def _normalize_tag_review_domain_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    text = text.split("/")[0].split(":")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text or None


def _parse_sync_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    if "|" not in cursor:
        return cursor, None
    updated_at, row_uuid = cursor.split("|", 1)
    return updated_at or None, row_uuid or None


def _is_incoming_newer(
    *,
    incoming_updated_at: str,
    incoming_id: str,
    existing_updated_at: str,
    existing_id: str,
) -> bool:
    if incoming_updated_at > existing_updated_at:
        return True
    if incoming_updated_at < existing_updated_at:
        return False
    return incoming_id >= existing_id


def _resolve_entity_table(entity: str) -> str:
    normalized = entity.strip().lower()
    if normalized in {"sessions", "session", "activity_sessions"}:
        return "activity_sessions"
    if normalized in {"journal", "journal_entries", "journals"}:
        return "journal_entries"
    if normalized in {"reflections", "reflection", "daily_reflections"}:
        return "daily_reflections"
    if normalized in {"work_events", "work-event", "work_events"}:
        return "work_events"
    raise ValueError(f"Unsupported sync entity: {entity}")


def _to_json_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _default_user_id() -> str:
    return str(uuid5(NAMESPACE_DNS, "workgraph.local.user"))


def _default_device_id() -> str:
    node_name = platform.node() or "unknown-device"
    return str(uuid5(NAMESPACE_DNS, f"workgraph.local.device.{node_name}"))


def _load_or_create_identity(identity_path: str | Path | None) -> dict[str, str]:
    default_identity = {
        "user_id": _default_user_id(),
        "device_id": _default_device_id(),
        "user_name": "Local User",
        "device_name": platform.node() or "Local Device",
        "device_type": "desktop",
    }
    if identity_path is None:
        return default_identity

    path = Path(identity_path)
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {
            "user_id": str(loaded.get("user_id") or default_identity["user_id"]),
            "device_id": str(loaded.get("device_id") or default_identity["device_id"]),
            "user_name": str(loaded.get("user_name") or default_identity["user_name"]),
            "device_name": str(
                loaded.get("device_name") or default_identity["device_name"]
            ),
            "device_type": str(
                loaded.get("device_type") or default_identity["device_type"]
            ),
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_identity, indent=2), encoding="utf-8")
    return default_identity


def restore_database_from_backup(db_path: str | Path, backup_path: str | Path) -> None:
    source = Path(backup_path)
    if not source.exists():
        raise FileNotFoundError(f"Backup not found: {source}")

    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
