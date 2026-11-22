"""Golden fake of the BrlAPI Python module.

Constants and ``expandKeyCode`` come from the real module (BRLTTY's
``brlapi``), so key codes are encoded and decoded exactly as on a real
system; only ``Connection`` is replaced.  Keys are read from the control file
``brlapi-keys`` (one key code per line).  Options: ``brlapi_fail`` (refuse the
connection), ``brlapi_size`` ([columns, rows]), ``brlapi_driver``.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections import deque
from pathlib import Path

import golden_trace


def _load_real_module():
    fakes = str(Path(__file__).resolve().parent)
    search = [entry for entry in sys.path if str(Path(entry or ".").resolve()) != fakes]
    spec = importlib.machinery.PathFinder.find_spec("brlapi", search)
    if spec is None:
        raise ImportError("the real brlapi module is needed for its constants")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_real = _load_real_module()
for _name in dir(_real):
    if _name.isupper() or _name.startswith(("KEY_", "rangeType_", "PARAM_")):
        globals()[_name] = getattr(_real, _name)
expandKeyCode = _real.expandKeyCode
ConnectionError = _real.ConnectionError


class Connection:
    def __init__(self, *args: object) -> None:
        if golden_trace.option("brlapi_fail", False):
            raise OSError("golden fake: BrlAPI unavailable")
        golden_trace.record("brlapi", "connect", *args)
        columns, rows = golden_trace.option("brlapi_size", [40, 1])
        self.displaySize = (columns, rows)
        self.driverName = golden_trace.option("brlapi_driver", "fake")
        self._keys = golden_trace.ControlQueue("brlapi-keys")
        self._pending: deque[int] = deque()

    def enterTtyModeWithPath(self, *args: object) -> None:
        golden_trace.record("brlapi", "enterTtyModeWithPath", *args)

    def leaveTtyMode(self) -> None:
        golden_trace.record("brlapi", "leaveTtyMode")

    def closeConnection(self) -> None:
        golden_trace.record("brlapi", "closeConnection")

    def setParameter(self, parameter: int, subparam: int, global_: bool, value: object) -> None:
        golden_trace.record("brlapi", "setParameter", parameter, subparam, global_, value)

    def ignoreKeys(self, range_type: int, keys: object) -> None:
        golden_trace.record("brlapi", "ignoreKeys", range_type, list(keys))

    def writeDots(self, dots: bytes) -> None:
        golden_trace.record("brlapi", "writeDots", bytes(dots).hex(" "))

    def readKey(self, wait: bool = True) -> int | None:
        self._pending.extend(int(code) for code in self._keys.pop_all())
        if not self._pending:
            return None
        code = self._pending.popleft()
        golden_trace.record("brlapi", "readKey", code)
        return code
