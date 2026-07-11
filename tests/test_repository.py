from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from db.repository import ActivityRepository
from workgraph.models import ActivitySession


class ActivityRepositoryTests(unittest.TestCase):
    def test_repository_saves_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"
            session = ActivitySession(
                start_time=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 7, 9, 10, 5, tzinfo=timezone.utc),
                duration_sec=300,
                app_name="Code",
                process_name="Code.exe",
                window_title="README.md",
                browser_domain=None,
                is_idle=False,
                idle_seconds=0,
                platform="windows",
                git_repo=None,
                git_branch=None,
                context_switches=0,
                tag=None,
            )

            with ActivityRepository(db_path) as repository:
                row_id = repository.save_session(session)
                rows = repository.recent_sessions()

        self.assertEqual(row_id, 1)
        self.assertEqual(rows[0]["app_name"], "Code")
        self.assertEqual(rows[0]["duration_sec"], 300)
        self.assertIsNotNone(rows[0]["uuid"])
        self.assertIsNotNone(rows[0]["user_id"])
        self.assertIsNotNone(rows[0]["device_id"])
        self.assertIsNotNone(rows[0]["updated_at"])

    def test_repository_saves_journal_entry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                entry_id = repository.save_journal_entry(
                    created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
                    start_time=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
                    title="Historical note",
                    notes="Network outage affected delivery.",
                    metadata={"labels": ["incident", "delivery"]},
                )
                entries = repository.recent_journal_entries(limit=10)

        self.assertEqual(entry_id, 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Historical note")
        self.assertIsNotNone(entries[0]["uuid"])
        self.assertIsNotNone(entries[0]["user_id"])
        self.assertIsNotNone(entries[0]["device_id"])
        self.assertIsNotNone(entries[0]["updated_at"])

    def test_repository_correlated_sessions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            inside = ActivitySession(
                start_time=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 7, 9, 10, 30, tzinfo=timezone.utc),
                duration_sec=1800,
                app_name="Code",
                process_name="Code",
                window_title="main.py",
                browser_domain=None,
                is_idle=False,
                idle_seconds=0,
                platform="linux",
                git_repo="workgraph",
                git_branch="main",
                context_switches=1,
                tag="Client Delivery",
            )
            outside = ActivitySession(
                start_time=datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 7, 9, 14, 30, tzinfo=timezone.utc),
                duration_sec=1800,
                app_name="Chrome",
                process_name="chrome",
                window_title="docs",
                browser_domain="example.com",
                is_idle=False,
                idle_seconds=0,
                platform="linux",
                git_repo=None,
                git_branch=None,
                context_switches=0,
                tag=None,
            )

            with ActivityRepository(db_path) as repository:
                repository.save_session(inside)
                repository.save_session(outside)
                results = repository.correlated_sessions(
                    start_time=datetime(2026, 7, 9, 9, 45, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 9, 11, 0, tzinfo=timezone.utc),
                )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["app_name"], "Code")

    def test_repository_upserts_reflection(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                repository.upsert_daily_reflection(
                    date="2026-07-10",
                    wins="Finished dashboard",
                    problems="Slow review turnaround",
                    tomorrow="Prepare demo checklist",
                    energy=6,
                    stress=5,
                )
                repository.upsert_daily_reflection(
                    date="2026-07-10",
                    wins="Finished dashboard and tests",
                    problems="",
                    tomorrow="Demo dry run",
                    energy=7,
                    stress=4,
                )
                reflections = repository.reflections_between("2026-07-10", "2026-07-10")

        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["energy"], 7)
        self.assertIn("tests", reflections[0]["wins"])
        self.assertIsNotNone(reflections[0]["uuid"])
        self.assertIsNotNone(reflections[0]["user_id"])
        self.assertIsNotNone(reflections[0]["device_id"])

    def test_repository_saves_work_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                event_id = repository.save_work_event(
                    created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
                    event_time=datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc),
                    event_type="Incident",
                    title="Demo failure",
                    impact="High",
                    project="MCP Platform",
                    notes="Auth service timeout.",
                    metadata={"labels": ["prod", "customer"]},
                )
                events = repository.recent_work_events(limit=10)

        self.assertEqual(event_id, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "Incident")
        self.assertEqual(events[0]["impact"], "High")

    def test_repository_initializes_and_updates_sync_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                original_state = repository.get_sync_state()
                repository.update_sync_state(
                    last_push_cursor="push-cursor-1",
                    last_pull_cursor="pull-cursor-1",
                )
                updated_state = repository.get_sync_state()

        self.assertIsNotNone(original_state)
        self.assertIsNotNone(updated_state)
        self.assertEqual(updated_state["last_push_cursor"], "push-cursor-1")
        self.assertEqual(updated_state["last_pull_cursor"], "pull-cursor-1")

    def test_repository_backfills_sync_fields_for_legacy_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            # Create a legacy schema row missing sync metadata columns.
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE activity_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_sec INTEGER NOT NULL,
                    app_name TEXT NOT NULL,
                    process_name TEXT,
                    window_title TEXT,
                    browser_domain TEXT,
                    is_idle INTEGER NOT NULL,
                    idle_seconds INTEGER NOT NULL DEFAULT 0,
                    git_repo TEXT,
                    git_branch TEXT,
                    context_switches INTEGER NOT NULL DEFAULT 0,
                    tag TEXT,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

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
                    platform,
                    created_at
                ) VALUES (
                    '2026-07-09T10:00:00+00:00',
                    '2026-07-09T10:05:00+00:00',
                    300,
                    'Code',
                    'Code',
                    'main.py',
                    NULL,
                    0,
                    0,
                    'workgraph',
                    'main',
                    0,
                    'Client Delivery',
                    'linux',
                    '2026-07-09T10:05:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            with ActivityRepository(db_path) as repository:
                rows = repository.all_sessions()

        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["uuid"])
        self.assertIsNotNone(rows[0]["user_id"])
        self.assertIsNotNone(rows[0]["device_id"])
        self.assertIsNotNone(rows[0]["updated_at"])

    def test_repository_lists_session_changes_since_cursor(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "00000000-0000-0000-0000-000000000001",
                        "start_time": "2026-07-10T10:00:00+00:00",
                        "end_time": "2026-07-10T10:05:00+00:00",
                        "duration_sec": 300,
                        "app_name": "Code",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T10:05:00+00:00",
                    }
                )
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "00000000-0000-0000-0000-000000000002",
                        "start_time": "2026-07-10T11:00:00+00:00",
                        "end_time": "2026-07-10T11:10:00+00:00",
                        "duration_sec": 600,
                        "app_name": "Chrome",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T11:10:00+00:00",
                    }
                )

                first_page = repository.list_session_changes_since(limit=1)
                cursor = f"{first_page[0]['updated_at']}|{first_page[0]['uuid']}"
                second_page = repository.list_session_changes_since(cursor=cursor, limit=10)

        self.assertEqual(len(first_page), 1)
        self.assertEqual(first_page[0]["uuid"], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(len(second_page), 1)
        self.assertEqual(second_page[0]["uuid"], "00000000-0000-0000-0000-000000000002")

    def test_repository_upsert_session_by_uuid_keeps_newer_version(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "11111111-1111-1111-1111-111111111111",
                        "start_time": "2026-07-10T10:00:00+00:00",
                        "end_time": "2026-07-10T10:05:00+00:00",
                        "duration_sec": 300,
                        "app_name": "Code",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T10:05:00+00:00",
                    }
                )
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "11111111-1111-1111-1111-111111111111",
                        "start_time": "2026-07-10T10:00:00+00:00",
                        "end_time": "2026-07-10T10:05:00+00:00",
                        "duration_sec": 300,
                        "app_name": "Overwritten-App",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T09:59:00+00:00",
                    }
                )
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "11111111-1111-1111-1111-111111111111",
                        "start_time": "2026-07-10T10:00:00+00:00",
                        "end_time": "2026-07-10T10:05:00+00:00",
                        "duration_sec": 300,
                        "app_name": "Code Final",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T10:06:00+00:00",
                    }
                )
                row = repository._connection.execute(
                    "SELECT * FROM activity_sessions WHERE uuid = ?",
                    ("11111111-1111-1111-1111-111111111111",),
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["app_name"], "Code Final")
        self.assertEqual(row["updated_at"], "2026-07-10T10:06:00+00:00")

    def test_repository_mark_deleted_sets_tombstone(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path) as repository:
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "22222222-2222-2222-2222-222222222222",
                        "start_time": "2026-07-10T12:00:00+00:00",
                        "end_time": "2026-07-10T12:15:00+00:00",
                        "duration_sec": 900,
                        "app_name": "Terminal",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-10T12:15:00+00:00",
                    }
                )
                repository.mark_deleted(
                    entity="sessions",
                    row_uuid="22222222-2222-2222-2222-222222222222",
                    deleted_at="2026-07-10T12:30:00+00:00",
                    updated_at="2026-07-10T12:30:00+00:00",
                )
                row = repository._connection.execute(
                    "SELECT deleted_at, updated_at FROM activity_sessions WHERE uuid = ?",
                    ("22222222-2222-2222-2222-222222222222",),
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["deleted_at"], "2026-07-10T12:30:00+00:00")
        self.assertEqual(row["updated_at"], "2026-07-10T12:30:00+00:00")

    def test_repository_upsert_reflection_by_uuid_resolves_same_date_conflict(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"

            with ActivityRepository(db_path, user_id="user-1") as repository:
                repository.upsert_reflection_by_uuid(
                    {
                        "uuid": "33333333-3333-3333-3333-333333333333",
                        "user_id": "user-1",
                        "date": "2026-07-11",
                        "wins": "Initial",
                        "updated_at": "2026-07-11T08:00:00+00:00",
                    }
                )
                repository.upsert_reflection_by_uuid(
                    {
                        "uuid": "44444444-4444-4444-4444-444444444444",
                        "user_id": "user-1",
                        "date": "2026-07-11",
                        "wins": "Latest",
                        "updated_at": "2026-07-11T09:00:00+00:00",
                    }
                )
                rows = repository.reflections_between("2026-07-11", "2026-07-11")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wins"], "Latest")
        self.assertEqual(rows[0]["uuid"], "44444444-4444-4444-4444-444444444444")

    def test_repository_creates_and_reuses_identity_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path_one = temp / "one.db"
            db_path_two = temp / "two.db"
            identity_path = temp / "identity.json"

            with ActivityRepository(db_path_one, identity_path=identity_path) as repository_one:
                state_one = repository_one.get_sync_state()

            self.assertTrue(identity_path.exists())
            identity_data = json.loads(identity_path.read_text(encoding="utf-8"))

            with ActivityRepository(db_path_two, identity_path=identity_path) as repository_two:
                state_two = repository_two.get_sync_state()

        self.assertIsNotNone(state_one)
        self.assertIsNotNone(state_two)
        self.assertEqual(state_one["user_id"], identity_data["user_id"])
        self.assertEqual(state_one["device_id"], identity_data["device_id"])
        self.assertEqual(state_two["user_id"], identity_data["user_id"])
        self.assertEqual(state_two["device_id"], identity_data["device_id"])


if __name__ == "__main__":
    unittest.main()
