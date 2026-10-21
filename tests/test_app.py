from __future__ import annotations

from typing import Self

import pytest

from nvda_rdp_linux.app import parse_arguments, serve


def test_language_and_channel_options() -> None:
    args = parse_arguments(["--language", "de_DE", "--no-braille"])
    assert (args.language, args.no_speech, args.no_braille) == ("de_DE", False, True)
    with pytest.raises(SystemExit):
        parse_arguments(["--no-speech", "--no-braille"])


class FakeConnection:
    def __init__(self, payloads: list[bytes]) -> None:
        import struct

        self._data = b"".join(struct.pack("!I", len(p)) + p for p in payloads)
        self.sent: list[bytes] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        data, self._data = self._data, b""
        return data

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class FakeServer:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self._connections = connections

    def accept(self) -> FakeConnection:
        if not self._connections:
            raise OSError("listener closed")
        return self._connections.pop(0)


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[bool] = []

    def notify(self, connected: bool) -> None:
        self.events.append(connected)


class ExplodingSession:
    KIND = "test"

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.fed: list[bytes] = []

    def connect(self) -> None:
        pass

    def feed(self, payload: bytes) -> None:
        if self.fail:
            raise KeyError("protocol bug")
        self.fed.append(payload)

    def disconnect(self) -> None:
        pass


def test_serve_survives_an_error_on_one_connection(caplog: pytest.LogCaptureFixture) -> None:
    sessions = [ExplodingSession(fail=True), ExplodingSession(fail=False)]
    notifier = FakeNotifier()
    serve(
        FakeServer([FakeConnection([b"a"]), FakeConnection([b"b"])]),
        lambda _send: sessions.pop(0) if len(sessions) == 2 else sessions[0],
        notifier,
    )
    assert "unexpected error on the test channel" in caplog.text
    assert notifier.events == [True, False, True, False]
