# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and test the Arch Linux packages in Arch VMs (``nix run .#build-arch``,
``nix run .#test-arch``).

build: builds packaging/arch/PKGBUILD with makepkg (check() included) from the
working tree as it is, uncommitted changes included, in a fresh VM; checks
that .SRCINFO matches the PKGBUILD; runs namcap; writes the packages to
dist/arch/.

test: installs the packages of dist/arch/ in two fresh VMs and runs the system
test of system_test.py.

The logs are written to logs/, as text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import distro_vm
import system_test

SOURCE = "/home/builder/source"


class Arch(system_test.Distribution):
    name = "arch"
    common = "nvda-rdp-common-git"
    plugin = "nvda-rdp-freerdp-plugin-git"
    client = "nvda-rdp-client-git"
    server = "nvda-rdp-server-git"
    server_tools = ("openssl",)
    client_tools = ("xorg-server-xvfb", "xdotool")

    def package_glob(self, package: str) -> str:
        return f"{package}-[0-9]*.pkg.tar.zst"

    def upgrade(self, vm: distro_vm.VM, *packages: str) -> None:
        vm.run("sudo sed -i 's/^#\\?ParallelDownloads.*/ParallelDownloads = 8/' /etc/pacman.conf")
        vm.run(
            "sudo pacman-key --init >/dev/null && sudo pacman-key --populate archlinux >/dev/null"
        )
        vm.run(f"sudo pacman -Syu --noconfirm --needed {' '.join(packages)} >/dev/null")
        vm.run("pacman -Q linux pacman python freerdp 2>/dev/null; true")

    def install_files(self, vm: distro_vm.VM, packages: list[str]) -> None:
        vm.run(
            f"sudo pacman -U --noconfirm {system_test.PACKAGES_DIRECTORY}/*.pkg.tar.zst >/dev/null"
        )

    def verify(self, vm: distro_vm.VM, packages: list[str]) -> None:
        vm.run(f"pacman -Qkk {' '.join(packages)}")

    def enable_login_screen_reader(self, vm: distro_vm.VM) -> None:
        system_test.write_file(
            vm,
            "/etc/dconf/db/gdm.d/00-nvda-rdp-test",
            "[org/gnome/desktop/a11y/applications]\nscreen-reader-enabled=true\n",
        )
        # GDM 51's own profile, in /usr/share, has no system database: the
        # local profile in /etc, which takes precedence, adds it before the
        # defaults.
        vm.run(
            "grep -v '^system-db:' /usr/share/dconf/profile/gdm"
            " | sed '/^user-db:/a system-db:gdm' | sudo install -D -m 644 /dev/stdin"
            " /etc/dconf/profile/gdm && cat /etc/dconf/profile/gdm && sudo dconf update"
        )

    def enable_display_manager(self, vm: distro_vm.VM) -> None:
        vm.run("sudo systemctl enable gdm.service")


ARCH = Arch()
PACKAGES = (ARCH.common, ARCH.plugin, ARCH.client, ARCH.server)


def build(repository: Path, log: distro_vm.Log) -> None:
    dist = repository / "dist" / "arch"
    image = distro_vm.latest_image(distro_vm.IMAGES["arch"], log)
    with distro_vm.booted(image, log, name="arch-build") as vm:
        ARCH.upgrade(vm, "base-devel", "git", "namcap")
        # The golden transcripts of the server's braille use this locale; they
        # are skipped where it is not installed.
        vm.run(
            "echo 'fr_FR.UTF-8 UTF-8' | sudo tee -a /etc/locale.gen >/dev/null && sudo locale-gen"
        )
        vm.put_tree(distro_vm.source_files(repository), SOURCE)
        # makepkg clones its git source: commit the tree as it is, in the VM.
        vm.run(
            f"cd {SOURCE} && git init -q && git add -A"
            " && git -c user.name=test -c user.email=test@invalid commit -q -m 'working tree'"
        )
        vm.run(
            f"mkdir -p ~/build && sed 's|git+$url.git|git+file://{SOURCE}|'"
            f" {SOURCE}/packaging/arch/PKGBUILD > ~/build/PKGBUILD"
            f" && test $(diff {SOURCE}/packaging/arch/PKGBUILD ~/build/PKGBUILD | grep -c '^>') = 1"
        )
        vm.run("cd ~/build && makepkg --syncdeps --noconfirm --cleanbuild 2>&1", timeout=3600)
        vm.log.line(".SRCINFO must match the PKGBUILD")
        vm.run(f"cd {SOURCE}/packaging/arch && makepkg --printsrcinfo > ~/build/SRCINFO")
        dist.mkdir(parents=True, exist_ok=True)
        vm.get_files("build/SRCINFO", dist)
        # Checked last, so that the other checks run too.
        srcinfo_matches = vm.succeeds(f"diff -q {SOURCE}/packaging/arch/.SRCINFO ~/build/SRCINFO")
        vm.run(f"diff -u {SOURCE}/packaging/arch/.SRCINFO ~/build/SRCINFO", check=False)
        vm.log.line("namcap (warnings are for review, not errors)")
        vm.run(f"namcap {SOURCE}/packaging/arch/PKGBUILD; namcap ~/build/*.pkg.tar.zst; true")
        for package in PACKAGES:
            vm.run(f"pacman -Qip ~/build/{ARCH.package_glob(package)}")
            vm.run(f"pacman -Qlp ~/build/{ARCH.package_glob(package)} | grep -v '/$'")
        for old in dist.glob("*.pkg.tar.zst"):
            old.unlink()
        vm.get_files("build/*.pkg.tar.zst", dist)
    names = sorted(path.name for path in dist.glob("*.pkg.tar.zst"))
    missing = [p for p in PACKAGES if not any(n.startswith(f"{p}-") for n in names)]
    if missing:
        raise RuntimeError(f"packages not built: {missing}")
    log.line(f"built into {dist}: {' '.join(names)}")
    if not srcinfo_matches:
        raise RuntimeError(f".SRCINFO is out of date: the generated one is {dist / 'SRCINFO'}")


if __name__ == "__main__":
    sys.exit(system_test.main(ARCH, build, __doc__.split("\n")[0]))
