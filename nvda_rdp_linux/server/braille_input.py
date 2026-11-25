# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 05 (rdaccess_bridge/braille_input.py).
# Generic braille keyboard bindings follow NVDA (nvda@bfe9a2e, GPL-2.0-or-later):
# source/globalCommands.py:4365-4425. Only names are reused, no code.

"""Braille display keys, resolved the way NVDA would.

1. The client sends its display driver's gesture map, merged with the user's
   own NVDA gestures (rdAccess handlers/remoteBrailleHandler.py:75-85).
2. A key is looked up by its identifiers in NVDA's order
   (nvda@d104e9b: source/braille/display/gesture.py ``_get_identifiers``,
   source/braille/input/gesture.py ``_get_identifiers``):
   ``br(<source>.<model>):<id>`` before ``br(<source>):<id>``, each preceded
   by its form with the 1-based cell number for a routing key; then, for
   braille keyboard input, ``bk:`` identifiers: the exact chord and
   ``bk:space+dots`` for space with dots, the exact dots and ``bk:dots``
   for dots 7 and/or 8, only ``bk:dots`` for other dots.
3. The script name decides what happens:
   - ``kb:<keys>``: a key press, injected through Mutter;
   - braille navigation scripts: a BrlAPI command for Orca;
   - ``braille_dots`` or plain dots: typed through Mutter after
     back-translation with liblouis, as NVDA's braille input does
     (nvda@bfe9a2e: source/brailleInput.py):
     - contracted braille off in Orca: each cell at once, with a computer
       braille table;
     - contracted braille on: with Orca's contraction table, the cells wait
       until a space, Enter, dots 7-8 (``braille_translate``) or any other
       action, then the word is typed.  Dot 7 (``braille_eraseLastCell``)
       removes the last waiting cell, or erases a typed character when none
       waits.  The waiting cells are shown at the cursor, as NVDA does.
   - ``braille_toggleShift`` and the other modifier toggles: as NVDA's
     braille input (nvda@d104e9b: source/braille/input/inputHandler.py),
     the modifiers are held for the next ``kb:`` key or the next typed
     cell, a single character in computer braille; a space, Enter or dots
     7-8 releases them.  The toggles with the NVDA key are not supported:
     which key is Orca's modifier is a setting of Orca.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Protocol

import louis

from ..common.braille import dots_gesture, normalize_gesture

LOG = logging.getLogger(__name__)

_UNTRANSLATED = re.compile(r"\\[0-9]+/")

# Generic braille keyboard bindings of NVDA's global commands.
GENERIC_BINDINGS = {
    normalize_gesture("bk:dot7"): "braille_eraseLastCell",
    normalize_gesture("bk:dot8"): "braille_enter",
    normalize_gesture("bk:dot7+dot8"): "braille_translate",
    "bk:dots": "braille_dots",
}
GENERIC_DOTS = "bk:dots"
GENERIC_SPACE_DOTS = normalize_gesture("bk:space+dots")
DOT7, DOT8 = 0x40, 0x80

# NVDA's braille input modifier toggles -> the modifiers they toggle.
MODIFIER_TOGGLES = {
    "braille_toggleShift": ("shift",),
    "braille_toggleControl": ("control",),
    "braille_toggleAlt": ("alt",),
    "braille_toggleWindows": ("windows",),
    "braille_toggleControlShift": ("control", "shift"),
    "braille_toggleAltShift": ("alt", "shift"),
    "braille_toggleWindowsShift": ("windows", "shift"),
    "braille_toggleControlAlt": ("control", "alt"),
    "braille_toggleControlAltShift": ("control", "alt", "shift"),
}

# BrlAPI command codes (brltty: Headers/brlapi_constants.h), the ones Orca
# handles (orca@7f241c6: src/orca/braille.py:159-178).
KEY_CMD_LNUP = 0x01
KEY_CMD_LNDN = 0x02
KEY_CMD_FWINLT = 0x17
KEY_CMD_FWINRT = 0x18
KEY_CMD_HOME = 0x1D
KEY_CMD_ROUTE = 0x10000

# NVDA script -> BrlAPI command handled by Orca.
NAVIGATION_COMMANDS = {
    "braille_scrollBack": KEY_CMD_FWINLT,
    "braille_scrollForward": KEY_CMD_FWINRT,
    "braille_previousLine": KEY_CMD_LNUP,
    "braille_nextLine": KEY_CMD_LNDN,
    "braille_toFocus": KEY_CMD_HOME,
}

# NVDA key names (as in "kb:" gestures) -> X keysyms.
KEY_NAMES = {
    "uparrow": 0xFF52,
    "downarrow": 0xFF54,
    "leftarrow": 0xFF51,
    "rightarrow": 0xFF53,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "tab": 0xFF09,
    "enter": 0xFF0D,
    "escape": 0xFF1B,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "insert": 0xFF63,
    "space": 0x0020,
    "applications": 0xFF67,
    "shift": 0xFFE1,
    "control": 0xFFE3,
    "alt": 0xFFE9,
    "windows": 0xFFEB,
    "capslock": 0xFFE5,
    "numlock": 0xFF7F,
    "printscreen": 0xFF61,
    **{f"f{n}": 0xFFBE + n - 1 for n in range(1, 25)},
}
MODIFIERS = {"shift", "control", "alt", "windows"}


def char_keysym(char: str) -> int:
    """The X keysym that types one character."""
    code = ord(char)
    if char == "\n":
        return KEY_NAMES["enter"]
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code
    return 0x01000000 + code


class KeyInjector(Protocol):
    def press(self, keysyms: list[int]) -> None: ...

    def close(self) -> None: ...


class BrailleInput:
    """Turns ``braille_input`` messages into Orca commands or keys."""

    def __init__(
        self,
        send_orca_command: Callable[[int, int], None],
        input_table: Callable[[], str],
        injector: KeyInjector,
        contraction_table: Callable[[], str | None] = lambda: None,
        on_pending_changed: Callable[[], None] = lambda: None,
    ) -> None:
        self._send_orca_command = send_orca_command
        self._input_table = input_table
        self._contraction_table = contraction_table
        self._on_pending_changed = on_pending_changed
        self.injector = injector
        self._bindings: dict[str, str] = {}
        self._pending: list[int] = []
        self._pending_table = ""
        self._modifiers: set[str] = set()
        self._reported_scripts: set[str] = set()

    @property
    def pending(self) -> list[int]:
        """The cells typed in contracted braille and not translated yet."""
        return list(self._pending)

    def discard_pending(self) -> None:
        self._pending = []

    def set_gesture_map(self, value: Any) -> None:
        """Value as JSON-encoded by RDAccess: ``[GESTURE_MAP_TYPE, export()]``."""
        if isinstance(value, list) and len(value) == 2:
            value = value[1]
        bindings: dict[str, str] = {}
        if isinstance(value, dict):
            for section in value.values():
                if not isinstance(section, dict):
                    continue
                for script, gestures in section.items():
                    if isinstance(gestures, str):
                        gestures = [gestures]
                    if not isinstance(gestures, list):
                        continue
                    for gesture in gestures:
                        try:
                            bindings[normalize_gesture(gesture)] = script
                        except (AttributeError, ValueError):
                            continue
        self._bindings = bindings
        LOG.info("gesture map from the client: %d gestures", len(bindings))

    def clear_gesture_map(self) -> None:
        self._bindings = {}

    @property
    def modifiers(self) -> set[str]:
        """The modifiers held by the braille modifier toggles."""
        return set(self._modifiers)

    def _lookup(self, message: dict[str, Any]) -> str | None:
        identifiers = []
        source, gesture_id = message.get("source"), message.get("id")
        if isinstance(source, str) and isinstance(gesture_id, str) and source and gesture_id:
            sources = [f"br({source}):"]
            model = message.get("model")
            if isinstance(model, str) and model:
                sources.insert(0, f"br({source}.{model}):")
            routing_index = message.get("routingIndex")
            indexed = "+" not in gesture_id and _is_index(routing_index)
            for prefix in sources:
                if indexed:
                    identifiers.append(f"{prefix}{gesture_id}{routing_index + 1}")
                identifiers.append(f"{prefix}{gesture_id}")
        dots = message.get("dots")
        if isinstance(dots, int) and not isinstance(dots, bool):
            space = bool(message.get("space"))
            if space and dots:
                identifiers += [dots_gesture(dots, True), GENERIC_SPACE_DOTS]
            elif dots in (DOT7, DOT8, DOT7 | DOT8):
                identifiers += [dots_gesture(dots, False), GENERIC_DOTS]
            elif dots or space:
                identifiers.append(GENERIC_DOTS)
        for identifier in (normalize_gesture(i) for i in identifiers):
            script = self._bindings.get(identifier) or GENERIC_BINDINGS.get(identifier)
            if script and script != "None":
                return script
        return None

    def handle(self, message: dict[str, Any]) -> None:
        script = self._lookup(message)
        dots = message.get("dots") or 0
        space = bool(message.get("space"))
        LOG.debug("key %s dots=%#x space=%s: %s", message.get("id"), dots, space, script)

        cell = script in (None, "braille_dots") and "dots" in message and not (space and dots)
        if script in MODIFIER_TOGGLES:
            self.flush()
            self._modifiers ^= set(MODIFIER_TOGGLES[script])
            LOG.info("braille input modifiers: %s", "+".join(sorted(self._modifiers)) or "none")
            return
        if cell and dots and self._modifiers:
            self.flush()
            self._type_modified_cell(dots)
            return
        if cell and dots:
            table = self._contraction_table()
            if table:
                self._wait(dots, table)
                return
        if script == "braille_eraseLastCell" and self._pending:
            self._pending.pop()
            self._on_pending_changed()
            return
        # Anything else types the waiting word first.
        self.flush()

        routing_index = message.get("routingIndex")
        if script == "braille_routeTo" and isinstance(routing_index, int):
            self._send_orca_command(KEY_CMD_ROUTE, routing_index)
        elif script in NAVIGATION_COMMANDS:
            self._send_orca_command(NAVIGATION_COMMANDS[script], 0)
        elif script and script.startswith("kb:"):
            keys = script[3:]
            if self._modifiers:
                keys = "+".join([*sorted(self._modifiers), keys])
                self._modifiers.clear()
            self._press_named(keys)
        elif script == "braille_eraseLastCell":
            self.injector.press([KEY_NAMES["backspace"]])
        elif script == "braille_enter":
            self._modifiers.clear()
            self.injector.press([KEY_NAMES["enter"]])
        elif script == "braille_translate":
            self._modifiers.clear()  # The waiting word, if any, is typed above.
        elif cell:
            if not dots:
                self._modifiers.clear()
            self._type_cell(dots)
        elif script and script not in self._reported_scripts:
            # Once per script: the log tells why a gesture does nothing.
            self._reported_scripts.add(script)
            LOG.info("NVDA script %s has no equivalent here", script)
        else:
            LOG.debug("no action for %s", script)

    def _press_named(self, keys: str) -> None:
        keysyms = []
        names = keys.split("+")
        # Modifiers first, as in NVDA's KeyboardInputGesture.fromName.
        for name in sorted(names, key=lambda n: n.lower() not in MODIFIERS):
            lower = name.lower()
            if lower in KEY_NAMES:
                keysyms.append(KEY_NAMES[lower])
            elif len(name) == 1:
                keysyms.append(char_keysym(lower))
            else:
                LOG.warning("unknown key name %s in gesture kb:%s", name, keys)
                return
        self.injector.press(keysyms)

    def _wait(self, dots: int, table: str) -> None:
        if self._pending and table != self._pending_table:
            self.flush()
        self._pending_table = table
        self._pending.append(dots)
        self._on_pending_changed()

    def flush(self) -> None:
        """Type the waiting cells, back-translated as one word."""
        if not self._pending:
            return
        cells = "".join(chr(0x8000 | dots) for dots in self._pending)
        self._pending = []
        self._on_pending_changed()
        text = louis.backTranslateString([self._pending_table], cells, mode=louis.dotsIO)
        LOG.debug("contracted word: %r", text)
        # liblouis writes dots it cannot translate as a backslash, the dots and a slash.
        text = _UNTRANSLATED.sub("", text)
        for char in text:
            self.injector.press([char_keysym(char)])

    def _type_modified_cell(self, dots: int) -> None:
        """One cell with the held modifiers, as one key (NVDA's emulateKey)."""
        modifiers = sorted(self._modifiers)
        self._modifiers.clear()
        text = louis.backTranslateString(
            [self._input_table()], chr(0x8000 | dots), mode=louis.dotsIO
        )
        if len(text) != 1:
            LOG.info("dots %#x with %s: no single key", dots, "+".join(modifiers))
            return
        self.injector.press([KEY_NAMES[m] for m in modifiers] + [char_keysym(text.lower())])

    def _type_cell(self, dots: int) -> None:
        if dots == 0:
            self.injector.press([KEY_NAMES["space"]])
            return
        text = louis.backTranslateString(
            [self._input_table()], chr(0x8000 | dots), mode=louis.dotsIO
        )
        if not text or text.startswith("\\"):
            LOG.debug("no character for dots %#x", dots)
            return
        for char in text:
            self.injector.press([char_keysym(char)])


def _is_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
