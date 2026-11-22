# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 03 (rdaccess_bridge/channel.py).
# Protocol behaviour from rdAccess@19f5c53, verified in the draft's experiment 02.

"""Server side of one RDAccess channel (``NVDA-SPEECH`` or ``NVDA-BRAILLE``).

The GNOME Remote Desktop preload exposes each channel as a Unix socket in
``$XDG_RUNTIME_DIR/gnome-remote-desktop/``.  Connecting to it makes GNOME
Remote Desktop open the dynamic virtual channel towards the RDP client.  Each
channel message travels in one frame of the 32-bit length framing
(``common.framing``), as between the FreeRDP plugin and the client.

The client attaches with XON, then the server announces protocol version 2
and both sides switch to JSON Lines.  XOFF detaches the client.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from ..common.framing import FrameDecoder, encode_frame
from ..common.rdaccess import (
    ATTRIBUTE_PROTOCOL_VERSION,
    CONNECTION_XOFF,
    CONNECTION_XON,
    LEGACY_COMMAND_ATTRIBUTE,
    MESSAGE_ATTRIBUTE_REQUEST,
    MESSAGE_ATTRIBUTE_VALUE,
    MESSAGE_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    ConnectionByte,
    InvalidBytes,
    InvalidLegacyAttribute,
    LegacyFrame,
    StreamDecoder,
    encode_json_line,
    legacy_protocol_version_frame,
    parse_legacy_attribute,
)

LOG = logging.getLogger(__name__)

SOCKET_DIRECTORY = "gnome-remote-desktop"
RETRY_SECONDS = 5
# After a connection that never got an XON (no RDAccess on the client), wait
# longer so as not to open a channel every few seconds.
RETRY_SECONDS_NO_CLIENT = 30
# Legacy attributes whose value is a little-endian integer.
LEGACY_INTEGER_ATTRIBUTES = (ATTRIBUTE_PROTOCOL_VERSION, "numCells", "numRows", "numCols")


def socket_path(file_name: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, SOCKET_DIRECTORY, file_name)


class Channel:
    """Keeps one RDAccess channel connected and parses what the client sends.

    ``on_message`` receives the client's JSON messages, and its legacy
    attribute requests and values translated to JSON.  ``on_ready_changed``
    is called when the client attaches (after the negotiation) or detaches.
    """

    def __init__(
        self,
        name: str,
        driver_type: bytes,
        file_name: str,
        on_message: Callable[[dict[str, Any]], None],
        on_ready_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self.name = name
        self._driver_type = driver_type
        self._path = socket_path(file_name)
        self._on_message = on_message
        self._on_ready_changed = on_ready_changed
        self._sock: socket.socket | None = None
        self._watch_id = 0
        self._retry_id = 0
        self._frames = FrameDecoder()
        self._stream = StreamDecoder(driver_type, CONNECTION_XON + CONNECTION_XOFF)
        self._got_xon = False
        self._json_mode = False
        self.ready = False
        self.attributes: dict[str, Any] = {}

    # Lifecycle

    def start(self) -> None:
        if self._sock is None and not self._retry_id:
            self._try_connect()

    def stop(self) -> None:
        if self._retry_id:
            GLib.source_remove(self._retry_id)
            self._retry_id = 0
        self._close(retry=False)

    def _schedule_retry(self, seconds: int) -> None:
        if not self._retry_id:
            self._retry_id = GLib.timeout_add_seconds(seconds, self._retry)

    def _retry(self) -> bool:
        self._retry_id = 0
        self._try_connect()
        return GLib.SOURCE_REMOVE

    def _try_connect(self) -> None:
        if not os.path.exists(self._path):
            self._schedule_retry(RETRY_SECONDS)
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(self._path)
        except OSError as error:
            sock.close()
            LOG.debug("%s: cannot connect to %s: %s", self.name, self._path, error)
            self._schedule_retry(RETRY_SECONDS)
            return
        sock.setblocking(False)
        self._sock = sock
        self._frames.reset()
        self._stream.clear()
        self._got_xon = False
        self._json_mode = False
        self._watch_id = GLib.io_add_watch(
            sock.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_io,
        )
        LOG.info("%s: channel open, waiting for the RDAccess client", self.name)

    def _close(self, retry: bool = True) -> None:
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = 0
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            LOG.info("%s: channel closed", self.name)
        got_xon = self._got_xon
        self._set_ready(False)
        if retry:
            self._schedule_retry(RETRY_SECONDS if got_xon else RETRY_SECONDS_NO_CLIENT)

    def _set_ready(self, ready: bool) -> None:
        if ready == self.ready:
            return
        self.ready = ready
        if self._on_ready_changed is not None:
            self._on_ready_changed(ready)

    # Output

    def _send_payload(self, payload: bytes) -> bool:
        if self._sock is None:
            return False
        try:
            self._sock.setblocking(True)
            self._sock.sendall(encode_frame(payload))
            self._sock.setblocking(False)
        except OSError as error:
            LOG.warning("%s: write failed: %s", self.name, error)
            self._close()
            return False
        return True

    def send(self, message_type: str, **fields: object) -> bool:
        """Send one JSON message; only once the channel is ready."""
        if not self.ready:
            return False
        return self._send_payload(encode_json_line(message_type, **fields))

    # Input

    def _on_io(self, _fd: int, condition: GLib.IOCondition) -> bool:
        data = b""
        if condition & GLib.IOCondition.IN and self._sock is not None:
            try:
                data = self._sock.recv(65536)
            except BlockingIOError:
                return GLib.SOURCE_CONTINUE
            except OSError:
                data = b""
        if not data:
            self._watch_id = 0
            self._close()
            return GLib.SOURCE_REMOVE
        try:
            frames = self._frames.feed(data)
        except ValueError as error:
            LOG.error("%s: invalid IPC data, closing the channel: %s", self.name, error)
            self._watch_id = 0
            self._close()
            return GLib.SOURCE_REMOVE
        for frame in frames:
            self._stream.feed(frame)
            try:
                self._parse()
            except Exception:
                # A handler failed: keep the channel, drop what it was parsing.
                # A GLib callback that raises would also remove this watch.
                LOG.exception("%s: cannot handle the client's data", self.name)
                self._stream.clear()
        return GLib.SOURCE_CONTINUE

    def _parse(self) -> None:
        while (item := self._stream.pop()) is not None:
            if isinstance(item, ConnectionByte):
                if item.value == CONNECTION_XON:
                    self._on_xon()
                else:
                    LOG.info("%s: RDAccess client detached", self.name)
                    self._json_mode = False
                    self._set_ready(False)
            elif isinstance(item, LegacyFrame):
                self._on_legacy(item)
            elif isinstance(item, InvalidBytes):
                LOG.error("%s: dropping %d unexpected bytes", self.name, len(item.data))
            else:
                self._on_json(item)

    def _on_xon(self) -> None:
        LOG.info("%s: RDAccess client attached", self.name)
        self._got_xon = True
        self._json_mode = False
        self._set_ready(False)
        self._send_payload(legacy_protocol_version_frame(self._driver_type))

    def _on_legacy(self, frame: LegacyFrame) -> None:
        if frame.command != LEGACY_COMMAND_ATTRIBUTE:
            return
        try:
            name, value = parse_legacy_attribute(frame.payload)
        except InvalidLegacyAttribute as error:
            LOG.error("%s: invalid legacy attribute frame: %s", self.name, error)
            return
        if not value:
            # Legacy attribute request (rdAccess addon/lib/protocol/legacy.py:203-207).
            self._switch_to_json()
            self._on_message({"type": MESSAGE_ATTRIBUTE_REQUEST, "attribute": name})
            return
        if name in LEGACY_INTEGER_ATTRIBUTES:
            self.attributes[name] = int.from_bytes(value, "little")
            if name == ATTRIBUTE_PROTOCOL_VERSION:
                if self.attributes[name] >= PROTOCOL_VERSION:
                    self._switch_to_json()
            else:
                self._on_message(
                    {
                        "type": MESSAGE_ATTRIBUTE_VALUE,
                        "attribute": name,
                        "value": self.attributes[name],
                    }
                )

    def _switch_to_json(self) -> None:
        if not self._json_mode:
            self._json_mode = True
            self._set_ready(True)
            LOG.info("%s: protocol version %d negotiated", self.name, PROTOCOL_VERSION)
            self.send(MESSAGE_PROTOCOL_VERSION, version=PROTOCOL_VERSION, channel=self.name)

    def _on_json(self, message: dict[str, Any]) -> None:
        self._switch_to_json()
        kind = message.get("type")
        if kind == MESSAGE_PROTOCOL_VERSION:
            return
        if kind == MESSAGE_ATTRIBUTE_VALUE:
            self.attributes[message.get("attribute")] = message.get("value")
        self._on_message(message)
