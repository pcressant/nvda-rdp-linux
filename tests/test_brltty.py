from __future__ import annotations

from types import SimpleNamespace

from client.rdaccess.brltty import AtSpiFocusTracker, BrlApiOutput


class FakeConnection:
    displaySize = (20, 2)

    def __init__(self) -> None:
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

    def leaveTtyMode(self) -> None:
        self.left = True

    def closeConnection(self) -> None:
        self.closed = True


class FakeApi:
    PARAM_CLIENT_PRIORITY = 1

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
