"""Braille display keys and braille typing, resolved with the client's gesture map."""

from __future__ import annotations

import pytest

from nvda_rdp_linux.server.braille_bridge import computer_table
from nvda_rdp_linux.server.braille_input import (
    KEY_CMD_FWINLT,
    KEY_CMD_HOME,
    KEY_CMD_ROUTE,
    KEY_NAMES,
    BrailleInput,
    char_keysym,
)
from nvda_rdp_linux.server.brlapi_main import default_tablesdir

GESTURE_MAP = [
    "GlobalGestureMap",
    {
        "globalCommands.GlobalCommands": {
            "braille_scrollBack": "br(brltty):fwinlt",
            "braille_toFocus": ["br(brltty):Home"],
            "braille_routeTo": "br(brltty):route",
            "kb:control+home": "br(brltty):top",
            "kb:shift+tab": ["br(brltty):bot", "bk:space+dot1+dot2"],
            "kb:nosuchkey": "br(brltty):bad",
            "None": "br(brltty):unbound",
            "ignored": [42, "no colon"],
        },
        "notASection": 3,
    },
]


class FakeInjector:
    def __init__(self) -> None:
        self.presses: list[list[int]] = []

    def press(self, keysyms: list[int]) -> None:
        self.presses.append(keysyms)

    def close(self) -> None:
        pass


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    commands: list[tuple[int, int]] = []
    injector = FakeInjector()
    tables = default_tablesdir()
    handler = BrailleInput(
        lambda command, argument: commands.append((command, argument)),
        lambda: computer_table(tables),
        injector,
    )
    handler.set_gesture_map(GESTURE_MAP)
    return handler, commands, injector


def key(gesture_id: str, **fields: object) -> dict[str, object]:
    return {"type": "braille_input", "source": "brltty", "id": gesture_id, **fields}


def dots(value: int, space: bool = False) -> dict[str, object]:
    return key("passdots", dots=value, space=space)


def test_navigation_and_routing(setup) -> None:
    handler, commands, injector = setup
    handler.handle(key("fwinlt"))
    handler.handle(key("HOME"))
    handler.handle(key("route", routingIndex=7))
    handler.handle(key("route"))
    assert commands == [(KEY_CMD_FWINLT, 0), (KEY_CMD_HOME, 0), (KEY_CMD_ROUTE, 7)]
    assert injector.presses == []


def test_keyboard_emulation_presses_modifiers_first(setup) -> None:
    handler, _commands, injector = setup
    handler.handle(key("top"))
    handler.handle(dots(0x03, space=True))
    assert injector.presses == [
        [KEY_NAMES["control"], KEY_NAMES["home"]],
        [KEY_NAMES["shift"], KEY_NAMES["tab"]],
    ]


def test_unknown_key_name_and_unbound_gestures_do_nothing(setup) -> None:
    handler, commands, injector = setup
    for gesture_id in ("bad", "unbound", "missing"):
        handler.handle(key(gesture_id))
    handler.handle({"type": "braille_input"})
    assert commands == []
    assert injector.presses == []


def test_typing_cells_space_erase_and_enter(setup) -> None:
    handler, _commands, injector = setup
    handler.handle(dots(0x01))
    handler.handle(dots(0x3F))
    handler.handle(dots(0x00, space=True))
    handler.handle(dots(0x40))
    handler.handle(dots(0x80))
    handler.handle(dots(0xC0))
    assert injector.presses == [
        [ord("a")],
        [ord("é")],
        [KEY_NAMES["space"]],
        [KEY_NAMES["backspace"]],
        [KEY_NAMES["enter"]],
    ]


def test_invalid_gesture_map_clears_the_bindings(setup) -> None:
    handler, commands, _injector = setup
    handler.set_gesture_map("not a map")
    handler.handle(key("fwinlt"))
    assert commands == []


def test_char_keysym() -> None:
    assert char_keysym("a") == ord("a")
    assert char_keysym("é") == 0xE9
    assert char_keysym("€") == 0x01000000 + 0x20AC
    assert char_keysym("\n") == KEY_NAMES["enter"]


@pytest.fixture
def contracted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    commands: list[tuple[int, int]] = []
    injector = FakeInjector()
    tables = default_tablesdir()
    table = [f"{tables}/fr-bfu-g2.ctb"]
    changes: list[list[int]] = []
    handler = BrailleInput(
        lambda command, argument: commands.append((command, argument)),
        lambda: computer_table(tables),
        injector,
        lambda: table[0],
        lambda: changes.append(handler.pending),
    )
    handler.set_gesture_map(GESTURE_MAP)
    return handler, commands, injector, table, changes


def typed(injector: FakeInjector) -> str:
    return "".join(chr(keysyms[0]) for keysyms in injector.presses)


def test_contracted_cells_wait_and_are_typed_as_a_word(contracted) -> None:
    handler, _commands, injector, _table, changes = contracted
    handler.handle(dots(0x03))
    handler.handle(dots(0x1A))
    assert injector.presses == []
    assert handler.pending == [0x03, 0x1A]
    handler.handle(dots(0, space=True))
    assert typed(injector) == "bonjour "
    assert handler.pending == []
    assert changes == [[0x03], [0x03, 0x1A], []]


def test_contracted_erase_removes_a_waiting_cell_first(contracted) -> None:
    handler, _commands, injector, _table, _changes = contracted
    handler.handle(dots(0x03))
    handler.handle(dots(0x40))
    assert handler.pending == []
    assert injector.presses == []
    handler.handle(dots(0x40))
    assert injector.presses == [[KEY_NAMES["backspace"]]]


def test_contracted_translate_and_other_actions_type_the_word(contracted) -> None:
    handler, commands, injector, _table, _changes = contracted
    handler.handle(dots(0x03))
    handler.handle(dots(0xC0))
    assert typed(injector) == "bien"
    injector.presses.clear()
    handler.handle(dots(0x03))
    handler.handle(key("fwinlt"))
    assert typed(injector) == "bien"
    assert commands == [(KEY_CMD_FWINLT, 0)]


def test_a_new_table_types_the_word_of_the_old_one(contracted) -> None:
    handler, _commands, injector, table, _changes = contracted
    handler.handle(dots(0x03))
    table[0] = f"{default_tablesdir()}/en-ueb-g2.ctb"
    handler.handle(dots(0x2E))
    assert typed(injector) == "bien"
    assert handler.pending == [0x2E]
    handler.flush()
    assert typed(injector) == "bienthe"


def test_contracted_off_types_each_cell_at_once(contracted) -> None:
    handler, _commands, injector, table, _changes = contracted
    table[0] = None
    handler.handle(dots(0x01))
    assert injector.presses == [[ord("a")]]
    assert handler.pending == []


def test_discarded_cells_are_not_typed(contracted) -> None:
    handler, _commands, injector, _table, _changes = contracted
    handler.handle(dots(0x03))
    handler.discard_pending()
    handler.flush()
    assert injector.presses == []
