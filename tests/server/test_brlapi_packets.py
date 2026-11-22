"""BrlAPI write packets and command codes."""

from __future__ import annotations

import struct

import pytest

from nvda_rdp_linux.server import braille_bridge, braille_input
from nvda_rdp_linux.server.brlapi_server import (
    WF_ATTR_OR,
    WF_CHARSET,
    WF_CURSOR,
    WF_REGION,
    WF_TEXT,
    parse_write,
)


def write_packet(flags: int, *fields: bytes) -> bytes:
    return struct.pack("!I", flags) + b"".join(fields)


def test_text_and_cursor_over_the_whole_display() -> None:
    text = "Été".encode()
    write = parse_write(
        write_packet(WF_TEXT | WF_CURSOR, struct.pack("!I", len(text)), text, struct.pack("!I", 3)),
        40,
    )
    assert (write.region_begin, write.region_size) == (1, 40)
    assert write.text == "Été"
    assert write.cursor == 3


def test_region_with_or_mask() -> None:
    write = parse_write(
        write_packet(
            WF_REGION | WF_TEXT | WF_ATTR_OR,
            struct.pack("!Ii", 10, -3),
            struct.pack("!I", 3),
            b"abc",
            bytes([0xC0, 0, 0x40]),
        ),
        40,
    )
    assert (write.region_begin, write.region_size) == (10, 3)
    assert write.or_mask == bytes([0xC0, 0, 0x40])
    assert write.cursor is None


def test_charset_is_honoured() -> None:
    write = parse_write(
        write_packet(
            WF_TEXT | WF_CHARSET, struct.pack("!I", 1), b"\xe9", bytes([10]), b"ISO-8859-1"
        ),
        40,
    )
    assert write.text == "é"


def test_truncated_packet_raises() -> None:
    with pytest.raises(struct.error):
        parse_write(write_packet(WF_REGION, b"\x00"), 40)


def test_command_codes_match_brltty() -> None:
    brlapi = pytest.importorskip("brlapi")
    assert braille_bridge.KEY_TYPE_CMD == brlapi.KEY_TYPE_CMD
    for name in ("LNUP", "LNDN", "FWINLT", "FWINRT", "HOME", "ROUTE"):
        assert getattr(braille_input, f"KEY_CMD_{name}") == getattr(brlapi, f"KEY_CMD_{name}")
