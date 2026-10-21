"""Default language of the Linux side.

RDAccess reports the language of the client's local synthesizer
(``synth.language``); the remote NVDA uses it for automatic language
switching, symbol processing and character descriptions.  Speech
Dispatcher's own default language is not a reliable equivalent: screen
readers such as Orca set their voice per connection, and the global default
often stays ``en-US``.  The user's locale is used instead, following the
POSIX precedence for messages: ``LC_ALL``, then ``LC_MESSAGES``, then
``LANG``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_LANGUAGE = "en"
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def locale_to_language(value: str) -> str | None:
    """Turn ``fr_FR.UTF-8@euro`` into NVDA's ``fr_FR``; None for C/POSIX."""
    language = value.split(".", 1)[0].split("@", 1)[0]
    if language in ("", "C", "POSIX"):
        return None
    return language


def system_language(environ: Mapping[str, str] = os.environ) -> str:
    for variable in _LOCALE_VARIABLES:
        value = environ.get(variable)
        if value:
            return locale_to_language(value) or DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE
