from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
