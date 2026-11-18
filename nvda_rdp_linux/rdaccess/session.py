"""Channel-independent part of the RDAccess v2 client protocol.

The server (remote NVDA driver) and this client exchange two kinds of payload
on each DVC:

* legacy v1 frames: one driver type byte (``S`` or ``B``), one command byte,
  a 16-bit little-endian length, then the payload.  Only the attribute
  command ``@`` is understood here, and only for the ``protocolVersion``
  negotiation and for attribute requests sent during the transition.  Legacy
  payloads are pickled on the NVDA side; they are never unpickled here.
* JSON Lines: one UTF-8 JSON object per LF-terminated line with a ``type``
  field.  Everything after the negotiation uses this format.

Subclasses implement the channel-specific messages and attributes.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger(__name__)

CONNECTION_XON = b"\x11"
PROTOCOL_VERSION = 2
LEGACY_COMMAND_ATTRIBUTE = ord("@")
LEGACY_ATTRIBUTE_SEPARATOR = b"`"
LEGACY_HEADER_SIZE = 4

MESSAGE_PROTOCOL_VERSION = "protocol_version"
MESSAGE_PING = "ping"
MESSAGE_ATTRIBUTE_REQUEST = "attribute_request"
MESSAGE_ATTRIBUTE_VALUE = "attribute_value"

ATTRIBUTE_PROTOCOL_VERSION = "protocolVersion"
ATTRIBUTE_SUPPORTED_SETTINGS = "supportedSettings"
ATTRIBUTE_TIME_SINCE_INPUT = "timeSinceInput"
ATTRIBUTE_NVDA_VERSION = "nvdaVersion"
ATTRIBUTE_RDACCESS_VERSION = "rdAccessVersion"
ATTRIBUTE_CAPS_LOCK_TOGGLE = "capsLockToggle"

MISSING = object()
"""Returned by ``_attribute_value`` when this client cannot answer."""


def legacy_protocol_version_frame(driver_type: bytes) -> bytes:
    """Return the legacy ``protocolVersion=2`` announcement for one channel."""
    payload = b"`" + ATTRIBUTE_PROTOCOL_VERSION.encode("ascii") + b"`" + bytes([PROTOCOL_VERSION])
    return (
        driver_type
        + bytes([LEGACY_COMMAND_ATTRIBUTE])
        + len(payload).to_bytes(2, "little")
        + payload
    )


class JsonLineDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            try:
                end = self._buffer.index(b"\n")
            except ValueError:
                break
            line = bytes(self._buffer[:end])
            del self._buffer[: end + 1]
            message = decode_json_line(line)
            if message is not None:
                messages.append(message)
        return messages

    def disconnect(self) -> None:
        if self._buffer:
            LOG.warning(
                "discarding %d buffered RDAccess bytes on disconnect",
                len(self._buffer),
            )
        self._buffer.clear()


def decode_json_line(line: bytes) -> dict[str, Any] | None:
    if not line:
        return None
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        LOG.error("invalid RDAccess JSON line: %s; raw=%r", error, line)
        return None
    if not isinstance(value, dict):
        LOG.error("RDAccess JSON line is not an object: %r", value)
        return None
    return value


class RdAccessSession:
    """Negotiation, stream parsing and generic attributes for one DVC.

    ``send`` is called with complete DVC payloads; it may be called from the
    receive thread and from Speech Dispatcher or BrlAPI threads, so every call
    goes through one lock.
    """

    DRIVER_TYPE = b"?"
    CHANNEL_NAME = ""
    KIND = "rdaccess"

    def __init__(
        self,
        *,
        send: Callable[[bytes], None] | None = None,
        request_versions: bool = False,
        on_caps_lock_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        self._send = send
        self._request_versions = request_versions
        self._on_caps_lock_toggle = on_caps_lock_toggle
        self._buffer = bytearray()
        self._version_sent = False
        self._send_lock = threading.Lock()
        self._connected = False
        self.peer_versions: dict[str, object] = {}

    @property
    def legacy_protocol_version(self) -> bytes:
        return legacy_protocol_version_frame(self.DRIVER_TYPE)

    def connect(self) -> None:
        """Start one protocol connection over the configured byte transport."""
        if self._connected:
            raise RuntimeError(f"RDAccess {self.KIND} session is already connected")
        self._connected = True
        if self._send_payload(CONNECTION_XON):
            LOG.info("sent %s connection XON 0x11 via transport", self.KIND)

    def disconnect(self) -> None:
        self._connected = False
        if self._buffer:
            LOG.warning(
                "discarding %d buffered RDAccess %s bytes on disconnect",
                len(self._buffer),
                self.KIND,
            )
        self._buffer.clear()
        self._version_sent = False
        self.peer_versions.clear()

    def feed(self, payload: bytes) -> None:
        """Parse one DVC payload; frames and lines may be split or concatenated."""
        self._buffer.extend(payload)
        while self._buffer:
            first = self._buffer[0]
            if first == ord("\n"):
                del self._buffer[0]
            elif first == ord("{"):
                end = self._buffer.find(b"\n")
                if end < 0:
                    return
                line = bytes(self._buffer[:end])
                del self._buffer[: end + 1]
                message = decode_json_line(line)
                if message is not None:
                    self._handle_message(message)
            elif first == self.DRIVER_TYPE[0]:
                if len(self._buffer) < LEGACY_HEADER_SIZE:
                    return
                size = int.from_bytes(self._buffer[2:4], "little")
                end = LEGACY_HEADER_SIZE + size
                if len(self._buffer) < end:
                    return
                command = self._buffer[1]
                legacy_payload = bytes(self._buffer[LEGACY_HEADER_SIZE:end])
                del self._buffer[:end]
                self._handle_legacy_frame(command, legacy_payload)
            else:
                LOG.error(
                    "discarding invalid RDAccess %s bytes: %s",
                    self.KIND,
                    bytes(self._buffer).hex(" "),
                )
                self._buffer.clear()

    # Legacy negotiation

    def _handle_legacy_frame(self, command: int, payload: bytes) -> None:
        if command != LEGACY_COMMAND_ATTRIBUTE or not payload.startswith(
            LEGACY_ATTRIBUTE_SEPARATOR
        ):
            LOG.warning(
                "ignoring unsupported legacy %s frame command=%r payload=%s",
                self.KIND,
                chr(command),
                payload.hex(" "),
            )
            return
        end = payload.find(LEGACY_ATTRIBUTE_SEPARATOR, 1)
        if end < 0:
            LOG.error("invalid legacy %s attribute frame: %s", self.KIND, payload.hex(" "))
            return
        try:
            attribute = payload[1:end].decode("ascii")
        except UnicodeDecodeError:
            LOG.error("invalid legacy %s attribute name: %r", self.KIND, payload)
            return
        value = payload[end + 1 :]
        if attribute == ATTRIBUTE_PROTOCOL_VERSION and value:
            version = int.from_bytes(value, "little")
            LOG.info("received legacy %s protocolVersion=%d", self.KIND, version)
            if version >= PROTOCOL_VERSION:
                self._announce_protocol_version()
            else:
                LOG.error("RDAccess 1.x (protocol version %d) is not supported", version)
        elif not value:
            LOG.info("received legacy %s attribute_request(%s)", self.KIND, attribute)
            if not self._version_sent:
                LOG.warning(
                    "received legacy %s attribute before version announcement; "
                    "advertising protocolVersion=2",
                    self.KIND,
                )
                self._announce_protocol_version()
            self._handle_attribute_request(attribute)
        else:
            LOG.warning("ignoring legacy %s attribute value for %r", self.KIND, attribute)

    def _announce_protocol_version(self) -> None:
        if self._version_sent:
            return
        self._version_sent = True
        if self._send_payload(self.legacy_protocol_version):
            LOG.info("sent legacy %s protocolVersion=2", self.KIND)
        if self._request_versions:
            for attribute in (ATTRIBUTE_RDACCESS_VERSION, ATTRIBUTE_NVDA_VERSION):
                self.request_attribute(attribute)

    # JSON messages

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        LOG.debug("received RDAccess %s message type=%r", self.KIND, message_type)
        if message_type == MESSAGE_PROTOCOL_VERSION:
            channel = message.get("channel")
            if channel is not None and channel != self.CHANNEL_NAME:
                LOG.error("protocol_version for unexpected channel %r", channel)
                return
            LOG.info(
                "server %s JSON protocol version=%r channel=%r",
                self.KIND,
                message.get("version"),
                channel,
            )
        elif message_type == MESSAGE_PING:
            LOG.debug("received %s ping", self.KIND)
        elif message_type == MESSAGE_ATTRIBUTE_REQUEST:
            self._handle_attribute_request(message.get("attribute"))
        elif message_type == MESSAGE_ATTRIBUTE_VALUE:
            attribute = message.get("attribute")
            if not isinstance(attribute, str):
                LOG.error("invalid %s attribute_value: %r", self.KIND, message)
                return
            self._handle_attribute_value(attribute, message.get("value"))
        elif not self._handle_channel_message(message_type, message):
            LOG.warning("ignoring unsupported RDAccess %s message: %r", self.KIND, message)

    def _handle_channel_message(self, _message_type: object, _message: dict[str, Any]) -> bool:
        """Handle one channel-specific message; return False when unknown."""
        return False

    # Attributes

    def _handle_attribute_request(self, attribute: object) -> None:
        if not isinstance(attribute, str):
            LOG.error("invalid %s attribute request: %r", self.KIND, attribute)
            return
        value = self._attribute_value(attribute)
        if value is MISSING:
            LOG.info("server requested unsupported %s attribute=%r", self.KIND, attribute)
            return
        if self.send_attribute_value(attribute, value):
            LOG.info("sent %s attribute %s=%r", self.KIND, attribute, value)

    def _attribute_value(self, attribute: str) -> object:
        """Return the value answered to an attribute request, or ``MISSING``."""
        if attribute == ATTRIBUTE_SUPPORTED_SETTINGS:
            return self._supported_settings()
        return MISSING

    def _supported_settings(self) -> list[object]:
        return []

    def _handle_attribute_value(self, attribute: str, value: object) -> None:
        if attribute in (ATTRIBUTE_RDACCESS_VERSION, ATTRIBUTE_NVDA_VERSION):
            self.peer_versions[attribute] = value
            LOG.info("server %s %s=%r", self.KIND, attribute, value)
        elif attribute == ATTRIBUTE_PROTOCOL_VERSION:
            LOG.info("server %s protocolVersion attribute=%r", self.KIND, value)
        elif attribute == ATTRIBUTE_TIME_SINCE_INPUT:
            LOG.debug("server %s timeSinceInput=%r", self.KIND, value)
        elif attribute == ATTRIBUTE_CAPS_LOCK_TOGGLE:
            if not isinstance(value, bool):
                LOG.error("invalid capsLockToggle value=%r", value)
                return
            LOG.info("server %s capsLockToggle=%s", self.KIND, value)
            if self._on_caps_lock_toggle is not None:
                self._on_caps_lock_toggle(value)
        elif not self._handle_channel_attribute_value(attribute, value):
            LOG.info("ignoring unsupported %s attribute value %s=%r", self.KIND, attribute, value)

    def _handle_channel_attribute_value(self, _attribute: str, _value: object) -> bool:
        return False

    # Sending

    def send_message(self, message_type: str, **fields: object) -> bool:
        fields["type"] = message_type
        return self._send_payload(json.dumps(fields).encode("utf-8") + b"\n")

    def send_attribute_value(self, attribute: str, value: object) -> bool:
        return self.send_message(MESSAGE_ATTRIBUTE_VALUE, attribute=attribute, value=value)

    def request_attribute(self, attribute: str) -> bool:
        sent = self.send_message(MESSAGE_ATTRIBUTE_REQUEST, attribute=attribute)
        if sent:
            LOG.info("requested %s attribute %s", self.KIND, attribute)
        return sent

    def _send_payload(self, payload: bytes) -> bool:
        if self._send is None or not self._connected:
            LOG.debug("no transport configured for %d outbound bytes", len(payload))
            return False
        with self._send_lock:
            if not self._connected:
                return False
            self._send(payload)
        LOG.debug("sent %d %s DVC bytes via IPC: %s", len(payload), self.KIND, payload.hex(" "))
        return True
