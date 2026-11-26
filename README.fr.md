# NVDA RDP Linux

Traduction française de [README.md](README.md), qui fait référence.

NVDA RDP Linux apporte à Linux
[RDAccess](https://github.com/LeonarddeR/rdAccess), l'extension de NVDA qui
envoie la parole et le braille par les canaux virtuels dynamiques RDP, des
deux côtés d'une connexion RDP :

* **client** : un utilisateur aveugle sous Linux travaille dans un bureau à
  distance Windows avec le lecteur d'écran NVDA distant, en entend la parole
  et en lit le braille localement ;
* **serveur** : un utilisateur aveugle sous Windows, avec NVDA et RDAccess,
  travaille dans une session GNOME d'un ordinateur Linux atteint en RDP ; il
  entend Orca par le synthétiseur de NVDA et lit le braille d'Orca sur la
  plage de NVDA.

## Client

Il a deux parties :

* un petit greffon FreeRDP (`nvda-rdp-dvc`) qui ne fait que transporter les
  canaux `NVDA-SPEECH` et `NVDA-BRAILLE` vers des sockets Unix locales ;
* un client Python (`nvda-rdp-client`) qui implémente le protocole RDAccess
  et le restitue avec Speech Dispatcher, PipeWire et BRLTTY.

`xfreerdp` comme Remmina conviennent, pourvu qu'ils utilisent un FreeRDP qui
contient le greffon.

### Fonctionnalités

* parole du NVDA distant par Speech Dispatcher, avec les index (lecture
  continue), le mode caractère, les changements de langue, de hauteur, de
  débit et de volume, et les pauses ;
* bips et sons de NVDA joués localement (depuis une copie du répertoire
  `waves` de NVDA) ;
* braille distant sur la plage locale de BRLTTY tant que la fenêtre RDP a le
  focus, rendu à Orca dès qu'elle le perd ; la priorité BrlAPI (80 par
  défaut, `--brlapi-priority`) reste au-dessus de celle d'Orca, revue à plat
  comprise ;
* parole distante arrêtée quand la fenêtre RDP perd le focus, pour ne pas
  parler par-dessus Orca (`--keep-speech-on-blur` la laisse continuer) ;
* touches de la plage et touches de routage renvoyées au NVDA distant, avec
  la table de gestes BRLTTY standard de NVDA ; saisie au clavier braille ;
* en option : réglage à distance de la voix locale depuis NVDA,
  synchronisation du verrouillage des majuscules, annonces de connexion ;
* un seul service utilisateur pour les deux canaux, qui continue de
  fonctionner si Speech Dispatcher ou BRLTTY est indisponible et se reconnecte
  seul à BRLTTY ; le greffon se reconnecte au service quand celui-ci redémarre
  pendant une session.

Les journaux restent courts au niveau `INFO` par défaut. Le texte dit, les
cellules braille et les gestes ne sont journalisés qu'avec
`--log-level DEBUG` ; le greffon ne vide chaque bloc DVC que si
`NVDA_RDP_DVC_DEBUG` est défini dans l'environnement du client RDP.

La langue de la voix et des messages suit la locale (`LC_ALL`,
`LC_MESSAGES`, `LANG`), sauf si `--language` est donné.

### État

Expérimental. De vraies sessions RDP avec RDAccess 2.0.4 (NVDA 2026.2) ont
confirmé la parole, l'affichage braille, les touches de la plage et le
routage, les sons de NVDA, Remmina et le service client unique ; le client
empaqueté a aussi été vérifié en vraie session sous Arch Linux et Debian
forky. La gestion
des paramètres du pilote, la synchronisation du verrouillage des majuscules,
la saisie au clavier braille et `pause_speech` sont implémentées et couvertes
par des tests unitaires, mais pas encore confirmées en vraie session.
RDAccess 1.x n'est pas pris en charge.

### Utilisation

Activez le client et, dans votre client RDP, ajoutez le canal virtuel
dynamique `nvda-rdp-dvc` (`/dvc:nvda-rdp-dvc` pour `xfreerdp3`, « Dynamic
virtual channel » dans les réglages avancés de Remmina) :

```bash
systemctl --user enable --now nvda-rdp-client.service
```

Voir [run.fr.md](run.fr.md) pour l'architecture, la configuration, les tests
et le diagnostic, ainsi que `man nvda-rdp-client`.

## Serveur

Orca n'est pas modifié. Une bibliothèque chargée dans un GNOME Remote Desktop
non modifié présente les deux canaux de NVDA comme des sockets Unix. Dans les
sessions à distance seulement, une instance de Speech Dispatcher et un
serveur BrlAPI propres à la session envoient à NVDA la parole et le braille
d'Orca, et renvoient à Orca les touches de la plage. L'écran de connexion à
distance de GDM est servi lui aussi. Les sessions locales gardent leur parole
et leur braille habituels, et rien ne change côté Windows.

Il faut GNOME 49 ou plus récent (GDM, GNOME Remote Desktop, Orca).

### État

Expérimental. Il vient de l'étude `draft-nvda-orca-rdp`. Depuis Windows,
avec NVDA, RDAccess et `mstsc`, elle a vérifié sous NixOS la parole avec la
lecture continue et les changements de voix d'Orca, l'affichage braille, les
touches de la plage, la saisie braille, la reconnexion et l'écran de
connexion à distance. Dans ce dépôt, les tests système dans des VM NixOS,
Arch Linux et Debian forky vérifient, de Linux à Linux, que la parole et le
braille d'Orca sur l'écran de connexion à distance parviennent au client et
que l'écran de connexion local n'est pas modifié. Des essais manuels depuis Windows, avec
NVDA, RDAccess et `mstsc`, braille compris, ont réussi avec les paquets de ce
dépôt sous NixOS (`services.nvda-rdp-server`), Arch Linux et Debian forky.
L'usage quotidien depuis la 0.4.0 a confirmé l'affichage et la saisie en
braille abrégé et les réglages de la plage braille de NVDA ; Verr. Maj. n'est
pas synchronisé.

Voir [docs/server.fr.md](docs/server.fr.md) et `man nvda-rdp-server`.

## Installation

| | Client | Serveur |
| --- | --- | --- |
| NixOS | `overlays.default` : `nvda-rdp-client`, `freerdp-with-nvda-rdp`, `remmina-with-nvda-rdp` | `nixosModules.server` : `services.nvda-rdp-server.enable` |
| Arch Linux (`makepkg`) | `nvda-rdp-client-git` | `nvda-rdp-server-git` |
| Debian forky | `nvda-rdp-common`, `nvda-rdp-freerdp-plugin`, `nvda-rdp-client` | `nvda-rdp-common`, `nvda-rdp-server` |

Sous Arch Linux, les paquets se construisent avec `makepkg` depuis
`packaging/arch/` ; les paquets Debian sont les fichiers `.deb` des versions
publiées. Debian 13 n'est pas pris en charge. Construction, tests
en VM, publication et autres distributions :
[docs/packaging.fr.md](docs/packaging.fr.md).

## Remerciements

Ce projet s'inspire du travail de
[Leonard de Ruijter sur RDAccess](https://github.com/LeonarddeR/rdAccess), et
s'est construit en l'étudiant. RDAccess fournit, côté NVDA, les fonctions
d'accessibilité à distance et le comportement du protocole qui ont rendu ce
projet possible. Leonard de Ruijter n'a pas participé à ce projet et n'en est
pas responsable.

## Note sur le développement

Ce projet a commencé comme une expérience personnelle pour répondre à un
besoin d'accessibilité. Je suis administrateur système plutôt que développeur
professionnel, et des outils de développement assistés par IA ont été
utilisés. J'ai néanmoins essayé de comprendre, de tester et de documenter
soigneusement l'implémentation, et de garder une conception et un code
propres et maintenables. Le projet reste un logiciel expérimental.

## Licence

Ce projet est sous licence publique générale GNU, version 2 ou ultérieure
(`GPL-2.0-or-later`). Voir [LICENSE](LICENSE).
