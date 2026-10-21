# RDAccess 2.0.0 captures

These fixtures come from a real RDP session against a Windows test VM on
7 August 2026.

Exact reference:

- RDAccess 2.0.0;
- commit `2da84e8496519cf23614c0cfb3ace61ee0f9cd95`;
- `NVDA-SPEECH` channel;
- Windows SessionId 5;
- FreeRDP 3.30.0;
- Linux plugin sending only XON `0x11`.

Each `.hex` file contains, without the drdynvc header, one block delivered to
`OnDataReceived`. Spaces and the final newline only make the fixture readable.

Observed order:

1. `server-protocol-version-v1.hex`, 22 bytes at 18:19:29.147;
2. `server-language-request-v1.hex`, 14 bytes at 18:19:29.189;
3. `first-speak-v1.hex`, 96 bytes at 18:19:29.262.

A second real test, in Windows SessionId 6, then negotiated version 2:

1. `client-protocol-version-v1.hex`, 22 bytes sent at 18:30:00.330;
2. `server-protocol-version-v2-jsonl.hex`, 69 bytes received at 18:30:00.331;
3. `server-language-request-v2-jsonl.hex`, 55 bytes received at 18:30:00.368;
4. `first-speak-v2-jsonl.hex`, 94 bytes received at 18:30:00.445.

A later test moved the responses into the Python process and observed:

- `client-language-v2-jsonl.hex`, 68 bytes sent by Python and forwarded to the DVC;
- plugin confirmation: `forwarded 68 IPC bytes to DVC`;
- the answer to the server's `language` request, value `fr`.

On 8 August 2026, `character-mode-speak-v2-jsonl.hex` was captured during a
real test. It contains the announcement "Quitter q", with character mode
enabled before `q`, then back to the default mode.

`pause-speech-v2-jsonl.hex` keeps the real `pause_speech` message with
`switch=true`, received on 7 August 2026 while testing the Python client.

`server-supported-commands-v2-jsonl.hex` keeps the real request for the
`supportedCommands` attribute, received several times during v2 sessions.

On 8 August 2026 at 06:21, the first test of the `NVDA-BRAILLE` listener
captured four real legacy blocks: `braille-server-protocol-version-v1.hex`,
then the `numRows`, `numCols` and `gestureMap` requests. The user-space braille
client was not running yet: these blocks were received after XON but no Linux
answer was sent.

After fixing the legacy/JSON transition, the 06:35 test received many 40-cell
`display` messages. `first-braille-display-v2-jsonl.hex` keeps the exact first
JSON Lines block of 159 bytes received at 06:35:24.200.

The handshake therefore starts in the legacy v1 framing. After the client's
legacy `protocolVersion=2` announcement, the server immediately switches to
JSON Lines. JSON Lines files include their final LF `0a` separator.

Complete sources:

- `logs/rdaccess-v2-2da84e8-session.log`;
- NVDA log of the initial v2 Windows session;
- `logs/rdaccess-v2-json-2da84e8-session6.log`;
- NVDA log of the JSON v2 Windows session;
- `logs/rdaccess-client-python-handshake.log`;
- `logs/rdaccess-speech-python-handshake.log`.
