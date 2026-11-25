"""The display size the bridge offers Orca, from the client's dimensions."""

from __future__ import annotations

import pytest

from nvda_rdp_linux.server.braille_bridge import BrailleBridge
from nvda_rdp_linux.server.brlapi_main import default_tablesdir


@pytest.fixture
def bridge(tmp_path) -> BrailleBridge:
    return BrailleBridge(0, str(tmp_path / "key"), default_tablesdir())


def report(bridge: BrailleBridge, **values: object) -> None:
    for attribute, value in values.items():
        bridge._on_message({"type": "attribute_value", "attribute": attribute, "value": value})


def test_num_cells_alone(bridge: BrailleBridge) -> None:
    report(bridge, numCells=40)
    assert bridge.server.display_size == 40


def test_rows_times_columns_wins_over_num_cells(bridge: BrailleBridge) -> None:
    report(bridge, numCells=40, numRows=4, numCols=20)
    assert bridge.server.display_size == 80


def test_num_cells_when_a_dimension_is_zero(bridge: BrailleBridge) -> None:
    report(bridge, numRows=0, numCols=20, numCells=40)
    assert bridge.server.display_size == 40


def test_zero_reports_no_display(bridge: BrailleBridge) -> None:
    report(bridge, numCells=40)
    report(bridge, numCells=0)
    assert bridge.server.display_size == 0


@pytest.mark.parametrize("value", [None, -1, True, "40"])
def test_invalid_values_change_nothing(bridge: BrailleBridge, value: object) -> None:
    report(bridge, numCells=40)
    report(bridge, numCells=value, numRows=value, numCols=value)
    assert bridge.server.display_size == 40


def test_detach_forgets_the_dimensions(bridge: BrailleBridge) -> None:
    report(bridge, numRows=4, numCols=20, numCells=80)
    bridge._on_ready(False)
    report(bridge, numCells=40)
    assert bridge.server.display_size == 40
