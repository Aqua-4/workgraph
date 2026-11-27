import pytest

from collector.wayland_idle import WaylandIdleState, WaylandIdleThreshold


def test_wayland_idle_starts_unknown_until_a_threshold_event() -> None:
    tracker = WaylandIdleThreshold(300)

    assert tracker.state(100) == WaylandIdleState("unknown", None)
    tracker.resumed()
    assert tracker.state(101) == WaylandIdleState("unknown", None)

    tracker.idled(200)
    assert tracker.state(201.9) == WaylandIdleState("idle", 301)
    tracker.idled(202)
    assert tracker.state(203) == WaylandIdleState("idle", 303)

    tracker.resumed()
    assert tracker.state(204) == WaylandIdleState("active", 0)
    tracker.idled(500)
    assert tracker.state(500) == WaylandIdleState("idle", 300)


def test_wayland_idle_disconnect_forgets_previous_state() -> None:
    tracker = WaylandIdleThreshold(60)
    tracker.idled(100)
    tracker.disconnected()

    assert tracker.state(200) == WaylandIdleState("unknown", None)


def test_wayland_idle_lower_bound_never_decreases_below_threshold() -> None:
    tracker = WaylandIdleThreshold(60)
    tracker.idled(100)

    assert tracker.state(99) == WaylandIdleState("idle", 60)


@pytest.mark.parametrize("threshold", [0, -1])
def test_wayland_idle_requires_positive_threshold(threshold: int) -> None:
    with pytest.raises(ValueError):
        WaylandIdleThreshold(threshold)


def test_known_idle_requires_input_since_connection() -> None:
    tracker = WaylandIdleThreshold(1)
    tracker.idled(10)
    assert tracker.known_idle_seconds(20) is None
    tracker.resumed()
    assert tracker.known_idle_seconds(20) == 0
    tracker.idled(21)
    assert tracker.known_idle_seconds(25) == 5
    tracker.disconnected()
    assert tracker.known_idle_seconds(30) is None
