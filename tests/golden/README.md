# Golden transcripts

Black-box non-regression tests. Each scenario in `scenarios/<side>/` drives a
real process as it runs in production. The harness records everything that
process does as a text transcript, and `test_golden.py` compares it with
`expected/<side>/`.

- `client/`: the client process (`python -m nvda_rdp_linux`) at revision
  `2b495eb`, with fake Speech Dispatcher, BrlAPI, AT-SPI, audio player and
  `xset`/`xdotool` (`fakes/`). Harness: `harness.py`.
- `server/`: the server side (`nvda_rdp_linux/server`): its BrlAPI server
  and its Speech Dispatcher output module. Harness: `server_harness.py`.
  - The harness plays the GNOME Remote Desktop preload and the RDAccess client.
  - It also plays Orca: a real BrlAPI client (`fakes/orca/`) and Orca's D-Bus
    service (`fakes/dbus/`).
  - It also plays Mutter's RemoteDesktop service and Speech Dispatcher.
  - Everything runs on a private session bus started by the harness, never on
    the user's own bus.
  - The transcripts were first recorded from the study draft-nvda-orca-rdp
    at revision `faae513`. The server was then ported onto `common/` and
    recorded again. The port changed exactly two things:
    - the module's `INIT` answer no longer names the study;
    - the client name it gives to the local Speech Dispatcher is now
      `nvda-rdp`.

## Startup

The client starts its Speech Dispatcher and AT-SPI connections on its main
thread and BrlAPI on another thread, concurrently. A client scenario lists in
`startup_trace` the last event of each of these sources its startup traces;
the harness waits for them before it closes the startup section, then lists
that section by source (Speech Dispatcher, AT-SPI, BrlAPI), each source in its
own order. A quiet period alone was not enough: under load, AT-SPI events fell
into the first step.

## Running

```sh
nix develop --command python -m pytest -m golden          # compare
nix develop --command python tests/golden/record.py       # record all
nix develop --command python tests/golden/record.py server/speech-nvda
```

A recorded transcript is reviewed before it is committed: it is the
reference.

## Environment

- `GOLDEN_SERVER_IMPL`: which server implementation the server scenarios run
  against:
  - `integrated` (default): the server of this repository;
  - `draft`: the study, to compare with it.
- `GOLDEN_DRAFT_DIR`: location of the draft repository. Default
  `../draft-nvda-orca-rdp`. Without it, the server scenarios are skipped
  when `GOLDEN_SERVER_IMPL=draft`.
- `GOLDEN_KEEP_DIR`: run the server scenarios in this directory and keep its
  logs. Unix socket paths are limited to 108 bytes, so the directory path must
  be short.

## Known library issues the harness works around

- **BRLTTY 6.9's Python binding** crashes when `brlapi.Connection()` gets the
  host and key as arguments, or when the connection fails. The fake Orca
  therefore:
  - connects through `BRLAPI_HOST` and `BRLAPI_AUTH`, as Orca does;
  - runs in its own process;
  - checks a refused key with a hand-written handshake.
- **liblouis 3.38's Python binding** sizes the result of `charToDots` from the
  number of characters instead of the encoded length, which truncates it.
  - The server works around it in `braille_bridge.char_to_dots`.
  - The draft was verified with liblouis 3.33. When it runs, the harness gives
    it a copy of the binding with the 3.33 length computation.
