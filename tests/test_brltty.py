from __future__ import annotations

from types import SimpleNamespace

from client.rdaccess.brltty import (
    AtSpiFocusTracker,
    BrlApiOutput,
    command_names,
    translate_key,
)


class FakeConnection:
    displaySize = (20, 2)
    driverName = b"bm"

    def __init__(self) -> None:
        self.keys: list[int] = []
        self.ignored: list[tuple[object, object]] = []
        self.priorities: list[int] = []
        self.writes: list[bytes] = []
        self.entered = False
        self.left = False
        self.closed = False

    def enterTtyModeWithPath(self) -> None:
        self.entered = True

    def setParameter(self, _parameter, _subparam, _global, value) -> None:
        self.priorities.append(value)

    def writeDots(self, cells: bytes) -> None:
        self.writes.append(cells)

    def readKey(self, wait: bool) -> int:
        assert wait is False
        return self.keys.pop(0) if self.keys else 0

    def ignoreKeys(self, key_type, keys) -> None:
        self.ignored.append((key_type, tuple(keys)))

    def leaveTtyMode(self) -> None:
        self.left = True

    def closeConnection(self) -> None:
        self.closed = True


class FakeApi:
    PARAM_CLIENT_PRIORITY = 1
    KEY_TYPE_CMD = 0x20000000
    KEY_TYPE_SYM = 0
    KEY_CMD_ARG_MASK = 0xFFFF
    KEY_CMD_BLK_SHIFT = 16
    KEY_CMD_FWINLT = 23
    KEY_CMD_ROUTE = 0x10000
    KEY_CMD_PASSDOTS = 0x220000
    DOTC = 0x100
    rangeType_type = "type"

    @staticmethod
    def expandKeyCode(code: int) -> dict[str, int]:
        return {
            "type": code & 0x20000000,
            "command": code & 0x1FFF0000 or code & 0xFFFF,
            "argument": code & 0xFFFF if code & 0x1FFF0000 else 0,
            "flags": 0,
        }

    def __init__(self) -> None:
        self.connection = FakeConnection()

    def Connection(self) -> FakeConnection:
        return self.connection


def test_brlapi_output_is_idle_until_freerdp_is_active() -> None:
    api = FakeApi()
    output = BrlApiOutput(api=api, priority=60)
    cells = list(range(40))
    output.display(cells)
    assert api.connection.priorities == [0]
    assert api.connection.writes == []

    output.set_active(True)
    assert api.connection.priorities == [0, 60]
    assert api.connection.writes == [bytes(cells)]

    output.set_active(False)
    output.display([0] * 40)
    assert api.connection.priorities == [0, 60, 0]
    assert api.connection.writes == [bytes(cells)]

    output.close()
    assert api.connection.priorities[-1] == 0
    assert api.connection.left
    assert api.connection.closed


class FakeRegistry:
    def __init__(self) -> None:
        self.listeners = []

    def registerEventListener(self, callback, event_type) -> None:
        self.listeners.append((callback, event_type))

    def deregisterEventListener(self, callback, event_type) -> None:
        self.listeners.remove((callback, event_type))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def event(event_type: str, application_name: str, window_name: str = ""):
    application = SimpleNamespace(name=application_name)
    source = SimpleNamespace(name=window_name, getApplication=lambda: application)
    return SimpleNamespace(type=event_type, source=source)


def test_focus_tracker_yields_on_alt_tab() -> None:
    states: list[bool] = []
    registry = FakeRegistry()
    tracker = AtSpiFocusTracker(states.append, registry=registry)
    tracker.start()

    tracker._on_window_event(event("window:activate", "xfreerdp"))
    tracker._on_window_event(event("window:deactivate", "xfreerdp"))
    tracker._on_window_event(event("window:activate", "org.gnome.Terminal"))
    assert states == [True, False]
    tracker.close()


def test_focus_tracker_recognizes_freerdp_xwayland_frame() -> None:
    states: list[bool] = []
    tracker = AtSpiFocusTracker(states.append, registry=FakeRegistry())
    tracker._on_window_event(
        event("window:activate", "mutter-x11-frames", "FreeRDP: windows-remote")
    )
    tracker._on_window_event(
        event("window:deactivate", "mutter-x11-frames", "FreeRDP: windows-remote")
    )
    assert states == [True, False]


def test_focus_tracker_ignores_delayed_deactivation_of_previous_window() -> None:
    states: list[bool] = []
    tracker = AtSpiFocusTracker(states.append, registry=FakeRegistry())

    tracker._on_window_event(
        event("window:activate", "mutter-x11-frames", "FreeRDP: windows-remote")
    )
    tracker._on_window_event(event("window:deactivate", "org.gnome.Terminal"))
    assert states == [True]

    tracker._on_window_event(event("window:activate", "org.gnome.Terminal"))
    assert states == [True, False]


def test_command_names_skip_masks() -> None:
    names = command_names(FakeApi)
    assert names[23] == "fwinlt"
    assert names[0x10000] == "route"
    assert "arg_mask" not in names.values()


def test_translate_key_matches_nvda_brltty_gestures() -> None:
    api = FakeApi
    names = command_names(api)
    route = translate_key(api.expandKeyCode(0x20000000 | 0x10000 | 7), api, names, "bm")
    assert (route.source, route.id, route.routing_index, route.model) == (
        "brltty",
        "route",
        7,
        "bm",
    )
    scroll = translate_key(api.expandKeyCode(0x20000000 | 23), api, names, "bm")
    assert (scroll.id, scroll.routing_index, scroll.is_braille_input) == ("fwinlt", None, False)
    dots = translate_key(api.expandKeyCode(0x20000000 | 0x220000 | 0x105), api, names, "bm")
    assert (dots.id, dots.dots, dots.space, dots.is_braille_input) == ("passdots", 5, True, True)
    assert translate_key(api.expandKeyCode(0x41), api, names, "bm") is None


def test_keys_are_forwarded_only_while_active() -> None:
    api = FakeApi()
    output = BrlApiOutput(api=api)
    assert output.model == "bm"
    gestures = []
    output.start_input(gestures.append)
    output.close()
    assert api.connection.ignored == [("type", (0,))]

    output = BrlApiOutput(api=api)
    output._on_gesture = gestures.append
    names = command_names(api)
    api.connection.keys = [0x20000000 | 23]
    output.poll_keys(names)
    assert gestures == []
    output.set_active(True)
    api.connection.keys = [0x20000000 | 23, 0x20000000 | 0x10000 | 3]
    output.poll_keys(names)
    assert [(g.id, g.routing_index) for g in gestures] == [("fwinlt", None), ("route", 3)]
