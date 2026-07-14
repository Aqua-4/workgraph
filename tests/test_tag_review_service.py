import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.tag_review import (
    build_tag_review_suggestions,
    build_yaml_preview,
    find_rule_conflicts,
    load_custom_tag_rules,
    normalize_domain_candidate,
    normalize_repo_candidate,
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
