"""Per-utterance prosody: Speech Dispatcher settings to NVDA commands."""

from __future__ import annotations

from nvda_rdp_linux.server.prosody import (
    NEUTRAL,
    nvda_language,
    orca_to_speechd,
    prosody_commands,
    reset_commands,
)


def test_orca_defaults_are_neutral_on_speech_dispatcher_scale() -> None:
    # Orca's default volume, 10, is its maximum.
    assert NEUTRAL == {"rate": 0, "pitch": 0, "volume": 100}
    assert orca_to_speechd(100, 10, 10) == {"rate": 100, "pitch": 100, "volume": 100}
    assert orca_to_speechd(-5, 20, 0) == {"rate": -100, "pitch": 100, "volume": -100}


def test_settings_equal_to_the_baseline_send_nothing() -> None:
    settings = {"rate": "0", "pitch": "0", "volume": "100", "language": "fr-FR"}
    assert prosody_commands(settings, NEUTRAL, "fr_FR") == []
    assert reset_commands([]) == []


def test_differences_become_offsets_and_a_volume_multiplier() -> None:
    settings = {"rate": "40", "pitch": "-20", "volume": "0"}
    assert prosody_commands(settings, NEUTRAL, None) == [
        ["RateCommand", {"_offset": 20, "_multiplier": 1, "isDefault": False}],
        ["PitchCommand", {"_offset": -10, "_multiplier": 1, "isDefault": False}],
        ["VolumeCommand", {"_offset": 0, "_multiplier": 0.5, "isDefault": False}],
    ]


def test_baseline_is_orcas_default_voice() -> None:
    base = orca_to_speechd(60, 5.0, 10.0)
    assert base["rate"] == 20
    assert prosody_commands({"rate": "20"}, base, None) == []


def test_silent_baseline_volume_is_not_divided() -> None:
    base = {"rate": 0, "pitch": 0, "volume": -100}
    assert prosody_commands({"volume": "0"}, base, None) == []


def test_language_change_only_for_another_primary_language() -> None:
    assert prosody_commands({"language": "fr-CA"}, NEUTRAL, "fr_FR") == []
    assert prosody_commands({"language": "en-US"}, NEUTRAL, "fr_FR") == [
        ["LangChangeCommand", {"lang": "en_US", "isDefault": False}]
    ]
    assert prosody_commands({"language": "NULL"}, NEUTRAL, "fr_FR") == []
    assert prosody_commands({"language": "en"}, NEUTRAL, None) == []


def test_unsupported_commands_are_dropped() -> None:
    settings = {"rate": "40", "pitch": "40", "language": "en"}
    commands = prosody_commands(settings, NEUTRAL, "fr", {"RateCommand"})
    assert [command[0] for command in commands] == ["RateCommand"]


def test_invalid_numbers_are_ignored() -> None:
    assert prosody_commands({"rate": "fast", "pitch": ""}, NEUTRAL, None) == []


def test_reset_undoes_only_what_was_changed() -> None:
    commands = prosody_commands({"pitch": "40", "language": "en"}, NEUTRAL, "fr")
    assert reset_commands(commands) == [
        ["PitchCommand", {"_offset": 0, "_multiplier": 1, "isDefault": True}],
        ["LangChangeCommand", {"lang": None, "isDefault": True}],
    ]


def test_nvda_language_names() -> None:
    assert nvda_language("fr-FR") == "fr_FR"
    assert nvda_language("EN") == "en"
    assert nvda_language("NULL") is None
    assert nvda_language(None) is None
