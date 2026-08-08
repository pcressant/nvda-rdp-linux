from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from client.rdaccess.protocol import (
    CONNECTION_XON,
    PROTOCOL_VERSION_2_LEGACY,
    JsonLineDecoder,
    ProtocolSession,
    render_sequence,
)

FIXTURES = (
    Path(__file__).parent / "fixtures" / "rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95"
)


def fixture_bytes(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text())


class FakeOutput:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, bool]] = []
        self.done_callbacks: list[Callable[[], None]] = []
        self.cancel_count = 0

    def speak(
        self,
        text: str,
        *,
        character_mode: bool = False,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self.spoken.append((text, character_mode))
        if on_done is not None:
            self.done_callbacks.append(on_done)

    def cancel(self) -> None:
        self.cancel_count += 1


def test_complete_real_json_line() -> None:
    decoder = JsonLineDecoder()
    messages = decoder.feed(fixture_bytes("first-speak-v2-jsonl.hex"))
    assert messages[0]["type"] == "speak"


def test_fragmented_utf8_json_line() -> None:
    decoder = JsonLineDecoder()
    encoded = '{"type":"speak","sequence":["fenêtre"]}\n'.encode()
    split = encoded.index("ê".encode()) + 1
    assert decoder.feed(encoded[:split]) == []
    assert decoder.feed(encoded[split:])[0]["sequence"] == ["fenêtre"]


def test_multiple_lines_in_one_block() -> None:
    decoder = JsonLineDecoder()
    block = b'{"type":"cancel"}\n{"type":"unknown"}\n'
    assert [message["type"] for message in decoder.feed(block)] == [
        "cancel",
        "unknown",
    ]


def test_incomplete_json_is_buffered() -> None:
    decoder = JsonLineDecoder()
    assert decoder.feed(b'{"type":"speak"') == []
    assert decoder.feed(b',"sequence":["ok"]}\n')[0]["type"] == "speak"


def test_invalid_complete_json_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    assert JsonLineDecoder().feed(b"{invalid}\n") == []
    assert "invalid RDAccess JSON line" in caplog.text


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
            b'["CharacterModeCommand", "IndexCommand"], "type": "attribute_value"}\n'
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
    caplog.set_level("INFO")
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
