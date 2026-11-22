# SPDX-License-Identifier: GPL-2.0-or-later
# System test of services.nvda-rdp-server, from Linux to Linux.
#
# - server: GNOME with GDM's remote login through GNOME Remote Desktop, Orca
#   on the login screen, and services.nvda-rdp-server;
# - client: xfreerdp with the nvda-rdp-dvc plugin and nvda-rdp-client, whose
#   Speech Dispatcher records what it is asked to speak.
#
# The client connects to the server's remote login screen. Orca speaks there
# through the nvda-rdp Speech Dispatcher of the greeter session, the channel
# library in GNOME Remote Desktop, the RDP connection, the plugin and the
# client, and its text must reach the client's Speech Dispatcher. Orca's
# braille goes the same way through the nvda-rdp BrlAPI server: the server
# must learn the client's display size and the client must receive cells that
# are not blank (it only logs them: --braille-capture, at DEBUG). The local
# login screen of the server must keep its usual speech.
#
# The RDP credentials are a fixture of these throwaway VMs; they are only
# passed on standard input.
{ self }:
{ lib, pkgs, ... }:
let
  rdpUser = "rdp-test";
  rdpPassword = "rdp-test-password";
  grdHome = "/var/lib/gnome-remote-desktop";
  password = pkgs.writeText "rdp-password" ''
    ${rdpPassword}
  '';
  # Username, domain (empty), password: the order of xfreerdp's /from-stdin.
  clientCredentials = pkgs.writeText "rdp-client-credentials" ''
    ${rdpUser}

    ${rdpPassword}
  '';

  recorder = pkgs.writeShellScript "speechd-recorder" ''
    exec ${pkgs.python3}/bin/python3 ${./speechd-recorder.py}
  '';
  recorderConf = pkgs.writeTextDir "speechd.conf" ''
    AddModule "recorder" "${recorder}" ""
    DefaultModule recorder
  '';
in
{
  name = "nvda-rdp-server";

  nodes.server =
    { config, ... }:
    {
      imports = [ self.nixosModules.server ];

      services.displayManager.gdm.enable = true;
      services.desktopManager.gnome.enable = true;
      services.gnome.gnome-remote-desktop.enable = true;
      services.nvda-rdp-server.enable = true;

      # Orca on the login screen.
      programs.dconf.profiles.gdm.databases = [
        { settings."org/gnome/desktop/a11y/applications".screen-reader-enabled = true; }
      ];

      # The system daemon of GNOME Remote Desktop, for GDM's remote login.
      # NixOS ignores [Install] sections and grdctl's "rdp enable" relies on
      # them, so the configuration file and the units are declared here.
      environment.etc."gnome-remote-desktop/grd.conf".text = ''
        [RDP]
        enabled=true
        port=3389
        tls-cert=${grdHome}/tls.crt
        tls-key=${grdHome}/tls.key
      '';
      systemd.services.grd-setup = {
        description = "RDP certificate and credentials for GNOME Remote Desktop";
        wantedBy = [ "graphical.target" ];
        before = [ "gnome-remote-desktop.service" ];
        # As the gnome-remote-desktop user, grdctl configures the system
        # daemon without pkexec (gnome-remote-desktop 50.1, src/grd-ctl.c).
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = "gnome-remote-desktop";
          StateDirectory = "gnome-remote-desktop";
          Environment = "HOME=${grdHome}";
        };
        script = ''
          ${pkgs.openssl}/bin/openssl req -quiet -new -newkey rsa:2048 -days 30 -nodes -x509 \
            -subj /CN=server -out ${grdHome}/tls.crt -keyout ${grdHome}/tls.key
          # One prompt only: grdctl 50.1 crashes when both the username and
          # the password come from a non-terminal standard input (the first
          # prompt's buffered stream reads both lines).
          ${pkgs.gnome-remote-desktop}/bin/grdctl rdp set-credentials ${rdpUser} < ${password}
        '';
      };
      systemd.services.gnome-remote-desktop = {
        wantedBy = [ "graphical.target" ];
        requires = [ "grd-setup.service" ];
        # GDM first, or the daemon never listens (seen in the study).
        after = [
          "grd-setup.service"
          "display-manager.service"
        ];
      };

      # GNOME Remote Desktop logs at WARN by default; the test reads the
      # channel library's INFO and DEBUG lines.
      systemd.user.units."gnome-remote-desktop-handover.service" = {
        overrideStrategy = "asDropin";
        text = ''
          [Service]
          Environment=WLOG_FILTER=nvda-rdp.grd-preload:DEBUG
        '';
      };

      networking.firewall.allowedTCPPorts = [ 3389 ];
      virtualisation.memorySize = 4096;
      virtualisation.cores = 2;
    };

  nodes.client =
    { config, ... }:
    {
      imports = [
        "${pkgs.path}/nixos/tests/common/user-account.nix"
        "${pkgs.path}/nixos/tests/common/x11.nix"
      ];
      test-support.displayManager.auto.user = "alice";

      environment.systemPackages = [ pkgs.freerdp-with-nvda-rdp ];
      systemd.packages = [ pkgs.nvda-rdp-client ];
      # nvda-rdp-client listens to AT-SPI events; without the accessibility
      # bus, libatspi aborts the process.
      services.gnome.at-spi2-core.enable = true;

      # The Speech Dispatcher nvda-rdp-client finds at its default address,
      # instead of the socket-activated one of the speechd package, which
      # listens there too.
      systemd.user.units."speech-dispatcher.socket".enable = lib.mkForce false;
      systemd.user.services.recording-speechd = {
        description = "Speech Dispatcher that records the text it is given";
        environment.RECORDED_SPEECH = "%t/recorded-speech.txt";
        serviceConfig = {
          RuntimeDirectory = "speech-dispatcher";
          RuntimeDirectoryPreserve = "yes";
          ExecStart = "${pkgs.speechd}/bin/speech-dispatcher -s -t 0 -C ${recorderConf} -c unix_socket -S %t/speech-dispatcher/speechd.sock -P %t/speech-dispatcher/pid -L %t/speech-dispatcher";
        };
      };

      virtualisation.memorySize = 2048;
    };

  testScript =
    { nodes, ... }:
    let
      alice = nodes.client.users.users.alice;
      inUser = command: "systemctl --user -M ${alice.name}@ ${command}";
    in
    ''
      import shlex

      def user_journal(machine, unit):
          return machine.succeed(f"journalctl _SYSTEMD_USER_UNIT={unit} -o cat")

      # Assertions read the journal by syslog identifier: the stream header
      # from systemd carries it, while journald may miss the unit of a
      # process that exits at once (it reads the unit from the cgroup).
      def journal_has(identifier, text):
          return f"journalctl -t {identifier} -o cat | grep -q {shlex.quote(text)}"

      def wait_or_dump(machine, command, units, timeout):
          try:
              machine.wait_until_succeeds(command, timeout=timeout)
          except Exception:
              for unit in units:
                  print(f"--- {machine.name}: {unit}")
                  print(user_journal(machine, unit))
              if machine is server:
                  print("--- server state")
                  print(server.execute("sh ${./server-state.sh} 2>&1")[1])
              else:
                  print("--- xfreerdp")
                  print(client.execute("cat /tmp/xfreerdp.log")[1])
              raise

      start_all()

      with subtest("The local login screen keeps its usual speech"):
          server.wait_for_unit("display-manager.service")
          server.wait_until_succeeds(
              journal_has("nvda-rdp-server-session", "local session"),
              timeout=300,
          )
          # [n]: a pattern must not match the shell that runs pgrep. The
          # launchers exec Python, so the processes show their module.
          server.fail("pgrep -f '[n]vda_rdp_linux.server.brlapi_main'")
          server.fail("pgrep -f '[n]vda_rdp_linux.server.speechd_main'")

      with subtest("GNOME Remote Desktop listens"):
          server.wait_for_unit("gnome-remote-desktop.service")
          server.wait_for_open_port(3389)

      with subtest("The client is ready"):
          client.wait_for_x()
          client.wait_for_file("${alice.home}/.Xauthority")
          client.succeed("xauth merge ${alice.home}/.Xauthority")
          # The client connects to Speech Dispatcher once, at start.
          client.succeed("${inUser "start recording-speechd.service"}")
          client.wait_until_succeeds(
              "test -S /run/user/${toString alice.uid}/speech-dispatcher/speechd.sock"
          )
          # Speech Dispatcher also loads its usual modules: name the recorder.
          # Braille cells are only logged, at DEBUG: the client has no BRLTTY.
          options = (
              "NVDA_RDP_OPTIONS='--output-module=recorder --braille-capture --log-level DEBUG'"
          )
          setup = (
              "mkdir -p ~/.config/nvda-rdp-linux"
              f" && echo {shlex.quote(options)} > ~/.config/nvda-rdp-linux/environment"
          )
          client.succeed(f"su - ${alice.name} -c {shlex.quote(setup)}")
          client.succeed("${inUser "start nvda-rdp-client.service"}")
          wait_or_dump(
              client,
              "test -S /run/user/${toString alice.uid}/nvda-rdp-linux/speech.sock",
              ["nvda-rdp-client.service", "recording-speechd.service"],
              60,
          )

      with subtest("Connect to the remote login screen"):
          command = (
              # su does not set XDG_RUNTIME_DIR, where the plugin finds the
              # client's sockets.
              "DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/${toString alice.uid}"
              " xfreerdp /v:server /cert:ignore /from-stdin /dvc:nvda-rdp-dvc"
              " < ${clientCredentials} > /tmp/xfreerdp.log 2>&1"
          )
          client.succeed(f"su - ${alice.name} -c {shlex.quote(command)} >&2 &")

      with subtest("The remote login screen uses nvda-rdp"):
          wait_or_dump(
              server,
              journal_has("nvda-rdp-server-session", "remote session"),
              ["nvda-rdp-server-session.service", "gnome-remote-desktop-handover.service"],
              300,
          )
          server.wait_until_succeeds("pgrep -f '[n]vda_rdp_linux.server.brlapi_main'", timeout=60)
          # The channel library is loaded into the handover daemon.
          server.wait_until_succeeds(
              "for pid in $(pgrep -f '[g]nome-remote-desktop-daemon --handover'); do"
              " grep -q libnvda-rdp-grd-preload /proc/$pid/maps && exit 0; done; exit 1",
              timeout=60,
          )
          wait_or_dump(
              server,
              journal_has("gnome-remote-desktop-daemon", "NVDA-SPEECH: channel open"),
              ["gnome-remote-desktop-handover.service", "nvda-rdp-speechd.service", "orca.service"],
              120,
          )

      with subtest("Both channels negotiate RDAccess version 2"):
          wait_or_dump(
              server,
              "grep -q 'NVDA-SPEECH: protocol version 2 negotiated'"
              " /run/user/*/nvda-rdp-speechd-module.log",
              ["nvda-rdp-speechd.service"],
              120,
          )
          wait_or_dump(
              server,
              journal_has("nvda-rdp-brlapi-server", "NVDA-BRAILLE: protocol version 2 negotiated"),
              ["nvda-rdp-brlapi.service"],
              120,
          )

      with subtest("Orca's speech reaches the client's Speech Dispatcher"):
          # The client announces the connection itself; Orca speaks when the
          # focus moves on the login screen, here with Tab in xfreerdp's
          # window, which GNOME Remote Desktop passes to the session.
          record = "/run/user/${toString alice.uid}/recorded-speech.txt"
          orca_spoke = f"grep -v 'Remote speech connected' {record} | grep -q ."

          def tab_and_check(_):
              client.send_key("tab")
              return client.execute(orca_spoke)[0] == 0

          retry(tab_and_check, timeout_seconds=120)
          print(client.succeed(f"cat {record}"))

      with subtest("Orca's braille reaches the client"):
          # The size is pushed by the client and asked for by the server.
          wait_or_dump(
              server,
              journal_has("nvda-rdp-brlapi-server", "client display: 40 cells"),
              ["nvda-rdp-brlapi.service"],
              60,
          )
          # A display message with at least one raised dot.
          shown = (
              "journalctl -t nvda-rdp-client -o cat | grep -Eq"
              " 'braille display 40 cells: .*([1-9a-f][0-9a-f]|0[1-9a-f])'"
          )

          def tab_and_check_braille(_):
              client.send_key("tab")
              return client.execute(shown)[0] == 0

          retry(tab_and_check_braille, timeout_seconds=120)
          print(client.succeed(
              "journalctl -t nvda-rdp-client -o cat | grep 'braille display' | tail -n 3"
          ))

      with subtest("Logs"):
          print(server.execute("sh ${./server-state.sh} 2>&1")[1])
          print(user_journal(server, "nvda-rdp-server-session.service"))
          print(user_journal(server, "gnome-remote-desktop-handover.service"))
          print(client.succeed("cat /tmp/xfreerdp.log"))
    '';
}
