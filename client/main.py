"""Run the user-space RDAccess speech client."""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from .rdaccess.audio import DEFAULT_PLAYER, AudioOutput
from .rdaccess.ipc import UnixFrameServer, default_socket_path, receive_frames, send_frame
from .rdaccess.language import system_language
from .rdaccess.notify import MODES, ConnectionNotifier
from .rdaccess.protocol import ProtocolSession
from .rdaccess.speech import SpeechDispatcherOutput

LOG = logging.getLogger(__name__)


def _stop(_signal_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--language",
        default=system_language(),
        help="voice and message language, e.g. fr_FR (default: from the locale)",
    )
    parser.add_argument("--rate", type=int, default=75, metavar="ORCA_RATE")
    parser.add_argument("--output-module", default="espeak-ng")
    parser.add_argument(
        "--pitch-change",
        type=int,
        default=10,
        help="pitch offset applied to remote speech, like RDAccess (0 disables)",
    )
    parser.add_argument(
        "--enable-pause",
        action="store_true",
        help="honour pause_speech (sd_espeak-ng may repeat the sentence on resume)",
    )
    parser.add_argument(
        "--settings-management",
        action="store_true",
        help="let the remote NVDA read and change the local voice settings",
    )
    parser.add_argument(
        "--nvda-waves-dir",
        type=Path,
        help="directory holding a copy of NVDA's waves (error.wav, ...)",
    )
    parser.add_argument("--audio-player", default=" ".join(DEFAULT_PLAYER))
    parser.add_argument("--notify", choices=MODES, default="message")
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
    audio = AudioOutput(waves_dir=args.nvda_waves_dir, player=args.audio_player.split())
    notifier = ConnectionNotifier(
        args.notify,
        kind="speech",
        language=args.language,
        speak=output.speak,
        audio=audio,
    )
    try:
        with UnixFrameServer(default_socket_path()) as server:
            while True:
                with server.accept() as connection:
                    session = ProtocolSession(
                        output,
                        send=lambda payload: send_frame(connection, payload),
                        language=args.language,
                        audio=audio,
                        pitch_change=args.pitch_change,
                        enable_pause=args.enable_pause,
                        settings_management=args.settings_management,
                        request_versions=True,
                    )
                    try:
                        session.connect()
                        notifier.notify(True)
                        for payload in receive_frames(connection):
                            session.feed(payload)
                    finally:
                        session.disconnect()
                        LOG.info("FreeRDP plugin disconnected")
                        notifier.notify(False)
    except KeyboardInterrupt:
        LOG.info("stopping on keyboard interrupt")
    finally:
        output.close()


if __name__ == "__main__":
    main()
