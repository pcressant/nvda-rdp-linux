"""JSON encoding of NVDA driver settings exchanged by RDAccess.

With driver settings management enabled, the remote NVDA asks the client
which settings its local synthesizer supports, then reads and writes them
through ``setting_<id>`` attributes.  Settings and their possible values use
RDAccess's ``[ClassName, __dict__]`` convention (``protocol/serializer.py``)
so that the remote side can rebuild NVDA ``DriverSetting``,
``NumericDriverSetting`` and ``VoiceInfo`` objects.  The field names below are
the instance attributes of the classes in NVDA's
``autoSettingsUtils/driverSetting.py`` and ``synthDriverHandler.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

SETTING_ATTRIBUTE_PREFIX = "setting_"

SETTING_VOICE = "voice"
SETTING_RATE = "rate"
SETTING_PITCH = "pitch"
SETTING_VOLUME = "volume"
NUMERIC_SETTINGS = (SETTING_RATE, SETTING_PITCH, SETTING_VOLUME)

# (label with accelerator, settings ring label), as in NVDA's synthDriverHandler.
_LABELS = {
    "en": {
        SETTING_VOICE: ("&Voice", "Voice"),
        SETTING_RATE: ("&Rate", "Rate"),
        SETTING_PITCH: ("&Pitch", "Pitch"),
        SETTING_VOLUME: ("V&olume", "Volume"),
    },
    "fr": {
        SETTING_VOICE: ("&Voix", "Voix"),
        SETTING_RATE: ("&Débit", "Débit"),
        SETTING_PITCH: ("&Hauteur", "Hauteur"),
        SETTING_VOLUME: ("V&olume", "Volume"),
    },
}


def labels_for(language: str) -> dict[str, tuple[str, str]]:
    return _LABELS.get(language.split("_")[0].split("-")[0].casefold(), _LABELS["en"])


def setting_attribute(setting_id: str) -> str:
    return SETTING_ATTRIBUTE_PREFIX + setting_id


def setting_id_from_attribute(attribute: str) -> str | None:
    if attribute.startswith(SETTING_ATTRIBUTE_PREFIX):
        return attribute[len(SETTING_ATTRIBUTE_PREFIX) :]
    return None


def available_values_attribute(setting_id: str) -> str:
    """Mirror ``SettingsAccessorBase._getAvailableSettingsPropertyName``."""
    return f"available{setting_id.capitalize()}s"


@dataclass(frozen=True)
class NumericSetting:
    id: str
    default: int = 50
    min_step: int = 1
    normal_step: int = 5
    large_step: int = 10


def encode_string_setting(setting_id: str, language: str) -> list[object]:
    accelerated, display = labels_for(language)[setting_id]
    return [
        "DriverSetting",
        {
            "id": setting_id,
            "displayNameWithAccelerator": accelerated,
            "displayName": display,
            "availableInSettingsRing": True,
            "defaultVal": None,
            "useConfig": False,
        },
    ]


def encode_numeric_setting(setting: NumericSetting, language: str) -> list[object]:
    accelerated, display = labels_for(language)[setting.id]
    return [
        "NumericDriverSetting",
        {
            "id": setting.id,
            "displayNameWithAccelerator": accelerated,
            "displayName": display,
            "availableInSettingsRing": True,
            "defaultVal": setting.default,
            "useConfig": False,
            "minVal": 0,
            "maxVal": 100,
            "minStep": setting.min_step,
            "normalStep": setting.normal_step,
            "largeStep": setting.large_step,
        },
    ]


def encode_voice_info(voice_id: str, display_name: str, language: str | None) -> list[object]:
    return ["VoiceInfo", {"id": voice_id, "displayName": display_name, "language": language}]
