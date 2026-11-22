# SPDX-License-Identifier: GPL-2.0-or-later
"""Throwaway VMs of other distributions, from their official cloud images.

Used by the flake's build-* and test-* apps. For one run:

1. the distribution's latest cloud image is downloaded into the cache, or
   reused when it still matches the published digest;
2. a copy-on-write disk is created on top of it, with a cloud-init seed that
   adds the user ``builder`` (passwordless sudo) and a throwaway SSH key;
3. QEMU starts it with KVM and user networking; only SSH is forwarded, on
   127.0.0.1;
4. commands run over SSH; everything is written, as text, to the run's log;
5. the VM is powered off and its disk deleted.

Nothing is installed on the host and the VM has no access to it.
"""

from __future__ import annotations

import contextlib
import email.utils
import hashlib
import io
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Image:
    name: str
    url: str
    checksum_url: str
    """A file of ``HASH  NAME`` lines (sha256sum or sha512sum format)."""
    checksum_name: str
    """The NAME to look for in it."""
    algorithm: str = "sha256"


IMAGES = {
    "arch": Image(
        name="arch",
        url="https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2",
        checksum_url=(
            "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2.SHA256"
        ),
        checksum_name="Arch-Linux-x86_64-cloudimg.qcow2",
    ),
    # Debian testing (forky): Debian 13 has GNOME 48, whose GDM runs the local
    # and remote login screens under one user, which the server cannot tell
    # apart. "generic", not "genericcloud": GDM needs the graphics drivers
    # that the cloud kernel leaves out.
    "debian": Image(
        name="debian",
        url=(
            "https://cloud.debian.org/images/cloud/forky/daily/latest/"
            "debian-14-generic-amd64-daily.qcow2"
        ),
        checksum_url="https://cloud.debian.org/images/cloud/forky/daily/latest/SHA512SUMS",
        checksum_name="debian-14-generic-amd64-daily.qcow2",
        algorithm="sha512",
    ),
}

USER = "builder"
BOOT_TIMEOUT = 600


class Log:
    """Writes to the run's log file and to standard output."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8")
        self.path = path

    def write(self, text: str) -> None:
        self._file.write(text)
        self._file.flush()
        sys.stdout.write(text)
        sys.stdout.flush()

    def line(self, text: str) -> None:
        self.write(f"== {text}\n")

    def close(self) -> None:
        self._file.close()


def cache_directory() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "nvda-rdp-linux" / "images"


def published_digest(image: Image) -> str:
    with urllib.request.urlopen(image.checksum_url, timeout=60) as response:
        text = response.read().decode()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == image.checksum_name:
            return fields[0].lower()
    raise RuntimeError(f"no checksum for {image.checksum_name} in {image.checksum_url}")


def digest_of(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def latest_image(image: Image, log: Log) -> Path:
    """The latest published image, verified against its published digest."""
    expected = published_digest(image)
    algorithm = image.algorithm.upper()
    path = cache_directory() / f"{image.name}.qcow2"
    if path.exists() and digest_of(path, image.algorithm) == expected:
        log.line(f"cached image {path} matches the published {algorithm} {expected}")
        log.line(f"image modified {time.ctime(path.stat().st_mtime)}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.line(f"downloading {image.url}")
    partial = path.with_suffix(".partial")
    with urllib.request.urlopen(image.url, timeout=60) as response, partial.open("wb") as out:
        modified = response.headers.get("Last-Modified")
        shutil.copyfileobj(response, out, 1 << 20)
    actual = digest_of(partial, image.algorithm)
    if actual != expected:
        partial.unlink()
        raise RuntimeError(f"{algorithm} mismatch: got {actual}, published {expected}")
    if modified:
        # The cached file keeps the image's date, for the logs of later runs.
        stamp = email.utils.parsedate_to_datetime(modified).timestamp()
        os.utime(partial, (stamp, stamp))
    partial.rename(path)
    log.line(f"image modified {time.ctime(path.stat().st_mtime)}")
    log.line(f"image verified: {algorithm} {expected}")
    return path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_seed(directory: Path, public_key: str) -> Path:
    """A NoCloud seed: the ``builder`` user with the throwaway key."""
    (directory / "meta-data").write_text(
        "instance-id: nvda-rdp-test\nlocal-hostname: nvda-rdp-test\n"
    )
    (directory / "user-data").write_text(
        "#cloud-config\n"
        "users:\n"
        f"  - name: {USER}\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    shell: /bin/bash\n"
        "    lock_passwd: true\n"
        "    ssh_authorized_keys:\n"
        f"      - {public_key}\n"
        "growpart:\n"
        "  mode: auto\n"
    )
    seed = directory / "seed.iso"
    subprocess.run(
        [
            "xorriso",
            "-as",
            "mkisofs",
            "-quiet",
            "-output",
            str(seed),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(directory / "user-data"),
            str(directory / "meta-data"),
        ],
        check=True,
    )
    return seed


class VM:
    """One running VM, reached over SSH."""

    def __init__(
        self, name: str, work: Path, port: int, key: Path, process: subprocess.Popen, log: Log
    ) -> None:
        self.name, self.work, self.port, self.key = name, work, port, key
        self.process, self.log = process, log

    def _ssh(self) -> list[str]:
        return [
            "ssh",
            "-q",
            "-i",
            str(self.key),
            "-p",
            str(self.port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=30",
            f"{USER}@127.0.0.1",
        ]

    def run(self, command: str, check: bool = True, timeout: float | None = None) -> str:
        """Run a shell command as ``builder``; its output goes to the log."""
        self.log.write(f"{self.name}$ {command}\n")
        process = subprocess.Popen(
            [*self._ssh(), "bash", "-lc", shlex.quote(command)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        output = []
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            self.log.write(line)
        code = process.wait(timeout=timeout)
        if check and code != 0:
            raise RuntimeError(f"command failed with exit code {code}: {command}")
        return "".join(output)

    def succeeds(self, command: str) -> bool:
        return (
            subprocess.run(
                [*self._ssh(), "bash", "-lc", shlex.quote(command)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    def put_tree(self, files: dict[str, tuple[bytes, int]], destination: str) -> None:
        """Copy files (relative path: content and mode) into ``destination`` in the VM."""
        buffer = io.BytesIO()
        now = time.time()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for name, (data, mode) in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = mode
                info.mtime = now
                tar.addfile(info, io.BytesIO(data))
        self.log.line(f"copying {len(files)} files to {destination}")
        subprocess.run(
            [
                *self._ssh(),
                f"mkdir -p {shlex.quote(destination)} && tar -x -C {shlex.quote(destination)}",
            ],
            input=buffer.getvalue(),
            check=True,
        )

    def get_files(self, pattern: str, destination: Path) -> list[Path]:
        """Copy the files matching a shell glob in the VM into ``destination``."""
        destination.mkdir(parents=True, exist_ok=True)
        data = subprocess.run(
            [*self._ssh(), f"tar -c -f - {pattern}"], capture_output=True, check=True
        ).stdout
        paths = []
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    target = destination / Path(member.name).name
                    extracted = tar.extractfile(member)
                    assert extracted is not None
                    target.write_bytes(extracted.read())
                    paths.append(target)
        for path in paths:
            self.log.line(f"copied out {path}")
        return paths


def wait_for_ssh(vm: VM) -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT
    while not vm.succeeds("cloud-init status --wait >/dev/null 2>&1 || true; true"):
        if vm.process.poll() is not None:
            raise RuntimeError(f"QEMU exited with code {vm.process.returncode}")
        if time.monotonic() > deadline:
            raise TimeoutError("the VM did not answer over SSH")
        time.sleep(5)
    vm.log.line(f"{vm.name} reachable over SSH")


@contextlib.contextmanager
def booted(
    image: Path,
    log: Log,
    name: str = "vm",
    memory_mib: int = 4096,
    disk: str = "40G",
    extra_args: tuple[str, ...] = (),
) -> Iterator[VM]:
    """A VM of ``image`` (from latest_image), powered off when the block ends.

    ``extra_args`` are added to QEMU's command line, e.g. a second network.
    """
    with tempfile.TemporaryDirectory(prefix=f"nvda-rdp-{name}-") as directory:
        work = Path(directory)
        key = work / "id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "nvda-rdp-test", "-f", str(key)],
            check=True,
        )
        seed = write_seed(work, (work / "id_ed25519.pub").read_text().strip())
        overlay = work / "disk.qcow2"
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-q",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(image),
                str(overlay),
                disk,
            ],
            check=True,
        )
        port = free_port()
        console = log.path.with_suffix(f".{name}.console.log")
        command = [
            "qemu-system-x86_64",
            "-enable-kvm",
            "-cpu",
            "host",
            "-m",
            str(memory_mib),
            "-smp",
            str(min(os.cpu_count() or 2, 4)),
            "-display",
            "none",
            "-drive",
            f"file={overlay},if=virtio",
            "-drive",
            f"file={seed},media=cdrom,readonly=on",
            "-nic",
            f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-serial",
            f"file:{console}",
            *extra_args,
        ]
        log.line(f"starting {name}: SSH on 127.0.0.1:{port}, console in {console}")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        vm = VM(name, work, port, key, process, log)
        try:
            wait_for_ssh(vm)
            yield vm
        finally:
            if process.poll() is None:
                vm.succeeds("sudo systemctl poweroff")
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.line(f"{name} stopped")


def source_files(repository: Path) -> dict[str, tuple[bytes, int]]:
    """The files of the working tree Git knows of or would add, as they are now."""
    names = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    files = {}
    for name in filter(None, (n.decode() for n in names)):
        path = repository / name
        if path.is_file() and not path.is_symlink():
            files[name] = (path.read_bytes(), 0o755 if os.access(path, os.X_OK) else 0o644)
    return files
