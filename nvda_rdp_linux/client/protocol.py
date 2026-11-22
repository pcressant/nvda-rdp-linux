"""RDAccess ``NVDA-SPEECH`` client: the Linux counterpart of RDAccess's
``remoteSpeechHandler``.

The remote NVDA sends speech sequences, cancel/pause commands, tones and
wave files; this client renders them through Speech Dispatcher and the local
audio player, reports reached indexes, and answers the attributes the remote
synthesizer driver asks for.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..common import settings
from ..common.rdaccess import legacy_protocol_version_frame
from ..common.speech import (
    ATTRIBUTE_AVAILABLE_LANGUAGES,
    ATTRIBUTE_LANGUAGE,
    ATTRIBUTE_SUPPORTED_COMMANDS,
    CHANNEL_NAME,
    COMMAND_BREAK,
    COMMAND_CHARACTER_MODE,
    COMMAND_END_UTTERANCE,
    COMMAND_INDEX,
    COMMAND_LANG_CHANGE,
    COMMAND_PHONEME,
    COMMAND_PITCH,
    COMMAND_RATE,
    COMMAND_VOLUME,
    DEFAULT_PROSODY,
    DRIVER_TYPE,
    MESSAGE_CANCEL,
    MESSAGE_INDEX,
    MESSAGE_PAUSE_SPEECH,
    MESSAGE_SPEAK,
    MESSAGE_TONE,
    MESSAGE_WAVE,
    PROSODY_COMMANDS,
    Prosody,
    is_number,
    parse_prosody,
    prosody_command,
)
from .audio import AudioOutput
from .session import MISSING, RdAccessSession

LOG = logging.getLogger(__name__)

PROTOCOL_VERSION_2_LEGACY = legacy_protocol_version_frame(DRIVER_TYPE)

SUPPORTED_COMMANDS = tuple(
    sorted(
        (
            COMMAND_BREAK,
            COMMAND_CHARACTER_MODE,
            COMMAND_INDEX,
            COMMAND_LANG_CHANGE,
            COMMAND_PITCH,
            COMMAND_RATE,
            COMMAND_VOLUME,
        )
    )
)
MAX_BREAK_MS = 10000


class SpeechOutput(Protocol):
    def speak(
        self,
        text: str,
        *,
        character_mode: bool = False,
        language: str | None = None,
        pitch: Prosody = DEFAULT_PROSODY,
        rate: Prosody = DEFAULT_PROSODY,
        volume: Prosody = DEFAULT_PROSODY,
        on_done: Callable[[], None] | None = None,
    ) -> None: ...

    def insert_break(self, milliseconds: int) -> None: ...

    def cancel(self) -> None: ...

    def pause(self, switch: bool) -> None: ...

    def available_languages(self) -> list[str | None]: ...

    def supported_settings(self, language: str) -> list[object]: ...

    def get_setting(self, setting_id: str) -> object: ...

    def set_setting(self, setting_id: str, value: object) -> bool: ...

    def available_values(self, setting_id: str) -> dict[str, object] | None: ...


@dataclass(frozen=True)
class SpeechCommand:
    name: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class SpeechSegment:
    """One Speech Dispatcher message and the state it is spoken with.

    ``indexes`` are reported to the remote NVDA once this segment has been
    spoken; ``break_before_ms`` is silence inserted before it.
    """

    text: str
    character_mode: bool = False
    language: str | None = None
    pitch: Prosody = DEFAULT_PROSODY
    rate: Prosody = DEFAULT_PROSODY
    volume: Prosody = DEFAULT_PROSODY
    indexes: tuple[int, ...] = ()
    break_before_ms: int = 0


@dataclass(frozen=True)
class RenderedSequence:
    text: str
    commands: tuple[SpeechCommand, ...]
    segments: tuple[SpeechSegment, ...]
    unattached_indexes: tuple[int, ...] = ()


@dataclass
class _RenderState:
    character_mode: bool = False
    language: str | None = None
    prosody: dict[str, Prosody] = field(
        default_factory=lambda: dict.fromkeys(PROSODY_COMMANDS.values(), DEFAULT_PROSODY)
    )
    break_ms: int = 0


def render_sequence(sequence: object) -> RenderedSequence:
    """Split an NVDA speech sequence into segments of uniform voice state.

    Items are plain strings or ``[CommandClassName, __dict__]`` pairs, as
    produced by RDAccess's serializer.  A new segment starts whenever the
    voice state changes, at each index and at each utterance end.
    """
    if not isinstance(sequence, list):
        raise TypeError("speak.sequence must be a list")

    text_parts: list[str] = []
    commands: list[SpeechCommand] = []
    segments: list[SpeechSegment] = []
    leading_indexes: list[int] = []
    state = _RenderState()

    def flush_text() -> None:
        if text_parts:
            segments.append(
                SpeechSegment(
                    "".join(text_parts),
                    state.character_mode,
                    state.language,
                    state.prosody["pitch"],
                    state.prosody["rate"],
                    state.prosody["volume"],
                    break_before_ms=state.break_ms,
                )
            )
            text_parts.clear()
            state.break_ms = 0

    for item in sequence:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], dict)
        ):
            LOG.warning("ignoring unsupported speech sequence item: %r", item)
            continue
        command = SpeechCommand(item[0], item[1])
        commands.append(command)
        parameters = command.parameters
        if command.name == COMMAND_INDEX:
            index = parameters.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                LOG.warning("ignoring invalid IndexCommand parameters=%s", parameters)
                continue
            LOG.info("received IndexCommand parameters=%s", parameters)
            flush_text()
            if segments:
                last = segments[-1]
                segments[-1] = replace(last, indexes=(*last.indexes, index))
            else:
                leading_indexes.append(index)
        elif command.name == COMMAND_CHARACTER_MODE:
            value = parameters.get("state")
            if not isinstance(value, bool):
                LOG.warning("ignoring invalid CharacterModeCommand parameters=%s", parameters)
                continue
            flush_text()
            state.character_mode = value
            LOG.info("received CharacterModeCommand state=%s", value)
        elif command.name == COMMAND_LANG_CHANGE:
            lang = parameters.get("lang")
            if lang is not None and not isinstance(lang, str):
                LOG.warning("ignoring invalid LangChangeCommand parameters=%s", parameters)
                continue
            flush_text()
            state.language = lang or None
            LOG.info("received LangChangeCommand lang=%r", lang)
        elif command.name in PROSODY_COMMANDS:
            prosody = parse_prosody(parameters)
            if prosody is None:
                LOG.warning("ignoring invalid %s parameters=%s", command.name, parameters)
                continue
            flush_text()
            state.prosody[PROSODY_COMMANDS[command.name]] = prosody
            LOG.info("received %s %s", command.name, prosody)
        elif command.name == COMMAND_BREAK:
            time = parameters.get("time", 0)
            if not is_number(time) or time < 0:
                LOG.warning("ignoring invalid BreakCommand parameters=%s", parameters)
                continue
            flush_text()
            state.break_ms = min(MAX_BREAK_MS, state.break_ms + int(time))
        elif command.name == COMMAND_END_UTTERANCE:
            flush_text()
        elif command.name == COMMAND_PHONEME:
            text = parameters.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        else:
            LOG.warning(
                "ignoring unsupported speech command name=%s parameters=%s",
                command.name,
                parameters,
            )

    flush_text()
    if segments:
        segments[0] = replace(segments[0], text=segments[0].text.lstrip())
        segments[-1] = replace(segments[-1], text=segments[-1].text.rstrip())

    kept: list[SpeechSegment] = []
    pending = list(leading_indexes)
    for segment in segments:
        if segment.text:
            if pending:
                segment = replace(segment, indexes=(*pending, *segment.indexes))
                pending.clear()
            kept.append(segment)
        elif kept:
            kept[-1] = replace(kept[-1], indexes=(*kept[-1].indexes, *segment.indexes))
        else:
            pending.extend(segment.indexes)

    return RenderedSequence(
        "".join(segment.text for segment in kept).strip(),
        tuple(commands),
        tuple(kept),
        tuple(pending),
    )


class ProtocolSession(RdAccessSession):
    DRIVER_TYPE = DRIVER_TYPE
    CHANNEL_NAME = CHANNEL_NAME
    KIND = "speech"

    def __init__(
        self,
        output: SpeechOutput,
        *,
        send: Callable[[bytes], None] | None = None,
        language: str = "fr",
        audio: AudioOutput | None = None,
        pitch_change: int = 0,
        enable_pause: bool = False,
        settings_management: bool = False,
        request_versions: bool = False,
        on_caps_lock_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(
            send=send,
            request_versions=request_versions,
            on_caps_lock_toggle=on_caps_lock_toggle,
        )
        self._output = output
        self._language = language
        self._audio = audio
        self._pitch_change = pitch_change
        self._enable_pause = enable_pause
        self._settings_management = settings_management

    def _handle_channel_message(self, message_type: object, message: dict[str, Any]) -> bool:
        if message_type == MESSAGE_SPEAK:
            self._speak(message.get("sequence"))
        elif message_type == MESSAGE_CANCEL:
            LOG.info("cancelling local speech")
            self._output.cancel()
            self._send_reached_indexes((), done=True)
        elif message_type == MESSAGE_PAUSE_SPEECH:
            switch = message.get("switch")
            if not isinstance(switch, bool):
                LOG.error("invalid pause_speech switch=%r", switch)
            elif self._enable_pause:
                LOG.info("pausing local speech switch=%s", switch)
                self._output.pause(switch)
            else:
                LOG.info("ignoring unsupported pause_speech switch=%s", switch)
        elif message_type == MESSAGE_TONE:
            self._tone(message)
        elif message_type == MESSAGE_WAVE:
            file_name = message.get("fileName")
            if not isinstance(file_name, str):
                LOG.error("invalid wave message: %r", message)
            elif self._audio is None:
                LOG.info("ignoring wave fileName=%r without audio output", file_name)
            else:
                self._audio.wave(file_name)
        else:
            return False
        return True

    def _speak(self, sequence: object) -> None:
        if (
            isinstance(sequence, list)
            and self._pitch_change != 0
            and COMMAND_PITCH in SUPPORTED_COMMANDS
        ):
            sequence = [
                prosody_command(COMMAND_PITCH, Prosody(self._pitch_change)),
                *sequence,
                prosody_command(COMMAND_PITCH),
            ]
        try:
            rendered = render_sequence(sequence)
        except (TypeError, ValueError) as error:
            LOG.error("invalid speak message: %s", error)
            return
        if not rendered.segments:
            self._send_reached_indexes(rendered.unattached_indexes, done=True)
            return
        LOG.info("submitting speech text=%r", rendered.text)
        last = len(rendered.segments) - 1
        for position, segment in enumerate(rendered.segments):
            on_done = None
            if segment.indexes or position == last:
                on_done = self._index_reporter(segment.indexes, done=position == last)
            if segment.break_before_ms:
                self._output.insert_break(segment.break_before_ms)
            self._output.speak(
                segment.text,
                character_mode=segment.character_mode,
                language=segment.language,
                pitch=segment.pitch,
                rate=segment.rate,
                volume=segment.volume,
                on_done=on_done,
            )

    def _index_reporter(self, indexes: tuple[int, ...], *, done: bool) -> Callable[[], None]:
        def report() -> None:
            self._send_reached_indexes(indexes, done=done)

        return report

    def _tone(self, message: dict[str, Any]) -> None:
        hz = message.get("hz")
        length = message.get("length")
        left = message.get("left", 50)
        right = message.get("right", 50)
        if not all(is_number(value) for value in (hz, length, left, right)):
            LOG.error("invalid tone message: %r", message)
        elif self._audio is None:
            LOG.info("ignoring tone hz=%r length=%r without audio output", hz, length)
        else:
            self._audio.tone(hz, int(length), int(left), int(right))

    def _send_reached_indexes(self, indexes: tuple[int, ...], *, done: bool) -> None:
        """Report indexes; index 0 is RDAccess's "done speaking" sentinel."""
        for index in (*indexes, 0) if done else indexes:
            if self.send_message(MESSAGE_INDEX, index=index):
                LOG.info("sent speech index=%d", index)

    def _attribute_value(self, attribute: str) -> object:
        if attribute == ATTRIBUTE_LANGUAGE:
            return self._language
        if attribute == ATTRIBUTE_SUPPORTED_COMMANDS:
            return list(SUPPORTED_COMMANDS)
        if attribute == ATTRIBUTE_AVAILABLE_LANGUAGES:
            return self._output.available_languages()
        setting_id = settings.setting_id_from_attribute(attribute)
        if setting_id is not None:
            if not self._settings_management:
                return None
            try:
                return self._output.get_setting(setting_id)
            except KeyError:
                return MISSING
        if attribute.startswith("available") and attribute.endswith("s"):
            if not self._settings_management:
                return {}
            for setting_id in (settings.SETTING_VOICE,):
                if attribute == settings.available_values_attribute(setting_id):
                    return self._output.available_values(setting_id)
            return MISSING
        return super()._attribute_value(attribute)

    def _supported_settings(self) -> list[object]:
        if not self._settings_management:
            return []
        return self._output.supported_settings(self._language)

    def _handle_channel_attribute_value(self, attribute: str, value: object) -> bool:
        setting_id = settings.setting_id_from_attribute(attribute)
        if setting_id is None:
            return False
        if not self._settings_management:
            LOG.info("ignoring %s=%r: settings management is disabled", attribute, value)
        elif self._output.set_setting(setting_id, value):
            LOG.info("applied remote setting %s=%r", setting_id, value)
        else:
            LOG.warning("refused remote setting %s=%r", setting_id, value)
        return True
