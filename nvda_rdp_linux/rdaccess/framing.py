"""Length-prefixed framing used on the local Unix stream socket."""

from __future__ import annotations

import struct

HEADER = struct.Struct("!I")
MAX_FRAME_SIZE = 16 * 1024 * 1024


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError("IPC frame is too large")
    return HEADER.pack(len(payload)) + payload


class FrameDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []

        while len(self._buffer) >= HEADER.size:
            (size,) = HEADER.unpack_from(self._buffer)
            if size > MAX_FRAME_SIZE:
                raise ValueError(f"IPC frame is too large: {size} bytes")
            end = HEADER.size + size
            if len(self._buffer) < end:
                break
            frames.append(bytes(self._buffer[HEADER.size : end]))
            del self._buffer[:end]

        return frames

    def reset(self) -> None:
        self._buffer.clear()
