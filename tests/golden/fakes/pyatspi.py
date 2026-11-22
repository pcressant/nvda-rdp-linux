"""Golden fake of ``pyatspi``: window events come from the control file
``atspi-events``, one ``[event type, application name, window name]`` per line.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import golden_trace

_POLL_SECONDS = 0.01


class _Registry:
    def __init__(self) -> None:
        self._listeners: list[tuple[object, str]] = []
        self._stop = threading.Event()

    def registerEventListener(self, listener, *event_types: str) -> None:
        golden_trace.record("atspi", "registerEventListener", *event_types)
        self._listeners.extend((listener, event_type) for event_type in event_types)

    def deregisterEventListener(self, listener, *event_types: str) -> None:
        golden_trace.record("atspi", "deregisterEventListener", *event_types)
        self._listeners = [
            (known, event_type)
            for known, event_type in self._listeners
            if not (known == listener and event_type in event_types)
        ]

    def start(self) -> None:
        queue = golden_trace.ControlQueue("atspi-events")
        while not self._stop.wait(_POLL_SECONDS):
            for event_type, application, window in queue.pop_all():
                golden_trace.record("atspi", "event", event_type, application, window)
                app = SimpleNamespace(name=application)
                source = SimpleNamespace(name=window, getApplication=lambda app=app: app)
                event = SimpleNamespace(type=event_type, source=source)
                for listener, wanted in list(self._listeners):
                    if wanted == event_type:
                        listener(event)

    def stop(self) -> None:
        golden_trace.record("atspi", "stop")
        self._stop.set()


Registry = _Registry()
