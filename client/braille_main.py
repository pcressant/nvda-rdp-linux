"""Run the capture-only RDAccess braille client."""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from .rdaccess.braille import BrailleProtocolSession
from .rdaccess.brltty import AtSpiFocusTracker, BrlApiOutput
from .rdaccess.ipc import UnixFrameServer, default_socket_path, receive_frames, send_frame

LOG = logging.getLogger(__name__)


def _stop(_signal_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--cells", type=int, default=40)
    parser.add_argument("--rows", type=int, default=1)
    parser.add_argument("--brlapi", action="store_true")
    parser.add_argument("--brlapi-priority", type=int, default=60)
    parser.add_argument("--focus-application", default="freerdp")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s.%(msecs)03d [rdaccess-braille] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _stop)

    output = None
    focus_tracker = None
    if args.brlapi:
        output = BrlApiOutput(priority=args.brlapi_priority)
        focus_tracker = AtSpiFocusTracker(
            output.set_active,
            application_pattern=args.focus_application,
        )
        focus_tracker.start()

    socket_path = default_socket_path(environment="NVDA_RDP_BRAILLE_SOCKET", name="braille.sock")
    try:
        with UnixFrameServer(Path(socket_path)) as server:
            while True:
                with server.accept() as connection:
                    session = BrailleProtocolSession(
                        send=lambda payload: send_frame(connection, payload),
                        num_cells=args.cells,
                        num_rows=args.rows,
                        output=output,
                    )
                    try:
                        session.connect()
                        for payload in receive_frames(connection):
                            session.feed(payload)
                    finally:
                        session.disconnect()
                        LOG.info("FreeRDP braille plugin disconnected")
    except KeyboardInterrupt:
        LOG.info("stopping on keyboard interrupt")
    finally:
        if focus_tracker is not None:
            focus_tracker.close()
        if output is not None:
            output.close()


if __name__ == "__main__":
    main()
