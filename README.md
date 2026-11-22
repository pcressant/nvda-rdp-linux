# NVDA RDP Linux

NVDA RDP Linux brings
[RDAccess](https://github.com/LeonarddeR/rdAccess), the NVDA add-on that sends
speech and braille over RDP dynamic virtual channels, to Linux, on both sides
of an RDP connection:

* **client**: a blind Linux user works in a Windows remote desktop with the
  remote NVDA screen reader, and hears its speech and reads its braille
  locally;
* **server**: a blind Windows user, with NVDA and RDAccess, works in a GNOME
  session of a Linux computer reached over RDP, and hears Orca through NVDA's
  synthesizer and reads Orca's braille on NVDA's display.

The French translation of this page is [README.fr.md](README.fr.md).

## Client

It has two parts:

* a small FreeRDP plugin (`nvda-rdp-dvc`) that only transports the
  `NVDA-SPEECH` and `NVDA-BRAILLE` channels to local Unix sockets;
* a Python client (`nvda-rdp-client`) that implements the RDAccess protocol
  and renders it with Speech Dispatcher, PipeWire and BRLTTY.

Both `xfreerdp` and Remmina can be used, as long as they use a FreeRDP that
contains the plugin.

### Features

* remote NVDA speech through Speech Dispatcher, with indexes (say all),
  character mode, language switching, pitch/rate/volume changes and pauses;
* NVDA beeps and sounds played locally (from a copy of NVDA's `waves`);
* remote braille on the local BRLTTY display while the RDP window has focus,
  handed back to Orca as soon as it loses focus; the BrlAPI priority (80 by
  default, `--brlapi-priority`) stays above Orca's, flat review included;
* remote speech stopped when the RDP window loses focus, so that it does not
  talk over Orca (`--keep-speech-on-blur` keeps it going);
* braille display keys and routing keys sent back to the remote NVDA, with
  NVDA's standard BRLTTY gesture map; braille keyboard input;
* optional: remote control of the local voice settings from NVDA, Caps Lock
  synchronization, connection notifications;
* one user service for both channels, which keeps working if Speech
  Dispatcher or BRLTTY is unavailable and reconnects to BRLTTY on its own;
  the plugin reconnects to the service when it restarts during a session.

Logs stay short at the default `INFO` level. Spoken text, braille cells and
gestures are only logged with `--log-level DEBUG`; the plugin dumps every DVC
block only when `NVDA_RDP_DVC_DEBUG` is set in the RDP client's environment.

The voice and message language follows the locale (`LC_ALL`, `LC_MESSAGES`,
`LANG`) unless `--language` is given.

### Status

Experimental. Real RDP sessions against RDAccess 2.0.4 (NVDA 2026.2) have
confirmed speech, braille output, braille display keys and routing, NVDA
sounds, Remmina, and the single client service; the packaged client was also
checked in real sessions on Arch Linux and Debian forky. Driver settings management,
Caps Lock synchronization, braille keyboard input and `pause_speech` are
implemented and unit-tested, but not yet confirmed in a real session.
RDAccess 1.x is not supported.

### Use

Enable the client and, in your RDP client, add the dynamic virtual channel
`nvda-rdp-dvc` (`/dvc:nvda-rdp-dvc` for `xfreerdp3`, "Dynamic virtual
channel" in Remmina's advanced settings):

```bash
systemctl --user enable --now nvda-rdp-client.service
```

See [run.md](run.md) for architecture, configuration, testing and
diagnostics, and `man nvda-rdp-client`.

## Server

Orca is not modified. A library loaded into an unmodified GNOME Remote
Desktop presents the two NVDA channels as Unix sockets; in remote sessions
only, a Speech Dispatcher instance and a BrlAPI server of the session send
Orca's speech and braille to NVDA, and the display's keys back to Orca. GDM's
remote login screen is served too. Local sessions keep their usual speech and
braille, and nothing changes on the Windows side.

It needs GNOME 49 or later (GDM, GNOME Remote Desktop, Orca).

### Status

Experimental. It comes from the study `draft-nvda-orca-rdp`, where, from
Windows with NVDA, RDAccess and `mstsc`, speech with say all and Orca's voice
changes, braille output, display keys, braille typing, reconnection and the
remote login screen were verified on NixOS. In this repository, the system
tests in VMs of NixOS, Arch Linux and Debian forky check, from Linux to Linux,
that Orca's speech on the remote login screen reaches the client and that the
local login screen is unchanged. Manual tests from Windows with NVDA,
RDAccess and `mstsc`, braille included, passed with this repository's
packages on NixOS (`services.nvda-rdp-server`), Arch Linux and Debian forky.

See [docs/server.md](docs/server.md) and `man nvda-rdp-server`.

## Installation

| | Client | Server |
| --- | --- | --- |
| NixOS | `overlays.default`: `nvda-rdp-client`, `freerdp-with-nvda-rdp`, `remmina-with-nvda-rdp` | `nixosModules.server`: `services.nvda-rdp-server.enable` |
| Arch Linux (AUR) | `nvda-rdp-client-git` | `nvda-rdp-server-git` |
| Debian forky | `nvda-rdp-common`, `nvda-rdp-freerdp-plugin`, `nvda-rdp-client` | `nvda-rdp-common`, `nvda-rdp-server` |

The AUR has the PKGBUILD only; the Debian packages are `.deb` files of the
releases. Debian 13 is not supported. Building, testing in VMs, releasing,
and other distributions: [docs/packaging.md](docs/packaging.md).

## Credits

This project was inspired by and developed through studying the work of
[Leonard de Ruijter on RDAccess](https://github.com/LeonarddeR/rdAccess).
RDAccess provides the NVDA-side remote accessibility functionality and protocol
behavior that made this project possible. Leonard de Ruijter did not
participate in this project and is not responsible for it.

## Development note

This project started as a personal experiment to solve an accessibility need.
I am a system administrator rather than a professional software developer, and
AI-assisted development tools were used during development. I have nevertheless
tried to understand, test, and document the implementation carefully and to
keep the design and code clean and maintainable. Users should still treat the
project as experimental software.

## License

This project is licensed under the GNU General Public License version 2 or
later (`GPL-2.0-or-later`). See [LICENSE](LICENSE).
