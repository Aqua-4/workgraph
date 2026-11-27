from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
from collections.abc import Mapping
from ctypes import wintypes
from typing import Protocol

import psutil

from workgraph.models import ActiveWindow, normalize_text

UNKNOWN_APP = "Unknown"


def is_kde_wayland_session(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    session_type = environment.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type:
        is_wayland = session_type == "wayland"
    else:
        is_wayland = bool(environment.get("WAYLAND_DISPLAY"))
    desktops = environment.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    return is_wayland and any(desktop.strip() == "kde" for desktop in desktops)


class PlatformProvider(Protocol):
    def get_active_window(self) -> ActiveWindow: ...

    def get_idle_seconds(self) -> int | None: ...


class WindowsPlatformProvider:
    def get_active_window(self) -> ActiveWindow:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ActiveWindow(UNKNOWN_APP, None, None, None, "windows")

        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value) if pid.value else None
        process_name = _process_name(process_id)
        return ActiveWindow(
            app_name=_app_name_from_process(process_name),
            process_name=process_name,
            window_title=normalize_text(buffer.value),
            process_id=process_id,
            platform="windows",
        )

    def get_idle_seconds(self) -> int | None:
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


class LinuxPlatformProvider:
    def get_active_window(self) -> ActiveWindow:
        window_id = self._active_window_id()
        if not window_id:
            return ActiveWindow(UNKNOWN_APP, None, None, None, "linux")

        title = self._window_title(window_id)
        pid = self._window_pid(window_id)
        process_name = _process_name(pid)
        return ActiveWindow(
            app_name=_app_name_from_process(process_name),
            process_name=process_name,
            window_title=normalize_text(title),
            process_id=pid,
            platform="linux",
        )

    def get_idle_seconds(self) -> int | None:
        output = _run_command(["xprintidle"])
        if output is None:
            return None
        try:
            return max(0, int(output) // 1000)
        except ValueError:
            return None

    @staticmethod
    def _active_window_id() -> str | None:
        output = _run_command(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        if output:
            match = re.search(r"window id # (0x[0-9a-fA-F]+)", output)
            if match and match.group(1) != "0x0":
                return match.group(1)

        output = _run_command(["xdotool", "getactivewindow"])
        return output if output and output.isdigit() else None

    @staticmethod
    def _window_title(window_id: str) -> str | None:
        output = _run_command(["xprop", "-id", window_id, "_NET_WM_NAME", "WM_NAME"])
        if output:
            matches = re.findall(r'=\s+"(.*)"', output)
            if matches:
                return matches[0]
        return _run_command(["xdotool", "getwindowname", window_id])

    @staticmethod
    def _window_pid(window_id: str) -> int | None:
        output = _run_command(["xprop", "-id", window_id, "_NET_WM_PID"])
        if not output:
            return None
        match = re.search(r"=\s+(\d+)", output)
        return int(match.group(1)) if match else None


class KDEWaylandPlatformProvider:
    requires_idle_for_collection = True

    def __init__(self) -> None:
        from collector.kwin_bridge import KWinBridge
        from collector.wayland_idle import WaylandIdleMonitor

        self._bridge = KWinBridge()
        self._idle_monitor = WaylandIdleMonitor()

    def start(self) -> None:
        self._bridge.start()
        self._idle_monitor.start()

    def close(self) -> None:
        self._bridge.close()
        self._idle_monitor.close()

    def get_active_window(self) -> ActiveWindow:
        window = self._bridge.get_active_window()
        if window is None:
            raise RuntimeError("KWin window tracking is unavailable")
        return window

    def get_idle_seconds(self) -> int | None:
        return self._idle_monitor.get_idle_seconds()


class UnsupportedPlatformProvider:
    def __init__(self, system_name: str) -> None:
        self.system_name = system_name or "unknown"

    def get_active_window(self) -> ActiveWindow:
        return ActiveWindow(UNKNOWN_APP, None, None, None, self.system_name)

    def get_idle_seconds(self) -> int | None:
        return None


def create_platform_provider(system_name: str | None = None) -> PlatformProvider:
    system = (system_name if system_name is not None else platform.system()).lower()
    if system == "windows":
        return WindowsPlatformProvider()
    if system == "linux":
        if is_kde_wayland_session():
            return KDEWaylandPlatformProvider()
        return LinuxPlatformProvider()
    return UnsupportedPlatformProvider(system)


def _process_name(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, ValueError):
        return None


def _app_name_from_process(process_name: str | None) -> str:
    if not process_name:
        return UNKNOWN_APP
    return process_name.removesuffix(".exe").lower()


def _run_command(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            check=False,
            text=True,
            timeout=1.5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
