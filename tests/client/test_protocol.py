from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from nvda_rdp_linux.client.protocol import (
    PROTOCOL_VERSION_2_LEGACY,
    ProtocolSession,
    render_sequence,
)
from nvda_rdp_linux.common.rdaccess import CONNECTION_XON
from nvda_rdp_linux.common.speech import Prosody

FIXTURES = (
    Path(__file__).parents[1] / "fixtures" / "rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95"
)


def fixture_bytes(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text())


class FakeOutput:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, bool]] = []
        self.done_callbacks: list[Callable[[], None]] = []
        self.cancel_count = 0
        self.states: list[dict[str, object]] = []
        self.breaks: list[int] = []
        self.pauses: list[bool] = []
        self.settings: dict[str, object] = {"rate": 75, "voice": "fr"}

    def speak(
        self,
        text: str,
        *,
        character_mode: bool = False,
        on_done: Callable[[], None] | None = None,
        **state: object,
    ) -> None:
        self.spoken.append((text, character_mode))
        self.states.append(state)
        if on_done is not None:
            self.done_callbacks.append(on_done)

    def insert_break(self, milliseconds: int) -> None:
        self.breaks.append(milliseconds)

    def cancel(self) -> None:
        self.cancel_count += 1

    def pause(self, switch: bool) -> None:
        self.pauses.append(switch)

    def available_languages(self) -> list[str | None]:
        return ["en", "fr", None]

    def supported_settings(self, language: str) -> list[object]:
        return [["NumericDriverSetting", {"id": "rate", "language": language}]]

    def get_setting(self, setting_id: str) -> object:
        return self.settings[setting_id]

    def set_setting(self, setting_id: str, value: object) -> bool:
        if setting_id not in self.settings:
            return False
        self.settings[setting_id] = value
        return True

    def available_values(self, setting_id: str) -> dict[str, object] | None:
        return {"fr": ["VoiceInfo", {"id": "fr", "displayName": "fr", "language": "fr"}]}


def test_render_text_only() -> None:
    assert render_sequence(["hello ", "world"]).text == "hello world"


def test_render_real_index_command() -> None:
    rendered = render_sequence(["Bureau  ", "fenêtre  ", ["IndexCommand", {"index": 1}]])
    assert rendered.text == "Bureau  fenêtre"
    assert rendered.commands[0].name == "IndexCommand"
    assert rendered.commands[0].parameters == {"index": 1}


def test_render_character_mode_segments() -> None:
    rendered = render_sequence(
        [
            "Alt+",
            ["CharacterModeCommand", {"state": True, "isDefault": False}],
            "F",
            ["CharacterModeCommand", {"state": False, "isDefault": True}],
            "1 sur 3",
        ]
    )
    assert [(segment.text, segment.character_mode) for segment in rendered.segments] == [
        ("Alt+", False),
        ("F", True),
        ("1 sur 3", False),
    ]


def test_character_mode_is_limited_to_one_speak_message() -> None:
    output = FakeOutput()
    session = ProtocolSession(output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(
        b'{"type":"speak","sequence":[["CharacterModeCommand",'
        b'{"state":true,"isDefault":false}],"q"]}\n'
    )
    session.feed(b'{"type":"speak","sequence":["normal"]}\n')
    assert output.spoken == [("q", True), ("normal", False)]


def test_real_character_mode_fixture_preserves_order() -> None:
    output = FakeOutput()
    session = ProtocolSession(output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("character-mode-speak-v2-jsonl.hex"))
    assert output.spoken == [("Quitter  ", False), ("q", True)]


def test_indexes_are_sent_only_when_speech_finishes() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"speak","sequence":["mot",["IndexCommand",{"index":7}]]}\n')
    assert sent == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]

    output.done_callbacks[-1]()

    assert sent[-2:] == [
        b'{"index": 7, "type": "index"}\n',
        b'{"index": 0, "type": "index"}\n',
    ]


def test_stop_speech_cancels_locally_and_reports_done() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"speak","sequence":["long text",["IndexCommand",{"index":3}]]}\n')

    session.stop_speech()

    assert output.cancel_count == 1
    assert sent[-1] == b'{"index": 0, "type": "index"}\n'


def test_stop_speech_survives_a_broken_transport() -> None:
    def broken(_payload: bytes) -> None:
        raise BrokenPipeError

    output = FakeOutput()
    session = ProtocolSession(output, send=broken)
    session._connected = True
    session.stop_speech()
    assert output.cancel_count == 1


def test_unknown_command_is_not_spoken(caplog: pytest.LogCaptureFixture) -> None:
    rendered = render_sequence(["safe", ["FutureCommand", {"value": 3}]])
    assert rendered.text == "safe"
    assert rendered.commands[0].name == "FutureCommand"
    assert "ignoring unsupported speech command" in caplog.text


def test_real_handshake_and_speak_fixture() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("server-protocol-version-v2-jsonl.hex"))
    session.feed(fixture_bytes("first-speak-v2-jsonl.hex"))
    assert sent == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]
    assert output.spoken == [("Bureau  fenêtre", False)]


def test_language_request_sends_json_attribute_value() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append, language="fr")
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("server-language-request-v2-jsonl.hex"))
    assert sent == [
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
        b'{"attribute": "language", "value": "fr", "type": "attribute_value"}\n',
    ]


def test_real_supported_commands_request_sends_implemented_commands() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("server-supported-commands-v2-jsonl.hex"))
    assert sent == [
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
        (
            b'{"attribute": "supportedCommands", "value": '
            b'["BreakCommand", "CharacterModeCommand", "IndexCommand", "LangChangeCommand", '
            b'"PitchCommand", "RateCommand", "VolumeCommand"], "type": "attribute_value"}\n'
        ),
    ]


def test_cancel() -> None:
    output = FakeOutput()
    session = ProtocolSession(output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"cancel"}\n')
    assert output.cancel_count == 1


def test_real_pause_fixture_is_recognized_and_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    output = FakeOutput()
    session = ProtocolSession(output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("pause-speech-v2-jsonl.hex"))
    session.feed(b'{"switch": false, "type": "pause_speech"}\n')
    assert "ignoring unsupported pause_speech switch=True" in caplog.text
    assert "ignoring unsupported pause_speech switch=False" in caplog.text


@pytest.mark.parametrize("switch", [None, 1, "true"])
def test_invalid_pause_switch_is_ignored(
    switch: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = FakeOutput()
    session = ProtocolSession(output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(json.dumps({"switch": switch, "type": "pause_speech"}).encode() + b"\n")
    assert "invalid pause_speech switch" in caplog.text


def test_disconnect_discards_fragment_and_allows_reconnect() -> None:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"speak","sequence":["stale')
    session.disconnect()
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"speak","sequence":["fresh"]}\n')
    assert output.spoken == [("fresh", False)]
    assert sent == [
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
    ]


# Synthetic messages below follow RDAccess 2.0.5's serializer format
# ([ClassName, __dict__] pairs); they were not captured from a real session.


def connected_session(**kwargs) -> tuple[ProtocolSession, FakeOutput, list[bytes]]:
    output = FakeOutput()
    sent: list[bytes] = []
    session = ProtocolSession(output, send=sent.append, **kwargs)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    sent.clear()
    return session, output, sent


def speak_line(*sequence: object) -> bytes:
    return json.dumps({"sequence": list(sequence), "type": "speak"}).encode() + b"\n"


def test_render_prosody_language_and_break() -> None:
    rendered = render_sequence(
        [
            "a ",
            ["PitchCommand", {"_offset": 20, "_multiplier": 1, "isDefault": False}],
            ["LangChangeCommand", {"lang": "en_US", "isDefault": False}],
            "b ",
            ["BreakCommand", {"time": 250}],
            ["RateCommand", {"_offset": 0, "_multiplier": 0.5, "isDefault": False}],
            "c",
        ]
    )
    a, b, c = rendered.segments
    assert (a.text, a.pitch, a.language) == ("a ", Prosody(), None)
    assert (b.text, b.pitch, b.language) == ("b ", Prosody(offset=20), "en_US")
    assert (c.text, c.rate, c.break_before_ms) == ("c", Prosody(multiplier=0.5), 250)
    assert c.pitch == Prosody(offset=20)


def test_prosody_apply_mirrors_nvda_new_value() -> None:
    assert Prosody(offset=10).apply(50) == 60
    assert Prosody(multiplier=1.5).apply(50) == 75
    assert Prosody(offset=80).apply(50) == 100
    assert Prosody().apply(42) == 42


def test_indexes_split_segments_and_are_reported_in_order() -> None:
    session, output, sent = connected_session()
    session.feed(
        speak_line(
            ["IndexCommand", {"index": 1}],
            "one ",
            ["IndexCommand", {"index": 2}],
            "two",
            ["IndexCommand", {"index": 3}],
        )
    )
    assert output.spoken == [("one ", False), ("two", False)]
    assert len(output.done_callbacks) == 2
    output.done_callbacks[0]()
    assert [json.loads(p)["index"] for p in sent] == [1, 2]
    output.done_callbacks[1]()
    assert [json.loads(p)["index"] for p in sent] == [1, 2, 3, 0]


def test_sequence_without_text_reports_indexes_immediately() -> None:
    session, output, sent = connected_session()
    session.feed(speak_line(["IndexCommand", {"index": 4}], "  "))
    assert output.spoken == []
    assert [json.loads(p)["index"] for p in sent] == [4, 0]


def test_cancel_reports_done_speaking() -> None:
    session, output, sent = connected_session()
    session.feed(b'{"type":"cancel"}\n')
    assert output.cancel_count == 1
    assert sent == [b'{"index": 0, "type": "index"}\n']


def test_pitch_change_wraps_remote_speech() -> None:
    session, output, _sent = connected_session(pitch_change=10)
    session.feed(speak_line("mot"))
    assert output.states == [
        {"language": None, "pitch": Prosody(offset=10), "rate": Prosody(), "volume": Prosody()}
    ]


def test_break_is_inserted_before_text() -> None:
    session, output, _sent = connected_session()
    session.feed(speak_line("a", ["BreakCommand", {"time": 300}], "b"))
    assert output.breaks == [300]
    assert output.spoken == [("a", False), ("b", False)]


def test_end_utterance_and_phoneme_text() -> None:
    rendered = render_sequence(
        ["a", ["EndUtteranceCommand", {}], ["PhonemeCommand", {"ipa": "x", "text": "b"}]]
    )
    assert [segment.text for segment in rendered.segments] == ["a", "b"]


def test_invalid_prosody_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    rendered = render_sequence(["a", ["PitchCommand", {"_offset": "x"}], "b"])
    assert [segment.text for segment in rendered.segments] == ["ab"]
    assert "ignoring invalid PitchCommand" in caplog.text


def test_pause_is_forwarded_when_enabled() -> None:
    session, output, _sent = connected_session(enable_pause=True)
    session.feed(b'{"switch": true, "type": "pause_speech"}\n')
    session.feed(b'{"switch": false, "type": "pause_speech"}\n')
    assert output.pauses == [True, False]


class FakeAudio:
    def __init__(self) -> None:
        self.tones: list[tuple[object, ...]] = []
        self.waves: list[str] = []

    def tone(self, hz, length, left=50, right=50) -> bool:
        self.tones.append((hz, length, left, right))
        return True

    def wave(self, file_name: str) -> bool:
        self.waves.append(file_name)
        return True


def test_tone_and_wave_reach_audio_output() -> None:
    audio = FakeAudio()
    session, _output, _sent = connected_session(audio=audio)
    session.feed(b'{"hz": 440, "length": 40, "left": 50, "right": 50, "type": "tone"}\n')
    session.feed(b'{"fileName": "waves\\\\error.wav", "asynchronous": true, "type": "wave"}\n')
    session.feed(b'{"hz": "loud", "length": 40, "type": "tone"}\n')
    assert audio.tones == [(440, 40, 50, 50)]
    assert audio.waves == ["waves\\error.wav"]


def test_available_languages_attribute() -> None:
    session, _output, sent = connected_session()
    session.feed(b'{"attribute":"_availableLanguages","type":"attribute_request"}\n')
    assert json.loads(sent[-1])["value"] == ["en", "fr", None]


def test_settings_are_hidden_without_settings_management() -> None:
    session, output, sent = connected_session()
    for attribute in ("supportedSettings", "setting_rate", "availableVoices"):
        session.feed(json.dumps({"attribute": attribute, "type": "attribute_request"}).encode())
        session.feed(b"\n")
    session.feed(b'{"attribute":"setting_rate","value":10,"type":"attribute_value"}\n')
    assert [json.loads(p)["value"] for p in sent] == [[], None, {}]
    assert output.settings["rate"] == 75


def test_settings_management_reads_and_writes_settings() -> None:
    session, output, sent = connected_session(settings_management=True)
    for attribute in ("supportedSettings", "setting_rate", "setting_voice", "availableVoices"):
        session.feed(json.dumps({"attribute": attribute, "type": "attribute_request"}).encode())
        session.feed(b"\n")
    session.feed(b'{"attribute":"setting_rate","value":40,"type":"attribute_value"}\n')
    values = [json.loads(p)["value"] for p in sent]
    assert values[0] == [["NumericDriverSetting", {"id": "rate", "language": "fr"}]]
    assert values[1:3] == [75, "fr"]
    assert values[3]["fr"][0] == "VoiceInfo"
    assert output.settings["rate"] == 40


def test_unknown_setting_is_not_answered() -> None:
    session, _output, sent = connected_session(settings_management=True)
    session.feed(b'{"attribute":"setting_inflection","type":"attribute_request"}\n')
    session.feed(b'{"attribute":"availableVariants","type":"attribute_request"}\n')
    assert sent == []
