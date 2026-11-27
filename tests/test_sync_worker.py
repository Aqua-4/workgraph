import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from db.repository import ActivityRepository
from services.sync_config import SyncEntity
from services.sync_worker import SyncWorker, SyncWorkerSettings
from workgraph.models import ActivitySession


class FakeSyncClient:
    def __init__(
        self, *, push_responses: list[dict], pull_responses: list[dict]
    ) -> None:
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


class FakeSyncRepository:
    def __init__(self) -> None:
        self.sync_entities = {
            "custom_items": SyncEntity(
                name="custom_items",
                table="custom_items",
                uuid="uuid",
                created_at="created_at",
                updated_at="updated_at",
                deleted_at="deleted_at",
                user_id="user_id",
                device_id="device_id",
                legacy_server_table="custom_items",
                local_id="id",
            )
        }
        self.state = {
            "device_id": "device-1",
            "user_id": "user-1",
            "last_push_cursor": None,
            "last_pull_cursor": None,
        }
        self._changes = {
            "custom_items": [
                {
                    "id": 1,
                    "uuid": "local-item",
                    "created_at": "2026-07-11T10:00:00+00:00",
                    "updated_at": "2026-07-11T10:00:00+00:00",
                    "title": "Local item",
                }
            ]
        }
        self.upserted: list[tuple[str, dict[str, object]]] = []
        self.deleted: list[tuple[str, str, str, str | None]] = []

    def get_sync_state(self) -> dict[str, object]:
        return self.state

    def update_sync_state(
        self,
        *,
        last_push_cursor: str | None,
        last_pull_cursor: str | None,
    ) -> None:
        self.state["last_push_cursor"] = last_push_cursor
        self.state["last_pull_cursor"] = last_pull_cursor

    def list_changes_since(
        self,
        *,
        entity: str,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        return self._changes.get(entity, []) if cursor is None else []

    def upsert_sync_entity(self, entity: str, payload: dict[str, object]) -> None:
        self.upserted.append((entity, payload))

    def mark_deleted(
        self,
        *,
        entity: str,
        row_uuid: str,
        deleted_at: str,
        updated_at: str | None = None,
    ) -> None:
        self.deleted.append((entity, row_uuid, deleted_at, updated_at))


class SyncWorkerTests(unittest.TestCase):
    def test_worker_accepts_repository_protocol_implementation(self) -> None:
        repository = FakeSyncRepository()
        client = FakeSyncClient(
            push_responses=[
                {
                    "supported_entities": ["custom_items"],
                    "accepted": {"custom_items": 1},
                    "next_push_cursor": "push-1",
                }
            ],
            pull_responses=[
                {
                    "supported_entities": ["custom_items"],
                    "changes": {
                        "custom_items": [
                            {
                                "uuid": "remote-item",
                                "created_at": "2026-07-11T11:00:00+00:00",
                                "updated_at": "2026-07-11T11:00:00+00:00",
                                "title": "Remote item",
                            }
                        ],
                        "tombstones": [
                            {
                                "entity": "custom_items",
                                "id": "deleted-item",
                                "deleted_at": "2026-07-11T12:00:00+00:00",
                            }
                        ],
                    },
                    "next_cursor": "pull-1",
                    "has_more": False,
                }
            ],
        )

        summary = SyncWorker(repository, client).run_once()

        self.assertEqual(summary["push"]["custom_items"], 1)
        self.assertEqual(summary["pull"]["custom_items"], 1)
        self.assertEqual(repository.state["last_push_cursor"], "push-1")
        self.assertEqual(repository.state["last_pull_cursor"], "pull-1")
        self.assertEqual(repository.upserted[0][0], "custom_items")
        self.assertEqual(repository.upserted[0][1]["uuid"], "remote-item")
        self.assertEqual(
            repository.deleted,
            [
                (
                    "custom_items",
                    "deleted-item",
                    "2026-07-11T12:00:00+00:00",
                    "2026-07-11T12:00:00+00:00",
                )
            ],
        )

    def test_worker_syncs_entity_added_only_to_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            identity_path = temp / "identity.json"
            sync_config_path = temp / "sync.yaml"
            sync_config_path.write_text(
                "version: 1\n"
                "entities:\n"
                "  custom_items:\n"
                "    table: custom_items\n"
                "    local_id: id\n"
                "    uuid: uuid\n"
                "    created_at: created_at\n"
                "    updated_at: updated_at\n"
                "    deleted_at: deleted_at\n"
                "    user_id: user_id\n"
                "    device_id: device_id\n",
                encoding="utf-8",
            )
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """CREATE TABLE custom_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uuid TEXT UNIQUE,
                        user_id TEXT,
                        device_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT,
                        deleted_at TEXT,
                        title TEXT NOT NULL
                    )"""
                )

            with ActivityRepository(
                db_path,
                identity_path=identity_path,
                sync_config_path=sync_config_path,
            ) as repository:
                repository._connection.execute(
                    """INSERT INTO custom_items (
                        uuid, user_id, device_id, created_at, updated_at, title
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "local-item",
                        repository._user_id,
                        repository._device_id,
                        "2026-07-11T10:00:00+00:00",
                        "2026-07-11T10:00:00+00:00",
                        "Local item",
                    ),
                )
                client = FakeSyncClient(
                    push_responses=[{"supported_entities": ["custom_items"]}],
                    pull_responses=[
                        {
                            "changes": {
                                "custom_items": [
                                    {
                                        "id": 999,
                                        "uuid": "remote-item",
                                        "created_at": "2026-07-11T11:00:00+00:00",
                                        "updated_at": "2026-07-11T11:00:00+00:00",
                                        "title": "Remote item",
                                    }
                                ]
                            },
                            "supported_entities": ["custom_items"],
                            "next_cursor": "pull-custom-1",
                            "has_more": False,
                        }
                    ],
                )
                summary = SyncWorker(repository, client).run_once()
                rows = repository.list_changes_since(entity="custom_items", limit=10)

        self.assertEqual(summary["push"]["custom_items"], 1)
        self.assertEqual(summary["pull"]["custom_items"], 1)
        pushed = client.push_payloads[0]["changes"]["custom_items"][0]
        self.assertEqual(pushed["uuid"], "local-item")
        self.assertNotIn("id", pushed)
        self.assertEqual({row["uuid"] for row in rows}, {"local-item", "remote-item"})

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
                    SyncWorkerSettings(
                        batch_size=1000, pull_limit=1000, max_pull_pages=5
                    ),
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

    def test_sync_worker_pushes_manual_session_tag_override_on_next_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"
            identity_path = Path(temp_dir) / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                session_id = repository.save_session(
                    ActivitySession(
                        start_time=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
                        end_time=datetime(2026, 7, 11, 9, 20, tzinfo=UTC),
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

                client = FakeSyncClient(
                    push_responses=[{}, {}],
                    pull_responses=[
                        {
                            "changes": {},
                            "next_cursor": "pull-cursor-1",
                            "has_more": False,
                        },
                        {
                            "changes": {},
                            "next_cursor": "pull-cursor-1",
                            "has_more": False,
                        },
                    ],
                )

                worker = SyncWorker(repository, client)
                first_summary = worker.run_once()

                repository.update_session_tag(session_id, "Finance")

                second_summary = worker.run_once()
                state = repository.get_sync_state()

        self.assertEqual(first_summary["push"]["sessions"], 1)
        self.assertEqual(second_summary["push"]["sessions"], 1)
        self.assertEqual(len(client.push_payloads), 2)
        self.assertEqual(
            client.push_payloads[1]["changes"]["sessions"][0]["tag"],
            "Finance",
        )
        self.assertIsNotNone(state["last_push_cursor"])


if __name__ == "__main__":
    unittest.main()
