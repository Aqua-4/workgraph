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
        migrations = [
            "ALTER TABLE activity_sessions ADD COLUMN idle_seconds INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE activity_sessions ADD COLUMN git_repo TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_branch TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN context_switches INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE activity_sessions ADD COLUMN tag TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_commit_hash TEXT",
            "ALTER TABLE activity_sessions ADD COLUMN git_modified_files TEXT",
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
                git_repo,
                git_branch,
                context_switches,
                tag,
                platform
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                session.git_repo,
                session.git_branch,
                session.context_switches,
                session.tag,
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
