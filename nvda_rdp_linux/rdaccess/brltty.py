"""Focus-aware BrlAPI output which yields the display to Orca when inactive.

While FreeRDP has focus, this client owns the braille display: it writes the
remote cells and reads the display's keys as BRLTTY commands.  Keys are
translated exactly like NVDA's own ``brltty`` braille display driver does
(``source="brltty"``, gesture id = lower-case BrlAPI command name), and
NVDA's ``brltty`` gesture map is advertised to the remote NVDA so that the
usual BRLTTY bindings (scroll, line up/down, routing, ...) work unchanged.
Braille keyboard dots (``PASSDOTS``) are additionally sent as braille input.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .braille import BrailleGesture

LOG = logging.getLogger(__name__)

PRIORITY_DISABLED = 0
PRIORITY_REMOTE_ACTIVE = 60
KEY_POLL_INTERVAL = 0.05
GESTURE_SOURCE = "brltty"
# Window or application names of supported RDP clients (substring, case-insensitive).
DEFAULT_FOCUS_APPLICATIONS = ("freerdp", "remmina")

# NVDA's brailleDisplayDrivers/brltty.py gestureMap, in GlobalGestureMap.export()
# form: {"module.Class": {script: gesture identifier or list of identifiers}}.
BRLTTY_GESTURE_MAP: dict[str, dict[str, object]] = {
    "globalCommands.GlobalCommands": {
        "braille_scrollBack": "br(brltty):fwinlt",
        "braille_scrollForward": "br(brltty):fwinrt",
        "braille_previousLine": "br(brltty):lnup",
        "braille_nextLine": "br(brltty):lndn",
        "braille_routeTo": "br(brltty):route",
        "toggleInputHelp": "br(brltty):learn",
        "showGui": "br(brltty):prefmenu",
        "revertConfiguration": "br(brltty):prefload",
        "saveConfiguration": "br(brltty):prefsave",
        "dateTime": "br(brltty):time",
        "review_currentLine": "br(brltty):say_line",
        "review_sayAll": "br(brltty):say_below",
    },
}


def command_names(api: Any) -> dict[int, str]:
    """Map BrlAPI command codes to NVDA gesture ids, e.g. ``KEY_CMD_FWINLT`` -> ``fwinlt``.

    The ``*_MASK``/``*_SHIFT`` constants share the ``KEY_CMD_`` prefix but are
    not commands; they are skipped so they cannot shadow real command codes.
    """
    return {
        getattr(api, name): name[len("KEY_CMD_") :].lower()
        for name in dir(api)
        if name.startswith("KEY_CMD_")
        and not name.endswith(("_MASK", "_SHIFT"))
        and isinstance(getattr(api, name), int)
    }


def translate_key(key: dict[str, int], api: Any, names: dict[int, str], model: str | None):
    """Turn one expanded BrlAPI key into a gesture, or None when it is not forwarded."""
    if key.get("type") != api.KEY_TYPE_CMD:
        return None
    command = key.get("command")
    argument = key.get("argument", 0)
    name = names.get(command)
    if name is None:
        LOG.debug("ignoring unknown BrlAPI command %r", command)
        return None
    if command == api.KEY_CMD_PASSDOTS:
        return BrailleGesture(
            GESTURE_SOURCE,
            name,
            model,
            dots=argument & 0xFF,
            space=bool(argument & api.DOTC),
            is_braille_input=True,
        )
    routing_index = argument if command == api.KEY_CMD_ROUTE else None
    return BrailleGesture(GESTURE_SOURCE, name, model, routing_index=routing_index)


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
        self._on_gesture: Callable[[BrailleGesture], None] | None = None
        self._input_thread: threading.Thread | None = None
        self._stop_input = threading.Event()
        self.failed = threading.Event()
        """Set when the BrlAPI connection stops working (e.g. BRLTTY restarted)."""
        self.model: str | None = None
        try:
            self._connection.enterTtyModeWithPath()
            self._set_priority(PRIORITY_DISABLED)
            columns, rows = self._connection.displaySize
            if columns <= 0 or rows <= 0:
                raise RuntimeError(f"invalid BrlAPI display size: {columns}x{rows}")
            self.num_cols = columns
            self.num_rows = rows
            self.num_cells = columns * rows
            self.model = _driver_name(self._connection)
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

    def start_input(self, on_gesture: Callable[[BrailleGesture], None]) -> None:
        """Forward display keys to ``on_gesture`` while the remote output is active.

        Like NVDA's driver, keys are polled without blocking under the same
        lock as ``writeDots``, and BrlAPI keysyms are left to BRLTTY (which
        types them into the focused window, i.e. FreeRDP).
        """
        self._on_gesture = on_gesture
        with self._lock:
            self._connection.ignoreKeys(self._api.rangeType_type, (self._api.KEY_TYPE_SYM,))
        self._input_thread = threading.Thread(
            target=self._input_loop,
            name="brlapi-keys",
            daemon=True,
        )
        self._input_thread.start()
        LOG.info("forwarding BrlAPI keys model=%r", self.model)

    def _input_loop(self) -> None:
        names = command_names(self._api)
        while not self._stop_input.wait(KEY_POLL_INTERVAL):
            self.poll_keys(names)

    def poll_keys(self, names: dict[int, str]) -> None:
        """Read pending keys once and forward them if the remote output is active."""
        for key in self._read_keys():
            gesture = translate_key(key, self._api, names, self.model)
            if gesture is None or self._on_gesture is None:
                continue
            with self._lock:
                active = self._active
            if active:
                self._on_gesture(gesture)
            else:
                LOG.debug("dropping braille gesture while idle: %s", gesture)

    def _read_keys(self) -> list[dict[str, int]]:
        keys: list[dict[str, int]] = []
        with self._lock:
            if self._connection is None:
                return keys
            while True:
                try:
                    code = self._connection.readKey(False)
                except Exception as error:  # noqa: BLE001 - BrlAPI raises its own exception types.
                    LOG.error("cannot read BrlAPI key: %s", error)
                    self._stop_input.set()
                    self.failed.set()
                    break
                if not code:
                    break
                keys.append(self._api.expandKeyCode(code))
        return keys

    def close(self) -> None:
        self._stop_input.set()
        if self._input_thread is not None:
            self._input_thread.join(timeout=1)
        with self._lock:
            try:
                if not self.failed.is_set():
                    self._set_priority(PRIORITY_DISABLED)
            except Exception as error:  # noqa: BLE001 - the connection may already be gone.
                LOG.debug("cannot reset BrlAPI priority during cleanup: %s", error)
            finally:
                self._active = False
                try:
                    self._close_connection()
                except Exception as error:  # noqa: BLE001 - the connection may already be gone.
                    LOG.debug("cannot close BrlAPI connection: %s", error)
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


class ReconnectingBrailleDisplay:
    """BrlAPI output that survives BRLTTY being unavailable or restarted.

    The braille channel must never take the speech channel down with it, so
    BrlAPI errors are contained here: while no BrlAPI connection exists, cells
    are only logged, and a background thread retries the connection every
    ``retry_interval`` seconds.  Until the first connection, the display size
    advertised to the remote NVDA is the fallback size; when the real size is
    known or changes, ``on_dimensions_changed`` is called so that the session
    can push the new ``numCells``/``numCols``/``numRows``, as RDAccess's
    ``_handleDisplayDimensionChanges`` does.
    """

    def __init__(
        self,
        *,
        priority: int = PRIORITY_REMOTE_ACTIVE,
        fallback_cells: int = 40,
        fallback_rows: int = 1,
        retry_interval: float = 5.0,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self._connect = connect or (lambda: BrlApiOutput(priority=priority))
        self._fallback = (fallback_cells, fallback_rows)
        self._retry_interval = retry_interval
        self._lock = threading.Lock()
        self._output: Any = None
        self._active = False
        self._on_gesture: Callable[[BrailleGesture], None] | None = None
        self.on_dimensions_changed: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self._output is not None

    @property
    def num_cells(self) -> int:
        output = self._output
        return output.num_cells if output is not None else self._fallback[0]

    @property
    def num_rows(self) -> int:
        output = self._output
        return output.num_rows if output is not None else self._fallback[1]

    def enable_input(self, on_gesture: Callable[[BrailleGesture], None]) -> None:
        """Forward display keys; applies to the current and future connections."""
        self._on_gesture = on_gesture

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="brlapi-connection", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._drop_output()

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = active
            output = self._output
        if output is not None:
            self._call(output, output.set_active, active)

    def display(self, cells: list[int]) -> None:
        output = self._output
        if output is None:
            LOG.debug("no BrlAPI connection; %d braille cells only logged", len(cells))
            return
        self._call(output, output.display, cells)

    def _call(self, output: Any, method: Callable[..., None], *args: object) -> None:
        try:
            method(*args)
        except Exception as error:  # noqa: BLE001 - BrlAPI raises its own exception types.
            LOG.error("BrlAPI connection failed: %s", error)
            output.failed.set()

    def _run(self) -> None:
        reported = False
        while not self._stop.is_set():
            output = self._output
            if output is not None:
                if output.failed.wait(self._retry_interval):
                    LOG.warning("BrlAPI connection lost; retrying every %gs", self._retry_interval)
                    self._drop_output()
                continue
            try:
                output = self._connect()
            except Exception as error:  # noqa: BLE001 - BrlAPI raises its own exception types.
                log = LOG.debug if reported else LOG.warning
                log("cannot connect to BrlAPI, retrying every %gs: %s", self._retry_interval, error)
                reported = True
                self._stop.wait(self._retry_interval)
                continue
            reported = False
            self._install(output)

    def _install(self, output: Any) -> None:
        previous_size = (self.num_cells, self.num_rows)
        with self._lock:
            self._output = output
            active = self._active
        if self._on_gesture is not None:
            self._call(output, output.start_input, self._on_gesture)
        if active:
            self._call(output, output.set_active, True)
        if (output.num_cells, output.num_rows) != previous_size and self.on_dimensions_changed:
            self.on_dimensions_changed()

    def _drop_output(self) -> None:
        with self._lock:
            output, self._output = self._output, None
        if output is not None:
            output.close()


def _driver_name(connection: Any) -> str | None:
    try:
        name = connection.driverName
    except (AttributeError, OSError, RuntimeError):
        return None
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    return name or None


class AtSpiFocusTracker:
    """Activate remote braille only while a FreeRDP application has focus."""

    def __init__(
        self,
        on_active_changed: Callable[[bool], None],
        *,
        application_patterns: tuple[str, ...] = DEFAULT_FOCUS_APPLICATIONS,
        registry: Any = None,
    ) -> None:
        if registry is None:
            import pyatspi

            registry = pyatspi.Registry
        self._registry = registry
        self._on_active_changed = on_active_changed
        self._patterns = tuple(pattern.casefold() for pattern in application_patterns)
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
        LOG.info("watching AT-SPI focus for application patterns=%r", self._patterns)

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
        matches = any(
            pattern in application_name.casefold() or pattern in window_name.casefold()
            for pattern in self._patterns
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
