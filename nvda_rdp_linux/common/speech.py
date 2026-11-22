"""``NVDA-SPEECH`` vocabulary and NVDA's prosody commands.

Speech sequences are lists of plain strings and ``[CommandClassName,
__dict__]`` pairs, as produced by RDAccess's serializer
(``protocol/serializer.py``) from NVDA's ``speech/commands.py`` objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

DRIVER_TYPE = b"S"
CHANNEL_NAME = "NVDA-SPEECH"

MESSAGE_SPEAK = "speak"
MESSAGE_CANCEL = "cancel"
MESSAGE_PAUSE_SPEECH = "pause_speech"
MESSAGE_TONE = "tone"
MESSAGE_WAVE = "wave"
MESSAGE_INDEX = "index"

COMMAND_INDEX = "IndexCommand"
COMMAND_CHARACTER_MODE = "CharacterModeCommand"
COMMAND_LANG_CHANGE = "LangChangeCommand"
COMMAND_BREAK = "BreakCommand"
COMMAND_PITCH = "PitchCommand"
COMMAND_RATE = "RateCommand"
COMMAND_VOLUME = "VolumeCommand"
COMMAND_PHONEME = "PhonemeCommand"
COMMAND_END_UTTERANCE = "EndUtteranceCommand"
PROSODY_COMMANDS = {COMMAND_PITCH: "pitch", COMMAND_RATE: "rate", COMMAND_VOLUME: "volume"}

ATTRIBUTE_LANGUAGE = "language"
ATTRIBUTE_SUPPORTED_COMMANDS = "supportedCommands"
ATTRIBUTE_AVAILABLE_LANGUAGES = "_availableLanguages"


def is_number(value: object) -> bool:
    """A finite JSON number; booleans are not numbers."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Prosody:
    """NVDA ``BaseProsodyCommand`` state: an offset or a multiplier, never both."""

    offset: float = 0
    multiplier: float = 1

    @property
    def is_default(self) -> bool:
        return self.offset == 0 and self.multiplier == 1

    def apply(self, base: int) -> int:
        """Mirror ``BaseProsodyCommand.newValue`` on NVDA's 0–100 scale."""
        if self.offset != 0:
            value = base + self.offset
        elif self.multiplier != 1:
            value = base * self.multiplier
        else:
            value = base
        return max(0, min(100, int(value)))


DEFAULT_PROSODY = Prosody()


def parse_prosody(parameters: dict[str, Any]) -> Prosody | None:
    """Decode the ``__dict__`` of a prosody command; None when invalid."""
    offset = parameters.get("_offset", 0)
    multiplier = parameters.get("_multiplier", 1)
    if not is_number(offset) or not is_number(multiplier) or multiplier < 0:
        return None
    return Prosody(offset, multiplier)


def prosody_command(name: str, prosody: Prosody = DEFAULT_PROSODY) -> list[object]:
    """Encode a prosody command; ``isDefault`` is set as NVDA computes it."""
    return [
        name,
        {
            "_offset": prosody.offset,
            "_multiplier": prosody.multiplier,
            "isDefault": prosody.is_default,
        },
    ]


def lang_change_command(language: str | None) -> list[object]:
    """Encode a ``LangChangeCommand``; None goes back to the synthesizer's language."""
    return [COMMAND_LANG_CHANGE, {"lang": language, "isDefault": language is None}]
