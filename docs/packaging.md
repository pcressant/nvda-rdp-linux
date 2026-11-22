# NVDA RDP Linux: Packaging and Tests

How the packages are built and tested for NixOS, Arch Linux and Debian, and
how they are released. The French translation of this guide is
[packaging.fr.md](packaging.fr.md).

## The four packages

One source gives four packages, with the same names on every distribution
(the Arch ones end in `-git`):

| Package | Content | Architecture |
| --- | --- | --- |
| `nvda-rdp-common` | `nvda_rdp_linux/common/`: frames, RDAccess wire format, speech and braille vocabulary; no I/O, no dependencies | any |
| `nvda-rdp-freerdp-plugin` | `libnvda-rdp-dvc-client.so`, in the system FreeRDP's plugin directory | machine |
| `nvda-rdp-client` | `nvda-rdp-client`, its user unit, `nvda-rdp-client(1)` | any |
| `nvda-rdp-server` | channel library for GNOME Remote Desktop, the two servers, the units and drop-ins, `nvda-rdp-server(7)` | machine |

The client depends on the common package and the plugin; the server depends
on the common package only. The plugin is linked to the FreeRDP 3 release it
was built against and must be rebuilt when the distribution upgrades FreeRDP.

The man pages are under `man/`, their French translations under `man/fr/`.
Files shared by the server packages of every distribution are under
`packaging/server/` (see its `README.md`): `install.sh` installs them with the
paths of each distribution.

## Nix

| Output | Content |
| --- | --- |
| `packages.<system>.client` | `nvda-rdp-client` with its runtime dependencies, its user unit and man page |
| `packages.<system>.freerdp-with-nvda-rdp` | FreeRDP containing the plugin |
| `packages.<system>.remmina-with-nvda-rdp` | an unmodified Remmina built against it |
| `packages.<system>.server`, `grd-preload` | the server and its channel library |
| `overlays.default` | the same packages, built with the consumer's nixpkgs |
| `overlays.brltty` | optional BRLTTY override with Polkit |
| `nixosModules.server` | `services.nvda-rdp-server` |
| `checks.<system>.server` | system test in NixOS VMs |
| `apps.x86_64-linux.{build,test}-{arch,debian}` | builds and tests in VMs of the distributions |

`nix flake check` builds the packages and runs the NixOS system test. A flake
only sees files known to Git: add new files (`git add -N` is enough) before
building.

## Builds and tests in VMs of the distributions

```bash
nix run .#build-arch     # makepkg in a fresh Arch VM, writes dist/arch/
nix run .#test-arch      # installs dist/arch/ in two Arch VMs, system test
nix run .#build-debian   # dpkg-buildpackage in a fresh Debian forky VM, writes dist/debian/
nix run .#test-debian    # installs dist/debian/ in two Debian VMs, system test
```

They are apps, not derivations, because pacman and apt need the network.
They need KVM. They run from the repository's working tree as it is,
uncommitted changes included.

* Images: the latest official cloud images, not pinned: Arch's `cloudimg`,
  Debian forky's daily `generic` image (`genericcloud` lacks the DRM drivers
  GDM needs). Each image is checked against the published SHA-256 (Arch) or
  SHA-512 (Debian) digest and cached in `~/.cache/nvda-rdp-linux/images/`.
  Each run starts from a fresh copy and upgrades it.
* Network: SSH is forwarded on 127.0.0.1 only. The two VMs of a test share a
  private network over a QEMU socket on 127.0.0.1 (192.168.77.1 and .2). A
  throwaway SSH key and cloud-init seed are created for each run.
* Build: the build VM runs the whole test suite (`check()`, or
  `dh_auto_test`), then `namcap` or `lintian --pedantic`, whose tags are for
  review, not errors. The Arch build also checks that `.SRCINFO` matches the
  PKGBUILD and writes the generated one to `dist/arch/SRCINFO`.
* System test (`nix/distro-vm/system_test.py`, the same scenario as
  `nix/tests/server.nix`): a server VM with GDM, GNOME Remote Desktop's remote
  login, Orca on the login screen and `nvda-rdp-server`; a client VM with
  `xfreerdp3`, the plugin and `nvda-rdp-client`, whose Speech Dispatcher
  records what it is asked to speak. The client connects to the remote login
  screen. Checks: the local login screen keeps its usual speech; the remote
  login screen uses nvda-rdp; the channel library is loaded; both channels
  negotiate RDAccess version 2; Orca's speech reaches the client.
* Logs: `logs/<distribution>-<command>-<date>.log`, plus each VM's console,
  as text.

Braille end to end, failure cases and a real Windows client are not covered
by these tests.

### Locales and skipped tests

The golden transcripts of the server's braille use `fr_FR.UTF-8` (Orca's
table follows the locale). They are skipped, not failed, where that locale is
not installed; the build VMs generate it. Without the locale, the BrlAPI
server falls back to the C locale with a warning, as Orca does.

## Arch Linux: AUR

Only `packaging/arch/PKGBUILD` and `packaging/arch/.SRCINFO` are published on
the AUR; users build the packages themselves. `nix run .#build-arch` only
validates the PKGBUILD.

The PKGBUILD builds the default branch of the GitHub repository
(`git+$url.git`). To release:

1. push the changes to the default branch;
2. if the PKGBUILD changed: run `nix run .#build-arch`, which builds the
   working tree with the PKGBUILD's source replaced by a local copy, and copy
   `dist/arch/SRCINFO` to `packaging/arch/.SRCINFO` when the build reports it
   out of date;
3. copy `PKGBUILD` and `.SRCINFO` to the AUR repository
   `nvda-rdp-linux-git` and push it.

`pkgver` is computed from Git: from the latest tag (`vX.Y.Z` gives
`X.Y.Z.rN.gHASH`), or, while the repository has no tag, `0.3.0.rN.gHASH`; bump
that fallback and `pkgver=` together. The old single package
`nvda-rdp-linux-git` is replaced by `nvda-rdp-freerdp-plugin-git` and
`nvda-rdp-client-git`.

On Arch, the Python BrlAPI module is in `python-brltty` and `pw-play` in
`pipewire-audio`. Arch's example `/etc/krb5.conf` makes NLA connections time
out; see "Arch Linux: connection timeout with NLA" in
[run.md](../run.md).

## Debian: .deb files

The Debian files are in `packaging/debian/` and are copied to `debian/` at
build time. The source package is native (`3.0 (native)`), built with
debhelper 14 and dh-python; the Python files are installed by the `.install`
files into `/usr/lib/python3/dist-packages`.

The packages are built and tested on Debian forky (testing), amd64; sid is
likely to work but is not tested. Debian 13 is not supported: its GNOME 48
cannot host the server (see [server.md](server.md)).

To release:

1. commit the changes; build from a clean working tree, since the build takes
   uncommitted changes too;
2. add an entry to `packaging/debian/changelog` with the new version (`dch`
   or by hand);
3. run `nix run .#build-debian`, then `nix run .#test-debian`;
4. publish the four `.deb` files of `dist/debian/`. The `-dbgsym` packages
   are debug symbols and need not be published.

Install them with:

```bash
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-freerdp-plugin_*.deb \
  ./nvda-rdp-client_*.deb    # client
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-server_*.deb    # server
```

## Other distributions

* plugin: `cmake -S freerdp-plugin -B build -DCMAKE_INSTALL_PREFIX=/usr`
  builds against the system FreeRDP 3 and installs into the plugin directory
  exported by FreeRDP's CMake package (`FreeRDP_RELATIVE_PLUGIN_DIR`, for
  example `lib/freerdp3`); `-DNVDA_RDP_PLUGIN_DIR=...` overrides it.
* channel library: `cmake -S grd-preload -B build-grd-preload` against the
  WinPR 3 headers; it links no WinPR.
* Python: install `nvda_rdp_linux/` (common part, plus `client/` with
  `__main__.py` and `app.py`, or `server/`) into the system's site packages;
  `pyproject.toml` also builds a wheel of the whole package, with the
  `nvda-rdp-client` command (Python 3.13 or later). Runtime dependencies come
  from system packages: `speechd`, `brlapi` (BRLTTY), `pyatspi` with the
  AT-SPI typelib, PyGObject and `pw-play` for the client; PyGObject, `louis`
  (liblouis) and `speechd` for the server.
* client: `packaging/client/nvda-rdp-client.in` with `@PYTHON@` replaced, and
  `packaging/systemd/nvda-rdp-client.service` into
  `/usr/lib/systemd/user/`. Users enable it themselves.
* server: `packaging/server/install.sh` with the distribution's paths.
