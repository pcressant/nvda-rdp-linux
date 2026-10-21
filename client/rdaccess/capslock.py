"""Keep the local Caps Lock state in line with the remote NVDA.

RDAccess 2.0.3 lets the remote NVDA push its Caps Lock toggle state
(``capsLockToggle``).  As in ``_remoteHandler._applyRemoteCapsLockToggle``,
the local state is only changed while the RDP client does not have focus,
so that it never fights the keyboard state FreeRDP synchronizes itself.

The only backend uses X11 tools (``xset`` to read, ``xdotool`` to toggle).
Under Wayland they only reach Xwayland, so the result is not guaranteed.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from typing import Protocol

LOG = logging.getLogger(__name__)
_CAPS_LOCK_STATE = re.compile(r"Caps Lock:\s+(on|off)")


class CapsLockBackend(Protocol):
    def get(self) -> bool | None: ...

    def toggle(self) -> None: ...


class X11CapsLockBackend:
    def get(self) -> bool | None:
        try:
            result = subprocess.run(
                ("xset", "q"), capture_output=True, text=True, timeout=2, check=True
            )
        except (OSError, subprocess.SubprocessError) as error:
            LOG.error("cannot read Caps Lock state with xset: %s", error)
            return None
        match = _CAPS_LOCK_STATE.search(result.stdout)
        return match.group(1) == "on" if match else None

    def toggle(self) -> None:
        try:
            subprocess.run(("xdotool", "key", "Caps_Lock"), timeout=2, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            LOG.error("cannot toggle Caps Lock with xdotool: %s", error)


def default_backend() -> CapsLockBackend | None:
    if not os.environ.get("DISPLAY"):
        LOG.warning("Caps Lock synchronization needs an X11 DISPLAY; disabled")
        return None
    if os.environ.get("WAYLAND_DISPLAY"):
        LOG.warning("Caps Lock synchronization under Wayland only reaches Xwayland")
    return X11CapsLockBackend()


class CapsLockSync:
    def __init__(self, backend: CapsLockBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._remote_state: bool | None = None
        self._remote_focused = False

    def set_remote_state(self, state: bool) -> None:
        with self._lock:
            self._remote_state = state
            self._apply()

    def set_remote_focused(self, focused: bool) -> None:
        with self._lock:
            self._remote_focused = focused
            self._apply()

    def _apply(self) -> None:
        if self._remote_state is None or self._remote_focused:
            return
        local = self._backend.get()
        if local is None or local == self._remote_state:
            return
        LOG.info("toggling local Caps Lock to match remote state=%s", self._remote_state)
        self._backend.toggle()
