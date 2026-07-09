from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from db.repository import ActivityRepository
from workgraph.models import ActivitySession


class ActivityRepositoryTests(unittest.TestCase):
    def test_repository_saves_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "activity.db"
            session = ActivitySession(
                start_time=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 7, 9, 10, 5, tzinfo=timezone.utc),
                duration_sec=300,
                app_name="Code",
                process_name="Code.exe",
                window_title="README.md",
                browser_domain=None,
                is_idle=False,
                platform="windows",
            )

            with ActivityRepository(db_path) as repository:
                row_id = repository.save_session(session)
                rows = repository.recent_sessions()

        self.assertEqual(row_id, 1)
        self.assertEqual(rows[0]["app_name"], "Code")
        self.assertEqual(rows[0]["duration_sec"], 300)


if __name__ == "__main__":
    unittest.main()
