"""Logging and main loop setup shared by the server programs."""

from __future__ import annotations

import logging
import signal

from gi.repository import GLib

try:  # GLib 2.80 moved the Unix functions to GLibUnix.
    from gi.repository import GLibUnix

    _signal_add = GLibUnix.signal_add
except ImportError:
    _signal_add = GLib.unix_signal_add


def configure_logging(program: str, level: str, filename: str | None = None) -> None:
    logging.basicConfig(
        level=level,
        filename=filename,
        format=f"%(asctime)s.%(msecs)03d [{program}] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def quit_on_signals(loop: GLib.MainLoop) -> None:
    """Quit the loop on SIGTERM and SIGINT, so that cleanup code runs."""

    def quit_loop() -> bool:
        loop.quit()
        return GLib.SOURCE_REMOVE

    for signum in (signal.SIGTERM, signal.SIGINT):
        _signal_add(GLib.PRIORITY_DEFAULT, signum, quit_loop)
