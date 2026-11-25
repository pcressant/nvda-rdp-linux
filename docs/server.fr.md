# NVDA RDP Linux : guide du serveur

Traduction française de [server.md](server.md), qui fait référence.

Le côté serveur permet à un utilisateur aveugle sous Windows, avec NVDA et
son extension RDAccess, de travailler dans une session GNOME d'un ordinateur
Linux atteint en RDP : la parole d'Orca est entendue par le synthétiseur de
NVDA et le braille d'Orca s'affiche sur la plage braille de NVDA. Rien n'est
installé ni modifié côté Windows, et les sessions locales de l'ordinateur
Linux gardent leur parole et leur braille habituels.

La page de référence est `nvda-rdp-server(7)`.

## État

Le serveur vient de l'étude `draft-nvda-orca-rdp` (expériences 01 à 13,
commit `faae513`). Depuis Windows avec NVDA, RDAccess et `mstsc`, sur une VM
NixOS avec un GNOME Remote Desktop non modifié, elle a vérifié :

* l'écran de connexion à distance de GDM et la session qui suit, servis tous
  les deux ;
* la parole d'Orca par NVDA, avec l'interruption et la lecture continue, et
  les changements de voix d'Orca (débit, hauteur, volume, langue) sous forme
  de commandes relatives de NVDA ;
* la ligne braille d'Orca sur la plage de NVDA, à sa taille ;
* les touches de défilement, de ligne et de routage de la plage (par
  simulation), la saisie braille intégrale et les gestes de NVDA depuis la
  plage ;
* la fermeture brutale de `mstsc` et la reconnexion ;
* une session locale du même ordinateur laissée intacte.

Dans ce dépôt, avec les paquets construits ici, les tests système vérifient
de Linux à Linux, dans des VM NixOS, Arch Linux et Debian forky : l'écran de
connexion local garde sa parole, l'écran de connexion à distance est servi,
les deux canaux négocient la version 2 de RDAccess, la parole d'Orca
parvient au Speech Dispatcher du client, et le braille d'Orca parvient au
client, à la taille de sa plage (le client journalise les cellules au lieu
d'utiliser BRLTTY). Des essais manuels depuis Windows,
avec NVDA, RDAccess et `mstsc`, braille compris, ont réussi avec ces paquets
sous NixOS (le module `services.nvda-rdp-server`, sur la VM de l'étude), Arch
Linux et Debian forky. Les touches et la saisie braille depuis la plage du
client, et les cas d'échec, ne sont pas couverts par les tests automatiques.

## Prérequis

* GNOME 49 ou plus récent : GDM, GNOME Remote Desktop et Orca. Depuis
  GNOME 49, GDM fait tourner l'écran de connexion de chaque connexion à
  distance sous son propre utilisateur, et Orca tourne comme service
  utilisateur systemd `orca.service`. Testé avec GNOME Remote Desktop 50.2
  (étude, NixOS), 50.1 (test NixOS de ce dépôt) et 51.0 (Arch, Debian forky).
* Python 3.13 ou plus récent avec PyGObject, le module `louis` (liblouis) et
  le module `speechd` (Speech Dispatcher).
* systemd avec logind.

Debian 13 (trixie) n'est pas pris en charge : son GNOME 48 fait tourner les
écrans de connexion locaux et à distance sous le même utilisateur et démarre
Orca depuis gnome-session ; le serveur ne peut pas les distinguer. Les
paquets Debian visent forky.

## Architecture

```text
Windows: NVDA + RDAccess, mstsc
        |
        | RDP, dynamic virtual channels NVDA-SPEECH and NVDA-BRAILLE
        v
GNOME Remote Desktop (unmodified)
  + libnvda-rdp-grd-preload.so (LD_PRELOAD): each channel as a Unix socket
        |
        | 32-bit framed Unix sockets, one RDAccess channel each
        v
nvda-rdp-speechd.service                 nvda-rdp-brlapi.service
  Speech Dispatcher of the session         BrlAPI server of the session
  + output module nvda-rdp                 liblouis, Orca's table
        ^                                        ^
        | SPEECHD_ADDRESS                        | BRLAPI_HOST, BRLAPI_AUTH
        +-------------------- Orca --------------+
```

Orca n'est pas modifié et ne charge aucune extension. Il utilise ses deux
interfaces habituelles, Speech Dispatcher et BrlAPI, dirigées vers deux
serveurs propres à la session. Le Speech Dispatcher et le BRLTTY de la
machine ne sont ni arrêtés ni reconfigurés.

### Composants

* `libnvda-rdp-grd-preload.so` (`grd-preload/`), chargée dans le démon de
  session de GNOME Remote Desktop par un drop-in de
  `gnome-remote-desktop-handover.service`. Dans chaque connexion RDP, elle
  ouvre `NVDA-SPEECH` et `NVDA-BRAILLE` et sert chacun sur une socket de
  `$XDG_RUNTIME_DIR/gnome-remote-desktop/` (`nvda-speech.sock`,
  `nvda-braille.sock`). Elle utilise les mêmes trames de 32 bits que le
  greffon FreeRDP du client, une trame par message du canal. Elle appelle les
  fonctions WinPR de GNOME Remote Desktop lui-même, cherchées à l'exécution,
  et journalise par WLog avec l'étiquette `nvda-rdp.grd-preload`.
* `nvda-rdp-server-session` et son unité, au début de chaque session
  graphique, écrans de connexion de GDM compris : d'après les propriétés
  `Remote` et `Class` de logind, décide si la session est servie. Pour une
  session à distance, écrit `$XDG_RUNTIME_DIR/nvda-rdp-server/orca.env`
  (l'environnement d'Orca) et le marqueur `remote` ; sinon ne change rien.
* `nvda-rdp-speechd.service`, seulement dans les sessions à distance : une
  instance de Speech Dispatcher dont le seul module de sortie est
  `nvda-rdp-speechd-module` (`nvda_rdp_linux/server/speechd_module.py`). Le
  module transforme chaque message en séquence de parole RDAccess avec des
  index, et les index rapportés par NVDA en événements de Speech Dispatcher,
  pour que la lecture continue suive le rythme de NVDA. Tant que NVDA n'est
  pas rattaché, il transmet les messages au Speech Dispatcher habituel de la
  session, entendu par l'audio RDP. La voix par défaut d'Orca, lue par
  l'interface D-Bus d'Orca `org.gnome.Orca1.SpeechManager`, sert de
  référence aux commandes relatives de débit, de hauteur et de volume.
* `nvda-rdp-brlapi.service`, seulement dans les sessions à distance : un
  serveur BrlAPI (`nvda_rdp_linux/server/brlapi_server.py`) sur 127.0.0.1,
  port 4101 plus l'UID de l'utilisateur, avec une nouvelle clé à chaque
  démarrage. Il présente la plage de NVDA à sa taille, transcrit le texte
  d'Orca en points avec liblouis et la table braille d'Orca, et traduit les
  touches et les gestes de la plage en commandes BrlAPI pour Orca. La saisie
  braille passe par l'interface D-Bus RemoteDesktop de Mutter.
* Un drop-in de `orca.service` entraîne les trois unités, place Orca après
  elles et lit `orca.env`.

Les deux canaux répondent à la requête `timeSinceInput` du client par le
délai depuis la dernière entrée de la session, lu par l'interface D-Bus
`org.gnome.Mutter.IdleMonitor` de Mutter. Comme RDAccess, la réponse vaut 0
pendant les 4 secondes qui suivent l'attachement du client, et aussi en
l'absence de Mutter. Le client considère que la session distante a le focus
tant que ce délai ne dépasse pas 200 ms.

Le code partagé (`nvda_rdp_linux/common/`) est celui qu'utilise le client :
trames, format RDAccess, vocabulaire de la parole et du braille. Le côté
serveur joue le rôle de serveur RDAccess, celui que NVDA joue sous Windows.

### Fichiers

| Chemin (Debian ; Arch utilise `/usr/lib/nvda-rdp-linux` pour les deux répertoires) | Contenu |
| --- | --- |
| `/usr/bin/nvda-rdp-settings` | réglages des pilotes de NVDA depuis la session |
| `/usr/lib/<multiarch>/nvda-rdp-linux/libnvda-rdp-grd-preload.so` | bibliothèque des canaux |
| `/usr/libexec/nvda-rdp-linux/` | `nvda-rdp-server-session`, `nvda-rdp-brlapi-server`, `nvda-rdp-speechd-module` |
| `/usr/share/nvda-rdp-linux/speechd/speechd.conf` | configuration du Speech Dispatcher de la session |
| `/usr/lib/systemd/user/` | les trois unités et les deux répertoires de drop-ins |
| `$XDG_RUNTIME_DIR/nvda-rdp-server/` | état de la session : `remote`, `orca.env` ; sockets `speech-settings.sock` et `braille-settings.sock` de `nvda-rdp-settings`, mode 0600 |
| `$XDG_RUNTIME_DIR/nvda-rdp-brlapi.key` | clé BrlAPI, mode 0600 |
| `$XDG_RUNTIME_DIR/nvda-rdp-speechd-module.log` | journal du module de parole |

## Installation

### NixOS

La flake fournit le module `nixosModules.server` :

```nix
imports = [ inputs.nvda-rdp-linux.nixosModules.server ];
services.nvda-rdp-server.enable = true;
# services.nvda-rdp-server.loginScreen = false; # serve users' sessions only
```

`loginScreen = false` ne sert que les sessions des utilisateurs. Le paquet
est construit avec le nixpkgs de la configuration. GDM, GNOME et GNOME Remote
Desktop se configurent comme d'habitude (voir plus bas).

### Arch Linux

`nvda-rdp-server-git` est construit par le PKGBUILD publié sur l'AUR
(`packaging/arch/PKGBUILD`), avec n'importe quel assistant AUR ou avec
`makepkg -si`.

### Debian forky

Installer les fichiers `.deb` d'une version publiée :

```bash
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-server_*.deb
```

## Configuration

### Ouverture de session à distance de GDM

Le démon système de GNOME Remote Desktop doit accepter les connexions RDP,
dans les Paramètres de GNOME (Système, Bureau à distance, Connexion à
distance), ou en root :

```bash
tls=/var/lib/gnome-remote-desktop/tls
sudo install -d -o gnome-remote-desktop -g gnome-remote-desktop -m 700 "$tls"
sudo -u gnome-remote-desktop openssl req -new -newkey rsa:4096 -days 720 -nodes -x509 \
  -subj "/CN=$(hostname)" -out "$tls/tls.crt" -keyout "$tls/tls.key"
sudo grdctl --system rdp set-tls-key "$tls/tls.key"
sudo grdctl --system rdp set-tls-cert "$tls/tls.crt"
sudo grdctl --system rdp set-credentials USER   # asks for the password
sudo grdctl --system rdp enable
sudo grdctl --system status
```

`set-credentials` demande le mot de passe. Ces identifiants sont ceux de la
connexion RDP à l'écran de connexion, pas ceux d'un compte utilisateur ;
l'utilisateur ouvre ensuite sa session dans GDM. Sous NixOS, le `enable` de
`grdctl` s'appuie sur les sections `[Install]`, que NixOS ignore : activez
`services.gnome.gnome-remote-desktop` et écrivez
`/etc/gnome-remote-desktop/grd.conf` comme le fait `nix/tests/server.nix`.
Ouvrez le port TCP 3389 dans le pare-feu.

Dans les VM de test, le démon système de GNOME Remote Desktop n'écoutait
jamais quand il démarrait avant GDM ; un drop-in avec
`After=display-manager.service` corrige cela.

### Orca sur l'écran de connexion

Orca ne parle sur l'écran de connexion que si le lecteur d'écran y est
activé, depuis le menu d'accessibilité de l'écran de connexion ou dans les
réglages dconf de GDM, clé `screen-reader-enabled` de
`org.gnome.desktop.a11y.applications` :

* NixOS :

  ```nix
  programs.dconf.profiles.gdm.databases = [
    { settings."org/gnome/desktop/a11y/applications".screen-reader-enabled = true; }
  ];
  ```

* Debian : ajouter à la fin de `/etc/gdm3/greeter.dconf-defaults`, puis lancer
  `sudo dpkg-reconfigure gdm3` :

  ```ini
  [org/gnome/desktop/a11y/applications]
  screen-reader-enabled=true
  ```

* Arch : le profil de GDM 51, `/usr/share/dconf/profile/gdm`, n'a pas de base
  système. Créer `/etc/dconf/profile/gdm`, qui a la priorité, avec les lignes
  de l'original et `system-db:gdm` après `user-db:user` ; mettre le réglage
  ci-dessus dans `/etc/dconf/db/gdm.d/00-screen-reader` ; lancer
  `sudo dconf update`.

Dans la session d'un utilisateur, le lecteur d'écran s'active comme
d'habitude, dans les Paramètres de GNOME ou avec `Super+Alt+S`.

### Réglages

| Variable | Unité | Effet |
| --- | --- | --- |
| `NVDA_RDP_LOGIN_SCREEN=no` | `nvda-rdp-server-session.service` | l'écran de connexion à distance garde sa sortie habituelle ; seules les sessions des utilisateurs sont servies |
| `NVDA_RDP_PROSODY=0` | `nvda-rdp-speechd.service` | les changements de voix d'Orca ne sont pas envoyés à NVDA |
| `NVDA_RDP_LOG_LEVEL=DEBUG` | `nvda-rdp-speechd.service` | le module de parole journalise aussi le texte dit |
| `WLOG_FILTER=nvda-rdp.grd-preload:DEBUG` | `gnome-remote-desktop-handover.service` | les lignes de la bibliothèque des canaux apparaissent dans le journal |

Pour tous les utilisateurs, les régler par un drop-in sous
`/etc/systemd/user/`, par exemple
`/etc/systemd/user/nvda-rdp-server-session.service.d/login-screen.conf` :

```ini
[Service]
Environment=NVDA_RDP_LOGIN_SCREEN=no
```

## Utilisation

Sous Windows : NVDA avec RDAccess 2, et `mstsc` (ou tout client RDP que
RDAccess prend en charge) connecté à l'ordinateur Linux, port 3389, avec les
identifiants donnés à `grdctl`. Le certificat est le certificat auto-signé
ci-dessus ; vérifiez son empreinte quand `mstsc` l'affiche. L'écran de
connexion de GDM apparaît et Orca parle par NVDA ; une fois l'utilisateur
connecté, sa session est servie de la même façon.

* La parole, l'interruption (`Ctrl`) et la lecture continue suivent le
  synthétiseur de NVDA. NVDA garde sa propre voix ; seules les différences
  d'Orca avec sa voix par défaut sont appliquées, et un changement de langue
  ne s'entend que si le changement automatique de langue est activé dans
  NVDA.
* Le braille affiche la ligne d'Orca sur la plage de NVDA, avec la table
  d'Orca. Les touches de défilement, de ligne et de routage de la plage vont
  à Orca, comme la saisie braille et les gestes de la plage.
* Les réglages des pilotes de NVDA, si la « gestion des paramètres du
  pilote » est autorisée dans les paramètres de RDAccess sous Windows (elle
  est désactivée par défaut) : les voix du synthétiseur de NVDA figurent
  parmi les voix d'Orca pour le synthétiseur NVDA RDP, et en choisir une
  dans Orca change la voix de NVDA. La commande `nvda-rdp-settings`, dans
  la session, liste, lit et change les réglages du synthétiseur et de la
  plage braille de NVDA, avec leurs valeurs possibles (voir
  `nvda-rdp-settings(1)`) :

  ```sh
  nvda-rdp-settings speech list
  nvda-rdp-settings speech values voice
  nvda-rdp-settings speech set rate 60
  nvda-rdp-settings braille list
  ```

  Sans cette autorisation, Orca propose une seule voix, `NVDA`, et la
  commande indique que NVDA ne signale aucun réglage.
* Le clavier ordinaire et l'audio sont ceux de RDP.
* Si `mstsc` est fermé, la session reste ouverte ; une nouvelle connexion au
  même compte la rejoint.

## Diagnostic

```bash
journalctl -b -t nvda-rdp-server-session -t nvda-rdp-brlapi-server \
  -t gnome-remote-desktop-daemon
cat "$XDG_RUNTIME_DIR/nvda-rdp-speechd-module.log"
```

Lignes attendues, dans le journal :

```text
remote session: Orca uses the nvda-rdp BrlAPI server and Speech Dispatcher
NVDA-SPEECH: channel open
NVDA-BRAILLE: protocol version 2 negotiated
```

et dans le journal du module de parole :

```text
NVDA-SPEECH: protocol version 2 negotiated
```

`local session: Orca keeps its usual braille and speech` est normal dans les
sessions locales. Le journal se lit par identifiant syslog (`-t`) : journald
peut manquer l'unité d'un processus bref. Les lignes des utilisateurs des
écrans de connexion sont dans le journal système ; lisez-les en root.

## Sécurité

* Les sockets des canaux sont dans des répertoires en mode 0700, en
  mode 0600.
* Le serveur BrlAPI n'écoute que sur 127.0.0.1 et n'accepte que les clients
  qui connaissent la clé de la session, réécrite à chaque démarrage en
  mode 0600.
* La parole et le braille sont ce que présente Orca, mots de passe saisis
  compris : ils sont présumés confidentiels. Les journaux ne les contiennent
  qu'au niveau `DEBUG`.
* Tout programme de la session à distance peut écrire dans les canaux de
  NVDA, comme avec RDAccess sous Windows. Le NVDA du client est un pair de
  confiance : ses gestes braille agissent sur la session.
* RDP lui-même doit être configuré de façon sûre : NLA et un certificat dont
  le client vérifie l'empreinte.

## Limites

* La bibliothèque des canaux dépend de détails internes de GNOME Remote
  Desktop : un `WTSOpenServerA` et un `WTSCloseServer` par connexion. Une
  nouvelle version peut la casser sans prévenir ; une interface publique de
  GNOME Remote Desktop pour ouvrir des canaux nommés supprimerait cette
  dépendance.
* Seule l'ouverture de session à distance de GDM est servie, pas le partage
  d'une session locale ni les sessions sans écran.
* Un compte a un seul `orca.service` : avec une session locale et une session
  à distance en même temps, la première ouverte décide.
* Le choix de synthétiseur d'Orca, et sa plage de hauteur, n'ont pas
  d'effet sur NVDA ; son choix de voix non plus, sans la gestion des
  paramètres du pilote. La voix de référence est lue quand NVDA se
  rattache. Orca lit la liste des voix quand il la demande : après un
  changement de synthétiseur dans NVDA, les préférences d'Orca montrent
  les nouvelles voix une fois rouvertes.
* Un changement de taille de la plage coûte à Orca une reconnexion
  d'environ 2 secondes, car Orca ne lit la taille qu'une fois. Ce n'est pas
  le cas si la plage est d'abord retirée : Orca attend alors une taille et
  prend celle de la plage suivante en moins d'une demi-seconde.
* La saisie braille utilise les tables d'Orca, pas la table de saisie de
  NVDA, que RDAccess ne transmet pas : la table abrégée d'Orca quand le
  braille abrégé y est activé, sinon le braille informatique. En abrégé,
  les cellules attendent, affichées au curseur, qu'un espace, Entrée, les
  points 7-8 ou une autre action saisissent le mot ; le point 7 retire la
  dernière cellule en attente. Contrairement à NVDA, un mot déjà saisi
  n'est pas retraduit quand on le complète.
* Les touches de la plage suivent la table de gestes de NVDA telle que
  RDAccess l'envoie, gestes personnels de l'utilisateur compris, les
  affectations propres au modèle d'abord. Seuls les scripts NVDA qui ont
  un équivalent agissent : défilement, lignes, routage, retour au focus,
  touches `kb:`, saisie braille, et les modificateurs verrouillables Maj,
  Ctrl, Alt et Windows. Ceux qui utilisent la touche NVDA, comme les
  autres scripts NVDA, sont sans effet ; le journal les nomme une fois
  chacun.
* Le serveur BrlAPI rend chaque écriture comme une ligne entière : il ne
  conserve pas la ligne d'une écriture à l'autre comme le fait BRLTTY. Une
  écriture limitée à une région efface donc le reste de la ligne, et le
  masque d'attributs `AND` est ignoré. Orca écrit toujours la ligne
  entière, avec au plus un masque `OR` : cela ne change rien avec Orca, et
  ne compterait que pour un autre client BrlAPI. C'est laissé tel quel pour
  ne pas modifier un rendu qui fonctionne.
* Orca gère une seule ligne braille. Une plage de plusieurs rangées lui est
  présentée comme une ligne de toutes ses cellules, remplie rangée après
  rangée, comme RDAccess les envoie.
* La lecture continue s'arrête à une déconnexion.

## Notes de conception tirées de l'étude

Ces choix viennent des expériences de l'étude ; ses rapports en gardent le
détail.

* **Pas d'extension Orca.** Les premières expériences utilisaient une
  extension d'Orca pour observer la parole et le braille. Le point d'accroche
  de la parole d'Orca n'a ni voix, ni langue, ni interruption, la lecture
  continue ne peut pas suivre un synthétiseur distant, et le braille n'est
  disponible qu'avant la mise en forme. Speech Dispatcher et BrlAPI donnent
  tout cela, sans modifier Orca.
* **Un GNOME Remote Desktop non modifié.** GNOME Remote Desktop n'ouvre pas
  de canal arbitraire ; un correctif a fonctionné, puis la bibliothèque
  préchargée l'a remplacé.
* **Des serveurs de la session, pas de la machine.** Le Speech Dispatcher et
  le BRLTTY de la machine continuent de servir les sessions locales ; l'Orca
  d'une session à distance est dirigé ailleurs par son seul environnement.
* **À distance ou local, d'après logind.** `Remote` et `Class` de la session
  graphique décident ; les utilisateurs séparés des écrans de connexion de
  GNOME 49 permettent de servir l'écran de connexion.
* **Du texte aux points sous Linux.** Orca envoie du texte à BrlAPI ; le
  serveur le transcrit avec liblouis et la table d'Orca, pour que NVDA
  affiche ce qu'Orca afficherait.
* **Des commandes de voix relatives.** NVDA garde sa propre voix ; les
  changements d'Orca (majuscules, changements de débit) passent comme des
  différences avec sa voix par défaut.
* **La saisie braille par Mutter.** Les touches sont injectées par une
  session RemoteDesktop de Mutter, pas par GNOME Remote Desktop.
