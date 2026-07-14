import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import app
from db.repository import ActivityRepository
from workgraph.models import ActivitySession


class TagReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "activity.db"

        with ActivityRepository(self.db_path):
            pass

        self.db_patcher = patch("api.app.get_db_path", return_value=self.db_path)
        self.mode_patcher = patch(
            "api.app._configured_dashboard_mode", return_value="standalone"
        )
        self.db_patcher.start()
        self.mode_patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.mode_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_tag_review_candidates_lists_only_untagged_by_default(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
                    end_time=datetime(2026, 7, 13, 10, 30, tzinfo=UTC),
                    duration_sec=1800,
                    app_name="Firefox",
                    process_name="firefox",
                    window_title="GitHub issue",
                    browser_domain="github.com",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo=None,
                    git_branch=None,
                    context_switches=1,
                    tag=None,
                )
            )
            repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 13, 11, 0, tzinfo=UTC),
                    end_time=datetime(2026, 7, 13, 11, 30, tzinfo=UTC),
                    duration_sec=1800,
                    app_name="Code",
                    process_name="code",
                    window_title="main.py",
                    browser_domain=None,
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="workgraph",
                    git_branch="main",
                    context_switches=0,
                    tag="Development",
                )
            )

        response = self.client.get("/api/tag-review/candidates", params={"days": 3650})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["browser_domain"], "github.com")

    def test_tag_review_assign_updates_session_and_records_action(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            session_id = repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
                    end_time=datetime(2026, 7, 13, 9, 20, tzinfo=UTC),
                    duration_sec=1200,
                    app_name="Chrome",
                    process_name="chrome",
                    window_title="TradingView",
                    browser_domain="tradingview.com",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo=None,
                    git_branch=None,
                    context_switches=0,
                    tag=None,
                )
            )

        response = self.client.post(
            "/api/tag-review/assign",
            json={
                "session_id": session_id,
                "selected_tag": "Finance",
                "reason": "Reviewed missed browser activity",
                "source_signal": "browser_domain",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session"]["tag"], "Finance")

        with ActivityRepository(self.db_path) as repository:
            stored = repository.get_session(session_id)
            review_actions = repository.list_review_actions_for_suggestions(
                days=3650,
                selected_tag="Finance",
            )

        self.assertEqual(stored["tag"], "Finance")
        self.assertEqual(len(review_actions), 1)
        self.assertEqual(review_actions[0]["source_signal"], "browser_domain")

    def test_tag_review_suggestions_and_yaml_preview_use_reviewed_actions(self) -> None:
        with ActivityRepository(self.db_path) as repository:
            first_id = repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
                    end_time=datetime(2026, 7, 13, 8, 30, tzinfo=UTC),
                    duration_sec=1800,
                    app_name="Chrome",
                    process_name="chrome",
                    window_title="GitHub",
                    browser_domain="github.com",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="/tmp/workgraph",
                    git_branch="main",
                    context_switches=0,
                    tag=None,
                )
            )
            second_id = repository.save_session(
                ActivitySession(
                    start_time=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
                    end_time=datetime(2026, 7, 13, 9, 25, tzinfo=UTC),
                    duration_sec=1500,
                    app_name="Chrome",
                    process_name="chrome",
                    window_title="GitHub PR",
                    browser_domain="github.com",
                    is_idle=False,
                    idle_seconds=0,
                    platform="linux",
                    git_repo="/tmp/workgraph/.git",
                    git_branch="main",
                    context_switches=0,
                    tag=None,
                )
            )
            repository.create_tag_review_action(
                session_id=first_id,
                original_tag=None,
                selected_tag="Development",
                source_signal="browser_domain",
            )
            repository.create_tag_review_action(
                session_id=second_id,
                original_tag=None,
                selected_tag="Development",
                source_signal="git_repo",
            )

        custom_rules = {
            "Development": {
                "repos": ["workgraph"],
                "domains": ["github.com"],
                "keywords": ["code", "debug"],
            }
        }

        with patch("api.app.load_custom_tag_rules", return_value=custom_rules):
            suggestions_response = self.client.post(
                "/api/tag-review/suggestions",
                json={
                    "days": 3650,
                    "selected_tag": "Development",
                    "min_domain_hits": 2,
                    "min_repo_hits": 2,
                },
            )
            preview_response = self.client.get(
                "/api/tag-review/yaml-preview",
                params={
                    "days": 3650,
                    "selected_tag": "Development",
                    "min_domain_hits": 2,
                    "min_repo_hits": 2,
                },
            )

        self.assertEqual(suggestions_response.status_code, 200)
        suggestions = suggestions_response.json()["suggestions"]["Development"]
        self.assertEqual(suggestions["domains"][0]["value"], "github.com")
        self.assertEqual(suggestions["repos"][0]["value"], "workgraph")
        self.assertEqual(suggestions["keywords"], ["code", "debug"])

        self.assertEqual(preview_response.status_code, 200)
        preview_payload = preview_response.json()
        self.assertIn("Development:", preview_payload["yaml"])
        self.assertIn("github.com", preview_payload["yaml"])

    def test_tag_review_yaml_download_returns_attachment(self) -> None:
        with patch(
            "api.app._build_tag_review_payload",
            return_value=(
                {
                    "Development": {
                        "repos": [],
                        "domains": [],
                        "keywords": [],
                        "total_reviewed": 0,
                    }
                },
                "tags:\n  Development: {}\n",
                [],
            ),
        ):
            response = self.client.post(
                "/api/tag-review/yaml-download", json={"days": 30}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/x-yaml")
        self.assertIn(
            'attachment; filename="tag-review-preview.yaml"',
            response.headers["content-disposition"],
        )
        self.assertIn("Development", response.text)

    def test_tag_review_page_renders_in_standalone_mode(self) -> None:
        response = self.client.get("/tag-review")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tag Review", response.text)
        self.assertIn("Missed Entries", response.text)
        self.assertIn("YAML Preview", response.text)

    def test_sync_server_mode_disables_tag_review_routes(self) -> None:
        with patch("api.app._configured_dashboard_mode", return_value="sync-server"):
            page = self.client.get("/tag-review")
            candidates = self.client.get("/api/tag-review/candidates")
            assign = self.client.post(
                "/api/tag-review/assign",
                json={"session_id": 1, "selected_tag": "Development"},
            )
            preview = self.client.get("/api/tag-review/yaml-preview")

        self.assertEqual(page.status_code, 404)
        self.assertEqual(candidates.status_code, 404)
        self.assertEqual(assign.status_code, 404)
        self.assertEqual(preview.status_code, 404)


if __name__ == "__main__":
    unittest.main()
