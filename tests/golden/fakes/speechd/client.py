"""Golden fake of ``speechd.client``: records every SSIP call.

Option ``ssip_auto_end`` (default true): report the events of each spoken
message as soon as it is submitted, so that indexes are sent back
deterministically; ``ssip_events`` lists them (default ``["end"]``).  Option
``ssip_voices``: the list returned by ``list_synthesis_voices``.  Option
``ssip_fail``: refuse the connection.
"""

from __future__ import annotations

from enum import Enum

import golden_trace


class CallbackType(str, Enum):
    BEGIN = "begin"
    END = "end"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    INDEX_MARK = "index_marks"


class DataMode(str, Enum):
    TEXT = "text"
    SSML = "ssml"


def _plain(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class SSIPClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if golden_trace.option("ssip_fail", False):
            raise ConnectionRefusedError("golden fake: Speech Dispatcher unavailable")
        golden_trace.record(
            "ssip", "connect", list(args), {key: _plain(kwargs[key]) for key in sorted(kwargs)}
        )

    def speak(self, text: str, callback=None, event_types=None) -> None:
        golden_trace.record("ssip", "speak", text, callback is not None, _plain(event_types))
        if callback is not None and golden_trace.option("ssip_auto_end", True):
            for event in golden_trace.option("ssip_events", ["end"]):
                callback(CallbackType(event))

    def list_synthesis_voices(self) -> list[tuple[str, str, str]]:
        golden_trace.record("ssip", "list_synthesis_voices")
        return [tuple(voice) for voice in golden_trace.option("ssip_voices", [])]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args: object, **kwargs: object) -> None:
            golden_trace.record(
                "ssip",
                name,
                *[_plain(arg) for arg in args],
                *([{key: _plain(kwargs[key]) for key in sorted(kwargs)}] if kwargs else []),
            )

        return call
