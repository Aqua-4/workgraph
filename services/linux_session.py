from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SESSION_VARIABLES = (
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_ID",
    "XDG_RUNTIME_DIR",
    "XDG_SEAT",
    "WAYLAND_DISPLAY",
    "DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
)


@dataclass(frozen=True)
class DesktopSession:
    session_id: str
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SessionDiscovery:
    session: DesktopSession | None
    reason: str
    available: bool = True
    identity: str | None = None


def _command(arguments: list[str], environment: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env=environment,
    )
    return result.stdout


def _properties(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _valid_runtime(runtime: str, user_id: int) -> bool:
    try:
        info = os.stat(runtime)
        return (
            Path(runtime).is_absolute()
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == user_id
            and os.access(runtime, os.R_OK | os.W_OK | os.X_OK)
        )
    except OSError:
        return False


def _valid_wayland_socket(runtime: str, display: str, user_id: int) -> bool:
    if not display:
        return False
    path = Path(display)
    if not path.is_absolute():
        path = Path(runtime) / path
    try:
        info = path.stat()
        return stat.S_ISSOCK(info.st_mode) and info.st_uid == user_id
    except OSError:
        return False


def _inherited_session(
    environment: Mapping[str, str], user_id: int
) -> DesktopSession | None:
    session_type = environment.get("XDG_SESSION_TYPE", "").lower()
    if not session_type and environment.get("WAYLAND_DISPLAY"):
        session_type = "wayland"
    if session_type == "wayland":
        desktops = environment.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
        runtime = environment.get("XDG_RUNTIME_DIR", "")
        if (
            "kde" not in desktops
            or not _valid_runtime(runtime, user_id)
            or not _valid_wayland_socket(
                runtime, environment.get("WAYLAND_DISPLAY", ""), user_id
            )
            or not environment.get("DBUS_SESSION_BUS_ADDRESS")
        ):
            return None
    elif session_type == "x11":
        authority = environment.get("XAUTHORITY")
        if not environment.get("DISPLAY") or (
            authority and not os.access(authority, os.R_OK)
        ):
            return None
    else:
        return None
    values = {key: environment[key] for key in SESSION_VARIABLES if key in environment}
    values["XDG_SESSION_TYPE"] = session_type
    return DesktopSession(
        environment.get("XDG_SESSION_ID", "inherited"), tuple(sorted(values.items()))
    )


def discover_session(environment: Mapping[str, str] | None = None) -> SessionDiscovery:
    inherited = os.environ if environment is None else environment
    user_id = os.getuid()
    try:
        lines = _command(
            ["loginctl", "list-sessions", "--no-legend", "--no-pager"]
        ).splitlines()
        sessions = []
        for line in lines:
            fields = line.split()
            if len(fields) < 2 or fields[1] != str(user_id):
                continue
            properties = _properties(
                _command(
                    [
                        "loginctl",
                        "show-session",
                        fields[0],
                        "--no-pager",
                        "-p",
                        "Id",
                        "-p",
                        "User",
                        "-p",
                        "Type",
                        "-p",
                        "Class",
                        "-p",
                        "Active",
                        "-p",
                        "Remote",
                        "-p",
                        "Desktop",
                        "-p",
                        "Seat",
                    ]
                )
            )
            if (
                properties.get("User") == str(user_id)
                and properties.get("Class") == "user"
                and properties.get("Remote") == "no"
                and properties.get("Type") in {"x11", "wayland"}
            ):
                sessions.append(properties)
    except (OSError, subprocess.SubprocessError):
        return SessionDiscovery(
            _inherited_session(inherited, user_id),
            "desktop session discovery is unavailable; "
            "using inherited environment when valid",
            available=False,
        )

    active = [session for session in sessions if session.get("Active") == "yes"]
    if not active:
        return SessionDiscovery(None, "waiting for a graphical login")
    if len(active) != 1 or len(sessions) != 1:
        return SessionDiscovery(
            None, "multiple graphical sessions cannot be attributed safely"
        )
    session = active[0]
    session_id = session.get("Id", "")
    session_type = session["Type"]
    desktop = session.get("Desktop", "")
    if not session_id:
        return SessionDiscovery(None, "desktop session identity is unavailable")
    if session_type == "wayland" and desktop.lower() != "kde":
        return SessionDiscovery(
            None, "unsupported or unidentified Wayland desktop", identity=session_id
        )

    if inherited.get("XDG_SESSION_ID") == session_id:
        session_environment = {
            key: inherited[key] for key in SESSION_VARIABLES if key in inherited
        }
    else:
        try:
            runtime = inherited.get("XDG_RUNTIME_DIR", "")
            if not _valid_runtime(runtime, user_id):
                user_properties = _properties(
                    _command(
                        [
                            "loginctl",
                            "show-user",
                            str(user_id),
                            "--no-pager",
                            "-p",
                            "RuntimePath",
                        ]
                    )
                )
                runtime = user_properties.get("RuntimePath", "")
            if not _valid_runtime(runtime, user_id):
                return SessionDiscovery(
                    None,
                    "waiting for the desktop runtime directory",
                    identity=session_id,
                )
            manager_environment = dict(inherited)
            manager_environment["XDG_RUNTIME_DIR"] = runtime
            manager_environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
            manager = _properties(
                _command(
                    ["systemctl", "--user", "show-environment"],
                    environment=manager_environment,
                )
            )
        except (OSError, subprocess.SubprocessError):
            return SessionDiscovery(
                None, "waiting for the desktop environment", identity=session_id
            )
        session_environment = {
            key: manager[key] for key in SESSION_VARIABLES if key in manager
        }
        session_environment.setdefault("XDG_RUNTIME_DIR", runtime)

    if session_environment.get("XDG_SESSION_ID", session_id) != session_id:
        return SessionDiscovery(
            None,
            "desktop environment belongs to a different session",
            identity=session_id,
        )
    if (
        session_environment.get("XDG_SESSION_TYPE", session_type).lower()
        != session_type
    ):
        return SessionDiscovery(
            None, "desktop environment does not match the session", identity=session_id
        )
    if desktop and session_environment.get("XDG_CURRENT_DESKTOP"):
        desktops = session_environment["XDG_CURRENT_DESKTOP"].lower().split(":")
        if desktop.lower() not in desktops:
            return SessionDiscovery(
                None,
                "desktop environment does not match the session",
                identity=session_id,
            )
    session_environment["XDG_SESSION_ID"] = session_id
    session_environment["XDG_SESSION_TYPE"] = session_type
    if desktop:
        session_environment["XDG_CURRENT_DESKTOP"] = desktop
    if session.get("Seat"):
        session_environment["XDG_SEAT"] = session["Seat"]
    runtime = session_environment.get("XDG_RUNTIME_DIR", "")
    if not _valid_runtime(runtime, user_id):
        return SessionDiscovery(
            None, "waiting for the desktop runtime directory", identity=session_id
        )
    if session_type == "wayland":
        if not _valid_wayland_socket(
            runtime, session_environment.get("WAYLAND_DISPLAY", ""), user_id
        ):
            return SessionDiscovery(
                None, "waiting for the Wayland display", identity=session_id
            )
        if not session_environment.get("DBUS_SESSION_BUS_ADDRESS"):
            return SessionDiscovery(
                None, "waiting for the desktop message bus", identity=session_id
            )
    elif not session_environment.get("DISPLAY"):
        return SessionDiscovery(
            None, "waiting for the X11 display", identity=session_id
        )
    authority = session_environment.get("XAUTHORITY")
    if session_type == "x11" and authority and not os.access(authority, os.R_OK):
        return SessionDiscovery(
            None, "waiting for X11 authorization", identity=session_id
        )
    return SessionDiscovery(
        DesktopSession(session_id, tuple(sorted(session_environment.items()))),
        "ready",
        identity=session_id,
    )
