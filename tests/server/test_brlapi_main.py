from __future__ import annotations

import locale

import pytest

from nvda_rdp_linux.server.brlapi_main import use_environment_locale


@pytest.fixture
def restored_locale():
    saved = locale.setlocale(locale.LC_ALL)
    yield
    locale.setlocale(locale.LC_ALL, saved)


@pytest.mark.usefixtures("restored_locale")
def test_missing_locale_leaves_c_instead_of_stopping(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    locale.setlocale(locale.LC_ALL, "C")
    monkeypatch.setenv("LC_ALL", "xx_YY.UTF-8")

    use_environment_locale()

    assert locale.setlocale(locale.LC_ALL) == "C"
    assert "Locale of the environment unavailable" in caplog.text


@pytest.mark.usefixtures("restored_locale")
def test_installed_locale_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "C.UTF-8")

    use_environment_locale()

    assert locale.setlocale(locale.LC_ALL) == "C.UTF-8"
