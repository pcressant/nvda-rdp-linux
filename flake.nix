{
  description = "Development environment for the NVDA RDP Linux project";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ (import ./overlays/brltty.nix) ];
        };
      mkFreerdpWithNvdaRdp =
        pkgs:
        pkgs.freerdp.overrideAttrs (old: {
          pname = "freerdp-with-nvda-rdp";
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            pkgs.cmake
            pkgs.ninja
            pkgs.pkg-config
          ];
          # Only the plugin directory is hashed, so changes elsewhere in the
          # repository do not rebuild FreeRDP (and Remmina).
          postInstall = (old.postInstall or "") + ''
            cmake \
              -S ${./freerdp-plugin} \
              -B "$TMPDIR/nvda-rdp-dvc-build" \
              -G Ninja \
              -DCMAKE_BUILD_TYPE=Release \
              -DCMAKE_INSTALL_PREFIX="$out" \
              -DCMAKE_PREFIX_PATH="$out;${pkgs.zlib.dev}"
            cmake --build "$TMPDIR/nvda-rdp-dvc-build"
            cmake --install "$TMPDIR/nvda-rdp-dvc-build"
          '';
        });

      # Runtime environment shared by the dev shell and the packaged clients.
      clientPythonPath =
        pkgs:
        pkgs.lib.concatStringsSep ":" [
          "${pkgs.speechd}/lib/python${pkgs.python3.pythonVersion}/site-packages"
          "${pkgs.brltty}/lib/python${pkgs.python3.pythonVersion}/site-packages"
        ];
      # pyatspi needs the Atspi typelib; do not rely on the host session's
      # GI_TYPELIB_PATH, which depends on where the process is started from.
      clientTypelibPath =
        pkgs:
        pkgs.lib.makeSearchPath "lib/girepository-1.0" [
          pkgs.at-spi2-core
          pkgs.glib
          pkgs.gobject-introspection
        ];
      clientTools = pkgs: [
        pkgs.pipewire # pw-play for NVDA tones and sounds
        pkgs.xdotool # optional Caps Lock synchronization
        pkgs.xset # optional Caps Lock synchronization
      ];

      # nvda-rdp-client (speech and braille channels in one process) and a
      # systemd user unit that keeps it running for the whole graphical session,
      # so that any FreeRDP client (xfreerdp, Remmina) finds its sockets when the
      # DVCs open.
      mkClient =
        pkgs:
        let
          python = pkgs.python3.withPackages (pythonPackages: [
            pythonPackages.pyatspi
            pythonPackages.pygobject3
          ]);
          source = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = ./client;
          };
          launcher = pkgs.writeShellScriptBin "nvda-rdp-client" ''
            export PYTHONPATH="${source}:${clientPythonPath pkgs}"
            export GI_TYPELIB_PATH="${clientTypelibPath pkgs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
            export PATH="${pkgs.lib.makeBinPath (clientTools pkgs)}:$PATH"
            exec ${python}/bin/python -m client "$@"
          '';
          unit = pkgs.writeTextDir "share/systemd/user/nvda-rdp-client.service" ''
            [Unit]
            Description=NVDA RDAccess client (remote speech and braille)
            PartOf=graphical-session.target
            After=graphical-session.target

            [Service]
            # Optional settings, e.g. NVDA_WAVES_DIR=... or NVDA_RDP_OPTIONS=...
            EnvironmentFile=-%h/.config/nvda-rdp-linux/environment
            ExecStart=${launcher}/bin/nvda-rdp-client ''$NVDA_RDP_OPTIONS
            Restart=on-failure
            RestartSec=2

            [Install]
            WantedBy=graphical-session.target
          '';
        in
        pkgs.symlinkJoin {
          name = "nvda-rdp-linux-client";
          paths = [
            launcher
            unit
          ];
        };
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3.withPackages (pythonPackages: [
            pythonPackages.pytest
            pythonPackages.pyatspi
            pythonPackages.pygobject3
          ]);
          freerdpWithNvdaRdp = mkFreerdpWithNvdaRdp pkgs;
        in
        {
          default = pkgs.mkShell {
            packages =
              with pkgs;
              [
                # External FreeRDP DVC plugin
                stdenv.cc
                cmake
                ninja
                pkg-config
                freerdpWithNvdaRdp
                zlib # Required by FreeRDP-ClientConfig.cmake

                # RDAccess client and tests
                python
                ruff
                speechd
                brltty

                # C/C++ editor tooling, formatting, and static analysis
                clang-tools
              ]
              ++ clientTools pkgs;
            PYTHONPATH = clientPythonPath pkgs;
            shellHook = ''
              export GI_TYPELIB_PATH="${clientTypelibPath pkgs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
            '';
          };
        }
      );

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          freerdpWithNvdaRdp = mkFreerdpWithNvdaRdp pkgs;
        in
        {
          freerdp-with-nvda-rdp = freerdpWithNvdaRdp;
          client = mkClient pkgs;
          # Unmodified Remmina built against the FreeRDP variant that contains
          # the plugin; enable it with the connection's advanced setting
          # "Dynamic virtual channel" = nvda-rdp-dvc.
          remmina-with-nvda-rdp = pkgs.remmina.override { freerdp = freerdpWithNvdaRdp; };
        }
      );
    };
}
