"""Focus-aware BrlAPI output which yields the display to Orca when inactive."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger(__name__)

PRIORITY_DISABLED = 0
PRIORITY_REMOTE_ACTIVE = 60


class BrlApiOutput:
    def __init__(self, *, priority: int = PRIORITY_REMOTE_ACTIVE, api: Any = None) -> None:
        if not 1 <= priority <= 100:
            raise ValueError("active BrlAPI priority must be between 1 and 100")
        if api is None:
            import brlapi as api  # type: ignore[no-redef]

        self._api = api
        self._priority = priority
        self._connection = api.Connection()
        self._lock = threading.Lock()
        self._active = False
        self._last_cells: bytes | None = None
        try:
            self._connection.enterTtyModeWithPath()
            self._set_priority(PRIORITY_DISABLED)
            columns, rows = self._connection.displaySize
            if columns <= 0 or rows <= 0:
                raise RuntimeError(f"invalid BrlAPI display size: {columns}x{rows}")
            self.num_cols = columns
            self.num_rows = rows
            self.num_cells = columns * rows
        except Exception:
            self._close_connection()
            raise
        LOG.info(
            "connected to BrlAPI via daemon authorization; display=%dx%d priority=disabled",
            self.num_cols,
            self.num_rows,
        )

    def set_active(self, active: bool) -> None:
        with self._lock:
            if active == self._active:
                return
            priority = self._priority if active else PRIORITY_DISABLED
            self._set_priority(priority)
            self._active = active
            LOG.info(
                "BrlAPI remote output %s priority=%d",
                "active" if active else "idle",
                priority,
            )
            if active and self._last_cells is not None:
                self._connection.writeDots(self._last_cells)

    def display(self, cells: list[int]) -> None:
        if len(cells) != self.num_cells:
            LOG.error(
                "refusing %d braille cells for a %d-cell display",
                len(cells),
                self.num_cells,
            )
            return
        encoded = bytes(cells)
        with self._lock:
            self._last_cells = encoded
            if self._active:
                self._connection.writeDots(encoded)

    def close(self) -> None:
        with self._lock:
            try:
                self._set_priority(PRIORITY_DISABLED)
            finally:
                self._active = False
                self._close_connection()
        LOG.info("closed BrlAPI connection")

    def _set_priority(self, priority: int) -> None:
        self._connection.setParameter(
            self._api.PARAM_CLIENT_PRIORITY,
            0,
            False,
            priority,
        )

    def _close_connection(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            connection.leaveTtyMode()
        except (AttributeError, OSError, RuntimeError) as error:
            LOG.debug("cannot leave BrlAPI TTY mode during cleanup: %s", error)
        try:
            connection.closeConnection()
        finally:
            self._connection = None


class AtSpiFocusTracker:
    """Activate remote braille only while a FreeRDP application has focus."""

    def __init__(
        self,
        on_active_changed: Callable[[bool], None],
        *,
        application_pattern: str = "freerdp",
        registry: Any = None,
    ) -> None:
        if registry is None:
            import pyatspi

            registry = pyatspi.Registry
        self._registry = registry
        self._on_active_changed = on_active_changed
        self._pattern = application_pattern.casefold()
        self._thread: threading.Thread | None = None
        self._active = False

    def start(self) -> None:
        self._registry.registerEventListener(self._on_window_event, "window:activate")
        self._registry.registerEventListener(self._on_window_event, "window:deactivate")
        self._thread = threading.Thread(
            target=self._registry.start,
            name="atspi-focus-tracker",
            daemon=True,
        )
        self._thread.start()
        LOG.info("watching AT-SPI focus for application pattern=%r", self._pattern)

    def close(self) -> None:
        self._set_active(False)
        self._registry.deregisterEventListener(self._on_window_event, "window:activate")
        self._registry.deregisterEventListener(self._on_window_event, "window:deactivate")
        self._registry.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _on_window_event(self, event: Any) -> None:
        application_name = ""
        window_name = getattr(event.source, "name", "")
        try:
            application = event.source.getApplication()
            application_name = application.name if application is not None else ""
        except (AttributeError, OSError, RuntimeError) as error:
            LOG.warning("cannot identify AT-SPI window application: %s", error)
        matches = (
            self._pattern in application_name.casefold() or self._pattern in window_name.casefold()
        )
        if event.type.endswith(":activate"):
            active = matches
        elif matches:
            active = False
        else:
            # AT-SPI may report the newly activated window before the old,
            # unrelated window's delayed deactivation.  That deactivation
            # must not revoke FreeRDP's ownership of the braille display.
            return
        LOG.debug(
            "AT-SPI event=%s application=%r window=%r remote_active=%s",
            event.type,
            application_name,
            window_name,
            active,
        )
        self._set_active(active)

    def _set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._on_active_changed(active)
