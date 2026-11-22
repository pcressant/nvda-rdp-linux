# SPDX-License-Identifier: GPL-2.0-or-later
"""A Speech Dispatcher output module that speaks nothing and records the text.

Used by the client of the system test: each message Speech Dispatcher asks it
to speak is appended, on one line, to the file named by ``RECORDED_SPEECH``.
The replies follow Speech Dispatcher's module protocol (speechd@af73916:
src/modules/module_main.c), as nvda_rdp_linux/server/speechd_module.py does.
"""

import os
import sys

RECORD = os.environ["RECORDED_SPEECH"]


def reply(*lines: str) -> None:
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def read_block() -> list[str]:
    lines = []
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if line == ".":
            break
        lines.append(line.removeprefix("."))
    return lines


def main() -> None:
    for raw in sys.stdin:
        command = raw.rstrip("\n")
        if command == "INIT":
            reply("299-recorder", "299 OK LOADED SUCCESSFULLY")
        elif command in ("SPEAK", "CHAR", "KEY", "SOUND_ICON"):
            reply("202 OK RECEIVING MESSAGE")
            text = " ".join(read_block())
            with open(RECORD, "a", encoding="utf-8") as record:
                record.write(f"{command} {text}\n")
            reply("200 OK SPEAKING", "701 BEGIN", "702 END")
        elif command == "SET":
            reply("203 OK RECEIVING SETTINGS")
            read_block()
            reply("203 OK SETTINGS RECEIVED")
        elif command in ("AUDIO", "LOGLEVEL"):
            reply(f"207 OK RECEIVING {command} SETTINGS")
            read_block()
            reply(f"203 OK {command} SET" if command == "LOGLEVEL" else "203 OK AUDIO INITIALIZED")
        elif command.startswith("LIST VOICES"):
            reply("200-recorder\ten\tnone", "200 OK VOICE LIST SENT")
        elif command.startswith("DEBUG"):
            reply("200 OK DEBUGGING")
        elif command == "QUIT":
            reply("210 OK QUIT")
            return
        elif command in ("STOP", "PAUSE"):
            pass
        else:
            reply("300 ERR UNKNOWN COMMAND")


main()
