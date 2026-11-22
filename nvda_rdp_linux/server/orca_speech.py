# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 13 (rdaccess_speechd/orca_speech.py).
# Orca's default voice settings, read through Orca's public D-Bus service (the
# Remote Controller), not its Python internals.
# Interface read in orca@7f241c6 (also present in the VM's orca a0ebd32):
# every Extension, SpeechManager included, is published as
# /org/gnome/Orca1/Service/<class name>, interface org.gnome.Orca1.<class name>
# (src/orca/extension.py:280-285, dbus_service.py:385, 1104); getters become
# properties named without get_ (dbus_service.py:292-297). SpeechManager:
# Rate, Pitch, Volume (speech_manager.py:1110-1114, 1245, 1437). No code
# copied.

"""The baseline of per-utterance prosody (prosody.py): Orca's default voice
when NVDA attaches. The calls are asynchronous, so that the module never
blocks Speech Dispatcher while Orca answers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

from .prosody import orca_to_speechd

LOG = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Orca1.Service"
OBJECT_PATH = "/org/gnome/Orca1/Service/SpeechManager"
INTERFACE = "org.gnome.Orca1.SpeechManager"


class OrcaSpeech:
    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._present = False

    def start(self) -> None:
        Gio.bus_watch_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_appeared,
            self._on_vanished,
        )

    def _on_appeared(self, connection: Gio.DBusConnection, _name: str, _owner: str) -> None:
        self._bus = connection
        self._present = True
        LOG.debug("Orca on the session bus")

    def _on_vanished(self, connection: Gio.DBusConnection | None, _name: str) -> None:
        self._bus = connection
        self._present = False

    def _get(self, name: str, callback: Callable[[Any], None]) -> None:
        if self._bus is None or not self._present:
            callback(None)
            return

        def done(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                value = bus.call_finish(result).unpack()[0]
            except GLib.Error as error:
                LOG.warning("cannot read Orca's %s: %s", name, error.message)
                value = None
            callback(value)

        self._bus.call(
            BUS_NAME,
            OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (INTERFACE, name)),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            done,
        )

    def baseline(self, callback: Callable[[dict[str, int] | None], None]) -> None:
        """Calls back with Orca's default rate, pitch and volume on Speech
        Dispatcher's scale, or None when Orca does not answer."""

        values: dict[str, Any] = {}

        def got(name: str) -> Callable[[Any], None]:
            def store(value: Any) -> None:
                values[name] = value
                if len(values) == 3:
                    if None in values.values():
                        callback(None)
                    else:
                        callback(orca_to_speechd(values["Rate"], values["Pitch"], values["Volume"]))

            return store

        for name in ("Rate", "Pitch", "Volume"):
            self._get(name, got(name))
