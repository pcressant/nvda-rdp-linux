# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 10 (rdaccess_bridge/mutter_input.py).
# Interface: gnome-remote-desktop@ba1b66d, src/org.gnome.Mutter.RemoteDesktop.xml
# (CreateSession, Session.Start, Session.NotifyKeyboardKeysym, Session.Stop).

"""Key injection into the session's compositor, through Mutter's RemoteDesktop
D-Bus API, as GNOME Remote Desktop does for RDP keyboard input.

A remote desktop session of Mutter is created at the first key and kept; it
is created again if Mutter closed it.
"""

from __future__ import annotations

import logging
from typing import Any

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Mutter.RemoteDesktop"
OBJECT_PATH = "/org/gnome/Mutter/RemoteDesktop"
SESSION_INTERFACE = "org.gnome.Mutter.RemoteDesktop.Session"
TIMEOUT_MS = 2000


class MutterKeyInjector:
    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._session: str | None = None

    def _call(
        self, path: str, interface: str, method: str, args: Any = None, reply: str | None = None
    ) -> Any:
        assert self._bus is not None
        return self._bus.call_sync(
            BUS_NAME,
            path,
            interface,
            method,
            args,
            GLib.VariantType(reply) if reply else None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT_MS,
            None,
        )

    def _ensure_session(self) -> str:
        if self._session is not None:
            return self._session
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path = self._call(OBJECT_PATH, BUS_NAME, "CreateSession", None, "(o)").unpack()[0]
        self._call(path, SESSION_INTERFACE, "Start")
        LOG.info("Mutter remote desktop session %s started", path)
        self._session = path
        return path

    def press(self, keysyms: list[int]) -> None:
        """Press keysyms in order, then release them in reverse order."""
        events = [(k, True) for k in keysyms] + [(k, False) for k in reversed(keysyms)]
        for attempt in range(2):
            try:
                path = self._ensure_session()
                for keysym, pressed in events:
                    self._call(
                        path,
                        SESSION_INTERFACE,
                        "NotifyKeyboardKeysym",
                        GLib.Variant("(ub)", (keysym, pressed)),
                    )
                return
            except GLib.Error as error:
                LOG.warning(
                    "Mutter call failed: %s%s",
                    error.message,
                    ", creating a new session" if attempt == 0 else "",
                )
                self._session = None

    def close(self) -> None:
        if self._session is not None and self._bus is not None:
            try:
                self._call(self._session, SESSION_INTERFACE, "Stop")
            except GLib.Error:
                pass
        self._session = None
