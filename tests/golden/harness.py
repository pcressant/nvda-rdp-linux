"""Black-box golden harness for the RDAccess client.

The client runs as in production (``python -m nvda_rdp_linux``) in its own
process.  The system libraries and tools it talks to are replaced by the fakes
in ``fakes/`` (Speech Dispatcher, BrlAPI, AT-SPI, audio player, X11 tools),
and this harness plays the FreeRDP plugin on the Unix sockets.  A scenario is
a list of steps; for each step the transcript holds the frames the client sent
back and the calls the fakes received.

The harness deliberately imports nothing from ``nvda_rdp_linux``: it only
relies on the client's external behaviour, so the transcripts stay valid
across internal refactoring.

Synchronization: after a step that sends data, a barrier attribute request
(``language`` on speech, ``numCols`` on braille) is sent as its own frame.
The client handles one channel's frames in order, so the barrier's answer
marks the end of that step.  Steps with asynchronous effects (focus, keys,
disconnection) wait until the trace and the sockets have been quiet for
``SETTLE_SECONDS``.

Startup is not timed: the client's main thread (Speech Dispatcher, AT-SPI)
and its BrlAPI connection thread start concurrently.  A scenario lists in
``startup_trace`` the last event of each source its startup produces; the
startup section ends once they are all traced and the trace is quiet.  Its
events are then listed by source, in ``STARTUP_SOURCES`` order, each source
keeping its own order: only the order between threads is not deterministic.
"""

from __future__ import annotations

import itertools
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).resolve().parent
FAKES_DIR = GOLDEN_DIR / "fakes"
REPOSITORY = GOLDEN_DIR.parent.parent
FIXTURES = (
    REPOSITORY / "tests" / "fixtures" / "rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95"
)

STARTUP_TIMEOUT = 10.0
STEP_TIMEOUT = 5.0
SETTLE_SECONDS = 0.25
CHANNELS = ("speech", "braille")
BARRIER_ATTRIBUTE = {"speech": "language", "braille": "numCols"}
HEADER = struct.Struct("!I")
# Order of the sources in the startup section (see the module docstring).
STARTUP_SOURCES = ("ssip", "atspi", "brlapi")


class HarnessError(RuntimeError):
    pass


def encode_frame(payload: bytes) -> bytes:
    return HEADER.pack(len(payload)) + payload


def describe_payload(payload: bytes) -> dict[str, str]:
    """Readable form of one frame: JSON lines as text, anything else as hex."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"hex": payload.hex(" ")}
    if text and all(char.isprintable() or char == "\n" for char in text):
        return {"text": text}
    return {"hex": payload.hex(" ")}


def _barrier_request(channel: str) -> bytes:
    attribute = BARRIER_ATTRIBUTE[channel]
    return json.dumps({"type": "attribute_request", "attribute": attribute}).encode() + b"\n"


def _is_barrier_answer(channel: str, payload: bytes) -> bool:
    try:
        message = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(message, dict)
        and message.get("type") == "attribute_value"
        and message.get("attribute") == BARRIER_ATTRIBUTE[channel]
    )


@dataclass
class PluginConnection:
    """The harness side of one plugin socket, with IPC frame reassembly."""

    sock: socket.socket
    buffer: bytearray = field(default_factory=bytearray)
    frames: list[bytes] = field(default_factory=list)
    closed: bool = False

    def send(self, payload: bytes) -> None:
        self.sock.sendall(encode_frame(payload))

    def pump(self, timeout: float) -> bool:
        """Read what is available within ``timeout``; return True if data came."""
        if self.closed:
            return False
        readable, _, _ = select.select([self.sock], [], [], timeout)
        if not readable:
            return False
        data = self.sock.recv(65536)
        if not data:
            self.closed = True
            return False
        self.buffer.extend(data)
        while len(self.buffer) >= HEADER.size:
            (size,) = HEADER.unpack_from(self.buffer)
            if len(self.buffer) < HEADER.size + size:
                break
            self.frames.append(bytes(self.buffer[HEADER.size : HEADER.size + size]))
            del self.buffer[: HEADER.size + size]
        return True

    def take(self) -> list[bytes]:
        frames, self.frames = self.frames, []
        return frames


class Run:
    """One client process and its environment."""

    def __init__(self, scenario: dict[str, Any], workdir: Path) -> None:
        self.scenario = scenario
        self.workdir = workdir
        self.runtime = workdir / "run"
        self.control = workdir / "control"
        self.trace_path = workdir / "trace.jsonl"
        self.waves = workdir / "waves"
        self.trace_offset = 0
        self.connections: dict[str, PluginConnection] = {}
        self.process: subprocess.Popen[bytes] | None = None
        self.log_path = workdir / "client.log"

    # Process

    def start(self) -> None:
        for directory in (self.runtime, self.control, self.waves):
            directory.mkdir(mode=0o700, parents=True)
        self.trace_path.touch()
        for name, content in self.scenario.get("waves", {}).items():
            path = self.waves / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes.fromhex(content))
        for name, content in self.scenario.get("control", {}).items():
            (self.control / name).write_text(content)
        substitutions = {
            "{waves}": str(self.waves),
            "{player}": f"{sys.executable} {FAKES_DIR / 'bin' / 'player'}",
        }
        args = [substitutions.get(arg, arg) for arg in self.scenario.get("args", [])]
        self.process = subprocess.Popen(
            [sys.executable, "-m", "nvda_rdp_linux", "--log-level", "DEBUG", *args],
            cwd=REPOSITORY,
            env=self._environment(),
            stdout=subprocess.DEVNULL,
            stderr=open(self.log_path, "wb"),  # noqa: SIM115 - closed with the process.
        )
        self._wait_for_sockets(args)

    def _environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("NVDA_", "LC_", "DISPLAY", "WAYLAND_", "GOLDEN_"))
            and key not in ("LANG", "LANGUAGE")
        }
        python_path = [str(FAKES_DIR), str(REPOSITORY)]
        if os.environ.get("PYTHONPATH"):
            python_path.append(os.environ["PYTHONPATH"])
        environment.update(
            {
                "PYTHONPATH": os.pathsep.join(python_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": os.pathsep.join([str(FAKES_DIR / "bin"), os.environ.get("PATH", "")]),
                "XDG_RUNTIME_DIR": str(self.runtime),
                "LANG": "fr_FR.UTF-8",
                "GOLDEN_TRACE": str(self.trace_path),
                "GOLDEN_CONTROL_DIR": str(self.control),
                "GOLDEN_FAKE_OPTIONS": json.dumps(self.scenario.get("fake", {})),
            }
        )
        environment.update(self.scenario.get("env", {}))
        return environment

    def _wait_for_sockets(self, args: list[str]) -> None:
        wanted = [
            channel
            for channel in CHANNELS
            if f"--no-{channel}" not in args
            and not (channel == "speech" and self.scenario.get("fake", {}).get("ssip_fail"))
        ]
        deadline = time.monotonic() + STARTUP_TIMEOUT
        for channel in wanted:
            path = self.socket_path(channel)
            while not path.exists():
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise HarnessError(f"client did not create {path}; see {self.log_path}")
                time.sleep(0.02)

    def socket_path(self, channel: str) -> Path:
        return self.runtime / "nvda-rdp-linux" / f"{channel}.sock"

    def stop(self) -> int | None:
        for channel in list(self.connections):
            self.disconnect(channel)
        self.settle()
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        try:
            return self.process.wait(timeout=STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            return None

    # Plugin side

    def connect(self, channel: str) -> None:
        # The socket file exists between bind() and listen(): retry briefly.
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(self.socket_path(channel)))
            except (ConnectionRefusedError, FileNotFoundError):
                sock.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
            else:
                break
        self.connections[channel] = PluginConnection(sock)

    def disconnect(self, channel: str) -> None:
        connection = self.connections.pop(channel, None)
        if connection is not None:
            connection.sock.close()

    def send(self, channel: str, payloads: list[bytes]) -> None:
        connection = self._connection(channel)
        for payload in payloads:
            connection.send(payload)

    def barrier(self, channel: str, expected_answers: int) -> bool:
        """Send the barrier request; return False if the client never answers."""
        connection = self._connection(channel)
        connection.send(_barrier_request(channel))
        expected_answers += 1
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            answers = sum(_is_barrier_answer(channel, frame) for frame in connection.frames)
            if answers >= expected_answers:
                return True
            if connection.closed:
                return False
            connection.pump(0.05)
        return False

    def settle(self) -> None:
        """Wait until neither the trace nor any socket changed for a while."""
        deadline = time.monotonic() + STEP_TIMEOUT
        quiet_since = time.monotonic()
        size = self.trace_path.stat().st_size
        while time.monotonic() < deadline:
            activity = any(c.pump(0.02) for c in self.connections.values())
            new_size = self.trace_path.stat().st_size
            if activity or new_size != size:
                size = new_size
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= SETTLE_SECONDS:
                return
            if not self.connections:
                time.sleep(0.02)

    def wait_trace(self, wanted: dict[str, Any], count: int = 1) -> bool:
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            matches = [
                event
                for event in self._read_trace(0)
                if all(event.get(key) == value for key, value in wanted.items())
            ]
            if len(matches) >= count:
                return True
            time.sleep(0.02)
        return False

    def _connection(self, channel: str) -> PluginConnection:
        try:
            return self.connections[channel]
        except KeyError:
            raise HarnessError(f"{channel} is not connected") from None

    # Control files

    def append_control(self, name: str, value: object) -> None:
        with open(self.control / name, "a", encoding="utf-8") as control:
            control.write(json.dumps(value) + "\n")

    def write_control(self, name: str, content: str) -> None:
        (self.control / name).write_text(content)

    # Trace

    def _read_trace(self, offset: int) -> list[dict[str, Any]]:
        with open(self.trace_path, encoding="utf-8") as trace:
            trace.seek(offset)
            return [json.loads(line) for line in trace.read().splitlines() if line]

    def new_trace(self) -> list[dict[str, Any]]:
        with open(self.trace_path, encoding="utf-8") as trace:
            trace.seek(self.trace_offset)
            data = trace.read()
        complete = data[: data.rfind("\n") + 1]
        self.trace_offset += len(complete.encode("utf-8"))
        text = complete.replace(str(self.workdir), "{workdir}")
        return [json.loads(line) for line in text.splitlines() if line]

    def received(self) -> dict[str, list[dict[str, str]]]:
        received = {}
        for channel, connection in self.connections.items():
            frames = connection.take()
            if frames:
                received[channel] = [describe_payload(frame) for frame in frames]
        return received


def _payloads(step: dict[str, Any]) -> list[bytes]:
    if "text" in step:
        data = step["text"].encode("utf-8")
    elif "hex" in step:
        data = bytes.fromhex(step["hex"])
    elif "fixture" in step:
        data = bytes.fromhex((FIXTURES / step["fixture"]).read_text())
    elif "json" in step:
        data = b"".join(json.dumps(message).encode() + b"\n" for message in step["json"])
    else:
        raise HarnessError(f"send step without data: {step}")
    cuts = [0, *step.get("split", []), len(data)]
    return [data[start:end] for start, end in itertools.pairwise(cuts)]


def _count_requests(channel: str, payloads: list[bytes]) -> int:
    """Count the barrier attribute requested by the scenario itself in this step.

    The step's data is scanned like the client does: JSON lines, and legacy
    frames (type byte, ``@``, 16-bit little-endian length, payload) whose
    payload is an attribute name between backquotes with no value.
    """
    name = BARRIER_ATTRIBUTE[channel]
    legacy_request = b"`" + name.encode() + b"`"
    data = b"".join(payloads)
    count = 0
    position = 0
    while position < len(data):
        if data[position : position + 1] == b"{":
            end = data.find(b"\n", position)
            end = len(data) if end < 0 else end
            try:
                message = json.loads(data[position:end])
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = None
            if (
                isinstance(message, dict)
                and message.get("type") == "attribute_request"
                and message.get("attribute") == name
            ):
                count += 1
            position = end + 1
        elif data[position + 1 : position + 2] == b"@" and position + 4 <= len(data):
            size = int.from_bytes(data[position + 2 : position + 4], "little")
            if data[position + 4 : position + 4 + size] == legacy_request:
                count += 1
            position += 4 + size
        else:
            position += 1
    return count


def _brlapi_key(key: dict[str, Any]) -> int:
    import brlapi

    if "sym" in key:
        return brlapi.KEY_TYPE_SYM | key["sym"]
    if "passdots" in key:
        argument = key["passdots"] | (brlapi.DOTC if key.get("space") else 0)
        return brlapi.KEY_TYPE_CMD | brlapi.KEY_CMD_PASSDOTS | argument
    command = getattr(brlapi, f"KEY_CMD_{key['cmd']}")
    return brlapi.KEY_TYPE_CMD | command | key.get("arg", 0)


def _run_step(run: Run, step: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "connect" in step:
        channel = step["connect"]
        run.connect(channel)
        if step.get("barrier", True) and not run.barrier(channel, 0):
            result["barrier_timeout"] = True
    elif "send" in step:
        channel = step["send"]
        payloads = _payloads(step)
        run.send(channel, payloads)
        expected = _count_requests(channel, payloads)
        if step.get("barrier", True) and not run.barrier(channel, expected):
            result["barrier_timeout"] = True
        if step.get("settle") or not step.get("barrier", True):
            run.settle()
    elif "disconnect" in step:
        run.disconnect(step["disconnect"])
        run.settle()
    elif "key" in step:
        code = _brlapi_key(step["key"])
        run.append_control("brlapi-keys", code)
        if not run.wait_trace({"src": "brlapi", "call": "readKey", "args": [code]}):
            result["key_not_read"] = True
        run.settle()
    elif "focus" in step:
        run.append_control("atspi-events", step["focus"])
        run.settle()
    elif "capslock" in step:
        run.write_control("capslock", step["capslock"])
    elif "wait_trace" in step:
        if not run.wait_trace(step["wait_trace"], step.get("count", 1)):
            result["wait_timeout"] = True
        run.settle()
    elif "settle" in step:
        run.settle()
    else:
        raise HarnessError(f"unknown step: {step}")
    received = run.received()
    if received:
        result["received"] = received
    trace = run.new_trace()
    if trace:
        result["trace"] = trace
    return result


def run_scenario(path: Path) -> dict[str, Any]:
    """Run one scenario file and return its transcript."""
    scenario = json.loads(path.read_text())
    with tempfile.TemporaryDirectory(prefix="nvda-rdp-golden-") as directory:
        run = Run(scenario, Path(directory))
        steps: list[dict[str, Any]] = []
        try:
            run.start()
            for wanted in scenario.get("startup_trace", []):
                if not run.wait_trace(wanted):
                    raise HarnessError(f"startup did not trace {wanted}")
            run.settle()
            startup = _by_source(run.new_trace())
            for step in scenario["steps"]:
                steps.append({"step": step, **_run_step(run, step)})
        except BaseException:
            run.stop()
            sys.stderr.write(run.log_path.read_text(errors="replace"))
            raise
        returncode = run.stop()
        shutdown = run.new_trace()
        return {
            "scenario": path.name,
            "description": scenario.get("description", ""),
            "startup": startup,
            "steps": steps,
            "shutdown": shutdown,
            "returncode": returncode,
        }


def _by_source(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Startup events listed by source; ``sorted`` keeps each source's order."""

    def rank(event: dict[str, Any]) -> int:
        source = event.get("src")
        return STARTUP_SOURCES.index(source) if source in STARTUP_SOURCES else len(STARTUP_SOURCES)

    return sorted(events, key=rank)


def _line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump(transcript: dict[str, Any]) -> str:
    """Text form of a transcript: one frame or one fake call per line, for review."""
    lines = [
        f"# {transcript['scenario']}: {transcript['description']}",
        "startup:",
        *(f"  trace {_line(event)}" for event in transcript["startup"]),
    ]
    for number, step in enumerate(transcript["steps"], 1):
        lines.append(f"step {number}: {_line(step['step'])}")
        lines.extend(f"  {flag}" for flag, value in step.items() if value is True)
        for channel, frames in step.get("received", {}).items():
            lines.extend(f"  {channel} <- {_line(frame)}" for frame in frames)
        lines.extend(f"  trace {_line(event)}" for event in step.get("trace", []))
    lines.append("shutdown:")
    lines.extend(f"  trace {_line(event)}" for event in transcript["shutdown"])
    lines.append(f"returncode: {transcript['returncode']}")
    return "\n".join(lines) + "\n"
