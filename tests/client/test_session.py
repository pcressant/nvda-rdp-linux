"""Generic RDAccess session behaviour.

Messages built here are synthetic: they follow RDAccess 2.0.5's serializer
format but were not captured from a real session.
"""

from __future__ import annotations

import json

import pytest

from nvda_rdp_linux.client.braille import PROTOCOL_VERSION_2_LEGACY as BRAILLE_VERSION
from nvda_rdp_linux.client.braille import BrailleProtocolSession
from nvda_rdp_linux.client.protocol import PROTOCOL_VERSION_2_LEGACY as SPEECH_VERSION
from nvda_rdp_linux.common.rdaccess import CONNECTION_XON, legacy_protocol_version_frame


def test_legacy_version_frames_match_real_captures() -> None:
    assert legacy_protocol_version_frame(b"S") == bytes.fromhex(
        "53 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"
    )
    assert legacy_protocol_version_frame(b"B") == bytes.fromhex(
        "42 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"
    )


def connected_braille(**kwargs) -> tuple[BrailleProtocolSession, list[bytes]]:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, **kwargs)
    session.connect()
    session.feed(BRAILLE_VERSION)
    return session, sent


def test_versions_are_requested_after_negotiation() -> None:
    _session, sent = connected_braille(request_versions=True)
    assert sent[:2] == [CONNECTION_XON, BRAILLE_VERSION]
    assert [json.loads(payload) for payload in sent[2:]] == [
        {"attribute": "rdAccessVersion", "type": "attribute_request"},
        {"attribute": "nvdaVersion", "type": "attribute_request"},
    ]


def test_version_values_are_recorded() -> None:
    session, _sent = connected_braille()
    session.feed(b'{"attribute":"rdAccessVersion","value":"2.0.5","type":"attribute_value"}\n')
    assert session.peer_versions == {"rdAccessVersion": "2.0.5"}


def test_ping_and_stray_line_feeds_are_accepted(caplog: pytest.LogCaptureFixture) -> None:
    session, sent = connected_braille()
    session.feed(b'\n\n{"type":"ping"}\n\n')
    assert sent == [CONNECTION_XON, BRAILLE_VERSION]
    assert "ignoring unsupported" not in caplog.text


def test_supported_settings_is_answered_empty() -> None:
    session, sent = connected_braille()
    session.feed(b'{"attribute":"supportedSettings","type":"attribute_request"}\n')
    assert json.loads(sent[-1]) == {
        "attribute": "supportedSettings",
        "value": [],
        "type": "attribute_value",
    }


def test_caps_lock_toggle_reaches_callback() -> None:
    states: list[bool] = []
    session, _sent = connected_braille(on_caps_lock_toggle=states.append)
    session.feed(b'{"attribute":"capsLockToggle","value":true,"type":"attribute_value"}\n')
    session.feed(b'{"attribute":"capsLockToggle","value":"yes","type":"attribute_value"}\n')
    assert states == [True]


def test_json_line_concatenated_with_legacy_frame_is_parsed() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(BRAILLE_VERSION + b'{"attribute":"numCells","type":"attribute_request"}\n')
    assert json.loads(sent[-1])["value"] == 40


def test_wrong_channel_protocol_version_is_rejected(caplog: pytest.LogCaptureFixture) -> None:
    session, _sent = connected_braille()
    session.feed(b'{"version":2,"channel":"NVDA-SPEECH","type":"protocol_version"}\n')
    assert "unexpected channel" in caplog.text


def test_legacy_v1_server_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(BRAILLE_VERSION[:-1] + b"\x01")
    assert sent == [CONNECTION_XON]
    assert "RDAccess 1.x" in caplog.text


def test_garbage_is_discarded_and_parsing_recovers(caplog: pytest.LogCaptureFixture) -> None:
    session, sent = connected_braille()
    session.feed(b"\x00\x01garbage")
    session.feed(b'{"attribute":"numRows","type":"attribute_request"}\n')
    assert "discarding invalid" in caplog.text
    assert json.loads(sent[-1])["value"] == 1


def test_speech_legacy_frame_on_braille_channel_is_garbage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, sent = connected_braille()
    session.feed(SPEECH_VERSION)
    assert sent == [CONNECTION_XON, BRAILLE_VERSION]
    assert "discarding invalid" in caplog.text
