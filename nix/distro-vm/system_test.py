# SPDX-License-Identifier: GPL-2.0-or-later
"""The system test of the distribution packages, in two VMs of the distribution.

It plays the scenario of nix/tests/server.nix on a private network between two
fresh VMs where the built packages are installed:

- server: GDM with GNOME Remote Desktop's remote login, Orca on the login
  screen, and nvda-rdp-server;
- client: xfreerdp3 with the nvda-rdp-dvc plugin and nvda-rdp-client, whose
  Speech Dispatcher records what it is asked to speak.

The client connects to the server's remote login screen; Orca's speech there
must reach the client's Speech Dispatcher, while the server's local login
screen keeps its usual speech.

Each distribution (arch.py, debian.py) gives a Distribution: how to install
packages and how to turn on Orca on GDM's login screen.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import distro_vm

NETWORK = "192.168.77"
SERVER_ADDRESS = f"{NETWORK}.1"
CLIENT_ADDRESS = f"{NETWORK}.2"
SERVER_MAC = "52:54:00:4e:56:01"
CLIENT_MAC = "52:54:00:4e:56:02"
ASSETS = "/opt/nvda-rdp-test"
PACKAGES_DIRECTORY = "/home/builder/packages"

# The RDP credentials are a fixture of these throwaway VMs; they are only
# passed on standard input.
RDP_USER = "rdp-test"
RDP_PASSWORD = "rdp-test-password"

CLIENT_USER = "alice"


class Distribution:
    """What differs between distributions; subclasses fill it in."""

    name = ""
    common = ""
    plugin = ""
    client = ""
    server = ""
    server_tools: tuple[str, ...] = ()
    """Packages of the distribution the server test needs (e.g. openssl)."""
    client_tools: tuple[str, ...] = ()
    """Packages of the distribution the client test needs (Xvfb, xdotool)."""

    def package_glob(self, package: str) -> str:
        raise NotImplementedError

    def upgrade(self, vm: distro_vm.VM, *packages: str) -> None:
        """Bring the system up to date and install these packages."""
        raise NotImplementedError

    def install_files(self, vm: distro_vm.VM, packages: list[str]) -> None:
        """Install the package files of PACKAGES_DIRECTORY, with their dependencies."""
        raise NotImplementedError

    def verify(self, vm: distro_vm.VM, packages: list[str]) -> None:
        """Check that the installed files match their packages."""
        raise NotImplementedError

    def enable_login_screen_reader(self, vm: distro_vm.VM) -> None:
        """Turn on Orca on GDM's login screen."""
        raise NotImplementedError

    def enable_display_manager(self, vm: distro_vm.VM) -> None:
        raise NotImplementedError


def package_files(
    distribution: Distribution, dist: Path, *names: str
) -> dict[str, tuple[bytes, int]]:
    files = {}
    for name in names:
        matches = sorted(dist.glob(distribution.package_glob(name)))
        if len(matches) != 1:
            raise RuntimeError(f"expected one {name} package in {dist}, found {len(matches)}")
        files[matches[0].name] = (matches[0].read_bytes(), 0o644)
    return files


def install(distribution: Distribution, vm: distro_vm.VM, dist: Path, *names: str) -> None:
    vm.put_tree(package_files(distribution, dist, *names), PACKAGES_DIRECTORY)
    distribution.install_files(vm, list(names))
    vm.log.line("installed files must match their packages")
    distribution.verify(vm, list(names))


def join_network(vm: distro_vm.VM, mac: str, address: str) -> None:
    """Give the private network's interface its address."""
    vm.run(
        f"interface=$(ip -o link | awk -F': ' '/{mac}/ {{print $2}}')"
        f" && sudo ip link set $interface up && sudo ip addr add {address}/24 dev $interface"
        f" && echo '{SERVER_ADDRESS} server' | sudo tee -a /etc/hosts >/dev/null"
    )


def write_file(vm: distro_vm.VM, path: str, text: str, mode: str = "644") -> None:
    vm.run(
        f"sudo install -D -m {mode} /dev/stdin {path} <<'NVDA_RDP_EOF'\n{text}NVDA_RDP_EOF",
    )


def set_up_server(distribution: Distribution, vm: distro_vm.VM, dist: Path) -> None:
    distribution.upgrade(vm, *distribution.server_tools)
    install(distribution, vm, dist, distribution.common, distribution.server)
    distribution.enable_login_screen_reader(vm)
    # GNOME Remote Desktop logs at WARN by default; the test reads the
    # channel library's INFO and DEBUG lines.
    write_file(
        vm,
        "/etc/systemd/user/gnome-remote-desktop-handover.service.d/nvda-rdp-test.conf",
        "[Service]\nEnvironment=WLOG_FILTER=nvda-rdp.grd-preload:DEBUG\n",
    )
    # GDM's remote login, configured as GNOME Remote Desktop documents it.
    tls = "/var/lib/gnome-remote-desktop/tls"
    vm.run(
        f"sudo install -d -o gnome-remote-desktop -g gnome-remote-desktop -m 700 {tls}"
        " && sudo -u gnome-remote-desktop openssl req -quiet -new -newkey rsa:2048 -days 30"
        f" -nodes -x509 -subj /CN=server -out {tls}/tls.crt -keyout {tls}/tls.key 2>/dev/null"
        f" && sudo grdctl --system rdp set-tls-key {tls}/tls.key"
        f" && sudo grdctl --system rdp set-tls-cert {tls}/tls.crt"
    )
    # One prompt only: grdctl 50.1 crashed when both the username and the
    # password came from a non-terminal standard input.
    vm.run(
        f"echo {shlex.quote(RDP_PASSWORD)} | sudo grdctl --system rdp set-credentials {RDP_USER}"
    )
    vm.run("sudo grdctl --system rdp enable && sudo grdctl --system status")
    vm.run("sudo systemctl set-default graphical.target")
    distribution.enable_display_manager(vm)
    # GDM first, or the daemon never listens (seen in the study).
    write_file(
        vm,
        "/etc/systemd/system/gnome-remote-desktop.service.d/nvda-rdp-test.conf",
        "[Unit]\nAfter=display-manager.service\n",
    )
    vm.run("sudo systemctl daemon-reload && sudo systemctl start display-manager.service")
    vm.run("sudo systemctl restart gnome-remote-desktop.service")


def set_up_client(
    distribution: Distribution, vm: distro_vm.VM, dist: Path, repository: Path
) -> None:
    distribution.upgrade(vm, *distribution.client_tools)
    install(distribution, vm, dist, distribution.common, distribution.plugin, distribution.client)
    recorder = (repository / "nix/tests/speechd-recorder.py").read_bytes()
    vm.put_tree({"speechd-recorder.py": (recorder, 0o755)}, "/tmp/assets")
    vm.run(f"sudo install -d {ASSETS} && sudo install -m 755 /tmp/assets/* {ASSETS}")
    write_file(
        vm,
        f"{ASSETS}/speechd-recorder",
        f"#!/bin/sh\nexec python3 {ASSETS}/speechd-recorder.py\n",
        "755",
    )
    write_file(
        vm,
        f"{ASSETS}/speechd/speechd.conf",
        f'AddModule "recorder" "{ASSETS}/speechd-recorder" ""\nDefaultModule recorder\n',
    )
    # Username, domain (empty), password: the order of xfreerdp's /from-stdin.
    write_file(vm, f"{ASSETS}/credentials", f"{RDP_USER}\n\n{RDP_PASSWORD}\n")
    # A user with a running systemd manager and session bus, as after a login.
    vm.run(f"sudo useradd --create-home {CLIENT_USER} && sudo loginctl enable-linger {CLIENT_USER}")


class Test:
    def __init__(self, server: distro_vm.VM, client: distro_vm.VM, log: distro_vm.Log) -> None:
        self.server, self.client, self.log = server, client, log

    def in_user(self, command: str) -> str:
        return f"sudo systemctl --user -M {CLIENT_USER}@ {command}"

    def wait(self, vm: distro_vm.VM, command: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not vm.succeeds(command):
            if time.monotonic() > deadline:
                self.log.line(f"timed out after {timeout} s: {command}")
                self.dump()
                raise TimeoutError(command)
            time.sleep(2)

    def journal_has(self, identifier: str, text: str) -> str:
        """Read by syslog identifier: journald may miss the unit of a short process."""
        return f"sudo journalctl -t {identifier} -o cat | grep -q {shlex.quote(text)}"

    def dump(self) -> None:
        self.log.line("server state")
        self.server.run(f"sudo sh {ASSETS}/server-state.sh 2>&1", check=False)
        self.server.run(
            "sudo journalctl -b --no-pager -o short-monotonic"
            " -t nvda-rdp-server-session -t gnome-remote-desktop-daemon -t nvda-rdp-brlapi-server"
            " -t orca -t gdm -t gnome-remote-desktop -t speech-dispatcher | tail -200",
            check=False,
        )
        self.log.line("client state")
        self.client.run(
            "sudo journalctl -b --no-pager -o cat"
            f" _UID=$(id -u {CLIENT_USER}) | tail -100; sudo cat /tmp/xfreerdp.log",
            check=False,
        )

    def subtest(self, name: str) -> None:
        self.log.line(f"subtest: {name}")

    def run(self, repository: Path) -> None:
        state = (repository / "nix/tests/server-state.sh").read_bytes()
        self.server.put_tree({"server-state.sh": (state, 0o755)}, "/tmp/assets")
        self.server.run(
            f"sudo install -D -m 755 /tmp/assets/server-state.sh {ASSETS}/server-state.sh"
        )

        self.subtest("The local login screen keeps its usual speech")
        self.wait(self.server, "systemctl is-active display-manager.service", 120)
        self.wait(self.server, self.journal_has("nvda-rdp-server-session", "local session"), 300)
        # [n]: a pattern must not match the shell that runs pgrep. The
        # launchers exec Python, so the processes show their module.
        self.server.run(
            "! pgrep -f '[n]vda_rdp_linux.server.brlapi_main'"
            " && ! pgrep -f '[n]vda_rdp_linux.server.speechd_main'"
        )

        self.subtest("GNOME Remote Desktop listens")
        self.wait(self.server, "ss -ltn | grep -q ':3389 '", 120)

        self.subtest("The client is ready")
        uid = self.client.run(f"id -u {CLIENT_USER}").strip()
        runtime = f"/run/user/{uid}"
        self.wait(self.client, f"sudo test -S {runtime}/bus", 60)
        # Speech Dispatcher's own socket-activated instance would listen at the
        # address of the recording one.
        self.client.run(self.in_user("mask --now speech-dispatcher.socket"))
        self.client.run(
            self.in_user(
                f"set-environment DISPLAY=:0 RECORDED_SPEECH={runtime}/recorded-speech.txt"
            )
        )
        self.client.run(
            f"sudo systemd-run --user -M {CLIENT_USER}@ --unit=xvfb"
            " Xvfb :0 -screen 0 1280x1024x24 -nolisten tcp"
        )
        self.client.run(
            f"sudo systemd-run --user -M {CLIENT_USER}@ --unit=recording-speechd"
            " -p RuntimeDirectory=speech-dispatcher -p RuntimeDirectoryPreserve=yes"
            f" speech-dispatcher -s -t 0 -C {ASSETS}/speechd -c unix_socket"
            f" -S {runtime}/speech-dispatcher/speechd.sock"
            f" -P {runtime}/speech-dispatcher/pid -L {runtime}/speech-dispatcher"
        )
        self.wait(self.client, f"sudo test -S {runtime}/speech-dispatcher/speechd.sock", 60)
        self.wait(self.client, "test -S /tmp/.X11-unix/X0", 60)
        # Speech Dispatcher also loads its usual modules: name the recorder.
        self.client.run(
            f"sudo -u {CLIENT_USER} sh -c 'mkdir -p ~/.config/nvda-rdp-linux"
            " && echo NVDA_RDP_OPTIONS=--output-module=recorder"
            " > ~/.config/nvda-rdp-linux/environment'"
        )
        self.client.run(self.in_user("start nvda-rdp-client.service"))
        self.wait(self.client, f"sudo test -S {runtime}/nvda-rdp-linux/speech.sock", 60)

        self.subtest("Connect to the remote login screen")
        self.client.run(
            f"sudo systemd-run --user -M {CLIENT_USER}@ --unit=xfreerdp"
            f" -p StandardInput=file:{ASSETS}/credentials"
            " -p StandardOutput=file:/tmp/xfreerdp.log -p StandardError=inherit"
            " xfreerdp3 /v:server /cert:ignore /from-stdin /dvc:nvda-rdp-dvc"
        )

        self.subtest("The remote login screen uses nvda-rdp")
        self.wait(self.server, self.journal_has("nvda-rdp-server-session", "remote session"), 300)
        self.wait(self.server, "pgrep -f '[n]vda_rdp_linux.server.brlapi_main'", 60)
        # The channel library is loaded into the handover daemon.
        self.wait(
            self.server,
            "for pid in $(pgrep -f '[g]nome-remote-desktop-daemon --handover'); do"
            " sudo grep -q libnvda-rdp-grd-preload /proc/$pid/maps && exit 0; done; exit 1",
            60,
        )
        self.wait(
            self.server,
            self.journal_has("gnome-remote-desktop-daemon", "NVDA-SPEECH: channel open"),
            120,
        )

        self.subtest("Both channels negotiate RDAccess version 2")
        self.wait(
            self.server,
            "sudo sh -c \"grep -q 'NVDA-SPEECH: protocol version 2 negotiated'"
            ' /run/user/*/nvda-rdp-speechd-module.log"',
            120,
        )
        self.wait(
            self.server,
            self.journal_has(
                "nvda-rdp-brlapi-server", "NVDA-BRAILLE: protocol version 2 negotiated"
            ),
            120,
        )

        self.subtest("Orca's speech reaches the client's Speech Dispatcher")
        # The client announces the connection itself; Orca speaks when the
        # focus moves on the login screen, here with Tab in xfreerdp's window,
        # which GNOME Remote Desktop passes to the session.
        record = f"{runtime}/recorded-speech.txt"
        tab = (
            f"sudo -u {CLIENT_USER} DISPLAY=:0 xdotool search --class freerdp"
            " windowfocus --sync key Tab"
        )
        orca_spoke = f"sudo grep -v 'Remote speech connected' {record} | grep -q ."
        self.wait(self.client, f"{tab} >/dev/null 2>&1; sleep 1; {orca_spoke}", 120)
        self.client.run(f"sudo cat {record}")

        self.subtest("Logs")
        self.dump()


def test(distribution: Distribution, repository: Path, log: distro_vm.Log) -> None:
    dist = repository / "dist" / distribution.name
    image = distro_vm.latest_image(distro_vm.IMAGES[distribution.name], log)
    port = distro_vm.free_port()
    link = "virtio-net-pci,netdev=link,mac="
    with (
        distro_vm.booted(
            image,
            log,
            name=f"{distribution.name}-server",
            extra_args=(
                "-netdev",
                f"socket,id=link,listen=127.0.0.1:{port}",
                "-device",
                f"{link}{SERVER_MAC}",
            ),
        ) as server,
        distro_vm.booted(
            image,
            log,
            name=f"{distribution.name}-client",
            memory_mib=2048,
            extra_args=(
                "-netdev",
                f"socket,id=link,connect=127.0.0.1:{port}",
                "-device",
                f"{link}{CLIENT_MAC}",
            ),
        ) as client,
    ):
        join_network(server, SERVER_MAC, SERVER_ADDRESS)
        join_network(client, CLIENT_MAC, CLIENT_ADDRESS)
        client.run(f"ping -c 1 -W 5 {SERVER_ADDRESS} >/dev/null")
        set_up_server(distribution, server, dist)
        set_up_client(distribution, client, dist, repository)
        Test(server, client, log).run(repository)
    log.line("all subtests passed")


def main(
    distribution: Distribution,
    build: Callable[[Path, distro_vm.Log], None],
    description: str,
) -> int:
    """The command line of the build-* and test-* apps."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("command", choices=("build", "test"))
    parser.add_argument("repository", type=Path, help="the working tree to build or test")
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = distro_vm.Log(
        repository / "logs" / f"{distribution.name}-{arguments.command}-{stamp}.log"
    )
    try:
        if arguments.command == "build":
            build(repository, log)
        else:
            test(distribution, repository, log)
    except (RuntimeError, TimeoutError, subprocess.CalledProcessError) as error:
        log.line(f"FAILED: {error}")
        log.line(f"log: {log.path}")
        return 1
    finally:
        log.close()
    print(f"log: {log.path}")
    return 0
