# SPDX-License-Identifier: GPL-2.0-or-later
# nvda-rdp-server: Orca in a session reached over RDP through GNOME Remote
# Desktop speaks and brailles on the client's NVDA. The files under
# packaging/server, with this package's store paths.
{
  pkgs,
  manPages,
  preload,
  source,
}:
let
  inherit (pkgs) lib;
  sitePackages = pkgs.python3.sitePackages;
  python = pkgs.python3.withPackages (pythonPackages: [ pythonPackages.pygobject3 ]);
  pythonPath = lib.concatStringsSep ":" [
    "${source}"
    "${pkgs.brltty}/${sitePackages}"
    "${pkgs.liblouis}/${sitePackages}"
    "${pkgs.speechd}/${sitePackages}"
  ];
  typelibPath = lib.makeSearchPath "lib/girepository-1.0" [
    pkgs.glib
    pkgs.gobject-introspection
  ];
  sessionPath = lib.makeBinPath [
    pkgs.coreutils
    pkgs.systemd
  ];
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "nvda-rdp-server";
  version = "0.5.0";
  src = ../packaging/server;

  dontConfigure = true;
  dontBuild = true;
  # NixOS reads user units from lib/systemd/user, not share/systemd/user.
  dontMoveSystemdUserUnits = true;

  # Python with the package and its dependencies: the launchers run it.
  python3Env = pkgs.writeShellScript "nvda-rdp-server-python" ''
    export PYTHONPATH="${pythonPath}"
    export GI_TYPELIB_PATH="${typelibPath}"
    exec ${python}/bin/python3 "$@"
  '';

  installPhase = ''
    runHook preInstall
    BINDIR=$out/bin \
      LIBEXECDIR=$out/libexec/nvda-rdp-linux \
      PKGLIBDIR=${preload}/lib/nvda-rdp-linux \
      PKGDATADIR=$out/share/nvda-rdp-linux \
      USERUNITDIR=$out/lib/systemd/user \
      PYTHON=$python3Env \
      SPEECH_DISPATCHER=${pkgs.speechd}/bin/speech-dispatcher \
      SESSION_PATH=${sessionPath} \
      sh install.sh
    for manPage in ${lib.escapeShellArgs manPages}; do
      (cd $manPage && find share -type f -exec install -Dm644 {} $out/{} \;)
    done
    runHook postInstall
  '';

  # No placeholder may be left.
  doInstallCheck = true;
  installCheckPhase = ''
    if grep -rn '@[A-Z_]*@' $out; then
      echo "unexpanded placeholder" >&2
      exit 1
    fi
  '';

  passthru = { inherit preload; };

  meta = {
    description = "Orca over RDP with the client's NVDA speech and braille (GNOME Remote Desktop)";
    license = lib.licenses.gpl2Plus;
    platforms = lib.platforms.linux;
  };
}
