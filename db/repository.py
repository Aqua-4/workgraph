from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from workgraph.models import ActivitySession


class ActivityRepository:
    def __init__(self, db_path: str | Path = "activity.db") -> None:
        self.db_path = Path(db_path)
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
        try:
            self._connection.execute(
                "ALTER TABLE activity_sessions ADD COLUMN idle_seconds INTEGER NOT NULL DEFAULT 0"
            )
            self._connection.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    def save_session(self, session: ActivitySession) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO activity_sessions (
                start_time,
                end_time,
                duration_sec,
                app_name,
                process_name,
                window_title,
                browser_domain,
                is_idle,
                idle_seconds,
                platform
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _format_datetime(session.start_time),
                _format_datetime(session.end_time),
                session.duration_sec,
                session.app_name,
                session.process_name,
                session.window_title,
                session.browser_domain,
                int(session.is_idle),
                session.idle_seconds,
                session.platform,
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

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ActivityRepository:
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
