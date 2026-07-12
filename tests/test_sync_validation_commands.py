from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from db.repository import ActivityRepository
from main import run_sync_validate_command, run_sync_verify_command
from workgraph.models import ActivitySession
from datetime import datetime, timezone


class SyncValidationCommandTests(unittest.TestCase):
    def test_sync_validate_passes_for_clean_database(self) -> None:
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
                run_sync_validate_command(type("Args", (), {"config": str(config_path)})())

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Sync validation: PASS", output)

    def test_sync_validate_detects_uuid_and_broken_reference_issues(self) -> None:
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
            conn.execute("UPDATE journal_entries SET uuid = NULL")
            conn.execute(
                """
                INSERT INTO git_activity (
                    session_id,
                    repo,
                    branch,
                    commit_hash,
                    file_name,
                    event_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (99999, "workgraph", "main", None, "main.py", "modify"),
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
                run_sync_validate_command(type("Args", (), {"config": str(config_path)})())

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Sync validation: FAIL", output)
        self.assertIn("journal_entries empty UUID rows", output)
        self.assertIn("git_activity rows with missing session reference", output)

    def test_sync_verify_passes_when_totals_match_server(self) -> None:
        class MatchingClient:
            def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 10.0) -> None:
                self.base_url = base_url
                self.token = token
                self.timeout_seconds = timeout_seconds

            def pull(self, payload: dict):
                return {
                    "changes": {
                        "sessions": [
                            {
                                "uuid": "sess-1",
                                "duration_sec": 300,
                            }
                        ],
                        "journal_entries": [{"uuid": "journal-1"}],
                        "daily_reflections": [{"uuid": "reflection-1"}],
                        "tombstones": [],
                    },
                    "next_cursor": "cursor-1",
                    "has_more": False,
                }

        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            config_path = temp / "settings.yaml"
            identity_path = temp / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.save_session(
                    ActivitySession(
                        start_time=datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc),
                        end_time=datetime(2026, 7, 12, 9, 5, tzinfo=timezone.utc),
                        duration_sec=300,
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
                repository.save_journal_entry(
                    created_at=datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc),
                    start_time=None,
                    end_time=None,
                    title="Note",
                    notes="text",
                    metadata=None,
                )
                repository.upsert_daily_reflection(
                    date="2026-07-12",
                    wins="done",
                    problems="",
                    tomorrow="next",
                    energy=6,
                    stress=3,
                )

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                        "sync_base_url: http://127.0.0.1:8000",
                        "sync_token: test-token",
                    ]
                ),
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "base_url": None,
                    "token": None,
                    "pull_limit": None,
                    "max_pages": 10,
                    "timeout_seconds": None,
                },
            )()

            with patch("main.HttpSyncClient", MatchingClient), patch("builtins.print") as mocked_print:
                run_sync_verify_command(args)

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Sync verify: PASS", output)

    def test_sync_verify_fails_when_totals_do_not_match(self) -> None:
        class MismatchClient:
            def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 10.0) -> None:
                self.base_url = base_url
                self.token = token
                self.timeout_seconds = timeout_seconds

            def pull(self, payload: dict):
                return {
                    "changes": {
                        "sessions": [],
                        "journal_entries": [],
                        "daily_reflections": [],
                        "tombstones": [],
                    },
                    "next_cursor": "cursor-1",
                    "has_more": False,
                }

        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "activity.db"
            config_path = temp / "settings.yaml"
            identity_path = temp / "identity.json"

            with ActivityRepository(db_path, identity_path=identity_path) as repository:
                repository.save_session(
                    ActivitySession(
                        start_time=datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc),
                        end_time=datetime(2026, 7, 12, 9, 5, tzinfo=timezone.utc),
                        duration_sec=300,
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

            config_path.write_text(
                "\n".join(
                    [
                        f"database_path: {db_path}",
                        f"identity_path: {identity_path}",
                        "sync_base_url: http://127.0.0.1:8000",
                        "sync_token: test-token",
                    ]
                ),
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "config": str(config_path),
                    "base_url": None,
                    "token": None,
                    "pull_limit": None,
                    "max_pages": 10,
                    "timeout_seconds": None,
                },
            )()

            with patch("main.HttpSyncClient", MismatchClient), patch("builtins.print") as mocked_print:
                run_sync_verify_command(args)

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn("Sync verify: FAIL", output)
        self.assertIn("sessions", output)


if __name__ == "__main__":
    unittest.main()
