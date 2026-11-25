# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiments 07 and 13 (rdaccess_speechd/module.py).
# Module protocol from speechd@af73916: src/modules/module_main.c:58-80,
# src/modules/module_process.c:181-330, 385-450, 539-625. No code copied.

"""A Speech Dispatcher output module, run by a Speech Dispatcher instance
dedicated to the remote session.

Speech Dispatcher keeps queueing, priorities and its clients (Orca); this
module speaks one message at a time and reports its events:

- NVDA attached to the ``NVDA-SPEECH`` channel: the SSML text becomes an
  RDAccess speak sequence; each ``<mark/>`` becomes an ``IndexCommand``, plus
  one index before the text and one after it.  NVDA reports every index when
  its synthesizer reaches it (rdAccess@19f5c53: addon/lib/protocol/speech.py:71-84,
  handlers/remoteSpeechHandler.py:81-94), and the module turns them into
  701 BEGIN, 700 INDEX MARK and 702 END.  Index 0, sent at the end of every
  sequence and on cancel, is ignored.
- NVDA not attached: the message goes to the session's usual Speech
  Dispatcher, as a client, with the same settings; its events are passed on.

If NVDA does not report the index placed before the text within 2 s (the
client machine idle or locked), the message is ended so that Speech
Dispatcher does not wait for ever.

PAUSE stops at the next of the ``__spd_`` marks that Speech Dispatcher puts
between sentences, as it asks of its modules: on resume, it sends the text
again from the last mark reported (speechd@0d80cd8:
doc/speech-dispatcher.texi, "module_pause"; src/server/speaking.c,
reload_message).  NVDA is told to cancel once that mark is reached, or at
once when no such mark is left or none comes within 1.5 s.  RDAccess's
``pause_speech`` is not used: NVDA would then also resume the text that
Speech Dispatcher sends again.

NVDA plays only tones and its own sound files (RDAccess's ``tone`` and
``wave``, rdAccess@2.1.0: addon/globalPlugins/rdAccess/handlers/
remoteSpeechHandler.py), and drops the beep and wave commands of a speech
sequence.  A sound icon (SOUND_ICON) named like one of NVDA's sounds plays
it, ``capital`` and the other names play a tone.  With Orca's capitalization
style "icon" (``cap_let_recogn=icon``), a capital letter spoken alone is
preceded by NVDA's tone for capitals once NVDA begins it.  Orca's own sounds
are played in the session and heard through RDP's audio.

Every message sent to NVDA starts with the per-utterance commands of
``prosody`` (Orca's voice changes relative to its default voice, language)
and ends with commands back to NVDA's defaults; ``NVDA_RDP_PROSODY=0`` in the
module's environment turns this off.

With driver settings management allowed in RDAccess on the client
(``driver_settings``), the voices of NVDA's synthesizer are the module's
voices: Orca lists them, and choosing one changes NVDA's voice.  The voice
is only sent when it differs from NVDA's, so that Speech Dispatcher's
repeated settings do not reload it.
"""

from __future__ import annotations

import html
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import speechd
from gi.repository import GLib

from ..common.rdaccess import (
    ATTRIBUTE_TIME_SINCE_INPUT,
    MESSAGE_ATTRIBUTE_REQUEST,
    MESSAGE_ATTRIBUTE_VALUE,
)
from ..common.speech import (
    ATTRIBUTE_LANGUAGE,
    ATTRIBUTE_SUPPORTED_COMMANDS,
    CHANNEL_NAME,
    COMMAND_INDEX,
    DRIVER_TYPE,
    MESSAGE_CANCEL,
    MESSAGE_INDEX,
    MESSAGE_SPEAK,
    MESSAGE_TONE,
    MESSAGE_WAVE,
)
from .channel import Channel
from .driver_settings import Choice, DriverSettings
from .input_time import InputTime
from .orca_speech import OrcaSpeech
from .prosody import NEUTRAL, prosody_commands, reset_commands
from .settings_service import SettingsService

LOG = logging.getLogger(__name__)

SOCKET_NAME = "nvda-speech.sock"
PROSODY_ENV = "NVDA_RDP_PROSODY"
_MARK_OR_TAG = re.compile(r"<mark\s+name\s*=\s*\"([^\"]*)\"\s*/>|<[^>]*>")
_FIRST_INDEX = 1
_MAX_INDEX = 1 << 30
# If NVDA does not report the index placed before the text within this delay,
# the message is ended so that Speech Dispatcher does not wait for ever.
_BEGIN_TIMEOUT_MS = 2000
# A pause waits this long for the next mark, then stops where NVDA is.
_PAUSE_TIMEOUT_MS = 1500
_PAUSE_MARK_PREFIX = "__spd_"
# NVDA's sounds (source/waves), played by their path relative to NVDA's directory.
NVDA_WAVES = (
    "browseMode",
    "clipboardPush",
    "clipboardReceive",
    "connected",
    "controlled",
    "controlling",
    "disconnected",
    "error",
    "exit",
    "focusMode",
    "screenCurtainOff",
    "screenCurtainOn",
    "start",
    "suggestionsClosed",
    "suggestionsOpened",
    "textError",
)
# NVDA's beep for capitals (speech/speech.py: BeepCommand(2000, 50)), and a short
# tone for the sound icons NVDA has no sound for.
CAPITAL_TONE = {"hz": 2000, "length": 50, "left": 50, "right": 50}
ICON_TONE = {"hz": 1000, "length": 40, "left": 50, "right": 50}
CAPITAL_ICON = "capital"
VOICE_SETTING = "voice"
GENERIC_VOICE = "NVDA"


def parse_ssml(text: str) -> list[tuple[str, str]]:
    """Return ``("text", s)`` and ``("mark", name)`` items; other tags are dropped."""
    items: list[tuple[str, str]] = []
    pos = 0
    for match in _MARK_OR_TAG.finditer(text):
        if match.start() > pos:
            items.append(("text", html.unescape(text[pos : match.start()])))
        if match.group(1) is not None:
            items.append(("mark", match.group(1)))
        pos = match.end()
    if pos < len(text):
        items.append(("text", html.unescape(text[pos:])))
    return items


def speechd_language(language: str | None) -> str:
    """NVDA's ``fr_FR`` as Speech Dispatcher's ``fr-FR``; the locale's language without one."""
    if not language:
        return (os.environ.get("LANG") or "en").split("_")[0].split(".")[0]
    return language.replace("_", "-")


def voice_names(choices: list[Choice]) -> dict[str, Choice]:
    """NVDA's voices by the name Speech Dispatcher and Orca show: unique, on one field."""
    names: dict[str, Choice] = {}
    for choice in choices:
        name = " ".join(choice.name.split()) or choice.id
        if name in names or name == GENERIC_VOICE:
            name = f"{name} ({choice.id})"
        names[name] = choice
    return names


def sound_icon(name: str) -> tuple[str, dict[str, object]]:
    """The RDAccess message that plays the sound icon ``name`` on NVDA."""
    key = name.strip().removesuffix(".wav").casefold()
    for wave in NVDA_WAVES:
        if key == wave.casefold():
            return MESSAGE_WAVE, {"fileName": f"waves\\{wave}.wav"}
    if key == CAPITAL_ICON:
        return MESSAGE_TONE, dict(CAPITAL_TONE)
    return MESSAGE_TONE, dict(ICON_TONE)


def _index(index: int) -> list[object]:
    return [COMMAND_INDEX, {"index": index}]


@dataclass
class _Message:
    via: str  # "nvda" or "local"
    begin: int = 0
    end: int = 0
    marks: dict[int, str] = field(default_factory=dict)
    began: bool = False
    marks_reached: int = 0
    pausing: bool = False
    capital: bool = False

    def pause_mark_ahead(self) -> bool:
        """Whether a mark Speech Dispatcher can resume from is still to come."""
        ahead = list(self.marks.values())[self.marks_reached :]
        return any(name.startswith(_PAUSE_MARK_PREFIX) for name in ahead)


class Module:
    def __init__(self) -> None:
        self.channel = Channel(
            CHANNEL_NAME, DRIVER_TYPE, SOCKET_NAME, self._on_channel_message, self._on_ready
        )
        self._prosody_enabled = os.environ.get(PROSODY_ENV) != "0"
        self._buffer = b""
        self._collect: str | None = None  # SPEAK, CHAR, KEY, SOUND_ICON, SET, AUDIO, LOGLEVEL
        self._lines: list[str] = []
        self._settings: dict[str, str] = {}
        self._applied: dict[str, str] = {}
        self._next_index = _FIRST_INDEX
        self._current: _Message | None = None
        self._local: Any = None
        self._base = dict(NEUTRAL)
        self.orca = OrcaSpeech()
        self._input_time = InputTime()
        self.driver_settings = DriverSettings(self.channel)
        self._settings_service = SettingsService("speech", self.driver_settings)
        self._voice_name: str | None = None
        self._listed_voices: set[str] = set()
        self._loop = GLib.MainLoop()

    # Main loop

    def run(self) -> None:
        os.set_blocking(0, False)
        GLib.io_add_watch(
            0, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_stdin
        )
        self.channel.start()
        self.orca.start()
        self._settings_service.start()
        try:
            self._loop.run()
        finally:
            self._settings_service.stop()
            self.channel.stop()
            if self._local is not None:
                self._local.close()

    @property
    def loop(self) -> GLib.MainLoop:
        return self._loop

    def _out(self, text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    # Commands from Speech Dispatcher

    def _on_stdin(self, _fd: int, _condition: GLib.IOCondition) -> bool:
        try:
            data = os.read(0, 65536)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        if not data:
            LOG.info("Speech Dispatcher closed the module's input, exiting")
            self._loop.quit()
            return GLib.SOURCE_REMOVE
        self._buffer += data
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            self._on_line(raw.decode("utf-8", "replace"))
        return GLib.SOURCE_CONTINUE

    def _on_line(self, line: str) -> None:
        if self._collect is not None:
            if line != ".":
                self._lines.append(line.removeprefix("."))
                return
            kind, lines = self._collect, self._lines
            self._collect, self._lines = None, []
            self._end_block(kind, lines)
            return
        if line == "INIT":
            self._out("299-nvda-rdp: speech through NVDA over RDP\n299 OK LOADED SUCCESSFULLY")
        elif line in ("SPEAK", "CHAR", "KEY", "SOUND_ICON"):
            self._collect = line
            self._out("202 OK RECEIVING MESSAGE")
        elif line == "SET":
            self._collect = line
            self._out("203 OK RECEIVING SETTINGS")
        elif line in ("AUDIO", "LOGLEVEL"):
            self._collect = line
            self._out(f"207 OK RECEIVING {line} SETTINGS")
        elif line == "STOP":
            self._stop(703, "STOP")
        elif line == "PAUSE":
            self._pause()
        elif line.startswith("LIST VOICES"):
            self._list_voices()
        elif line.startswith("DEBUG"):
            self._out("200 OK DEBUGGING")
        elif line == "QUIT":
            self._out("210 OK QUIT")
            self._loop.quit()
        else:
            LOG.warning("unknown module command %r", line)
            self._out("300 ERR UNKNOWN COMMAND")

    def _end_block(self, kind: str, lines: list[str]) -> None:
        if kind == "SET":
            for item in lines:
                name, _, value = item.partition("=")
                self._settings[name] = value
            self._out("203 OK SETTINGS RECEIVED")
            self._select_voice(self._settings.get("synthesis_voice"))
        elif kind == "AUDIO":
            self._out("203 OK AUDIO INITIALIZED")
        elif kind == "LOGLEVEL":
            self._out("203 OK LOGLEVEL SET")
        else:
            text = "\n".join(lines)
            if kind in ("CHAR", "KEY") and text == "space":
                text = " "
            if not text:
                self._out("301 ERROR CANT SPEAK")
                return
            self._out("200 OK SPEAKING")
            self._speak(kind, text)

    # NVDA's voices

    def _nvda_voices(self) -> dict[str, Choice]:
        if not self.channel.ready or self.driver_settings.setting(VOICE_SETTING) is None:
            return {}
        return voice_names(self.driver_settings.choices(VOICE_SETTING) or [])

    def _list_voices(self) -> None:
        voices = self._nvda_voices()
        if voices:
            self._listed_voices = set(voices)
            lines = [
                f"200-{name}\t{speechd_language(choice.language)}\tnone"
                for name, choice in voices.items()
            ]
        else:
            lines = [f"200-{GENERIC_VOICE}\t{speechd_language(None)}\tnone"]
        self._out("\n".join([*lines, "200 OK VOICE LIST SENT"]))

    def _select_voice(self, name: str | None) -> None:
        """Orca chose a voice: NVDA's voice of that name, if it is not NVDA's already."""
        if name == self._voice_name:
            return
        choice = self._nvda_voices().get(name or "")
        if choice is None:
            return  # Not one of NVDA's voices, or they are not known yet: tried again.
        self._voice_name = name
        if self.driver_settings.value(VOICE_SETTING) == choice.id:
            return
        LOG.info("Orca chose NVDA's voice %s (%s)", name, choice.id)
        if self.driver_settings.set(VOICE_SETTING, choice.id):
            # NVDA's language follows its voice.
            self.channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=ATTRIBUTE_LANGUAGE)

    # Per-utterance prosody and language

    def _prosody(self) -> tuple[list[list[object]], list[list[object]]]:
        if not self._prosody_enabled:
            return [], []
        language = self.channel.attributes.get(ATTRIBUTE_LANGUAGE)
        if not isinstance(language, str) or not language:
            language = (os.environ.get("LANG") or "").split(".")[0] or None
        supported = self.channel.attributes.get(ATTRIBUTE_SUPPORTED_COMMANDS)
        supported_set = set(supported) if isinstance(supported, list) else None
        commands = prosody_commands(self._settings, self._base, language, supported_set)
        if commands:
            LOG.debug("prosody %s", commands)
        return commands, reset_commands(commands)

    def _on_baseline(self, base: dict[str, int] | None) -> None:
        if base is not None:
            self._base = base
        LOG.info(
            "prosody baseline %s (%s)",
            self._base,
            "Orca's default voice" if base else "Orca's defaults",
        )

    # Speaking

    def _speak(self, kind: str, text: str) -> None:
        if kind == "SOUND_ICON" and self.channel.ready:
            # Speech Dispatcher sends it once the previous message has ended.
            message_type, fields = sound_icon(text)
            LOG.debug("sound icon %r: %s %s", text, message_type, fields)
            self.channel.send(message_type, **fields)
            self._out("701 BEGIN")
            self._out("702 END")
            return
        if self.channel.ready:
            self._speak_nvda(kind, text)
        else:
            self._speak_local(kind, text)

    def _new_index(self) -> int:
        index = self._next_index
        self._next_index = index + 1 if index < _MAX_INDEX else _FIRST_INDEX
        return index

    def _speak_nvda(self, kind: str, text: str) -> None:
        message = _Message("nvda", begin=self._new_index())
        before, after = self._prosody()
        sequence: list[object] = [_index(message.begin), *before]
        items = parse_ssml(text) if kind == "SPEAK" else [("text", text)]
        for item_kind, value in items:
            if item_kind == "text":
                if value:
                    sequence.append(value)
            else:
                index = self._new_index()
                message.marks[index] = value
                sequence.append(_index(index))
        message.capital = self._capital_icon(kind, items)
        message.end = self._new_index()
        sequence.extend(after)
        sequence.append(_index(message.end))
        LOG.debug(
            "NVDA %s, indexes %d-%d, %d marks: %r",
            kind,
            message.begin,
            message.end,
            len(message.marks),
            text,
        )
        self._current = message
        if not self.channel.send(MESSAGE_SPEAK, sequence=sequence):
            self._current = None
            self._out("701 BEGIN")
            self._out("702 END")
            return
        GLib.timeout_add(_BEGIN_TIMEOUT_MS, self._check_begin, message)

    def _capital_icon(self, kind: str, items: list[tuple[str, str]]) -> bool:
        """A capital letter alone, with Orca's capitalization style "icon"."""
        if kind not in ("SPEAK", "CHAR") or self._settings.get("cap_let_recogn") != "icon":
            return False
        text = "".join(value for item_kind, value in items if item_kind == "text").strip()
        return len(text) == 1 and text.isupper()

    def _check_begin(self, message: _Message) -> bool:
        if self._current is message and not message.began:
            LOG.warning(
                "NVDA did not report index %d within %d ms, ending the message",
                message.begin,
                _BEGIN_TIMEOUT_MS,
            )
            self._current = None
            self._out("701 BEGIN")
            self._out("702 END")
        return GLib.SOURCE_REMOVE

    def _on_channel_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if (
            kind == MESSAGE_ATTRIBUTE_REQUEST
            and message.get("attribute") == ATTRIBUTE_TIME_SINCE_INPUT
        ):
            # The client takes the remote session as focused while this is at most 200 ms.
            self.channel.send(
                MESSAGE_ATTRIBUTE_VALUE,
                attribute=ATTRIBUTE_TIME_SINCE_INPUT,
                value=self._input_time.since_input(self.channel.attached_at),
            )
            return
        if kind == MESSAGE_ATTRIBUTE_VALUE:
            self.driver_settings.on_attribute_value(message.get("attribute"))
            return
        if kind != MESSAGE_INDEX:
            return
        index = message.get("index")
        LOG.debug("NVDA index %s", index)
        current = self._current
        if current is None or current.via != "nvda":
            return
        if not index:
            # Index 0: end of a sequence or cancel on the client side.  After the
            # end index it is redundant; before the current message began it
            # belongs to an earlier, cancelled message.  Otherwise NVDA cancelled
            # this message itself (a key pressed on the client, a synthesizer
            # change): report it, or Speech Dispatcher would wait for its end.
            if current.began:
                LOG.debug("NVDA cancelled %d-%d", current.begin, current.end)
                self._current = None
                self._out("703 STOP")
            return
        if index == current.begin:
            current.began = True
            if current.capital:
                self.channel.send(MESSAGE_TONE, **CAPITAL_TONE)
            self._out("701 BEGIN")
        elif index in current.marks:
            current.marks_reached += 1
            name = current.marks[index]
            self._out(f"700-{name}\n700 INDEX MARK")
            if current.pausing and name.startswith(_PAUSE_MARK_PREFIX):
                self._stop(704, "PAUSE")
        elif index == current.end:
            if not current.began:
                self._out("701 BEGIN")
            self._current = None
            self._out("702 END")

    def _on_ready(self, ready: bool) -> None:
        self.driver_settings.on_ready(ready)
        if not ready:
            self._voice_name = None
        if ready:
            # NVDA's synthesizer language and the commands it supports
            # (rdAccess@19f5c53: addon/globalPlugins/rdAccess/handlers/
            # remoteSpeechHandler.py:48-58).
            for attribute in (ATTRIBUTE_SUPPORTED_COMMANDS, ATTRIBUTE_LANGUAGE):
                self.channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=attribute)
            self.orca.baseline(self._on_baseline)
        if not ready and self._current is not None and self._current.via == "nvda":
            LOG.info("NVDA detached while speaking")
            self._current = None
            self._out("703 STOP")

    def _pause(self) -> None:
        current = self._current
        if (
            current is None
            or current.via != "nvda"
            or current.pausing
            or not current.began
            or not current.pause_mark_ahead()
        ):
            self._stop(704, "PAUSE")
            return
        LOG.debug("PAUSE: stopping at the next sentence mark")
        current.pausing = True
        GLib.timeout_add(_PAUSE_TIMEOUT_MS, self._check_pause, current)

    def _check_pause(self, message: _Message) -> bool:
        if self._current is message:
            LOG.debug("PAUSE: no mark within %d ms, stopping", _PAUSE_TIMEOUT_MS)
            self._stop(704, "PAUSE")
        return GLib.SOURCE_REMOVE

    def _stop(self, code: int, name: str) -> None:
        current = self._current
        if current is None:
            return
        LOG.debug("%s while speaking through %s", name, current.via)
        if current.via == "nvda":
            self._current = None
            self.channel.send(MESSAGE_CANCEL)
            self._out(f"{code} {name}")
        elif self._local is not None:
            # The local server reports the cancel event, passed on as 703.
            self._local.cancel()

    # Local Speech Dispatcher (no NVDA attached)

    def _local_client(self) -> Any:
        if self._local is None:
            try:
                client = speechd.SSIPClient("nvda-rdp", component="fallback")
                client.set_data_mode(speechd.DataMode.SSML)
                self._local = client
                self._applied = {}
            except Exception:
                LOG.exception("no local Speech Dispatcher")
        return self._local

    def _apply_settings(self, client: Any) -> None:
        setters = {
            "rate": lambda v: client.set_rate(int(v)),
            "pitch": lambda v: client.set_pitch(int(v)),
            "pitch_range": lambda v: client.set_pitch_range(int(v)),
            "volume": lambda v: client.set_volume(int(v)),
            "punctuation_mode": client.set_punctuation,
            "language": client.set_language,
            "synthesis_voice": client.set_synthesis_voice,
        }
        for name, setter in setters.items():
            value = self._settings.get(name)
            if value is None or value in ("", "NULL") or self._applied.get(name) == value:
                continue
            if name == "synthesis_voice" and (
                value == GENERIC_VOICE or value in self._listed_voices
            ):
                continue  # A voice of NVDA, unknown to the local Speech Dispatcher.
            try:
                setter(value)
                self._applied[name] = value
            except Exception as error:  # noqa: BLE001 - speechd raises its own exception types.
                LOG.warning("cannot apply %s=%s locally: %s", name, value, error)

    def _speak_local(self, kind: str, text: str) -> None:
        client = self._local_client()
        if client is None:
            self._out("701 BEGIN")
            self._out("702 END")
            return
        message = _Message("local")
        LOG.debug("local %s: %r", kind, text)
        self._current = message
        events = {
            speechd.CallbackType.BEGIN: "701 BEGIN",
            speechd.CallbackType.END: "702 END",
            speechd.CallbackType.CANCEL: "703 STOP",
        }

        def on_event(callback_type: Any, index_mark: str | None = None) -> None:
            # Called in the client's listener thread.
            GLib.idle_add(self._on_local_event, message, callback_type, index_mark)

        try:
            self._apply_settings(client)
            if kind == "CHAR":
                client.char(text)
                self._on_local_event(message, speechd.CallbackType.BEGIN, None)
                self._on_local_event(message, speechd.CallbackType.END, None)
            elif kind == "SOUND_ICON":
                client.sound_icon(text)
                self._on_local_event(message, speechd.CallbackType.BEGIN, None)
                self._on_local_event(message, speechd.CallbackType.END, None)
            elif kind == "KEY":
                client.key(text)
                self._on_local_event(message, speechd.CallbackType.BEGIN, None)
                self._on_local_event(message, speechd.CallbackType.END, None)
            else:
                client.speak(
                    text,
                    callback=on_event,
                    event_types=[*events, speechd.CallbackType.INDEX_MARK],
                )
        except Exception:
            LOG.exception("local speech failed")
            self._local = None
            if self._current is message:
                self._current = None
                self._out("701 BEGIN")
                self._out("702 END")

    def _on_local_event(
        self, message: _Message, callback_type: Any, index_mark: str | None
    ) -> bool:
        if self._current is not message:
            return GLib.SOURCE_REMOVE
        if callback_type == speechd.CallbackType.INDEX_MARK:
            self._out(f"700-{index_mark}\n700 INDEX MARK")
        elif callback_type == speechd.CallbackType.BEGIN:
            if not message.began:
                message.began = True
                self._out("701 BEGIN")
        elif callback_type == speechd.CallbackType.END:
            self._current = None
            self._out("702 END")
        elif callback_type == speechd.CallbackType.CANCEL:
            self._current = None
            self._out("703 STOP")
        return GLib.SOURCE_REMOVE
