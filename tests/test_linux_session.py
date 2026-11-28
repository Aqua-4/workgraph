import os
import socket
from unittest.mock import patch

from services.linux_session import discover_session


def test_discovers_wayland_session_from_user_manager(tmp_path):
    display = tmp_path / "wayland-test"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(display))
        outputs = [
            f"3 {os.getuid()} user seat0\n",
            f"Id=3\nUser={os.getuid()}\nType=wayland\nClass=user\nActive=yes\nRemote=no\nDesktop=KDE\nSeat=seat0\n",
            f"RuntimePath={tmp_path}\n",
            f"XDG_RUNTIME_DIR={tmp_path}\nWAYLAND_DISPLAY=wayland-test\nDBUS_SESSION_BUS_ADDRESS=unix:path={tmp_path}/bus\n",
        ]
        with patch("services.linux_session._command", side_effect=outputs):
            result = discover_session({})
    assert result.session is not None
    assert dict(result.session.environment)["XDG_SESSION_TYPE"] == "wayland"
    assert dict(result.session.environment)["XDG_CURRENT_DESKTOP"] == "KDE"


def test_waits_for_login_and_rejects_ambiguous_sessions():
    with patch("services.linux_session._command", return_value=""):
        assert discover_session({}).reason == "waiting for a graphical login"
    properties = (
        f"Id=3\nUser={os.getuid()}\nType=x11\nClass=user\nActive=yes\nRemote=no\n"
    )
    with patch(
        "services.linux_session._command",
        side_effect=[f"3 {os.getuid()}\n4 {os.getuid()}\n", properties, properties],
    ):
        assert "multiple" in discover_session({}).reason


def test_rejects_stale_or_unsupported_wayland_environment(tmp_path):
    properties = (
        f"Id=3\nUser={os.getuid()}\nType=wayland\nClass=user\n"
        "Active=yes\nRemote=no\nDesktop=KDE\n"
    )
    outputs = [
        f"3 {os.getuid()}\n",
        properties,
        f"RuntimePath={tmp_path}\n",
        f"XDG_RUNTIME_DIR={tmp_path}\nXDG_SESSION_ID=old\n",
    ]
    with patch("services.linux_session._command", side_effect=outputs):
        assert "different session" in discover_session({}).reason
    with patch(
        "services.linux_session._command",
        side_effect=[f"3 {os.getuid()}\n", properties.replace("KDE", "GNOME")],
    ):
        assert "unsupported" in discover_session({}).reason


def test_logind_unavailable_uses_valid_inherited_x11_session():
    with patch("services.linux_session._command", side_effect=FileNotFoundError):
        result = discover_session({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
        missing = discover_session({})
    assert not result.available
    assert result.session is not None
    assert dict(result.session.environment)["DISPLAY"] == ":0"
    assert missing.session is None
