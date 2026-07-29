from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from api.app import _ensure_sync_tables
from db.repository import ActivityRepository
from services.reporting import (
    analyze_goal_allocation,
    default_goals_path,
    export_activity_sessions,
    generate_goals_report_markdown,
    generate_monthly_report_markdown,
    generate_sync_report_markdown,
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

    def test_monthly_report_uses_longer_window(self) -> None:
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

            report = generate_monthly_report_markdown(
                db_path=db_path,
                days=30,
                goals_path=goals_path,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )

        self.assertIn("# Month Summary", report)
        self.assertIn("Goal Allocation Drift", report)

    def test_goals_report_surfaces_drift_summary(self) -> None:
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

            report = generate_goals_report_markdown(
                db_path=db_path,
                days=7,
                goals_path=goals_path,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )

        self.assertIn("# Goals Report", report)
        self.assertIn("Goal Allocation Drift", report)

    def test_goals_report_supports_hours_and_intent_mapping(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            goals_path = temp / "goals.yaml"
            goals_path.write_text(
                """
goals:
  Client Delivery:
    target:
      hours: 2
      period: week
    intents:
      - delivery_work
      - client_meeting
  Learning:
    target:
      hours: 1
      period: week
    intents:
      - learning
""".strip()
                + "\n",
                encoding="utf-8",
            )
            self._seed_sessions(db_path)

            report = generate_goals_report_markdown(
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

        self.assertIn("Goal Allocation Drift", report)
        self.assertIn("Hours", report)
        client_goal = next(item for item in drift if item.goal == "Client Delivery")
        self.assertGreater(client_goal.actual_hours, 0)
        self.assertGreaterEqual(client_goal.progress_pct, 0)

    def test_goal_allocation_uses_tag_intent_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            goals_path = temp / "goals.yaml"
            goals_path.write_text(
                """
goals:
  Client Delivery:
    target:
      hours: 1
      period: week
    intent_ids:
      - client_delivery
""".strip()
                + "\n",
                encoding="utf-8",
            )
            self._seed_sessions(db_path)

            drift = analyze_goal_allocation(
                db_path=db_path,
                goals_path=goals_path,
                days=7,
                now=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
            )

        client_goal = next(item for item in drift if item.goal == "Client Delivery")
        self.assertGreater(client_goal.actual_hours, 0)
        self.assertGreater(client_goal.progress_pct, 0)

    def test_sync_report_summarizes_device_health(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                _ensure_sync_tables(conn)
                conn.execute(
                    "INSERT INTO sync_users (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        "user-1",
                        "Parashar",
                        "2026-07-11T00:00:00+00:00",
                        "2026-07-11T00:00:00+00:00",
                    ),
                )
                conn.execute(
                    "INSERT INTO sync_devices (id, user_id, name, type, created_at, updated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "device-1",
                        "user-1",
                        "Office Laptop",
                        "work",
                        "2026-07-11T00:00:00+00:00",
                        "2026-07-11T01:00:00+00:00",
                        "2026-07-11T01:00:00+00:00",
                    ),
                )
                conn.execute(
                    "INSERT INTO sync_tokens (token, device_id, user_id, created_at, revoked_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        "token-1",
                        "device-1",
                        "user-1",
                        "2026-07-11T00:00:00+00:00",
                        None,
                    ),
                )
                conn.execute(
                    "INSERT INTO sync_checkpoints (device_id, user_id, last_push_cursor, last_pull_cursor, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        "device-1",
                        "user-1",
                        "2026-07-11T01:00:00+00:00|sess-1",
                        "2026-07-11T01:00:00+00:00|sess-1",
                        "2026-07-11T01:00:00+00:00",
                    ),
                )
                conn.execute(
                    "INSERT INTO sync_error_logs (endpoint, error_type, message, retry_count, status, status_code, device_id, user_id, batch_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "/api/sync/v1/push",
                        "ConflictError",
                        "Replay detected",
                        1,
                        "open",
                        409,
                        "device-1",
                        "user-1",
                        "batch-1",
                        "2026-07-11T01:05:00+00:00",
                        "2026-07-11T01:05:00+00:00",
                    ),
                )
                conn.commit()

            report = generate_sync_report_markdown(db_path=db_path)

        self.assertIn("# Sync Status Report", report)
        self.assertIn("Registered Devices: 1", report)
        self.assertIn("Pending Pull Rows:", report)
        self.assertIn("Recent Sync Errors", report)

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
