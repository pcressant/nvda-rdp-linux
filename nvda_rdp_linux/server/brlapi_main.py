# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 06 (rdaccess_brlapi/__main__.py).

"""``nvda-rdp-brlapi-server``: the client's braille display as a BrlAPI display.

It listens on 127.0.0.1, port 4101 + N, and writes a fresh key to the key
file at each start.  Orca then needs, in its environment (the number after
the colon is N, not the port: brltty@3c0a178, Programs/brlapi_common.h:422-435):

    BRLAPI_HOST=127.0.0.1:N
    BRLAPI_AUTH=keyfile:FILE
"""

from __future__ import annotations

import argparse
import locale
import logging
import os
from pathlib import Path

from gi.repository import GLib

from .braille_bridge import BrailleBridge
from .glib_loop import configure_logging, quit_on_signals

LOG = logging.getLogger(__name__)

BRLAPI_BASE_PORT = 4101  # brlapi.h.in, BRLAPI_SOCKETPORTNUM
TABLES_SUBDIRECTORY = Path("share") / "liblouis" / "tables"


def default_tablesdir() -> str:
    """liblouis' tables: next to the louis package (Nix), else the FHS location."""
    import louis

    prefix = Path(louis.__file__).resolve().parents[4]
    for candidate in (prefix / TABLES_SUBDIRECTORY, Path("/usr") / TABLES_SUBDIRECTORY):
        if candidate.is_dir():
            return str(candidate)
    return str(Path("/usr") / TABLES_SUBDIRECTORY)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    parser = argparse.ArgumentParser(
        prog="nvda-rdp-brlapi-server", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--display", type=int, default=os.getuid(), help="BrlAPI display number (default: the UID)"
    )
    parser.add_argument(
        "--key",
        default=os.path.join(runtime, "nvda-rdp-brlapi.key"),
        help="key file written at start (default: %(default)s)",
    )
    parser.add_argument("--tables", default=None, help="liblouis tables directory")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def use_environment_locale() -> None:
    """Take the locale of the environment, which selects Orca's default table.

    A locale that is not installed leaves the C locale, as it does for Orca,
    instead of stopping the server.
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error as error:
        LOG.warning("Locale of the environment unavailable (%s): using C", error)


def main(argv: list[str] | None = None) -> None:
    args = parse_arguments(argv)
    configure_logging("nvda-rdp-brlapi", args.log_level)
    use_environment_locale()
    bridge = BrailleBridge(
        BRLAPI_BASE_PORT + args.display, args.key, args.tables or default_tablesdir()
    )
    bridge.start()
    loop = GLib.MainLoop()
    quit_on_signals(loop)
    try:
        loop.run()
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
