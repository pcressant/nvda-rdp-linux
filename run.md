# NVDA RDP Linux: Client Guide

This guide explains the client's architecture, and how to build, test, use
and diagnose it. The server side, Orca over GNOME Remote Desktop for an NVDA
user on Windows, has its own guide, [docs/server.md](docs/server.md);
packaging and the tests in VMs are in [docs/packaging.md](docs/packaging.md).
The French translation of this guide is [run.fr.md](run.fr.md); the reference
page of the command is `nvda-rdp-client(1)`.

The client implements the client side of RDAccess 2.0.5 (released
2026-10-15). Validation history:

* the protocol captures in `tests/fixtures/` come from RDAccess 2.0.0, commit
  `2da84e8496519cf23614c0cfb3ace61ee0f9cd95`;
* real sessions against RDAccess 2.0.4 with NVDA 2026.2 confirmed speech,
  braille output, braille display keys and routing, NVDA sounds, Remmina and
  the single client service;
* driver settings management, Caps Lock synchronization, braille keyboard input
  and `pause_speech` are covered by unit tests only.

## Overview

The client is split into two parts:

```text
Windows NVDA/RDAccess
        |
        | NVDA-SPEECH and NVDA-BRAILLE DVCs
        v
FreeRDP + C plugin
        |
        | framed Unix socket
        v
Python client (one process, one thread per channel)
        |
        +--> persistent SSIP connection --> Speech Dispatcher / eSpeak NG
        |
        +--> pw-play --> NVDA tones and sounds
        |
        +--> BrlAPI --> BRLTTY (cells out, display keys back to NVDA)
```

The C plugin handles FreeRDP, the DVCs, and IPC transport. The Python client process listens on one socket per DVC, reconstructs JSON Lines messages, implements the client side of RDAccess, and communicates with Speech Dispatcher, PipeWire and BRLTTY.

The plugin contains no RDAccess logic and generates no application-level bytes. It transports payloads in both directions and produces diagnostic dumps. The Python client sends XON on each connection, then handles the `protocolVersion=2` handshake and channel-specific responses.

Main files:

* `flake.nix`: Nix environment and FreeRDP variant containing the plugin;
* `freerdp-plugin/nvda_rdp_dvc.c`: DVC callbacks and Unix socket bridge;
* `freerdp-plugin/CMakeLists.txt`: FreeRDP module build configuration;
* `nvda_rdp_linux/app.py`: client entry point (`python -m nvda_rdp_linux`), running both
  channels in one process and containing channel failures;
* `nvda_rdp_linux/common/`: code shared with the server side, without I/O:
  * `framing.py`: IPC frame reconstruction (32-bit length);
  * `rdaccess.py`: RDAccess v2 wire format (legacy attribute frames, JSON Lines, stream
    decoding);
  * `speech.py`: `NVDA-SPEECH` vocabulary and prosody commands;
  * `braille.py`: `NVDA-BRAILLE` vocabulary, cells and gestures;
  * `settings.py`: JSON encoding of NVDA driver settings;
* `nvda_rdp_linux/client/ipc.py`: Unix socket of the FreeRDP plugin;
* `nvda_rdp_linux/client/session.py`: client handshake and generic attributes
  (`supportedSettings`, versions, `capsLockToggle`);
* `nvda_rdp_linux/client/protocol.py`: `NVDA-SPEECH` messages and speech sequence
  rendering (indexes, character mode, language, prosody, breaks);
* `nvda_rdp_linux/client/speech.py`: persistent Speech Dispatcher connection and
  voice settings;
* `nvda_rdp_linux/client/audio.py`: NVDA tones and wave files through `pw-play`;
* `nvda_rdp_linux/client/braille.py`: `NVDA-BRAILLE` messages and gestures;
* `nvda_rdp_linux/client/brltty.py`: BrlAPI output, key input and AT-SPI focus;
* `nvda_rdp_linux/client/capslock.py`: optional Caps Lock synchronization;
* `nvda_rdp_linux/client/notify.py`: connection notifications;
* `tests/common/`, `tests/client/`: unit tests; `tests/fixtures/`: fixtures captured from
  real exchanges; `tests/golden/`: black-box non-regression transcripts;
* `scripts/run-rdaccess-speech-session`: recommended complete launcher;
* `man/`: man pages, with their French translations in `man/fr/`;
* `packaging/`: files of the distribution packages (see
  [docs/packaging.md](docs/packaging.md)).

The server side lives in `grd-preload/`, `nvda_rdp_linux/server/`,
`packaging/server/`, `nix/server.nix`, `nix/modules/` and `tests/server/`; see
[docs/server.md](docs/server.md).

## Entering the Development Environment

From the repository root:

```bash
nix develop
```

The shell includes FreeRDP 3.30.0 with the plugin installed, GCC, CMake, Ninja, pkg-config, Python, pytest, Ruff, clang-format, clangd, Speech Dispatcher 0.12.1, BRLTTY, PipeWire's `pw-play`, `xdotool` and `xset`.

After modifying the plugin, running `nix develop` again may rebuild the local FreeRDP variant. The first invocation can therefore take longer.

The `build/` and `build-*` directories are local and ignored by Git. They can be recreated at any time without being included in a commit.

## Building the C Plugin Manually

Inside `nix develop`:

```bash
cmake -S freerdp-plugin -B build/dev -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/dev --target nvda-rdp-dvc
```

The resulting file is:

```text
build/dev/libnvda-rdp-dvc-client.so
```

Check dynamic libraries and symbols with:

```bash
ldd -r build/dev/libnvda-rdp-dvc-client.so
```

The output should contain neither `not found` nor `undefined symbol`.

Note: FreeRDP 3.30 from the flake uses absolute paths for its add-ins. The file built under `build/dev` is useful for development checks, but the `xfreerdp` provided by the shell loads the copy installed in the Nix `freerdp-with-nvda-rdp` variant. Exit and re-enter `nix develop` to rebuild that variant after modifying the C code.

## Running Tests Without RDP

Inside `nix develop`:

```bash
pytest -q
ruff check nvda_rdp_linux tests nix/distro-vm
ruff format --check nvda_rdp_linux tests nix/distro-vm
clang-format --dry-run --Werror freerdp-plugin/nvda_rdp_dvc.c grd-preload/nvda_rdp_grd_preload.c
```

`pytest` runs the client, common and server tests. The golden tests replay
black-box scenarios against the client and server processes and compare
their output with recorded transcripts (`tests/golden/README.md`).

Run the complete flake check, which also runs the NixOS system test in VMs
(KVM), from the repository root:

```bash
nix flake check
```

The tests cover fragmented and concatenated IPC framing, JSON Lines, fragmented UTF-8, real-world fixtures, `speak` and all supported speech commands, `cancel`, `pause_speech`, tones and waves (including path traversal refusal), driver settings, braille gestures and gesture map, Caps Lock synchronization, notifications, reconnections, and outgoing handshake responses. Messages that were not captured from a real session are marked as synthetic in the tests.

## Recommended Full Session

From the repository root:

```bash
export RDP_HOST=windows-remote
export RDP_USER=username
export RDP_DOMAIN=workgroup  # Optional.
./scripts/run-rdaccess-speech-session
```

The script:

1. enters `nix develop`;
2. starts the Python client (`python -m nvda_rdp_linux`), unless the
   `nvda-rdp-client` user service is already running;
3. waits for both Unix sockets to be created;
4. launches FreeRDP and connects to the remote;
5. prompts for the password interactively;
6. enables the `NVDA-SPEECH` and `NVDA-BRAILLE` listeners, Windows audio,
   fullscreen mode, and the French keyboard layout;
7. writes traces directly under `logs/` instead of copying them to the terminal;
8. cleanly stops the client it started when the session ends.

The password is never included in the command line or logs.

Optional environment variables:

```text
NVDA_WAVES_DIR             copy of NVDA's waves directory (sounds); default:
                           nvda-waves/ at the repository root if present
RDACCESS_CLIENT_OPTIONS    extra options for python -m nvda_rdp_linux
```

Example:

```bash
export NVDA_WAVES_DIR=~/nvda-waves
export RDACCESS_CLIENT_OPTIONS="--settings-management --sync-caps-lock"
```

Important FreeRDP settings used by the script:

```text
/v:windows-remote
/u:username
/d:workgroup
/dvc:nvda-rdp-dvc
/sound:sys:pulse
/kbd:layout:0x0000040C
+f
+grab-keyboard
```

`0x0000040C` is FreeRDP's "French" keyboard layout.

## Persistent Client (systemd user service)

The flake output `client` provides `nvda-rdp-client` (the Python client with
its runtime dependencies) and a systemd user unit that keeps it running for the
whole graphical session. Any FreeRDP client, `xfreerdp` or Remmina, then finds
its sockets when the DVCs open.

```bash
nix profile install .#client
systemctl --user link ~/.nix-profile/lib/systemd/user/nvda-rdp-client.service
systemctl --user enable --now nvda-rdp-client.service
```

On NixOS, the flake's `overlays.default` adds `nvda-rdp-client`,
`freerdp-with-nvda-rdp` and `remmina-with-nvda-rdp` to the configuration's own
`pkgs` (its nixpkgs and overlays). `overlays.brltty` is the optional BRLTTY
override with Polkit. NixOS ignores the unit's `[Install]` section, so the
target is set in the configuration:

```nix
nixpkgs.overlays = [ inputs.nvda-rdp-linux.overlays.default ];
environment.systemPackages = [
  pkgs.nvda-rdp-client
  pkgs.remmina-with-nvda-rdp # replaces remmina
  pkgs.freerdp-with-nvda-rdp # replaces freerdp (xfreerdp)
];
systemd.packages = [ pkgs.nvda-rdp-client ];
systemd.user.services.nvda-rdp-client.wantedBy = [ "graphical-session.target" ];
```

Optional settings go in `~/.config/nvda-rdp-linux/environment`:

```text
NVDA_WAVES_DIR=/path/to/nvda-waves
NVDA_RDP_OPTIONS=--settings-management --sync-caps-lock
```

Restart the service after changing it. Log:

```bash
journalctl --user -u nvda-rdp-client -f
```

When the service is active, `scripts/run-rdaccess-speech-session` uses it
instead of starting its own client.

## Failure Isolation

Both channels run in one process, so a failure on one must not stop the other:

* an error while handling one plugin connection is logged with its traceback
  and the channel accepts the next connection;
* if Speech Dispatcher cannot be reached at startup, the speech channel is
  disabled and braille keeps working;
* BrlAPI is connected in the background and retried every 5 seconds when
  BRLTTY is unavailable, refuses authorization, or is restarted; meanwhile
  braille cells are only logged and the size given by `--cells`/`--rows` is
  advertised, then the real size is pushed to the remote NVDA once known.

`--no-speech` or `--no-braille` disable a channel entirely.

## Distribution Packages

Arch Linux and Debian have four packages: `nvda-rdp-common`,
`nvda-rdp-freerdp-plugin`, `nvda-rdp-client` and `nvda-rdp-server` (`-git`
on Arch). The client needs the first three:

* Arch Linux: `nvda-rdp-client-git` from the AUR, built from
  `packaging/arch/PKGBUILD`; it replaces the former single package
  `nvda-rdp-linux-git`. The Python BrlAPI module is in `python-brltty` and
  `pw-play` in `pipewire-audio`;
* Debian forky (testing), amd64: the `.deb` files of a release:

  ```bash
  sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-freerdp-plugin_*.deb \
    ./nvda-rdp-client_*.deb
  ```

The plugin is installed into the system FreeRDP's plugin directory, so the
distribution's `xfreerdp3` and Remmina find it without changes. It is built
against one FreeRDP 3 release and must be rebuilt when the distribution
upgrades FreeRDP. Each user then enables the service:

```bash
systemctl --user enable --now nvda-rdp-client.service
```

How the packages are built, tested in VMs and released, and how to install
on other distributions: [docs/packaging.md](docs/packaging.md).

## Using Remmina

Remmina is used unmodified. FreeRDP only loads channel plugins from its own
install directory, so Remmina must be built against the FreeRDP variant that
contains the plugin; the flake provides it:

```bash
nix run .#remmina-with-nvda-rdp
```

or install it with `nix profile install .#remmina-with-nvda-rdp`. On Arch and
Debian, the plugin package installs `libnvda-rdp-dvc-client.so` into the
plugin directory of the system FreeRDP (`/usr/lib/freerdp3/`,
`/usr/lib/x86_64-linux-gnu/freerdp3/`), which the distribution's Remmina uses
as well. The VM tests check this with `xfreerdp3`; the distributions' Remmina
has not been tested yet.

In the RDP connection profile, Advanced tab, set "Dynamic virtual channel" to:

```text
nvda-rdp-dvc
```

This is the same as `/dvc:nvda-rdp-dvc` for `xfreerdp` (key `dvc` in the
`.remmina` file). Audio, keyboard layout and fullscreen are configured in the
Remmina profile. The client must be running before connecting, preferably as
the user service above.

Remote braille output follows the focus of windows whose application or window
name contains `freerdp` or `remmina` (see `--focus-application`). Remmina's
main connection list window also matches; check with `--log-level DEBUG`
what AT-SPI reports for Remmina's windows.

## Manual Launch for Understanding the Components

The automatic launcher is recommended for normal use. To observe each layer separately, open two terminals.

First terminal:

```bash
nix develop
python -m nvda_rdp_linux --log-level DEBUG
```

The client should report that it is listening on:

```text
$XDG_RUNTIME_DIR/nvda-rdp-linux/speech.sock
$XDG_RUNTIME_DIR/nvda-rdp-linux/braille.sock
```

It connects to the BRLTTY daemon through BrlAPI without an explicit `brlapi.key`
path; authorization is delegated to the Polkit configuration already present on
the machine. With `--braille-capture`, BrlAPI is not used and braille cells are
only logged; the number of virtual cells can be changed with `--cells`.

Each log line names its thread: `speech`, `braille`, `brlapi-connection`,
`brlapi-keys`, or `MainThread`.

Second terminal:

```bash
nix develop
xfreerdp \
  /v:windows-remote \
  /u:username \
  /d:workgroup \
  /dvc:nvda-rdp-dvc \
  /from-stdin:force \
  /cert:tofu \
  /sound:sys:pulse \
  /kbd:layout:0x0000040C \
  +f \
  +grab-keyboard \
  /log-level:INFO \
  /log-filters:com.freerdp.channels.drdynvc.client:TRACE
```

The Python client must be started before FreeRDP. If a socket is unavailable,
the plugin continues to display the corresponding DVC blocks for diagnostic
purposes, but no client-side handshake will be sent back on that channel.

## Configuring the Voice

Default values:

```text
module: espeak-ng
language: from the locale (LC_ALL, then LC_MESSAGES, then LANG), else en
Orca rate: 75
```

The language is reported to the remote NVDA as the language of the local
synthesizer, as RDAccess does with `synth.language`; the remote NVDA uses it
for automatic language switching, symbol processing and character
descriptions. It also selects the language of connection notifications.
Speech Dispatcher's own default language is not used because screen readers
such as Orca set their voice per connection and the global default often stays
`en-US`. Orca itself falls back to the locale when no voice language is
configured.

To experiment manually:

```bash
python -m nvda_rdp_linux --language fr_FR --rate 70 --output-module espeak-ng
```

The rate uses Orca's 0–100 scale. The SSIP conversion is:

```text
SSIP = 2 × Orca rate − 100
```

Therefore, Orca 70 maps to SSIP +40, and Orca 75 maps to SSIP +50. Pitch and
volume use the same conversion.

Other options:

```text
--pitch-change N        pitch offset added to remote speech, like RDAccess's
                        "incoming speech pitch change" (default 10, 0 disables)
--enable-pause          honour pause_speech (off by default: sd_espeak-ng
                        repeated the sentence on resume)
--settings-management   let the remote NVDA read and change the local voice,
                        rate, pitch and volume (RDAccess "driver settings
                        management"; off by default, as upstream)
--nvda-waves-dir DIR    play NVDA sounds such as waves\error.wav from DIR
--audio-player CMD      player fed with a WAV file on stdin (default "pw-play -")
--notify MODE           none, message, sound or both (default message)
```

## NVDA Sounds

When RDAccess's remote synthesizer is active, the remote NVDA does not play its
own beeps and sounds: it sends them as `tone` and `wave` messages. Tones are
generated locally. Wave files are referenced by their path inside NVDA's
program directory (for example `waves\error.wav`), so the Linux side needs a
copy of NVDA's `waves` directory, given with `--nvda-waves-dir` or
`NVDA_WAVES_DIR`. The launcher uses `nvda-waves/` at the repository root when
it exists (ignored by Git). Only files inside that directory are played; sounds from
other add-ons are refused and logged.

## Braille Options

```text
--braille-capture        only log braille cells, do not use BrlAPI
--cells N, --rows N      size advertised until BrlAPI answers (default 40x1)
--no-braille-input       do not forward display keys to the remote NVDA
--sync-caps-lock         align the local Caps Lock with the remote NVDA when
                         the RDP client loses focus (X11 tools; uncertain on
                         Wayland)
--focus-application NAME RDP client window/application name, repeatable
                         (default: freerdp and remmina)
```

Braille connection notifications are only given while a braille display is
connected through BrlAPI.

Display keys are read as BRLTTY commands while FreeRDP has
focus and sent to the remote NVDA exactly like NVDA's own `brltty` driver
does (`br(brltty):fwinlt`, `br(brltty):route`, ...). The remote NVDA receives
NVDA's standard `brltty` gesture map, so scrolling, line navigation and
routing work without configuration. Braille keyboard dots (`passdots`) are sent
as braille input and translated by the remote NVDA's input table.

## Reading the Logs

The full launcher produces two main log files:

```text
logs/rdaccess-speech-session.log
logs/nvda-rdp-client.log
```

The first contains FreeRDP, drdynvc, and C plugin messages. The second contains
the client: speech protocol, Speech Dispatcher, braille negotiation, cells and
gestures. With the user service, the client log is in the journal instead
(`journalctl --user -u nvda-rdp-client`).

Follow both logs during a session:

```bash
tail -F logs/rdaccess-speech-session.log logs/nvda-rdp-client.log
```

Quickly check the handshake and IPC:

```bash
rg -n 'NVDA-SPEECH|connected IPC|delegated client|forwarded .* IPC|failed' \
  logs/rdaccess-speech-session.log
```

Expected healthy output:

```text
connected IPC socket .../nvda-rdp-linux/speech.sock
received 22 bytes
forwarded 22 IPC bytes to DVC
received 69 bytes
received 55 bytes
forwarded 68 IPC bytes to DVC
```

Check the protocol and speech path:

```bash
rg -n 'sent legacy speech|sent speech attribute|server speech|submitting speech|accepted speak|accepted cancel|playing|unsupported' \
  logs/nvda-rdp-client.log
```

Typical healthy output:

```text
sent legacy speech protocolVersion=2
sent speech attribute language='fr_FR'
server speech rdAccessVersion='2.0.5'
submitting speech text='Bureau  fenêtre'
Speech Dispatcher accepted speak character_mode=False text='Bureau  fenêtre'
Speech Dispatcher accepted cancel
```

Check the braille channel:

```bash
rg -n '\(braille\)|\(brlapi' logs/nvda-rdp-client.log
```

A healthy session reports `numRows=1`, `numCols=40`, confirms the JSON
`NVDA-BRAILLE` channel, and then displays each 40-cell array in hexadecimal.

An `accepted speak` line proves that Speech Dispatcher accepted the text. Actual
audio output must still be confirmed by listening to it.

Frozen captures from successful tests use different filenames so they are not
overwritten, including:

```text
logs/rdaccess-client-python-handshake.log
logs/rdaccess-speech-python-handshake.log
logs/rdaccess-client-stable-final.log
logs/rdaccess-speech-stable-final.log
```

## Inspecting the Fixtures

Minimal captures are stored under:

```text
tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/
```

Each `.hex` file contains bytes actually observed or produced through the exact
path being tested. To display a fixture as raw data:

```bash
xxd -r -p tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/first-speak-v2-jsonl.hex
```

To count its bytes:

```bash
xxd -r -p tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/first-speak-v2-jsonl.hex | wc -c
```

## Keyboard and FreeRDP Window

* `Ctrl+Alt+Enter`: toggle fullscreen mode;
* right `Ctrl`: release FreeRDP keyboard capture;
* `Super+M`: send Windows+M while the keyboard is captured;
* pressing `Ctrl` during an NVDA announcement causes a `cancel` message and
  interrupts local speech.

If the keyboard layout is still incorrect, first check that the script contains:

```text
/kbd:layout:0x0000040C
```

List keyboard layouts known to FreeRDP:

```bash
nix develop --command xfreerdp /list:kbd
```

## Where to Make Changes

To experiment without mixing responsibilities:

* DVC transport or socket connection: `freerdp-plugin/nvda_rdp_dvc.c`;
* IPC framing: `nvda_rdp_linux/common/framing.py` and `nvda_rdp_linux/client/ipc.py`;
* wire format shared with the server: `nvda_rdp_linux/common/`;
* handshake and generic attributes: `nvda_rdp_linux/client/session.py`;
* new speech messages or commands: `nvda_rdp_linux/common/speech.py` and
  `nvda_rdp_linux/client/protocol.py`;
* braille messages and gestures: `nvda_rdp_linux/common/braille.py` and
  `nvda_rdp_linux/client/braille.py`;
* braille display keys: `nvda_rdp_linux/client/brltty.py`;
* tones and sounds: `nvda_rdp_linux/client/audio.py`;
* Speech Dispatcher rendering: `nvda_rdp_linux/client/speech.py`;
* client options and channel startup: `nvda_rdp_linux/app.py`;
* FreeRDP options: `scripts/run-rdaccess-speech-session`;
* reproducible cases: add a fixture first, then a test under `tests/client/` or
  `tests/common/`, and a golden scenario for behaviour seen from outside.

After making a change, proceed in this order:

```text
fixture or observation
unit test
minimal modification
pytest and lint
C build if necessary
real RDP test
compare the FreeRDP and client logs
```

## Current Limitations

* driver settings management, Caps Lock synchronization, braille keyboard
  input and `pause_speech` have not been confirmed in a real session yet;
* `pause_speech` is ignored unless `--enable-pause` is given, because
  `sd_espeak-ng` repeated the sentence after resuming;
* NVDA sounds need a local copy of NVDA's `waves` directory;
* Caps Lock synchronization relies on X11 tools and may not work under Wayland;
* `timeSinceInput` is not used: AT-SPI focus decides when the remote output
  owns the braille display;
* no RDAccess 1.x compatibility (its legacy payloads are pickled and are
  deliberately never decoded);
* the client does not integrate with the local Orca: the remote braille takes
  the display while the RDP window has the focus, then gives it back.

## Assisted Testing Rule

Before starting a diagnostic session, first agree in the conversation on which
keys will be used, what audio result is expected, and how to exit the session.
Only run `scripts/run-rdaccess-speech-session` after explicitly confirming that
the procedure is understood.
