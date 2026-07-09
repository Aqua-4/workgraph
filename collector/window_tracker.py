from __future__ import annotations

import ctypes
import platform
import re
import subprocess
from ctypes import wintypes

import psutil

from workgraph.models import ActiveWindow, normalize_text


UNKNOWN_APP = "Unknown"


class WindowTracker:
    """Reads the currently focused application and window title."""

    def get_active_window(self) -> ActiveWindow:
        system = platform.system().lower()
        if system == "windows":
            return _get_windows_active_window()
        if system == "linux":
            return _get_linux_active_window()
        return ActiveWindow(
            app_name=UNKNOWN_APP,
            process_name=None,
            window_title=None,
            process_id=None,
            platform=system or "unknown",
        )


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
    return process_name.removesuffix(".exe")


def _get_windows_active_window() -> ActiveWindow:
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


def _get_linux_active_window() -> ActiveWindow:
    window_id = _linux_active_window_id()
    if not window_id:
        return ActiveWindow(UNKNOWN_APP, None, None, None, "linux")

    title = _linux_window_title(window_id)
    pid = _linux_window_pid(window_id)
    process_name = _process_name(pid)
    return ActiveWindow(
        app_name=_app_name_from_process(process_name),
        process_name=process_name,
        window_title=normalize_text(title),
        process_id=pid,
        platform="linux",
    )


def _linux_active_window_id() -> str | None:
    output = _run_command(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if output:
        match = re.search(r"window id # (0x[0-9a-fA-F]+)", output)
        if match and match.group(1) != "0x0":
            return match.group(1)

    output = _run_command(["xdotool", "getactivewindow"])
    return output if output and output.isdigit() else None


def _linux_window_title(window_id: str) -> str | None:
    output = _run_command(["xprop", "-id", window_id, "_NET_WM_NAME", "WM_NAME"])
    if output:
        matches = re.findall(r'=\s+"(.*)"', output)
        if matches:
            return matches[0]

    return _run_command(["xdotool", "getwindowname", window_id])


def _linux_window_pid(window_id: str) -> int | None:
    output = _run_command(["xprop", "-id", window_id, "_NET_WM_PID"])
    if not output:
        return None
    match = re.search(r"=\s+(\d+)", output)
    return int(match.group(1)) if match else None
