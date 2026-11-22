"""packaging/server/nvda-rdp-server-session.in, run with a fake loginctl."""

from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "server" / "nvda-rdp-server-session.in"

FAKE_LOGINCTL = """#!/bin/sh
# show-user UID -p Display --value | show-session SESSION -p NAME --value
case "$1 $4" in
  "show-user Display") echo "$FAKE_SESSION" ;;
  "show-session Remote") [ "$2" = "$FAKE_SESSION" ] && echo "$FAKE_REMOTE" ;;
  "show-session Class") [ "$2" = "$FAKE_SESSION" ] && echo "$FAKE_CLASS" ;;
esac
"""


@pytest.fixture
def run(tmp_path: Path):
    tools = tmp_path / "bin"
    tools.mkdir()
    loginctl = tools / "loginctl"
    loginctl.write_text(FAKE_LOGINCTL)
    loginctl.chmod(0o755)
    path = os.pathsep.join([str(tools), str(Path(shutil.which("sleep")).parent)])
    script = tmp_path / "nvda-rdp-server-session"
    script.write_text(SCRIPT.read_text().replace("@PATH@", path))
    script.chmod(0o755)
    runtime = tmp_path / "run"
    runtime.mkdir()

    def run(*args: str, session="c2", remote="yes", klass="user", **environment: str):
        env = {
            "XDG_RUNTIME_DIR": str(runtime),
            "FAKE_SESSION": session,
            "FAKE_REMOTE": remote,
            "FAKE_CLASS": klass,
            **environment,
        }
        process = subprocess.run(
            [str(script), *args], env=env, capture_output=True, text=True, timeout=30, check=False
        )
        return process, runtime

    return run


def state(runtime: Path) -> list[str]:
    directory = runtime / "nvda-rdp-server"
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def test_remote_user_session(run) -> None:
    process, runtime = run()
    assert process.returncode == 0
    assert "remote session" in process.stdout
    assert state(runtime) == ["orca.env", "remote"]
    uid = os.getuid()
    assert (runtime / "nvda-rdp-server" / "orca.env").read_text().splitlines() == [
        f"BRLAPI_HOST=127.0.0.1:{uid}",
        f"BRLAPI_AUTH=keyfile:{runtime}/nvda-rdp-brlapi.key",
        f"SPEECHD_ADDRESS=unix_socket:{runtime}/nvda-rdp-speechd/speechd.sock",
    ]


def test_local_session_clears_a_previous_remote_state(run) -> None:
    _, runtime = run()
    key = runtime / "nvda-rdp-brlapi.key"
    key.write_text("old")
    process, runtime = run(remote="no")
    assert process.returncode == 0
    assert "local session" in process.stdout
    assert state(runtime) == []
    assert not key.exists()


def test_remote_login_screen_is_served_by_default(run) -> None:
    process, runtime = run(klass="greeter")
    assert "remote session" in process.stdout
    assert state(runtime) == ["orca.env", "remote"]


def test_remote_login_screen_can_be_left_alone(run) -> None:
    process, runtime = run(klass="greeter", NVDA_RDP_LOGIN_SCREEN="no")
    assert process.returncode == 0
    assert "login screen not served" in process.stdout
    assert state(runtime) == []


def test_other_session_classes_are_left_alone(run) -> None:
    process, runtime = run(klass="background")
    assert "not a user session" in process.stdout
    assert state(runtime) == []


def test_explicit_session(run) -> None:
    process, runtime = run("check", "c7", session="c7")
    assert "graphical session 'c7', Remote=yes, Class=user" in process.stdout
    assert state(runtime) == ["orca.env", "remote"]


def test_unknown_session(run) -> None:
    process, runtime = run("check", "c9")
    assert process.returncode == 0
    assert "Remote=, Class=" in process.stdout
    assert state(runtime) == []


def test_wait_for_a_file(run, tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("k")
    assert run("wait-file", str(key))[0].returncode == 0
    key.write_text("")
    process, _ = run("wait-file", str(key))
    assert process.returncode == 1
    assert "timed out" in process.stderr


def test_wait_for_a_socket(run, tmp_path: Path) -> None:
    path = tmp_path / "s"
    assert run("wait-socket", str(path))[0].returncode == 1
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        assert stat.S_ISSOCK(path.stat().st_mode)
        assert run("wait-socket", str(path))[0].returncode == 0


@pytest.mark.parametrize("args", [("bogus",), ("check", "a", "b"), ("wait-file",)])
def test_usage(run, args) -> None:
    process, _ = run(*args)
    assert process.returncode == 2
    assert "usage" in process.stderr
