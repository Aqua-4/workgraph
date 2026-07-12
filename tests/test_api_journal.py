from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import app, get_summary_stats, _human_datetime
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
        self.mode_patcher = patch("api.app._configured_dashboard_mode", return_value="standalone")
        self.patcher.start()
        self.mode_patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.mode_patcher.stop()
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

    def test_human_datetime_formats_dates_and_timestamps(self) -> None:
        self.assertEqual(_human_datetime("2026-07-10"), "Jul 10, 2026")
        expected = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
        expected_text = expected.astimezone().strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ")
        self.assertEqual(_human_datetime("2026-07-10T15:30:00+00:00"), expected_text)

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

    def test_get_and_correlate_reflection(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
                    duration_sec=3600,
                    app_name="Code",
                    process_name="Code",
                    window_title="journal.html",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=1,
                    tag="Learning",
                )
            )

        save_response = self.client.put(
            "/api/reflections/2026-07-10",
            json={
                "wins": "Good focus block",
                "problems": "None",
                "tomorrow": "Continue",
                "energy": 8,
                "stress": 3,
            },
        )
        self.assertEqual(save_response.status_code, 200)

        get_response = self.client.get("/api/reflections/2026-07-10")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["reflection"]["wins"], "Good focus block")

        correlate_response = self.client.get("/api/reflections/2026-07-10/correlated-sessions")
        self.assertEqual(correlate_response.status_code, 200)
        correlate_payload = correlate_response.json()
        self.assertEqual(correlate_payload["summary"]["session_count"], 1)
        self.assertIn("Code", correlate_payload["summary"]["apps"])

    def test_create_and_filter_work_events(self) -> None:
        create_response = self.client.post(
            "/api/work-events",
            json={
                "event_time": "2026-07-10T11:00:00+00:00",
                "event_type": "Incident",
                "title": "Demo auth outage",
                "impact": "High",
                "project": "MCP Platform",
                "notes": "Login service timed out under load",
                "metadata": {"labels": ["auth", "customer"]},
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get(
            "/api/work-events",
            params={"event_type": "Incident", "impact": "High", "project": "MCP Platform"},
        )
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["title"], "Demo auth outage")

    def test_get_update_and_correlate_work_event(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 10, 45, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 11, 15, tzinfo=timezone.utc),
                    duration_sec=1800,
                    app_name="Code",
                    process_name="Code",
                    window_title="incident.md",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=4,
                    tag="Client Delivery",
                )
            )

        create_response = self.client.post(
            "/api/work-events",
            json={
                "event_time": "2026-07-10T11:00:00+00:00",
                "event_type": "Incident",
                "title": "Initial title",
                "impact": "High",
                "project": "MCP Platform",
                "notes": "Initial notes",
                "metadata": {"labels": ["auth"]},
            },
        )
        self.assertEqual(create_response.status_code, 200)
        event_id = create_response.json()["id"]

        get_response = self.client.get(f"/api/work-events/{event_id}")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["event"]["title"], "Initial title")

        update_response = self.client.put(
            f"/api/work-events/{event_id}",
            json={
                "event_time": "2026-07-10T11:00:00+00:00",
                "event_type": "Decision",
                "title": "Updated title",
                "impact": "Medium",
                "project": "MCP Platform",
                "notes": "Updated notes",
                "metadata": {"labels": ["postmortem"]},
            },
        )
        self.assertEqual(update_response.status_code, 200)

        updated_get_response = self.client.get(f"/api/work-events/{event_id}")
        self.assertEqual(updated_get_response.status_code, 200)
        self.assertEqual(updated_get_response.json()["event"]["event_type"], "Decision")

        correlate_response = self.client.get(f"/api/work-events/{event_id}/correlated-sessions")
        self.assertEqual(correlate_response.status_code, 200)
        correlate_payload = correlate_response.json()
        self.assertEqual(correlate_payload["summary"]["session_count"], 1)
        self.assertEqual(correlate_payload["summary"]["total_context_switches"], 4)

    def test_work_event_type_validation(self) -> None:
        response = self.client.post(
            "/api/work-events",
            json={
                "event_type": "UnknownType",
                "title": "Something happened",
            },
        )
        self.assertEqual(response.status_code, 422)

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

    def test_context_switch_rate_uses_session_transitions(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 9, 30, tzinfo=timezone.utc),
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
                    context_switches=0,
                    tag="Client Delivery",
                )
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 30, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
                    duration_sec=1800,
                    app_name="Chrome",
                    process_name="chrome",
                    window_title="docs",
                    browser_domain="docs.python.org",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=0,
                    tag="Learning",
                )
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 10, 30, tzinfo=timezone.utc),
                    duration_sec=1800,
                    app_name="Terminal",
                    process_name="bash",
                    window_title="workgraph",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=0,
                    tag="Operations",
                )
            )

        stats = get_summary_stats(self.db_path, days=7)
        self.assertEqual(stats["total_switches"], 2)
        self.assertAlmostEqual(stats["switch_rate_per_hour"], 1.33, places=2)
        self.assertIsNotNone(stats["goal_drift"])
        self.assertGreater(stats["goal_drift"]["goal_drift_score_pct_points"], 0)
        self.assertGreater(stats["goal_drift"]["unmapped_pct"], 0)

    def test_summary_stats_merges_short_active_sessions_into_focus_blocks(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 9, 12, tzinfo=timezone.utc),
                    duration_sec=720,
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
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 12, 30, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 9, 25, 30, tzinfo=timezone.utc),
                    duration_sec=780,
                    app_name="Terminal",
                    process_name="bash",
                    window_title="workgraph",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=1,
                    tag="Client Delivery",
                )
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 9, 25, 45, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 9, 44, 45, tzinfo=timezone.utc),
                    duration_sec=1140,
                    app_name="Browser",
                    process_name="chrome",
                    window_title="docs",
                    browser_domain="docs.python.org",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo=None,
                    git_branch=None,
                    context_switches=1,
                    tag="Learning",
                )
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 10, 10, 30, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 10, 10, 45, tzinfo=timezone.utc),
                    duration_sec=900,
                    app_name="Mail",
                    process_name="mail",
                    window_title="Inbox",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo=None,
                    git_branch=None,
                    context_switches=0,
                    tag=None,
                )
            )

        stats = get_summary_stats(self.db_path, days=7)

        self.assertEqual(stats["deep_work_blocks"], 1)
        self.assertEqual(stats["longest_focus_sec"], 2640)
        self.assertEqual(stats["avg_focus_sec"], 2640)
        self.assertAlmostEqual(stats["longest_focus_hours"], 0.73, places=2)
        self.assertAlmostEqual(stats["avg_focus_minutes"], 44.0, places=1)

    def test_timeline_and_journal_show_sync_health_when_enabled(self) -> None:
        register_response = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-1", "name": "Parashar"},
                "device": {
                    "id": "device-1",
                    "name": "Office Laptop",
                    "type": "work",
                    "hostname": "LAT-001",
                    "category": "employer",
                },
            },
        )
        self.assertEqual(register_response.status_code, 200)

        timeline_response = self.client.get("/timeline")
        self.assertEqual(timeline_response.status_code, 200)
        self.assertIn("Sync Health", timeline_response.text)
        self.assertIn("registered, not synced yet", timeline_response.text)

        journal_response = self.client.get("/journal")
        self.assertEqual(journal_response.status_code, 200)
        self.assertIn("Sync Health", journal_response.text)
        self.assertIn("registered, not synced yet", journal_response.text)

    def test_dashboard_hides_sync_health_in_standalone_mode(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Sync Health", response.text)
        self.assertNotIn("No devices registered yet.", response.text)


if __name__ == "__main__":
    unittest.main()
