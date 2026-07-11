from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from db.repository import ActivityRepository
from services.reporting import (
    analyze_goal_allocation,
    default_goals_path,
    export_activity_sessions,
    generate_weekly_report_markdown,
)
from workgraph.models import ActivitySession


class ReportingFeaturesTests(unittest.TestCase):
    def test_default_goals_path_prefers_my_goals(self) -> None:
        config_dir = Path(__file__).resolve().parent.parent / "config"
        custom = config_dir / "my-goals.yaml"
        backup = config_dir / "my-goals.yaml.test-backup"

        had_existing = custom.exists()
        if had_existing:
            custom.rename(backup)

        try:
            custom.write_text("goals:\n  Learning: 100\n", encoding="utf-8")
            selected = default_goals_path()
            self.assertEqual(selected.name, "my-goals.yaml")
        finally:
            if custom.exists():
                custom.unlink()
            if had_existing and backup.exists():
                backup.rename(custom)

    def test_default_goals_path_falls_back_to_goals(self) -> None:
        config_dir = Path(__file__).resolve().parent.parent / "config"
        custom = config_dir / "my-goals.yaml"
        backup = config_dir / "my-goals.yaml.test-backup"

        had_existing = custom.exists()
        if had_existing:
            custom.rename(backup)

        try:
            selected = default_goals_path()
            self.assertEqual(selected.name, "goals.yaml")
        finally:
            if had_existing and backup.exists():
                backup.rename(custom)

    def test_export_activity_sessions_all_formats(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            self._seed_sessions(db_path)

            json_out = temp / "sessions.json"
            csv_out = temp / "sessions.csv"
            md_out = temp / "sessions.md"

            export_activity_sessions(
                db_path=db_path,
                export_format="json",
                output_path=json_out,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )
            export_activity_sessions(
                db_path=db_path,
                export_format="csv",
                output_path=csv_out,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )
            export_activity_sessions(
                db_path=db_path,
                export_format="markdown",
                output_path=md_out,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )

            json_text = json_out.read_text(encoding="utf-8")
            csv_text = csv_out.read_text(encoding="utf-8")
            md_text = md_out.read_text(encoding="utf-8")

        self.assertIn('"app_name": "Code"', json_text)
        self.assertIn("start_time,end_time,duration_sec", csv_text)
        self.assertIn("| Start | End | Duration (min) |", md_text)

    def test_weekly_report_and_goal_drift(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            goals_path = temp / "goals.yaml"
            goals_path.write_text(
                """
goals:
  Client Delivery: 70
  Learning: 10
  NZ Migration: 10
  Personal Projects: 10
""".strip()
                + "\n",
                encoding="utf-8",
            )
            self._seed_sessions(db_path)

            report = generate_weekly_report_markdown(
                db_path=db_path,
                days=7,
                goals_path=goals_path,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )
            drift = analyze_goal_allocation(
                db_path=db_path,
                goals_path=goals_path,
                days=7,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )

        self.assertIn("# Week Summary", report)
        self.assertIn("Focus Time:", report)
        self.assertIn("Goal Allocation Drift", report)

        # Seed data contains no NZ Migration sessions, so it should be under target.
        nz_items = [item for item in drift if item.goal == "NZ Migration"]
        self.assertEqual(len(nz_items), 1)
        self.assertLess(nz_items[0].actual_pct, nz_items[0].planned_pct)

    def _seed_sessions(self, db_path: Path) -> None:
        sessions = [
            ActivitySession(
                start_time=datetime(2026, 7, 10, 9, 0, tzinfo=UTC),
                end_time=datetime(2026, 7, 10, 11, 0, tzinfo=UTC),
                duration_sec=7200,
                app_name="Code",
                process_name="code",
                window_title="main.py",
                browser_domain=None,
                is_idle=False,
                idle_seconds=0,
                platform="linux",
                git_repo="workgraph",
                git_branch="main",
                context_switches=1,
                tag="Client Delivery",
            ),
            ActivitySession(
                start_time=datetime(2026, 7, 10, 11, 15, tzinfo=UTC),
                end_time=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
                duration_sec=2700,
                app_name="Chrome",
                process_name="chrome",
                window_title="Course lesson",
                browser_domain="udemy.com",
                is_idle=False,
                idle_seconds=0,
                platform="linux",
                git_repo=None,
                git_branch=None,
                context_switches=1,
                tag="Learning",
            ),
            ActivitySession(
                start_time=datetime(2026, 7, 10, 13, 0, tzinfo=UTC),
                end_time=datetime(2026, 7, 10, 13, 30, tzinfo=UTC),
                duration_sec=1800,
                app_name="Microsoft Teams",
                process_name="teams",
                window_title="Weekly meeting",
                browser_domain=None,
                is_idle=False,
                idle_seconds=0,
                platform="linux",
                git_repo=None,
                git_branch=None,
                context_switches=1,
                tag="Client Delivery",
            ),
        ]

        with ActivityRepository(db_path) as repository:
            repository.save_sessions(sessions)


if __name__ == "__main__":
    unittest.main()
