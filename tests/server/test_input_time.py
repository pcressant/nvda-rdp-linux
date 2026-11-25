"""timeSinceInput: 0 during the grace after an attach, then Mutter's idle time."""

from __future__ import annotations

from typing import Any

import pytest
from gi.repository import GLib

from nvda_rdp_linux.server import input_time
from nvda_rdp_linux.server.input_time import CONNECT_GRACE_SECONDS, InputTime


class FakeBus:
    def __init__(self, idle: int | None) -> None:
        self.idle = idle
        self.calls: list[tuple[Any, ...]] = []

    def call_sync(self, *args: Any) -> GLib.Variant:
        self.calls.append(args[:4])
        if self.idle is None:
            raise GLib.Error("org.freedesktop.DBus.Error.ServiceUnknown")
        return GLib.Variant("(t)", (self.idle,))


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [1000.0]
    monkeypatch.setattr(input_time.time, "monotonic", lambda: clock[0])
    return clock


def make(idle: int | None) -> tuple[InputTime, FakeBus]:
    bus = FakeBus(idle)
    value = InputTime()
    value._bus = bus  # type: ignore[assignment]
    return value, bus


def test_zero_while_detached(now: list[float]) -> None:
    value, bus = make(1500)
    assert value.since_input(None) == 0
    assert bus.calls == []


def test_zero_during_the_grace_without_asking_mutter(now: list[float]) -> None:
    value, bus = make(1500)
    attached = now[0]
    now[0] = attached + CONNECT_GRACE_SECONDS - 0.001
    assert value.since_input(attached) == 0
    assert bus.calls == []


def test_mutter_idle_time_after_the_grace(now: list[float]) -> None:
    value, bus = make(1500)
    attached = now[0]
    now[0] = attached + CONNECT_GRACE_SECONDS
    assert value.since_input(attached) == 1500
    assert bus.calls == [
        (
            "org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor",
            "GetIdletime",
        )
    ]


def test_zero_without_mutter(now: list[float]) -> None:
    value, _bus = make(None)
    attached = now[0] - CONNECT_GRACE_SECONDS
    assert value.since_input(attached) == 0
    assert value.since_input(attached) == 0
