# SPDX-License-Identifier: GPL-2.0-or-later

"""``nvda-rdp-settings``: read and change the driver settings of NVDA on the
RDP client, from the remote session.

    nvda-rdp-settings speech list
    nvda-rdp-settings speech values voice
    nvda-rdp-settings speech set rate 60
    nvda-rdp-settings braille get <id>

It asks the server process of the channel (``settings_service``).  NVDA
answers only if driver settings management is allowed in RDAccess's
settings on the client.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any

from .settings_service import socket_path

TIMEOUT_SECONDS = 10
EXIT_ERROR = 1
NOT_ALLOWED = (
    "NVDA reports no driver settings: allow driver settings management in "
    "RDAccess's settings on the client, or its driver has none."
)


def request(channel: str, message: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect(socket_path(channel))
        sock.sendall(json.dumps(message).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def format_value(setting: dict[str, Any]) -> str:
    value = setting.get("value")
    if value is None:
        return "unknown"
    if setting.get("type") == "boolean":
        return "on" if value else "off"
    if setting.get("valueName") and setting["valueName"] != value:
        return f"{value}, {setting['valueName']}"
    return str(value)


def format_setting(setting: dict[str, Any]) -> str:
    line = f"{setting['id']}: {format_value(setting)} ({setting['name']}"
    if setting.get("type") == "numeric":
        line += f", {setting['min']} to {setting['max']}"
    elif setting.get("type") == "boolean":
        line += ", on or off"
    return line + ")"


def format_choice(choice: dict[str, Any]) -> str:
    line = f"{choice['id']}: {choice['name']}"
    if choice.get("language"):
        line += f" ({choice['language']})"
    return line


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nvda-rdp-settings",
        description="Read and change the driver settings of NVDA on the RDP client.",
    )
    parser.add_argument(
        "channel", choices=("speech", "braille"), help="NVDA's synthesizer or braille display"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="the settings and their values")
    get = commands.add_parser("get", help="one setting")
    get.add_argument("id")
    values = commands.add_parser("values", help="the possible values of a setting")
    values.add_argument("id")
    set_ = commands.add_parser("set", help="change a setting")
    set_.add_argument("id")
    set_.add_argument("value", help="a number, on or off, or the id or name of a possible value")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    message: dict[str, Any] = {"command": args.command}
    if args.command != "list":
        message["id"] = args.id
    if args.command == "set":
        message["value"] = args.value
    try:
        answer = request(args.channel, message)
    except (OSError, ValueError) as error:
        print(
            f"nvda-rdp-settings: the {args.channel} server of this session does not answer: {error}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if not answer.get("ok"):
        print(f"nvda-rdp-settings: {answer.get('message', 'failed')}", file=sys.stderr)
        return EXIT_ERROR
    if args.command == "list":
        settings = answer.get("settings") or []
        if not settings:
            print(NOT_ALLOWED)
        for setting in settings:
            print(format_setting(setting))
    elif args.command == "values":
        for choice in answer.get("values") or []:
            print(format_choice(choice))
    else:
        print(format_setting(answer["setting"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
