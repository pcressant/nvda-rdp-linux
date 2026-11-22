"""The GNOME Remote Desktop preload, against a fake WinPR.

The preload and the fake are compiled with the C compiler and the WinPR 3
headers, found with pkg-config (both are in the development shell).  Each
scenario runs ``driver.py`` in a new process with LD_PRELOAD set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PRELOAD_SOURCE = REPOSITORY / "grd-preload" / "nvda_rdp_grd_preload.c"
SPEECH_VERSION = "53 40 12 00 60 70 72 6f 74 6f 63 6f 6c 56 65 72 73 69 6f 6e 60 02"


def compile_library(source: Path, output: Path, cflags: list[str]) -> None:
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-g",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            *cflags,
            "-o",
            str(output),
            str(source),
            "-ldl",
            "-pthread",
        ],
        check=True,
    )


@pytest.fixture(scope="module")
def libraries(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    if shutil.which("cc") is None or shutil.which("pkg-config") is None:
        pytest.skip("no C compiler or pkg-config")
    flags = subprocess.run(
        ["pkg-config", "--cflags", "winpr3"], capture_output=True, text=True, check=False
    )
    if flags.returncode != 0:
        pytest.skip("no WinPR 3 headers")
    directory = tmp_path_factory.mktemp("preload")
    preload = directory / "libnvda-rdp-grd-preload.so"
    fake = directory / "libfake-winpr.so"
    compile_library(PRELOAD_SOURCE, preload, flags.stdout.split())
    compile_library(HERE / "fake_winpr.c", fake, flags.stdout.split())
    return preload, fake


def run(libraries: tuple[Path, Path], scenario: str) -> dict:
    preload, fake = libraries
    # A short runtime directory: Unix socket paths are limited to 108 bytes.
    with tempfile.TemporaryDirectory(prefix="nvda-rdp-") as runtime:
        environment = {
            **os.environ,
            "LD_PRELOAD": str(preload),
            "XDG_RUNTIME_DIR": runtime,
            "PYTHONPATH": os.pathsep.join([str(REPOSITORY), os.environ.get("PYTHONPATH", "")]),
        }
        process = subprocess.run(
            [sys.executable, str(HERE / "driver.py"), str(fake), scenario],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_relay_frames_messages_both_ways(libraries) -> None:
    result = run(libraries, "relay")
    assert result["sockets"] == {"nvda-speech.sock": "0o600", "nvda-braille.sock": "0o600"}
    # The empty frame is not written: a DVC message is never empty.
    assert result["to_client"] == ["11", b'{"type":"index","index":1}\n'.hex(" ")]
    # The empty client message is skipped; the large one arrives whole.
    assert result["from_client"] == [SPEECH_VERSION, 256 * 400]
    assert result["braille_opens"] == 0
    assert result["closed_by_preload"] is True
    assert result["sockets_after_close"] == {}
    assert (result["opens"], result["closes"]) == (1, 1)
    assert result["server"] == [1, 1]
    assert result["log"] == [
        "INFO NVDA-SPEECH: listening on " + "{runtime}/gnome-remote-desktop/nvda-speech.sock",
        "INFO NVDA-BRAILLE: listening on " + "{runtime}/gnome-remote-desktop/nvda-braille.sock",
        "INFO NVDA-SPEECH: channel open",
        "INFO NVDA-SPEECH: channel closed",
    ]


def test_messages_larger_than_any_buffer(libraries) -> None:
    assert run(libraries, "large_from_client") == {"received": True, "written": True}


def test_oversized_frame_ends_the_relay(libraries) -> None:
    result = run(libraries, "oversized_frame")
    assert result["closed_by_preload"] is True
    assert result["log"] == ["ERROR NVDA-BRAILLE: rejecting an oversized frame of 16777217 bytes"]


def test_partial_frame_times_out(libraries) -> None:
    result = run(libraries, "partial_frame")
    # FRAME_TIMEOUT_S is 5 s; the upper bound leaves room for a loaded machine
    # while staying under the driver's own 10 s socket timeout.
    assert 4.9 <= result.pop("after_seconds") < 8
    assert result == {"closed_by_preload": True, "written": 0}


def test_client_closing_the_channel_ends_the_relay(libraries) -> None:
    result = run(libraries, "client_closes_channel")
    assert result == {
        "closed_by_preload": True,
        "opens": 2,
        "closes": 1,
        "closed_at_shutdown": True,
    }


def test_a_second_connection_is_left_alone(libraries) -> None:
    result = run(libraries, "second_server")
    assert sorted(result["sockets_after_second_close"]) == ["nvda-braille.sock", "nvda-speech.sock"]
    assert result["sockets_after_first_close"] == {}
    assert result["server"] == [2, 2]
    assert result["warnings"] == ["WARN a channel bridge already exists, not adding another one"]


def test_shutdown_while_the_channel_is_not_open_yet(libraries) -> None:
    result = run(libraries, "shutdown_while_opening")
    assert result["close_seconds"] == 0
    assert result["closed_by_preload"] is True
    assert result["opens"] == 0


def test_nvda_rdp_server_channel_through_the_preload(libraries) -> None:
    result = run(libraries, "server_channel")
    assert result["to_client"] == [
        "B@\x12\x00`protocolVersion`\x02",
        '{"version": 2, "channel": "NVDA-BRAILLE", "type": "protocol_version"}\n',
        '{"cells": [1, 2, 3], "type": "display"}\n',
    ]
    assert result["received"] == [{"type": "attribute_value", "attribute": "numCells", "value": 40}]
