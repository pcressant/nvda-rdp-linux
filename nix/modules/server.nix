# SPDX-License-Identifier: GPL-2.0-or-later
# From draft-nvda-orca-rdp faae513, experiments 08 to 10 (rdaccess.nix,
# stock-grd.nix).
#
# services.nvda-rdp-server: in a GNOME session reached over RDP through
# GNOME Remote Desktop, Orca speaks with the client's NVDA and brailles on
# the client's display (NVDA's RDAccess add-on on the client). In local
# sessions nothing changes.
{ mkServer }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.nvda-rdp-server;
in
{
  options.services.nvda-rdp-server = {
    enable = lib.mkEnableOption "the client's NVDA speech and braille for Orca in RDP sessions of GNOME Remote Desktop";
    package = lib.mkOption {
      type = lib.types.package;
      default = mkServer pkgs;
      defaultText = lib.literalMD "nvda-rdp-server built from this flake with the system's nixpkgs";
      description = "The nvda-rdp-server package.";
    };
    loginScreen = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Also serve GDM's login screen of a remote connection.";
    };
  };

  config = lib.mkIf cfg.enable {
    # The units and the two drop-ins (orca.service, the handover daemon of
    # GNOME Remote Desktop) are plain files of the package, as on other
    # distributions.
    systemd.packages = [ cfg.package ];
    # nvda-rdp-settings and its man pages.
    environment.systemPackages = [ cfg.package ];

    systemd.user.units."nvda-rdp-server-session.service" = lib.mkIf (!cfg.loginScreen) {
      overrideStrategy = "asDropin";
      text = ''
        [Service]
        Environment=NVDA_RDP_LOGIN_SCREEN=no
      '';
    };
  };
}
