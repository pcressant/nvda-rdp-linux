# AGENTS.md — nvda-rdp-linux

## Project

This repository experiments with a Linux client compatible with NVDA's Remote Desktop Accessibility (RDAccess) support over RDP/FreeRDP.

The long-term goal is to allow a Linux client to use the RDP channels exposed by NVDA/RDAccess to obtain speech and braille locally from a remote Windows session.

Target architecture:

```text
Remote Windows
  NVDA + RDAccess
        |
        | RDP Dynamic Virtual Channels
        | NVDA-SPEECH
        | NVDA-BRAILLE
        v
Linux FreeRDP
        |
        | DVC plugin
        v
RDAccess protocol/bridge
        |
        +--> Speech Dispatcher
        +--> BRLTTY
        |
        +--> possible Orca integration
```

The project is currently experimental. Do not implement future stages prematurely.

## Environment

The project uses Nix.

Always prefer:

```bash
nix develop
```

to obtain the development environment.

The root `flake.nix` is the authoritative source for project dependencies.

Do not:

* modify the host NixOS configuration;
* install system dependencies imperatively;
* create system-wide overlays unless explicitly required;
* create multiple flakes without a strong technical justification.

The repository must remain self-contained and reproducible.

## Working Method

Work in small, verifiable milestones.

For each problem:

1. observe;
2. collect traces;
3. formulate a hypothesis;
4. verify the hypothesis against source code or through experimentation;
5. make the smallest necessary change;
6. build;
7. test against the real system;
8. document the result.

Do not stack multiple speculative changes in an attempt to make something work.

Do not claim that a feature works until it has actually been tested.

Explicitly distinguish between:

* what has been observed;
* what has been confirmed from source code;
* what has been inferred;
* what remains hypothetical.

## IMPORTANT — out.md

`out.md` is the technical report intended for the human user, in french.

AT THE END OF EVERY SIGNIFICANT TASK, update `out.md`.

This is a permanent rule and must not require reminders from the user.

`out.md` must explain what the user needs to understand about the work performed, rather than merely copying the commands that were executed.

After a long Codex session, the user should be able to read only `out.md` and understand:

* what was investigated;
* what was discovered;
* what works;
* what does not work;
* what evidence is available;
* which files/code were modified;
* which technical decisions were made and why;
* what uncertainties remain;
* what problem should be solved next;
* which commands, if any, the user must run manually.

Prefer technically precise but readable explanations.

Avoid cluttering `out.md` with:

* every intermediate command;
* unimportant failed attempts;
* normal build output;
* internal reasoning details that do not contribute to the diagnosis.

However, retain details that are useful for understanding or reproducing a result:

* relevant timestamps;
* error messages;
* function/class names;
* DVC names;
* important package or software versions;
* protocol excerpts;
* useful reproduction commands.

If an experiment invalidates a previous conclusion, update `out.md` so that it reflects the current state of knowledge. Clearly identify the invalidated conclusion if it was important.

Treat `out.md` as the living documentation of the experiment's current state.

## User Accessibility

The user works with a screen reader, primarily Orca on Linux.

Outputs must therefore remain usable without relying on purely visual information.

Never ask the user to:

* identify a color;
* visually locate a window;
* read a screenshot;
* observe an animation or visual indicator without providing a textual alternative.

Prefer:

* text logs;
* stdout/stderr;
* text files;
* deterministic commands;
* precise textual descriptions of state.

When graphical interaction is unavoidable, provide accessible control names and a keyboard-driven procedure whenever possible.

Avoid tools or terminals known to have poor accessibility when an accessible alternative exists.

Do not use xterm because its contents are not readable with Orca.

## Git

The project's Git repository is independent from the NixOS configuration.

Keep changes coherent and limited in scope.

Before considering a task complete, check at least:

```bash
git diff --check
git status --short
```

Never:

* delete or overwrite user work without permission;
* use `git reset --hard`;
* rewrite history;
* push to a remote unless explicitly requested;
* commit secrets, passwords, or private keys.

Do not create commits automatically unless explicitly requested by the user.

## Secrets

Never put a password:

* in Git;
* in `out.md`;
* in a tracked script;
* on a command line when it could appear in `ps` output or logs.

Temporary files containing secrets must be protected and excluded from Git.

Prefer interactive authentication mechanisms or already configured SSH keys.

## Windows Test VM

Test against a Windows RDP host configured with RDAccess.
Do not store hostnames, usernames, domains, credentials,
IP addresses, or other environment-specific information
in the repository.

Do not modify:

* Network settings;
* firewall configuration;
* OpenSSH;
* RDP configuration;
* general network configuration

without evidence that such a change is necessary and without user authorization.

The Windows SSH connection may belong to a different Windows session than the graphical RDP session.

Always verify:

* PID;
* SessionId;
* user;
* session context

before drawing conclusions about NVDA or RDAccess.

Windows SSH may be used to:

* inspect logs;
* inspect processes;
* inspect test project files/configuration;
* execute diagnostic commands;
* correlate Windows and Linux events.

Ask before making any significant or difficult-to-reverse Windows changes.

## RDAccess — Established Knowledge

The component being studied is RDAccess (Remote Desktop Accessibility).

Two RDP Dynamic Virtual Channels are used:

```text
NVDA-SPEECH
NVDA-BRAILLE
```

Normal keyboard input does not use these channels. It uses standard RDP input.

`NVDA-SPEECH` carries, among other things, speech presentation data prepared by the remote NVDA instance.

`NVDA-BRAILLE` carries, among other things, braille cells prepared on the remote side and braille gestures in the opposite direction.

The current RDAccess v2 protocol uses JSON Lines among other mechanisms, while retaining negotiation compatible with its older v1 protocol.

Connection control uses, among other values:

```text
XON  = 0x11
XOFF = 0x13
```

Do not confuse RDAccess with the complete NVDA Remote protocol.

When exact behavior matters, inspect the RDAccess source code corresponding to the version actually under test rather than relying solely on this summary.

## FreeRDP

Current development targets the FreeRDP 3.x version provided by the `devShell`.

Always verify APIs against the headers of the version actually present in the environment.

Do not blindly copy code written for FreeRDP 2.x or older APIs.

The current plugin handles Dynamic Virtual Channels.

FreeRDP must remain responsible for the RDP/drdynvc protocol.

Do not reimplement MS-RDPEDYC in this project.

Application code should work with the DVC payload as directly as possible.

## C Code

The FreeRDP plugin is written in C.

The user is not a C developer but must still be able to audit and understand the generated code.

Therefore:

* keep the code as small as reasonably possible;
* prefer short functions;
* avoid unnecessary abstractions;
* avoid complex macros when an explicit construct is sufficient;
* document elements whose lifetime or memory ownership is not obvious;
* clearly document FreeRDP callbacks;
* make allocations, deallocations, and ownership explicit;
* systematically check important return values.

When making a significant C change, explain its operation in `out.md` in terms of system logic:

* who calls the function;
* when it is called;
* what data enters;
* what data leaves;
* who owns the memory;
* when the object is destroyed.

Use the tools available in the `devShell`, including clang-format/clang-tidy when relevant.

## Diagnostics

For problems involving both Linux and Windows, prefer a correlated timeline.

Recommended format:

```text
Linux / FreeRDP                    Windows / NVDA-RDAccess

17:00:00.100 DVC open       <-->  17:00:00.098 WTS channel open
17:00:00.120 TX XON         --->  17:00:00.121 RX XON
                                  17:00:00.122 pushProtocolVersion
17:00:00.125 RX version     <---  17:00:00.124 WTS write
```

Use sufficiently precise timestamps.

A log showing that one layer accepted a write does not necessarily prove that the other endpoint received it.

Always distinguish between:

* a successful API call;
* a packet actually being transported;
* reception by the peer;
* processing by the remote application.

## Architecture

Preserve the following separation as much as possible:

```text
FreeRDP / DVC
      |
      v
raw transport
      |
      v
RDAccess protocol
      |
      v
input/output adapters
      |
      v
Speech Dispatcher / BRLTTY / possibly Orca
```

The FreeRDP plugin must not unnecessarily become responsible for all RDAccess protocol logic.

The transport and protocol layers should remain separable and independently testable.

## Project Order

Do not start higher-level layers prematurely.

General order:

1. prove bidirectional DVC opening and communication;
2. understand and reproduce the RDAccess handshake;
3. actually receive and decode `NVDA-SPEECH`;
4. stabilize transport between the plugin and the user-space process;
5. render a `SpeechSequence` locally;
6. correctly handle speech commands and indexes;
7. implement `NVDA-BRAILLE`;
8. interface with BRLTTY;
9. only then evaluate Orca integration.

This order may evolve when justified by technical discoveries, but do not skip several stages without a reason.

## Tests

Each layer should be independently testable as far as practical.

Prefer:

* fixtures;
* real protocol captures;
* unit tests for codecs;
* fragmentation/concatenation tests;
* reconnection tests;
* invalid-input tests.

Never use a simulated test as proof that a real RDP exchange works.

## General Principle

The goal is not to produce a large amount of code quickly.

The goal is to understand each layer well enough to produce an implementation that is:

* minimal;
* verifiable;
* maintainable;
* auditable;
* reproducible;
* usable with the user's accessibility tools.

When in doubt, reduce the scope of the change, increase observability, and document the result in `out.md`.

