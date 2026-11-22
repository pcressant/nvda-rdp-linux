"""Run one scenario of the GNOME Remote Desktop preload, in a process started
with LD_PRELOAD set to it (see test_preload.py).

The process plays GNOME Remote Desktop: it loads the fake WinPR
(``fake_winpr.c``) and calls ``WTSOpenServerA`` and ``WTSCloseServer``, which
the preload interposes.  It also plays the nvda-rdp server on the channel
sockets.  The scenario prints one JSON object with what it observed.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path

HEADER = struct.Struct("!I")
TIMEOUT = 5.0
SPEECH = b"NVDA-SPEECH"
BRAILLE = b"NVDA-BRAILLE"

fake = ctypes.CDLL(sys.argv[1], mode=ctypes.RTLD_GLOBAL)
fake.fake_written.restype = ctypes.c_long
fake.fake_log.restype = ctypes.c_size_t
fake.fake_init()
# Resolved in the whole process: the preload's functions come first.
process = ctypes.CDLL(None)
process.WTSOpenServerA.restype = ctypes.c_void_p
process.WTSOpenServerA.argtypes = [ctypes.c_char_p]
process.WTSCloseServer.argtypes = [ctypes.c_void_p]
SOCKETS = Path(os.environ["XDG_RUNTIME_DIR"]) / "gnome-remote-desktop"


def until(condition: Callable[[], object], timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.01)


def written(name: bytes) -> list[bytes]:
    messages = []
    buffer = ctypes.create_string_buffer(1 << 20)
    for index in range(fake.fake_written_count(name)):
        length = fake.fake_written(name, index, buffer, len(buffer))
        messages.append(buffer.raw[:length])
    return messages


def log() -> list[str]:
    buffer = ctypes.create_string_buffer(65536)
    length = fake.fake_log(buffer, len(buffer))
    text = buffer.raw[:length].decode()
    return text.replace(os.environ["XDG_RUNTIME_DIR"], "{runtime}").splitlines()


def peer(name: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCKETS / name))
    sock.settimeout(TIMEOUT)
    return sock


def send_frame(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(HEADER.pack(len(payload)) + payload)


def receive_exactly(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("closed")
        data += chunk
    return data


def receive_frame(sock: socket.socket) -> bytes:
    (size,) = HEADER.unpack(receive_exactly(sock, HEADER.size))
    return receive_exactly(sock, size)


def closed_by_preload(sock: socket.socket) -> bool:
    try:
        return sock.recv(1) == b""
    except TimeoutError:
        return False


def sockets() -> dict[str, str]:
    if not SOCKETS.exists():
        return {}
    return {path.name: oct(stat.S_IMODE(path.stat().st_mode)) for path in SOCKETS.iterdir()}


def scenario_relay() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    result: dict[str, object] = {"sockets": sockets()}
    fake.fake_set_open_failures(SPEECH, 2)
    sock = peer("nvda-speech.sock")
    fake.fake_set_ready(SPEECH, 1)
    send_frame(sock, b"\x11")
    send_frame(sock, b"")
    send_frame(sock, b'{"type":"index","index":1}\n')
    until(lambda: fake.fake_written_count(SPEECH) == 2)
    large = bytes(range(256)) * 400
    for message in (b"S@\x12\x00`protocolVersion`\x02", b"", large):
        fake.fake_push(SPEECH, message, len(message))
    result["to_client"] = [message.hex(" ") for message in written(SPEECH)]
    result["from_client"] = [receive_frame(sock)[:24].hex(" "), len(receive_frame(sock))]
    result["large_intact"] = True
    result["braille_opens"] = fake.fake_opens(BRAILLE)
    process.WTSCloseServer(vcm)
    result["closed_by_preload"] = closed_by_preload(sock)
    result["sockets_after_close"] = sockets()
    result["opens"] = fake.fake_opens(SPEECH)
    result["closes"] = fake.fake_closes(SPEECH)
    result["server"] = [fake.fake_server_opens(), fake.fake_server_closes()]
    result["log"] = [line for line in log() if not line.startswith(("TRACE", "DEBUG"))]
    return result


def scenario_large_from_client() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    sock = peer("nvda-braille.sock")
    fake.fake_set_ready(BRAILLE, 1)
    until(lambda: fake.fake_opens(BRAILLE) == 1)
    time.sleep(0.2)
    large = os.urandom(300_000)
    fake.fake_push(BRAILLE, large, len(large))
    received = receive_frame(sock)
    send_frame(sock, large)
    until(lambda: fake.fake_written_count(BRAILLE) == 1)
    process.WTSCloseServer(vcm)
    return {"received": received == large, "written": written(BRAILLE) == [large]}


def scenario_oversized_frame() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    sock = peer("nvda-braille.sock")
    fake.fake_set_ready(BRAILLE, 1)
    until(lambda: fake.fake_opens(BRAILLE) == 1)
    sock.sendall(HEADER.pack(16 * 1024 * 1024 + 1))
    result = {"closed_by_preload": closed_by_preload(sock)}
    until(lambda: fake.fake_closes(BRAILLE) == 1)
    result["log"] = [line for line in log() if line.startswith("ERROR")]
    process.WTSCloseServer(vcm)
    return result


def scenario_partial_frame() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    sock = peer("nvda-braille.sock")
    sock.settimeout(10)
    fake.fake_set_ready(BRAILLE, 1)
    until(lambda: fake.fake_opens(BRAILLE) == 1)
    sock.sendall(HEADER.pack(10) + b"abc")
    start = time.monotonic()
    result = {"closed_by_preload": closed_by_preload(sock)}
    result["after_seconds"] = round(time.monotonic() - start, 1)
    result["written"] = fake.fake_written_count(BRAILLE)
    process.WTSCloseServer(vcm)
    return result


def scenario_client_closes_channel() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    sock = peer("nvda-speech.sock")
    fake.fake_set_ready(SPEECH, 1)
    until(lambda: fake.fake_opens(SPEECH) == 1)
    fake.fake_set_ready(SPEECH, 0)
    result = {"closed_by_preload": closed_by_preload(sock)}
    # A new local peer gets a new channel once the client opens it again.
    fake.fake_set_ready(SPEECH, 1)
    again = peer("nvda-speech.sock")
    send_frame(again, b"\x11")
    until(lambda: fake.fake_written_count(SPEECH) == 1)
    result["opens"] = fake.fake_opens(SPEECH)
    result["closes"] = fake.fake_closes(SPEECH)
    process.WTSCloseServer(vcm)
    result["closed_at_shutdown"] = closed_by_preload(again)
    return result


def scenario_second_server() -> dict[str, object]:
    first = process.WTSOpenServerA(None)
    second = process.WTSOpenServerA(None)
    process.WTSCloseServer(second)
    result = {"sockets_after_second_close": sockets()}
    process.WTSCloseServer(first)
    result["sockets_after_first_close"] = sockets()
    result["server"] = [fake.fake_server_opens(), fake.fake_server_closes()]
    result["warnings"] = [line for line in log() if line.startswith("WARN")]
    return result


def scenario_shutdown_while_opening() -> dict[str, object]:
    vcm = process.WTSOpenServerA(None)
    sock = peer("nvda-speech.sock")
    fake.fake_set_open_failures(SPEECH, 1000)
    time.sleep(0.5)
    start = time.monotonic()
    process.WTSCloseServer(vcm)
    return {
        "close_seconds": round(time.monotonic() - start),
        "closed_by_preload": closed_by_preload(sock),
        "opens": fake.fake_opens(SPEECH),
    }


def scenario_server_channel() -> dict[str, object]:
    """The nvda-rdp server's Channel against the preload, the client faked."""
    from gi.repository import GLib

    from nvda_rdp_linux.common.rdaccess import legacy_protocol_version_frame
    from nvda_rdp_linux.server.channel import Channel

    vcm = process.WTSOpenServerA(None)
    received: list[dict[str, object]] = []
    channel = Channel("NVDA-BRAILLE", b"B", "nvda-braille.sock", received.append)
    fake.fake_set_ready(BRAILLE, 1)
    channel.start()
    context = GLib.MainContext.default()

    def pump(condition: Callable[[], object]) -> None:
        until(lambda: context.iteration(False) or condition())

    until(lambda: fake.fake_opens(BRAILLE) == 1)
    time.sleep(0.2)
    for message in (b"\x11", legacy_protocol_version_frame(b"B")):
        fake.fake_push(BRAILLE, message, len(message))
    pump(lambda: channel.ready)
    channel.send("display", cells=[1, 2, 3])
    numcells = b'{"type":"attribute_value","attribute":"numCells","value":40}\n'
    fake.fake_push(BRAILLE, numcells, len(numcells))
    pump(lambda: received and fake.fake_written_count(BRAILLE) == 3)
    result = {
        "to_client": [message.decode("latin-1") for message in written(BRAILLE)],
        "received": received,
    }
    channel.stop()
    process.WTSCloseServer(vcm)
    return result


if __name__ == "__main__":
    print(json.dumps(globals()[f"scenario_{sys.argv[2]}"]()))
    sys.stdout.flush()
    # The preload's threads have been joined; skip the interpreter's cleanup.
    os._exit(0)
