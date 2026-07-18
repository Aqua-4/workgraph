import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.tag_review import (
    build_tag_review_groups,
    build_tag_review_suggestions,
    build_yaml_preview,
    derive_browser_context,
    find_rule_conflicts,
    load_custom_tag_rules,
    normalize_domain_candidate,
    normalize_repo_candidate,
    resolve_tag_review_group,
)


class TagReviewServiceTests(unittest.TestCase):
    def test_normalize_repo_candidate_uses_repo_basename(self) -> None:
        self.assertEqual(
            normalize_repo_candidate("/home/user/src/workgraph/.git"), "workgraph"
        )
        self.assertEqual(normalize_repo_candidate("C:\\repo\\backend"), "backend")
        self.assertIsNone(normalize_repo_candidate("  "))

    def test_normalize_domain_candidate_strips_scheme_path_and_www(self) -> None:
        self.assertEqual(
            normalize_domain_candidate("https://www.github.com/parashar/workgraph"),
            "github.com",
        )
        self.assertEqual(
            normalize_domain_candidate("docs.python.org:443"), "docs.python.org"
        )
        self.assertIsNone(normalize_domain_candidate(None))

    def test_load_custom_tag_rules_reads_expected_shape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "my-tags.yaml"
            config_path.write_text(
                """
tags:
  Development:
    repos:
      - workgraph
    domains:
      - github.com
    keywords:
      - code
""".strip(),
                encoding="utf-8",
            )

            rules = load_custom_tag_rules(config_path)

        self.assertEqual(rules["Development"]["repos"], ["workgraph"])
        self.assertEqual(rules["Development"]["domains"], ["github.com"])
        self.assertEqual(rules["Development"]["keywords"], ["code"])

    def test_build_tag_review_suggestions_groups_candidates_and_keeps_existing_keywords(
        self,
    ) -> None:
        existing_rules = {
            "Development": {
                "repos": ["workgraph"],
                "domains": ["github.com"],
                "keywords": ["code", "debug"],
            }
        }
        review_actions = [
            {
                "selected_tag": "Development",
                "browser_domain": "github.com",
                "git_repo": "/tmp/workgraph",
            },
            {
                "selected_tag": "Development",
                "browser_domain": "github.com",
                "git_repo": "/tmp/workgraph",
            },
            {
                "selected_tag": "Development",
                "browser_domain": "stackoverflow.com",
                "git_repo": "/tmp/workgraph",
            },
            {"selected_tag": "Development", "browser_domain": None, "git_repo": None},
        ]

        suggestions = build_tag_review_suggestions(
            review_actions,
            existing_rules,
            min_domain_hits=2,
            min_repo_hits=2,
        )

        self.assertEqual(suggestions["Development"]["keywords"], ["code", "debug"])
        self.assertEqual(suggestions["Development"]["total_reviewed"], 4)
        self.assertEqual(
            suggestions["Development"]["domains"][0]["value"], "github.com"
        )
        self.assertEqual(suggestions["Development"]["domains"][0]["sample_count"], 2)
        self.assertEqual(suggestions["Development"]["repos"][0]["value"], "workgraph")

    def test_build_tag_review_suggestions_adds_app_assignments_to_keywords(
        self,
    ) -> None:
        existing_rules = {
            "Development": {
                "repos": [],
                "domains": [],
                "keywords": ["code"],
            }
        }
        review_actions = [
            {
                "selected_tag": "Development",
                "source_signal": "app",
                "app_name": "Obsidian",
                "browser_domain": None,
                "git_repo": None,
            },
            {
                "selected_tag": "Development",
                "source_signal": "app",
                "app_name": "Obsidian",
                "browser_domain": None,
                "git_repo": None,
            },
        ]

        suggestions = build_tag_review_suggestions(
            review_actions,
            existing_rules,
            min_domain_hits=2,
            min_repo_hits=2,
        )

        self.assertEqual(suggestions["Development"]["keywords"], ["code", "Obsidian"])

    def test_derive_browser_context_extracts_known_browser_titles(self) -> None:
        self.assertEqual(
            derive_browser_context(
                "Brave - compare text and find differences online or offline - Diffchecker - Brave",
                "Brave",
            ),
            "Diffchecker",
        )
        self.assertEqual(
            derive_browser_context("Brave - YouTube Music", "Brave"),
            "YouTube Music",
        )
        self.assertEqual(
            derive_browser_context("Brave - New Tab - Brave", "Brave"),
            "New Tab",
        )
        self.assertIsNone(derive_browser_context("README.md", "Code"))

    def test_resolve_tag_review_group_prefers_domain_then_browser_context_then_app(
        self,
    ) -> None:
        self.assertEqual(
            resolve_tag_review_group(
                {
                    "app_name": "Chrome",
                    "process_name": "chrome",
                    "window_title": "Diffchecker - Brave",
                    "browser_domain": "diffchecker.com",
                    "git_repo": None,
                }
            ),
            {"group_type": "domain", "group_value": "diffchecker.com"},
        )
        self.assertEqual(
            resolve_tag_review_group(
                {
                    "app_name": "Chrome",
                    "process_name": "chrome",
                    "window_title": "Brave - New Tab - Brave",
                    "browser_domain": None,
                    "git_repo": None,
                }
            ),
            {"group_type": "browser_context", "group_value": "New Tab"},
        )
        self.assertEqual(
            resolve_tag_review_group(
                {
                    "app_name": "Slack",
                    "process_name": "slack",
                    "window_title": "Engineering",
                    "browser_domain": None,
                    "git_repo": None,
                }
            ),
            {"group_type": "app", "group_value": "Slack"},
        )

    def test_build_tag_review_groups_groups_browser_context_rows_separately(
        self,
    ) -> None:
        groups = build_tag_review_groups(
            [
                {
                    "app_name": "Brave",
                    "process_name": "brave",
                    "window_title": "Brave - YouTube Music",
                    "browser_domain": None,
                    "git_repo": None,
                },
                {
                    "app_name": "Brave",
                    "process_name": "brave",
                    "window_title": "Brave - New Tab - Brave",
                    "browser_domain": None,
                    "git_repo": None,
                },
            ]
        )

        self.assertIn(("browser_context", "YouTube Music"), groups)
        self.assertIn(("browser_context", "New Tab"), groups)
        self.assertEqual(len(groups[("browser_context", "YouTube Music")]), 1)

    def test_find_rule_conflicts_flags_values_owned_by_other_tags(self) -> None:
        existing_rules = {
            "Development": {
                "repos": ["workgraph"],
                "domains": ["github.com"],
                "keywords": [],
            },
            "Finance": {"repos": [], "domains": ["tradingview.com"], "keywords": []},
        }
        candidate_rules = {
            "Finance": {
                "repos": ["workgraph"],
                "domains": ["github.com", "tradingview.com"],
            }
        }

        conflicts = find_rule_conflicts(existing_rules, candidate_rules)

        self.assertEqual(conflicts["repos"]["workgraph"], "Development")
        self.assertEqual(conflicts["domains"]["github.com"], "Development")
        self.assertNotIn("tradingview.com", conflicts["domains"])

    def test_build_yaml_preview_dedupes_case_insensitively_and_adds_new_tags(
        self,
    ) -> None:
        existing_rules = {
            "Development": {
                "repos": ["workgraph"],
                "domains": ["github.com"],
                "keywords": ["code"],
            }
        }
        selected_suggestions = {
            "Development": {
                "repos": [{"value": "WorkGraph", "sample_count": 3}],
                "domains": [{"value": "GitHub.com", "sample_count": 2}],
                "keywords": ["code"],
            },
            "Finance": {
                "repos": [],
                "domains": [{"value": "tradingview.com", "sample_count": 2}],
                "keywords": [],
            },
        }

        preview_text, warnings = build_yaml_preview(
            existing_rules, selected_suggestions
        )

        self.assertIn("Development:", preview_text)
        self.assertIn("Finance:", preview_text)
        self.assertEqual(preview_text.lower().count("github.com"), 1)
        self.assertEqual(preview_text.lower().count("workgraph"), 1)
        self.assertIn("tradingview.com", preview_text)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
