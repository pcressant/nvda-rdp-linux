# SPDX-License-Identifier: GPL-2.0-or-later

"""The driver settings of one channel, offered to ``nvda-rdp-settings``.

Each server process listens on a Unix socket of the session,
``$XDG_RUNTIME_DIR/nvda-rdp-server/<channel>-settings.sock`` (``speech`` or
``braille``), only reachable by the user.  A request is one JSON line, the
answer one JSON line, then the connection closes:

* ``{"command": "list"}``: the settings with their values;
* ``{"command": "get", "id": ...}``: one setting;
* ``{"command": "values", "id": ...}``: the possible values of a setting
  that is neither numeric nor boolean;
* ``{"command": "set", "id": ..., "value": "..."}``: a new value, given as
  text: a number, ``on``/``off``, or the id or name of a possible value.

The client is asked again before each answer (``DriverSettings.refresh``).
An answer is ``{"ok": true, ...}`` or ``{"ok": false, "error": code,
"message": text}``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from .driver_settings import KIND_BOOLEAN, KIND_NUMERIC, Choice, DriverSettings, Setting

LOG = logging.getLogger(__name__)

STATE_DIRECTORY = "nvda-rdp-server"
MAX_REQUEST = 4096
TRUE_WORDS = ("on", "true", "yes", "1")
FALSE_WORDS = ("off", "false", "no", "0")


def socket_path(channel: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, STATE_DIRECTORY, f"{channel}-settings.sock")


class SettingsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def describe(setting: Setting, value: object, choices: list[Choice] | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": setting.id,
        "name": setting.name,
        "type": setting.kind,
        "value": value,
    }
    if setting.kind == KIND_NUMERIC:
        result.update(min=setting.min, max=setting.max, step=setting.step)
    for choice in choices or []:
        if choice.id == value:
            result["valueName"] = choice.name
    return result


def describe_choice(choice: Choice) -> dict[str, Any]:
    result: dict[str, Any] = {"id": choice.id, "name": choice.name}
    if choice.language:
        result["language"] = choice.language
    return result


def convert(setting: Setting, text: str, choices: list[Choice] | None) -> object:
    """The value to send for ``text``, or SettingsError."""
    if setting.kind == KIND_NUMERIC:
        try:
            number = int(text)
        except ValueError:
            raise SettingsError("invalid-value", f"{setting.id} needs a whole number") from None
        if not setting.min <= number <= setting.max:
            raise SettingsError(
                "invalid-value", f"{setting.id} goes from {setting.min} to {setting.max}"
            )
        return number
    if setting.kind == KIND_BOOLEAN:
        word = text.casefold()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise SettingsError("invalid-value", f"{setting.id} is on or off")
    if choices is None:
        raise SettingsError("no-values", f"the possible values of {setting.id} are unknown")
    for choice in choices:
        if text == choice.id:
            return choice.id
    for choice in choices:
        if text.casefold() == choice.name.casefold():
            return choice.id
    raise SettingsError("invalid-value", f"{text!r} is not a value of {setting.id}")


class SettingsService:
    def __init__(self, channel: str, settings: DriverSettings) -> None:
        self._channel = channel
        self._settings = settings
        self._path = socket_path(channel)
        self._listener: socket.socket | None = None
        self._watch_id = 0

    def start(self) -> None:
        os.makedirs(os.path.dirname(self._path), mode=0o700, exist_ok=True)
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            listener.bind(self._path)
            os.chmod(self._path, 0o600)
            listener.listen(4)
        except OSError as error:
            listener.close()
            LOG.warning("no driver settings socket %s: %s", self._path, error)
            return
        listener.setblocking(False)
        self._listener = listener
        self._watch_id = GLib.io_add_watch(
            listener.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, self._on_accept
        )

    def stop(self) -> None:
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = 0
        if self._listener is not None:
            self._listener.close()
            self._listener = None
            try:
                os.unlink(self._path)
            except OSError:
                pass

    def _on_accept(self, _fd: int, _condition: GLib.IOCondition) -> bool:
        assert self._listener is not None
        try:
            conn, _address = self._listener.accept()
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        conn.setblocking(False)
        buffer = bytearray()

        def on_data(_fd: int, _condition: GLib.IOCondition) -> bool:
            try:
                data = conn.recv(MAX_REQUEST)
            except BlockingIOError:
                return GLib.SOURCE_CONTINUE
            except OSError:
                data = b""
            buffer.extend(data)
            if b"\n" not in buffer and data and len(buffer) < MAX_REQUEST:
                return GLib.SOURCE_CONTINUE
            line = bytes(buffer).split(b"\n", 1)[0]
            self._handle(line, lambda answer: self._answer(conn, answer))
            return GLib.SOURCE_REMOVE

        GLib.io_add_watch(
            conn.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            on_data,
        )
        return GLib.SOURCE_CONTINUE

    def _answer(self, conn: socket.socket, answer: dict[str, Any]) -> None:
        try:
            conn.setblocking(True)
            conn.sendall(json.dumps(answer, ensure_ascii=False).encode() + b"\n")
        except OSError as error:
            LOG.debug("cannot answer a settings request: %s", error)
        finally:
            conn.close()

    # Requests

    def _handle(self, line: bytes, reply: Callable[[dict[str, Any]], None]) -> None:
        try:
            request = json.loads(line)
        except ValueError:
            request = None
        if not isinstance(request, dict):
            reply({"ok": False, "error": "invalid-request", "message": "invalid request"})
            return
        LOG.info("driver settings request %s", request)

        def answer() -> None:
            try:
                reply({"ok": True, **self._run(request)})
            except SettingsError as error:
                reply({"ok": False, "error": error.code, "message": str(error)})

        self._settings.refresh(answer)

    def _run(self, request: dict[str, Any]) -> dict[str, Any]:
        settings = self._settings.supported()
        if not self._settings.ready:
            raise SettingsError(
                "not-attached", f"NVDA is not attached to the {self._channel} channel"
            )
        if settings is None:
            raise SettingsError("unknown", "the client did not report its driver settings")
        command = request.get("command")
        if not settings and command != "list":
            raise SettingsError(
                "not-allowed",
                "NVDA reports no driver settings: driver settings management is off in "
                "RDAccess on the client, or its driver has none",
            )
        if command == "list":
            return {"settings": [self._describe(s) for s in settings]}
        if command not in ("get", "values", "set"):
            raise SettingsError("invalid-request", f"unknown command {command!r}")
        setting = self._settings.setting(str(request.get("id")))
        if setting is None:
            raise SettingsError("unknown-setting", f"no setting {request.get('id')!r}")
        if command == "get":
            return {"setting": self._describe(setting)}
        if command == "values":
            if setting.kind in (KIND_NUMERIC, KIND_BOOLEAN):
                kind = "a number" if setting.kind == KIND_NUMERIC else "on or off"
                raise SettingsError(
                    "no-values", f"{setting.id} has no list of values: it is {kind}"
                )
            choices = self._settings.choices(setting.id)
            if choices is None:
                raise SettingsError("no-values", f"the possible values of {setting.id} are unknown")
            return {"values": [describe_choice(c) for c in choices]}
        text = request.get("value")
        if not isinstance(text, str):
            raise SettingsError("invalid-request", "set needs a value")
        value = convert(setting, text, self._settings.choices(setting.id))
        if not self._settings.set(setting.id, value):
            raise SettingsError(
                "not-attached", f"NVDA is not attached to the {self._channel} channel"
            )
        return {"setting": self._describe(setting)}

    def _describe(self, setting: Setting) -> dict[str, Any]:
        return describe(
            setting, self._settings.value(setting.id), self._settings.choices(setting.id)
        )
