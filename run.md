# Getting Started with the NVDA/RDAccess Prototype

This guide explains how to build, test, and experiment with the current prototype. Compatibility has been specifically verified against RDAccess 2.0.0 at the following exact commit:

```text
2da84e8496519cf23614c0cfb3ace61ee0f9cd95
```

## Overview

The project is split into two parts:

```text
Windows NVDA/RDAccess
        |
        | NVDA-SPEECH and NVDA-BRAILLE DVCs
        v
FreeRDP + C plugin
        |
        | framed Unix socket
        v
Python clients
        |
        +--> persistent SSIP connection --> Speech Dispatcher / eSpeak NG
        |
        +--> braille cell capture (BRLTTY connected)
```

The C plugin handles FreeRDP, the DVCs, and IPC transport. The Python processes reconstruct JSON Lines messages, implement the small subset of RDAccess currently required, and communicate with Speech Dispatcher.

The plugin contains no RDAccess logic and generates no application-level bytes. It transports payloads in both directions and produces diagnostic dumps. The Python clients send XON on each connection, then handle the `protocolVersion=2` handshake and channel-specific responses.

Main files:

* `flake.nix`: Nix environment and FreeRDP variant containing the plugin;
* `freerdp-plugin/nvda_rdp_dvc.c`: DVC callbacks and Unix socket bridge;
* `freerdp-plugin/CMakeLists.txt`: FreeRDP module build configuration;
* `client/main.py`: speech process entry point;
* `client/braille_main.py`: braille process entry point;
* `client/rdaccess/ipc.py`: Unix socket and 32-bit framing;
* `client/rdaccess/framing.py`: IPC frame reconstruction;
* `client/rdaccess/protocol.py`: handshake, JSON Lines, `speak`, `cancel`, and
  `CharacterModeCommand` segmentation;
* `client/rdaccess/speech.py`: persistent Speech Dispatcher connection;
* `tests/`: unit tests and fixtures captured from real exchanges;
* `scripts/run-rdaccess-speech-session`: recommended complete launcher;
* `out.md`: detailed technical project log.

## Entering the Development Environment

From the repository root:

```bash
nix develop
```

The shell includes FreeRDP 3.30.0 with the plugin installed, GCC, CMake, Ninja, pkg-config, Python, pytest, Ruff, clang-format, clangd, and Speech Dispatcher 0.12.1.

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
ruff check client tests
ruff format --check client tests
clang-format --dry-run --Werror freerdp-plugin/nvda_rdp_dvc.c
```

Run the complete flake check from the repository root:

```bash
nix flake check
```

The tests cover fragmented and concatenated IPC framing, JSON Lines, fragmented UTF-8, real-world fixtures, `speak`, `IndexCommand`, `CharacterModeCommand`, `cancel`, reconnections, and outgoing handshake responses.

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
2. starts the Python speech and braille clients;
3. waits for both Unix sockets to be created;
4. launches FreeRDP and connects to the remote;
5. prompts for the password interactively;
6. enables the `NVDA-SPEECH` and `NVDA-BRAILLE` listeners, Windows audio,
   fullscreen mode, and the French keyboard layout;
7. writes traces directly under `logs/` instead of copying them to the terminal;
8. cleanly stops both Python clients when the session ends.

The password is never included in the command line or logs.

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

## Manual Launch for Understanding the Components

The automatic launcher is recommended for normal use. To observe each layer separately, open three terminals.

First terminal:

```bash
nix develop
python -m client.main --log-level DEBUG
```

The client should report that it is listening on:

```text
$XDG_RUNTIME_DIR/nvda-rdp-linux/speech.sock
```

Second terminal:

```bash
nix develop
python -m client.braille_main --brlapi --log-level DEBUG
```

It connects to the BRLTTY daemon through BrlAPI without an explicit `brlapi.key`
path, then listens on `$XDG_RUNTIME_DIR/nvda-rdp-linux/braille.sock`.
Authorization is delegated to the Polkit configuration already present on the
machine. Without `--brlapi`, capture mode remains available, and the number of
virtual cells can be changed with `--cells`.

Third terminal:

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

The Python clients must be started before FreeRDP. If a socket is unavailable,
the plugin continues to display the corresponding DVC blocks for diagnostic
purposes, but no client-side handshake will be sent back on that channel.

## Configuring the Voice

Default values:

```text
module: espeak-ng
language: fr
Orca rate: 75
```

To experiment manually:

```bash
python -m client.main --language fr --rate 70 --output-module espeak-ng
```

The rate uses Orca's 0–100 scale. The SSIP conversion is:

```text
SSIP = 2 × Orca rate − 100
```

Therefore, Orca 70 maps to SSIP +40, and Orca 75 maps to SSIP +50.

## Reading the Logs

The full launcher produces three main log files:

```text
logs/rdaccess-speech-session.log
logs/rdaccess-client.log
logs/rdaccess-braille.log
```

The first contains FreeRDP, drdynvc, and C plugin messages. The second contains
speech protocol and Speech Dispatcher messages. The third contains braille
negotiation and cell data.

Follow all three logs during a session:

```bash
tail -F logs/rdaccess-speech-session.log logs/rdaccess-client.log \
  logs/rdaccess-braille.log
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
rg -n 'sent legacy client|sent language|submitting speech|accepted speak|accepted cancel|unsupported' \
  logs/rdaccess-client.log
```

Typical healthy output:

```text
sent legacy client protocolVersion=2
sent language attribute value='fr'
submitting speech text='Bureau  fenêtre'
Speech Dispatcher accepted speak character_mode=False text='Bureau  fenêtre'
Speech Dispatcher accepted cancel
```

Check the braille channel:

```bash
rg -n 'protocolVersion|numRows|numCols|display|invalid|ERROR' \
  logs/rdaccess-braille.log
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
* IPC framing: `client/rdaccess/framing.py` and `client/rdaccess/ipc.py`;
* new RDAccess messages: `client/rdaccess/protocol.py`;
* braille negotiation and capture: `client/rdaccess/braille.py`;
* Speech Dispatcher rendering: `client/rdaccess/speech.py`;
* Python launch options: `client/main.py`;
* braille process startup: `client/braille_main.py`;
* FreeRDP options: `scripts/run-rdaccess-speech-session`;
* reproducible cases: add a fixture first, then a test under `tests/`.

After making a change, proceed in this order:

```text
fixture or observation
unit test
minimal modification
pytest and lint
C build if necessary
real RDP test
compare all three logs
```

## Current Limitations

* compatibility is currently targeted only at the RDAccess commit specified at
  the beginning of this document;
* `NVDA-BRAILLE` performs a real JSON v2 negotiation and captures `display`
  arrays in `logs/rdaccess-braille.log`; BrlAPI rendering with focus-based
  priority is implemented and worked with a physical braille
  display, but no gestures are currently sent yet;
* `IndexCommand` is sent back to RDAccess when local speech synthesis actually
  finishes; full spelling and typing assistance have been confirmed by
  listening to the resulting speech;
* `pause_speech` is recognized but intentionally ignored because `sd_espeak-ng`
  repeated the sentence after resuming; interruption through `cancel` remains
  functional;
* the `supportedCommands` attribute advertises only `CharacterModeCommand` and
  `IndexCommand`, the two commands actually supported;
* `wave` is logged but not rendered;
  Windows sounds remain available through FreeRDP audio redirection and are
  therefore not duplicated on the Linux side;
* no RDAccess 1.x compatibility;
* no direct Orca integration.

## Assisted Testing Rule

Before starting a diagnostic session, first agree in the conversation on which
keys will be used, what audio result is expected, and how to exit the session.
Only run `scripts/run-rdaccess-speech-session` after explicitly confirming that
the procedure is understood.
