import pytest

from nvda_rdp_linux.rdaccess.language import system_language


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "fr_FR.UTF-8"}, "fr_FR"),
        ({"LC_ALL": "de_DE.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8", "LANG": "it_IT"}, "de_DE"),
        ({"LC_ALL": "", "LC_MESSAGES": "pt_BR.UTF-8", "LANG": "fr_FR"}, "pt_BR"),
        ({"LANG": "ca_ES.UTF-8@valencia"}, "ca_ES"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LC_ALL": "POSIX", "LANG": "fr_FR"}, "en"),
        ({}, "en"),
    ],
)
def test_system_language_follows_posix_precedence(environ, expected) -> None:
    assert system_language(environ) == expected
