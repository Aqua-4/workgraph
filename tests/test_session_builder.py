import unittest
from datetime import datetime, timedelta, timezone

from processor.session_builder import SessionBuilder
from workgraph.models import ActivitySample


def sample(offset: int, app: str = "Code", idle: bool = False) -> ActivitySample:
    return ActivitySample(
        observed_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
        + timedelta(seconds=offset),
        app_name=app,
        process_name=f"{app}.exe",
        window_title="main.py",
        browser_domain=None,
        is_idle=idle,
        idle_seconds=0,
        platform="windows",
        git_repo=None,
        git_branch=None,
    )


class SessionBuilderTests(unittest.TestCase):
    def test_builder_extends_matching_session(self) -> None:
        builder = SessionBuilder(max_gap_seconds=90)

        self.assertIsNone(builder.ingest(sample(0)))
        self.assertIsNone(builder.ingest(sample(30)))

        current = builder.current
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.duration_sec, 30)

    def test_builder_completes_when_app_changes(self) -> None:
        builder = SessionBuilder(max_gap_seconds=90)

        builder.ingest(sample(0))
        completed = builder.ingest(sample(30, app="Chrome"))

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.app_name, "Code")
        self.assertEqual(completed.duration_sec, 0)

    def test_builder_completes_after_large_gap(self) -> None:
        builder = SessionBuilder(max_gap_seconds=90)

        builder.ingest(sample(0))
        completed = builder.ingest(sample(120))

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.app_name, "Code")


if __name__ == "__main__":
    unittest.main()
