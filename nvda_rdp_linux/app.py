"""Run the RDAccess client: speech and braille channels in one process.

The FreeRDP plugin connects to one Unix socket per DVC (``speech.sock`` and
``braille.sock``).  This process listens on both, each in its own thread, and
shares one Speech Dispatcher connection, one audio player and one
configuration between them.  A failure on one channel is logged and contained:
the other channel keeps working, a broken connection is followed by a new
``accept()``, and BrlAPI is reconnected in the background.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from pathlib import Path

from .client.audio import DEFAULT_PLAYER, AudioOutput, default_waves_dir
from .client.braille import BrailleProtocolSession
from .client.brltty import (
    BRLTTY_GESTURE_MAP,
    DEFAULT_FOCUS_APPLICATIONS,
    AtSpiFocusTracker,
    ReconnectingBrailleDisplay,
)
from .client.capslock import CapsLockSync, default_backend
from .client.ipc import UnixFrameServer, default_socket_path, receive_frames, send_frame
from .client.language import system_language
from .client.notify import MODES, ConnectionNotifier
from .client.protocol import ProtocolSession
from .client.session import RdAccessSession
from .client.speech import SpeechDispatcherOutput
from .common.braille import BrailleGesture

LOG = logging.getLogger(__name__)


def _stop(_signal_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nvda-rdp-client")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--language",
        default=system_language(),
        help="voice and message language, e.g. fr_FR (default: from the locale)",
    )
    parser.add_argument("--notify", choices=MODES, default="message")
    parser.add_argument(
        "--nvda-waves-dir",
        type=Path,
        default=default_waves_dir(),
        help="directory holding a copy of NVDA's waves (default: $NVDA_WAVES_DIR)",
    )
    parser.add_argument("--audio-player", default=" ".join(DEFAULT_PLAYER))

    speech = parser.add_argument_group("speech")
    speech.add_argument("--no-speech", action="store_true", help="do not handle NVDA-SPEECH")
    speech.add_argument("--rate", type=int, default=75, metavar="ORCA_RATE")
    speech.add_argument("--output-module", default="espeak-ng")
    speech.add_argument(
        "--pitch-change",
        type=int,
        default=10,
        help="pitch offset applied to remote speech, like RDAccess (0 disables)",
    )
    speech.add_argument(
        "--enable-pause",
        action="store_true",
        help="honour pause_speech (sd_espeak-ng may repeat the sentence on resume)",
    )
    speech.add_argument(
        "--settings-management",
        action="store_true",
        help="let the remote NVDA read and change the local voice settings",
    )

    braille = parser.add_argument_group("braille")
    braille.add_argument("--no-braille", action="store_true", help="do not handle NVDA-BRAILLE")
    braille.add_argument(
        "--braille-capture",
        action="store_true",
        help="only log braille cells; do not use BrlAPI",
    )
    braille.add_argument("--cells", type=int, default=40, help="size until BrlAPI answers")
    braille.add_argument("--rows", type=int, default=1)
    braille.add_argument("--brlapi-priority", type=int, default=60)
    braille.add_argument(
        "--no-braille-input",
        action="store_true",
        help="do not forward braille display keys to the remote NVDA",
    )
    braille.add_argument(
        "--focus-application",
        action="append",
        help="window or application name of the RDP client; repeatable "
        f"(default: {', '.join(DEFAULT_FOCUS_APPLICATIONS)})",
    )
    braille.add_argument(
        "--sync-caps-lock",
        action="store_true",
        help="align the local Caps Lock with the remote NVDA when the RDP client loses focus",
    )
    args = parser.parse_args(argv)
    if args.no_speech and args.no_braille:
        parser.error("--no-speech and --no-braille leave nothing to do")
    return args


def serve(
    server: UnixFrameServer,
    make_session: Callable[[Callable[[bytes], None]], RdAccessSession],
    notifier: ConnectionNotifier,
    on_session: Callable[[RdAccessSession | None], None] = lambda _session: None,
) -> None:
    """Accept plugin connections until the listener is closed.

    Errors raised while handling one connection are logged and the next
    connection is accepted, so a protocol bug never stops the process.
    """
    while True:
        try:
            connection = server.accept()
        except OSError:
            return
        with connection:
            session = make_session(
                lambda payload, connection=connection: send_frame(connection, payload)
            )
            on_session(session)
            try:
                session.connect()
                notifier.notify(True)
                for payload in receive_frames(connection):
                    session.feed(payload)
            except (OSError, ValueError) as error:
                LOG.warning("%s plugin connection failed: %s", session.KIND, error)
            except Exception:
                LOG.exception("unexpected error on the %s channel", session.KIND)
            finally:
                on_session(None)
                session.disconnect()
                LOG.info("FreeRDP %s plugin disconnected", session.KIND)
                notifier.notify(False)


def main(argv: list[str] | None = None) -> None:
    args = parse_arguments(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s.%(msecs)03d [nvda-rdp] %(levelname)s (%(threadName)s) %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _stop)

    audio = AudioOutput(waves_dir=args.nvda_waves_dir, player=args.audio_player.split())
    speech_output = None
    if not args.no_speech:
        try:
            speech_output = SpeechDispatcherOutput(
                language=args.language,
                orca_rate=args.rate,
                output_module=args.output_module,
            )
        except Exception:
            LOG.exception("cannot connect to Speech Dispatcher; speech channel disabled")
    speak = speech_output.speak if speech_output is not None else None

    with contextlib.ExitStack() as stack:
        caps_lock = None
        if args.sync_caps_lock:
            backend = default_backend()
            caps_lock = CapsLockSync(backend) if backend is not None else None
        on_caps_lock = caps_lock.set_remote_state if caps_lock is not None else None

        threads: list[threading.Thread] = []
        if speech_output is not None:
            stack.callback(speech_output.close)
            speech_server = stack.enter_context(UnixFrameServer(default_socket_path()))
            threads.append(
                threading.Thread(
                    target=serve,
                    args=(
                        speech_server,
                        lambda send: ProtocolSession(
                            speech_output,
                            send=send,
                            language=args.language,
                            audio=audio,
                            pitch_change=args.pitch_change,
                            enable_pause=args.enable_pause,
                            settings_management=args.settings_management,
                            request_versions=True,
                            on_caps_lock_toggle=on_caps_lock,
                        ),
                        ConnectionNotifier(
                            args.notify,
                            kind="speech",
                            language=args.language,
                            speak=speak,
                            audio=audio,
                        ),
                    ),
                    name="speech",
                    daemon=True,
                )
            )

        display = None
        if not args.no_braille:
            if not args.braille_capture:
                display = ReconnectingBrailleDisplay(
                    priority=args.brlapi_priority,
                    fallback_cells=args.cells,
                    fallback_rows=args.rows,
                )
                stack.callback(display.close)
            thread, current = _braille_channel(args, stack, display, speak, audio, on_caps_lock)
            threads.append(thread)
            if display is not None:

                def forward_gesture(gesture: BrailleGesture) -> None:
                    session = current()
                    if session is not None:
                        session.send_gesture(gesture)

                def push_dimensions() -> None:
                    session = current()
                    if session is not None:
                        session.push_dimensions()

                if not args.no_braille_input:
                    display.enable_input(forward_gesture)
                display.on_dimensions_changed = push_dimensions
                display.start()

        if display is not None or caps_lock is not None:

            def on_focus_changed(active: bool) -> None:
                if display is not None:
                    display.set_active(active)
                if caps_lock is not None:
                    caps_lock.set_remote_focused(active)

            focus_tracker = AtSpiFocusTracker(
                on_focus_changed,
                application_patterns=tuple(args.focus_application or DEFAULT_FOCUS_APPLICATIONS),
            )
            focus_tracker.start()
            stack.callback(focus_tracker.close)

        if not threads:
            LOG.error("no channel could be started")
            return
        for thread in threads:
            thread.start()
        try:
            while any(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(timeout=1)
        except KeyboardInterrupt:
            LOG.info("stopping on keyboard interrupt")


def _braille_channel(
    args: argparse.Namespace,
    stack: contextlib.ExitStack,
    display: ReconnectingBrailleDisplay | None,
    speak: Callable[[str], None] | None,
    audio: AudioOutput,
    on_caps_lock: Callable[[bool], None] | None,
) -> tuple[threading.Thread, Callable[[], BrailleProtocolSession | None]]:
    socket_path = default_socket_path(environment="NVDA_RDP_BRAILLE_SOCKET", name="braille.sock")
    server = stack.enter_context(UnixFrameServer(Path(socket_path)))
    braille_input = display is not None and not args.no_braille_input
    holder: list[BrailleProtocolSession | None] = [None]

    def make_session(send: Callable[[bytes], None]) -> BrailleProtocolSession:
        return BrailleProtocolSession(
            send=send,
            num_cells=args.cells,
            num_rows=args.rows,
            output=display,
            gesture_map=BRLTTY_GESTURE_MAP if braille_input else None,
            request_versions=True,
            on_caps_lock_toggle=on_caps_lock,
        )

    def on_session(session: RdAccessSession | None) -> None:
        holder[0] = session  # type: ignore[assignment]

    # Braille connection notifications only make sense with a braille display.
    def speak_if_display(text: str) -> None:
        if speak is not None and display is not None and display.connected:
            speak(text)

    notifier = ConnectionNotifier(
        args.notify if display is not None else "none",
        kind="braille",
        language=args.language,
        speak=speak_if_display,
        audio=audio,
    )
    thread = threading.Thread(
        target=serve,
        args=(server, make_session, notifier, on_session),
        name="braille",
        daemon=True,
    )
    return thread, lambda: holder[0]
