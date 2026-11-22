from __future__ import annotations

import socket
from pathlib import Path

import pytest

from nvda_rdp_linux.client.ipc import UnixFrameServer, receive_frames
from nvda_rdp_linux.common.framing import encode_frame


def test_socket_permissions_and_framing(tmp_path: Path) -> None:
    path = tmp_path / "private" / "speech.sock"
    with UnixFrameServer(path) as server:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(path))
        with client, server.accept() as connection:
            client.sendall(encode_frame(b"one") + encode_frame(b"two"))
            client.shutdown(socket.SHUT_WR)
            assert list(receive_frames(connection)) == [b"one", b"two"]
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not path.exists()


def test_reconnect_after_disconnect(tmp_path: Path) -> None:
    path = tmp_path / "speech.sock"
    with UnixFrameServer(path) as server:
        for payload in (b"first", b"second"):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(path))
            with client, server.accept() as connection:
                client.sendall(encode_frame(payload))
                client.shutdown(socket.SHUT_WR)
                assert list(receive_frames(connection)) == [payload]


def test_regular_file_is_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "speech.sock"
    path.write_text("keep me")
    with pytest.raises(RuntimeError, match="non-socket"), UnixFrameServer(path):
        pass
    assert path.read_text() == "keep me"
