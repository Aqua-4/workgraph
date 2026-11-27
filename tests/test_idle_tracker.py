from unittest.mock import Mock

from collector.idle_tracker import IdleTracker


def test_idle_state_distinguishes_failure_from_activity() -> None:
    provider = Mock()
    provider.get_idle_seconds.side_effect = [None, 0, 300]
    tracker = IdleTracker(idle_threshold_seconds=300, provider=provider)
    state = tracker.get_idle_state()
    assert not state.available
    assert not state.is_idle
    assert state.idle_seconds == 0

    state = tracker.get_idle_state()
    assert state.available
    assert not state.is_idle

    state = tracker.get_idle_state()
    assert state.available
    assert state.is_idle
