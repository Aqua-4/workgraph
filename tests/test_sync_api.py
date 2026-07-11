from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
