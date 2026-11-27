import ctypes
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from collector.platforms import (
    KDEWaylandPlatformProvider,
    LinuxPlatformProvider,
    UnsupportedPlatformProvider,
    WindowsPlatformProvider,
    create_platform_provider,
    is_kde_wayland_session,
)
from workgraph.models import ActiveWindow


def test_kde_wayland_provider_delegates_to_bridge() -> None:
    pytest.importorskip("dbus_next")
    window = ActiveWindow("code", "code", "editor", 42, "linux")
    with (
        patch("collector.kwin_bridge.KWinBridge") as bridge_type,
        patch("collector.wayland_idle.WaylandIdleMonitor") as idle_type,
    ):
        bridge = bridge_type.return_value
        idle = idle_type.return_value
        bridge.get_active_window.return_value = window
        idle.get_idle_seconds.return_value = 15
        provider = KDEWaylandPlatformProvider()
        provider.start()
        assert provider.get_active_window() == window
        assert provider.get_idle_seconds() == 15
        provider.close()
        bridge.start.assert_called_once()
        bridge.close.assert_called_once()
        idle.start.assert_called_once()
        idle.close.assert_called_once()


def test_kde_wayland_provider_rejects_unavailable_window() -> None:
    pytest.importorskip("dbus_next")
    with (
        patch("collector.kwin_bridge.KWinBridge") as bridge_type,
        patch("collector.wayland_idle.WaylandIdleMonitor"),
    ):
        bridge_type.return_value.get_active_window.return_value = None
        provider = KDEWaylandPlatformProvider()
        with pytest.raises(RuntimeError, match="unavailable"):
            provider.get_active_window()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}, True),
        (
            {
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "KDE",
                "DISPLAY": ":0",
            },
            True,
        ),
        ({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}, True),
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, False),
        (
            {
                "XDG_SESSION_TYPE": "x11",
                "XDG_CURRENT_DESKTOP": "KDE",
                "WAYLAND_DISPLAY": "wayland-0",
            },
            False,
        ),
        ({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "KDE"}, False),
        ({}, False),
    ],
)
def test_identify_kde_wayland_session(
    environment: dict[str, str], expected: bool
) -> None:
    assert is_kde_wayland_session(environment) is expected


@pytest.mark.parametrize(
    ("system_name", "provider_type"),
    [
        ("Windows", WindowsPlatformProvider),
        ("Linux", LinuxPlatformProvider),
        ("Darwin", UnsupportedPlatformProvider),
    ],
)
def test_platform_provider_factory(system_name: str, provider_type: type) -> None:
    with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}):
        assert isinstance(create_platform_provider(system_name), provider_type)


def test_platform_provider_factory_selects_kde_wayland() -> None:
    with (
        patch.dict(
            "os.environ",
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
        ),
        patch("collector.platforms.KDEWaylandPlatformProvider") as provider_type,
    ):
        assert create_platform_provider("Linux") is provider_type.return_value


def test_unsupported_platform_reports_window_and_idle_unavailable() -> None:
    provider = create_platform_provider("Darwin")

    active_window = provider.get_active_window()

    assert active_window.app_name == "Unknown"
    assert active_window.platform == "darwin"
    assert provider.get_idle_seconds() is None


def test_linux_active_window_uses_xdotool_when_xprop_fails() -> None:
    provider = LinuxPlatformProvider()
    with (
        patch(
            "collector.platforms._run_command",
            side_effect=[
                None,
                "123",
                '_NET_WM_NAME(UTF8_STRING) = "Window title"',
                "_NET_WM_PID(CARDINAL) = 42",
            ],
        ),
        patch("collector.platforms._process_name", return_value="code"),
    ):
        active_window = provider.get_active_window()

    assert active_window.platform == "linux"
    assert active_window.app_name == "code"
    assert active_window.process_name == "code"
    assert active_window.window_title == "window title"
    assert active_window.process_id == 42


def test_windows_provider_reads_active_window_and_idle_state() -> None:
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 1,
        GetWindowTextLengthW=lambda _hwnd: 7,
        GetWindowTextW=lambda _hwnd, buffer, _size: setattr(buffer, "value", "Editor"),
        GetWindowThreadProcessId=lambda _hwnd, pointer: setattr(
            pointer._obj, "value", 42
        ),
        GetLastInputInfo=lambda pointer: setattr(pointer._obj, "dwTime", 10000) or 1,
    )
    kernel32 = SimpleNamespace(GetTickCount=lambda: 40000)
    with (
        patch.object(
            ctypes,
            "windll",
            SimpleNamespace(user32=user32, kernel32=kernel32),
            create=True,
        ),
        patch("collector.platforms._process_name", return_value="editor.exe"),
    ):
        provider = WindowsPlatformProvider()
        active_window = provider.get_active_window()
        idle_seconds = provider.get_idle_seconds()

    assert active_window.app_name == "editor"
    assert active_window.process_name == "editor.exe"
    assert active_window.window_title == "editor"
    assert active_window.process_id == 42
    assert active_window.platform == "windows"
    assert idle_seconds == 30


@pytest.mark.parametrize(
    ("output", "expected"),
    [("12500", 12), ("0", 0), ("invalid", None), (None, None)],
)
def test_linux_idle_provider_parses_tool_output(
    output: str | None, expected: int | None
) -> None:
    with patch("collector.platforms._run_command", return_value=output):
        assert LinuxPlatformProvider().get_idle_seconds() == expected
