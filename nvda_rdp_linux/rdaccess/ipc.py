"""Unix socket server connecting the FreeRDP plugin to user-space protocol code."""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Self

from .framing import FrameDecoder, encode_frame

LOG = logging.getLogger(__name__)
SOCKET_ENV = "NVDA_RDP_SOCKET"
SOCKET_NAME = "speech.sock"


def default_socket_path(*, environment: str = SOCKET_ENV, name: str = SOCKET_NAME) -> Path:
    configured = os.environ.get(environment)
    if configured:
        return Path(configured)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    return Path(runtime_dir) / "nvda-rdp-linux" / name


class UnixFrameServer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._listener: socket.socket | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            if not self.path.is_socket():
                raise RuntimeError(f"refusing to replace non-socket path: {self.path}")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(self.path))
            except ConnectionRefusedError:
                self.path.unlink()
            else:
                raise RuntimeError(f"another client is already listening on {self.path}")
            finally:
                probe.close()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(1)
        except Exception:
            listener.close()
            raise
        self._listener = listener
        LOG.info("listening for FreeRDP plugin on %s", self.path)
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def accept(self) -> socket.socket:
        if self._listener is None:
            raise RuntimeError("server is not listening")
        connection, _address = self._listener.accept()
        LOG.info("FreeRDP plugin connected")
        return connection


def receive_frames(connection: socket.socket) -> Iterator[bytes]:
    decoder = FrameDecoder()
    while data := connection.recv(65536):
        yield from decoder.feed(data)


def send_frame(connection: socket.socket, payload: bytes) -> None:
    connection.sendall(encode_frame(payload))
