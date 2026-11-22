# Server packaging files

Files shared by the server packages of every distribution. `install.sh`
installs them with each package's paths (its header lists the variables); the
`.in` files contain these placeholders:

- `@LIBEXECDIR@`: directory of the server's helper programs
  (`nvda-rdp-server-session`, `nvda-rdp-brlapi-server`,
  `nvda-rdp-speechd-module`);
- `@PKGLIBDIR@`: directory of `libnvda-rdp-grd-preload.so`;
- `@PKGDATADIR@`: directory that contains `speechd/speechd.conf`;
- `@PYTHON@`: the Python 3 interpreter that finds `nvda_rdp_linux`;
- `@SPEECH_DISPATCHER@`: the `speech-dispatcher` program;
- `@PATH@`: the `PATH` of `nvda-rdp-server-session`, which needs
  `loginctl`, `id`, `mkdir`, `rm`, `touch` and `sleep`.

The user units go to the systemd user unit directory
(`/usr/lib/systemd/user` on Arch and Debian), with the two drop-in
directories:

- `orca.service.d/nvda-rdp-server.conf` starts the session check and the two
  servers with Orca, and gives Orca their addresses in a remote session;
- `gnome-remote-desktop-handover.service.d/nvda-rdp-server.conf` loads the
  channel library into GNOME Remote Desktop's session daemon.

In a local session the check finds the session is not remote: nothing else
starts and Orca keeps its usual BRLTTY and Speech Dispatcher.
