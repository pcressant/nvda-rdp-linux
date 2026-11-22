"""Shared trace file of the golden fakes.

The fakes in this directory replace the system libraries and tools the client
talks to (Speech Dispatcher, BrlAPI, AT-SPI, audio player, X11 tools).  Each
call they receive is appended as one JSON line to ``$GOLDEN_TRACE``, which the
harness reads back step by step.  Control files written by the harness
(``$GOLDEN_CONTROL_DIR``) feed them input such as braille keys or focus events.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()


def record(source: str, call: str, *args: object) -> None:
    line = json.dumps({"src": source, "call": call, "args": list(args)}, ensure_ascii=False)
    with _LOCK, open(os.environ["GOLDEN_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(line + "\n")


def control_path(name: str) -> Path:
    return Path(os.environ["GOLDEN_CONTROL_DIR"]) / name


def option(name: str, default: object) -> object:
    """Read one fake setting from ``$GOLDEN_FAKE_OPTIONS`` (a JSON object)."""
    return json.loads(os.environ.get("GOLDEN_FAKE_OPTIONS", "{}")).get(name, default)


class ControlQueue:
    """Read the JSON lines appended to one control file, each line once."""

    def __init__(self, name: str) -> None:
        self._path = control_path(name)
        self._offset = 0

    def pop_all(self) -> list[object]:
        try:
            with open(self._path, encoding="utf-8") as queue:
                queue.seek(self._offset)
                data = queue.read()
        except FileNotFoundError:
            return []
        complete = data[: data.rfind("\n") + 1]
        self._offset += len(complete.encode("utf-8"))
        return [json.loads(line) for line in complete.splitlines() if line]
