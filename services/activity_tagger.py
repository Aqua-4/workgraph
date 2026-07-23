from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from workgraph.models import ActivitySession


class ActivityTagger:
    """Assigns activity tags based on rules."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.rules = self._load_rules(config_path)
        self.system_idle_apps: frozenset[str] = self._load_system_idle_apps()

    def is_system_idle(self, app_name: str | None, process_name: str | None = None) -> bool:
        """Return True if app/process is a known lock-screen or login UI."""
        for name in (app_name, process_name):
            if name and name.lower() in self.system_idle_apps:
                return True
        return False

    def tag_session(self, session: ActivitySession) -> str | None:
        """Determine tag for a session based on repo, domain, and keywords."""
        for tag_name, rule in self.rules.items():
            if self._matches_rule(session, rule):
                return tag_name
        return None

    def _matches_rule(self, session: ActivitySession, rule: dict[str, Any]) -> bool:
        """Check if a session matches a tagging rule."""
        # Check git repo
        if rule.get("repos"):
            if session.git_repo and any(
                session.git_repo.lower().startswith(repo.lower())
                for repo in rule["repos"]
            ):
                return True

        # Check browser domain
        if rule.get("domains"):
            if session.browser_domain and any(
                session.browser_domain.lower().endswith(domain.lower())
                for domain in rule["domains"]
            ):
                return True

        # Check window title / app name keywords
        if rule.get("keywords"):
            text_to_search = (
                (session.window_title or "").lower()
                + " "
                + (session.app_name or "").lower()
                + " "
                + (session.browser_domain or "").lower()
            )
            if any(
                _keyword_matches(text_to_search, keyword)
                for keyword in rule["keywords"]
            ):
                return True

        return False

    def _load_rules(self, config_path: Path | str | None) -> dict[str, dict[str, Any]]:
        """Load tagging rules from YAML config."""
        config_dir = Path(__file__).parent.parent / "config"
        default_path = config_dir / "tags.yaml"
        custom_path = config_dir / "my-tags.yaml"

        default_rules = self._load_yaml_tags(default_path)
        custom_rules = self._load_yaml_tags(custom_path) if custom_path.exists() else {}

        default_rules.update(custom_rules)
        return default_rules

    def _load_system_idle_apps(self) -> frozenset[str]:
        """Load system_idle_apps from tags.yaml (and my-tags.yaml override)."""
        config_dir = Path(__file__).parent.parent / "config"
        apps: set[str] = set()
        for path in [config_dir / "tags.yaml", config_dir / "my-tags.yaml"]:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if data:
                            apps.update(
                                a.lower()
                                for a in (data.get("system_idle_apps") or [])
                            )
                except Exception:
                    pass
        return frozenset(apps)

    def _load_yaml_tags(self, path: Path) -> dict[str, dict[str, Any]]:
        config_path = Path(path)
        if not config_path.exists():
            return {}

        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("tags", {}) if data else {}
        except Exception:
            return {}


def _keyword_matches(text: str, keyword: str) -> bool:
    if not keyword:
        return False

    keyword = keyword.lower().strip()
    if not keyword:
        return False

    pattern = rf"\b{re.escape(keyword)}\b"
    return bool(re.search(pattern, text))
