from __future__ import annotations

import logging
import platform
import queue
import shutil
import subprocess
import sys
import threading
from importlib import import_module
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_ACTIONS = ("snooze", "break")


def error_beep() -> None:
    if platform.system().lower() == "windows":
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND)
            return
        except OSError:
            LOGGER.warning("Could not play Windows error sound")

    command = shutil.which("canberra-gtk-play")
    if command:
        try:
            result = subprocess.run(
                [command, "--id=dialog-error"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except OSError:
        LOGGER.warning("Could not play error sound")


def _control_dir(log_path: str) -> Path:
    return Path(log_path).parent / ".wellbeing"


def send_control_command(log_path: str, action: str) -> None:
    if action not in _ACTIONS:
        raise ValueError("Unknown wellbeing action")
    directory = _control_dir(log_path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / action).touch()


def read_control_commands(log_path: str) -> list[str]:
    actions = []
    for action in _ACTIONS:
        try:
            (_control_dir(log_path) / action).unlink()
        except FileNotFoundError:
            continue
        except OSError:
            LOGGER.warning("Could not read wellbeing control command", exc_info=True)
        else:
            actions.append(action)
    return actions


class WellbeingDelivery:
    def __init__(self) -> None:
        self._actions: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._speech_lock = threading.Lock()
        self._speaking = False
        self._speech_process: subprocess.Popen[str] | None = None
        self._startup_speech_status = "Not checked"
        self._closed = False
        self._warned: set[str] = set()

    def notify(self, message: str) -> None:
        threading.Thread(target=self._notify, args=(message,), daemon=True).start()

    def notify_reminder(self, title: str, message: str) -> None:
        threading.Thread(
            target=self._notify_reminder, args=(title, message), daemon=True
        ).start()

    def _notify_reminder(self, title: str, message: str) -> None:
        system = platform.system().lower()
        if system == "linux":
            command = shutil.which("notify-send")
            if not command:
                self._warn_once("notifications", "notify-send is not installed")
                return
            try:
                result = subprocess.run(
                    [command, "--app-name=WorkGraph", title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                if result.returncode != 0:
                    self._warn_once("notifications", "Desktop notification failed")
            except (OSError, subprocess.SubprocessError):
                self._warn_once("notifications", "Could not launch notify-send")
        elif system == "windows":
            try:
                Notification = import_module("winotify").Notification

                Notification(app_id="WorkGraph", title=title, msg=message).show()
            except Exception:
                self._warn_once(
                    "notifications",
                    "Windows notifications unavailable; check winotify installation",
                )
        elif system == "darwin":
            command = shutil.which("osascript")
            if not command:
                self._warn_once("notifications", "osascript is not installed")
                return
            script = (
                "on run argv\n"
                " display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run"
            )
            try:
                result = subprocess.run(
                    [command, "-e", script, title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                if result.returncode != 0:
                    self._warn_once("notifications", "Desktop notification failed")
            except (OSError, subprocess.SubprocessError):
                self._warn_once("notifications", "Could not launch osascript")
        else:
            self._warn_once("notifications", "Desktop notifications are not supported")

    def _notify(self, message: str) -> None:
        system = platform.system().lower()
        if system == "linux":
            command = shutil.which("notify-send")
            if not command:
                self._warn_once("notifications", "notify-send is not installed")
                return
            base = [command, "--app-name=WorkGraph"]
            try:
                result = subprocess.run(
                    base
                    + [
                        "--action=snooze=Snooze",
                        "--action=break=Break taken",
                        "Time for a break",
                        message,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if result.returncode != 0:
                    result = subprocess.run(
                        base + ["Time for a break", message],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    if result.returncode != 0:
                        self._warn_once("notifications", "Desktop notification failed")
                elif result.stdout.strip() in _ACTIONS:
                    self._actions.put(result.stdout.strip())
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                self._warn_once("notifications", "Could not launch notify-send")
        elif system == "windows":
            try:
                Notification = import_module("winotify").Notification

                Notification(
                    app_id="WorkGraph", title="Time for a break", msg=message
                ).show()
            except Exception:
                self._warn_once(
                    "notifications",
                    "Windows notifications unavailable; check winotify installation",
                )
        else:
            self._warn_once("notifications", "Desktop notifications are not supported")

    @property
    def startup_speech_status(self) -> str:
        with self._speech_lock:
            return self._startup_speech_status

    def speak(self, message: str, *, error_on_failure: bool = False) -> None:
        with self._speech_lock:
            if self._closed or self._speaking:
                return
            self._speaking = True
            if error_on_failure:
                self._startup_speech_status = "Starting"
        threading.Thread(
            target=self._speak, args=(message, error_on_failure), daemon=True
        ).start()

    def _speak(self, message: str, error_on_failure: bool = False) -> None:
        startup_status = "Failed"
        try:
            with self._speech_lock:
                if self._closed:
                    return
                self._speech_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import pyttsx3, sys; engine = pyttsx3.init(); "
                        "engine.say(sys.argv[1]); engine.runAndWait()",
                        message,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                process = self._speech_process
            try:
                _, errors = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self._warn_once("speech", "Speech engine timed out")
                if error_on_failure:
                    error_beep()
            else:
                if process.returncode != 0:
                    self._warn_once("speech", f"Speech unavailable: {errors.strip()}")
                    if error_on_failure:
                        error_beep()
                else:
                    startup_status = "Completed"
        except OSError:
            self._warn_once("speech", "Could not start speech engine")
            if error_on_failure:
                error_beep()
        finally:
            with self._speech_lock:
                if error_on_failure:
                    self._startup_speech_status = startup_status
                self._speech_process = None
                self._speaking = False

    def read_actions(self) -> list[str]:
        actions = []
        while True:
            try:
                actions.append(self._actions.get_nowait())
            except queue.Empty:
                return actions

    def close(self) -> None:
        with self._speech_lock:
            self._closed = True
            if self._speech_process and self._speech_process.poll() is None:
                self._speech_process.terminate()

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            LOGGER.warning("Wellbeing: %s", message)
