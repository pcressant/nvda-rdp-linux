"""Client role of one RDAccess v2 channel: the part both channels share.

The wire format itself is in ``common.rdaccess``.  This class negotiates the
protocol version, answers the generic attributes and serializes every send;
subclasses implement the channel-specific messages and attributes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ..common.rdaccess import (
    ATTRIBUTE_CAPS_LOCK_TOGGLE,
    ATTRIBUTE_NVDA_VERSION,
    ATTRIBUTE_PROTOCOL_VERSION,
    ATTRIBUTE_RDACCESS_VERSION,
    ATTRIBUTE_SUPPORTED_SETTINGS,
    ATTRIBUTE_TIME_SINCE_INPUT,
    CONNECTION_XON,
    LEGACY_ATTRIBUTE_SEPARATOR,
    LEGACY_COMMAND_ATTRIBUTE,
    MESSAGE_ATTRIBUTE_REQUEST,
    MESSAGE_ATTRIBUTE_VALUE,
    MESSAGE_PING,
    MESSAGE_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    InvalidBytes,
    InvalidLegacyAttribute,
    LegacyFrame,
    StreamDecoder,
    encode_json_line,
    legacy_protocol_version_frame,
    parse_legacy_attribute,
)

LOG = logging.getLogger(__name__)

MISSING = object()
"""Returned by ``_attribute_value`` when this client cannot answer."""


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
        self._stream = StreamDecoder(self.DRIVER_TYPE)
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
        if self._stream:
            LOG.warning(
                "discarding %d buffered RDAccess %s bytes on disconnect",
                len(self._stream),
                self.KIND,
            )
        self._stream.clear()
        self._version_sent = False
        self.peer_versions.clear()

    def feed(self, payload: bytes) -> None:
        """Parse one DVC payload; frames and lines may be split or concatenated."""
        self._stream.feed(payload)
        while (item := self._stream.pop()) is not None:
            if isinstance(item, LegacyFrame):
                self._handle_legacy_frame(item.command, item.payload)
            elif isinstance(item, InvalidBytes):
                LOG.error(
                    "discarding invalid RDAccess %s bytes: %s",
                    self.KIND,
                    item.data.hex(" "),
                )
            else:
                self._handle_message(item)

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
        try:
            attribute, value = parse_legacy_attribute(payload)
        except InvalidLegacyAttribute as error:
            LOG.error(
                "invalid legacy %s attribute frame (%s): %s", self.KIND, error, payload.hex(" ")
            )
            return
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
        return self._send_payload(encode_json_line(message_type, **fields))

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
