import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from main import _count_sync_metadata_gaps, load_settings, run_sync_migrate_command


class MainSettingsTests(unittest.TestCase):
    def test_load_settings_reads_identity_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "settings.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "database_path: temp.db",
                        "poll_interval_seconds: 3",
                        "idle_threshold_seconds: 120",
                        "session_gap_seconds: 45",
                        "browser_history_lookback_seconds: 300",
                        "log_path: logs/test.log",
                        "identity_path: config/custom-identity.json",
                        "sync_interval_seconds: 30",
                        "sync_backoff_base_seconds: 2",
                        "sync_backoff_max_seconds: 45",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(str(config_path))

        self.assertEqual(settings.database_path, "temp.db")
        self.assertEqual(settings.identity_path, "config/custom-identity.json")

    def test_sync_migrate_command_backfills_legacy_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            config_path = temp / "settings.yaml"

            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE activity_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_sec INTEGER NOT NULL,
                    app_name TEXT NOT NULL,
                    process_name TEXT,
                    window_title TEXT,
                    browser_domain TEXT,
                    is_idle INTEGER NOT NULL,
                    idle_seconds INTEGER NOT NULL DEFAULT 0,
                    git_repo TEXT,
                    git_branch TEXT,
                    context_switches INTEGER NOT NULL DEFAULT 0,
                    tag TEXT,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO activity_sessions (
                    start_time,
                    end_time,
                    duration_sec,
                    app_name,
                    process_name,
                    window_title,
                    browser_domain,
                    is_idle,
                    idle_seconds,
                    git_repo,
                    git_branch,
                    context_switches,
                    tag,
                    platform,
                    created_at
                ) VALUES (
                    '2026-07-09T10:00:00+00:00',
                    '2026-07-09T10:05:00+00:00',
                    300,
                    'Code',
                    'Code',
                    'main.py',
                    NULL,
                    0,
                    0,
                    'workgraph',
                    'main',
                    0,
                    'Client Delivery',
                    'linux',
                    '2026-07-09T10:05:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {temp / 'identity.json'}",
                    ]
                ),
                encoding="utf-8",
            )

            before = _count_sync_metadata_gaps(db_path)
            self.assertEqual(before["activity_sessions"], 1)

            with patch("builtins.print") as mocked_print:
                run_sync_migrate_command(type("Args", (), {"config": str(config_path)})())

            after = _count_sync_metadata_gaps(db_path)

        self.assertEqual(after["activity_sessions"], 0)
        printed_lines = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Sync migration complete", printed_lines)

    def test_load_settings_prefers_my_settings_when_default_path_used(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            (config_dir / "settings.yaml").write_text(
                "\n".join(
                    [
                        "database_path: shared.db",
                        "identity_path: config/shared-identity.json",
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "my-settings.yaml").write_text(
                "\n".join(
                    [
                        "database_path: personal.db",
                        "identity_path: config/personal-identity.json",
                    ]
                ),
                encoding="utf-8",
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                settings = load_settings("config/settings.yaml")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(settings.database_path, "personal.db")
        self.assertEqual(settings.identity_path, "config/personal-identity.json")

    def test_load_settings_uses_explicit_path_even_when_my_settings_exists(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            explicit_path = temp / "team-settings.yaml"
            explicit_path.write_text(
                "\n".join(
                    [
                        "database_path: explicit.db",
                        "identity_path: config/explicit-identity.json",
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "my-settings.yaml").write_text(
                "\n".join(
                    [
                        "database_path: personal.db",
                        "identity_path: config/personal-identity.json",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(str(explicit_path))

        self.assertEqual(settings.database_path, "explicit.db")
        self.assertEqual(settings.identity_path, "config/explicit-identity.json")

    def test_load_settings_prefers_my_identity_when_default_identity_used(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            (config_dir / "settings.yaml").write_text(
                "\n".join(
                    [
                        "database_path: shared.db",
                        "identity_path: config/identity.json",
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "my-identity.json").write_text(
                '{"device_id":"my-device-id"}',
                encoding="utf-8",
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                settings = load_settings("config/settings.yaml")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(settings.identity_path, "config/my-identity.json")

    def test_load_settings_keeps_explicit_identity_path_even_when_my_identity_exists(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            (config_dir / "settings.yaml").write_text(
                "\n".join(
                    [
                        "database_path: shared.db",
                        "identity_path: config/team-identity.json",
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "my-identity.json").write_text(
                '{"device_id":"my-device-id"}',
                encoding="utf-8",
            )

            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                settings = load_settings("config/settings.yaml")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(settings.identity_path, "config/team-identity.json")


if __name__ == "__main__":
    unittest.main()
