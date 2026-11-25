"""Channel: what a client reported does not outlive its attachment."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

from nvda_rdp_linux.common.rdaccess import (
    CONNECTION_XOFF,
    CONNECTION_XON,
    legacy_protocol_version_frame,
)
from nvda_rdp_linux.server.channel import Channel

DRIVER_TYPE = b"S"
LANGUAGE = b'{"type": "attribute_value", "attribute": "language", "value": "de_DE"}\n'


@pytest.fixture
def channel() -> Iterator[Channel]:
    ours, theirs = socket.socketpair()
    messages: list[dict[str, Any]] = []
    value = Channel("TEST", DRIVER_TYPE, "test.sock", messages.append)
    value._sock = ours
    yield value
    value._sock = None
    ours.close()
    theirs.close()


def feed(channel: Channel, *payloads: bytes) -> None:
    for payload in payloads:
        channel._stream.feed(payload)
        channel._parse()


def attach(channel: Channel) -> None:
    feed(channel, CONNECTION_XON, legacy_protocol_version_frame(DRIVER_TYPE), LANGUAGE)
    assert channel.ready
    assert channel.attributes["language"] == "de_DE"


def test_attributes_are_forgotten_on_a_new_attach(channel: Channel) -> None:
    attach(channel)
    feed(channel, CONNECTION_XON)
    assert channel.attributes == {}


def test_attributes_are_forgotten_on_detach(channel: Channel) -> None:
    attach(channel)
    feed(channel, CONNECTION_XOFF)
    assert not channel.ready
    assert channel.attributes == {}


def test_attributes_are_forgotten_when_the_channel_closes(channel: Channel) -> None:
    attach(channel)
    channel._close(retry=False)
    assert channel.attributes == {}
    assert channel.attached_at is None
