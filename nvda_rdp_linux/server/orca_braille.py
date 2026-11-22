# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 06 (rdaccess_brlapi/orca_settings.py).
# Orca's contracted braille settings, read through Orca's public D-Bus service
# (the Remote Controller), not its Python internals.

"""Which liblouis table turns the text Orca writes into dots.

Orca contracts text itself with louis.translate in mode 0, so the text it
writes holds the display characters of its contraction table
(orca@7f241c6:src/orca/braille.py:1109-1120). With contracted braille off,
the text is plain. The server needs both settings to get the dots right:

- ContractedBrailleIsEnabled and ContractionTable, properties of
  org.gnome.Orca1.BraillePresenter (braille_presenter.py:1080-1092,
  1156-1167); the table is a name without extension, empty for the default;
- the default table, computed by Orca from the locale with the rules of
  braille.py:939-998, repeated here.
"""

from __future__ import annotations

import locale
import logging
import os
import time
from collections.abc import Callable

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Orca1.Service"
OBJECT_PATH = "/org/gnome/Orca1/Service/BraillePresenter"
INTERFACE = "org.gnome.Orca1.BraillePresenter"
REFRESH_SECONDS = 1.0


def default_contraction_table(tablesdir: str) -> str:
    """Same rules as Orca's _get_default_table (braille.py:939-998)."""

    value = locale.getlocale(locale.LC_MESSAGES)[0] or ""
    if value in ("", "C"):
        language = "en-us"
    else:
        parts = value.split("_")
        language = f"{parts[0]}-{parts[1]}".lower() if len(parts) > 1 else parts[0].lower()
    try:
        tables = [x for x in os.listdir(tablesdir) if x[-4:] in (".utb", ".ctb")]
    except OSError:
        return ""
    exclude = ["interline", "mathtext"]

    def is_candidate(t: str, prefix: str) -> bool:
        return t.startswith(prefix) and not any(e in t for e in exclude)

    candidates = [t for t in tables if is_candidate(t, language)]
    if not candidates:
        short = language.split("-", maxsplit=1)[0]
        if short != language:
            candidates = [t for t in tables if is_candidate(t, short)]
    if not candidates:
        return ""
    for p in ["g1", "g2", "comp6", "comp8"]:
        for table in candidates:
            if p in table:
                return os.path.join(tablesdir, table)
    return os.path.join(tablesdir, candidates[0])


class OrcaBrailleSettings:
    """Caches Orca's contracted braille settings, refreshed asynchronously."""

    def __init__(self, tablesdir: str, on_changed: Callable[[], None]) -> None:
        self._tablesdir = tablesdir
        self._on_changed = on_changed
        self._bus: Gio.DBusConnection | None = None
        self._last_refresh = 0.0
        self._pending = False
        self.contracted = False
        self.table_name = ""
        self._default_table = default_contraction_table(tablesdir)

    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            LOG.warning("no session bus, contracted braille settings unknown: %s", error.message)
            return
        self.refresh()

    def contraction_table(self) -> str:
        if not self.table_name:
            return self._default_table
        for extension in (".ctb", ".utb", ".tbl"):
            path = os.path.join(self._tablesdir, self.table_name + extension)
            if os.path.exists(path):
                return path
        return self._default_table

    def maybe_refresh(self) -> None:
        if time.monotonic() - self._last_refresh >= REFRESH_SECONDS:
            self.refresh()

    def refresh(self) -> None:
        if self._bus is None or self._pending:
            return
        self._pending = True
        self._last_refresh = time.monotonic()
        self._bus.call(
            BUS_NAME,
            OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "GetAll",
            GLib.Variant("(s)", (INTERFACE,)),
            GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NO_AUTO_START,
            2000,
            None,
            self._on_reply,
        )

    def _on_reply(self, bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        self._pending = False
        try:
            (values,) = bus.call_finish(result).unpack()
        except GLib.Error as error:
            LOG.debug("Orca settings unavailable: %s", error.message)
            return
        contracted = bool(values.get("ContractedBrailleIsEnabled", False))
        table_name = str(values.get("ContractionTable", ""))
        if (contracted, table_name) != (self.contracted, self.table_name):
            self.contracted, self.table_name = contracted, table_name
            LOG.info("Orca contracted braille %s, table %s", contracted, self.contraction_table())
            self._on_changed()
