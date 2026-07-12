from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from api.app import _ensure_sync_tables
from db.repository import ActivityRepository
from main import run_doctor_command


class DoctorCommandTests(unittest.TestCase):
    def test_doctor_passes_for_clean_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            config_path = temp / "settings.yaml"
            identity_path = temp / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path):
                pass

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("builtins.print") as mocked_print:
                run_doctor_command(type("Args", (), {"config": str(config_path)})())

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Doctor: PASS", output)

    def test_doctor_reports_invalid_timestamps_and_rollup_mismatches(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            config_path = temp / "settings.yaml"
            identity_path = temp / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.save_journal_entry(
                    created_at=datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc),
                    start_time=None,
                    end_time=None,
                    title="Note",
                    notes="text",
                    metadata=None,
                )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            _ensure_sync_tables(conn)
            conn.execute("INSERT OR REPLACE INTO journal_entries (id, created_at, title, notes) VALUES (1, 'not-a-timestamp', 'Broken entry', 'text')")
            conn.execute(
                """
                INSERT INTO sync_sessions (
                    uuid,
                    user_id,
                    device_id,
                    utc_start,
                    utc_end,
                    timezone_name,
                    active_seconds,
                    application_name,
                    tag,
                    repo_name,
                    created_at,
                    updated_at,
                    deleted_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sync-rollup-1",
                    "user-1",
                    "device-1",
                    "2026-07-12T09:00:00+00:00",
                    "2026-07-12T09:10:00+00:00",
                    "UTC",
                    600,
                    "Code",
                    "Client Delivery",
                    "workgraph",
                    "2026-07-12T09:00:00+00:00",
                    "2026-07-12T09:00:00+00:00",
                    None,
                    json.dumps(
                        {
                            "uuid": "sync-rollup-1",
                            "created_at": "2026-07-12T09:00:00+00:00",
                            "updated_at": "2026-07-12T09:00:00+00:00",
                            "duration_sec": 600,
                            "app_name": "Code",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO sync_metrics_daily (
                    user_id,
                    device_id,
                    day_utc,
                    active_seconds,
                    focus_seconds,
                    meeting_seconds,
                    context_switches,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "user-1",
                    "device-1",
                    "2026-07-12",
                    1,
                    0,
                    0,
                    0,
                    "2026-07-12T09:00:00+00:00",
                ),
            )
            conn.commit()
            conn.close()

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("builtins.print") as mocked_print:
                run_doctor_command(type("Args", (), {"config": str(config_path)})())

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Doctor: FAIL", output)
        self.assertIn("journal_entries.created_at invalid timestamp values", output)
        self.assertIn("sync rollup mismatch", output)


if __name__ == "__main__":
    unittest.main()
