# SPDX-License-Identifier: GPL-2.0-or-later
# Behaviour from rdAccess 2.1.0: addon/lib/driver/__init__.py
# (CONNECT_FOCUS_GRACE, _outgoing_timeSinceInput). No code copied.
# Interface: mutter, data/dbus-interfaces/org.gnome.Mutter.IdleMonitor.xml.

"""The ``timeSinceInput`` attribute: milliseconds since the last input of
the remote session.

The RDAccess client asks for it when the RDP window takes the focus, and
takes the remote session as focused if the answer is at most 200 ms.  As
RDAccess's own server, the answer is 0 for 4 s after the client attached
(XON), then Mutter's idle time of the session: RDP input reaches Mutter
through GNOME Remote Desktop and resets it.  Without Mutter's idle monitor
the answer is 0, as if the session had just received input.
"""

from __future__ import annotations

import logging
import time

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Mutter.IdleMonitor"
OBJECT_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
INTERFACE = "org.gnome.Mutter.IdleMonitor"
TIMEOUT_MS = 500
CONNECT_GRACE_SECONDS = 4.0


class InputTime:
    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._warned = False

    def since_input(self, attached_at: float | None) -> int:
        """The value of ``timeSinceInput`` for a client attached at
        ``attached_at`` (``time.monotonic()``), or None if not attached."""
        if attached_at is None or time.monotonic() - attached_at < CONNECT_GRACE_SECONDS:
            return 0
        try:
            if self._bus is None:
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = self._bus.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "GetIdletime",
                None,
                GLib.VariantType("(t)"),
                Gio.DBusCallFlags.NONE,
                TIMEOUT_MS,
                None,
            )
        except GLib.Error as error:
            if not self._warned:
                self._warned = True
                LOG.warning("no idle time from Mutter, reporting 0: %s", error.message)
            return 0
        self._warned = False
        return reply.unpack()[0]
