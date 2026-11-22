"""Golden fakes of the session D-Bus services the server side talks to.

Run on an isolated session bus started by the harness, never on the user's:

* ``org.gnome.Orca1.Service``: Orca's public service, objects
  ``SpeechManager`` (``Rate``, ``Pitch``, ``Volume``) and ``BraillePresenter``
  (``ContractedBrailleIsEnabled``, ``ContractionTable``), from options
  ``orca_speech`` and ``orca_braille``; option ``orca`` false leaves the name
  unowned, as when Orca is not running;
* ``org.gnome.Mutter.RemoteDesktop``: ``CreateSession`` and the session's
  ``Start``, ``NotifyKeyboardKeysym``, ``NotifyKeyboardKeycode`` and ``Stop``.

Property reads and method calls are recorded in the golden trace.  The
control file ``dbus-properties`` changes properties while running, one
``{"object": name, "values": {...}}`` per line.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

from gi.repository import Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import golden_trace

ORCA_NAME = "org.gnome.Orca1.Service"
ORCA_PATH = "/org/gnome/Orca1/Service/"
MUTTER_NAME = "org.gnome.Mutter.RemoteDesktop"
MUTTER_PATH = "/org/gnome/Mutter/RemoteDesktop"
MUTTER_SESSION_PATH = "/org/gnome/Mutter/RemoteDesktop/Session/golden"
POLL_MS = 20

ORCA_OBJECTS = {
    "SpeechManager": {"Rate": "i", "Pitch": "d", "Volume": "d"},
    "BraillePresenter": {"ContractedBrailleIsEnabled": "b", "ContractionTable": "s"},
}
ORCA_DEFAULTS = {
    "SpeechManager": {"Rate": 50, "Pitch": 5.0, "Volume": 10.0},
    "BraillePresenter": {"ContractedBrailleIsEnabled": False, "ContractionTable": ""},
}

MUTTER_XML = """
<node>
  <interface name="org.gnome.Mutter.RemoteDesktop">
    <method name="CreateSession"><arg type="o" direction="out"/></method>
  </interface>
</node>
"""
MUTTER_SESSION_XML = """
<node>
  <interface name="org.gnome.Mutter.RemoteDesktop.Session">
    <method name="Start"/>
    <method name="Stop"/>
    <method name="NotifyKeyboardKeysym">
      <arg type="u" direction="in"/><arg type="b" direction="in"/>
    </method>
    <method name="NotifyKeyboardKeycode">
      <arg type="u" direction="in"/><arg type="b" direction="in"/>
    </method>
  </interface>
</node>
"""


def orca_xml(name: str) -> str:
    properties = "".join(
        f'<property name="{prop}" type="{kind}" access="read"/>'
        for prop, kind in ORCA_OBJECTS[name].items()
    )
    return f'<node><interface name="org.gnome.Orca1.{name}">{properties}</interface></node>'


class Services:
    def __init__(self, bus: Gio.DBusConnection) -> None:
        self.bus = bus
        self.values = {
            name: {**ORCA_DEFAULTS[name], **golden_trace.option(option, {})}
            for name, option in (
                ("SpeechManager", "orca_speech"),
                ("BraillePresenter", "orca_braille"),
            )
        }
        self.controls = golden_trace.ControlQueue("dbus-properties")

    def register(self) -> list[str]:
        names = []
        if golden_trace.option("orca", True):
            for name in ORCA_OBJECTS:
                info = Gio.DBusNodeInfo.new_for_xml(orca_xml(name)).interfaces[0]
                self.bus.register_object(
                    ORCA_PATH + name, info, None, self._orca_property(name), None
                )
            names.append(ORCA_NAME)
        if golden_trace.option("mutter", True):
            info = Gio.DBusNodeInfo.new_for_xml(MUTTER_XML).interfaces[0]
            self.bus.register_object(MUTTER_PATH, info, self._mutter_call, None, None)
            info = Gio.DBusNodeInfo.new_for_xml(MUTTER_SESSION_XML).interfaces[0]
            self.bus.register_object(MUTTER_SESSION_PATH, info, self._mutter_call, None, None)
            names.append(MUTTER_NAME)
        return names

    def _orca_property(self, name: str):
        def get(_bus, _sender, _path, _interface, prop):
            golden_trace.record("dbus", "get", name, prop)
            return GLib.Variant(ORCA_OBJECTS[name][prop], self.values[name][prop])

        return get

    def _mutter_call(self, _bus, _sender, path, _interface, method, parameters, invocation):
        golden_trace.record("dbus", method, path, *parameters.unpack())
        if method == "CreateSession":
            invocation.return_value(GLib.Variant("(o)", (MUTTER_SESSION_PATH,)))
        else:
            invocation.return_value(None)

    def poll_controls(self) -> bool:
        for change in self.controls.pop_all():
            self.values[change["object"]].update(change["values"])
            golden_trace.record("dbus", "changed", change["object"], change["values"])
        return GLib.SOURCE_CONTINUE


def main() -> None:
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    services = Services(bus)
    names = services.register()
    pending = set(names)

    def acquired(_bus, name):
        pending.discard(name)
        if not pending:
            golden_trace.record("dbus", "ready", sorted(names))

    for name in names:
        Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, acquired, None)
    if not names:
        golden_trace.record("dbus", "ready", [])
    GLib.timeout_add(POLL_MS, services.poll_controls)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or False)
    loop.run()


if __name__ == "__main__":
    main()
