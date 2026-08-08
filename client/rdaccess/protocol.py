"""Small RDAccess subset validated against the pinned development commit."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

LOG = logging.getLogger(__name__)

CONNECTION_XON = b"\x11"
PROTOCOL_VERSION_2_LEGACY = bytes.fromhex(
    "53 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"
)

MESSAGE_PROTOCOL_VERSION = "protocol_version"
MESSAGE_ATTRIBUTE_REQUEST = "attribute_request"
MESSAGE_ATTRIBUTE_VALUE = "attribute_value"
MESSAGE_SPEAK = "speak"
MESSAGE_CANCEL = "cancel"
MESSAGE_PAUSE_SPEECH = "pause_speech"
COMMAND_INDEX = "IndexCommand"
COMMAND_CHARACTER_MODE = "CharacterModeCommand"
ATTRIBUTE_LANGUAGE = "language"
ATTRIBUTE_SUPPORTED_COMMANDS = "supportedCommands"
SUPPORTED_COMMANDS = (COMMAND_CHARACTER_MODE, COMMAND_INDEX)


class SpeechOutput(Protocol):
    def speak(
        self,
        text: str,
        *,
        character_mode: bool = False,
        on_done: Callable[[], None] | None = None,
    ) -> None: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True)
class SpeechCommand:
    name: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class SpeechSegment:
    text: str
    character_mode: bool


@dataclass(frozen=True)
class RenderedSequence:
    text: str
    commands: tuple[SpeechCommand, ...]
    segments: tuple[SpeechSegment, ...]


def render_sequence(sequence: object) -> RenderedSequence:
    if not isinstance(sequence, list):
        raise TypeError("speak.sequence must be a list")

    text_parts: list[str] = []
    commands: list[SpeechCommand] = []
    segments: list[SpeechSegment] = []
    character_mode = False

    def flush_text() -> None:
        if text_parts:
            segments.append(SpeechSegment("".join(text_parts), character_mode))
            text_parts.clear()

    for item in sequence:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], dict)
        ):
            command = SpeechCommand(item[0], item[1])
            commands.append(command)
            if command.name == COMMAND_INDEX:
                LOG.info("received IndexCommand parameters=%s", command.parameters)
            elif command.name == COMMAND_CHARACTER_MODE:
                state = command.parameters.get("state")
                if isinstance(state, bool):
                    flush_text()
                    character_mode = state
                    LOG.info("received CharacterModeCommand state=%s", state)
                else:
                    LOG.warning(
                        "ignoring invalid CharacterModeCommand parameters=%s",
                        command.parameters,
                    )
            else:
                LOG.warning(
                    "ignoring unsupported speech command name=%s parameters=%s",
                    command.name,
                    command.parameters,
                )
            continue
        LOG.warning("ignoring unsupported speech sequence item: %r", item)

    flush_text()
    text = "".join(segment.text for segment in segments).strip()
    if segments:
        segments[0] = SpeechSegment(segments[0].text.lstrip(), segments[0].character_mode)
        segments[-1] = SpeechSegment(segments[-1].text.rstrip(), segments[-1].character_mode)
    return RenderedSequence(
        text,
        tuple(commands),
        tuple(segment for segment in segments if segment.text),
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
            if not line:
                continue
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                LOG.error("invalid RDAccess JSON line: %s; raw=%r", error, line)
                continue
            if not isinstance(value, dict):
                LOG.error("RDAccess JSON line is not an object: %r", value)
                continue
            messages.append(value)
        return messages

    def disconnect(self) -> None:
        if self._buffer:
            LOG.warning(
                "discarding %d buffered RDAccess bytes on disconnect",
                len(self._buffer),
            )
        self._buffer.clear()


class ProtocolSession:
    def __init__(
        self,
        output: SpeechOutput,
        *,
        send: Callable[[bytes], None] | None = None,
        language: str = "fr",
    ) -> None:
        self._output = output
        self._send = send
        self._language = language
        self._json = JsonLineDecoder()
        self._json_mode = False
        self._send_lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        """Start one protocol connection over the configured byte transport."""
        if self._connected:
            raise RuntimeError("RDAccess speech session is already connected")
        self._connected = True
        if self._send_payload(CONNECTION_XON):
            LOG.info("sent speech connection XON 0x11 via transport")

    def feed(self, payload: bytes) -> None:
        if not self._json_mode:
            if payload.startswith(PROTOCOL_VERSION_2_LEGACY):
                LOG.info("received legacy server protocolVersion=2")
                if self._send_payload(PROTOCOL_VERSION_2_LEGACY):
                    LOG.info("sent legacy client protocolVersion=2")
                self._json_mode = True
                payload = payload[len(PROTOCOL_VERSION_2_LEGACY) :]
            elif payload.startswith(b"{"):
                LOG.warning("received JSON before the legacy version announcement")
                self._json_mode = True
            else:
                LOG.warning("ignoring pre-negotiation DVC payload: %s", payload.hex(" "))
                return

        for message in self._json.feed(payload):
            self._handle_message(message)

    def disconnect(self) -> None:
        self._connected = False
        self._json.disconnect()
        self._json_mode = False

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        LOG.info("received RDAccess message type=%r", message_type)
        if message_type == MESSAGE_SPEAK:
            try:
                rendered = render_sequence(message.get("sequence"))
            except (TypeError, ValueError) as error:
                LOG.error("invalid speak message: %s", error)
                return
            if rendered.segments:
                LOG.info("submitting speech text=%r", rendered.text)
                indexes = tuple(
                    command.parameters.get("index")
                    for command in rendered.commands
                    if command.name == COMMAND_INDEX
                    and isinstance(command.parameters.get("index"), int)
                    and not isinstance(command.parameters.get("index"), bool)
                )
                for position, segment in enumerate(rendered.segments):
                    self._output.speak(
                        segment.text,
                        character_mode=segment.character_mode,
                        on_done=(lambda indexes=indexes: self._send_reached_indexes(indexes))
                        if position == len(rendered.segments) - 1
                        else None,
                    )
        elif message_type == MESSAGE_CANCEL:
            LOG.info("cancelling local speech")
            self._output.cancel()
        elif message_type == MESSAGE_PAUSE_SPEECH:
            switch = message.get("switch")
            if not isinstance(switch, bool):
                LOG.error("invalid pause_speech switch=%r", switch)
                return
            LOG.info("ignoring unsupported pause_speech switch=%s", switch)
        elif message_type == MESSAGE_PROTOCOL_VERSION:
            LOG.info(
                "server JSON protocol version=%r channel=%r",
                message.get("version"),
                message.get("channel"),
            )
        elif message_type == MESSAGE_ATTRIBUTE_REQUEST:
            attribute = message.get("attribute")
            if attribute == ATTRIBUTE_LANGUAGE:
                if self._send_attribute_value(attribute, self._language):
                    LOG.info("sent language attribute value=%r", self._language)
            elif attribute == ATTRIBUTE_SUPPORTED_COMMANDS:
                if self._send_attribute_value(attribute, list(SUPPORTED_COMMANDS)):
                    LOG.info("sent supported speech commands=%r", SUPPORTED_COMMANDS)
            else:
                LOG.info("server requested unsupported attribute=%r", attribute)
        else:
            LOG.warning("ignoring unsupported RDAccess message: %r", message)

    def _send_payload(self, payload: bytes) -> bool:
        if self._send is None or not self._connected:
            LOG.debug("no transport configured for %d outbound bytes", len(payload))
            return False
        with self._send_lock:
            if not self._connected:
                return False
            self._send(payload)
        LOG.debug("sent %d DVC bytes via IPC: %s", len(payload), payload.hex(" "))
        return True

    def _send_reached_indexes(self, indexes: tuple[int, ...]) -> None:
        for index in (*indexes, 0):
            encoded = json.dumps({"index": index, "type": "index"}).encode("utf-8") + b"\n"
            if self._send_payload(encoded):
                LOG.info("sent speech index=%d", index)

    def _send_attribute_value(self, attribute: str, value: object) -> bool:
        response = {
            "attribute": attribute,
            "value": value,
            "type": MESSAGE_ATTRIBUTE_VALUE,
        }
        return self._send_payload(json.dumps(response).encode("utf-8") + b"\n")
