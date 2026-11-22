"""``NVDA-BRAILLE`` vocabulary: display cells and display gestures.

Gesture identifiers follow NVDA (nvda@bfe9a2e, GPL-2.0-or-later):
source/inputCore.py:976-992 and source/braille/input/gesture.py:20-60.
Only the rules are reused, no code.
"""

from __future__ import annotations

from dataclasses import dataclass

DRIVER_TYPE = b"B"
CHANNEL_NAME = "NVDA-BRAILLE"

MESSAGE_DISPLAY = "display"
MESSAGE_BRAILLE_INPUT = "braille_input"

ATTRIBUTE_NUM_CELLS = "numCells"
ATTRIBUTE_NUM_COLS = "numCols"
ATTRIBUTE_NUM_ROWS = "numRows"
ATTRIBUTE_GESTURE_MAP = "gestureMap"

GESTURE_MAP_TYPE = "GlobalGestureMap"
"""First item of the ``gestureMap`` value: ``[GESTURE_MAP_TYPE, export()]``."""


def normalize_gesture(identifier: str) -> str:
    """NVDA's normalized gesture identifier (``inputCore.normalizeGestureIdentifier``).

    ``"br(brltty):Route"`` becomes ``"br(brltty):route"``, and the keys of a
    chord are sorted: ``"bk:space+dot1"`` becomes ``"bk:dot1+space"``.
    Raises ValueError when there is no ``:``.
    """
    prefix, main = identifier.lower().split(":", 1)
    return f"{prefix}:{'+'.join(sorted(main.split('+')))}"


def dots_gesture(dots: int, space: bool) -> str:
    """The ``bk:`` identifier of braille keyboard input (``BrailleInputGesture._makeDotsId``)."""
    keys = [f"dot{i + 1}" for i in range(8) if dots & (1 << i)]
    if space:
        keys.append("space")
    return normalize_gesture("bk:" + "+".join(keys))


def parse_cells(value: object) -> list[int] | None:
    """The cells of a ``display`` message, or None when they are invalid."""
    if not isinstance(value, list) or not all(
        isinstance(cell, int) and not isinstance(cell, bool) and 0 <= cell <= 255 for cell in value
    ):
        return None
    return value


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

    def fields(self) -> dict[str, object]:
        """The message fields, in the order RDAccess sends them."""
        fields: dict[str, object] = {
            "source": self.source,
            "id": self.id,
            "routingIndex": self.routing_index,
            "model": self.model,
        }
        if self.is_braille_input:
            fields["dots"] = self.dots
            fields["space"] = self.space
        return fields
