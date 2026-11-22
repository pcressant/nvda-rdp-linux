# NVDA RDP Linux : guide du client

Traduction française de [run.md](run.md), qui fait référence.

Ce guide explique l'architecture du client, et comment le construire, le
tester, l'utiliser et le diagnostiquer. Le côté serveur, Orca par GNOME
Remote Desktop pour un utilisateur de NVDA sous Windows, a son propre guide,
[docs/server.fr.md](docs/server.fr.md) ; l'empaquetage et les tests en VM sont
dans [docs/packaging.fr.md](docs/packaging.fr.md). La page de référence de la
commande est `nvda-rdp-client(1)`.

Le client implémente le côté client de RDAccess 2.0.5 (publié le
15 octobre 2026). Historique de validation :

* les captures de protocole de `tests/fixtures/` viennent de RDAccess 2.0.0,
  commit `2da84e8496519cf23614c0cfb3ace61ee0f9cd95` ;
* de vraies sessions avec RDAccess 2.0.4 et NVDA 2026.2 ont confirmé la
  parole, l'affichage braille, les touches de la plage et le routage, les sons
  de NVDA, Remmina et le service client unique ;
* la gestion des paramètres du pilote, la synchronisation du verrouillage des
  majuscules, la saisie au clavier braille et `pause_speech` ne sont couvertes
  que par des tests unitaires.

## Vue d'ensemble

Le client a deux parties :

```text
Windows NVDA/RDAccess
        |
        | NVDA-SPEECH and NVDA-BRAILLE DVCs
        v
FreeRDP + C plugin
        |
        | framed Unix socket
        v
Python client (one process, one thread per channel)
        |
        +--> persistent SSIP connection --> Speech Dispatcher / eSpeak NG
        |
        +--> pw-play --> NVDA tones and sounds
        |
        +--> BrlAPI --> BRLTTY (cells out, display keys back to NVDA)
```

Le greffon C s'occupe de FreeRDP, des canaux virtuels dynamiques (DVC) et du
transport IPC. Le processus du client Python écoute sur une socket par DVC,
reconstitue les messages JSON Lines, implémente le côté client de RDAccess et
dialogue avec Speech Dispatcher, PipeWire et BRLTTY.

Le greffon ne contient aucune logique RDAccess et ne produit aucun octet de
niveau applicatif. Il transporte les données dans les deux sens ; avec
`NVDA_RDP_DVC_DEBUG` défini dans l'environnement du client RDP, il journalise
aussi chaque bloc avec un vidage hexadécimal. Il se reconnecte à la socket du
client quand celui-ci redémarre pendant une session. Le client Python envoie XON à chaque connexion,
puis mène la négociation `protocolVersion=2` et les réponses propres à chaque
canal.

Fichiers principaux :

* `flake.nix` : environnement Nix et variante de FreeRDP contenant le
  greffon ;
* `freerdp-plugin/nvda_rdp_dvc.c` : fonctions de rappel DVC et pont vers les
  sockets Unix ;
* `freerdp-plugin/CMakeLists.txt` : configuration de construction du module
  FreeRDP ;
* `nvda_rdp_linux/app.py` : point d'entrée du client
  (`python -m nvda_rdp_linux`), qui fait tourner les deux canaux dans un seul
  processus et contient leurs défaillances ;
* `nvda_rdp_linux/common/` : code partagé avec le côté serveur, sans E/S :
  * `framing.py` : reconstitution des trames IPC (longueur sur 32 bits) ;
  * `rdaccess.py` : format RDAccess v2 (trames d'attributs historiques, JSON
    Lines, décodage du flux) ;
  * `speech.py` : vocabulaire de `NVDA-SPEECH` et commandes de prosodie ;
  * `braille.py` : vocabulaire de `NVDA-BRAILLE`, cellules et gestes ;
  * `settings.py` : encodage JSON des paramètres de pilote de NVDA ;
* `nvda_rdp_linux/client/ipc.py` : socket Unix du greffon FreeRDP ;
* `nvda_rdp_linux/client/session.py` : négociation du client et attributs
  génériques (`supportedSettings`, versions, `capsLockToggle`) ;
* `nvda_rdp_linux/client/protocol.py` : messages de `NVDA-SPEECH` et rendu des
  séquences de parole (index, mode caractère, langue, prosodie, pauses) ;
* `nvda_rdp_linux/client/speech.py` : connexion persistante à Speech
  Dispatcher et réglages de la voix ;
* `nvda_rdp_linux/client/audio.py` : bips de NVDA et fichiers WAV par
  `pw-play` ;
* `nvda_rdp_linux/client/braille.py` : messages et gestes de `NVDA-BRAILLE` ;
* `nvda_rdp_linux/client/brltty.py` : sortie BrlAPI, lecture des touches et
  focus AT-SPI ;
* `nvda_rdp_linux/client/capslock.py` : synchronisation facultative du
  verrouillage des majuscules ;
* `nvda_rdp_linux/client/notify.py` : annonces de connexion ;
* `tests/common/`, `tests/client/` : tests unitaires ; `tests/fixtures/` :
  données capturées lors d'échanges réels ; `tests/golden/` : transcriptions
  de non-régression en boîte noire ;
* `scripts/run-rdaccess-speech-session` : lanceur complet recommandé ;
* `man/` : pages de manuel, avec leurs traductions françaises dans `man/fr/` ;
* `packaging/` : fichiers des paquets de distribution (voir
  [docs/packaging.fr.md](docs/packaging.fr.md)).

Le côté serveur se trouve dans `grd-preload/`, `nvda_rdp_linux/server/`,
`packaging/server/`, `nix/server.nix`, `nix/modules/` et `tests/server/` ;
voir [docs/server.fr.md](docs/server.fr.md).

## Entrer dans l'environnement de développement

Depuis la racine du dépôt :

```bash
nix develop
```

Le shell contient FreeRDP 3.30.0 avec le greffon installé, GCC, CMake, Ninja,
pkg-config, Python, pytest, Ruff, clang-format, clangd, Speech
Dispatcher 0.12.1, BRLTTY, `pw-play` de PipeWire, `xdotool` et `xset`.

Après une modification du greffon, relancer `nix develop` peut reconstruire la
variante locale de FreeRDP. Le premier lancement peut donc être plus long.

Les répertoires `build/` et `build-*` sont locaux et ignorés par Git. Ils
peuvent être recréés à tout moment sans entrer dans un commit.

## Construire le greffon C à la main

Dans `nix develop` :

```bash
cmake -S freerdp-plugin -B build/dev -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/dev --target nvda-rdp-dvc
```

Le fichier obtenu est :

```text
build/dev/libnvda-rdp-dvc-client.so
```

Vérifier les bibliothèques dynamiques et les symboles avec :

```bash
ldd -r build/dev/libnvda-rdp-dvc-client.so
```

La sortie ne doit contenir ni `not found` ni `undefined symbol`.

Remarque : le FreeRDP 3.30 de la flake utilise des chemins absolus pour ses
modules. Le fichier construit sous `build/dev` sert aux vérifications de
développement, mais le `xfreerdp` du shell charge la copie installée dans la
variante Nix `freerdp-with-nvda-rdp`. Sortez de `nix develop` et rentrez-y
pour reconstruire cette variante après une modification du code C.

## Lancer les tests sans RDP

Dans `nix develop` :

```bash
pytest -q
ruff check nvda_rdp_linux tests nix/distro-vm
ruff format --check nvda_rdp_linux tests nix/distro-vm
clang-format --dry-run --Werror freerdp-plugin/nvda_rdp_dvc.c grd-preload/nvda_rdp_grd_preload.c
```

`pytest` lance les tests du client, du code commun et du serveur. Les tests
golden rejouent des scénarios en boîte noire contre les processus du client
et du serveur, et comparent leur sortie à des transcriptions enregistrées
(`tests/golden/README.md`).

Lancer la vérification complète de la flake, qui fait aussi tourner le test
système NixOS dans des VM (KVM), depuis la racine du dépôt :

```bash
nix flake check
```

Les tests couvrent les trames IPC fragmentées et concaténées, JSON Lines,
l'UTF-8 fragmenté, les captures réelles, `speak` et toutes les commandes de
parole prises en charge, `cancel`, `pause_speech`, les bips et les sons (dont
le refus des chemins qui sortent du répertoire), les paramètres du pilote,
les gestes braille et la table de gestes, la synchronisation du verrouillage
des majuscules, les annonces, les reconnexions et les réponses de
négociation envoyées. Les messages qui n'ont pas été capturés en session
réelle sont marqués comme synthétiques dans les tests.

## Session complète recommandée

Depuis la racine du dépôt :

```bash
export RDP_HOST=windows-remote
export RDP_USER=username
export RDP_DOMAIN=workgroup  # Optional.
./scripts/run-rdaccess-speech-session
```

Le script :

1. entre dans `nix develop` ;
2. démarre le client Python (`python -m nvda_rdp_linux`), sauf si le service
   utilisateur `nvda-rdp-client` tourne déjà ;
3. attend que les deux sockets Unix soient créées ;
4. lance FreeRDP et se connecte à la machine distante ;
5. demande le mot de passe de façon interactive ;
6. active les canaux `NVDA-SPEECH` et `NVDA-BRAILLE`, l'audio Windows, le
   plein écran et la disposition de clavier française ;
7. écrit les traces directement sous `logs/` au lieu de les copier dans le
   terminal ;
8. arrête proprement, en fin de session, le client qu'il a démarré.

Le mot de passe n'apparaît jamais sur la ligne de commande ni dans les
journaux.

Variables d'environnement facultatives :

```text
NVDA_WAVES_DIR             copy of NVDA's waves directory (sounds); default:
                           nvda-waves/ at the repository root if present
RDACCESS_CLIENT_OPTIONS    extra options for python -m nvda_rdp_linux
```

`NVDA_WAVES_DIR` est une copie du répertoire `waves` de NVDA (les sons), par
défaut `nvda-waves/` à la racine du dépôt s'il existe ;
`RDACCESS_CLIENT_OPTIONS` contient des options supplémentaires pour
`python -m nvda_rdp_linux`.

Exemple :

```bash
export NVDA_WAVES_DIR=~/nvda-waves
export RDACCESS_CLIENT_OPTIONS="--settings-management --sync-caps-lock"
```

Réglages importants de FreeRDP utilisés par le script :

```text
/v:windows-remote
/u:username
/d:workgroup
/dvc:nvda-rdp-dvc
/sound:sys:pulse
/kbd:layout:0x0000040C
+f
+grab-keyboard
```

`0x0000040C` est la disposition « French » de FreeRDP.

## Client persistant (service utilisateur systemd)

La sortie `client` de la flake fournit `nvda-rdp-client` (le client Python
avec ses dépendances d'exécution) et une unité utilisateur systemd qui le
garde actif pendant toute la session graphique. Tout client FreeRDP,
`xfreerdp` ou Remmina, trouve alors ses sockets quand les DVC s'ouvrent.

```bash
nix profile install .#client
systemctl --user link ~/.nix-profile/lib/systemd/user/nvda-rdp-client.service
systemctl --user enable --now nvda-rdp-client.service
```

Sous NixOS, l'`overlays.default` de la flake ajoute `nvda-rdp-client`,
`freerdp-with-nvda-rdp` et `remmina-with-nvda-rdp` aux `pkgs` de la
configuration (son nixpkgs et ses overlays). `overlays.brltty` est la
surcharge facultative de BRLTTY avec Polkit. NixOS ignore la section
`[Install]` de l'unité, la cible est donc fixée dans la configuration :

```nix
nixpkgs.overlays = [ inputs.nvda-rdp-linux.overlays.default ];
environment.systemPackages = [
  pkgs.nvda-rdp-client
  pkgs.remmina-with-nvda-rdp # replaces remmina
  pkgs.freerdp-with-nvda-rdp # replaces freerdp (xfreerdp)
];
systemd.packages = [ pkgs.nvda-rdp-client ];
systemd.user.services.nvda-rdp-client.wantedBy = [ "graphical-session.target" ];
```

Les réglages facultatifs vont dans `~/.config/nvda-rdp-linux/environment` :

```text
NVDA_WAVES_DIR=/path/to/nvda-waves
NVDA_RDP_OPTIONS=--settings-management --sync-caps-lock
```

Redémarrez le service après une modification. Journal :

```bash
journalctl --user -u nvda-rdp-client -f
```

Quand le service est actif, `scripts/run-rdaccess-speech-session` l'utilise
au lieu de démarrer son propre client.

## Isolation des défaillances

Les deux canaux tournent dans un seul processus ; une défaillance de l'un ne
doit donc pas arrêter l'autre :

* une erreur pendant le traitement d'une connexion du greffon est
  journalisée avec sa pile d'appels, et le canal accepte la connexion
  suivante ;
* si Speech Dispatcher est injoignable au démarrage, le canal de parole est
  désactivé et le braille continue de fonctionner ;
* BrlAPI est connecté en arrière-plan et réessayé toutes les 5 secondes
  quand BRLTTY est indisponible, refuse l'autorisation ou est redémarré ;
  pendant ce temps, les cellules braille sont seulement journalisées et la
  taille donnée par `--cells`/`--rows` est annoncée, puis la vraie taille est
  transmise au NVDA distant dès qu'elle est connue.

`--no-speech` ou `--no-braille` désactivent entièrement un canal.

## Paquets des distributions

Arch Linux et Debian ont quatre paquets : `nvda-rdp-common`,
`nvda-rdp-freerdp-plugin`, `nvda-rdp-client` et `nvda-rdp-server` (`-git`
sous Arch). Le client a besoin des trois premiers :

* Arch Linux : `nvda-rdp-client-git` depuis l'AUR, construit à partir de
  `packaging/arch/PKGBUILD` ; il remplace l'ancien paquet unique
  `nvda-rdp-linux-git`. Le module Python de BrlAPI est dans `python-brltty`
  et `pw-play` dans `pipewire-audio` ;
* Debian forky (testing), amd64 : les fichiers `.deb` d'une version
  publiée :

  ```bash
  sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-freerdp-plugin_*.deb \
    ./nvda-rdp-client_*.deb
  ```

Le greffon est installé dans le répertoire des greffons du FreeRDP du
système : le `xfreerdp3` et le Remmina de la distribution le trouvent sans
modification. Il est construit pour une version précise de FreeRDP 3 et doit
être reconstruit quand la distribution met FreeRDP à jour. Chaque
utilisateur active ensuite le service :

```bash
systemctl --user enable --now nvda-rdp-client.service
```

Construction des paquets, tests en VM, publication, et installation sur
d'autres distributions : [docs/packaging.fr.md](docs/packaging.fr.md).

### Arch Linux : expiration de la connexion avec NLA

Le paquet `krb5` d'Arch installe un `/etc/krb5.conf` d'exemple dont le
domaine par défaut est `ATHENA.MIT.EDU`. FreeRDP essaie d'abord Kerberos
pour NLA, attend le KDC de ce domaine sur Internet, et l'hôte Windows
abandonne le premier : la connexion échoue après environ une minute, avec
`Cannot contact any KDC for realm 'ATHENA.MIT.EDU'` puis `NLA begin failed`
dans le journal du client (celui de Remmina est dans le journal de
l'utilisateur). Cela ne dépend pas de ce projet. Au choix :

* mettre en commentaire `default_realm` dans `/etc/krb5.conf` (ou y mettre
  votre vrai domaine) quand l'ordinateur n'utilise pas Kerberos ;
* ou, pour une seule connexion, sauter Kerberos : `/auth-pkg-list:!kerberos`
  pour `xfreerdp3`, ou lancer Remmina avec `KRB5_CONFIG=/dev/null remmina`.

## Utiliser Remmina

Remmina est utilisé sans modification. FreeRDP ne charge les greffons de
canaux que depuis son propre répertoire d'installation : Remmina doit donc
être construit avec la variante de FreeRDP qui contient le greffon. La flake
la fournit :

```bash
nix run .#remmina-with-nvda-rdp
```

ou l'installer avec `nix profile install .#remmina-with-nvda-rdp`. Sous Arch
et Debian, le paquet du greffon installe `libnvda-rdp-dvc-client.so` dans le
répertoire des greffons du FreeRDP du système (`/usr/lib/freerdp3/`,
`/usr/lib/x86_64-linux-gnu/freerdp3/`), que le Remmina de la distribution
utilise aussi. Les tests en VM le vérifient avec `xfreerdp3` ; le Remmina
d'Arch Linux a aussi servi en vraie session.

Dans le profil de connexion RDP, onglet Avancé, mettre dans « Dynamic
virtual channel » (canal virtuel dynamique) :

```text
nvda-rdp-dvc
```

C'est l'équivalent de `/dvc:nvda-rdp-dvc` pour `xfreerdp` (clé `dvc` du
fichier `.remmina`). L'audio, la disposition du clavier et le plein écran se
règlent dans le profil Remmina. Le client doit tourner avant la connexion,
de préférence comme service utilisateur (voir plus haut).

L'affichage braille distant suit le focus des fenêtres dont le nom
d'application ou de fenêtre contient `freerdp` ou `remmina` (voir
`--focus-application`). La fenêtre principale de Remmina, avec la liste des
connexions, correspond aussi ; vérifiez avec `--log-level DEBUG` ce
qu'AT-SPI indique pour les fenêtres de Remmina.

## Lancement manuel pour comprendre les composants

Le lanceur automatique est recommandé pour l'usage normal. Pour observer
chaque couche séparément, ouvrez deux terminaux.

Premier terminal :

```bash
nix develop
python -m nvda_rdp_linux --log-level DEBUG
```

Le client doit indiquer qu'il écoute sur :

```text
$XDG_RUNTIME_DIR/nvda-rdp-linux/speech.sock
$XDG_RUNTIME_DIR/nvda-rdp-linux/braille.sock
```

Il se connecte au démon BRLTTY par BrlAPI sans chemin `brlapi.key` explicite ;
l'autorisation est confiée à la configuration Polkit déjà présente sur la
machine. Avec `--braille-capture`, BrlAPI n'est pas utilisé et les cellules
braille sont seulement journalisées ; le nombre de cellules virtuelles se
change avec `--cells`.

Chaque ligne du journal nomme son thread : `speech`, `braille`,
`brlapi-connection`, `brlapi-keys` ou `MainThread`.

Second terminal :

```bash
nix develop
xfreerdp \
  /v:windows-remote \
  /u:username \
  /d:workgroup \
  /dvc:nvda-rdp-dvc \
  /from-stdin:force \
  /cert:tofu \
  /sound:sys:pulse \
  /kbd:layout:0x0000040C \
  +f \
  +grab-keyboard \
  /log-level:INFO \
  /log-filters:com.freerdp.channels.drdynvc.client:TRACE
```

Le client Python doit être démarré avant FreeRDP. Si une socket est
indisponible, le greffon continue d'afficher les blocs du DVC correspondant
pour le diagnostic, mais aucune réponse de négociation du client ne sera
renvoyée sur ce canal.

## Régler la voix

Valeurs par défaut :

```text
module: espeak-ng
language: from the locale (LC_ALL, then LC_MESSAGES, then LANG), else en
Orca rate: 75
```

Soit : module `espeak-ng` ; langue d'après la locale (`LC_ALL`, puis
`LC_MESSAGES`, puis `LANG`), sinon `en` ; débit 75 sur l'échelle d'Orca.

La langue est annoncée au NVDA distant comme celle du synthétiseur local,
comme RDAccess le fait avec `synth.language` ; le NVDA distant s'en sert pour
le changement automatique de langue, le traitement des symboles et la
description des caractères. Elle choisit aussi la langue des annonces de
connexion. La langue par défaut de Speech Dispatcher n'est pas utilisée, car
les lecteurs d'écran comme Orca règlent leur voix pour chaque connexion et le
réglage global reste souvent `en-US`. Orca lui-même se rabat sur la locale
quand aucune langue de voix n'est configurée.

Pour expérimenter à la main :

```bash
python -m nvda_rdp_linux --language fr_FR --rate 70 --output-module espeak-ng
```

Le débit utilise l'échelle 0–100 d'Orca. La conversion vers SSIP est :

```text
SSIP = 2 × Orca rate − 100
```

Ainsi, Orca 70 correspond à SSIP +40, et Orca 75 à SSIP +50. La hauteur et
le volume utilisent la même conversion.

Autres options :

```text
--pitch-change N        pitch offset added to remote speech, like RDAccess's
                        "incoming speech pitch change" (default 10, 0 disables)
--enable-pause          honour pause_speech (off by default: sd_espeak-ng
                        repeated the sentence on resume)
--settings-management   let the remote NVDA read and change the local voice,
                        rate, pitch and volume (RDAccess "driver settings
                        management"; off by default, as upstream)
--nvda-waves-dir DIR    play NVDA sounds such as waves\error.wav from DIR
--audio-player CMD      player fed with a WAV file on stdin (default "pw-play -")
--notify MODE           none, message, sound or both (default message)
--keep-speech-on-blur   keep remote speech going when the RDP client loses
                        focus (default: stop it, as NVDA's cancel would)
```

* `--pitch-change N` : décalage de hauteur ajouté à la parole distante, comme
  le « changement de hauteur de la parole entrante » de RDAccess (10 par
  défaut, 0 le désactive) ;
* `--enable-pause` : respecter `pause_speech` (désactivé par défaut :
  `sd_espeak-ng` répétait la phrase à la reprise) ;
* `--settings-management` : laisser le NVDA distant lire et modifier la voix,
  le débit, la hauteur et le volume locaux (« gestion des paramètres du
  pilote » de RDAccess ; désactivé par défaut, comme chez RDAccess) ;
* `--nvda-waves-dir DIR` : jouer depuis `DIR` les sons de NVDA comme
  `waves\error.wav` ;
* `--audio-player CMD` : lecteur qui reçoit un fichier WAV sur son entrée
  standard (par défaut `pw-play -`) ;
* `--notify MODE` : `none`, `message`, `sound` ou `both` (par défaut
  `message`) ;
* `--keep-speech-on-blur` : laisser continuer la parole distante quand le
  client RDP perd le focus (par défaut, elle est arrêtée, comme le ferait une
  annulation de NVDA).

## Sons de NVDA

Quand le synthétiseur distant de RDAccess est actif, le NVDA distant ne joue
pas lui-même ses bips et ses sons : il les envoie sous forme de messages
`tone` et `wave`. Les bips sont générés localement. Les fichiers WAV sont
désignés par leur chemin dans le répertoire du programme NVDA (par exemple
`waves\error.wav`) ; le côté Linux a donc besoin d'une copie du répertoire
`waves` de NVDA, donnée par `--nvda-waves-dir` ou `NVDA_WAVES_DIR`. Le
lanceur utilise `nvda-waves/` à la racine du dépôt s'il existe (ignoré par
Git). Seuls les fichiers de ce répertoire sont joués ; les sons des autres
extensions sont refusés et journalisés.

## Options braille

```text
--braille-capture        only log braille cells, do not use BrlAPI
--cells N, --rows N      size advertised until BrlAPI answers (default 40x1)
--no-braille-input       do not forward display keys to the remote NVDA
--sync-caps-lock         align the local Caps Lock with the remote NVDA when
                         the RDP client loses focus (X11 tools; uncertain on
                         Wayland)
--brlapi-priority N      BrlAPI priority while focused (default 80, above
                         Orca's 70 in flat review)
--focus-application NAME RDP client window/application name, repeatable
                         (default: freerdp and remmina)
```

* `--braille-capture` : seulement journaliser les cellules braille, ne pas
  utiliser BrlAPI ;
* `--cells N`, `--rows N` : taille annoncée jusqu'à la réponse de BrlAPI
  (40×1 par défaut) ;
* `--no-braille-input` : ne pas transmettre les touches de la plage au NVDA
  distant ;
* `--brlapi-priority N` : priorité BrlAPI tant que le client RDP a le focus
  (80 par défaut, au-dessus du 70 d'Orca en revue à plat) ;
* `--sync-caps-lock` : aligner le verrouillage des majuscules local sur celui
  du NVDA distant quand le client RDP perd le focus (outils X11 ; incertain
  sous Wayland) ;
* `--focus-application NOM` : nom de fenêtre ou d'application du client RDP,
  répétable (par défaut : `freerdp` et `remmina`).

Les annonces de connexion en braille ne sont données que lorsqu'une plage
braille est connectée par BrlAPI.

Les touches de la plage sont lues comme des commandes BRLTTY tant que FreeRDP
a le focus, et envoyées au NVDA distant exactement comme le fait le pilote
`brltty` de NVDA (`br(brltty):fwinlt`, `br(brltty):route`, ...). Le NVDA
distant reçoit la table de gestes `brltty` standard de NVDA : le défilement,
la navigation par ligne et le routage fonctionnent sans configuration. Les
points du clavier braille (`passdots`) sont envoyés comme saisie braille et
traduits par la table de saisie du NVDA distant.

## Lire les journaux

Le lanceur complet produit deux journaux principaux :

```text
logs/rdaccess-speech-session.log
logs/nvda-rdp-client.log
```

Le premier contient les messages de FreeRDP, de drdynvc et du greffon C. Le
second contient ceux du client : protocole de parole, Speech Dispatcher,
négociation braille, cellules et gestes. Avec le service utilisateur, le
journal du client est dans le journal système
(`journalctl --user -u nvda-rdp-client`).

Aux niveaux par défaut, les journaux restent courts et ne contiennent aucun
contenu. Les lignes par bloc du greffon (`received`, `forwarded`)
n'apparaissent qu'avec `NVDA_RDP_DVC_DEBUG=1` dans l'environnement du client
RDP, et le texte dit, les cellules braille et les gestes du client qu'avec
`--log-level DEBUG` (pour le lanceur :
`RDACCESS_CLIENT_OPTIONS="--log-level DEBUG"`). Les vérifications ci-dessous
demandent les deux ; gardez ces journaux privés.

Suivre les deux journaux pendant une session :

```bash
tail -F logs/rdaccess-speech-session.log logs/nvda-rdp-client.log
```

Vérifier rapidement la négociation et l'IPC :

```bash
rg -n 'NVDA-SPEECH|connected IPC|delegated client|forwarded .* IPC|failed' \
  logs/rdaccess-speech-session.log
```

Sortie attendue quand tout va bien :

```text
connected IPC socket .../nvda-rdp-linux/speech.sock
received 22 bytes
forwarded 22 IPC bytes to DVC
received 69 bytes
received 55 bytes
forwarded 68 IPC bytes to DVC
```

Vérifier le protocole et le chemin de la parole :

```bash
rg -n 'sent legacy speech|sent speech attribute|server speech|submitting speech|accepted speak|accepted cancel|playing|unsupported' \
  logs/nvda-rdp-client.log
```

Sortie typique quand tout va bien :

```text
sent legacy speech protocolVersion=2
sent speech attribute language='fr_FR'
server speech rdAccessVersion='2.0.5'
submitting speech text='Bureau  fenêtre'
Speech Dispatcher accepted speak character_mode=False text='Bureau  fenêtre'
Speech Dispatcher accepted cancel
```

Vérifier le canal braille :

```bash
rg -n '\(braille\)|\(brlapi' logs/nvda-rdp-client.log
```

Une session saine indique `numRows=1`, `numCols=40`, confirme le canal JSON
`NVDA-BRAILLE`, puis affiche chaque tableau de 40 cellules en hexadécimal.

Une ligne `accepted speak` prouve que Speech Dispatcher a accepté le texte. La
sortie audio réelle doit encore être confirmée à l'écoute.

Les captures figées des tests réussis portent d'autres noms pour ne pas être
écrasées, notamment :

```text
logs/rdaccess-client-python-handshake.log
logs/rdaccess-speech-python-handshake.log
logs/rdaccess-client-stable-final.log
logs/rdaccess-speech-stable-final.log
```

## Examiner les captures

Les captures minimales sont rangées sous :

```text
tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/
```

Chaque fichier `.hex` contient des octets réellement observés ou produits par
le chemin exact testé. Pour afficher une capture en données brutes :

```bash
xxd -r -p tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/first-speak-v2-jsonl.hex
```

Pour compter ses octets :

```bash
xxd -r -p tests/fixtures/rdaccess-v2-2da84e8496519cf23614c0cfb3ace61ee0f9cd95/first-speak-v2-jsonl.hex | wc -c
```

## Clavier et fenêtre FreeRDP

* `Ctrl+Alt+Entrée` : basculer le plein écran ;
* `Ctrl` droit : libérer la capture du clavier par FreeRDP ;
* `Super+M` : envoyer Windows+M quand le clavier est capturé ;
* appuyer sur `Ctrl` pendant une annonce de NVDA provoque un message
  `cancel` et interrompt la parole locale.

Si la disposition du clavier est encore incorrecte, vérifiez d'abord que le
script contient :

```text
/kbd:layout:0x0000040C
```

Lister les dispositions de clavier connues de FreeRDP :

```bash
nix develop --command xfreerdp /list:kbd
```

## Où faire les modifications

Pour expérimenter sans mélanger les responsabilités :

* transport DVC ou connexion des sockets : `freerdp-plugin/nvda_rdp_dvc.c` ;
* trames IPC : `nvda_rdp_linux/common/framing.py` et
  `nvda_rdp_linux/client/ipc.py` ;
* format partagé avec le serveur : `nvda_rdp_linux/common/` ;
* négociation et attributs génériques : `nvda_rdp_linux/client/session.py` ;
* nouveaux messages ou commandes de parole : `nvda_rdp_linux/common/speech.py`
  et `nvda_rdp_linux/client/protocol.py` ;
* messages et gestes braille : `nvda_rdp_linux/common/braille.py` et
  `nvda_rdp_linux/client/braille.py` ;
* touches de la plage braille : `nvda_rdp_linux/client/brltty.py` ;
* bips et sons : `nvda_rdp_linux/client/audio.py` ;
* rendu par Speech Dispatcher : `nvda_rdp_linux/client/speech.py` ;
* options du client et démarrage des canaux : `nvda_rdp_linux/app.py` ;
* options de FreeRDP : `scripts/run-rdaccess-speech-session` ;
* cas reproductibles : d'abord une capture, puis un test sous
  `tests/client/` ou `tests/common/`, et un scénario golden pour le
  comportement vu de l'extérieur.

Après une modification, procéder dans cet ordre :

```text
fixture or observation
unit test
minimal modification
pytest and lint
C build if necessary
real RDP test
compare the FreeRDP and client logs
```

Soit : capture ou observation ; test unitaire ; modification minimale ;
pytest et lint ; construction C si nécessaire ; vrai test RDP ; comparaison
des journaux de FreeRDP et du client.

## Limites actuelles

* la gestion des paramètres du pilote, la synchronisation du verrouillage des
  majuscules, la saisie au clavier braille et `pause_speech` n'ont pas encore
  été confirmées en vraie session ;
* `pause_speech` est ignoré sans `--enable-pause`, car `sd_espeak-ng`
  répétait la phrase à la reprise ;
* les sons de NVDA nécessitent une copie locale du répertoire `waves` de
  NVDA ;
* la synchronisation du verrouillage des majuscules repose sur des outils X11
  et peut ne pas fonctionner sous Wayland ;
* `timeSinceInput` n'est pas utilisé : c'est le focus AT-SPI qui décide quand
  la sortie distante possède la plage braille ;
* pas de compatibilité avec RDAccess 1.x (ses données historiques sont
  sérialisées par pickle et ne sont délibérément jamais décodées) ;
* le client ne s'intègre pas à l'Orca local : le braille distant prend la
  plage tant que la fenêtre RDP a le focus, puis la rend.

## Règle des tests assistés

Avant de commencer une session de diagnostic, convenir d'abord dans la
conversation des touches qui seront utilisées, du résultat audio attendu et
de la façon de quitter la session. Ne lancer
`scripts/run-rdaccess-speech-session` qu'après avoir confirmé explicitement
que la procédure est comprise.
