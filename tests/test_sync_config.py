from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.sync_config import load_sync_entities


class SyncConfigTests(unittest.TestCase):
    def test_default_manifest_defines_current_sync_entities(self) -> None:
        entities = load_sync_entities()

        self.assertEqual(
            set(entities),
            {"sessions", "journal_entries", "daily_reflections", "work_events"},
        )
        self.assertEqual(entities["sessions"].table, "activity_sessions")
        self.assertEqual(
            entities["daily_reflections"].conflict_columns,
            ("date",),
        )

    def test_manifest_validation_checks_local_table_columns(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE sample_items (
                uuid TEXT UNIQUE,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT,
                user_id TEXT,
                device_id TEXT
            )
            """
        )
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "sync.yaml"
            config_path.write_text(
                "version: 1\n"
                "entities:\n"
                "  sample_items:\n"
                "    table: sample_items\n"
                "    uuid: uuid\n"
                "    created_at: created_at\n"
                "    updated_at: updated_at\n"
                "    deleted_at: deleted_at\n"
                "    user_id: user_id\n"
                "    device_id: device_id\n",
                encoding="utf-8",
            )
            entities = load_sync_entities(config_path, connection=connection)

        self.assertEqual(list(entities), ["sample_items"])
