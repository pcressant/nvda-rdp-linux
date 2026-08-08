# NVDA RDP Linux

NVDA RDP Linux is an experimental Linux prototype that receives speech and
braille produced by NVDA/RDAccess in a Windows RDP session through FreeRDP and
handles them locally with Speech Dispatcher and BRLTTY.

The project keeps the FreeRDP DVC plugin limited to raw transport. RDAccess
protocol handling and local output live in separate Python processes.

This is a proof of concept, not a finished production solution. Compatibility
has so far been developed and tested against the specific RDAccess version and
commit documented in [run.md](run.md). Braille input, complete RDAccess command
coverage, RDAccess 1.x compatibility, and direct Orca integration are not yet
implemented.

Development and builds have so far been performed and validated with Nix; a
build outside the provided Nix environment has not yet been tested.

See [run.md](run.md) for architecture, build, test, usage, and diagnostic
instructions.

## Credits

This prototype was inspired by and developed through studying the work of
[Leonard de Ruijter on RDAccess](https://github.com/LeonarddeR/rdAccess).
RDAccess provides the NVDA-side remote accessibility functionality and protocol
behavior that made this experiment possible. Leonard de Ruijter did not
participate in or endorse this prototype and is not responsible for it.

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
