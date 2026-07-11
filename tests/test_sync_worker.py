from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from db.repository import ActivityRepository
from services.sync_worker import SyncWorker, SyncWorkerSettings


class FakeSyncClient:
    def __init__(self, *, push_responses: list[dict], pull_responses: list[dict]) -> None:
        self._push_responses = list(push_responses)
        self._pull_responses = list(pull_responses)
        self.push_payloads: list[dict] = []
        self.pull_payloads: list[dict] = []

    def push(self, payload: dict) -> dict:
        self.push_payloads.append(payload)
        if self._push_responses:
            return self._push_responses.pop(0)
        return {}

    def pull(self, payload: dict) -> dict:
        self.pull_payloads.append(payload)
        if self._pull_responses:
            return self._pull_responses.pop(0)
        return {"changes": {}, "has_more": False}


class SyncWorkerTests(unittest.TestCase):
    def test_sync_worker_pushes_local_changes_and_updates_cursors(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"
            identity_path = Path(temp_dir) / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "start_time": "2026-07-11T09:00:00+00:00",
                        "end_time": "2026-07-11T09:10:00+00:00",
                        "duration_sec": 600,
                        "app_name": "Code",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-11T09:10:00+00:00",
                    }
                )

                client = FakeSyncClient(
                    push_responses=[{"next_push_cursor": "push-cursor-1"}],
                    pull_responses=[
                        {
                            "changes": {
                                "journal_entries": [
                                    {
                                        "uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                                        "created_at": "2026-07-11T10:00:00+00:00",
                                        "updated_at": "2026-07-11T10:00:00+00:00",
                                        "title": "From server",
                                        "notes": "Pulled journal",
                                    }
                                ]
                            },
                            "next_cursor": "pull-cursor-1",
                            "has_more": False,
                        }
                    ],
                )

                worker = SyncWorker(
                    repository,
                    client,
                    SyncWorkerSettings(batch_size=1000, pull_limit=1000, max_pull_pages=5),
                )
                summary = worker.run_once()
                state = repository.get_sync_state()
                journals = repository.recent_journal_entries(limit=10)

        self.assertEqual(summary["push"]["sessions"], 1)
        self.assertEqual(summary["pull"]["journal_entries"], 1)
        self.assertEqual(len(client.push_payloads), 1)
        self.assertEqual(len(client.pull_payloads), 1)
        self.assertEqual(state["last_push_cursor"], "push-cursor-1")
        self.assertEqual(state["last_pull_cursor"], "pull-cursor-1")
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]["uuid"], "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    def test_sync_worker_applies_tombstones_from_pull(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"
            identity_path = Path(temp_dir) / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.upsert_session_by_uuid(
                    {
                        "uuid": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                        "start_time": "2026-07-11T11:00:00+00:00",
                        "end_time": "2026-07-11T11:20:00+00:00",
                        "duration_sec": 1200,
                        "app_name": "Terminal",
                        "is_idle": 0,
                        "idle_seconds": 0,
                        "platform": "linux",
                        "updated_at": "2026-07-11T11:20:00+00:00",
                    }
                )

                client = FakeSyncClient(
                    push_responses=[{"next_push_cursor": "push-cursor-2"}],
                    pull_responses=[
                        {
                            "changes": {
                                "tombstones": [
                                    {
                                        "entity": "sessions",
                                        "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                                        "deleted_at": "2026-07-11T12:00:00+00:00",
                                    }
                                ]
                            },
                            "next_cursor": "pull-cursor-2",
                            "has_more": False,
                        }
                    ],
                )

                worker = SyncWorker(repository, client)
                worker.run_once()
                row = repository.list_session_changes_since(limit=10)[0]
                state = repository.get_sync_state()

        self.assertEqual(row["deleted_at"], "2026-07-11T12:00:00+00:00")
        self.assertEqual(state["last_push_cursor"], "push-cursor-2")
        self.assertEqual(state["last_pull_cursor"], "pull-cursor-2")


if __name__ == "__main__":
    unittest.main()
