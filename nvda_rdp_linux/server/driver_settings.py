# SPDX-License-Identifier: GPL-2.0-or-later
# Behaviour from rdAccess 2.1.0: addon/lib/driver/__init__.py (supportedSettings
# receiver) and addon/lib/driver/settingsAccessor.py. No code copied.

"""The client's driver settings, through one RDAccess channel.

With "driver settings management" allowed in RDAccess on the client, the
client tells which settings its synthesizer or braille display driver has
(``supportedSettings``); the server reads each one (``setting_<id>``), the
possible values of the settings that are neither numeric nor boolean
(``available<Id>s``), and changes a setting by sending its new value.
Without that permission, the client reports no settings and ignores
changes.

As RDAccess's remote driver, the server does not ask for
``supportedSettings``: NVDA pushes it when it connects and when its driver
changes.  When it arrives, every value is requested.  ``refresh`` asks
again, for a client that only answers requests.

Values are kept in the channel's attributes, so that they are forgotten
when a client attaches or detaches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gi.repository import GLib

from ..common.rdaccess import (
    ATTRIBUTE_SUPPORTED_SETTINGS,
    MESSAGE_ATTRIBUTE_REQUEST,
    MESSAGE_ATTRIBUTE_VALUE,
)
from ..common.settings import (
    available_values_attribute,
    setting_attribute,
    setting_id_from_attribute,
)
from .channel import Channel

LOG = logging.getLogger(__name__)

REFRESH_TIMEOUT_MS = 1500

KIND_NUMERIC = "numeric"
KIND_BOOLEAN = "boolean"
KIND_STRING = "string"
_KINDS = {
    "NumericDriverSetting": KIND_NUMERIC,
    "BooleanDriverSetting": KIND_BOOLEAN,
    "DriverSetting": KIND_STRING,
}


@dataclass(frozen=True)
class Setting:
    id: str
    kind: str
    name: str
    min: int = 0
    max: int = 100
    step: int = 1


@dataclass(frozen=True)
class Choice:
    """One possible value of a string setting (NVDA's StringParameterInfo or VoiceInfo)."""

    id: str
    name: str
    language: str | None = None


def _int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def parse_supported_settings(value: object) -> list[Setting] | None:
    """The settings of a ``supportedSettings`` value, or None when it is invalid."""
    if not isinstance(value, list):
        return None
    settings = []
    for item in value:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict)):
            continue
        kind = _KINDS.get(item[0])
        fields = item[1]
        setting_id = fields.get("id")
        if kind is None or not isinstance(setting_id, str) or not setting_id:
            continue
        name = fields.get("displayName")
        settings.append(
            Setting(
                id=setting_id,
                kind=kind,
                name=name if isinstance(name, str) and name else setting_id,
                min=_int(fields.get("minVal"), 0),
                max=_int(fields.get("maxVal"), 100),
                step=max(1, _int(fields.get("minStep"), 1)),
            )
        )
    return settings


def parse_choices(value: object) -> list[Choice] | None:
    """The possible values of an ``available<Id>s`` value, in its order."""
    if not isinstance(value, dict):
        return None
    choices = []
    for key, item in value.items():
        fields = item[1] if isinstance(item, list) and len(item) == 2 else item
        if not isinstance(fields, dict):
            continue
        choice_id = fields.get("id", key)
        if not isinstance(choice_id, str):
            continue
        name = fields.get("displayName")
        language = fields.get("language")
        choices.append(
            Choice(
                id=choice_id,
                name=name if isinstance(name, str) and name else choice_id,
                language=language if isinstance(language, str) and language else None,
            )
        )
    return choices


class DriverSettings:
    def __init__(self, channel: Channel, on_changed: Callable[[], None] = lambda: None) -> None:
        self._channel = channel
        self._on_changed = on_changed
        self._pending: set[str] = set()
        self._waiters: list[Callable[[], None]] = []
        self._timeout_id = 0

    # State

    @property
    def ready(self) -> bool:
        return self._channel.ready

    def supported(self) -> list[Setting] | None:
        """The client's settings; None while unknown, empty when not allowed."""
        return parse_supported_settings(self._channel.attributes.get(ATTRIBUTE_SUPPORTED_SETTINGS))

    def setting(self, setting_id: str) -> Setting | None:
        for setting in self.supported() or []:
            if setting.id == setting_id:
                return setting
        return None

    def value(self, setting_id: str) -> Any:
        return self._channel.attributes.get(setting_attribute(setting_id))

    def choices(self, setting_id: str) -> list[Choice] | None:
        attribute = available_values_attribute(setting_id)
        return parse_choices(self._channel.attributes.get(attribute))

    # Changes

    def set(self, setting_id: str, value: object) -> bool:
        """Send a new value; kept as the current one, as RDAccess does."""
        attribute = setting_attribute(setting_id)
        if not self._channel.send(MESSAGE_ATTRIBUTE_VALUE, attribute=attribute, value=value):
            return False
        self._channel.attributes[attribute] = value
        LOG.info("%s: setting %s=%r", self._channel.name, setting_id, value)
        return True

    # Client's values

    def on_ready(self, ready: bool) -> None:
        if not ready:
            self._pending.clear()
            self._resolve()

    def on_attribute_value(self, attribute: object) -> None:
        """Called with each attribute value the client sends."""
        if not isinstance(attribute, str):
            return
        self._pending.discard(attribute)
        if attribute == ATTRIBUTE_SUPPORTED_SETTINGS:
            settings = self.supported()
            LOG.info(
                "%s: client driver settings %s",
                self._channel.name,
                [s.id for s in settings] if settings is not None else "invalid",
            )
            self._request_values(settings or [])
            self._on_changed()
        elif setting_id_from_attribute(attribute) is not None or (
            attribute.startswith("available") and attribute.endswith("s")
        ):
            self._on_changed()
        if not self._pending:
            self._resolve()

    def refresh(self, done: Callable[[], None]) -> None:
        """Ask the client again, then call ``done`` once it answered or after a delay."""
        if not self._channel.ready:
            done()
            return
        self._waiters.append(done)
        settings = self.supported()
        if settings is None:
            self._request(ATTRIBUTE_SUPPORTED_SETTINGS)
        else:
            self._request_values(settings)
        if not self._pending:
            self._resolve()
        elif not self._timeout_id:
            self._timeout_id = GLib.timeout_add(REFRESH_TIMEOUT_MS, self._on_timeout)

    def _request_values(self, settings: list[Setting]) -> None:
        for setting in settings:
            self._request(setting_attribute(setting.id))
            if setting.kind == KIND_STRING:
                self._request(available_values_attribute(setting.id))

    def _request(self, attribute: str) -> None:
        if self._channel.send(MESSAGE_ATTRIBUTE_REQUEST, attribute=attribute):
            self._pending.add(attribute)

    def _on_timeout(self) -> bool:
        self._timeout_id = 0
        if self._pending:
            LOG.info("%s: no answer for %s", self._channel.name, sorted(self._pending))
        self._pending.clear()
        self._resolve()
        return GLib.SOURCE_REMOVE

    def _resolve(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
        waiters, self._waiters = self._waiters, []
        for done in waiters:
            done()
