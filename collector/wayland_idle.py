from __future__ import annotations

import logging
import os
import select
import socket
import threading
import time
from dataclasses import dataclass
from typing import Literal

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WaylandIdleState:
    status: Literal["unknown", "active", "idle"]
    idle_lower_bound_seconds: int | None


class WaylandIdleThreshold:
    def __init__(self, threshold_seconds: int) -> None:
        if threshold_seconds <= 0:
            raise ValueError("Idle threshold must be positive")
        self.threshold_seconds = threshold_seconds
        self._status: Literal["unknown", "active", "idle"] = "unknown"
        self._idled_at: float | None = None
        self._observed_input = False

    def idled(self, tick: float) -> None:
        if self._status != "idle":
            self._status = "idle"
            self._idled_at = tick

    def resumed(self) -> None:
        if self._status == "idle":
            self._status = "active"
            self._idled_at = None
            self._observed_input = True

    def disconnected(self) -> None:
        self._status = "unknown"
        self._idled_at = None
        self._observed_input = False

    def state(self, tick: float) -> WaylandIdleState:
        if self._status == "idle" and self._idled_at is not None:
            elapsed = max(0, int(tick - self._idled_at))
            return WaylandIdleState("idle", self.threshold_seconds + elapsed)
        if self._status == "active":
            return WaylandIdleState("active", 0)
        return WaylandIdleState("unknown", None)

    def known_idle_seconds(self, tick: float) -> int | None:
        if not self._observed_input:
            return None
        return self.state(tick).idle_lower_bound_seconds


class WaylandIdleMonitor:
    def __init__(self) -> None:
        self._state = WaylandIdleThreshold(1)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake_read: socket.socket | None = None
        self._wake_write: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Wayland idle monitor already started")
        self._wake_read, self._wake_write = socket.socketpair()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._wake_write is not None:
            try:
                self._wake_write.send(b"x")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                LOGGER.warning("Wayland idle monitor did not stop promptly")
                return
        if self._wake_read is not None:
            self._wake_read.close()
        if self._wake_write is not None:
            self._wake_write.close()
        with self._lock:
            self._state.disconnected()

    def get_idle_seconds(self) -> int | None:
        with self._lock:
            return self._state.known_idle_seconds(time.monotonic())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_session()
            except Exception as error:
                LOGGER.warning(
                    "Wayland idle monitor unavailable: %s", type(error).__name__
                )
            finally:
                with self._lock:
                    self._state.disconnected()
            if not self._stop.is_set():
                self._stop.wait(3)

    def _run_session(self) -> None:
        from pywayland.client import Display
        from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1
        from pywayland.protocol.wayland import WlSeat

        display = Display()
        display.connect()
        try:
            registry = display.get_registry()
            notifiers = []
            seats = []
            lost = threading.Event()

            def global_added(registry, name, interface, version):
                if interface == ExtIdleNotifierV1.name and version >= 2:
                    notifiers.append((name, registry.bind(name, ExtIdleNotifierV1, 2)))
                elif interface == WlSeat.name:
                    seat = registry.bind(name, WlSeat, min(version, 2))
                    details = {"id": name, "seat": seat, "name": None}
                    if version >= 2:
                        seat.dispatcher["name"] = lambda _seat, seat_name: (
                            details.update(name=seat_name)
                        )
                    seats.append(details)

            def global_removed(_registry, name):
                if name in watched_globals:
                    lost.set()

            watched_globals: set[int] = set()
            registry.dispatcher["global"] = global_added
            registry.dispatcher["global_remove"] = global_removed
            display.roundtrip()
            display.roundtrip()
            target_seat = os.environ.get("XDG_SEAT", "seat0")
            matching = [seat for seat in seats if seat["name"] == target_seat]
            if not matching and len(seats) == 1:
                matching = seats
            if not notifiers or len(matching) != 1:
                raise RuntimeError("Wayland input-idle protocol or seat unavailable")
            watched_globals.update((notifiers[0][0], matching[0]["id"]))
            notification = notifiers[0][1].get_input_idle_notification(
                1000, matching[0]["seat"]
            )

            def idled(_notification):
                with self._lock:
                    self._state.idled(time.monotonic())

            def resumed(_notification):
                with self._lock:
                    self._state.resumed()

            notification.dispatcher["idled"] = idled
            notification.dispatcher["resumed"] = resumed
            display.roundtrip()
            assert self._wake_read is not None
            while not self._stop.is_set() and not lost.is_set():
                readable, _, _ = select.select(
                    [display.get_fd(), self._wake_read], [], []
                )
                if self._wake_read in readable:
                    break
                display.read()
                display.dispatch()
            notification.destroy()
        finally:
            display.disconnect()
