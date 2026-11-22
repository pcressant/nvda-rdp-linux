"""Persistent Speech Dispatcher output.

Voice parameters are kept on NVDA's 0–100 scale (which is also Orca's) and
converted to SSIP's -100..100 scale when sent: ``SSIP = 2 × value − 100``.
Per-segment prosody and language changes from the speech sequence are
applied relative to these base values and only sent to Speech Dispatcher
when they differ from what was last applied.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from typing import Any

from ..common import settings
from ..common.speech import DEFAULT_PROSODY, Prosody

LOG = logging.getLogger(__name__)

DEFAULT_PITCH = 50
DEFAULT_VOLUME = 100


def orca_rate_to_ssip(rate: int) -> int:
    if not 0 <= rate <= 100:
        raise ValueError("Orca-compatible rate must be between 0 and 100")
    return 2 * rate - 100


percent_to_ssip = orca_rate_to_ssip


def nvda_language_to_ssip(language: str) -> str:
    """NVDA uses ``fr_FR``; Speech Dispatcher uses ``fr-FR``."""
    return language.replace("_", "-")


def ssip_language_to_nvda(language: str) -> str:
    return language.replace("-", "_")


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize a method on the instance's lock (see ``SpeechDispatcherOutput``)."""

    @functools.wraps(method)
    def wrapper(self: SpeechDispatcherOutput, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class SpeechDispatcherOutput:
    """One SSIP connection shared by the speech and braille channel threads.

    ``SSIPClient`` does not serialize commands sent from several threads, and
    ``speak`` sends several commands that must stay together (language,
    prosody, spelling, text); every public method therefore holds one lock.
    Speech Dispatcher callbacks run on its own thread and only report indexes,
    they never take this lock.
    """

    def __init__(
        self,
        *,
        language: str = "fr",
        orca_rate: int = 75,
        output_module: str = "espeak-ng",
        pitch: int = DEFAULT_PITCH,
        volume: int = DEFAULT_VOLUME,
    ) -> None:
        from speechd.client import SSIPClient

        self._lock = threading.RLock()
        self._client = SSIPClient(
            "nvda-rdp-linux",
            component="rdaccess",
            autospawn=True,
        )
        self._client.set_output_module(output_module)
        self._client.set_language(nvda_language_to_ssip(language))
        ssip_rate = orca_rate_to_ssip(orca_rate)
        self._client.set_rate(ssip_rate)
        self._language = language
        self._values = {
            settings.SETTING_RATE: orca_rate,
            settings.SETTING_PITCH: pitch,
            settings.SETTING_VOLUME: volume,
        }
        self._applied = dict(self._values)
        self._applied_language = language
        self._voice: str | None = None
        self._voices: tuple[tuple[str, str | None, str | None], ...] | None = None
        if pitch != DEFAULT_PITCH:
            self._client.set_pitch(percent_to_ssip(pitch))
        if volume != DEFAULT_VOLUME:
            self._client.set_volume(percent_to_ssip(volume))
        LOG.info(
            "connected to Speech Dispatcher module=%s language=%s Orca-rate=%d SSIP-rate=%d",
            output_module,
            language,
            orca_rate,
            ssip_rate,
        )

    @_locked
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
    ) -> None:
        from speechd.client import CallbackType

        def callback(event_type: str, **_kwargs: object) -> None:
            if event_type == CallbackType.END and on_done is not None:
                on_done()

        self._apply_language(language or self._language)
        self._apply_prosody(settings.SETTING_PITCH, pitch)
        self._apply_prosody(settings.SETTING_RATE, rate)
        self._apply_prosody(settings.SETTING_VOLUME, volume)
        self._client.set_spelling(character_mode)
        try:
            self._client.speak(
                text,
                callback=callback if on_done is not None else None,
                event_types=(CallbackType.END,) if on_done is not None else None,
            )
        finally:
            if character_mode:
                self._client.set_spelling(False)
        LOG.info(
            "Speech Dispatcher accepted speak character_mode=%s text=%r",
            character_mode,
            text,
        )

    @_locked
    def insert_break(self, milliseconds: int) -> None:
        from speechd.client import DataMode

        self._client.set_data_mode(DataMode.SSML)
        try:
            self._client.speak(f'<speak><break time="{int(milliseconds)}ms"/></speak>')
        finally:
            self._client.set_data_mode(DataMode.TEXT)
        LOG.info("Speech Dispatcher accepted break of %d ms", milliseconds)

    @_locked
    def cancel(self) -> None:
        self._client.cancel()
        LOG.info("Speech Dispatcher accepted cancel")

    @_locked
    def pause(self, switch: bool) -> None:
        if switch:
            self._client.pause()
        else:
            self._client.resume()
        LOG.info("Speech Dispatcher accepted %s", "pause" if switch else "resume")

    @_locked
    def close(self) -> None:
        self._client.close()
        LOG.info("closed Speech Dispatcher connection")

    # Voice state

    def _apply_language(self, language: str) -> None:
        if language == self._applied_language:
            return
        self._client.set_language(nvda_language_to_ssip(language))
        self._applied_language = language

    def _apply_prosody(self, setting_id: str, prosody: Prosody) -> None:
        value = prosody.apply(self._values[setting_id])
        if value == self._applied[setting_id]:
            return
        # SSIPClient.set_rate / set_pitch / set_volume
        getattr(self._client, f"set_{setting_id}")(percent_to_ssip(value))
        self._applied[setting_id] = value

    def _synthesis_voices(self) -> tuple[tuple[str, str | None, str | None], ...]:
        if self._voices is None:
            self._voices = tuple(self._client.list_synthesis_voices())
        return self._voices

    @_locked
    def available_languages(self) -> list[str | None]:
        """Encode like RDAccess's ``encodeAvailableLanguages``: sorted, None last."""
        languages: set[str | None] = {self._language}
        for _name, language, _variant in self._synthesis_voices():
            languages.add(ssip_language_to_nvda(language) if language else None)
        return sorted(languages, key=lambda lang: (lang is None, lang or ""))

    # Driver settings management

    @_locked
    def supported_settings(self, language: str) -> list[object]:
        result: list[object] = []
        if self._synthesis_voices():
            result.append(settings.encode_string_setting(settings.SETTING_VOICE, language))
        result.extend(
            [
                settings.encode_numeric_setting(settings.NumericSetting("rate"), language),
                settings.encode_numeric_setting(settings.NumericSetting("pitch"), language),
                settings.encode_numeric_setting(
                    settings.NumericSetting("volume", default=DEFAULT_VOLUME, normal_step=5),
                    language,
                ),
            ]
        )
        return result

    @_locked
    def get_setting(self, setting_id: str) -> object:
        if setting_id in self._values:
            return self._values[setting_id]
        if setting_id == settings.SETTING_VOICE:
            return self._current_voice()
        raise KeyError(setting_id)

    @_locked
    def set_setting(self, setting_id: str, value: object) -> bool:
        if setting_id in self._values:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                return False
            self._values[setting_id] = value
            return True
        if setting_id == settings.SETTING_VOICE:
            names = {name for name, _language, _variant in self._synthesis_voices()}
            if not isinstance(value, str) or value not in names:
                return False
            self._client.set_synthesis_voice(value)
            self._voice = value
            return True
        return False

    @_locked
    def available_values(self, setting_id: str) -> dict[str, object] | None:
        if setting_id != settings.SETTING_VOICE:
            return None
        return {
            name: settings.encode_voice_info(
                name,
                f"{name} ({variant})" if variant else name,
                ssip_language_to_nvda(language) if language else None,
            )
            for name, language, variant in self._synthesis_voices()
        }

    def _current_voice(self) -> str | None:
        if self._voice is None:
            voices = self._synthesis_voices()
            wanted = nvda_language_to_ssip(self._language).casefold()
            for name, language, _variant in voices:
                if language and language.casefold().startswith(wanted):
                    self._voice = name
                    break
            else:
                self._voice = voices[0][0] if voices else None
        return self._voice
