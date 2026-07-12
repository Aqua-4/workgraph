from __future__ import annotations

import ctypes
import platform
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class IdleState:
    is_idle: bool
    idle_seconds: int


class IdleTracker:
    def __init__(self, idle_threshold_seconds: int = 300) -> None:
        self.idle_threshold_seconds = idle_threshold_seconds

    def get_idle_state(self) -> IdleState:
        seconds = _idle_seconds()
        return IdleState(
            is_idle=seconds >= self.idle_threshold_seconds,
            idle_seconds=seconds,
        )


def _idle_seconds() -> int:
    system = platform.system().lower()
    if system == "windows":
        return _windows_idle_seconds()
    if system == "linux":
        return _linux_idle_seconds()
    return 0


def _windows_idle_seconds() -> int:
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
        return 0
    elapsed_ms = kernel32.GetTickCount() - info.dwTime
    return max(0, int(elapsed_ms / 1000))


def _linux_idle_seconds() -> int:
    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1.5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 0
    if result.returncode != 0:
        return 0
    try:
        return max(0, int(result.stdout.strip()) // 1000)
    except ValueError:
        return 0
