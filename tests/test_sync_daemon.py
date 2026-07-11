import unittest

from services.sync_daemon import SyncDaemon, SyncDaemonSettings


class FakeWorker:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def run_once(self) -> dict:
        self.calls += 1
        if not self._outcomes:
            return {"push": {}, "pull": {}}
        outcome = self._outcomes.pop(0)
        if not outcome:
            raise RuntimeError("sync failure")
        return {"push": {"batches": 1}, "pull": {"pages": 1}}


class SyncDaemonTests(unittest.TestCase):
    def test_sync_daemon_run_cycles_backoff_and_reset(self) -> None:
        sleep_calls: list[float] = []
        worker = FakeWorker([False, False, True, False])
        daemon = SyncDaemon(
            worker,
            SyncDaemonSettings(interval_seconds=10, backoff_base_seconds=1, backoff_max_seconds=4),
            sleep_fn=sleep_calls.append,
        )

        summary = daemon.run_cycles(4)

        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["errors"], 3)
        self.assertEqual(worker.calls, 4)
        self.assertEqual(sleep_calls, [1, 2, 10, 1])


if __name__ == "__main__":
    unittest.main()
