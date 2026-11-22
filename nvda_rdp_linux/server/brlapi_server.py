# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 06 (rdaccess_brlapi/brlapi_server.py).
# Written from the protocol as the BrlAPI client library sends and expects it
# (brltty@3c0a178: Programs/brlapi_protocol.h, Programs/brlapi_client.c,
# Programs/brlapi_server.c, Programs/brlapi_param.h). No BRLTTY code copied.

"""A minimal BrlAPI server, protocol version 8, on the GLib loop, for one
virtual braille display.

It speaks just enough of the protocol for a client such as Orca
(orca@7f241c6: src/orca/braille.py) to use the display as it would a display
driven by BRLTTY:

1. handshake: server version, client version, key authorization;
2. display size, entering and leaving tty mode, key ranges, priority;
3. write packets (text, attributes, cursor), handed to the owner;
4. key packets (BrlAPI command codes) sent by the owner to the client.

Several clients may connect. Like BRLTTY with no screen focus, the last
client that wrote is the one shown, and keys go to the last client that
entered tty mode and accepts them.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from gi.repository import GLib

LOG = logging.getLogger(__name__)

PROTOCOL_VERSION = 8
MAX_PACKET_SIZE = 4096
HEADER = struct.Struct("!II")  # payload size, packet type


def _type(name: str) -> int:
    return ord(name) if len(name) == 1 else (ord(name[0]) << 8) | ord(name[1])


# brlapi_protocol.h:58-81
P_VERSION = _type("v")
P_AUTH = _type("a")
P_GETDRIVERNAME = _type("n")
P_GETMODELID = _type("d")
P_GETDISPLAYSIZE = _type("s")
P_ENTERTTYMODE = _type("t")
P_SETFOCUS = _type("F")
P_LEAVETTYMODE = _type("L")
P_KEY = _type("k")
P_IGNOREKEYRANGES = _type("m")
P_ACCEPTKEYRANGES = _type("u")
P_WRITE = _type("w")
P_ACK = _type("A")
P_ERROR = _type("e")
P_SYNCHRONIZE = _type("Z")
P_PARAM_VALUE = _type("PV")
P_PARAM_REQUEST = _type("PR")

AUTH_KEY = ord("K")  # brlapi_protocol.h, BRLAPI_AUTH_KEY

# brlapi_protocol.h:129-135
WF_DISPLAYNUMBER = 0x01
WF_REGION = 0x02
WF_TEXT = 0x04
WF_ATTR_AND = 0x08
WF_ATTR_OR = 0x10
WF_CURSOR = 0x20
WF_CHARSET = 0x40

# brlapi.h.in:1292-1318
ERROR_UNKNOWN_INSTRUCTION = 4
ERROR_ILLEGAL_INSTRUCTION = 5
ERROR_INVALID_PACKET = 7
ERROR_AUTHENTICATION = 17

DRIVER_NAME = b"RDAccess"
MODEL_ID = b"nvda"


@dataclass
class Write:
    """One write packet, decoded."""

    region_begin: int = 0
    region_size: int = 0
    text: str | None = None
    and_mask: bytes | None = None
    or_mask: bytes | None = None
    cursor: int | None = None  # None: leave as is; 0: no cursor; n: cell n (1-based)


def parse_write(payload: bytes, display_size: int) -> Write:
    """Decodes a write packet (field order: brlapi_client.c, brlapi__write)."""

    (flags,) = struct.unpack_from("!I", payload, 0)
    pos = 4
    w = Write()
    if flags & WF_DISPLAYNUMBER:
        pos += 4
    if flags & WF_REGION:
        w.region_begin, size = struct.unpack_from("!Ii", payload, pos)
        pos += 8
        w.region_size = abs(size)
    else:
        w.region_begin, w.region_size = 1, display_size
    raw_text = None
    if flags & WF_TEXT:
        (length,) = struct.unpack_from("!I", payload, pos)
        pos += 4
        raw_text = payload[pos : pos + length]
        pos += length
    if flags & WF_ATTR_AND:
        w.and_mask = payload[pos : pos + w.region_size]
        pos += w.region_size
    if flags & WF_ATTR_OR:
        w.or_mask = payload[pos : pos + w.region_size]
        pos += w.region_size
    if flags & WF_CURSOR:
        (w.cursor,) = struct.unpack_from("!I", payload, pos)
        pos += 4
    charset = "UTF-8"
    if flags & WF_CHARSET:
        length = payload[pos]
        charset = payload[pos + 1 : pos + 1 + length].decode("ascii", "replace") or "UTF-8"
        pos += 1 + length
    if raw_text is not None:
        try:
            w.text = raw_text.decode(charset, "replace")
        except LookupError:
            w.text = raw_text.decode("latin-1")
    return w


@dataclass
class _Connection:
    sock: socket.socket
    watch_id: int = 0
    buffer: bytes = b""
    state: str = "version"  # version, auth, ready
    in_tty: bool = False
    accepted: list[tuple[int, int]] = field(default_factory=list)
    serial: int = 0


class BrlapiServer:
    """Listens on 127.0.0.1:<port> and serves one virtual display."""

    def __init__(
        self,
        port: int,
        key_path: str,
        on_write: Callable[[Write], None],
        on_clients_changed: Callable[[int], None] | None = None,
    ) -> None:
        self._port = port
        self._key_path = key_path
        self._on_write = on_write
        self._on_clients_changed = on_clients_changed
        self._key = b""
        self._listener: socket.socket | None = None
        self._listen_id = 0
        self._connections: list[_Connection] = []
        self._serial = 0
        self.display_size = 0

    # Lifecycle

    def start(self) -> None:
        self._key = secrets.token_bytes(32)
        fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self._key)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self._port))
        listener.listen(4)
        listener.setblocking(False)
        self._listener = listener
        self._listen_id = GLib.io_add_watch(
            listener.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_accept
        )
        LOG.info("listening on 127.0.0.1:%d, key in %s", self._port, self._key_path)

    def stop(self) -> None:
        self.disconnect_all()
        if self._listen_id:
            GLib.source_remove(self._listen_id)
            self._listen_id = 0
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def disconnect_all(self) -> None:
        for conn in list(self._connections):
            self._close(conn)

    @property
    def has_clients(self) -> bool:
        return any(c.in_tty for c in self._connections)

    # Keys

    def send_key(self, code: int) -> bool:
        """Sends a key code to the client in tty mode that accepts it."""

        candidates = [c for c in self._connections if c.in_tty]
        for conn in sorted(candidates, key=lambda c: c.serial, reverse=True):
            if any(first <= code <= last for first, last in conn.accepted):
                self._send(conn, P_KEY, struct.pack("!II", code >> 32, code & 0xFFFFFFFF))
                return True
        LOG.debug("no client accepts key %#x", code)
        return False

    # Connections

    def _on_accept(self, _fd: int, _condition: GLib.IOCondition) -> bool:
        assert self._listener is not None
        try:
            sock, _addr = self._listener.accept()
        except BlockingIOError:
            return True
        sock.setblocking(False)
        conn = _Connection(sock)
        conn.watch_id = GLib.io_add_watch(
            sock.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
            self._on_readable,
            conn,
        )
        self._connections.append(conn)
        self._send(conn, P_VERSION, struct.pack("!I", PROTOCOL_VERSION))
        LOG.info("BrlAPI client connected")
        return True

    def _close(self, conn: _Connection) -> None:
        if conn.watch_id:
            GLib.source_remove(conn.watch_id)
            conn.watch_id = 0
        try:
            conn.sock.close()
        except OSError:
            pass
        if conn in self._connections:
            self._connections.remove(conn)
            LOG.info("BrlAPI client disconnected")
            if self._on_clients_changed is not None:
                self._on_clients_changed(len(self._connections))

    def _send(self, conn: _Connection, packet_type: int, payload: bytes = b"") -> None:
        try:
            conn.sock.setblocking(True)
            conn.sock.sendall(HEADER.pack(len(payload), packet_type) + payload)
            conn.sock.setblocking(False)
        except OSError as error:
            LOG.warning("send failed: %s", error)
            GLib.idle_add(self._close_later, conn)

    def _close_later(self, conn: _Connection) -> bool:
        self._close(conn)
        return GLib.SOURCE_REMOVE

    def _on_readable(self, _fd: int, condition: GLib.IOCondition, conn: _Connection) -> bool:
        try:
            data = conn.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:
            conn.watch_id = 0
            self._close(conn)
            return False
        conn.buffer += data
        while len(conn.buffer) >= HEADER.size:
            size, packet_type = HEADER.unpack_from(conn.buffer)
            if size > MAX_PACKET_SIZE:
                LOG.warning("packet too large (%d), closing", size)
                conn.watch_id = 0
                self._close(conn)
                return False
            if len(conn.buffer) < HEADER.size + size:
                break
            payload = conn.buffer[HEADER.size : HEADER.size + size]
            conn.buffer = conn.buffer[HEADER.size + size :]
            if not self._handle(conn, packet_type, payload):
                conn.watch_id = 0
                self._close(conn)
                return False
        return True

    # Packets

    def _handle(self, conn: _Connection, packet_type: int, payload: bytes) -> bool:
        """Returns False to close the connection."""

        if conn.state == "version":
            if (
                packet_type != P_VERSION
                or len(payload) < 4
                or struct.unpack("!I", payload[:4])[0] < 8
            ):
                self._send(conn, P_ERROR, struct.pack("!I", ERROR_INVALID_PACKET))
                return False
            conn.state = "auth"
            self._send(conn, P_AUTH, struct.pack("!I", AUTH_KEY))
            return True

        if conn.state == "auth":
            ok = (
                packet_type == P_AUTH
                and len(payload) >= 4
                and struct.unpack("!I", payload[:4])[0] == AUTH_KEY
                and hmac.compare_digest(payload[4:], self._key)
            )
            if not ok:
                LOG.warning("authorization refused")
                self._send(conn, P_ERROR, struct.pack("!I", ERROR_AUTHENTICATION))
                return False
            conn.state = "ready"
            self._send(conn, P_ACK)
            return True

        if packet_type == P_GETDISPLAYSIZE:
            self._send(conn, P_GETDISPLAYSIZE, struct.pack("!II", self.display_size, 1))
        elif packet_type == P_ENTERTTYMODE:
            conn.in_tty = True
            self._serial += 1
            conn.serial = self._serial
            self._send(conn, P_ACK)
            if self._on_clients_changed is not None:
                self._on_clients_changed(len(self._connections))
        elif packet_type == P_LEAVETTYMODE:
            conn.in_tty = False
            conn.accepted = []
            self._send(conn, P_ACK)
        elif packet_type == P_SETFOCUS:
            pass  # no reply (brlapi_client.c, brlapi__setFocus)
        elif packet_type in (P_IGNOREKEYRANGES, P_ACCEPTKEYRANGES):
            ranges = [
                (
                    struct.unpack_from("!Q", payload, i)[0],
                    struct.unpack_from("!Q", payload, i + 8)[0],
                )
                for i in range(0, len(payload) - 15, 16)
            ]
            self._update_ranges(conn, ranges, packet_type == P_ACCEPTKEYRANGES)
            self._send(conn, P_ACK)
        elif packet_type == P_WRITE:
            if not conn.in_tty:
                self._send(conn, P_ERROR, struct.pack("!I", ERROR_ILLEGAL_INSTRUCTION))
            else:
                try:
                    write = parse_write(payload, self.display_size)
                except (struct.error, IndexError):
                    self._send(conn, P_ERROR, struct.pack("!I", ERROR_INVALID_PACKET))
                    return True
                self._serial += 1
                conn.serial = self._serial
                self._on_write(write)
        elif packet_type == P_PARAM_VALUE:
            # Setting a parameter (Orca sets its client priority): accepted, ignored.
            self._send(conn, P_ACK)
        elif packet_type == P_SYNCHRONIZE:
            self._send(conn, P_ACK)
        elif packet_type == P_GETDRIVERNAME:
            self._send(conn, P_GETDRIVERNAME, DRIVER_NAME + b"\0")
        elif packet_type == P_GETMODELID:
            self._send(conn, P_GETMODELID, MODEL_ID + b"\0")
        else:
            LOG.debug("unsupported packet type %#x", packet_type)
            self._send(conn, P_ERROR, struct.pack("!I", ERROR_UNKNOWN_INSTRUCTION))
        return True

    @staticmethod
    def _update_ranges(conn: _Connection, ranges: list[tuple[int, int]], accept: bool) -> None:
        if accept:
            conn.accepted.extend(ranges)
            return
        kept: list[tuple[int, int]] = []
        for first, last in conn.accepted:
            pieces = [(first, last)]
            for ifirst, ilast in ranges:
                next_pieces = []
                for a, b in pieces:
                    if ilast < a or ifirst > b:
                        next_pieces.append((a, b))
                        continue
                    if a < ifirst:
                        next_pieces.append((a, ifirst - 1))
                    if b > ilast:
                        next_pieces.append((ilast + 1, b))
                pieces = next_pieces
            kept.extend(pieces)
        conn.accepted = kept
