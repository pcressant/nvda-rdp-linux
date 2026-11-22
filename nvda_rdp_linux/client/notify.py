"""Local "remote speech/braille connected" notifications.

Mirrors RDAccess's ``connectionNotifications`` setting: a spoken message,
NVDA's ``connected.wav``/``disconnected.wav`` sounds, both, or nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .audio import AudioOutput

LOG = logging.getLogger(__name__)

MODES = ("none", "message", "sound", "both")
_MESSAGES = {
    "en": {
        ("speech", True): "Remote speech connected",
        ("speech", False): "Remote speech disconnected",
        ("braille", True): "Remote braille connected",
        ("braille", False): "Remote braille disconnected",
    },
    "fr": {
        ("speech", True): "Parole distante connectée",
        ("speech", False): "Parole distante déconnectée",
        ("braille", True): "Braille distant connecté",
        ("braille", False): "Braille distant déconnecté",
    },
}


def message_for(kind: str, connected: bool, language: str) -> str:
    messages = _MESSAGES.get(language.split("_")[0].split("-")[0].casefold(), _MESSAGES["en"])
    return messages[(kind, connected)]


class ConnectionNotifier:
    def __init__(
        self,
        mode: str,
        *,
        kind: str,
        language: str,
        speak: Callable[[str], None] | None = None,
        audio: AudioOutput | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"notification mode must be one of {MODES}")
        self._mode = mode
        self._kind = kind
        self._language = language
        self._speak = speak
        self._audio = audio

    def notify(self, connected: bool) -> None:
        if self._mode in ("message", "both") and self._speak is not None:
            message = message_for(self._kind, connected, self._language)
            LOG.debug("notifying %r", message)
            try:
                self._speak(message)
            except Exception as error:  # noqa: BLE001 - a notification must not stop the client.
                LOG.error("cannot speak connection notification: %s", error)
        if self._mode in ("sound", "both") and self._audio is not None:
            self._audio.wave("waves\\connected.wav" if connected else "waves\\disconnected.wav")
