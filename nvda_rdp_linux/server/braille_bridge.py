# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 06 (rdaccess_brlapi/bridge.py).

"""The client's NVDA braille display, offered to Orca as a BrlAPI display.

1. Size: the client's ``numCells`` is the display size the server reports.
   Orca waits while it is 0 (orca@7f241c6:src/orca/braille.py:690-722) and
   reads it once otherwise, so a later change closes Orca's connection: Orca
   reconnects within about two seconds (braille.py:123, 770-790) and reads
   the new size.
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
            self._send_command, lambda: computer_table(tablesdir), MutterKeyInjector()
        )
        self._num_cells = 0
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

    # Size

    def _set_size(self, num_cells: object) -> None:
        valid = isinstance(num_cells, int) and not isinstance(num_cells, bool)
        num_cells = num_cells if valid and num_cells > 0 else 0
        self._num_cells = num_cells
        self._last_cells = None
        if not num_cells:
            LOG.info("the client reports no braille display")
            return
        reported = self.server.display_size
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
        if 0 < self._cursor <= size:
            cells[self._cursor - 1] |= CURSOR_DOTS
        return cells

    # Channel

    def _on_ready(self, ready: bool) -> None:
        if not ready:
            self._last_cells = None
            return
        # The legacy push of the gesture map is pickled and skipped; ask for it in JSON.
        self.channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=ATTRIBUTE_GESTURE_MAP)

    def _on_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == MESSAGE_ATTRIBUTE_REQUEST:
            if message.get("attribute") == ATTRIBUTE_TIME_SINCE_INPUT:
                # Required for the client to show cells (draft experiment 02).
                self.channel.send(
                    MESSAGE_ATTRIBUTE_VALUE, attribute=ATTRIBUTE_TIME_SINCE_INPUT, value=0
                )
        elif kind == MESSAGE_ATTRIBUTE_VALUE:
            if message.get("attribute") == ATTRIBUTE_NUM_CELLS:
                self._set_size(message.get("value"))
            elif message.get("attribute") == ATTRIBUTE_GESTURE_MAP:
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
