from __future__ import annotations

import json
from pathlib import Path

from nvda_rdp_linux.client.braille import PROTOCOL_VERSION_2_LEGACY, BrailleProtocolSession
from nvda_rdp_linux.common.rdaccess import CONNECTION_XON

FIXTURES = (
    Path(__file__).parents[1] / "fixtures" / "rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95"
)


def fixture_bytes(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text())


def test_real_braille_legacy_protocol_version_is_answered() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(fixture_bytes("braille-server-protocol-version-v1.hex"))
    assert sent == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]


def test_braille_dimensions_are_advertised_in_json_mode() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, num_cells=40, num_rows=1)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    for attribute in ("numCells", "numRows", "numCols"):
        session.feed(
            json.dumps({"attribute": attribute, "type": "attribute_request"}).encode() + b"\n"
        )
    assert sent[:2] == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]
    assert [json.loads(payload) for payload in sent[2:]] == [
        {"attribute": "numCells", "value": 40, "type": "attribute_value"},
        {"attribute": "numRows", "value": 1, "type": "attribute_value"},
        {"attribute": "numCols", "value": 40, "type": "attribute_value"},
    ]


def test_real_legacy_requests_during_json_transition_are_answered() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, num_cells=40, num_rows=1)
    session.connect()
    session.feed(fixture_bytes("braille-server-protocol-version-v1.hex"))
    session.feed(fixture_bytes("braille-server-num-rows-request-v1.hex")[:7])
    session.feed(fixture_bytes("braille-server-num-rows-request-v1.hex")[7:])
    session.feed(fixture_bytes("braille-server-num-cols-request-v1.hex"))
    session.feed(b'{"version":2,"channel":"NVDA-BRAILLE","type":"protocol_version"}\n')
    assert sent[:2] == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]
    assert [json.loads(payload) for payload in sent[2:]] == [
        {"attribute": "numRows", "value": 1, "type": "attribute_value"},
        {"attribute": "numCols", "value": 40, "type": "attribute_value"},
    ]


def test_reconnect_recovers_when_server_sends_attribute_before_version() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, num_cells=40, num_rows=1)
    session.connect()

    session.feed(fixture_bytes("braille-server-num-rows-request-v1.hex"))

    assert sent[:2] == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]
    assert json.loads(sent[2]) == {
        "attribute": "numRows",
        "value": 1,
        "type": "attribute_value",
    }


def test_fragmented_braille_display_is_logged(caplog) -> None:
    caplog.set_level("INFO")
    session = BrailleProtocolSession()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"display","cells":[1,')
    session.feed(b"2,255]}\n")
    assert "braille display 3 cells: 01 02 ff" in caplog.text


def test_real_first_braille_display_has_40_cells(caplog) -> None:
    caplog.set_level("INFO")
    session = BrailleProtocolSession()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("first-braille-display-v2-jsonl.hex"))
    assert "braille display 40 cells: 43 25 17 11 01 25" in caplog.text


def test_invalid_braille_cells_are_rejected(caplog) -> None:
    session = BrailleProtocolSession()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"display","cells":[1,true,256]}\n')
    assert "invalid braille display cells" in caplog.text


def test_real_display_is_forwarded_to_output() -> None:
    class FakeOutput:
        num_cells = 40
        num_rows = 1

        def __init__(self) -> None:
            self.displays: list[list[int]] = []

        def display(self, cells: list[int]) -> None:
            self.displays.append(cells)

    output = FakeOutput()
    session = BrailleProtocolSession(output=output)
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(fixture_bytes("first-braille-display-v2-jsonl.hex"))
    assert len(output.displays) == 1
    assert len(output.displays[0]) == 40
    assert output.displays[0][:6] == [67, 37, 23, 17, 1, 37]


def test_gesture_map_is_not_fabricated(caplog) -> None:
    caplog.set_level("INFO")
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"attribute":"gestureMap","type":"attribute_request"}\n')
    assert sent == [CONNECTION_XON, PROTOCOL_VERSION_2_LEGACY]
    assert "ignoring gestureMap request" in caplog.text


def test_braille_reconnect_sends_xon_and_resets_partial_json() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"display","cells":[1')
    session.disconnect()

    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"type":"display","cells":[2]}\n')

    assert sent == [
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
        CONNECTION_XON,
        PROTOCOL_VERSION_2_LEGACY,
    ]


def test_gesture_map_is_advertised_when_braille_input_is_enabled() -> None:
    from nvda_rdp_linux.client.brltty import BRLTTY_GESTURE_MAP

    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, gesture_map=BRLTTY_GESTURE_MAP)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    session.feed(b'{"attribute":"gestureMap","type":"attribute_request"}\n')
    name, export = json.loads(sent[-1])["value"]
    assert name == "GlobalGestureMap"
    assert export["globalCommands.GlobalCommands"]["braille_routeTo"] == "br(brltty):route"


def test_gestures_are_sent_as_braille_input() -> None:
    from nvda_rdp_linux.common.braille import BrailleGesture

    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.send_gesture(BrailleGesture("brltty", "route", "bm", routing_index=5))
    session.send_gesture(
        BrailleGesture("brltty", "passdots", "bm", dots=0b11, space=True, is_braille_input=True)
    )
    assert [json.loads(payload) for payload in sent[1:]] == [
        {
            "source": "brltty",
            "id": "route",
            "routingIndex": 5,
            "model": "bm",
            "type": "braille_input",
        },
        {
            "source": "brltty",
            "id": "passdots",
            "routingIndex": None,
            "model": "bm",
            "dots": 3,
            "space": True,
            "type": "braille_input",
        },
    ]


def test_braille_settings_are_empty() -> None:
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    for attribute in ("supportedSettings", "setting_foo", "availableFoos"):
        session.feed(json.dumps({"attribute": attribute, "type": "attribute_request"}).encode())
        session.feed(b"\n")
    assert [json.loads(payload)["value"] for payload in sent[2:]] == [[], None, {}]


def test_dimensions_follow_output_and_can_be_pushed() -> None:
    class ResizableOutput:
        num_cells = 40
        num_rows = 1

        def display(self, cells: list[int]) -> None:
            pass

    output = ResizableOutput()
    sent: list[bytes] = []
    session = BrailleProtocolSession(send=sent.append, output=output)
    session.connect()
    session.feed(PROTOCOL_VERSION_2_LEGACY)
    output.num_cells = 80
    session.push_dimensions()
    assert [json.loads(payload) for payload in sent[2:]] == [
        {"attribute": "numCells", "value": 80, "type": "attribute_value"},
        {"attribute": "numCols", "value": 80, "type": "attribute_value"},
        {"attribute": "numRows", "value": 1, "type": "attribute_value"},
    ]
