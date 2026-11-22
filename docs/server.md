# NVDA RDP Linux: Server Guide

The server side lets a blind user on Windows, with NVDA and its RDAccess
add-on, work in a GNOME session of a Linux computer reached over RDP: Orca's
speech is heard through NVDA's synthesizer and Orca's braille is shown on
NVDA's braille display. Nothing is installed or changed on the Windows side,
and local sessions of the Linux computer keep their usual speech and braille.

The French translation of this guide is [server.fr.md](server.fr.md). The
reference page is `nvda-rdp-server(7)`.

## Status

The server comes from the study `draft-nvda-orca-rdp` (experiments 01 to 13,
commit `faae513`). There, from Windows with NVDA, RDAccess and `mstsc`, on a
NixOS VM with an unmodified GNOME Remote Desktop, the following were verified:

* GDM's remote login screen and the session that follows, both served;
* Orca's speech through NVDA, with interruption and say all, and Orca's voice
  changes (rate, pitch, volume, language) as NVDA's relative commands;
* Orca's braille line on NVDA's display, at its size;
* the display's panning, line and routing keys (by simulation), uncontracted
  braille typing and NVDA gestures from the display;
* abrupt closing of `mstsc` and reconnection;
* a local session of the same computer left unchanged.

In this repository, with the packages built here, the system tests check from
Linux to Linux, in VMs of NixOS, Arch Linux and Debian forky: the local login
screen keeps its speech, the remote login screen is served, both channels
negotiate RDAccess version 2, and Orca's speech reaches the client's Speech
Dispatcher. Manual tests from Windows with NVDA, RDAccess and `mstsc`,
braille included, passed with these packages on NixOS (the
`services.nvda-rdp-server` module, on the study's VM), Arch Linux and Debian
forky. Braille end to end and failure cases are not covered by the automated
tests.

## Requirements

* GNOME 49 or later: GDM, GNOME Remote Desktop and Orca. From GNOME 49 on,
  GDM runs the login screen of each remote connection as its own user, and
  Orca runs as the systemd user service `orca.service`. Tested with GNOME
  Remote Desktop 50.2 (study, NixOS), 50.1 (this repository's NixOS test) and
  51.0 (Arch, Debian forky).
* Python 3.13 or later with PyGObject, the `louis` module (liblouis) and the
  `speechd` module (Speech Dispatcher).
* systemd with logind.

Debian 13 (trixie) is not supported: its GNOME 48 runs the local and the
remote login screens under the same user and starts Orca from gnome-session,
so the server cannot tell them apart. Debian packages target forky.

## Architecture

```text
Windows: NVDA + RDAccess, mstsc
        |
        | RDP, dynamic virtual channels NVDA-SPEECH and NVDA-BRAILLE
        v
GNOME Remote Desktop (unmodified)
  + libnvda-rdp-grd-preload.so (LD_PRELOAD): each channel as a Unix socket
        |
        | 32-bit framed Unix sockets, one RDAccess channel each
        v
nvda-rdp-speechd.service                 nvda-rdp-brlapi.service
  Speech Dispatcher of the session         BrlAPI server of the session
  + output module nvda-rdp                 liblouis, Orca's table
        ^                                        ^
        | SPEECHD_ADDRESS                        | BRLAPI_HOST, BRLAPI_AUTH
        +-------------------- Orca --------------+
```

Orca is not modified and loads no extension. It uses its two usual
interfaces, Speech Dispatcher and BrlAPI, pointed at two servers of the
session. The machine's own Speech Dispatcher and BRLTTY are neither stopped
nor reconfigured.

### Components

* `libnvda-rdp-grd-preload.so` (`grd-preload/`), loaded into GNOME Remote
  Desktop's session daemon by a drop-in of
  `gnome-remote-desktop-handover.service`. In each RDP connection it opens
  `NVDA-SPEECH` and `NVDA-BRAILLE` and serves each one on a socket of
  `$XDG_RUNTIME_DIR/gnome-remote-desktop/` (`nvda-speech.sock`,
  `nvda-braille.sock`). It uses the same 32-bit frames as the client's FreeRDP
  plugin, one frame per channel message. It calls GNOME Remote Desktop's own
  WinPR functions, looked up at run time, and logs through WLog with the tag
  `nvda-rdp.grd-preload`.
* `nvda-rdp-server-session` and its unit, at the start of each graphical
  session, GDM's login screens included: from logind's `Remote` and `Class`
  properties, decide whether the session is served. For a remote session,
  write `$XDG_RUNTIME_DIR/nvda-rdp-server/orca.env` (Orca's environment) and
  the marker `remote`; otherwise change nothing.
* `nvda-rdp-speechd.service`, only in remote sessions: a Speech Dispatcher
  instance whose only output module is `nvda-rdp-speechd-module`
  (`nvda_rdp_linux/server/speechd_module.py`). The module turns each message
  into an RDAccess speech sequence with indexes, and NVDA's index reports into
  Speech Dispatcher's events, so that say all follows NVDA's pace. While NVDA
  is not attached, it passes the messages to the session's usual Speech
  Dispatcher, heard through RDP audio. Orca's default voice, read from Orca's
  D-Bus interface `org.gnome.Orca1.SpeechManager`, is the reference of the
  relative rate, pitch and volume commands.
* `nvda-rdp-brlapi.service`, only in remote sessions: a BrlAPI server
  (`nvda_rdp_linux/server/brlapi_server.py`) on 127.0.0.1, port 4101 plus the
  user's UID, with a fresh key at each start. It presents NVDA's display at
  its size, writes Orca's text into dots with liblouis and Orca's braille
  table, and turns the display's keys and gestures into BrlAPI commands for
  Orca. Braille typing goes through Mutter's RemoteDesktop D-Bus interface.
* A drop-in of `orca.service` pulls in the three units, orders Orca after them
  and reads `orca.env`.

The shared code (`nvda_rdp_linux/common/`) is the one the client uses: frames,
RDAccess wire format, speech and braille vocabulary. The server side plays the
RDAccess server role, the one NVDA plays on Windows.

### Files

| Path (Debian; Arch uses `/usr/lib/nvda-rdp-linux` for both directories) | Content |
| --- | --- |
| `/usr/lib/<multiarch>/nvda-rdp-linux/libnvda-rdp-grd-preload.so` | channel library |
| `/usr/libexec/nvda-rdp-linux/` | `nvda-rdp-server-session`, `nvda-rdp-brlapi-server`, `nvda-rdp-speechd-module` |
| `/usr/share/nvda-rdp-linux/speechd/speechd.conf` | the session's Speech Dispatcher configuration |
| `/usr/lib/systemd/user/` | the three units and the two drop-in directories |
| `$XDG_RUNTIME_DIR/nvda-rdp-server/` | state of the session: `remote`, `orca.env` |
| `$XDG_RUNTIME_DIR/nvda-rdp-brlapi.key` | BrlAPI key, mode 0600 |
| `$XDG_RUNTIME_DIR/nvda-rdp-speechd-module.log` | log of the speech module |

## Installation

### NixOS

The flake provides the module `nixosModules.server`:

```nix
imports = [ inputs.nvda-rdp-linux.nixosModules.server ];
services.nvda-rdp-server.enable = true;
# services.nvda-rdp-server.loginScreen = false; # serve users' sessions only
```

The package is built with the configuration's own nixpkgs. GDM, GNOME and
GNOME Remote Desktop are configured as usual (see below).

### Arch Linux

`nvda-rdp-server-git` is built by the PKGBUILD published on the AUR
(`packaging/arch/PKGBUILD`), with any AUR helper or with `makepkg -si`.

### Debian forky

Install the `.deb` files of a release:

```bash
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-server_*.deb
```

## Configuration

### GDM's remote login

GNOME Remote Desktop's system daemon must accept RDP connections, in GNOME
Settings (System, Remote Desktop, Remote Login), or as root:

```bash
tls=/var/lib/gnome-remote-desktop/tls
sudo install -d -o gnome-remote-desktop -g gnome-remote-desktop -m 700 "$tls"
sudo -u gnome-remote-desktop openssl req -new -newkey rsa:4096 -days 720 -nodes -x509 \
  -subj "/CN=$(hostname)" -out "$tls/tls.crt" -keyout "$tls/tls.key"
sudo grdctl --system rdp set-tls-key "$tls/tls.key"
sudo grdctl --system rdp set-tls-cert "$tls/tls.crt"
sudo grdctl --system rdp set-credentials USER   # asks for the password
sudo grdctl --system rdp enable
sudo grdctl --system status
```

These credentials are those of the RDP connection to the login screen, not
those of a user account; the user then logs in on GDM. On NixOS, `grdctl`'s
`enable` relies on `[Install]` sections, which NixOS ignores: enable
`services.gnome.gnome-remote-desktop` and write
`/etc/gnome-remote-desktop/grd.conf` as `nix/tests/server.nix` does. Open TCP
port 3389 in the firewall.

In the test VMs, GNOME Remote Desktop's system daemon never listened when it
started before GDM; a drop-in with `After=display-manager.service` fixes it.

### Orca on the login screen

Orca speaks on the login screen only if the screen reader is enabled there,
from the login screen's accessibility menu or in GDM's dconf settings, key
`screen-reader-enabled` of `org.gnome.desktop.a11y.applications`:

* NixOS:

  ```nix
  programs.dconf.profiles.gdm.databases = [
    { settings."org/gnome/desktop/a11y/applications".screen-reader-enabled = true; }
  ];
  ```

* Debian: append to `/etc/gdm3/greeter.dconf-defaults`, then run
  `sudo dpkg-reconfigure gdm3`:

  ```ini
  [org/gnome/desktop/a11y/applications]
  screen-reader-enabled=true
  ```

* Arch: GDM 51's profile `/usr/share/dconf/profile/gdm` has no system
  database. Create `/etc/dconf/profile/gdm`, which takes precedence, with the
  lines of the original and `system-db:gdm` after `user-db:user`; put the
  setting above in `/etc/dconf/db/gdm.d/00-screen-reader`; run
  `sudo dconf update`.

In a user's session, the screen reader is enabled as usual, in GNOME
Settings or with `Super+Alt+S`.

### Settings

| Variable | Unit | Effect |
| --- | --- | --- |
| `NVDA_RDP_LOGIN_SCREEN=no` | `nvda-rdp-server-session.service` | the remote login screen keeps its usual output; only users' sessions are served |
| `NVDA_RDP_PROSODY=0` | `nvda-rdp-speechd.service` | Orca's voice changes are not sent to NVDA |
| `NVDA_RDP_LOG_LEVEL=DEBUG` | `nvda-rdp-speechd.service` | the speech module also logs the spoken text |
| `WLOG_FILTER=nvda-rdp.grd-preload:DEBUG` | `gnome-remote-desktop-handover.service` | the channel library's lines appear in the journal |

Set them for every user with a drop-in under `/etc/systemd/user/`, for
example `/etc/systemd/user/nvda-rdp-server-session.service.d/login-screen.conf`:

```ini
[Service]
Environment=NVDA_RDP_LOGIN_SCREEN=no
```

## Use

On Windows: NVDA with RDAccess 2, and `mstsc` (or any RDP client RDAccess
supports) connected to the Linux computer, port 3389, with the credentials of
`grdctl`. The certificate is the self-signed one above; check its fingerprint
when `mstsc` shows it. GDM's login screen appears and Orca speaks through
NVDA; once the user has logged in, the session is served the same way.

* Speech, interruption (`Ctrl`) and say all follow NVDA's synthesizer. NVDA
  keeps its own voice; only Orca's differences from its default voice are
  applied, and a language change is heard only if NVDA's automatic language
  switching is on.
* Braille shows Orca's line on NVDA's display, with Orca's table. The
  display's panning, line and routing keys go to Orca, as do braille typing
  and the display's gestures.
* Ordinary keyboard and audio are RDP's own.
* If `mstsc` is closed, the session stays open; a new connection to the same
  account joins it.

## Diagnostics

```bash
journalctl -b -t nvda-rdp-server-session -t nvda-rdp-brlapi-server \
  -t gnome-remote-desktop-daemon
cat "$XDG_RUNTIME_DIR/nvda-rdp-speechd-module.log"
```

Healthy lines, in the journal:

```text
remote session: Orca uses the nvda-rdp BrlAPI server and Speech Dispatcher
NVDA-SPEECH: channel open
NVDA-BRAILLE: protocol version 2 negotiated
```

and in the speech module's log:

```text
NVDA-SPEECH: protocol version 2 negotiated
```

`local session: Orca keeps its usual braille and speech` is expected in local
sessions. The journal is read by syslog identifier (`-t`): journald may miss
the unit of a short process. The lines of the greeter users are in the
system journal; read them as root.

## Security

* The channel sockets are in directories of mode 0700, with mode 0600.
* The BrlAPI server listens on 127.0.0.1 only and accepts only clients that
  know the session's key, written at each start with mode 0600.
* The speech and braille are what Orca presents, typed passwords included:
  they are presumed confidential. The logs contain them only at `DEBUG`.
* Any program of the remote session can write to the NVDA channels, as with
  RDAccess on Windows. The client's NVDA is a trusted peer: its braille
  gestures act on the session.
* RDP itself must be configured safely: NLA and a certificate whose
  fingerprint the client checks.

## Limitations

* The channel library depends on internal details of GNOME Remote Desktop:
  one `WTSOpenServerA` and one `WTSCloseServer` per connection. A new version
  can break it without warning; a public GNOME Remote Desktop interface to
  open named channels would remove this dependency.
* Only GDM's remote login is served, not desktop sharing of a local session
  nor headless sessions.
* An account has one `orca.service`: with a local and a remote session at the
  same time, the first one opened decides.
* Orca's choice of synthesizer and voice, and its pitch range, have no effect
  on NVDA. The reference voice is read when NVDA attaches.
* A display size change costs a reconnection of about 2 seconds.
* Say all stops on a disconnection.

## Design notes from the study

These choices come from the study's experiments; its reports keep the
details.

* **No Orca extension.** The first experiments used an Orca extension to
  observe speech and braille. Orca's speech hook has no voice, language or
  interruption, say all cannot follow a remote synthesizer, and braille is
  only available before layout. Speech Dispatcher and BrlAPI give all of
  this, with no change to Orca.
* **An unmodified GNOME Remote Desktop.** GNOME Remote Desktop opens no
  arbitrary channel; a patch worked, then the preloaded library replaced it.
* **Servers of the session, not of the machine.** The machine's Speech
  Dispatcher and BRLTTY keep serving local sessions; Orca of a remote session
  is pointed elsewhere by its environment only.
* **Remote or local, from logind.** `Remote` and `Class` of the graphical
  session decide; GNOME 49's separate greeter users make the login screen
  servable.
* **Text to dots on Linux.** Orca sends text to BrlAPI; the server writes it
  with liblouis and Orca's table, so that NVDA shows what Orca would.
* **Relative voice commands.** NVDA keeps its own voice; Orca's changes
  (capitals, rate changes) are passed as differences from its default voice.
* **Braille typing through Mutter.** Keys are injected with Mutter's
  RemoteDesktop session, not by GNOME Remote Desktop.
