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


if __name__ == "__main__":
    unittest.main()
