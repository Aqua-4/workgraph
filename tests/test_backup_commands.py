from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

from db.repository import ActivityRepository
from main import run_backup_create_command, run_backup_restore_command, run_sync_snapshot_command
from workgraph.models import ActivitySession


class BackupCommandTests(unittest.TestCase):
    def test_backup_create_writes_database_and_config_archive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = temp / "activity.db"
            config_path = config_dir / "settings.yaml"
            identity_path = config_dir / "identity.json"
            output_path = temp / "backups" / "workgraph-backup.zip"

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                    ]
                ),
                encoding="utf-8",
            )
            identity_path.write_text(
                "\n".join(
                    [
                        "{",
                        '  "user_id": "user-1",',
                        '  "device_id": "device-1",',
                        '  "user_name": "Backup User",',
                        '  "device_name": "Backup Device",',
                        '  "device_type": "desktop"',
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.save_session(
                    ActivitySession(
                        start_time=datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc),
                        end_time=datetime(2026, 7, 12, 9, 10, tzinfo=timezone.utc),
                        duration_sec=600,
                        app_name="Code",
                        process_name="Code",
                        window_title="main.py",
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

            args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "output": str(output_path),
                    "include_config": True,
                },
            )()

            with patch("builtins.print") as mocked_print:
                original_cwd = Path.cwd()
                try:
                    os.chdir(temp)
                    run_backup_create_command(args)
                finally:
                    os.chdir(original_cwd)

            self.assertTrue(output_path.exists())
            with zipfile.ZipFile(output_path, mode="r") as archive:
                members = set(archive.namelist())
            self.assertIn("activity.db", members)
            self.assertIn("config/settings.yaml", members)
            self.assertIn("config/identity.json", members)
            output_text = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
            self.assertIn("Backup create: PASS", output_text)

    def test_backup_restore_recovers_database_and_config_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = temp / "activity.db"
            config_path = config_dir / "settings.yaml"
            identity_path = config_dir / "identity.json"
            output_path = temp / "backups" / "workgraph-backup.zip"

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                    ]
                ),
                encoding="utf-8",
            )
            identity_path.write_text(
                "\n".join(
                    [
                        "{",
                        '  "user_id": "user-1",',
                        '  "device_id": "device-1",',
                        '  "user_name": "Backup User",',
                        '  "device_name": "Backup Device",',
                        '  "device_type": "desktop"',
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.save_session(
                    ActivitySession(
                        start_time=datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc),
                        end_time=datetime(2026, 7, 12, 9, 10, tzinfo=timezone.utc),
                        duration_sec=600,
                        app_name="Code",
                        process_name="Code",
                        window_title="main.py",
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

            create_args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "output": str(output_path),
                    "include_config": True,
                },
            )()
            original_cwd = Path.cwd()
            try:
                os.chdir(temp)
                run_backup_create_command(create_args)
            finally:
                os.chdir(original_cwd)

            config_path.write_text("database_path: broken.db\nidentity_path: broken.json\n", encoding="utf-8")
            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM activity_sessions")
                conn.commit()

            restore_args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "backup_path": str(output_path),
                    "restore_config": True,
                },
            )()
            with patch("builtins.print") as mocked_print:
                original_cwd = Path.cwd()
                try:
                    os.chdir(temp)
                    run_backup_restore_command(restore_args)
                finally:
                    os.chdir(original_cwd)

            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM activity_sessions").fetchone()[0]
            self.assertEqual(row_count, 1)
            self.assertIn("database_path: ", config_path.read_text(encoding="utf-8"))
            output_text = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
            self.assertIn("Backup restore: PASS", output_text)

    def test_sync_snapshot_creates_archive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config_dir = temp / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = temp / "activity.db"
            config_path = config_dir / "settings.yaml"
            identity_path = config_dir / "identity.json"
            output_path = temp / "backups" / "sync-snapshot.zip"

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                    ]
                ),
                encoding="utf-8",
            )
            identity_path.write_text(
                "\n".join(
                    [
                        "{",
                        '  "user_id": "user-1",',
                        '  "device_id": "device-1",',
                        '  "user_name": "Snapshot User",',
                        '  "device_name": "Snapshot Device",',
                        '  "device_type": "desktop"',
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            with ActivityRepository(db_path, identity_path=identity_path):
                pass

            args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "output": str(output_path),
                    "include_config": True,
                },
            )()
            with patch("builtins.print") as mocked_print:
                original_cwd = Path.cwd()
                try:
                    os.chdir(temp)
                    run_sync_snapshot_command(args)
                finally:
                    os.chdir(original_cwd)

            self.assertTrue(output_path.exists())
            with zipfile.ZipFile(output_path, mode="r") as archive:
                self.assertIn("activity.db", set(archive.namelist()))
            output_text = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
            self.assertIn("Sync snapshot: PASS", output_text)


if __name__ == "__main__":
    unittest.main()
