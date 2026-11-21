from unittest.mock import patch

from collector.idle_tracker import IdleTracker


def test_idle_state_distinguishes_failure_from_activity() -> None:
    tracker = IdleTracker(idle_threshold_seconds=300)
    with patch("collector.idle_tracker._idle_seconds", return_value=None):
        state = tracker.get_idle_state()
    assert not state.available
    assert not state.is_idle
    assert state.idle_seconds == 0

    with patch("collector.idle_tracker._idle_seconds", return_value=0):
        state = tracker.get_idle_state()
    assert state.available
    assert not state.is_idle

    with patch("collector.idle_tracker._idle_seconds", return_value=300):
        state = tracker.get_idle_state()
    assert state.available
    assert state.is_idle
