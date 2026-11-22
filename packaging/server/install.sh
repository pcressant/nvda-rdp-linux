#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Install the server's helper programs, units and Speech Dispatcher
# configuration, with each package's paths (see README.md). The Python
# package and libnvda-rdp-grd-preload.so are installed separately.
#
# Usage: install.sh, run from this directory, with these variables set:
#   DESTDIR            staging root (empty for a direct install)
#   LIBEXECDIR         helper programs, e.g. /usr/lib/nvda-rdp-linux
#   PKGLIBDIR          directory of libnvda-rdp-grd-preload.so
#   PKGDATADIR         e.g. /usr/share/nvda-rdp-linux
#   USERUNITDIR        e.g. /usr/lib/systemd/user
#   PYTHON             Python 3 interpreter that finds nvda_rdp_linux
#   SPEECH_DISPATCHER  the speech-dispatcher program
#   SESSION_PATH       PATH of nvda-rdp-server-session (loginctl, coreutils)
set -eu

for name in LIBEXECDIR PKGLIBDIR PKGDATADIR USERUNITDIR PYTHON SPEECH_DISPATCHER SESSION_PATH; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "install.sh: $name is not set" >&2
    exit 2
  fi
done
DESTDIR=${DESTDIR:-}

expand() {
  mkdir -p "$(dirname "$2")"
  sed -e "s|@LIBEXECDIR@|$LIBEXECDIR|g" \
    -e "s|@PKGLIBDIR@|$PKGLIBDIR|g" \
    -e "s|@PKGDATADIR@|$PKGDATADIR|g" \
    -e "s|@PYTHON@|$PYTHON|g" \
    -e "s|@SPEECH_DISPATCHER@|$SPEECH_DISPATCHER|g" \
    -e "s|@PATH@|$SESSION_PATH|g" \
    "$1" >"$2"
  if grep -n '@[A-Z_]*@' "$2"; then
    echo "install.sh: unexpanded placeholder in $2" >&2
    exit 1
  fi
  chmod "$3" "$2"
}

for program in nvda-rdp-server-session nvda-rdp-brlapi-server nvda-rdp-speechd-module; do
  expand "$program.in" "$DESTDIR$LIBEXECDIR/$program" 755
done
expand speechd.conf.in "$DESTDIR$PKGDATADIR/speechd/speechd.conf" 644
for unit in nvda-rdp-server-session nvda-rdp-brlapi nvda-rdp-speechd; do
  expand "systemd/$unit.service.in" "$DESTDIR$USERUNITDIR/$unit.service" 644
done
expand systemd/orca.service.d/nvda-rdp-server.conf \
  "$DESTDIR$USERUNITDIR/orca.service.d/nvda-rdp-server.conf" 644
expand systemd/gnome-remote-desktop-handover.service.d/nvda-rdp-server.conf.in \
  "$DESTDIR$USERUNITDIR/gnome-remote-desktop-handover.service.d/nvda-rdp-server.conf" 644
