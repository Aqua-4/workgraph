from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from workgraph.models import ActivitySession


class ActivityTagger:
    """Assigns activity tags based on rules."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.rules = self._load_rules(config_path)

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
            if any(keyword.lower() in text_to_search for keyword in rule["keywords"]):
                return True

        return False

    def _load_rules(self, config_path: Path | str | None) -> dict[str, dict[str, Any]]:
        """Load tagging rules from YAML config."""
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "tags.yaml"

        config_path = Path(config_path)
        if not config_path.exists():
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("tags", {}) if data else {}
        except Exception:
            return {}
