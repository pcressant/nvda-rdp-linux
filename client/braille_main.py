"""Run the user-space RDAccess braille client."""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from .rdaccess.audio import DEFAULT_PLAYER, AudioOutput
from .rdaccess.braille import BrailleProtocolSession
from .rdaccess.brltty import BRLTTY_GESTURE_MAP, AtSpiFocusTracker, BrlApiOutput
from .rdaccess.capslock import CapsLockSync, default_backend
from .rdaccess.ipc import UnixFrameServer, default_socket_path, receive_frames, send_frame
from .rdaccess.language import system_language
from .rdaccess.notify import MODES, ConnectionNotifier, LazySpeaker

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
    parser.add_argument(
        "--no-braille-input",
        action="store_true",
        help="do not forward braille display keys to the remote NVDA",
    )
    parser.add_argument("--focus-application", default="freerdp")
    parser.add_argument(
        "--sync-caps-lock",
        action="store_true",
        help="align the local Caps Lock with the remote NVDA when FreeRDP loses focus",
    )
    parser.add_argument(
        "--language",
        default=system_language(),
        help="voice and message language, e.g. fr_FR (default: from the locale)",
    )
    parser.add_argument(
        "--notify",
        choices=MODES,
        default=None,
        help="connection notifications (default: message with --brlapi, none otherwise)",
    )
    parser.add_argument("--nvda-waves-dir", type=Path)
    parser.add_argument("--audio-player", default=" ".join(DEFAULT_PLAYER))
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s.%(msecs)03d [rdaccess-braille] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _stop)

    output = None
    focus_tracker = None
    caps_lock = None
    if args.sync_caps_lock:
        backend = default_backend()
        caps_lock = CapsLockSync(backend) if backend is not None else None
    if args.brlapi:
        output = BrlApiOutput(priority=args.brlapi_priority)
    if output is not None or caps_lock is not None:

        def on_focus_changed(active: bool) -> None:
            if output is not None:
                output.set_active(active)
            if caps_lock is not None:
                caps_lock.set_remote_focused(active)

        focus_tracker = AtSpiFocusTracker(
            on_focus_changed,
            application_pattern=args.focus_application,
        )
        focus_tracker.start()

    speaker = LazySpeaker(args.language)
    notifier = ConnectionNotifier(
        args.notify or ("message" if args.brlapi else "none"),
        kind="braille",
        language=args.language,
        speak=speaker,
        audio=AudioOutput(waves_dir=args.nvda_waves_dir, player=args.audio_player.split()),
    )
    braille_input = output is not None and not args.no_braille_input
    socket_path = default_socket_path(environment="NVDA_RDP_BRAILLE_SOCKET", name="braille.sock")
    session: BrailleProtocolSession | None = None

    def forward_gesture(gesture) -> None:
        if session is not None:
            session.send_gesture(gesture)

    if braille_input:
        output.start_input(forward_gesture)
    try:
        with UnixFrameServer(Path(socket_path)) as server:
            while True:
                with server.accept() as connection:
                    session = BrailleProtocolSession(
                        send=lambda payload: send_frame(connection, payload),
                        num_cells=args.cells,
                        num_rows=args.rows,
                        output=output,
                        gesture_map=BRLTTY_GESTURE_MAP if braille_input else None,
                        request_versions=True,
                        on_caps_lock_toggle=(
                            caps_lock.set_remote_state if caps_lock is not None else None
                        ),
                    )
                    try:
                        session.connect()
                        notifier.notify(True)
                        for payload in receive_frames(connection):
                            session.feed(payload)
                    finally:
                        session.disconnect()
                        LOG.info("FreeRDP braille plugin disconnected")
                        notifier.notify(False)
    except KeyboardInterrupt:
        LOG.info("stopping on keyboard interrupt")
    finally:
        if focus_tracker is not None:
            focus_tracker.close()
        if output is not None:
            output.close()
        speaker.close()


if __name__ == "__main__":
    main()
