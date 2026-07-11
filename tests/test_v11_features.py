import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from collector.git_tracker import GitTracker
from services.activity_tagger import ActivityTagger
from workgraph.models import ActivitySession


class GitTrackerTests(unittest.TestCase):
    def test_git_tracker_handles_no_repo(self) -> None:
        """Should gracefully handle non-git directories."""
        with TemporaryDirectory() as temp_dir:
            tracker = GitTracker(Path(temp_dir))
            activity = tracker.get_activity()

            self.assertIsNone(activity.repo_name)
            self.assertIsNone(activity.branch)
            self.assertIsNone(activity.commit_hash)
            self.assertEqual(activity.modified_files, [])


class ActivityTaggerTests(unittest.TestCase):
    def test_tagger_matches_repo(self) -> None:
        """Should tag sessions by git repository."""
        tagger = ActivityTagger()
        session = ActivitySession(
            start_time=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 9, 10, 5, tzinfo=UTC),
            duration_sec=300,
            app_name="Code",
            process_name="Code.exe",
            window_title="main.py",
            browser_domain=None,
            is_idle=False,
            idle_seconds=0,
            platform="windows",
            git_repo="aicoe-enterprise-mcp-api-backend",
            git_branch="feature/rbac",
            context_switches=0,
        )

        tag = tagger.tag_session(session)
        self.assertEqual(tag, "Client Delivery")

    def test_tagger_matches_domain(self) -> None:
        """Should tag sessions by browser domain."""
        tagger = ActivityTagger()
        session = ActivitySession(
            start_time=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 9, 10, 5, tzinfo=UTC),
            duration_sec=300,
            app_name="Chrome",
            process_name="chrome.exe",
            window_title="Learning Python - Udemy",
            browser_domain="udemy.com",
            is_idle=False,
            idle_seconds=0,
            platform="windows",
            git_repo=None,
            git_branch=None,
            context_switches=0,
        )

        tag = tagger.tag_session(session)
        self.assertEqual(tag, "Learning")

    def test_tagger_returns_none_for_unmapped_activity(self) -> None:
        """Should return None if no rule matches."""
        tagger = ActivityTagger()
        session = ActivitySession(
            start_time=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 9, 10, 5, tzinfo=UTC),
            duration_sec=300,
            app_name="Slack",
            process_name="slack.exe",
            window_title="Engineering channel",
            browser_domain=None,
            is_idle=False,
            idle_seconds=0,
            platform="windows",
            git_repo=None,
            git_branch=None,
            context_switches=0,
        )

        tag = tagger.tag_session(session)
        self.assertIsNone(tag)

    def test_tagger_uses_whole_word_keyword_matching(self) -> None:
        """Should not match short keywords inside longer words."""
        tagger = ActivityTagger()
        session = ActivitySession(
            start_time=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 9, 10, 5, tzinfo=UTC),
            duration_sec=300,
            app_name="Chrome",
            process_name="chrome.exe",
            window_title="Prompt engineering notes",
            browser_domain=None,
            is_idle=False,
            idle_seconds=0,
            platform="windows",
            git_repo=None,
            git_branch=None,
            context_switches=0,
        )

        tag = tagger.tag_session(session)
        self.assertEqual(tag, "AI Tools")


if __name__ == "__main__":
    unittest.main()
