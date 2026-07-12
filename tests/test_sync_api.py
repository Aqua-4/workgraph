import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.app import app


class SyncApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sync-server.db"
        self.patcher = patch("api.app.get_db_path", return_value=self.db_path)
        self.patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_register_push_pull_and_idempotent_batch(self) -> None:
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
        token = register_response.json()["device_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        push_payload = {
            "device_id": "device-1",
            "user_id": "user-1",
            "client_cursor": None,
            "batch_id": "batch-1",
            "changes": {
                "sessions": [
                    {
                        "uuid": "sess-1",
                        "user_id": "user-1",
                        "device_id": "device-1",
                        "created_at": "2026-07-11T10:00:00+00:00",
                        "updated_at": "2026-07-11T10:00:00+00:00",
                        "app_name": "Code",
                        "duration_sec": 600,
                    }
                ],
                "journal_entries": [
                    {
                        "uuid": "journal-1",
                        "user_id": "user-1",
                        "device_id": "device-1",
                        "created_at": "2026-07-11T10:05:00+00:00",
                        "updated_at": "2026-07-11T10:05:00+00:00",
                        "title": "Incident note",
                    }
                ],
                "daily_reflections": [
                    {
                        "uuid": "refl-1",
                        "user_id": "user-1",
                        "device_id": "device-1",
                        "date": "2026-07-11",
                        "created_at": "2026-07-11T12:00:00+00:00",
                        "updated_at": "2026-07-11T12:00:00+00:00",
                        "wins": "Completed sync",
                    }
                ],
            },
        }

        push_response = self.client.post(
            "/api/sync/v1/push",
            json=push_payload,
            headers=auth_headers,
        )
        self.assertEqual(push_response.status_code, 200)
        push_json = push_response.json()
        self.assertEqual(push_json["accepted"]["sessions"], 1)
        self.assertEqual(push_json["accepted"]["journal_entries"], 1)
        self.assertEqual(push_json["accepted"]["daily_reflections"], 1)

        # Same batch replay should return same response body.
        replay_response = self.client.post(
            "/api/sync/v1/push",
            json=push_payload,
            headers=auth_headers,
        )
        self.assertEqual(replay_response.status_code, 200)
        self.assertEqual(replay_response.json(), push_json)

        pull_response = self.client.post(
            "/api/sync/v1/pull",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "cursor": None,
                "limit": 1000,
            },
            headers=auth_headers,
        )
        self.assertEqual(pull_response.status_code, 200)
        pull_json = pull_response.json()
        self.assertEqual(len(pull_json["changes"]["sessions"]), 1)
        self.assertEqual(len(pull_json["changes"]["journal_entries"]), 1)
        self.assertEqual(len(pull_json["changes"]["daily_reflections"]), 1)

    def test_push_rejects_invalid_token(self) -> None:
        response = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "client_cursor": None,
                "batch_id": "batch-x",
                "changes": {},
            },
            headers={"Authorization": "Bearer invalid"},
        )
        self.assertEqual(response.status_code, 401)

    def test_pull_uses_global_cursor_paging(self) -> None:
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
        token = register_response.json()["device_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        push_response = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "client_cursor": None,
                "batch_id": "batch-global-page",
                "changes": {
                    "sessions": [
                        {
                            "uuid": "sess-1",
                            "created_at": "2026-07-11T10:00:00+00:00",
                            "updated_at": "2026-07-11T10:00:00+00:00",
                            "app_name": "Code",
                            "duration_sec": 600,
                        }
                    ],
                    "journal_entries": [
                        {
                            "uuid": "journal-1",
                            "created_at": "2026-07-11T10:01:00+00:00",
                            "updated_at": "2026-07-11T10:01:00+00:00",
                            "title": "Note",
                        }
                    ],
                    "daily_reflections": [
                        {
                            "uuid": "refl-1",
                            "date": "2026-07-11",
                            "created_at": "2026-07-11T10:02:00+00:00",
                            "updated_at": "2026-07-11T10:02:00+00:00",
                            "wins": "Done",
                        }
                    ],
                },
            },
            headers=auth_headers,
        )
        self.assertEqual(push_response.status_code, 200)

        pull_page_1 = self.client.post(
            "/api/sync/v1/pull",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "cursor": None,
                "limit": 2,
            },
            headers=auth_headers,
        )
        self.assertEqual(pull_page_1.status_code, 200)
        page_1 = pull_page_1.json()
        total_1 = (
            len(page_1["changes"]["sessions"])
            + len(page_1["changes"]["journal_entries"])
            + len(page_1["changes"]["daily_reflections"])
            + len(page_1["changes"]["tombstones"])
        )
        self.assertEqual(total_1, 2)
        self.assertTrue(page_1["has_more"])

        pull_page_2 = self.client.post(
            "/api/sync/v1/pull",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "cursor": page_1["next_cursor"],
                "limit": 2,
            },
            headers=auth_headers,
        )
        self.assertEqual(pull_page_2.status_code, 200)
        page_2 = pull_page_2.json()
        total_2 = (
            len(page_2["changes"]["sessions"])
            + len(page_2["changes"]["journal_entries"])
            + len(page_2["changes"]["daily_reflections"])
            + len(page_2["changes"]["tombstones"])
        )
        self.assertEqual(total_2, 1)
        self.assertFalse(page_2["has_more"])

    def test_sync_health_and_two_device_conflict_resolution(self) -> None:
        register_device_1 = self.client.post(
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
        register_device_2 = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-1", "name": "Parashar"},
                "device": {
                    "id": "device-2",
                    "name": "Home PC",
                    "type": "personal",
                    "hostname": "HOME-001",
                    "category": "personal",
                },
            },
        )
        self.assertEqual(register_device_1.status_code, 200)
        self.assertEqual(register_device_2.status_code, 200)

        token_1 = register_device_1.json()["device_token"]
        token_2 = register_device_2.json()["device_token"]
        headers_1 = {"Authorization": f"Bearer {token_1}"}
        headers_2 = {"Authorization": f"Bearer {token_2}"}

        push_old = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "client_cursor": None,
                "batch_id": "batch-conflict-1",
                "changes": {
                    "sessions": [],
                    "journal_entries": [
                        {
                            "uuid": "journal-conflict-1",
                            "created_at": "2026-07-11T08:00:00+00:00",
                            "updated_at": "2026-07-11T08:00:00+00:00",
                            "title": "Conflict Entry",
                            "notes": "Old value",
                        }
                    ],
                    "daily_reflections": [],
                },
            },
            headers=headers_1,
        )
        self.assertEqual(push_old.status_code, 200)

        push_new = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-2",
                "user_id": "user-1",
                "client_cursor": None,
                "batch_id": "batch-conflict-2",
                "changes": {
                    "sessions": [],
                    "journal_entries": [
                        {
                            "uuid": "journal-conflict-1",
                            "created_at": "2026-07-11T08:00:00+00:00",
                            "updated_at": "2026-07-11T09:00:00+00:00",
                            "title": "Conflict Entry",
                            "notes": "New value",
                        }
                    ],
                    "daily_reflections": [],
                },
            },
            headers=headers_2,
        )
        self.assertEqual(push_new.status_code, 200)

        pull_after_conflict = self.client.post(
            "/api/sync/v1/pull",
            json={
                "device_id": "device-1",
                "user_id": "user-1",
                "cursor": None,
                "limit": 100,
            },
            headers=headers_1,
        )
        self.assertEqual(pull_after_conflict.status_code, 200)
        pulled = pull_after_conflict.json()["changes"]["journal_entries"]
        self.assertEqual(len(pulled), 1)
        self.assertEqual(pulled[0]["notes"], "New value")

        health_response = self.client.get("/api/sync/health")
        self.assertEqual(health_response.status_code, 200)
        health = health_response.json()
        self.assertTrue(health["enabled"])
        self.assertEqual(health["registered_devices"], 2)
        self.assertEqual(health["active_tokens"], 2)
        self.assertIn("push_success_rate", health)
        self.assertIn("push_conflict_rate", health)
        self.assertIn("avg_push_latency_ms", health)
        self.assertIn("synced_devices", health)
        self.assertIn("unsynced_devices", health)
        self.assertIn("device_statuses", health)
        self.assertEqual(len(health["device_statuses"]), 2)

    def test_retry_storm_duplicate_batch_id_is_idempotent(self) -> None:
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
        token = register_response.json()["device_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        push_payload = {
            "device_id": "device-1",
            "user_id": "user-1",
            "client_cursor": None,
            "batch_id": "batch-retry-storm-1",
            "changes": {
                "sessions": [
                    {
                        "uuid": "sess-storm-1",
                        "created_at": "2026-07-11T10:00:00+00:00",
                        "updated_at": "2026-07-11T10:00:00+00:00",
                        "app_name": "Code",
                        "duration_sec": 600,
                    }
                ],
                "journal_entries": [],
                "daily_reflections": [],
            },
        }

        first_response = self.client.post(
            "/api/sync/v1/push",
            json=push_payload,
            headers=auth_headers,
        )
        self.assertEqual(first_response.status_code, 200)
        first_body = first_response.json()

        for _ in range(30):
            replay_response = self.client.post(
                "/api/sync/v1/push",
                json=push_payload,
                headers=auth_headers,
            )
            self.assertEqual(replay_response.status_code, 200)
            self.assertEqual(replay_response.json(), first_body)

        health = self.client.get("/api/sync/health").json()
        self.assertEqual(health["push_requests_total"], 31)
        self.assertEqual(health["push_conflict_rate"], 0.0)
        self.assertEqual(health["push_success_rate"], 1.0)

    @unittest.skipUnless(
        os.getenv("WORKGRAPH_RUN_LOAD_TESTS") == "1",
        "Set WORKGRAPH_RUN_LOAD_TESTS=1 to run 100k batch push safety test.",
    )
    def test_load_batch_push_100k_sessions(self) -> None:
        register_response = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-load", "name": "Load Tester"},
                "device": {
                    "id": "device-load",
                    "name": "Load Device",
                    "type": "work",
                    "hostname": "LOAD-001",
                    "category": "test",
                },
            },
        )
        self.assertEqual(register_response.status_code, 200)
        token = register_response.json()["device_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        sessions = [
            {
                "uuid": f"sess-load-{i}",
                "created_at": "2026-07-11T10:00:00+00:00",
                "updated_at": f"2026-07-11T10:00:{i % 60:02d}+00:00",
                "app_name": "Code",
                "duration_sec": 60,
            }
            for i in range(100_000)
        ]

        push_response = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-load",
                "user_id": "user-load",
                "client_cursor": None,
                "batch_id": "batch-load-100k",
                "changes": {
                    "sessions": sessions,
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers=auth_headers,
        )
        self.assertEqual(push_response.status_code, 200)
        body = push_response.json()
        self.assertEqual(body["accepted"]["sessions"], 100_000)

    def test_pull_is_scoped_to_user_and_metadata_endpoints_work(self) -> None:
        reg_user_1 = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-a", "name": "User A"},
                "device": {
                    "id": "device-a1",
                    "name": "A Laptop",
                    "type": "work",
                    "hostname": "A-1",
                    "category": "linux",
                },
            },
        )
        reg_user_2 = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-b", "name": "User B"},
                "device": {
                    "id": "device-b1",
                    "name": "B Laptop",
                    "type": "work",
                    "hostname": "B-1",
                    "category": "windows",
                },
            },
        )
        self.assertEqual(reg_user_1.status_code, 200)
        self.assertEqual(reg_user_2.status_code, 200)

        token_a = reg_user_1.json()["device_token"]
        token_b = reg_user_2.json()["device_token"]

        push_a = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-a1",
                "user_id": "user-a",
                "client_cursor": None,
                "batch_id": "batch-a",
                "changes": {
                    "sessions": [
                        {
                            "uuid": "sess-a",
                            "created_at": "2026-07-11T10:00:00+00:00",
                            "updated_at": "2026-07-11T10:00:00+00:00",
                            "start_time": "2026-07-11T10:00:00+00:00",
                            "end_time": "2026-07-11T10:30:00+00:00",
                            "duration_sec": 1800,
                            "app_name": "Code",
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        push_b = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-b1",
                "user_id": "user-b",
                "client_cursor": None,
                "batch_id": "batch-b",
                "changes": {
                    "sessions": [
                        {
                            "uuid": "sess-b",
                            "created_at": "2026-07-11T11:00:00+00:00",
                            "updated_at": "2026-07-11T11:00:00+00:00",
                            "start_time": "2026-07-11T11:00:00+00:00",
                            "end_time": "2026-07-11T11:45:00+00:00",
                            "duration_sec": 2700,
                            "app_name": "Browser",
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token_b}"},
        )
        self.assertEqual(push_a.status_code, 200)
        self.assertEqual(push_b.status_code, 200)

        pull_a = self.client.post(
            "/api/sync/v1/pull",
            json={
                "device_id": "device-a1",
                "user_id": "user-a",
                "cursor": None,
                "limit": 100,
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        self.assertEqual(pull_a.status_code, 200)
        sessions_a = pull_a.json()["changes"]["sessions"]
        self.assertEqual(len(sessions_a), 1)
        self.assertEqual(sessions_a[0]["uuid"], "sess-a")

        users_response = self.client.get("/api/sync/users")
        self.assertEqual(users_response.status_code, 200)
        self.assertGreaterEqual(len(users_response.json()["users"]), 2)

        devices_response = self.client.get("/api/sync/devices", params={"user_id": "user-a"})
        self.assertEqual(devices_response.status_code, 200)
        devices = devices_response.json()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], "device-a1")

    def test_sync_stats_endpoint_and_api_stats_source_sync(self) -> None:
        register_response = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-sync", "name": "Sync User"},
                "device": {
                    "id": "device-sync-1",
                    "name": "Sync Laptop",
                    "type": "work",
                    "hostname": "SYNC-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(register_response.status_code, 200)
        token = register_response.json()["device_token"]

        push_response = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-sync-1",
                "user_id": "user-sync",
                "client_cursor": None,
                "batch_id": "batch-sync-stats-1",
                "changes": {
                    "sessions": [
                        {
                            "uuid": "sess-sync-1",
                            "created_at": "2026-07-11T10:00:00+00:00",
                            "updated_at": "2026-07-11T10:00:00+00:00",
                            "start_time": "2026-07-11T10:00:00+00:00",
                            "end_time": "2026-07-11T11:00:00+00:00",
                            "duration_sec": 3600,
                            "app_name": "Code",
                            "tag": "Build",
                            "git_repo": "workgraph",
                            "focus_seconds": 1200,
                            "meeting_seconds": 300,
                            "context_switches": 5,
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(push_response.status_code, 200)

        stats_response = self.client.get(
            "/api/sync/stats",
            params={"user_id": "user-sync", "days": 3650},
        )
        self.assertEqual(stats_response.status_code, 200)
        stats = stats_response.json()
        self.assertEqual(stats["total_seconds"], 3600)
        self.assertEqual(stats["meeting_seconds"], 300)
        self.assertIn("Build", stats["tag_stats"])

        api_stats_sync = self.client.get(
            "/api/stats",
            params={"source": "sync", "user_id": "user-sync", "days": 30},
        )
        self.assertEqual(api_stats_sync.status_code, 200)
        body = api_stats_sync.json()
        self.assertEqual(body["total_seconds"], 3600)
        self.assertIn("Code", body["app_stats"])

    def test_same_uuid_allowed_for_different_users(self) -> None:
        reg_a = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-dup-a", "name": "User A"},
                "device": {
                    "id": "device-dup-a",
                    "name": "Laptop A",
                    "type": "work",
                    "hostname": "A",
                    "category": "linux",
                },
            },
        )
        reg_b = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-dup-b", "name": "User B"},
                "device": {
                    "id": "device-dup-b",
                    "name": "Laptop B",
                    "type": "work",
                    "hostname": "B",
                    "category": "windows",
                },
            },
        )
        self.assertEqual(reg_a.status_code, 200)
        self.assertEqual(reg_b.status_code, 200)

        token_a = reg_a.json()["device_token"]
        token_b = reg_b.json()["device_token"]

        shared_uuid = "sess-shared-uuid"
        push_a = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-dup-a",
                "user_id": "user-dup-a",
                "client_cursor": None,
                "batch_id": "batch-dup-a",
                "changes": {
                    "sessions": [
                        {
                            "uuid": shared_uuid,
                            "created_at": "2026-07-11T10:00:00+00:00",
                            "updated_at": "2026-07-11T10:00:00+00:00",
                            "start_time": "2026-07-11T10:00:00+00:00",
                            "end_time": "2026-07-11T10:30:00+00:00",
                            "duration_sec": 1800,
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        push_b = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-dup-b",
                "user_id": "user-dup-b",
                "client_cursor": None,
                "batch_id": "batch-dup-b",
                "changes": {
                    "sessions": [
                        {
                            "uuid": shared_uuid,
                            "created_at": "2026-07-11T11:00:00+00:00",
                            "updated_at": "2026-07-11T11:00:00+00:00",
                            "start_time": "2026-07-11T11:00:00+00:00",
                            "end_time": "2026-07-11T11:20:00+00:00",
                            "duration_sec": 1200,
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token_b}"},
        )
        self.assertEqual(push_a.status_code, 200)
        self.assertEqual(push_b.status_code, 200)

        pull_a = self.client.post(
            "/api/sync/v1/pull",
            json={"device_id": "device-dup-a", "user_id": "user-dup-a", "cursor": None, "limit": 100},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        pull_b = self.client.post(
            "/api/sync/v1/pull",
            json={"device_id": "device-dup-b", "user_id": "user-dup-b", "cursor": None, "limit": 100},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        self.assertEqual(pull_a.status_code, 200)
        self.assertEqual(pull_b.status_code, 200)
        self.assertEqual(len(pull_a.json()["changes"]["sessions"]), 1)
        self.assertEqual(len(pull_b.json()["changes"]["sessions"]), 1)

    def test_sync_rollup_rebuild_endpoint(self) -> None:
        reg = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-rebuild", "name": "Rebuilder"},
                "device": {
                    "id": "device-rebuild-1",
                    "name": "Rebuild Device",
                    "type": "work",
                    "hostname": "RB-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(reg.status_code, 200)
        token = reg.json()["device_token"]

        push = self.client.post(
            "/api/sync/v1/push",
            json={
                "device_id": "device-rebuild-1",
                "user_id": "user-rebuild",
                "client_cursor": None,
                "batch_id": "batch-rebuild-1",
                "changes": {
                    "sessions": [
                        {
                            "uuid": "sess-rebuild-1",
                            "created_at": "2026-07-10T10:00:00+00:00",
                            "updated_at": "2026-07-10T10:00:00+00:00",
                            "start_time": "2026-07-10T10:00:00+00:00",
                            "end_time": "2026-07-10T10:30:00+00:00",
                            "duration_sec": 1800,
                        }
                    ],
                    "journal_entries": [],
                    "daily_reflections": [],
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(push.status_code, 200)

        rebuild = self.client.post(
            "/api/sync/rollups/rebuild",
            params={"user_id": "user-rebuild"},
        )
        self.assertEqual(rebuild.status_code, 200)
        self.assertGreaterEqual(rebuild.json()["rebuilt_rows"], 1)

    def test_mode_aware_device_register_standalone_and_sync_server(self) -> None:
        standalone_response = self.client.post(
            "/api/device/register",
            json={
                "mode": "standalone",
                "user": {"id": "user-standalone", "name": "Standalone User"},
                "device": {
                    "id": "device-standalone-1",
                    "name": "Standalone Laptop",
                    "type": "personal",
                    "hostname": "ST-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(standalone_response.status_code, 200)
        self.assertTrue(standalone_response.json()["registered"])
        self.assertIsNone(standalone_response.json()["device_token"])

        server_response = self.client.post(
            "/api/device/register",
            json={
                "mode": "sync-server",
                "user": {"id": "user-sync-server", "name": "Server User"},
                "device": {
                    "id": "device-sync-server-1",
                    "name": "Server Laptop",
                    "type": "work",
                    "hostname": "SV-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(server_response.status_code, 200)
        self.assertTrue(server_response.json()["registered"])
        self.assertTrue(server_response.json()["device_token"])

    def test_mode_aware_device_register_sync_client_requires_base_url(self) -> None:
        response = self.client.post(
            "/api/device/register",
            json={
                "mode": "sync-client",
                "user": {"id": "user-client", "name": "Client User"},
                "device": {
                    "id": "device-client-1",
                    "name": "Client Device",
                    "type": "work",
                    "hostname": "CL-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_mode_aware_device_register_sync_client_forwards_registration(self) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"device_token":"remote-token","server_time":"2026-07-12T00:00:00+00:00"}'
        context_manager = MagicMock()
        context_manager.__enter__.return_value = mock_response
        context_manager.__exit__.return_value = None

        with patch("api.app.urllib_request.urlopen", return_value=context_manager) as mocked_urlopen:
            response = self.client.post(
                "/api/device/register",
                json={
                    "mode": "sync-client",
                    "sync_base_url": "http://127.0.0.1:8000",
                    "user": {"id": "user-client", "name": "Client User"},
                    "device": {
                        "id": "device-client-2",
                        "name": "Client Device 2",
                        "type": "work",
                        "hostname": "CL-2",
                        "category": "linux",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["device_token"], "remote-token")
        mocked_urlopen.assert_called_once()

    def test_dashboard_mode_is_enforced_from_settings(self) -> None:
        bootstrap = self.client.post(
            "/api/device/register",
            json={
                "mode": "standalone",
                "user": {"id": "user-bootstrap", "name": "Bootstrap User"},
                "device": {
                    "id": "device-bootstrap-1",
                    "name": "Bootstrap Device",
                    "type": "personal",
                    "hostname": "BP-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(bootstrap.status_code, 200)

        with patch("api.app._configured_dashboard_mode", return_value="standalone"):
            local_response = self.client.get("/")
            self.assertEqual(local_response.status_code, 200)
            self.assertIn("Configured Mode", local_response.text)
            self.assertIn("standalone", local_response.text)

            attempted_override = self.client.get("/?mode=sync-server")
            self.assertEqual(attempted_override.status_code, 200)
            self.assertIn("standalone", attempted_override.text)

    def test_api_stats_uses_configured_mode_over_query_source(self) -> None:
        register_response = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-mode-sync", "name": "Mode Sync User"},
                "device": {
                    "id": "device-mode-sync-1",
                    "name": "Mode Sync Device",
                    "type": "work",
                    "hostname": "MS-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(register_response.status_code, 200)

        with patch("api.app._configured_dashboard_mode", return_value="sync-server"):
            response = self.client.get("/api/stats", params={"source": "local", "user_id": "user-mode-sync", "days": 7})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIn("total_seconds", body)

    def test_dashboard_sync_server_mode_uses_aggregated_template(self) -> None:
        register_response = self.client.post(
            "/api/sync/v1/devices/register",
            json={
                "user": {"id": "user-server-dash", "name": "Server Dash User"},
                "device": {
                    "id": "device-server-dash-1",
                    "name": "Server Dash Device",
                    "type": "work",
                    "hostname": "SD-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(register_response.status_code, 200)

        with patch("api.app._configured_dashboard_mode", return_value="sync-server"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sync Server Dashboard", response.text)

    def test_dashboard_sync_client_shows_status_when_registered(self) -> None:
        bootstrap = self.client.post(
            "/api/device/register",
            json={
                "mode": "standalone",
                "user": {"id": "user-bootstrap", "name": "Bootstrap User"},
                "device": {
                    "id": "device-bootstrap-1",
                    "name": "Bootstrap Device",
                    "type": "personal",
                    "hostname": "BP-1",
                    "category": "linux",
                },
            },
        )
        self.assertEqual(bootstrap.status_code, 200)

        with patch(
            "api.app._configured_sync_client_status",
            return_value={
                "registered": True,
                "sync_base_url": "http://127.0.0.1:8000",
                "last_synced_at": "2026-07-12T12:34:56+00:00",
                "device_id": "device-client-1",
                "user_id": "user-client-1",
            },
        ), patch("api.app._configured_dashboard_mode", return_value="sync-client"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sync Client Status", response.text)
        self.assertIn("Last Synced:", response.text)
        self.assertNotIn("Sync Health", response.text)
        self.assertNotIn("Register On Sync Server", response.text)
        self.assertNotIn("Registered Devices", response.text)
        self.assertNotIn("Active Tokens", response.text)
        self.assertNotIn("No devices registered yet.", response.text)
        self.assertNotIn("User ID:", response.text)
        self.assertNotIn("Device ID:", response.text)


if __name__ == "__main__":
    unittest.main()
