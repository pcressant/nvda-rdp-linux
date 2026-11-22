"""The RDAccess v2 wire format shared by the client and the server."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvda_rdp_linux.common.rdaccess import (
    InvalidBytes,
    InvalidLegacyAttribute,
    JsonLineDecoder,
    LegacyFrame,
    StreamDecoder,
    encode_json_line,
    encode_legacy_attribute,
    legacy_protocol_version_frame,
    parse_legacy_attribute,
)

FIXTURES = (
    Path(__file__).parents[1] / "fixtures" / "rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95"
)


def fixture_bytes(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text())


def drain(decoder: StreamDecoder) -> list[object]:
    items = []
    while (item := decoder.pop()) is not None:
        items.append(item)
    return items


# Legacy attribute frames


def test_protocol_version_frames_match_rdaccess() -> None:
    assert legacy_protocol_version_frame(b"S") == bytes.fromhex(
        "53 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"
    )
    assert legacy_protocol_version_frame(b"B")[0:1] == b"B"


def test_legacy_attribute_request_has_an_empty_value() -> None:
    frame = encode_legacy_attribute(b"B", "numCols")
    assert frame == b"B@\x09\x00`numCols`"
    assert parse_legacy_attribute(frame[4:]) == ("numCols", b"")


def test_legacy_attribute_value_is_kept_raw() -> None:
    assert parse_legacy_attribute(b"`gestureMap`\x80\x04pickle") == (
        "gestureMap",
        b"\x80\x04pickle",
    )


@pytest.mark.parametrize("payload", [b"", b"noname", b"`unterminated", "`é`".encode()])
def test_invalid_legacy_attribute_payloads(payload: bytes) -> None:
    with pytest.raises(InvalidLegacyAttribute):
        parse_legacy_attribute(payload)


# JSON Lines


def test_json_line_puts_type_last() -> None:
    assert encode_json_line("index", index=3) == b'{"index": 3, "type": "index"}\n'


def test_json_line_keeps_non_ascii_escaped() -> None:
    assert encode_json_line("display", text="é") == b'{"text": "\\u00e9", "type": "display"}\n'


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


# Mixed stream


def test_stream_mixes_legacy_frames_and_json_lines() -> None:
    decoder = StreamDecoder(b"S")
    version = legacy_protocol_version_frame(b"S")
    decoder.feed(version + b'{"type":"cancel"}\n\n' + version)
    assert drain(decoder) == [
        LegacyFrame(ord("@"), version[4:]),
        {"type": "cancel"},
        LegacyFrame(ord("@"), version[4:]),
    ]
    assert len(decoder) == 0


def test_stream_items_split_at_every_byte() -> None:
    stream = legacy_protocol_version_frame(b"B") + b'{"type":"display","cells":[1]}\n'
    decoder = StreamDecoder(b"B")
    items = []
    for byte in stream:
        decoder.feed(bytes([byte]))
        items.extend(drain(decoder))
    assert items == [LegacyFrame(ord("@"), stream[4:22]), {"type": "display", "cells": [1]}]


def test_stream_real_fixture_decodes_to_json_messages() -> None:
    decoder = StreamDecoder(b"S")
    decoder.feed(fixture_bytes("first-speak-v2-jsonl.hex"))
    assert [item["type"] for item in drain(decoder)] == ["speak"]


def test_frame_of_the_other_channel_is_invalid() -> None:
    decoder = StreamDecoder(b"S")
    frame = legacy_protocol_version_frame(b"B")
    decoder.feed(frame + b'{"type":"cancel"}\n')
    assert drain(decoder) == [InvalidBytes(frame + b'{"type":"cancel"}\n')]
    assert len(decoder) == 0


def test_invalid_json_line_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    decoder = StreamDecoder(b"S")
    decoder.feed(b'{invalid}\n{"type":"cancel"}\n')
    assert drain(decoder) == [{"type": "cancel"}]
    assert "invalid RDAccess JSON line" in caplog.text


def test_clear_drops_what_follows_the_current_item() -> None:
    decoder = StreamDecoder(b"S")
    decoder.feed(b'{"type":"cancel"}\n{"type":"speak"')
    assert decoder.pop() == {"type": "cancel"}
    assert len(decoder) == len(b'{"type":"speak"')
    decoder.clear()
    assert decoder.pop() is None
