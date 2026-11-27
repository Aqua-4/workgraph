import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from collector.browser_tracker import (
    _chromium_timestamp,
    domain_from_browser_history,
    extract_domain,
    history_paths,
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

    def test_windows_chrome_history_path_uses_profile_registry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Google" / "Chrome" / "User Data"
            history_path = root / "Default" / "History"
            history_path.parent.mkdir(parents=True)
            history_path.touch()

            with patch.dict("os.environ", {"LOCALAPPDATA": temp_dir}):
                paths = history_paths("chrome.exe", "windows")

        self.assertEqual(paths, [history_path])

    def test_linux_firefox_history_path_uses_profile_registry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / ".mozilla" / "firefox"
            history_path = root / "default-release" / "places.sqlite"
            history_path.parent.mkdir(parents=True)
            history_path.touch()

            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                paths = history_paths("firefox", "linux")

        self.assertEqual(paths, [history_path])

    def test_history_paths_are_empty_for_unknown_browser_or_os(self) -> None:
        self.assertEqual(history_paths("unknown-browser", "linux"), [])
        self.assertEqual(history_paths("chrome", "darwin"), [])

    def test_domain_from_brave_history_matches_window_title(self) -> None:
        with TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            _create_chromium_history(
                history_path,
                "https://docs.python.org/3/library/sqlite3.html",
                "sqlite3 - DB-API 2.0 interface",
            )

            with patch(
                "collector.browser_tracker.history_paths", return_value=[history_path]
            ):
                domain = domain_from_browser_history(
                    "brave.exe",
                    "sqlite3 - DB-API 2.0 interface - Brave",
                    "windows",
                    lookback_seconds=600,
                )

            self.assertEqual(domain, "docs.python.org")

    def test_domain_from_history_does_not_fallback_to_unrelated_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "History"
            _create_chromium_history(
                history_path,
                "https://github.com/example/repo",
                "Example Repo",
            )

            with patch(
                "collector.browser_tracker.history_paths", return_value=[history_path]
            ):
                domain = domain_from_browser_history(
                    "brave.exe",
                    (
                        "64,225.9 | BTCUSDT USDⓈ-Margined Perpetual Chart | "
                        "Binance Futures - Brave"
                    ),
                    "windows",
                    lookback_seconds=600,
                )

            self.assertIsNone(domain)


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
        (url, title, _chromium_timestamp(datetime.now(UTC))),
    )
    connection.commit()
    connection.close()


if __name__ == "__main__":
    unittest.main()
