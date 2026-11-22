"""RDAccess v2 wire format, the same in both directions of a channel.

Each RDAccess channel (``NVDA-SPEECH``, ``NVDA-BRAILLE``) carries a byte
stream that mixes:

* legacy v1 frames: one driver type byte (``S`` or ``B``), one command byte,
  a 16-bit little-endian length, then the payload.  Only the attribute
  command ``@`` matters for v2: the ``protocolVersion`` negotiation and the
  attribute requests sent during the transition.  Legacy values are pickled
  on the NVDA side; they are never unpickled here.
* JSON Lines: one UTF-8 JSON object per LF-terminated line with a ``type``
  field.  Everything after the negotiation uses this format.

Frames and lines may be split or concatenated in any way by the transport.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)

CONNECTION_XON = b"\x11"
"""Sent by the client when it attaches to a channel."""
CONNECTION_XOFF = b"\x13"
"""Sent by the client when it detaches from a channel."""
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


# Legacy attribute frames


def encode_legacy_attribute(driver_type: bytes, attribute: str, value: bytes = b"") -> bytes:
    """Encode a legacy ``@`` frame; an empty value is an attribute request."""
    payload = (
        LEGACY_ATTRIBUTE_SEPARATOR + attribute.encode("ascii") + LEGACY_ATTRIBUTE_SEPARATOR + value
    )
    return (
        driver_type
        + bytes([LEGACY_COMMAND_ATTRIBUTE])
        + len(payload).to_bytes(2, "little")
        + payload
    )


def legacy_protocol_version_frame(driver_type: bytes) -> bytes:
    """Return the legacy ``protocolVersion=2`` announcement for one channel."""
    return encode_legacy_attribute(
        driver_type, ATTRIBUTE_PROTOCOL_VERSION, bytes([PROTOCOL_VERSION])
    )


class InvalidLegacyAttribute(ValueError):
    """A legacy ``@`` payload that is not `` `name`value``."""


def parse_legacy_attribute(payload: bytes) -> tuple[str, bytes]:
    """Split a legacy ``@`` payload into its attribute name and raw value."""
    if not payload.startswith(LEGACY_ATTRIBUTE_SEPARATOR):
        raise InvalidLegacyAttribute("no attribute name")
    end = payload.find(LEGACY_ATTRIBUTE_SEPARATOR, 1)
    if end < 0:
        raise InvalidLegacyAttribute("unterminated attribute name")
    try:
        attribute = payload[1:end].decode("ascii")
    except UnicodeDecodeError:
        raise InvalidLegacyAttribute("non-ASCII attribute name") from None
    return attribute, payload[end + 1 :]


# JSON Lines


def encode_json_line(message_type: str, **fields: object) -> bytes:
    """Encode one JSON message; ``type`` is the last field, as RDAccess sends it."""
    fields["type"] = message_type
    return json.dumps(fields).encode("utf-8") + b"\n"


def decode_json_line(line: bytes) -> dict[str, Any] | None:
    """Decode one line without its LF; invalid lines are logged and dropped."""
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


class JsonLineDecoder:
    """Decode a stream made of JSON Lines only."""

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


# Mixed stream


@dataclass(frozen=True)
class LegacyFrame:
    command: int
    payload: bytes


@dataclass(frozen=True)
class ConnectionByte:
    """``CONNECTION_XON`` or ``CONNECTION_XOFF``, seen by the server role."""

    value: bytes


@dataclass(frozen=True)
class InvalidBytes:
    """Bytes that start neither a JSON line nor a legacy frame of this channel.

    The rest of the buffered stream is dropped with them: there is no way to
    find the next message boundary.
    """

    data: bytes


StreamItem = dict[str, Any] | LegacyFrame | ConnectionByte | InvalidBytes


class StreamDecoder:
    """Decode the mixed legacy/JSON stream of one channel.

    ``pop`` returns one item at a time, so that a caller may ``clear`` the
    decoder while handling an item and drop what follows it.

    ``connection_bytes`` are recognised where an item may start: the server
    role passes ``CONNECTION_XON + CONNECTION_XOFF``, the bytes only the
    client sends.
    """

    def __init__(self, driver_type: bytes, connection_bytes: bytes = b"") -> None:
        self._driver_type = driver_type[0]
        self._connection_bytes = connection_bytes
        self._buffer = bytearray()

    def __len__(self) -> int:
        """The number of buffered bytes not decoded yet."""
        return len(self._buffer)

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    def clear(self) -> None:
        self._buffer.clear()

    def pop(self) -> StreamItem | None:
        """Return the next complete item, or None until more bytes are fed."""
        while self._buffer:
            first = self._buffer[0]
            if first == ord("\n"):
                del self._buffer[0]
            elif first in self._connection_bytes:
                del self._buffer[0]
                return ConnectionByte(bytes([first]))
            elif first == ord("{"):
                end = self._buffer.find(b"\n")
                if end < 0:
                    return None
                line = bytes(self._buffer[:end])
                del self._buffer[: end + 1]
                message = decode_json_line(line)
                if message is not None:
                    return message
            elif first == self._driver_type:
                if len(self._buffer) < LEGACY_HEADER_SIZE:
                    return None
                size = int.from_bytes(self._buffer[2:4], "little")
                end = LEGACY_HEADER_SIZE + size
                if len(self._buffer) < end:
                    return None
                frame = LegacyFrame(self._buffer[1], bytes(self._buffer[LEGACY_HEADER_SIZE:end]))
                del self._buffer[:end]
                return frame
            else:
                invalid = InvalidBytes(bytes(self._buffer))
                self._buffer.clear()
                return invalid
        return None
