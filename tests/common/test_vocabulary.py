"""Speech and braille vocabulary shared by the client and the server."""

from __future__ import annotations

import pytest

from nvda_rdp_linux.common.braille import BrailleGesture, parse_cells
from nvda_rdp_linux.common.speech import (
    COMMAND_PITCH,
    COMMAND_VOLUME,
    Prosody,
    is_number,
    parse_prosody,
    prosody_command,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, True),
        (0.5, True),
        (True, False),
        (float("nan"), False),
        (float("inf"), False),
        ("1", False),
    ],
)
def test_is_number(value: object, expected: bool) -> None:
    assert is_number(value) is expected


def test_prosody_command_round_trip() -> None:
    for prosody in (Prosody(), Prosody(offset=10), Prosody(multiplier=0.5)):
        name, parameters = prosody_command(COMMAND_VOLUME, prosody)
        assert name == COMMAND_VOLUME
        assert parameters["isDefault"] is prosody.is_default
        assert parse_prosody(parameters) == prosody


def test_default_prosody_command_matches_nvda() -> None:
    assert prosody_command(COMMAND_PITCH) == [
        COMMAND_PITCH,
        {"_offset": 0, "_multiplier": 1, "isDefault": True},
    ]


@pytest.mark.parametrize(
    "parameters", [{"_offset": "1"}, {"_multiplier": -1}, {"_multiplier": float("nan")}]
)
def test_invalid_prosody_parameters(parameters: dict[str, object]) -> None:
    assert parse_prosody(parameters) is None


def test_parse_cells() -> None:
    assert parse_cells([0, 1, 255]) == [0, 1, 255]
    assert parse_cells([]) == []
    for invalid in (None, "01", [256], [-1], [True], [1.0]):
        assert parse_cells(invalid) is None


def test_gesture_fields_order_and_braille_input() -> None:
    key = BrailleGesture("brltty", "route", "al", 5)
    assert list(key.fields().items()) == [
        ("source", "brltty"),
        ("id", "route"),
        ("routingIndex", 5),
        ("model", "al"),
    ]
    typed = BrailleGesture("brltty", "passdots", dots=3, space=True, is_braille_input=True)
    assert typed.fields()["dots"] == 3
    assert typed.fields()["space"] is True
