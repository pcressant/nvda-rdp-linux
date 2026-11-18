"""RDAccess ``NVDA-BRAILLE`` client: the Linux counterpart of RDAccess's
``remoteBrailleHandler``.

The remote NVDA sends ``display`` cell arrays; this client shows them on the
local display and sends the display's keys back as ``braille_input``
gestures, interpreted remotely through the ``gestureMap`` advertised here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .session import (
    CONNECTION_XON,
    MISSING,
    RdAccessSession,
    legacy_protocol_version_frame,
)

LOG = logging.getLogger(__name__)

__all__ = [
    "CONNECTION_XON",
    "PROTOCOL_VERSION_2_LEGACY",
    "BrailleGesture",
    "BrailleProtocolSession",
]

DRIVER_TYPE = b"B"
CHANNEL_NAME = "NVDA-BRAILLE"
PROTOCOL_VERSION_2_LEGACY = legacy_protocol_version_frame(DRIVER_TYPE)
MESSAGE_DISPLAY = "display"
MESSAGE_BRAILLE_INPUT = "braille_input"
ATTRIBUTE_NUM_CELLS = "numCells"
ATTRIBUTE_NUM_COLS = "numCols"
ATTRIBUTE_NUM_ROWS = "numRows"
ATTRIBUTE_GESTURE_MAP = "gestureMap"


def _check_dimensions(num_cells: int, num_rows: int) -> None:
    if num_cells <= 0 or num_cells > 255:
        raise ValueError("num_cells must be between 1 and 255")
    if num_rows <= 0 or num_cells % num_rows:
        raise ValueError("num_rows must divide num_cells")


class BrailleOutput:
    num_cells: int
    num_rows: int

    def display(self, cells: list[int]) -> None: ...


@dataclass(frozen=True)
class BrailleGesture:
    """Fields of RDAccess's ``braille_input`` message (``GESTURE_FIELDS``).

    ``dots``/``space`` are only sent for braille keyboard input, as
    ``remoteBrailleHandler._handleExecuteGesture`` does for NVDA's
    ``BrailleInputGesture``.
    """

    source: str
    id: str
    model: str | None = None
    routing_index: int | None = None
    dots: int = 0
    space: bool = False
    is_braille_input: bool = False


class BrailleProtocolSession(RdAccessSession):
    """Negotiate JSON v2, show cells and forward display keys."""

    DRIVER_TYPE = DRIVER_TYPE
    CHANNEL_NAME = CHANNEL_NAME
    KIND = "braille"

    def __init__(
        self,
        *,
        send: Callable[[bytes], None] | None = None,
        num_cells: int = 40,
        num_rows: int = 1,
        output: BrailleOutput | None = None,
        gesture_map: dict[str, dict[str, object]] | None = None,
        request_versions: bool = False,
        on_caps_lock_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(
            send=send,
            request_versions=request_versions,
            on_caps_lock_toggle=on_caps_lock_toggle,
        )
        if output is None:
            _check_dimensions(num_cells, num_rows)
        self._output = output
        self._num_cells = num_cells
        self._num_rows = num_rows
        self._gesture_map = gesture_map

    def _dimensions(self) -> tuple[int, int]:
        """Current (cells, rows); read from the output, whose size may change."""
        if self._output is None:
            return self._num_cells, self._num_rows
        return self._output.num_cells, self._output.num_rows

    def push_dimensions(self) -> None:
        """Announce a new display size, like ``_handleDisplayDimensionChanges``."""
        for attribute in (ATTRIBUTE_NUM_CELLS, ATTRIBUTE_NUM_COLS, ATTRIBUTE_NUM_ROWS):
            value = self._attribute_value(attribute)
            if self.send_attribute_value(attribute, value):
                LOG.info("pushed braille attribute %s=%r", attribute, value)

    def _handle_channel_message(self, message_type: object, message: dict[str, Any]) -> bool:
        if message_type != MESSAGE_DISPLAY:
            return False
        cells = message.get("cells")
        if not isinstance(cells, list) or not all(
            isinstance(cell, int) and not isinstance(cell, bool) and 0 <= cell <= 255
            for cell in cells
        ):
            LOG.error("invalid braille display cells=%r", cells)
            return True
        LOG.debug("braille display %d cells: %s", len(cells), " ".join(f"{c:02x}" for c in cells))
        if self._output is not None:
            self._output.display(cells)
        return True

    def _attribute_value(self, attribute: str) -> object:
        num_cells, num_rows = self._dimensions()
        values = {
            ATTRIBUTE_NUM_CELLS: num_cells,
            ATTRIBUTE_NUM_COLS: num_cells // num_rows,
            ATTRIBUTE_NUM_ROWS: num_rows,
        }
        if attribute in values:
            return values[attribute]
        if attribute == ATTRIBUTE_GESTURE_MAP:
            if self._gesture_map is None:
                LOG.info("ignoring gestureMap request: braille input is not enabled")
                return MISSING
            return ["GlobalGestureMap", self._gesture_map]
        if attribute.startswith("setting_"):
            return None
        if attribute.startswith("available") and attribute.endswith("s"):
            return {}
        return super()._attribute_value(attribute)

    def send_gesture(self, gesture: BrailleGesture) -> bool:
        fields: dict[str, object] = {
            "source": gesture.source,
            "id": gesture.id,
            "routingIndex": gesture.routing_index,
            "model": gesture.model,
        }
        if gesture.is_braille_input:
            fields["dots"] = gesture.dots
            fields["space"] = gesture.space
        sent = self.send_message(MESSAGE_BRAILLE_INPUT, **fields)
        if sent:
            LOG.debug("sent braille gesture %s", fields)
        return sent
