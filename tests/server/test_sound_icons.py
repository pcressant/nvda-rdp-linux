"""Sound icons of Speech Dispatcher as NVDA's sounds and tones."""

from __future__ import annotations

import pytest

from nvda_rdp_linux.server.speechd_module import CAPITAL_TONE, ICON_TONE, sound_icon


@pytest.mark.parametrize(
    ("name", "file_name"),
    [
        ("error", "waves\\error.wav"),
        ("BrowseMode", "waves\\browseMode.wav"),
        (" start.wav", "waves\\start.wav"),
    ],
)
def test_nvda_sounds_by_name(name: str, file_name: str) -> None:
    assert sound_icon(name) == ("wave", {"fileName": file_name})


def test_capital_and_other_icons_are_tones() -> None:
    assert sound_icon("capital") == ("tone", CAPITAL_TONE)
    assert sound_icon("bell") == ("tone", ICON_TONE)
    assert sound_icon("../../evil") == ("tone", ICON_TONE)
