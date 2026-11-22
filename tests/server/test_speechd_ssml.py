"""SSML from Speech Dispatcher: text and marks."""

from __future__ import annotations

from nvda_rdp_linux.server.speechd_module import parse_ssml


def test_marks_and_text_in_order() -> None:
    assert parse_ssml('<speak>Bonjour <mark name="m1"/>monde</speak>') == [
        ("text", "Bonjour "),
        ("mark", "m1"),
        ("text", "monde"),
    ]


def test_entities_are_unescaped_and_other_tags_dropped() -> None:
    assert parse_ssml("<speak><s>a &amp; b &lt;c&gt;</s></speak>") == [("text", "a & b <c>")]


def test_plain_text_and_empty_input() -> None:
    assert parse_ssml("plain") == [("text", "plain")]
    assert parse_ssml("") == []
    assert parse_ssml("<speak></speak>") == []


def test_mark_attribute_spacing() -> None:
    assert parse_ssml('<mark  name = "x" />') == [("mark", "x")]
