"""SSML from Speech Dispatcher: text, marks, languages and breaks."""

from __future__ import annotations

import pytest

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


def test_inner_languages_and_back() -> None:
    ssml = (
        '<speak>Il a dit <s xml:lang="en-US">hello <lang xml:lang="de">Hallo</lang> again</s>'
        " puis</speak>"
    )
    assert parse_ssml(ssml) == [
        ("text", "Il a dit "),
        ("lang", "en-US"),
        ("text", "hello "),
        ("lang", "de"),
        ("text", "Hallo"),
        ("lang", "en-US"),
        ("text", " again"),
        ("lang", None),
        ("text", " puis"),
    ]


def test_language_of_speak_is_the_message_setting() -> None:
    assert parse_ssml('<speak xml:lang="fr-FR">bonjour</speak>') == [("text", "bonjour")]


def test_unclosed_and_stray_tags() -> None:
    assert parse_ssml("<p xml:lang='it'>ciao</s>") == [("lang", "it"), ("text", "ciao")]
    assert parse_ssml('<voice name="x">a</voice>') == [("text", "a")]


@pytest.mark.parametrize(
    ("tag", "items"),
    [
        ('<break time="500ms"/>', [("break", 500)]),
        ('<break time="1.5s"/>', [("break", 1500)]),
        ('<break time="60s"/>', [("break", 10000)]),
        ('<break strength="strong"/>', []),
        ('<break time="soon"/>', []),
    ],
)
def test_breaks(tag: str, items: list[tuple[str, object]]) -> None:
    assert parse_ssml(f"<speak>a{tag}b</speak>") == [("text", "a"), *items, ("text", "b")]
