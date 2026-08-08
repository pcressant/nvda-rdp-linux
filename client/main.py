"""Run the minimal user-space RDAccess speech client."""

from __future__ import annotations

import argparse
import logging
import signal

from .rdaccess.ipc import UnixFrameServer, default_socket_path, receive_frames, send_frame
from .rdaccess.protocol import ProtocolSession
from .rdaccess.speech import SpeechDispatcherOutput

LOG = logging.getLogger(__name__)


def _stop(_signal_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--language", default="fr")
    parser.add_argument("--rate", type=int, default=75, metavar="ORCA_RATE")
    parser.add_argument("--output-module", default="espeak-ng")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s.%(msecs)03d [rdaccess-client] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _stop)

    output = SpeechDispatcherOutput(
        language=args.language,
        orca_rate=args.rate,
        output_module=args.output_module,
    )
    try:
        with UnixFrameServer(default_socket_path()) as server:
            while True:
                with server.accept() as connection:
                    session = ProtocolSession(
                        output,
                        send=lambda payload: send_frame(connection, payload),
                        language=args.language,
                    )
                    try:
                        session.connect()
                        for payload in receive_frames(connection):
                            session.feed(payload)
                    finally:
                        session.disconnect()
                        LOG.info("FreeRDP plugin disconnected")
    except KeyboardInterrupt:
        LOG.info("stopping on keyboard interrupt")
    finally:
        output.close()


if __name__ == "__main__":
    main()
