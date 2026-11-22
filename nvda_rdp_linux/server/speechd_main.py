# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiment 07 (rdaccess_speechd/__main__.py).

"""``nvda-rdp-speechd-module``: a Speech Dispatcher output module.

Speech Dispatcher starts it (``AddModule``) and talks to it on stdin and
stdout, so its log goes to ``$XDG_RUNTIME_DIR/nvda-rdp-speechd-module.log``,
at the level of ``NVDA_RDP_LOG_LEVEL`` (default ``INFO``).
"""

from __future__ import annotations

import os

from .glib_loop import configure_logging, quit_on_signals
from .speechd_module import Module

LOG_LEVEL_ENV = "NVDA_RDP_LOG_LEVEL"
LOG_FILE = "nvda-rdp-speechd-module.log"


def main() -> None:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    configure_logging(
        "nvda-rdp-speechd",
        os.environ.get(LOG_LEVEL_ENV, "INFO"),
        filename=os.path.join(runtime, LOG_FILE),
    )
    module = Module()
    quit_on_signals(module.loop)
    module.run()


if __name__ == "__main__":
    main()
