# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 13 (rdaccess_speechd/prosody.py).
# NVDA command classes read in nvda@bfe9a2e: source/speech/commands.py
# (LangChangeCommand 177-198, BaseProsodyCommand 249-330, Pitch/Volume/Rate
# 347-362); both serializers rebuild them with __new__ and __dict__.update
# (source/_remoteClient/serializer.py:163-195, rdAccess@19f5c53:
# addon/lib/protocol/serializer.py:92-114). No code copied.

"""Orca's voice settings sent to NVDA as per-utterance speech commands,
never as NVDA settings.

Speech Dispatcher sends every message's settings to the module (rate, pitch,
volume from -100 to 100, language; speechd@af73916:
src/server/output.c:692-733).  NVDA has no command that sets an absolute
value without changing its configuration, but ``RateCommand``,
``PitchCommand`` and ``VolumeCommand`` change the user's configured value for
one utterance only, by an offset or a multiplier (commands.py:249-322).

So the module sends the difference between a message's settings and a
baseline: Orca's default voice when NVDA attached, read through Orca's public
D-Bus service.  NVDA keeps its own rate, pitch and volume; what Orca changes
from its default (a voice category such as capitals, a rate changed with
Orca's keys) changes NVDA's speech by the same amount on NVDA's 0 to 100
scale.  Nothing is stored in NVDA's configuration.

Language: a ``LangChangeCommand`` when the message's language differs from
NVDA's (or the session's) language.  NVDA passes it to its synthesizer only
with automatic language switching on (source/speech/manager.py:366-369).
"""

from __future__ import annotations

from ..common.speech import (
    COMMAND_PITCH,
    COMMAND_RATE,
    COMMAND_VOLUME,
    Prosody,
    lang_change_command,
    prosody_command,
)

# Orca's default voice, in Orca's units, and its conversion to Speech
# Dispatcher's (orca@7f241c6: src/orca/speechdispatcherfactory.py:224-246).
ORCA_DEFAULT_RATE = 50
ORCA_DEFAULT_PITCH = 5.0
ORCA_DEFAULT_VOLUME = 10.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def orca_to_speechd(rate: float, pitch: float, volume: float) -> dict[str, int]:
    return {
        "rate": int(2 * _clamp(rate, 0, 100) - 100),
        "pitch": int(20 * _clamp(pitch, 0, 10) - 100),
        "volume": int(20 * _clamp(volume, 0, 10) - 100),
    }


NEUTRAL = orca_to_speechd(ORCA_DEFAULT_RATE, ORCA_DEFAULT_PITCH, ORCA_DEFAULT_VOLUME)

# Back to NVDA's configured values, after a sequence that changed them.
RESET_COMMANDS = [
    prosody_command(COMMAND_RATE),
    prosody_command(COMMAND_PITCH),
    prosody_command(COMMAND_VOLUME),
    lang_change_command(None),
]


def _int(settings: dict[str, str], name: str) -> int | None:
    try:
        return int(settings.get(name, ""))
    except ValueError:
        return None


def primary_language(language: str | None) -> str:
    return (language or "").replace("_", "-").split("-")[0].lower()


def nvda_language(language: str | None) -> str | None:
    """Speech Dispatcher's ``fr-FR``, NVDA's ``fr_FR``."""
    if not language or language == "NULL":
        return None
    parts = language.replace("_", "-").split("-")
    if len(parts) > 1:
        return f"{parts[0].lower()}_{parts[1].upper()}"
    return parts[0].lower()


def prosody_commands(
    settings: dict[str, str],
    base: dict[str, int],
    base_language: str | None,
    supported: set[str] | None = None,
) -> list[list[object]]:
    """The NVDA commands to put before a message's text; empty when nothing differs."""
    commands: list[list[object]] = []
    for name, command in (("rate", COMMAND_RATE), ("pitch", COMMAND_PITCH)):
        value = _int(settings, name)
        if value is None:
            continue
        offset = round((value - base[name]) / 2)
        if offset:
            commands.append(prosody_command(command, Prosody(offset=offset)))
    volume = _int(settings, "volume")
    if volume is not None and base["volume"] > -100:
        multiplier = round((volume + 100) / (base["volume"] + 100), 3)
        if multiplier != 1:
            commands.append(prosody_command(COMMAND_VOLUME, Prosody(multiplier=multiplier)))
    language = settings.get("language")
    if (
        language
        and language != "NULL"
        and base_language
        and primary_language(language) != primary_language(base_language)
    ):
        commands.append(lang_change_command(nvda_language(language)))
    if supported is not None:
        commands = [command for command in commands if command[0] in supported]
    return commands


def reset_commands(commands: list[list[object]]) -> list[list[object]]:
    """The commands that undo ``commands`` at the end of a sequence."""
    names = {command[0] for command in commands}
    return [reset for reset in RESET_COMMANDS if reset[0] in names]
