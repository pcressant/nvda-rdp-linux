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
          overlays = [
            self.overlays.brltty
            self.overlays.default
          ];
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
      # Test-only runtime of the golden transcripts of the server side
      # (liblouis Python module and tables, an isolated session bus).
      testPythonPath = pkgs: "${pkgs.liblouis}/lib/python${pkgs.python3.pythonVersion}/site-packages";
      testTools = pkgs: [
        pkgs.dbus
        pkgs.liblouis
      ];
      clientTools = pkgs: [
        pkgs.pipewire # pw-play for NVDA tones and sounds
        pkgs.xdotool # optional Caps Lock synchronization
        pkgs.xset # optional Caps Lock synchronization
      ];

      # The Python package with only the given parts, besides common/.
      pythonSource =
        pkgs: parts:
        pkgs.lib.fileset.toSource {
          root = ./.;
          fileset = pkgs.lib.fileset.unions (
            [
              ./nvda_rdp_linux/__init__.py
              ./nvda_rdp_linux/common
            ]
            ++ parts
          );
        };

      # A man page of man/ and its French translation of man/fr/, under
      # share/man.
      manPage =
        pkgs: name:
        let
          section = pkgs.lib.last (pkgs.lib.splitString "." name);
        in
        pkgs.runCommand "${name}-man-page" { } ''
          install -Dm644 ${./man + "/${name}"} $out/share/man/man${section}/${name}
          install -Dm644 ${./man/fr + "/${name}"} $out/share/man/fr/man${section}/${name}
        '';

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
          source = pythonSource pkgs [
            ./nvda_rdp_linux/__main__.py
            ./nvda_rdp_linux/app.py
            ./nvda_rdp_linux/client
          ];
          launcher = pkgs.writeShellScriptBin "nvda-rdp-client" ''
            export PYTHONPATH="${source}:${clientPythonPath pkgs}"
            export GI_TYPELIB_PATH="${clientTypelibPath pkgs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
            export PATH="${pkgs.lib.makeBinPath (clientTools pkgs)}:$PATH"
            exec ${python}/bin/python -m nvda_rdp_linux "$@"
          '';
          # Same unit as distribution packages, pointing at the Nix launcher.
          # lib/systemd/user is where NixOS's systemd.packages looks for units.
          unit = pkgs.writeTextDir "lib/systemd/user/nvda-rdp-client.service" (
            builtins.replaceStrings [ "/usr/bin/nvda-rdp-client" ] [ "${launcher}/bin/nvda-rdp-client" ] (
              builtins.readFile ./packaging/systemd/nvda-rdp-client.service
            )
          );
        in
        pkgs.symlinkJoin {
          name = "nvda-rdp-linux-client";
          paths = [
            launcher
            unit
            (manPage pkgs "nvda-rdp-client.1")
          ];
        };
      # LD_PRELOAD library that adds the RDAccess channels to an unmodified
      # GNOME Remote Desktop. Built against the WinPR headers of the FreeRDP
      # that nixpkgs' gnome-remote-desktop uses; it links no WinPR.
      mkGrdPreload =
        pkgs:
        pkgs.stdenv.mkDerivation {
          pname = "nvda-rdp-grd-preload";
          version = "0.5.0";
          src = ./grd-preload;
          nativeBuildInputs = [
            pkgs.cmake
            pkgs.ninja
          ];
          buildInputs = [ pkgs.freerdp ];
        };

      mkServer =
        pkgs:
        import ./nix/server.nix {
          inherit pkgs;
          manPages = [
            (manPage pkgs "nvda-rdp-server.7")
            (manPage pkgs "nvda-rdp-settings.1")
          ];
          preload = mkGrdPreload pkgs;
          source = pythonSource pkgs [ ./nvda_rdp_linux/server ];
        };
    in
    {
      # Packages built from the consumer's nixpkgs, so that a NixOS
      # configuration uses its own nixpkgs and overlays (BRLTTY included).
      overlays.default = final: prev: {
        nvda-rdp-client = mkClient final;
        nvda-rdp-grd-preload = mkGrdPreload final;
        nvda-rdp-server = mkServer final;
        freerdp-with-nvda-rdp = mkFreerdpWithNvdaRdp final;
        # Unmodified Remmina built against the FreeRDP variant that contains
        # the plugin; enable it with the connection's advanced setting
        # "Dynamic virtual channel" = nvda-rdp-dvc.
        remmina-with-nvda-rdp = final.remmina.override { freerdp = final.freerdp-with-nvda-rdp; };
      };

      # BRLTTY with Polkit, for BrlAPI access; not part of overlays.default so
      # that configurations keep control over their own BRLTTY.
      overlays.brltty = import ./overlays/brltty.nix;

      # services.nvda-rdp-server, for a GNOME machine reached over RDP through
      # GNOME Remote Desktop. Needs no overlay.
      nixosModules.server = import ./nix/modules/server.nix { inherit mkServer; };

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3.withPackages (pythonPackages: [
            pythonPackages.pytest
            pythonPackages.pyatspi
            pythonPackages.pygobject3
          ]);
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
                freerdp-with-nvda-rdp
                zlib # Required by FreeRDP-ClientConfig.cmake

                # RDAccess client and tests
                python
                ruff
                speechd
                brltty

                # C/C++ editor tooling, formatting, and static analysis
                clang-tools
              ]
              ++ clientTools pkgs
              ++ testTools pkgs;
            PYTHONPATH = "${clientPythonPath pkgs}:${testPythonPath pkgs}";
            # Only this shell's typelibs: those of the host session (another
            # GLib release) would not match the shell's PyGObject.
            shellHook = ''
              export GI_TYPELIB_PATH="${clientTypelibPath pkgs}"
            '';
          };
        }
      );

      # Distribution packages, built and tested in VMs of the distribution
      # (nix/distro-vm). They need the network and KVM, so they are apps and
      # not derivations. Run from the working tree: `nix run .#build-arch`.
      apps = nixpkgs.lib.genAttrs [ "x86_64-linux" ] (
        system:
        let
          pkgs = pkgsFor system;
          distroVm =
            name: command:
            pkgs.writeShellApplication {
              name = "${command}-${name}";
              runtimeInputs = [
                pkgs.git
                pkgs.openssh
                pkgs.python3
                pkgs.qemu_kvm
                pkgs.xorriso
              ];
              text = ''
                repository=$(git rev-parse --show-toplevel)
                exec python3 -P ${./nix/distro-vm}/${name}.py ${command} "$repository"
              '';
            };
          app = name: command: {
            type = "app";
            program = "${distroVm name command}/bin/${command}-${name}";
            meta.description = "${command} the ${name} packages in a VM of the distribution";
          };
        in
        {
          build-arch = app "arch" "build";
          test-arch = app "arch" "test";
          build-debian = app "debian" "build";
          test-debian = app "debian" "test";
        }
      );

      # System tests in NixOS VMs (KVM).
      checks = forAllSystems (system: {
        server = (pkgsFor system).testers.runNixOSTest (import ./nix/tests/server.nix { inherit self; });
      });

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          inherit (pkgs) freerdp-with-nvda-rdp remmina-with-nvda-rdp;
          client = pkgs.nvda-rdp-client;
          grd-preload = pkgs.nvda-rdp-grd-preload;
          server = pkgs.nvda-rdp-server;
        }
      );
    };
}
