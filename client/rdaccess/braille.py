"""Minimal RDAccess braille negotiation and display capture."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from .protocol import JsonLineDecoder

LOG = logging.getLogger(__name__)

CONNECTION_XON = b"\x11"
PROTOCOL_VERSION_2_LEGACY = bytes.fromhex(
    "42 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"
)
CHANNEL_NAME = "NVDA-BRAILLE"
MESSAGE_PROTOCOL_VERSION = "protocol_version"
MESSAGE_ATTRIBUTE_REQUEST = "attribute_request"
MESSAGE_ATTRIBUTE_VALUE = "attribute_value"
MESSAGE_DISPLAY = "display"
ATTRIBUTE_NUM_CELLS = "numCells"
ATTRIBUTE_NUM_COLS = "numCols"
ATTRIBUTE_NUM_ROWS = "numRows"
ATTRIBUTE_GESTURE_MAP = "gestureMap"


class BrailleOutput:
    num_cells: int
    num_rows: int

    def display(self, cells: list[int]) -> None: ...


class BrailleProtocolSession:
    """Negotiate JSON v2 and capture cells without a physical display."""

    def __init__(
        self,
        *,
        send: Callable[[bytes], None] | None = None,
        num_cells: int = 40,
        num_rows: int = 1,
        output: BrailleOutput | None = None,
    ) -> None:
        if output is not None:
            num_cells = output.num_cells
            num_rows = output.num_rows
        if num_cells <= 0 or num_cells > 255:
            raise ValueError("num_cells must be between 1 and 255")
        if num_rows <= 0 or num_cells % num_rows:
            raise ValueError("num_rows must divide num_cells")
        self._send = send
        self._output = output
        self._num_cells = num_cells
        self._num_rows = num_rows
        self._json = JsonLineDecoder()
        self._json_mode = False
        self._json_started = False
        self._transition_buffer = bytearray()
        self._send_lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        """Start one protocol connection over the configured byte transport."""
        if self._connected:
            raise RuntimeError("RDAccess braille session is already connected")
        self._connected = True
        if self._send_payload(CONNECTION_XON):
            LOG.info("sent braille connection XON 0x11 via transport")

    def feed(self, payload: bytes) -> None:
        if not self._json_mode:
            if payload.startswith(PROTOCOL_VERSION_2_LEGACY):
                LOG.info("received legacy braille protocolVersion=2")
                if self._send_payload(PROTOCOL_VERSION_2_LEGACY):
                    LOG.info("sent legacy braille protocolVersion=2")
                self._json_mode = True
                payload = payload[len(PROTOCOL_VERSION_2_LEGACY) :]
            elif payload.startswith(b"{"):
                LOG.warning("received braille JSON before legacy version announcement")
                self._json_mode = True
            elif payload.startswith(b"B@"):
                LOG.warning(
                    "received legacy braille attribute before version announcement; "
                    "advertising protocolVersion=2"
                )
                if self._send_payload(PROTOCOL_VERSION_2_LEGACY):
                    LOG.info("sent legacy braille protocolVersion=2")
                self._json_mode = True
            else:
                LOG.warning("ignoring pre-negotiation braille payload: %s", payload.hex(" "))
                return

        if not self._json_started:
            self._feed_transition(payload)
            return
        for message in self._json.feed(payload):
            self._handle_message(message)

    def disconnect(self) -> None:
        self._connected = False
        self._json.disconnect()
        self._json_mode = False
        self._json_started = False
        self._transition_buffer.clear()

    def _feed_transition(self, payload: bytes) -> None:
        self._transition_buffer.extend(payload)
        while self._transition_buffer:
            if self._transition_buffer.startswith(b"{"):
                self._json_started = True
                json_payload = bytes(self._transition_buffer)
                self._transition_buffer.clear()
                for message in self._json.feed(json_payload):
                    self._handle_message(message)
                return
            if len(self._transition_buffer) < 4:
                return
            if self._transition_buffer[:2] != b"B@":
                LOG.error(
                    "invalid braille transition bytes: %s",
                    self._transition_buffer.hex(" "),
                )
                self._transition_buffer.clear()
                return
            size = int.from_bytes(self._transition_buffer[2:4], "little")
            end = 4 + size
            if len(self._transition_buffer) < end:
                return
            legacy_payload = bytes(self._transition_buffer[4:end])
            del self._transition_buffer[:end]
            if legacy_payload.startswith(b"`") and legacy_payload.endswith(b"`"):
                try:
                    attribute = legacy_payload[1:-1].decode("ascii")
                except UnicodeDecodeError:
                    LOG.error("invalid legacy braille attribute: %r", legacy_payload)
                else:
                    LOG.info("received transitional legacy attribute_request(%s)", attribute)
                    self._handle_attribute_request(attribute)
            else:
                LOG.warning(
                    "ignoring transitional legacy braille payload: %s", legacy_payload.hex(" ")
                )

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        LOG.info("received RDAccess braille message type=%r", message_type)
        if message_type == MESSAGE_PROTOCOL_VERSION:
            LOG.info(
                "server braille JSON protocol version=%r channel=%r",
                message.get("version"),
                message.get("channel"),
            )
        elif message_type == MESSAGE_ATTRIBUTE_REQUEST:
            self._handle_attribute_request(message.get("attribute"))
        elif message_type == MESSAGE_DISPLAY:
            cells = message.get("cells")
            if not isinstance(cells, list) or not all(
                isinstance(cell, int) and not isinstance(cell, bool) and 0 <= cell <= 255
                for cell in cells
            ):
                LOG.error("invalid braille display cells=%r", cells)
                return
            LOG.info(
                "braille display %d cells: %s", len(cells), " ".join(f"{c:02x}" for c in cells)
            )
            if self._output is not None:
                self._output.display(cells)
        else:
            LOG.warning("ignoring unsupported RDAccess braille message: %r", message)

    def _handle_attribute_request(self, attribute: object) -> None:
        values = {
            ATTRIBUTE_NUM_CELLS: self._num_cells,
            ATTRIBUTE_NUM_COLS: self._num_cells // self._num_rows,
            ATTRIBUTE_NUM_ROWS: self._num_rows,
        }
        if isinstance(attribute, str) and attribute in values:
            value = values[attribute]
            self._send_attribute_value(attribute, value)
            LOG.info("sent braille attribute %s=%d", attribute, value)
        elif attribute == ATTRIBUTE_GESTURE_MAP:
            LOG.info("ignoring gestureMap request until braille input is implemented")
        else:
            LOG.info("server requested unsupported braille attribute=%r", attribute)

    def _send_payload(self, payload: bytes) -> bool:
        if self._send is None or not self._connected:
            return False
        with self._send_lock:
            if not self._connected:
                return False
            self._send(payload)
        return True

    def _send_attribute_value(self, attribute: str, value: object) -> bool:
        response = {
            "attribute": attribute,
            "value": value,
            "type": MESSAGE_ATTRIBUTE_VALUE,
        }
        return self._send_payload(json.dumps(response).encode("utf-8") + b"\n")
