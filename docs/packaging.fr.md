# NVDA RDP Linux : empaquetage et tests

Traduction française de [packaging.md](packaging.md), qui fait référence.

Comment les paquets sont construits et testés pour NixOS, Arch Linux et
Debian, et comment ils sont publiés.

## Les quatre paquets

Une seule source donne quatre paquets, avec les mêmes noms sur toutes les
distributions (ceux d'Arch finissent par `-git`) :

| Paquet | Contenu | Architecture |
| --- | --- | --- |
| `nvda-rdp-common` | `nvda_rdp_linux/common/` : trames, format RDAccess, vocabulaire de la parole et du braille ; sans E/S, sans dépendances | toutes |
| `nvda-rdp-freerdp-plugin` | `libnvda-rdp-dvc-client.so`, dans le répertoire des greffons du FreeRDP du système | machine |
| `nvda-rdp-client` | `nvda-rdp-client`, son unité utilisateur, `nvda-rdp-client(1)` | toutes |
| `nvda-rdp-server` | bibliothèque des canaux pour GNOME Remote Desktop, les deux serveurs, les unités et les drop-ins, `nvda-rdp-server(7)` | machine |

Le client dépend du paquet commun et du greffon ; le serveur dépend
seulement du paquet commun. Le greffon est lié à la version de FreeRDP 3 avec
laquelle il a été construit, et doit être reconstruit quand la distribution
met FreeRDP à jour.

Les pages de manuel sont sous `man/`, leurs traductions françaises sous
`man/fr/`. Les fichiers communs aux paquets serveur de toutes les
distributions sont sous `packaging/server/` (voir son `README.md`) :
`install.sh` les installe avec les chemins de chaque distribution.

## Nix

| Sortie | Contenu |
| --- | --- |
| `packages.<system>.client` | `nvda-rdp-client` avec ses dépendances d'exécution, son unité utilisateur et sa page de manuel |
| `packages.<system>.freerdp-with-nvda-rdp` | FreeRDP contenant le greffon |
| `packages.<system>.remmina-with-nvda-rdp` | un Remmina non modifié construit avec ce FreeRDP |
| `packages.<system>.server`, `grd-preload` | le serveur et sa bibliothèque des canaux |
| `overlays.default` | les mêmes paquets, construits avec le nixpkgs de l'utilisateur |
| `overlays.brltty` | surcharge facultative de BRLTTY avec Polkit |
| `nixosModules.server` | `services.nvda-rdp-server` |
| `checks.<system>.server` | test système dans des VM NixOS |
| `apps.x86_64-linux.{build,test}-{arch,debian}` | constructions et tests dans des VM des distributions |

`nix flake check` construit les paquets et lance le test système NixOS. Une
flake ne voit que les fichiers connus de Git : ajoutez les nouveaux fichiers
(`git add -N` suffit) avant de construire.

## Constructions et tests dans des VM des distributions

```bash
nix run .#build-arch     # makepkg in a fresh Arch VM, writes dist/arch/
nix run .#test-arch      # installs dist/arch/ in two Arch VMs, system test
nix run .#build-debian   # dpkg-buildpackage in a fresh Debian forky VM, writes dist/debian/
nix run .#test-debian    # installs dist/debian/ in two Debian VMs, system test
```

`build-arch` lance `makepkg` dans une VM Arch neuve et écrit `dist/arch/` ;
`test-arch` installe `dist/arch/` dans deux VM Arch et lance le test système ;
`build-debian` et `test-debian` font de même avec `dpkg-buildpackage` et des
VM Debian forky, dans `dist/debian/`.

Ce sont des applications et non des dérivations, car pacman et apt ont besoin
du réseau. Elles ont besoin de KVM. Elles partent de l'arbre de travail du
dépôt tel qu'il est, modifications non commitées comprises.

* Images : les dernières images cloud officielles, non épinglées : le
  `cloudimg` d'Arch, l'image quotidienne `generic` de Debian forky
  (`genericcloud` n'a pas les pilotes DRM dont GDM a besoin). Chaque image
  est vérifiée contre l'empreinte SHA-256 (Arch) ou SHA-512 (Debian) publiée,
  et gardée en cache dans `~/.cache/nvda-rdp-linux/images/`. Chaque lancement
  part d'une copie neuve et la met à jour.
* Réseau : SSH n'est redirigé que sur 127.0.0.1. Les deux VM d'un test
  partagent un réseau privé par une socket QEMU sur 127.0.0.1 (192.168.77.1
  et .2). Une clé SSH jetable et une graine cloud-init sont créées à chaque
  lancement.
* Construction : la VM de construction lance toute la suite de tests
  (`check()`, ou `dh_auto_test`), puis `namcap` ou `lintian --pedantic`,
  dont les étiquettes sont à examiner, pas des erreurs. La construction Arch
  vérifie aussi que `.SRCINFO` correspond au PKGBUILD et écrit celui qu'elle
  génère dans `dist/arch/SRCINFO`.
* Test système (`nix/distro-vm/system_test.py`, le même scénario que
  `nix/tests/server.nix`) : une VM serveur avec GDM, l'ouverture de session à
  distance de GNOME Remote Desktop, Orca sur l'écran de connexion et
  `nvda-rdp-server` ; une VM client avec `xfreerdp3`, le greffon et
  `nvda-rdp-client`, dont le Speech Dispatcher enregistre ce qu'on lui
  demande de dire. Le client se connecte à l'écran de connexion à distance.
  Vérifications : l'écran de connexion local garde sa parole habituelle ;
  l'écran de connexion à distance utilise nvda-rdp ; la bibliothèque des
  canaux est chargée ; les deux canaux négocient la version 2 de RDAccess ;
  la parole d'Orca parvient au client ; le serveur apprend la taille de la
  plage du client et le braille d'Orca parvient au client, qui journalise les
  cellules (`--braille-capture`, au niveau DEBUG) au lieu d'utiliser BRLTTY.
* Journaux : `logs/<distribution>-<commande>-<date>.log`, plus la console de
  chaque VM, en texte.

Les touches et la saisie braille depuis la plage du client, les cas d'échec
et un vrai client Windows ne sont pas couverts par ces tests.

### Locales et tests ignorés

Les transcriptions golden du braille du serveur utilisent `fr_FR.UTF-8` (la
table d'Orca suit la locale). Elles sont ignorées, et non en échec, là où
cette locale n'est pas installée ; les VM de construction la génèrent. Sans
la locale, le serveur BrlAPI se rabat sur la locale C avec un avertissement,
comme Orca.

## Arch Linux : PKGBUILD

Le projet n'est pas sur l'AUR : les utilisateurs construisent eux-mêmes les
paquets avec `makepkg` depuis `packaging/arch/` d'un clone du dépôt :

```bash
git clone https://github.com/pcressant/nvda-rdp-linux.git
cd nvda-rdp-linux/packaging/arch
makepkg -s
sudo pacman -U nvda-rdp-common-git-*.pkg.tar.zst \
  nvda-rdp-freerdp-plugin-git-*.pkg.tar.zst \
  nvda-rdp-client-git-*.pkg.tar.zst    # client
sudo pacman -U nvda-rdp-common-git-*.pkg.tar.zst \
  nvda-rdp-server-git-*.pkg.tar.zst    # serveur
```

Le PKGBUILD construit la branche par défaut du dépôt GitHub
(`git+$url.git`), pas l'arbre de travail du clone. `nix run .#build-arch` ne
sert qu'à valider le PKGBUILD : il construit l'arbre de travail en
remplaçant la source du PKGBUILD par une copie locale. Quand le PKGBUILD
change, le lancer et copier `dist/arch/SRCINFO` vers
`packaging/arch/.SRCINFO` quand la construction le signale comme périmé,
puis pousser les modifications sur la branche par défaut.

`pkgver` est calculé à partir de Git : depuis la dernière étiquette
(`vX.Y.Z` donne `X.Y.Z.rN.gHASH`), ou, tant que le dépôt n'a pas
d'étiquette, `0.5.0.rN.gHASH` ; changer cette valeur de repli et `pkgver=`
ensemble. L'ancien paquet unique `nvda-rdp-linux-git` est remplacé par
`nvda-rdp-freerdp-plugin-git` et `nvda-rdp-client-git`.

Sous Arch, le module Python de BrlAPI est dans `python-brltty` et `pw-play`
dans `pipewire-audio`. Le `/etc/krb5.conf` d'exemple d'Arch fait expirer les
connexions NLA : voir « Arch Linux : expiration de la connexion avec NLA »
dans [run.fr.md](../run.fr.md).

## Debian : fichiers .deb

Les fichiers Debian sont dans `packaging/debian/` et sont copiés dans
`debian/` au moment de la construction. Le paquet source est natif
(`3.0 (native)`), construit avec debhelper 14 et dh-python ; les fichiers
Python sont installés par les fichiers `.install` dans
`/usr/lib/python3/dist-packages`.

Les paquets sont construits et testés sur Debian forky (testing), amd64 ;
sid devrait fonctionner mais n'est pas testé. Debian 13 n'est pas pris en
charge : son GNOME 48 ne peut pas accueillir le serveur (voir
[server.fr.md](server.fr.md)).

Pour publier :

1. commiter les modifications ; construire depuis un arbre de travail propre,
   puisque la construction prend aussi les modifications non commitées ;
2. ajouter une entrée à `packaging/debian/changelog` avec la nouvelle version
   (`dch` ou à la main) ;
3. lancer `nix run .#build-debian`, puis `nix run .#test-debian` ;
4. publier les quatre fichiers `.deb` de `dist/debian/`. Les paquets
   `-dbgsym` contiennent les symboles de débogage et n'ont pas besoin d'être
   publiés.

Pour les installer :

```bash
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-freerdp-plugin_*.deb \
  ./nvda-rdp-client_*.deb    # client
sudo apt install ./nvda-rdp-common_*.deb ./nvda-rdp-server_*.deb    # server
```

## Autres distributions

* greffon : `cmake -S freerdp-plugin -B build -DCMAKE_INSTALL_PREFIX=/usr`
  construit avec le FreeRDP 3 du système et installe dans le répertoire des
  greffons exporté par le paquet CMake de FreeRDP
  (`FreeRDP_RELATIVE_PLUGIN_DIR`, par exemple `lib/freerdp3`) ;
  `-DNVDA_RDP_PLUGIN_DIR=...` le remplace.
* bibliothèque des canaux : `cmake -S grd-preload -B build-grd-preload` avec
  les en-têtes de WinPR 3 ; elle ne se lie à aucune WinPR.
* Python : installer `nvda_rdp_linux/` (partie commune, plus `client/` avec
  `__main__.py` et `app.py`, ou `server/`) dans les site-packages du
  système ; `pyproject.toml` construit aussi une wheel du paquet entier, avec
  la commande `nvda-rdp-client` (Python 3.13 ou plus récent). Les dépendances
  d'exécution viennent des paquets du système : `speechd`, `brlapi`
  (BRLTTY), `pyatspi` avec la typelib AT-SPI, PyGObject et `pw-play` pour le
  client ; PyGObject, `louis` (liblouis) et `speechd` pour le serveur.
* client : `packaging/client/nvda-rdp-client.in` avec `@PYTHON@` remplacé, et
  `packaging/systemd/nvda-rdp-client.service` dans `/usr/lib/systemd/user/`.
  Les utilisateurs l'activent eux-mêmes.
* serveur : `packaging/server/install.sh` avec les chemins de la
  distribution.
