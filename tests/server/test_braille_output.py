"""Orca's text to cells, with the real liblouis."""

from __future__ import annotations

import pytest

from nvda_rdp_linux.server.braille_bridge import char_to_dots, computer_table
from nvda_rdp_linux.server.brlapi_main import default_tablesdir

TABLES = default_tablesdir()


def test_long_text_is_converted_entirely() -> None:
    # liblouis 3.38's binding returns a quarter of it without the padding.
    text = "Bonjour tout le monde"
    cells = char_to_dots(computer_table(TABLES), text)
    assert len(cells) == len(text)
    assert cells[:2] == [0x43, 0x15]
    assert cells[7] == 0


def test_computer_table_follows_the_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert computer_table("/t").endswith("fr-bfu-comp8.utb")
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    assert computer_table("/t").endswith("en-us-comp8.ctb")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert computer_table("/t").endswith("en-us-comp8.ctb")


def test_unknown_table_raises() -> None:
    with pytest.raises(RuntimeError):
        char_to_dots("/nonexistent.utb", "a")
