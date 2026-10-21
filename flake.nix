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
          postInstall = (old.postInstall or "") + ''
            cmake \
              -S ${self}/freerdp-plugin \
              -B "$TMPDIR/nvda-rdp-dvc-build" \
              -G Ninja \
              -DCMAKE_BUILD_TYPE=Release \
              -DCMAKE_INSTALL_PREFIX="$out" \
              -DCMAKE_PREFIX_PATH="$out;${pkgs.zlib.dev}"
            cmake --build "$TMPDIR/nvda-rdp-dvc-build"
            cmake --install "$TMPDIR/nvda-rdp-dvc-build"
          '';
        });
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
            packages = with pkgs; [
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
              pipewire # pw-play for NVDA tones and sounds
              xdotool # optional Caps Lock synchronization
              xset # optional Caps Lock synchronization

              # C/C++ editor tooling, formatting, and static analysis
              clang-tools
            ];
            PYTHONPATH = pkgs.lib.concatStringsSep ":" [
              "${pkgs.speechd}/lib/python${pkgs.python3.pythonVersion}/site-packages"
              "${pkgs.brltty}/lib/python${pkgs.python3.pythonVersion}/site-packages"
            ];
            # pyatspi needs the Atspi typelib; do not rely on the host session's
            # GI_TYPELIB_PATH, which depends on the terminal the shell starts from.
            shellHook = ''
              export GI_TYPELIB_PATH="${
                pkgs.lib.makeSearchPath "lib/girepository-1.0" [
                  pkgs.at-spi2-core
                  pkgs.glib
                  pkgs.gobject-introspection
                ]
              }''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
            '';
          };
        }
      );

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          freerdp-with-nvda-rdp = mkFreerdpWithNvdaRdp pkgs;
        }
      );
    };
}
