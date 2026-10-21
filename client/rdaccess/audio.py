"""Local playback of NVDA tones and wave files forwarded by RDAccess.

When RDAccess's remote synthesizer is active, the remote NVDA does not play
its beeps and sounds itself: ``tones.decide_beep`` and
``nvwave.decide_playWaveFile`` forward them as ``tone`` and ``wave``
messages and veto local playback.  They must therefore be played here.

Playback runs an external player (``pw-play -`` by default) and feeds it an
in-memory WAV file on stdin, one short-lived process per sound, from a
daemon thread so that the RDAccess receive loop never blocks on audio.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import subprocess
import threading
import wave
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

LOG = logging.getLogger(__name__)

SAMPLE_RATE = 44100
MAX_TONE_MS = 5000
FADE_MS = 5
TONE_AMPLITUDE = 0.5
MAX_WAVE_BYTES = 16 * 1024 * 1024
DEFAULT_PLAYER = ("pw-play", "-")


def make_tone_wav(hz: float, length_ms: int, left: int = 50, right: int = 50) -> bytes:
    """Return a 16-bit stereo WAV sine tone; ``left``/``right`` are 0–100 volumes."""
    frames = int(SAMPLE_RATE * length_ms / 1000)
    fade = max(1, int(SAMPLE_RATE * FADE_MS / 1000))
    left_gain = TONE_AMPLITUDE * max(0, min(left, 100)) / 100
    right_gain = TONE_AMPLITUDE * max(0, min(right, 100)) / 100
    samples = bytearray()
    for frame in range(frames):
        envelope = min(1.0, frame / fade, (frames - frame) / fade)
        value = math.sin(2 * math.pi * hz * frame / SAMPLE_RATE) * envelope * 32767
        samples += struct.pack("<hh", int(value * left_gain), int(value * right_gain))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(bytes(samples))
    return buffer.getvalue()


def resolve_wave_path(waves_dir: Path, file_name: str) -> Path | None:
    """Map a remote NVDA wave path onto a file inside ``waves_dir``.

    RDAccess sends the path relative to NVDA's program directory with Windows
    separators, for example ``waves\\error.wav``.  Only files inside the
    configured directory are accepted: absolute paths, drive letters, ``..``
    components and symbolic links escaping the directory are refused.
    """
    path = PurePosixPath(file_name.replace("\\", "/"))
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or ":" in parts[0]
        or any(part in ("..", ".") for part in parts)
    ):
        return None
    if parts[0].casefold() == "waves":
        parts = parts[1:]
    if not parts:
        return None
    root = waves_dir.resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


class AudioOutput:
    def __init__(
        self,
        *,
        waves_dir: Path | None = None,
        player: Sequence[str] = DEFAULT_PLAYER,
        start: Callable[[Callable[[], None]], None] | None = None,
        run: Callable[..., object] = subprocess.run,
    ) -> None:
        self._waves_dir = waves_dir
        self._player = tuple(player)
        self._start = start or _start_daemon_thread
        self._run = run
        self._player_missing = False
        if waves_dir is not None and not waves_dir.is_dir():
            LOG.warning("NVDA waves directory does not exist: %s", waves_dir)

    def tone(self, hz: float, length: int, left: int = 50, right: int = 50) -> bool:
        if not 0 < hz <= 20000 or not 0 < length <= MAX_TONE_MS:
            LOG.error("refusing tone hz=%r length=%r", hz, length)
            return False
        LOG.info("playing tone hz=%s length=%d left=%d right=%d", hz, length, left, right)
        self._play(lambda: make_tone_wav(hz, length, left, right), f"tone {hz} Hz")
        return True

    def wave(self, file_name: str) -> bool:
        if self._waves_dir is None:
            LOG.info("no NVDA waves directory configured; not playing %r", file_name)
            return False
        path = resolve_wave_path(self._waves_dir, file_name)
        if path is None:
            LOG.warning("refusing or missing NVDA wave file %r", file_name)
            return False
        LOG.info("playing wave %s", path)
        self._play(lambda: _read_wave(path), str(path))
        return True

    def _play(self, data: Callable[[], bytes], description: str) -> None:
        if self._player_missing:
            return

        def play() -> None:
            try:
                self._run(
                    self._player,
                    input=data(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=MAX_TONE_MS / 1000 + 30,
                    check=False,
                )
            except FileNotFoundError:
                self._player_missing = True
                LOG.error("audio player not found: %s", self._player[0])
            except (OSError, subprocess.SubprocessError) as error:
                LOG.error("cannot play %s: %s", description, error)

        self._start(play)


def _read_wave(path: Path) -> bytes:
    if path.stat().st_size > MAX_WAVE_BYTES:
        raise OSError(f"wave file is too large: {path}")
    return path.read_bytes()


def _start_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="rdaccess-audio", daemon=True).start()
