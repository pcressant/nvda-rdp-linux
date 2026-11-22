# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and test the Debian packages in Debian VMs (``nix run .#build-debian``,
``nix run .#test-debian``).

The target is Debian testing (forky): Debian 13 has GNOME 48, whose GDM runs
the local and remote login screens as one user, which the server cannot tell
apart.

build: builds the source and binary packages of packaging/debian with
dpkg-buildpackage (the tests included) from the working tree as it is,
uncommitted changes included, in a fresh VM; runs lintian; writes the
packages to dist/debian/.

test: installs the packages of dist/debian/ in two fresh VMs and runs the
system test of system_test.py.

The logs are written to logs/, as text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import distro_vm
import system_test

BUILD = "/home/builder/build"
SOURCE = f"{BUILD}/nvda-rdp-linux"
APT = "sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q -o Dpkg::Use-Pty=0"


class Debian(system_test.Distribution):
    name = "debian"
    common = "nvda-rdp-common"
    plugin = "nvda-rdp-freerdp-plugin"
    client = "nvda-rdp-client"
    server = "nvda-rdp-server"
    server_tools = ("openssl",)
    client_tools = ("xvfb", "xdotool")

    def package_glob(self, package: str) -> str:
        return f"{package}_*.deb"

    def upgrade(self, vm: distro_vm.VM, *packages: str) -> None:
        vm.run(f"{APT} update >/dev/null && {APT} full-upgrade >/dev/null")
        if packages:
            vm.run(f"{APT} install {' '.join(packages)} >/dev/null")
        vm.run("cat /etc/debian_version; uname -r; dpkg-query -W python3 libfreerdp3-3 || true")

    def install_files(self, vm: distro_vm.VM, packages: list[str]) -> None:
        vm.run(f"{APT} install {system_test.PACKAGES_DIRECTORY}/*.deb >/dev/null")

    def verify(self, vm: distro_vm.VM, packages: list[str]) -> None:
        vm.run(f"dpkg --verify {' '.join(packages)} && dpkg-query -W {' '.join(packages)}")

    def enable_login_screen_reader(self, vm: distro_vm.VM) -> None:
        # Debian's GDM reads its login screen settings from this file, which
        # its configuration step compiles into a dconf database.
        vm.run(
            "printf '\\n[org/gnome/desktop/a11y/applications]\\nscreen-reader-enabled=true\\n'"
            " | sudo tee -a /etc/gdm3/greeter.dconf-defaults >/dev/null"
            " && sudo dpkg-reconfigure gdm3 && cat /usr/share/dconf/profile/gdm"
        )

    def enable_display_manager(self, vm: distro_vm.VM) -> None:
        # gdm3's installation makes it the display manager.
        vm.run("systemctl cat display-manager.service | head -3")


DEBIAN = Debian()
PACKAGES = (DEBIAN.common, DEBIAN.plugin, DEBIAN.client, DEBIAN.server)


def build(repository: Path, log: distro_vm.Log) -> None:
    dist = repository / "dist" / "debian"
    image = distro_vm.latest_image(distro_vm.IMAGES["debian"], log)
    with distro_vm.booted(image, log, name="debian-build") as vm:
        DEBIAN.upgrade(vm, "build-essential", "dpkg-dev", "lintian", "locales")
        # The golden transcripts of the server's braille use these locales;
        # they are skipped where they are not installed.
        vm.run(
            "printf 'fr_FR.UTF-8 UTF-8\\nen_US.UTF-8 UTF-8\\n' | sudo tee -a /etc/locale.gen >/dev/null"
            " && sudo locale-gen"
        )
        vm.put_tree(distro_vm.source_files(repository), SOURCE)
        # The Debian files are kept in packaging/debian; the tools expect them
        # in debian/ at the top of the source.
        vm.run(f"cp -r {SOURCE}/packaging/debian {SOURCE}/debian")
        vm.run(f"cd {SOURCE} && {APT} build-dep ./ >/dev/null")
        vm.run(f"cd {SOURCE} && dpkg-buildpackage -us -uc 2>&1", timeout=3600)
        vm.log.line("lintian (tags are for review, not errors)")
        vm.run(f"cd {BUILD} && lintian --info --display-info --pedantic *.changes; true")
        for package in PACKAGES:
            vm.run(f"dpkg-deb --info {BUILD}/{DEBIAN.package_glob(package)}")
            vm.run(
                f"dpkg-deb --contents {BUILD}/{DEBIAN.package_glob(package)}"
                " | awk '{print $6, $7, $8}' | grep -v '/$'"
            )
        dist.mkdir(parents=True, exist_ok=True)
        for old in dist.glob("*.deb"):
            old.unlink()
        vm.get_files("build/*.deb", dist)
    names = sorted(path.name for path in dist.glob("*.deb"))
    missing = [p for p in PACKAGES if not any(n.startswith(f"{p}_") for n in names)]
    if missing:
        raise RuntimeError(f"packages not built: {missing}")
    log.line(f"built into {dist}: {' '.join(names)}")


if __name__ == "__main__":
    sys.exit(system_test.main(DEBIAN, build, __doc__.split("\n")[0]))
