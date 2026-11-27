import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("dbus_next")

from dbus_next import Message, MessageType

from collector.kwin_bridge import (
    BUS_NAME,
    OBJECT_PATH,
    KWinBridge,
    parse_window_snapshot,
)


def test_kwin_snapshot_resolves_process_and_normalizes_title() -> None:
    with patch("collector.kwin_bridge._process_name", return_value="code"):
        window = parse_window_snapshot(
            '{"title": " Editor  Window ", "pid": 42, "resource_class": "code"}'
        )

    assert window.app_name == "code"
    assert window.process_name == "code"
    assert window.window_title == "editor window"
    assert window.process_id == 42
    assert window.platform == "linux"


@pytest.mark.parametrize("payload", ["null", '{"title": null, "pid": 0}'])
def test_kwin_snapshot_with_no_process_is_unknown(payload: str) -> None:
    with patch("collector.kwin_bridge._process_name", return_value=None):
        window = parse_window_snapshot(payload)

    assert window.app_name == "Unknown"
    assert window.process_id is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"title": 123}',
        '{"resource_class": []}',
        '{"pid": true}',
        '{"pid": -1}',
        '{"pid": 2147483648}',
        '"' + "x" * 16384 + '"',
    ],
)
def test_kwin_snapshot_rejects_malformed_data(payload: str) -> None:
    with pytest.raises(ValueError):
        parse_window_snapshot(payload)


def test_kwin_bridge_rejects_untrusted_and_invalid_updates() -> None:
    bridge = KWinBridge()
    bridge._kwin_owner = ":1.42"
    bus = SimpleNamespace(send=lambda _message: None)

    def update(sender: str, payload: str) -> Message:
        return Message(
            message_type=MessageType.METHOD_CALL,
            path=OBJECT_PATH,
            interface=BUS_NAME,
            member="UpdateWindow",
            sender=sender,
            serial=1,
            signature="s",
            body=[payload],
        )

    with patch("collector.kwin_bridge._process_name", return_value="code"):
        bridge._handle_message(bus, update(":1.42", '{"pid": 42}'))
        assert bridge.get_active_window().app_name == "code"

        bridge._handle_message(bus, update(":1.99", '{"pid": 99}'))
        assert bridge.get_active_window().process_id == 42

        bridge._handle_message(bus, update(":1.42", '{"pid": true}'))
        assert bridge.get_active_window() is None


def test_kwin_bridge_clears_window_when_kwin_owner_changes() -> None:
    bridge = KWinBridge()
    bridge._kwin_owner = ":1.42"
    bus = SimpleNamespace(send=lambda _message: None)
    update = Message(
        path=OBJECT_PATH,
        interface=BUS_NAME,
        member="UpdateWindow",
        sender=":1.42",
        serial=1,
        signature="s",
        body=['{"pid": 42}'],
    )
    with patch("collector.kwin_bridge._process_name", return_value="code"):
        bridge._handle_message(bus, update)
    assert bridge.get_active_window() is not None

    owner_changed = Message(
        message_type=MessageType.SIGNAL,
        path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus",
        member="NameOwnerChanged",
        sender="org.freedesktop.DBus",
        signature="sss",
        body=["org.kde.KWin", ":1.42", ":1.43"],
    )
    bridge._handle_message(bus, owner_changed)

    assert bridge.get_active_window() is None
    assert bridge._kwin_owner is None


def test_kwin_bridge_reloads_orphaned_script() -> None:
    bridge = KWinBridge()
    call = AsyncMock(side_effect=[[":1.42"], [True], [True], [7], [], [True]])

    async def stop_after_iteration() -> None:
        bridge._stop.set()

    with (
        patch.object(bridge, "_call", call),
        patch.object(bridge, "_wait_for_stop", stop_after_iteration),
    ):
        asyncio.run(bridge._monitor_kwin(SimpleNamespace()))

    members = [entry.args[4] for entry in call.await_args_list]
    assert members == [
        "GetNameOwner",
        "isScriptLoaded",
        "unloadScript",
        "loadScript",
        "run",
        "unloadScript",
    ]
