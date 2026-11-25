# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 06 (rdaccess_brlapi/bridge.py).

"""The client's NVDA braille display, offered to Orca as a BrlAPI display.

1. Size: as RDAccess's remote driver (rdAccess 2.1.0:
   addon/brailleDisplayDrivers/remote.py, ``_get_numCells``), the client's
   ``numRows`` times ``numCols`` when both are known, else its ``numCells``.
   ``numCells`` is requested when the channel is ready; all three are taken
   from any push.  Orca handles one line only, so a display of several rows
   is offered as one line of all its cells, row after row, which is also
   how RDAccess sends them.  Orca waits while the size is 0, asking every
   500 ms (orca@7f241c6:src/orca/braille.py:690-722), and reads it once
   otherwise, so a change between two nonzero sizes closes Orca's
   connection: Orca reconnects within about two seconds (braille.py:123,
   770-790) and reads the new size.  A size of 0 (no display) is reported
   too: Orca reconnects, waits, and takes the next display's size without
   another reconnection.
2. Output: Orca writes the visible text, attributes and cursor; the text
   becomes dots with liblouis, with Orca's contraction table when contracted
   braille is on (``orca_braille``), else a computer braille table.
3. Input: ``braille_input``.  Navigation becomes BrlAPI command keys sent to
   Orca; typing and ``kb:`` gestures are injected through Mutter.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import louis

from ..common.braille import (
    ATTRIBUTE_GESTURE_MAP,
    ATTRIBUTE_NUM_CELLS,
    ATTRIBUTE_NUM_COLS,
    ATTRIBUTE_NUM_ROWS,
    CHANNEL_NAME,
    DRIVER_TYPE,
    MESSAGE_BRAILLE_INPUT,
    MESSAGE_DISPLAY,
)
from ..common.rdaccess import (
    ATTRIBUTE_TIME_SINCE_INPUT,
    MESSAGE_ATTRIBUTE_REQUEST,
    MESSAGE_ATTRIBUTE_VALUE,
)
from .braille_input import BrailleInput
from .brlapi_server import BrlapiServer, Write
from .channel import Channel
from .input_time import InputTime
from .mutter_input import MutterKeyInjector
from .orca_braille import OrcaBrailleSettings

LOG = logging.getLogger(__name__)

SOCKET_NAME = "nvda-braille.sock"
# Computer braille tables used when contracted braille is off.
COMPUTER_TABLES = {"fr": "fr-bfu-comp8.utb", "en": "en-us-comp8.ctb"}
DEFAULT_COMPUTER_TABLE = "en-us-comp8.ctb"
CURSOR_DOTS = 0xC0  # dots 7 and 8
KEY_TYPE_CMD = 0x20000000  # brltty: Headers/brlapi_constants.h, BRLAPI_KEY_TYPE_CMD


def computer_table(tablesdir: str) -> str:
    lang = (os.environ.get("LANG") or "en").split("_")[0].split(".")[0].lower()
    return os.path.join(tablesdir, COMPUTER_TABLES.get(lang, DEFAULT_COMPUTER_TABLE))


def char_to_dots(table: str, text: str) -> list[int]:
    """The cells of each character of ``text``, without contraction.

    liblouis 3.38's Python binding sizes the result of ``charToDots`` as the
    input's length divided by the size of a liblouis character, so that only
    the first quarter comes back.  Padding the input with spaces to that
    many characters works around it, and is harmless with a correct binding.
    """
    padded = text + " " * ((louis.wideCharBytes - 1) * len(text))
    dots = louis.charToDots([table], padded, louis.ucBrl)[: len(text)]
    return [ord(char) - 0x2800 if 0x2800 <= ord(char) <= 0x28FF else 0 for char in dots]


class BrailleBridge:
    def __init__(self, port: int, key_path: str, tablesdir: str) -> None:
        self._tablesdir = tablesdir
        self.server = BrlapiServer(port, key_path, self._on_write)
        self.channel = Channel(
            CHANNEL_NAME, DRIVER_TYPE, SOCKET_NAME, self._on_message, self._on_ready
        )
        self.settings = OrcaBrailleSettings(tablesdir, self._render_again)
        self.input = BrailleInput(
            self._send_command,
            lambda: computer_table(tablesdir),
            MutterKeyInjector(),
            self._input_contraction_table,
            self._render_again,
        )
        self._input_time = InputTime()
        self._num_cells = 0
        self._dimensions: dict[str, int] = {}
        self._last_write: Write | None = None
        self._cursor = 0
        self._last_cells: list[int] | None = None

    def start(self) -> None:
        self.settings.start()
        self.server.start()
        self.channel.start()

    def stop(self) -> None:
        self.channel.stop()
        self.server.stop()
        self.input.injector.close()

    def _input_contraction_table(self) -> str | None:
        """Orca's contraction table while contracted braille is on, for typing."""
        if not self.settings.contracted:
            return None
        return self.settings.contraction_table() or None

    # Size

    def _set_dimension(self, attribute: str, value: object) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            # Not "no display" (0): an invalid value changes nothing.
            LOG.warning("ignoring invalid %s=%r", attribute, value)
            return
        self._dimensions[attribute] = value
        rows = self._dimensions.get(ATTRIBUTE_NUM_ROWS, 0)
        cols = self._dimensions.get(ATTRIBUTE_NUM_COLS, 0)
        size = rows * cols or self._dimensions.get(ATTRIBUTE_NUM_CELLS, 0)
        if size != self._num_cells or attribute == ATTRIBUTE_NUM_CELLS:
            self._set_size(size)

    def _set_size(self, num_cells: int) -> None:
        self._num_cells = num_cells
        self._last_cells = None
        reported = self.server.display_size
        if not num_cells:
            LOG.info("the client reports no braille display")
            if reported:
                self.server.display_size = 0
                if self.server.has_clients:
                    LOG.info("Orca must reconnect and wait for a display")
                    self._last_write = None
                    self.server.disconnect_all()
            return
        self.server.display_size = num_cells
        if reported == num_cells:
            self._render_again()
        elif reported and self.server.has_clients:
            LOG.info("client display: %d cells (was %d), Orca must reconnect", num_cells, reported)
            self._last_write = None
            self.server.disconnect_all()
        else:
            LOG.info("client display: %d cells", num_cells)

    # Output

    def _on_write(self, write: Write) -> None:
        self.settings.maybe_refresh()
        if write.cursor is not None:
            self._cursor = write.cursor
        self._last_write = write
        self._render(write)

    def _render_again(self) -> None:
        if self._last_write is not None:
            self._last_cells = None
            self._render(self._last_write)

    def _render(self, write: Write) -> None:
        size = self.server.display_size
        if not self.channel.ready or not self._num_cells or size != self._num_cells:
            return
        try:
            cells = self._to_cells(write, size)
        except RuntimeError:
            LOG.exception("cannot convert braille")
            return
        if cells != self._last_cells:
            self._last_cells = cells
            self.channel.send(MESSAGE_DISPLAY, cells=cells)

    def _to_cells(self, write: Write, size: int) -> list[int]:
        cells = [0] * size
        begin = max(1, write.region_begin) - 1
        text = (write.text or "")[: max(0, size - begin)]
        if text:
            if self.settings.contracted:
                table = self.settings.contraction_table() or computer_table(self._tablesdir)
            else:
                table = computer_table(self._tablesdir)
            for i, value in enumerate(char_to_dots(table, text)):
                cells[begin + i] = value
        if write.or_mask:
            for i, value in enumerate(write.or_mask[: size - begin]):
                cells[begin + i] |= value
        # BrlAPI cursor: 0 for none, else the 1-based cell.
        cursor = self._cursor
        pending = self.input.pending
        if pending and 0 < cursor <= size:
            # Contracted input not translated yet: shown at the cursor, which
            # moves after it, as NVDA shows its input buffer.
            cells = (cells[: cursor - 1] + pending + cells[cursor - 1 :])[:size]
            cursor += len(pending)
        if 0 < cursor <= size:
            cells[cursor - 1] |= CURSOR_DOTS
        return cells

    # Channel

    def _on_ready(self, ready: bool) -> None:
        if not ready:
            # The next client may have another display: wait for its size and map.
            self._num_cells = 0
            self._dimensions = {}
            self._last_cells = None
            self.input.clear_gesture_map()
            self.input.discard_pending()
            return
        # Ask for the size, as RDAccess's remote braille driver does on init
        # (rdAccess@19f5c53: addon/brailleDisplayDrivers/remote.py:28-32):
        # a client that only answers requests never pushes it.
        self.channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=ATTRIBUTE_NUM_CELLS)
        # The legacy push of the gesture map is pickled and skipped; ask for it in JSON.
        self.channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=ATTRIBUTE_GESTURE_MAP)

    def _on_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == MESSAGE_ATTRIBUTE_REQUEST:
            if message.get("attribute") == ATTRIBUTE_TIME_SINCE_INPUT:
                # The client takes the remote session as focused while this is at most 200 ms.
                self.channel.send(
                    MESSAGE_ATTRIBUTE_VALUE,
                    attribute=ATTRIBUTE_TIME_SINCE_INPUT,
                    value=self._input_time.since_input(self.channel.attached_at),
                )
        elif kind == MESSAGE_ATTRIBUTE_VALUE:
            attribute = message.get("attribute")
            if attribute in (ATTRIBUTE_NUM_CELLS, ATTRIBUTE_NUM_ROWS, ATTRIBUTE_NUM_COLS):
                self._set_dimension(attribute, message.get("value"))
            elif attribute == ATTRIBUTE_GESTURE_MAP:
                self.input.set_gesture_map(message.get("value"))
        elif kind == MESSAGE_BRAILLE_INPUT:
            try:
                self.input.handle(message)
            except Exception:
                # One bad key must not drop the messages that follow it.
                LOG.exception("cannot handle braille input")

    # Input

    def _send_command(self, command: int, argument: int) -> None:
        LOG.debug("BrlAPI command %#x argument %d", command, argument)
        self.server.send_key(KEY_TYPE_CMD | command | argument)
