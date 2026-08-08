"""Persistent Speech Dispatcher output."""

from __future__ import annotations

import logging
from collections.abc import Callable

LOG = logging.getLogger(__name__)


def orca_rate_to_ssip(rate: int) -> int:
    if not 0 <= rate <= 100:
        raise ValueError("Orca-compatible rate must be between 0 and 100")
    return 2 * rate - 100


class SpeechDispatcherOutput:
    def __init__(
        self,
        *,
        language: str = "fr",
        orca_rate: int = 75,
        output_module: str = "espeak-ng",
    ) -> None:
        from speechd.client import SSIPClient

        self._client = SSIPClient(
            "nvda-rdp-linux",
            component="rdaccess",
            autospawn=True,
        )
        self._client.set_output_module(output_module)
        self._client.set_language(language)
        ssip_rate = orca_rate_to_ssip(orca_rate)
        self._client.set_rate(ssip_rate)
        LOG.info(
            "connected to Speech Dispatcher module=%s language=%s Orca-rate=%d SSIP-rate=%d",
            output_module,
            language,
            orca_rate,
            ssip_rate,
        )

    def speak(
        self,
        text: str,
        *,
        character_mode: bool = False,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        from speechd.client import CallbackType

        def callback(event_type: str, **_kwargs: object) -> None:
            if event_type == CallbackType.END and on_done is not None:
                on_done()

        self._client.set_spelling(character_mode)
        try:
            self._client.speak(
                text,
                callback=callback if on_done is not None else None,
                event_types=(CallbackType.END,) if on_done is not None else None,
            )
        finally:
            if character_mode:
                self._client.set_spelling(False)
        LOG.info(
            "Speech Dispatcher accepted speak character_mode=%s text=%r",
            character_mode,
            text,
        )

    def cancel(self) -> None:
        self._client.cancel()
        LOG.info("Speech Dispatcher accepted cancel")

    def close(self) -> None:
        self._client.close()
        LOG.info("closed Speech Dispatcher connection")
