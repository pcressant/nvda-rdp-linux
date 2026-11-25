"""Driver settings of the client: parsing, values given as text, NVDA's voices."""

from __future__ import annotations

from typing import Any

import pytest

from nvda_rdp_linux.server import settings_cli
from nvda_rdp_linux.server.driver_settings import (
    KIND_BOOLEAN,
    KIND_NUMERIC,
    KIND_STRING,
    Choice,
    DriverSettings,
    Setting,
    parse_choices,
    parse_supported_settings,
)
from nvda_rdp_linux.server.settings_service import SettingsError, convert, describe
from nvda_rdp_linux.server.speechd_module import speechd_language, voice_names

RATE = Setting("rate", KIND_NUMERIC, "Rate", min=0, max=100)
BOOST = Setting("rateBoost", KIND_BOOLEAN, "Rate boost")
VOICE = Setting("voice", KIND_STRING, "Voice")
VOICES = [Choice("hortense", "Microsoft Hortense", "fr_FR"), Choice("zira", "Zira", "en_US")]


class FakeChannel:
    name = "TEST"

    def __init__(self) -> None:
        self.ready = True
        self.attributes: dict[str, Any] = {}
        self.sent: list[dict[str, Any]] = []

    def send(self, kind: str, **fields: Any) -> bool:
        if not self.ready:
            return False
        self.sent.append({"type": kind, **fields})
        return True


def test_supported_settings_of_nvda() -> None:
    value = [
        ["DriverSetting", {"id": "voice", "displayName": "Voix"}],
        ["NumericDriverSetting", {"id": "rate", "displayName": "Débit", "minVal": 0, "maxVal": 80}],
        ["BooleanDriverSetting", {"id": "rateBoost"}],
        ["UnknownSetting", {"id": "other"}],
        ["DriverSetting", {"displayName": "no id"}],
        "garbage",
    ]
    assert parse_supported_settings(value) == [
        Setting("voice", KIND_STRING, "Voix"),
        Setting("rate", KIND_NUMERIC, "Débit", min=0, max=80),
        Setting("rateBoost", KIND_BOOLEAN, "rateBoost"),
    ]
    assert parse_supported_settings([]) == []
    assert parse_supported_settings(None) is None


def test_available_values_keep_their_order_and_language() -> None:
    value = {
        "b": ["VoiceInfo", {"id": "b", "displayName": "Bee", "language": "fr_FR"}],
        "a": ["StringParameterInfo", {"id": "a", "displayName": "Ay"}],
        "bad": 3,
    }
    assert parse_choices(value) == [Choice("b", "Bee", "fr_FR"), Choice("a", "Ay")]
    assert parse_choices([]) is None


@pytest.mark.parametrize(
    ("setting", "text", "value"),
    [
        (RATE, "60", 60),
        (BOOST, "ON", True),
        (BOOST, "off", False),
        (VOICE, "zira", "zira"),
        (VOICE, "microsoft hortense", "hortense"),
    ],
)
def test_text_to_value(setting: Setting, text: str, value: object) -> None:
    assert convert(setting, text, VOICES) == value


@pytest.mark.parametrize(
    ("setting", "text", "choices"),
    [
        (RATE, "101", None),
        (RATE, "fast", None),
        (BOOST, "maybe", None),
        (VOICE, "nobody", VOICES),
        (VOICE, "zira", None),
    ],
)
def test_invalid_text(setting: Setting, text: str, choices: list[Choice] | None) -> None:
    with pytest.raises(SettingsError):
        convert(setting, text, choices)


def test_new_values_are_requested_when_nvda_pushes_its_settings() -> None:
    channel = FakeChannel()
    settings = DriverSettings(channel)  # type: ignore[arg-type]
    channel.attributes["supportedSettings"] = [
        ["DriverSetting", {"id": "voice"}],
        ["NumericDriverSetting", {"id": "rate"}],
    ]
    settings.on_attribute_value("supportedSettings")
    assert [m["attribute"] for m in channel.sent] == [
        "setting_voice",
        "availableVoices",
        "setting_rate",
    ]


def test_a_refresh_ends_when_every_value_came() -> None:
    channel = FakeChannel()
    settings = DriverSettings(channel)  # type: ignore[arg-type]
    done: list[bool] = []
    settings.refresh(lambda: done.append(True))
    assert channel.sent == [{"type": "attribute_request", "attribute": "supportedSettings"}]
    channel.attributes["supportedSettings"] = [["BooleanDriverSetting", {"id": "beep"}]]
    settings.on_attribute_value("supportedSettings")
    assert not done
    settings.on_attribute_value("setting_beep")
    assert done == [True]


def test_a_refresh_ends_on_detach() -> None:
    channel = FakeChannel()
    settings = DriverSettings(channel)  # type: ignore[arg-type]
    done: list[bool] = []
    settings.refresh(lambda: done.append(True))
    settings.on_ready(False)
    assert done == [True]


def test_a_new_value_is_kept() -> None:
    channel = FakeChannel()
    settings = DriverSettings(channel)  # type: ignore[arg-type]
    assert settings.set("rate", 40)
    assert settings.value("rate") == 40
    channel.ready = False
    assert not settings.set("rate", 50)
    assert settings.value("rate") == 40


def test_voice_names_are_unique() -> None:
    voices = [Choice("a", "Anna"), Choice("b", "Anna"), Choice("c", "NVDA"), Choice("d", "  x\ty ")]
    assert list(voice_names(voices)) == ["Anna", "Anna (b)", "NVDA (c)", "x y"]


def test_speech_dispatcher_language(monkeypatch: pytest.MonkeyPatch) -> None:
    assert speechd_language("fr_FR") == "fr-FR"
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert speechd_language(None) == "de"


def test_cli_lines() -> None:
    assert (
        settings_cli.format_setting(describe(VOICE, "zira", VOICES)) == "voice: zira, Zira (Voice)"
    )
    assert (
        settings_cli.format_setting(describe(RATE, None, None)) == "rate: unknown (Rate, 0 to 100)"
    )
    assert settings_cli.format_setting(describe(BOOST, True, None)) == (
        "rateBoost: on (Rate boost, on or off)"
    )
    assert settings_cli.format_choice({"id": "zira", "name": "Zira", "language": "en_US"}) == (
        "zira: Zira (en_US)"
    )


def test_cli_without_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert settings_cli.main(["braille", "list"]) == settings_cli.EXIT_ERROR
    assert "the braille server of this session does not answer" in capsys.readouterr().err
