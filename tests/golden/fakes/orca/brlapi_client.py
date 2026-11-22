"""Orca's side of BrlAPI for the golden harness, with the real BrlAPI library.

Like Orca, it finds the server through ``BRLAPI_HOST`` and ``BRLAPI_AUTH``
(passing them as arguments makes BRLTTY 6.9's Python binding crash).  It runs
in its own process: one JSON command per line on stdin, one JSON result per
line on stdout.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time

import brlapi

KEY_QUIET_SECONDS = 0.6
BRLAPI_BASE_PORT = 4101
PACKET_HEADER = struct.Struct("!II")


def raw_auth(key: bytes) -> dict[str, object]:
    """Speak the BrlAPI handshake by hand with a given key.

    The library cannot be used for a refused authorization: its Python
    binding crashes when the connection fails.
    """
    host, _, display = os.environ["BRLAPI_HOST"].partition(":")
    packets = []
    with socket.create_connection((host, BRLAPI_BASE_PORT + int(display)), timeout=5) as sock:
        stream = sock.makefile("rb")

        def receive() -> None:
            header = stream.read(PACKET_HEADER.size)
            if len(header) < PACKET_HEADER.size:
                packets.append({"closed": True})
                return
            size, packet_type = PACKET_HEADER.unpack(header)
            packets.append({"type": packet_type, "payload": stream.read(size).hex(" ")})

        receive()
        sock.sendall(PACKET_HEADER.pack(4, ord("v")) + struct.pack("!I", 8))
        receive()
        sock.sendall(PACKET_HEADER.pack(4 + len(key), ord("a")) + struct.pack("!I", ord("K")) + key)
        receive()
        receive()
    return {"packets": packets}


def text(value: object) -> object:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def read_keys(connection) -> dict[str, object]:
    keys = []
    deadline = time.monotonic() + KEY_QUIET_SECONDS
    while time.monotonic() < deadline:
        code = connection.readKey(False)
        if code:
            expanded = connection.expandKeyCode(code)
            # Orca accepts commands only: the type is always KEY_TYPE_CMD.
            keys.append({name: expanded[name] for name in ("command", "argument", "flags")})
            deadline = time.monotonic() + KEY_QUIET_SECONDS
        else:
            time.sleep(0.02)
    return {"keys": keys}


def run(connection, step: dict[str, object]):
    action = step["orca"]
    if action == "connect":
        connection = brlapi.Connection()
        return connection, {
            "displaySize": list(connection.displaySize),
            "driverName": text(connection.driverName),
            "modelIdentifier": text(connection.modelIdentifier),
        }
    if action == "raw_auth":
        return connection, raw_auth(bytes.fromhex(step["key"]))
    if action == "enter":
        connection.enterTtyModeWithPath()
    elif action == "accept_commands":
        # Orca's key ranges (orca/braille.py): commands only.
        connection.ignoreKeys(brlapi.rangeType_all, [0])
        connection.acceptKeys(brlapi.rangeType_type, [brlapi.KEY_TYPE_CMD])
    elif action == "write":
        write = brlapi.WriteStruct()
        for name in ("text", "cursor", "regionBegin", "regionSize", "charset"):
            if name in step:
                setattr(write, name, step[name])
        if "attrOr" in step:
            write.attrOr = bytes(step["attrOr"])
        connection.write(write)
    elif action == "size":
        return connection, {"displaySize": list(connection.displaySize)}
    elif action == "keys":
        return connection, read_keys(connection)
    elif action == "leave":
        connection.leaveTtyMode()
    elif action == "close":
        connection.closeConnection()
        return None, {}
    else:
        raise ValueError(f"unknown orca step: {step}")
    return connection, {}


def main() -> None:
    connection = None
    for line in sys.stdin:
        step = json.loads(line)
        try:
            connection, result = run(connection, step)
        except (brlapi.ConnectionError, brlapi.OperationError, OSError) as error:
            result = {"error": f"{type(error).__name__}: {error}"}
        print(json.dumps({"action": step["orca"], **result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
