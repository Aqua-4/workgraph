from __future__ import annotations

import ctypes
import platform
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class IdleState:
    is_idle: bool
    idle_seconds: int
    available: bool = True


class IdleTracker:
    def __init__(self, idle_threshold_seconds: int = 300) -> None:
        self.idle_threshold_seconds = idle_threshold_seconds

    def get_idle_state(self) -> IdleState:
        seconds = _idle_seconds()
        return IdleState(
            is_idle=seconds is not None and seconds >= self.idle_threshold_seconds,
            idle_seconds=seconds if seconds is not None else 0,
            available=seconds is not None,
        )


def _idle_seconds() -> int | None:
    system = platform.system().lower()
    if system == "windows":
        return _windows_idle_seconds()
    if system == "linux":
        return _linux_idle_seconds()
    return None


def _windows_idle_seconds() -> int | None:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("dwTime", ctypes.c_uint),
        ]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return max(0, int(elapsed_ms / 1000))


def _linux_idle_seconds() -> int | None:
    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1.5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return max(0, int(result.stdout.strip()) // 1000)
    except ValueError:
        return None
