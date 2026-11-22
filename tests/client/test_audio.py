from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from nvda_rdp_linux.client.audio import AudioOutput, make_tone_wav, resolve_wave_path


def test_tone_wav_has_expected_length_and_channel_volumes() -> None:
    data = make_tone_wav(440, 100, left=100, right=0)
    with wave.open(io.BytesIO(data)) as reader:
        assert reader.getnchannels() == 2
        assert reader.getnframes() == 4410
        frames = reader.readframes(reader.getnframes())
    right_samples = frames[2::4] + frames[3::4]
    assert set(right_samples) == {0}
    assert any(frames[0::4])


@pytest.fixture
def waves(tmp_path: Path) -> Path:
    directory = tmp_path / "waves"
    directory.mkdir()
    (directory / "error.wav").write_bytes(b"RIFF")
    (tmp_path / "secret.wav").write_bytes(b"RIFF")
    (directory / "link.wav").symlink_to(tmp_path / "secret.wav")
    return directory


def test_remote_wave_paths_resolve_inside_waves_dir(waves: Path) -> None:
    assert resolve_wave_path(waves, "waves\\error.wav") == waves / "error.wav"
    assert resolve_wave_path(waves, "error.wav") == waves / "error.wav"


@pytest.mark.parametrize(
    "name",
    [
        "..\\secret.wav",
        "waves\\..\\..\\secret.wav",
        "C:\\Windows\\Media\\ding.wav",
        "/etc/passwd",
        "waves\\link.wav",
        "waves\\missing.wav",
        "waves",
        "",
    ],
)
def test_unsafe_or_missing_wave_paths_are_refused(waves: Path, name: str) -> None:
    assert resolve_wave_path(waves, name) is None


def test_audio_output_feeds_player_on_stdin(waves: Path) -> None:
    runs: list[tuple[tuple[str, ...], bytes]] = []

    def run(command, *, input, **_kwargs):
        runs.append((command, input))

    audio = AudioOutput(waves_dir=waves, start=lambda target: target(), run=run)
    assert audio.wave("waves\\error.wav")
    assert audio.tone(880, 20)
    assert not audio.tone(880, 60000)
    assert not audio.wave("..\\secret.wav")
    assert [command for command, _data in runs] == [("pw-play", "-"), ("pw-play", "-")]
    assert runs[0][1] == b"RIFF"
    assert runs[1][1].startswith(b"RIFF")


def test_missing_player_is_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    def run(*_args, **_kwargs):
        raise FileNotFoundError

    audio = AudioOutput(start=lambda target: target(), run=run)
    audio.tone(440, 10)
    audio.tone(440, 10)
    assert caplog.text.count("audio player not found") == 1


def test_wave_without_directory_is_skipped() -> None:
    audio = AudioOutput(start=lambda target: target(), run=lambda *a, **k: None)
    assert not audio.wave("waves\\error.wav")
