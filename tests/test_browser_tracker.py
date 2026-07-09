import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from collector.browser_tracker import (
    _chromium_timestamp,
    domain_from_browser_history,
    extract_domain,
)


class BrowserTrackerTests(unittest.TestCase):
    def test_extract_domain_from_url(self) -> None:
        self.assertEqual(
            extract_domain("https://www.example.com/path?q=1 - Browser"),
            "example.com",
        )

    def test_extract_domain_from_title_host(self) -> None:
        self.assertEqual(
            extract_domain("Docs - developer.mozilla.org - Mozilla Firefox"),
            "developer.mozilla.org",
        )

    def test_extract_domain_returns_none_without_host(self) -> None:
        self.assertIsNone(extract_domain("Project notes - Visual Studio Code"))

    def test_domain_from_brave_history_matches_window_title(self) -> None:
        with TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            _create_chromium_history(
                history_path,
                "https://docs.python.org/3/library/sqlite3.html",
                "sqlite3 - DB-API 2.0 interface",
            )

            with patch("collector.browser_tracker.history_paths", return_value=[history_path]):
                domain = domain_from_browser_history(
                    "brave.exe",
                    "sqlite3 - DB-API 2.0 interface - Brave",
                    "windows",
                    lookback_seconds=600,
                )

            self.assertEqual(domain, "docs.python.org")


def _create_chromium_history(path: Path, url: str, title: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE urls (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            last_visit_time INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO urls (url, title, last_visit_time) VALUES (?, ?, ?)",
        (url, title, _chromium_timestamp(datetime.now(timezone.utc))),
    )
    connection.commit()
    connection.close()


if __name__ == "__main__":
    unittest.main()
