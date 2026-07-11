from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import app
from db.repository import ActivityRepository
from workgraph.models import ActivitySession


class JournalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "activity.db"

        # Initialize schema and baseline tables.
        with ActivityRepository(self.db_path):
            pass

        self.patcher = patch("api.app.get_db_path", return_value=self.db_path)
        self.patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_create_and_list_journal_entries(self) -> None:
        create_response = self.client.post(
            "/api/journal",
            json={
                "start_time": "2026-07-10T10:00:00+00:00",
                "end_time": "2026-07-10T12:00:00+00:00",
                "title": "Infra issue during demo",
                "notes": "Spent time troubleshooting lambda issue",
                "metadata": {"labels": ["incident", "client"]},
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/api/journal")
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["entries"][0]["title"], "Infra issue during demo")

    def test_update_journal_entry(self) -> None:
        create_response = self.client.post(
            "/api/journal",
            json={
                "start_time": "2026-07-10T10:00:00+00:00",
                "end_time": "2026-07-10T12:00:00+00:00",
                "title": "Original title",
                "notes": "Original notes",
                "metadata": {"labels": ["incident"], "tags": ["Client Delivery"]},
            },
        )
        self.assertEqual(create_response.status_code, 200)
        journal_id = create_response.json()["id"]

        update_response = self.client.put(
            f"/api/journal/{journal_id}",
            json={
                "start_time": "2026-07-10T10:30:00+00:00",
                "end_time": "2026-07-10T12:30:00+00:00",
                "title": "Updated title",
                "notes": "Updated notes",
                "metadata": {"labels": ["learning"], "tags": ["Learning"]},
            },
        )
        self.assertEqual(update_response.status_code, 200)

        detail_response = self.client.get(f"/api/journal/{journal_id}")
        self.assertEqual(detail_response.status_code, 200)
        entry = detail_response.json()["entry"]
        self.assertEqual(entry["title"], "Updated title")
        self.assertEqual(entry["metadata"]["tags"], ["Learning"])

    def test_list_available_journal_tags(self) -> None:
        response = self.client.get("/api/journal/tags")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("tags", payload)
        self.assertTrue(len(payload["tags"]) > 0)

    def test_correlated_sessions_summary(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 10, 15, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 10, 45, tzinfo=timezone.utc),
                    duration_sec=1800,
                    app_name="Code",
                    process_name="Code",
                    window_title="service.py",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=2,
                    tag="Client Delivery",
                )
            )

        create_response = self.client.post(
            "/api/journal",
            json={
                "start_time": "2026-07-10T10:00:00+00:00",
                "end_time": "2026-07-10T11:00:00+00:00",
                "title": "Debugging window",
                "notes": "Focused troubleshooting",
            },
        )
        journal_id = create_response.json()["id"]

        correlate_response = self.client.get(f"/api/journal/{journal_id}/correlated-sessions")
        self.assertEqual(correlate_response.status_code, 200)

        payload = correlate_response.json()
        self.assertEqual(payload["summary"]["session_count"], 1)
        self.assertEqual(payload["summary"]["total_context_switches"], 2)
        self.assertIn("Code", payload["summary"]["apps"])

    def test_upsert_and_list_reflections(self) -> None:
        response_1 = self.client.put(
            "/api/reflections/2026-07-10",
            json={
                "wins": "Completed dashboard",
                "problems": "Infra delays",
                "tomorrow": "Run dry demo",
                "energy": 6,
                "stress": 5,
            },
        )
        self.assertEqual(response_1.status_code, 200)

        response_2 = self.client.put(
            "/api/reflections/2026-07-10",
            json={
                "wins": "Completed dashboard and tests",
                "problems": "",
                "tomorrow": "Run dry demo",
                "energy": 7,
                "stress": 4,
            },
        )
        self.assertEqual(response_2.status_code, 200)

        list_response = self.client.get("/api/reflections")
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["reflections"][0]["energy"], 7)

    def test_dashboard_daily_trend_shows_weekday_labels(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
                    duration_sec=3600,
                    app_name="Code",
                    process_name="Code",
                    window_title="dashboard.html",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=3,
                    tag="Client Delivery",
                )
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Daily Trend (Last 7 Days)", response.text)
        self.assertIn(">Fri</td>", response.text)
        self.assertNotIn(">2026-07-10</td>", response.text)


if __name__ == "__main__":
    unittest.main()
