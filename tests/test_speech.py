import pytest

from client.rdaccess.speech import SpeechDispatcherOutput, orca_rate_to_ssip


@pytest.mark.parametrize(
    ("orca_rate", "ssip_rate"),
    [(0, -100), (50, 0), (70, 40), (75, 50), (100, 100)],
)
def test_orca_rate_to_ssip(orca_rate: int, ssip_rate: int) -> None:
    assert orca_rate_to_ssip(orca_rate) == ssip_rate


@pytest.mark.parametrize("rate", [-1, 101])
def test_invalid_orca_rate(rate: int) -> None:
    with pytest.raises(ValueError):
        orca_rate_to_ssip(rate)


class FakeSSIPClient:
    instance: FakeSSIPClient

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.calls: list[tuple[object, ...]] = []
        FakeSSIPClient.instance = self

    def set_output_module(self, value: str) -> None:
        self.calls.append(("module", value))

    def set_language(self, value: str) -> None:
        self.calls.append(("language", value))

    def set_rate(self, value: int) -> None:
        self.calls.append(("rate", value))

    def set_spelling(self, value: bool) -> None:
        self.calls.append(("spelling", value))

    def speak(
        self,
        text: str,
        callback: object = None,
        event_types: object = None,
    ) -> None:
        self.calls.append(("speak", text, callback, event_types))

    def cancel(self) -> None:
        self.calls.append(("cancel",))

    def close(self) -> None:
        self.calls.append(("close",))


def test_character_mode_uses_speech_dispatcher_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("speechd.client.SSIPClient", FakeSSIPClient)
    output = SpeechDispatcherOutput()
    client = FakeSSIPClient.instance
    client.calls.clear()

    output.speak("q", character_mode=True)
    output.speak("normal")

    assert client.calls == [
        ("spelling", True),
        ("speak", "q", None, None),
        ("spelling", False),
        ("spelling", False),
        ("speak", "normal", None, None),
    ]


def test_speech_dispatcher_end_callback_reports_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from speechd.client import CallbackType

    monkeypatch.setattr("speechd.client.SSIPClient", FakeSSIPClient)
    output = SpeechDispatcherOutput()
    client = FakeSSIPClient.instance
    client.calls.clear()
    done: list[bool] = []

    output.speak("mot", on_done=lambda: done.append(True))
    _name, _text, callback, event_types = client.calls[-1]
    assert event_types == (CallbackType.END,)
    assert callable(callback)
    callback(CallbackType.CANCEL)
    assert done == []
    callback(CallbackType.END)
    assert done == [True]


class RichFakeSSIPClient(FakeSSIPClient):
    voices = (("fr-voice", "fr-FR", None), ("en-voice", "en-US", "f1"), ("other", None, None))

    def set_pitch(self, value: int) -> None:
        self.calls.append(("pitch", value))

    def set_volume(self, value: int) -> None:
        self.calls.append(("volume", value))

    def set_data_mode(self, value: object) -> None:
        self.calls.append(("data_mode", value))

    def set_synthesis_voice(self, value: str) -> None:
        self.calls.append(("voice", value))

    def list_synthesis_voices(self):
        return self.voices

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))


@pytest.fixture
def rich_output(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SpeechDispatcherOutput, RichFakeSSIPClient]:
    monkeypatch.setattr("speechd.client.SSIPClient", RichFakeSSIPClient)
    output = SpeechDispatcherOutput(language="fr", orca_rate=50)
    client = RichFakeSSIPClient.instance
    client.calls.clear()
    return output, client


def test_prosody_and_language_are_applied_then_restored(rich_output) -> None:
    from client.rdaccess.protocol import Prosody

    output, client = rich_output
    output.speak("a", pitch=Prosody(offset=10), language="en_US", rate=Prosody(multiplier=2))
    output.speak("b")
    assert client.calls == [
        ("language", "en-US"),
        ("pitch", 20),
        ("rate", 100),
        ("spelling", False),
        ("speak", "a", None, None),
        ("language", "fr"),
        ("pitch", 0),
        ("rate", 0),
        ("spelling", False),
        ("speak", "b", None, None),
    ]


def test_break_uses_ssml_mode(rich_output) -> None:
    from speechd.client import DataMode

    output, client = rich_output
    output.insert_break(250)
    assert client.calls == [
        ("data_mode", DataMode.SSML),
        ("speak", '<speak><break time="250ms"/></speak>', None, None),
        ("data_mode", DataMode.TEXT),
    ]


def test_pause_and_resume(rich_output) -> None:
    output, client = rich_output
    output.pause(True)
    output.pause(False)
    assert client.calls == [("pause",), ("resume",)]


def test_available_languages_are_sorted_with_none_last(rich_output) -> None:
    output, _client = rich_output
    assert output.available_languages() == ["en_US", "fr", "fr_FR", None]


def test_settings_round_trip(rich_output) -> None:
    output, client = rich_output
    ids = [setting[1]["id"] for setting in output.supported_settings("fr")]
    assert ids == ["voice", "rate", "pitch", "volume"]
    assert output.get_setting("voice") == "fr-voice"
    assert output.get_setting("volume") == 100
    assert output.set_setting("rate", 80)
    assert not output.set_setting("rate", 101)
    assert not output.set_setting("rate", True)
    assert output.set_setting("voice", "en-voice")
    assert not output.set_setting("voice", "missing")
    assert ("voice", "en-voice") in client.calls
    assert output.available_values("voice")["en-voice"] == [
        "VoiceInfo",
        {"id": "en-voice", "displayName": "en-voice (f1)", "language": "en_US"},
    ]
    output.speak("x")
    assert ("rate", 60) in client.calls


def test_numeric_setting_encoding_matches_nvda_fields() -> None:
    from client.rdaccess.settings import NumericSetting, encode_numeric_setting

    name, state = encode_numeric_setting(NumericSetting("pitch"), "fr")
    assert name == "NumericDriverSetting"
    assert set(state) == {
        "id",
        "displayNameWithAccelerator",
        "displayName",
        "availableInSettingsRing",
        "defaultVal",
        "useConfig",
        "minVal",
        "maxVal",
        "minStep",
        "normalStep",
        "largeStep",
    }
    assert state["displayName"] == "Hauteur"
