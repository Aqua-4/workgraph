from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

from dbus_next import Message, MessageType, NameFlag, RequestNameReply
from dbus_next.aio import MessageBus

from collector.platforms import UNKNOWN_APP, _app_name_from_process, _process_name
from workgraph.models import ActiveWindow, normalize_text

MAX_WINDOW_PAYLOAD_BYTES = 16384
LOGGER = logging.getLogger(__name__)
BUS_NAME = "org.workgraph.Collector"
OBJECT_PATH = "/org/workgraph/Collector"
KWIN_NAME = "org.kde.KWin"
SCRIPT_NAME = "workgraph-window-tracking"


def parse_window_snapshot(payload: str) -> ActiveWindow:
    if len(payload.encode("utf-8")) > MAX_WINDOW_PAYLOAD_BYTES:
        raise ValueError("KWin window snapshot is too large")
    try:
        snapshot = json.loads(payload)
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid KWin window snapshot") from error

    if snapshot is None:
        return ActiveWindow(UNKNOWN_APP, None, None, None, "linux")
    if not isinstance(snapshot, dict):
        raise ValueError("Invalid KWin window snapshot")

    title = snapshot.get("title")
    resource_class = snapshot.get("resource_class")
    pid = snapshot.get("pid")
    if (
        (title is not None and not isinstance(title, str))
        or (resource_class is not None and not isinstance(resource_class, str))
        or (pid is not None and (type(pid) is not int or not 0 <= pid <= 2147483647))
    ):
        raise ValueError("Invalid KWin window snapshot")

    process_id = pid or None
    process_name = _process_name(process_id)
    return ActiveWindow(
        app_name=_app_name_from_process(process_name),
        process_name=process_name,
        window_title=normalize_text(title),
        process_id=process_id,
        platform="linux",
    )


class KWinBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window: ActiveWindow | None = None
        self._kwin_owner: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("KWin bridge already started")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        loop = self._loop
        async_stop = self._async_stop
        if loop is not None and async_stop is not None:
            try:
                loop.call_soon_threadsafe(async_stop.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                LOGGER.warning("KWin bridge did not stop promptly")
        self._clear_window()

    def get_active_window(self) -> ActiveWindow | None:
        with self._lock:
            return self._window

    def _clear_window(self) -> None:
        with self._lock:
            self._window = None

    def _handle_message(self, bus: MessageBus, message: Message) -> bool:
        if (
            message.message_type == MessageType.SIGNAL
            and message.sender == "org.freedesktop.DBus"
            and message.interface == "org.freedesktop.DBus"
            and message.member == "NameOwnerChanged"
            and len(message.body) == 3
            and message.body[0] == KWIN_NAME
        ):
            self._kwin_owner = None
            self._clear_window()
            return False
        if not (
            message.message_type == MessageType.METHOD_CALL
            and message.path == OBJECT_PATH
            and message.interface == BUS_NAME
            and message.member == "UpdateWindow"
        ):
            return False
        if message.sender != self._kwin_owner or self._kwin_owner is None:
            bus.send(Message.new_error(message, "org.workgraph.AccessDenied", "Denied"))
            return True
        if message.signature != "s" or len(message.body) != 1:
            self._clear_window()
            bus.send(
                Message.new_error(message, "org.workgraph.InvalidWindow", "Invalid")
            )
            return True
        try:
            window = parse_window_snapshot(message.body[0])
        except ValueError:
            self._clear_window()
            bus.send(
                Message.new_error(message, "org.workgraph.InvalidWindow", "Invalid")
            )
            return True
        with self._lock:
            self._window = window
        bus.send(Message.new_method_return(message))
        return True

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        if self._stop.is_set():
            self._async_stop.set()
        while not self._stop.is_set():
            bus: MessageBus | None = None
            try:
                bus = await asyncio.wait_for(MessageBus().connect(), timeout=3)
                reply = await asyncio.wait_for(
                    bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE), timeout=3
                )
                if reply != RequestNameReply.PRIMARY_OWNER:
                    raise RuntimeError("KWin bridge bus name is already in use")
                bus.add_message_handler(
                    lambda message: self._handle_message(bus, message)
                )
                await self._call(
                    bus,
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "AddMatch",
                    "s",
                    [
                        "type='signal',sender='org.freedesktop.DBus',"
                        "interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                        "arg0='org.kde.KWin'"
                    ],
                )
                await self._monitor_kwin(bus)
            except Exception as error:
                LOGGER.warning("KWin bridge unavailable: %s", type(error).__name__)
            finally:
                self._kwin_owner = None
                self._clear_window()
                if bus is not None:
                    bus.disconnect()
            if not self._stop.is_set():
                await self._wait_for_stop()
        self._loop = None
        self._async_stop = None

    async def _monitor_kwin(self, bus: MessageBus) -> None:
        try:
            while not self._stop.is_set():
                owner = (
                    await self._call(
                        bus,
                        "org.freedesktop.DBus",
                        "/org/freedesktop/DBus",
                        "org.freedesktop.DBus",
                        "GetNameOwner",
                        "s",
                        [KWIN_NAME],
                    )
                )[0]
                owner_changed = owner != self._kwin_owner
                if owner_changed:
                    self._clear_window()
                    self._kwin_owner = owner
                loaded = (
                    await self._call(
                        bus,
                        owner,
                        "/Scripting",
                        "org.kde.kwin.Scripting",
                        "isScriptLoaded",
                        "s",
                        [SCRIPT_NAME],
                    )
                )[0]
                if owner_changed and loaded:
                    await self._call(
                        bus,
                        owner,
                        "/Scripting",
                        "org.kde.kwin.Scripting",
                        "unloadScript",
                        "s",
                        [SCRIPT_NAME],
                    )
                    loaded = False
                if not loaded:
                    self._clear_window()
                    script_id = (
                        await self._call(
                            bus,
                            owner,
                            "/Scripting",
                            "org.kde.kwin.Scripting",
                            "loadScript",
                            "ss",
                            [
                                str(Path(__file__).with_name("kwin_window.js")),
                                SCRIPT_NAME,
                            ],
                        )
                    )[0]
                    await self._call(
                        bus,
                        owner,
                        f"/Scripting/Script{script_id}",
                        "org.kde.kwin.Script",
                        "run",
                    )
                await self._wait_for_stop()
        finally:
            if self._kwin_owner is not None:
                try:
                    await self._call(
                        bus,
                        self._kwin_owner,
                        "/Scripting",
                        "org.kde.kwin.Scripting",
                        "unloadScript",
                        "s",
                        [SCRIPT_NAME],
                    )
                except Exception:
                    LOGGER.debug("Could not unload KWin tracking script")

    async def _wait_for_stop(self) -> None:
        assert self._async_stop is not None
        try:
            await asyncio.wait_for(self._async_stop.wait(), timeout=3)
        except TimeoutError:
            pass

    @staticmethod
    async def _call(
        bus: MessageBus,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list | None = None,
    ) -> list:
        reply = await asyncio.wait_for(
            bus.call(
                Message(
                    destination=destination,
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            ),
            timeout=3,
        )
        if reply is None or reply.message_type == MessageType.ERROR:
            raise RuntimeError("KWin D-Bus request failed")
        return reply.body
