"""Black-box golden harness for the server side (Orca to NVDA over RDP).

Two server processes are exercised, each in its own scenario:

* the BrlAPI server, which offers the RDP client's braille display to Orca;
* the Speech Dispatcher output module, which speaks through NVDA.

The harness stands in for everything around them:

* the GNOME Remote Desktop preload: it listens on the channel sockets and
  plays the RDAccess client (NVDA on the RDP client machine);
* Orca: as a BrlAPI client using the real BrlAPI library, and as the owner of
  ``org.gnome.Orca1.Service`` (``fakes/dbus``);
* Mutter's RemoteDesktop service (``fakes/dbus``), which receives typed keys;
* Speech Dispatcher: it writes the module protocol on the module's stdin;
* the local Speech Dispatcher used without NVDA (``fakes/speechd``).

Everything runs on an isolated session bus and in a private runtime
directory.  The transcripts list RDAccess messages, not transport frames,
and JSON messages as normalized JSON: the draft relays a raw byte stream and
writes compact JSON, the integrated server frames each message and writes
JSON as the client does, and both must produce the same messages.

``GOLDEN_SERVER_IMPL`` selects the implementation: ``integrated`` (default),
the server of this repository, or ``draft``, the study it was ported from
(``GOLDEN_DRAFT_DIR``), to compare the two.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from harness import FAKES_DIR, FIXTURES, GOLDEN_DIR, REPOSITORY, HarnessError, describe_payload

STARTUP_TIMEOUT = 10.0
STEP_TIMEOUT = 5.0
SETTLE_SECONDS = 0.3
# The locale of the transcripts, unless a scenario sets LANG.
DEFAULT_LANG = "fr_FR.UTF-8"
DRAFT_DIR = Path(os.environ.get("GOLDEN_DRAFT_DIR", REPOSITORY.parent / "draft-nvda-orca-rdp"))
DRAFT_EXPERIMENTS = DRAFT_DIR / "experiments"
HEADER = struct.Struct("!I")
XON, XOFF = 0x11, 0x13
DRIVER_TYPES = {"speech": ord("S"), "braille": ord("B")}

# How each implementation is started and reached.
IMPLEMENTATIONS: dict[str, dict[str, Any]] = {
    "draft": {
        "pythonpath": [
            DRAFT_EXPERIMENTS / "03-orca-rdaccess-speech",
            DRAFT_EXPERIMENTS / "06-brlapi-server",
            DRAFT_EXPERIMENTS / "07-speechd-module",
        ],
        "brlapi": ["-m", "rdaccess_brlapi"],
        "speechd": ["-m", "rdaccess_speechd"],
        "framing": "stream",
        "socket_dir": "gnome-remote-desktop",
        "sockets": {"speech": "nvda-speech.sock", "braille": "nvda-braille.sock"},
        # As the draft's NixOS module sets them (rdaccess.nix, stock-grd.nix).
        "env": {"RDACCESS_KEY_INJECTION": "mutter", "RDACCESS_LOUIS_TABLES": "{louis_tables}"},
        # The draft was verified with liblouis 3.33.  The Python binding of
        # liblouis 3.38 computes the length of charToDots and dotsToChar from
        # the number of characters instead of the encoded size, which
        # truncates the result to a quarter; the reference transcripts are
        # recorded with the 3.33 behaviour (see _fixed_louis_binding).
        "louis_binding_fix": True,
        # The repository's names of the draft's environment variables.
        "env_names": {"NVDA_RDP_PROSODY": "RDACCESS_PROSODY"},
    },
    # The server of this repository, ported from the draft onto common/.
    "integrated": {
        "pythonpath": [REPOSITORY],
        "brlapi": ["-m", "nvda_rdp_linux.server.brlapi_main"],
        "speechd": ["-m", "nvda_rdp_linux.server.speechd_main"],
        "framing": "frames",
        "socket_dir": "gnome-remote-desktop",
        "sockets": {"speech": "nvda-speech.sock", "braille": "nvda-braille.sock"},
        "env": {},
        "env_names": {},
    },
}

# The faulty statement of liblouis 3.38's binding, and its 3.33 meaning.
LOUIS_FAULTY_LENGTH = "length = c_int(len(inbuf) // wideCharBytes)"
LOUIS_FIXED_LENGTH = "length = c_int(len(_inbuf) // wideCharBytes)"


def louis_tables() -> str:
    """The liblouis tables directory next to the louis Python package."""
    import louis

    prefix = Path(louis.__file__).resolve().parents[4]
    return str(prefix / "share" / "liblouis" / "tables")


def _fixed_louis_binding(target: Path) -> None:
    """Copy the louis package into ``target`` with the 3.38 length fixed."""
    import shutil

    import louis

    package = Path(louis.__file__).resolve().parent
    # copyfile: the copy must be writable, unlike the read-only original.
    shutil.copytree(
        package,
        target / "louis",
        ignore=shutil.ignore_patterns("__pycache__"),
        copy_function=shutil.copyfile,
    )
    source = target / "louis" / "__init__.py"
    text = source.read_text()
    if LOUIS_FAULTY_LENGTH in text:
        source.write_text(text.replace(LOUIS_FAULTY_LENGTH, LOUIS_FIXED_LENGTH))


def implementation() -> dict[str, Any]:
    name = os.environ.get("GOLDEN_SERVER_IMPL", "integrated")
    try:
        return IMPLEMENTATIONS[name]
    except KeyError:
        raise HarnessError(f"unknown server implementation {name!r}") from None


def describe_message(payload: bytes) -> dict[str, Any]:
    """A JSON message as its normalized JSON, anything else as bytes.

    The JSON text itself (key order, spacing, escapes) is not compared: the
    draft and the ported server write the same messages differently.
    """
    if payload.startswith(b"{") and payload.endswith(b"\n"):
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            return {"json": json.dumps(message, sort_keys=True, ensure_ascii=False)}
    return describe_payload(payload)


class ChannelPeer:
    """The RDAccess client end of one channel, as the preload hands it over."""

    def __init__(self, listener: socket.socket, channel: str, framing: str) -> None:
        self.listener = listener
        self.channel = channel
        self.framing = framing
        self.sock: socket.socket | None = None
        self.buffer = bytearray()
        self.messages: list[dict[str, str]] = []
        self.closed = False

    def accept(self, timeout: float) -> bool:
        readable, _, _ = select.select([self.listener], [], [], timeout)
        if not readable:
            return False
        self.sock, _ = self.listener.accept()
        self.buffer.clear()
        self.closed = False
        return True

    def send(self, payload: bytes) -> None:
        if self.sock is None:
            raise HarnessError(f"{self.channel} channel is not connected")
        if self.framing == "frames":
            self.sock.sendall(HEADER.pack(len(payload)) + payload)
        else:
            self.sock.sendall(payload)

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def pump(self, timeout: float) -> bool:
        if self.sock is None or self.closed:
            return False
        readable, _, _ = select.select([self.sock], [], [], timeout)
        if not readable:
            return False
        data = self.sock.recv(65536)
        if not data:
            self.closed = True
            self.messages.append({"event": "closed by the server"})
            return True
        if self.framing == "frames":
            self.buffer.extend(data)
            while len(self.buffer) >= HEADER.size:
                (size,) = HEADER.unpack_from(self.buffer)
                if len(self.buffer) < HEADER.size + size:
                    break
                payload = bytes(self.buffer[HEADER.size : HEADER.size + size])
                del self.buffer[: HEADER.size + size]
                self.messages.extend(self._split(bytearray(payload), final=True))
        else:
            self.buffer.extend(data)
            self.messages.extend(self._split(self.buffer, final=False))
        return True

    def _split(self, buffer: bytearray, *, final: bool) -> list[dict[str, str]]:
        """Cut a byte stream into RDAccess messages; consume what was cut."""
        messages = []
        while buffer:
            first = buffer[0]
            if first in (XON, XOFF):
                end = 1
            elif first == ord("{"):
                end = buffer.find(b"\n") + 1
                if end == 0:
                    break
            elif first == DRIVER_TYPES[self.channel]:
                if len(buffer) < 4:
                    break
                end = 4 + int.from_bytes(buffer[2:4], "little")
                if len(buffer) < end:
                    break
            else:
                end = len(buffer)
            messages.append(describe_message(bytes(buffer[:end])))
            del buffer[:end]
        if final and buffer:
            messages.append({"incomplete": bytes(buffer).hex(" ")})
            buffer.clear()
        return messages

    def take(self) -> list[dict[str, str]]:
        messages, self.messages = self.messages, []
        return messages


class OrcaBrlapiClient:
    """Orca's side of BrlAPI: ``fakes/orca/brlapi_client.py`` in its own process."""

    def __init__(self, environment: dict[str, str], log_path: Path) -> None:
        self._environment = environment
        self._log_path = log_path
        self._process: subprocess.Popen[str] | None = None
        self.results: list[dict[str, Any]] = []

    def run(self, step: dict[str, Any]) -> None:
        if self._process is None or self._process.poll() is not None:
            self._process = subprocess.Popen(
                [sys.executable, str(FAKES_DIR / "orca" / "brlapi_client.py")],
                env=self._environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=open(self._log_path, "a"),  # noqa: SIM115 - closed with the process.
                text=True,
            )
        self._process.stdin.write(json.dumps(step) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if line:
            self.results.append(json.loads(line))
        else:
            self.results.append({"action": step["orca"], "crashed": self._process.wait()})

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.stdin.close()
            try:
                self._process.wait(timeout=STEP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def take(self) -> list[dict[str, Any]]:
        results, self.results = self.results, []
        return results


class ServerRun:
    def __init__(self, scenario: dict[str, Any], workdir: Path) -> None:
        self.scenario = scenario
        self.workdir = workdir
        self.impl = implementation()
        self.runtime = workdir / "run"
        self.control = workdir / "control"
        self.trace_path = workdir / "trace.jsonl"
        self.fakes = workdir / "fakes"
        self.key_path = self.runtime / "golden-brlapi.key"
        self.display = 20000 + os.getpid() % 20000
        self.trace_offset = 0
        self.processes: list[subprocess.Popen[bytes]] = []
        self.peers: dict[str, ChannelPeer] = {}
        self.server: subprocess.Popen[bytes] | None = None
        self.module_buffer = bytearray()
        self.module_lines: list[str] = []
        self.orca: OrcaBrlapiClient | None = None
        self.bus_address = ""

    # Environment

    def start(self) -> None:
        for directory in (self.runtime, self.control, self.fakes):
            directory.mkdir(mode=0o700, parents=True)
        self.trace_path.touch()
        # Only the fakes the server side may meet: not the client's brlapi.
        (self.fakes / "golden_trace.py").symlink_to(FAKES_DIR / "golden_trace.py")
        (self.fakes / "speechd").symlink_to(FAKES_DIR / "speechd")
        if self.impl.get("louis_binding_fix"):
            _fixed_louis_binding(self.fakes)
        self._start_bus()
        self._start_fake_services()
        self._listen_channels()
        self._start_server()
        environment = self._base_environment()
        environment["BRLAPI_HOST"] = f"127.0.0.1:{self.display}"
        environment["BRLAPI_AUTH"] = f"keyfile:{self.key_path}"
        self.orca = OrcaBrlapiClient(environment, self.workdir / "orca.log")

    def _base_environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("NVDA_", "LC_", "DISPLAY", "WAYLAND_", "GOLDEN_", "BRLAPI_"))
            and key not in ("LANG", "LANGUAGE", "DBUS_SESSION_BUS_ADDRESS", "SPEECHD_ADDRESS")
        }
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "XDG_RUNTIME_DIR": str(self.runtime),
                "LANG": DEFAULT_LANG,
                "GOLDEN_TRACE": str(self.trace_path),
                "GOLDEN_CONTROL_DIR": str(self.control),
                "GOLDEN_FAKE_OPTIONS": json.dumps(self.scenario.get("fake", {})),
            }
        )
        if self.bus_address:
            environment["DBUS_SESSION_BUS_ADDRESS"] = self.bus_address
        return environment

    def _start_bus(self) -> None:
        bus = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--nopidfile", "--print-address=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._base_environment(),
        )
        self.processes.append(bus)
        self.bus_address = bus.stdout.readline().decode().strip()
        if not self.bus_address:
            raise HarnessError("dbus-daemon did not start")

    def _start_fake_services(self) -> None:
        environment = self._base_environment()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(self.fakes), os.environ.get("PYTHONPATH", "")]
        )
        self.processes.append(
            subprocess.Popen(
                [sys.executable, str(FAKES_DIR / "dbus" / "fake_session_services.py")],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=open(self.workdir / "dbus-services.log", "wb"),  # noqa: SIM115
            )
        )
        if not self.wait_trace({"src": "dbus", "call": "ready"}):
            raise HarnessError("fake D-Bus services did not start")

    def _listen_channels(self) -> None:
        directory = self.runtime / self.impl["socket_dir"]
        directory.mkdir(mode=0o700)
        for channel, name in self.impl["sockets"].items():
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(directory / name))
            listener.listen(1)
            self.peers[channel] = ChannelPeer(listener, channel, self.impl["framing"])

    def _start_server(self) -> None:
        component = self.scenario["component"]
        environment = self._base_environment()
        environment.update(
            {
                name: value.replace("{louis_tables}", louis_tables())
                for name, value in self.impl["env"].items()
            }
        )
        names = self.impl["env_names"]
        environment.update(
            {names.get(name, name): value for name, value in self.scenario.get("env", {}).items()}
        )
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(self.fakes), *map(str, self.impl["pythonpath"]), os.environ.get("PYTHONPATH", "")]
        )
        args = [sys.executable, *self.impl[component]]
        if component == "brlapi":
            args += ["--display", str(self.display), "--key", str(self.key_path)]
        self.server = subprocess.Popen(
            args,
            env=environment,
            stdin=subprocess.PIPE if component == "speechd" else subprocess.DEVNULL,
            stdout=subprocess.PIPE if component == "speechd" else subprocess.DEVNULL,
            stderr=open(self.workdir / "server.log", "wb"),  # noqa: SIM115
        )
        if component == "brlapi":
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while not (self.key_path.exists() and self.key_path.stat().st_size):
                if self.server.poll() is not None or time.monotonic() > deadline:
                    raise HarnessError("BrlAPI server did not start")
                time.sleep(0.02)

    def stop(self) -> int | None:
        if self.orca is not None:
            self.orca.close()
        for peer in self.peers.values():
            peer.close()
        returncode = None
        if self.server is not None:
            if self.server.stdin is not None and not self.server.stdin.closed:
                # Speech Dispatcher closing the pipe: the module exits by itself.
                self.server.stdin.close()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.server.wait(timeout=STEP_TIMEOUT)
            if self.server.poll() is None:
                self.server.send_signal(signal.SIGTERM)
            try:
                returncode = self.server.wait(timeout=STEP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait()
        time.sleep(SETTLE_SECONDS)
        for process in reversed(self.processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=STEP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
        for peer in self.peers.values():
            peer.listener.close()
        return returncode

    # Waiting

    def _pump_module(self, timeout: float) -> bool:
        if self.server is None or self.server.stdout is None:
            return False
        readable, _, _ = select.select([self.server.stdout], [], [], timeout)
        if not readable:
            return False
        data = os.read(self.server.stdout.fileno(), 65536)
        if not data:
            return False
        self.module_buffer.extend(data)
        while b"\n" in self.module_buffer:
            line, _, rest = bytes(self.module_buffer).partition(b"\n")
            self.module_buffer = bytearray(rest)
            self.module_lines.append(line.decode("utf-8", "replace"))
        return True

    def settle(self) -> None:
        deadline = time.monotonic() + STEP_TIMEOUT
        quiet_since = time.monotonic()
        size = self.trace_path.stat().st_size
        while time.monotonic() < deadline:
            activity = any(peer.pump(0.01) for peer in self.peers.values())
            activity = self._pump_module(0.01) or activity
            new_size = self.trace_path.stat().st_size
            if activity or new_size != size:
                size = new_size
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= SETTLE_SECONDS:
                return

    def wait_trace(self, wanted: dict[str, Any], count: int = 1) -> bool:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            with open(self.trace_path, encoding="utf-8") as trace:
                events = [json.loads(line) for line in trace.read().splitlines() if line]
            matches = [e for e in events if all(e.get(k) == v for k, v in wanted.items())]
            if len(matches) >= count:
                return True
            time.sleep(0.02)
        return False

    # Results

    def new_trace(self) -> list[dict[str, Any]]:
        with open(self.trace_path, encoding="utf-8") as trace:
            trace.seek(self.trace_offset)
            data = trace.read()
        complete = data[: data.rfind("\n") + 1]
        self.trace_offset += len(complete.encode("utf-8"))
        text = complete.replace(str(self.workdir), "{workdir}")
        return [json.loads(line) for line in text.splitlines() if line]

    def received(self) -> dict[str, list[Any]]:
        received: dict[str, list[Any]] = {}
        for channel, peer in self.peers.items():
            messages = peer.take()
            if messages:
                received[channel] = messages
        if self.module_lines:
            received["module"] = [{"line": line} for line in self.module_lines]
            self.module_lines = []
        results = self.orca.take() if self.orca is not None else []
        if results:
            received["orca"] = results
        return received


def _payloads(step: dict[str, Any]) -> list[bytes]:
    if "text" in step:
        return [step["text"].encode("utf-8")]
    if "hex" in step:
        return [bytes.fromhex(step["hex"])]
    if "fixture" in step:
        return [bytes.fromhex((FIXTURES / step["fixture"]).read_text())]
    if "json" in step:
        return [json.dumps(message).encode() + b"\n" for message in step["json"]]
    raise HarnessError(f"send step without data: {step}")


def _run_step(run: ServerRun, step: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "accept" in step:
        if not run.peers[step["accept"]].accept(step.get("timeout", STEP_TIMEOUT)):
            result["accept_timeout"] = True
    elif "send" in step:
        for payload in _payloads(step):
            run.peers[step["send"]].send(payload)
    elif "disconnect" in step:
        run.peers[step["disconnect"]].close()
    elif "orca" in step:
        run.orca.run(step)
    elif "module" in step:
        run.server.stdin.write("".join(line + "\n" for line in step["module"]).encode())
        run.server.stdin.flush()
    elif "dbus" in step:
        with open(run.control / "dbus-properties", "a", encoding="utf-8") as control:
            control.write(json.dumps(step["dbus"]) + "\n")
        if not run.wait_trace({"src": "dbus", "call": "changed"}, step.get("count", 1)):
            result["wait_timeout"] = True
    elif "sleep" in step:
        time.sleep(step["sleep"])
    elif "settle" not in step:
        raise HarnessError(f"unknown step: {step}")
    run.settle()
    received = run.received()
    if received:
        result["received"] = received
    trace = run.new_trace()
    if trace:
        result["trace"] = trace
    return result


@functools.cache
def locale_installed(name: str) -> bool:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LC_") and key not in ("LANG", "LANGUAGE")
    }
    environment["LANG"] = name
    check = "import locale; locale.setlocale(locale.LC_ALL, '')"
    return (
        subprocess.run(
            [sys.executable, "-c", check], env=environment, capture_output=True, check=False
        ).returncode
        == 0
    )


def unavailable_reason(scenario: dict[str, Any] | None = None) -> str | None:
    """Why the selected implementation, or this scenario, cannot run here, or None."""
    if (
        os.environ.get("GOLDEN_SERVER_IMPL", "integrated") == "draft"
        and not DRAFT_EXPERIMENTS.is_dir()
    ):
        return f"draft experiments not found in {DRAFT_EXPERIMENTS} (GOLDEN_DRAFT_DIR)"
    # The braille transcripts depend on the default table of the locale.
    if scenario is not None and scenario["component"] == "brlapi":
        name = scenario.get("env", {}).get("LANG", DEFAULT_LANG)
        if not locale_installed(name):
            return f"locale {name} is not installed"
    return None


def run_server_scenario(path: Path) -> dict[str, Any]:
    scenario = json.loads(path.read_text())
    reason = unavailable_reason(scenario)
    if reason is not None:
        raise HarnessError(reason)
    # GOLDEN_KEEP_DIR: run in this directory and keep it, to read the logs.
    keep = os.environ.get("GOLDEN_KEEP_DIR")
    with (
        contextlib.nullcontext(keep)
        if keep
        else tempfile.TemporaryDirectory(prefix="nvda-rdp-golden-server-")
    ) as directory:
        run = ServerRun(scenario, Path(directory))
        steps: list[dict[str, Any]] = []
        try:
            run.start()
            run.settle()
            startup = run.new_trace()
            startup_received = run.received()
            for step in scenario["steps"]:
                steps.append({"step": step, **_run_step(run, step)})
        except BaseException:
            run.stop()
            log = Path(directory) / "server.log"
            if log.exists():
                sys.stderr.write(log.read_text(errors="replace"))
            raise
        returncode = run.stop()
        shutdown = run.new_trace()
        if startup_received:
            steps.insert(0, {"step": {"startup": True}, "received": startup_received})
        return {
            "scenario": path.name,
            "description": scenario.get("description", ""),
            "startup": startup,
            "steps": steps,
            "shutdown": shutdown,
            "returncode": returncode,
        }


__all__ = ["GOLDEN_DIR", "run_server_scenario"]
