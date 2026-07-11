from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

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

    def all_sessions(self) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM activity_sessions
            ORDER BY start_time ASC
            """
        )
        return list(cursor.fetchall())

    def update_session_tag(self, session_id: int, tag: str | None) -> None:
        self._connection.execute(
            """
            UPDATE activity_sessions
            SET tag = ?
            WHERE id = ?
            """,
            (tag, session_id),
        )
        self._connection.commit()

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
        cursor = self._connection.execute(
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
                _format_datetime(created_at),
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
            (date, wins, problems, tomorrow, energy, stress, now, now),
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
        cursor = self._connection.execute(
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
                _format_datetime(created_at),
                _format_datetime(event_time) if event_time else None,
                event_type,
                title,
                impact,
                project,
                notes,
                json.dumps(metadata) if metadata is not None else None,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def recent_work_events(self, limit: int = 50) -> list[sqlite3.Row]:
        cursor = self._connection.execute(
            """
            SELECT *
            FROM work_events
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


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def restore_database_from_backup(db_path: str | Path, backup_path: str | Path) -> None:
    source = Path(backup_path)
    if not source.exists():
        raise FileNotFoundError(f"Backup not found: {source}")

    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
