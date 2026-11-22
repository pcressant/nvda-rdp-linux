"""Contract tests: the server's channel against the client's sessions.

The server side (``server.channel.Channel``, on the GLib loop) and the client
side (``client.braille.BrailleProtocolSession``, ``client.protocol.ProtocolSession``)
are connected through a Unix socket with the IPC framing, the test playing
the GNOME Remote Desktop preload and the FreeRDP plugin in between.  What
one side encodes, the other must decode.
"""

from __future__ import annotations

import select
import socket
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from gi.repository import GLib

from nvda_rdp_linux.client.braille import BrailleProtocolSession
from nvda_rdp_linux.client.protocol import ProtocolSession
from nvda_rdp_linux.common.braille import BrailleGesture
from nvda_rdp_linux.common.framing import MAX_FRAME_SIZE, FrameDecoder, encode_frame
from nvda_rdp_linux.common.speech import Prosody
from nvda_rdp_linux.server.channel import SOCKET_DIRECTORY, Channel
from nvda_rdp_linux.server.prosody import NEUTRAL, prosody_commands, reset_commands

TIMEOUT = 5.0


class Link:
    """The preload's socket, with a client session at its far end."""

    def __init__(self, directory: Path, name: str) -> None:
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(directory / name))
        self._listener.listen(1)
        self.sock: socket.socket | None = None
        self._frames = FrameDecoder()
        self.session: Any = None

    def accept(self) -> None:
        self.sock, _ = self._listener.accept()

    def send(self, payload: bytes) -> None:
        assert self.sock is not None
        self.sock.sendall(encode_frame(payload))

    def pump(self) -> None:
        """Run the server's loop once and hand what it sent to the client."""
        GLib.MainContext.default().iteration(False)
        if self.sock is None:
            return
        readable, _, _ = select.select([self.sock], [], [], 0.01)
        if readable:
            data = self.sock.recv(65536)
            for frame in self._frames.feed(data):
                self.session.feed(frame)

    def until(self, condition: Callable[[], bool]) -> None:
        deadline = time.monotonic() + TIMEOUT
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("timed out")
            self.pump()

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
        self._listener.close()


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # A short path: pytest's tmp_path is too long for a Unix socket.
    with tempfile.TemporaryDirectory(prefix="nvda-rdp-") as directory:
        monkeypatch.setenv("XDG_RUNTIME_DIR", directory)
        sockets = Path(directory) / SOCKET_DIRECTORY
        sockets.mkdir()
        yield sockets


def connect(runtime: Path, name: str, driver: bytes, file_name: str) -> Iterator[tuple]:
    link = Link(runtime, file_name)
    received: list[dict[str, Any]] = []
    ready: list[bool] = []
    channel = Channel(name, driver, file_name, received.append, ready.append)
    channel.start()
    link.accept()
    try:
        yield link, channel, received, ready
    finally:
        channel.stop()
        link.close()


@pytest.fixture
def braille(runtime: Path):
    yield from connect(runtime, "NVDA-BRAILLE", b"B", "nvda-braille.sock")


@pytest.fixture
def speech(runtime: Path):
    yield from connect(runtime, "NVDA-SPEECH", b"S", "nvda-speech.sock")


class Display:
    num_cells = 40
    num_rows = 1

    def __init__(self) -> None:
        self.shown: list[list[int]] = []

    def display(self, cells: list[int]) -> None:
        self.shown.append(cells)


GESTURE_MAP = {"globalCommands.GlobalCommands": {"braille_scrollBack": "br(brltty):fwinlt"}}


def test_braille_negotiation_attributes_cells_and_keys(braille) -> None:
    link, channel, received, ready = braille
    display = Display()
    link.session = BrailleProtocolSession(send=link.send, output=display, gesture_map=GESTURE_MAP)
    link.session.connect()
    link.until(lambda: channel.ready)
    assert ready == [True]

    # The server asks for the attributes the braille bridge needs.
    for attribute in ("numCells", "gestureMap"):
        channel.send("attribute_request", attribute=attribute)
    link.until(lambda: len(received) == 2)
    assert received == [
        {"type": "attribute_value", "attribute": "numCells", "value": 40},
        {
            "type": "attribute_value",
            "attribute": "gestureMap",
            "value": ["GlobalGestureMap", GESTURE_MAP],
        },
    ]
    assert channel.attributes["numCells"] == 40

    channel.send("display", cells=[0x43, 0xC0] + [0] * 38)
    link.until(lambda: display.shown)
    assert display.shown == [[0x43, 0xC0] + [0] * 38]

    link.session.send_gesture(BrailleGesture("brltty", "route", "al", 5))
    link.session.send_gesture(BrailleGesture("brltty", "passdots", dots=3, is_braille_input=True))
    link.until(lambda: len(received) == 4)
    assert received[2:] == [
        {
            "source": "brltty",
            "id": "route",
            "routingIndex": 5,
            "model": "al",
            "type": "braille_input",
        },
        {
            "source": "brltty",
            "id": "passdots",
            "routingIndex": None,
            "model": None,
            "dots": 3,
            "space": False,
            "type": "braille_input",
        },
    ]


def test_client_disconnect_makes_the_channel_not_ready(braille) -> None:
    link, channel, _received, ready = braille
    link.session = BrailleProtocolSession(send=link.send, output=Display())
    link.session.connect()
    link.until(lambda: channel.ready)
    link.sock.close()
    link.sock = None
    link.until(lambda: not channel.ready)
    assert ready == [True, False]


def test_xoff_detaches_and_xon_negotiates_again(braille) -> None:
    link, channel, _received, ready = braille
    link.session = BrailleProtocolSession(send=link.send, output=Display())
    link.session.connect()
    link.until(lambda: channel.ready)
    link.send(b"\x13")
    link.until(lambda: not channel.ready)
    link.session.disconnect()
    link.session.connect()
    link.until(lambda: channel.ready)
    assert ready == [True, False, True]


class Voice:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, dict[str, object]]] = []
        self.pending: list[Callable[[], None]] = []
        self.cancelled = 0

    def speak(
        self, text: str, *, on_done: Callable[[], None] | None = None, **state: object
    ) -> None:
        self.spoken.append((text, state))
        if on_done is not None:
            self.pending.append(on_done)

    def insert_break(self, milliseconds: int) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled += 1

    def pause(self, switch: bool) -> None:
        pass

    def available_languages(self) -> list[str | None]:
        return ["fr"]


def test_speech_prosody_and_indexes_round_trip(speech) -> None:
    link, channel, received, _ready = speech
    voice = Voice()
    link.session = ProtocolSession(voice, send=link.send, language="fr_FR")
    link.session.connect()
    link.until(lambda: channel.ready)

    channel.send("attribute_request", attribute="supportedCommands")
    channel.send("attribute_request", attribute="language")
    link.until(lambda: len(received) == 2)
    supported = set(channel.attributes["supportedCommands"])
    assert channel.attributes["language"] == "fr_FR"

    # What the speech module sends for an utterance spoken by Orca faster,
    # higher, quieter and in English.
    settings = {"rate": "40", "pitch": "20", "volume": "0", "language": "en-US"}
    before = prosody_commands(settings, NEUTRAL, "fr_FR", supported)
    assert len(before) == 4
    sequence = [
        ["IndexCommand", {"index": 1}],
        *before,
        "Hello ",
        ["IndexCommand", {"index": 2}],
        "world",
        *reset_commands(before),
        ["IndexCommand", {"index": 3}],
    ]
    channel.send("speak", sequence=sequence)
    link.until(lambda: len(voice.spoken) == 2)
    state = {
        "character_mode": False,
        "language": "en_US",
        "rate": Prosody(offset=20),
        "pitch": Prosody(offset=10),
        "volume": Prosody(multiplier=0.5),
    }
    assert voice.spoken == [("Hello ", state), ("world", state)]

    for done in voice.pending:
        done()
    link.until(lambda: len(received) == 6)
    assert [message["index"] for message in received[2:]] == [1, 2, 3, 0]

    channel.send("cancel")
    link.until(lambda: voice.cancelled)


def test_oversized_frame_closes_the_channel(braille) -> None:
    link, channel, _received, _ready = braille
    link.session = BrailleProtocolSession(send=link.send, output=Display())
    link.session.connect()
    link.until(lambda: channel.ready)
    link.sock.sendall((MAX_FRAME_SIZE + 1).to_bytes(4, "big"))
    link.until(lambda: not channel.ready)
    assert link.sock.recv(1) == b""


def test_a_failing_handler_keeps_the_channel(runtime: Path) -> None:
    link = Link(runtime, "nvda-braille.sock")
    received: list[dict[str, Any]] = []

    def on_message(message: dict[str, Any]) -> None:
        if message.get("attribute") == "fail":
            raise RuntimeError("handler failure")
        received.append(message)

    channel = Channel("NVDA-BRAILLE", b"B", "nvda-braille.sock", on_message)
    channel.start()
    link.accept()
    try:
        link.session = BrailleProtocolSession(send=link.send, output=Display())
        link.session.connect()
        link.until(lambda: channel.ready)
        link.send(b'{"type":"attribute_value","attribute":"fail","value":1}\n')
        link.send(b'{"type":"attribute_value","attribute":"ok","value":2}\n')
        link.until(lambda: received)
        assert received == [{"type": "attribute_value", "attribute": "ok", "value": 2}]
        assert channel.ready
    finally:
        channel.stop()
        link.close()
