from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from workgraph.models import ActiveWindow


@dataclass(frozen=True)
class BrowserProfile:
    process_names: frozenset[str]
    history_filename: str | None
    profile_roots: Mapping[str, Callable[[], Path]]


def _windows_local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def _windows_roaming_app_data() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def _linux_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


BROWSER_PROFILES = {
    "arc": BrowserProfile(frozenset({"arc"}), None, {}),
    "brave": BrowserProfile(
        frozenset({"brave", "brave-browser", "brave.exe"}),
        "History",
        {
            "windows": lambda: (
                _windows_local_app_data()
                / "BraveSoftware"
                / "Brave-Browser"
                / "User Data"
            ),
            "linux": lambda: _linux_config_home() / "BraveSoftware" / "Brave-Browser",
        },
    ),
    "chrome": BrowserProfile(
        frozenset({"chrome", "chrome.exe", "google-chrome"}),
        "History",
        {
            "windows": lambda: (
                _windows_local_app_data() / "Google" / "Chrome" / "User Data"
            ),
            "linux": lambda: _linux_config_home() / "google-chrome",
        },
    ),
    "chromium": BrowserProfile(
        frozenset({"chromium", "chromium.exe", "chromium-browser"}),
        "History",
        {
            "windows": lambda: _windows_local_app_data() / "Chromium" / "User Data",
            "linux": lambda: _linux_config_home() / "chromium",
        },
    ),
    "edge": BrowserProfile(
        frozenset({"msedge", "msedge.exe"}),
        "History",
        {
            "windows": lambda: (
                _windows_local_app_data() / "Microsoft" / "Edge" / "User Data"
            ),
            "linux": lambda: _linux_config_home() / "microsoft-edge",
        },
    ),
    "firefox": BrowserProfile(
        frozenset({"firefox", "firefox.exe"}),
        "places.sqlite",
        {
            "windows": lambda: (
                _windows_roaming_app_data() / "Mozilla" / "Firefox" / "Profiles"
            ),
            "linux": lambda: Path.home() / ".mozilla" / "firefox",
        },
    ),
    "iexplorer": BrowserProfile(frozenset({"iexplore.exe"}), None, {}),
    "opera": BrowserProfile(frozenset({"opera", "opera.exe"}), None, {}),
    "safari": BrowserProfile(frozenset({"safari"}), None, {}),
    "vivaldi": BrowserProfile(frozenset({"vivaldi", "vivaldi.exe"}), None, {}),
}
_PROFILE_BY_PROCESS = {
    process_name: profile
    for profile in BROWSER_PROFILES.values()
    for process_name in profile.process_names
}
BROWSER_PROCESSES = set(_PROFILE_BY_PROCESS)


class BrowserTracker:
    """Best-effort browser domain detection without reading page content."""

    def __init__(self, history_lookback_seconds: int = 600) -> None:
        self.history_lookback_seconds = history_lookback_seconds

    def get_domain(self, active_window: ActiveWindow) -> str | None:
        if not _is_browser(active_window.process_name):
            return None

        title_domain = extract_domain(active_window.window_title)
        if title_domain:
            return title_domain

        return domain_from_browser_history(
            active_window.process_name,
            active_window.window_title,
            active_window.platform,
            self.history_lookback_seconds,
        )


def _is_browser(process_name: str | None) -> bool:
    return bool(process_name and process_name.lower() in BROWSER_PROCESSES)


def extract_domain(text: str | None) -> str | None:
    if not text:
        return None

    url_match = re.search(r"https?://[^\s\])}>\"']+", text)
    if url_match:
        return _clean_host(urlparse(url_match.group(0)).hostname)

    host_match = re.search(
        r"\b((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})(?:/[^\s]*)?\b",
        text,
    )
    if host_match:
        return _clean_host(host_match.group(1))
    return None


def domain_from_browser_history(
    process_name: str | None,
    window_title: str | None,
    active_platform: str,
    lookback_seconds: int = 600,
) -> str | None:
    for history_path in history_paths(process_name, active_platform):
        domain = _domain_from_history_file(history_path, window_title, lookback_seconds)
        if domain:
            return domain
    return None


def history_paths(process_name: str | None, active_platform: str) -> list[Path]:
    if not process_name:
        return []

    profile = _PROFILE_BY_PROCESS.get(process_name.lower())
    if profile is None or profile.history_filename is None:
        return []
    root_resolver = profile.profile_roots.get(active_platform.lower())
    if root_resolver is None:
        return []
    return _profile_history_paths(root_resolver(), profile.history_filename)


def _profile_history_paths(base_path: Path, history_filename: str) -> list[Path]:
    if not base_path.exists():
        return []

    direct_history = base_path / history_filename
    if direct_history.exists():
        return [direct_history]

    candidates = []
    for profile in base_path.iterdir():
        if not profile.is_dir():
            continue
        history = profile / history_filename
        if history.exists():
            candidates.append(history)

    def sort_key(path: Path) -> tuple[int, str]:
        profile_name = path.parent.name
        if profile_name == "Default":
            return (0, profile_name)
        if profile_name.startswith("Profile "):
            return (1, profile_name)
        return (2, profile_name)

    return sorted(candidates, key=sort_key)


def _domain_from_history_file(
    history_path: Path,
    window_title: str | None,
    lookback_seconds: int,
) -> str | None:
    # NamedTemporaryFile holds an open exclusive handle on Windows, so
    # shutil.copy2 into the same path fails with PermissionError.  Use
    # mkstemp instead and clean up explicitly.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="workgraph-history-", suffix=".sqlite")
    os.close(tmp_fd)
    try:
        try:
            shutil.copy2(history_path, tmp_path)
        except OSError:
            return None

        if history_path.name == "places.sqlite":
            rows = _read_firefox_history(Path(tmp_path), lookback_seconds)
        else:
            rows = _read_chromium_history(Path(tmp_path), lookback_seconds)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not rows:
        return None

    matched_domain = _domain_matching_title(rows, window_title)
    if matched_domain:
        return matched_domain

    if not window_title:
        return _clean_host(urlparse(rows[0][0]).hostname)

    return None


def _read_chromium_history(
    history_path: Path, lookback_seconds: int
) -> list[tuple[str, str | None]]:
    threshold = _chromium_timestamp(
        datetime.now(UTC) - timedelta(seconds=lookback_seconds)
    )
    try:
        with sqlite3.connect(f"file:{history_path}?mode=ro", uri=True) as connection:
            cursor = connection.execute(
                """
                SELECT url, title
                FROM urls
                WHERE last_visit_time >= ?
                ORDER BY last_visit_time DESC
                LIMIT 50
                """,
                (threshold,),
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.Error:
        return []


def _read_firefox_history(
    history_path: Path, lookback_seconds: int
) -> list[tuple[str, str | None]]:
    threshold = int(
        (datetime.now(UTC) - timedelta(seconds=lookback_seconds)).timestamp()
        * 1_000_000
    )
    try:
        with sqlite3.connect(f"file:{history_path}?mode=ro", uri=True) as connection:
            cursor = connection.execute(
                """
                SELECT moz_places.url, moz_places.title
                FROM moz_places
                JOIN moz_historyvisits
                    ON moz_historyvisits.place_id = moz_places.id
                WHERE moz_historyvisits.visit_date >= ?
                ORDER BY moz_historyvisits.visit_date DESC
                LIMIT 50
                """,
                (threshold,),
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.Error:
        return []


def _domain_matching_title(
    rows: list[tuple[str, str | None]], window_title: str | None
) -> str | None:
    if not window_title:
        return None
    normalized_window_title = _normalize_title(window_title)
    if not normalized_window_title:
        return None

    for url, title in rows:
        normalized_title = _normalize_title(title)
        if normalized_title and (
            normalized_title in normalized_window_title
            or normalized_window_title in normalized_title
        ):
            return _clean_host(urlparse(url).hostname)
    return None


def _normalize_title(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(
        r"\s+-\s+(Brave|Google Chrome|Microsoft Edge|Mozilla Firefox)$", "", value
    )
    return " ".join(value.casefold().split())


def _chromium_timestamp(value: datetime) -> int:
    epoch = datetime(1601, 1, 1, tzinfo=UTC)
    return int((value - epoch).total_seconds() * 1_000_000)


def _clean_host(host: str | None) -> str | None:
    if not host:
        return None
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None
