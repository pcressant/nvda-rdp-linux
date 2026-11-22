# SPDX-License-Identifier: GPL-2.0-or-later
# Printed by the system test: where Orca's speech and braille go on the server.
echo "== processes"
pgrep -af 'orca|speech-dispatcher|nvda_rdp_linux|handover'
for pid in $(pgrep -f '[o]rca'); do
  echo "== environment of $pid: $(tr '\0' ' ' < "/proc/$pid/cmdline" | cut -c1-120)"
  tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(SPEECHD_|BRLAPI_|XDG_RUNTIME_DIR=)'
done
for pid in $(pgrep -f '[g]nome-remote-desktop-daemon --handover'); do
  echo "== handover $pid"
  tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(WLOG|LD_PRELOAD)'
done
echo "== handover drop-ins"
ls -l /etc/systemd/user/gnome-remote-desktop-handover.service.d/
for file in /run/user/*/nvda-rdp-server/orca.env /run/user/*/nvda-rdp-speechd-module.log; do
  [ -e "$file" ] && { echo "== $file"; cat "$file"; }
done
echo "== Unix sockets"
ss -xp | grep -E 'speech|nvda'
