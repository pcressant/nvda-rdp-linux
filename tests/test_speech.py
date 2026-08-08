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
