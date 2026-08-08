# Captures RDAccess 2.0.0

Ces fixtures proviennent d'une session RDP réelle contre une VM Windows de test,
le 7 août 2026.

Référence exacte :

- RDAccess 2.0.0 ;
- commit `2da84e8496519cf23614c0cfb3ace61ee0f9cd95` ;
- canal `NVDA-SPEECH` ;
- SessionId Windows 5 ;
- FreeRDP 3.30.0 ;
- plugin Linux envoyant uniquement XON `0x11`.

Chaque fichier `.hex` contient, sans l'en-tête drdynvc, un bloc remis à `OnDataReceived`. Les espaces et la fin de ligne servent uniquement à rendre la fixture lisible.

Ordre observé :

1. `server-protocol-version-v1.hex`, 22 octets à 18:19:29.147 ;
2. `server-language-request-v1.hex`, 14 octets à 18:19:29.189 ;
3. `first-speak-v1.hex`, 96 octets à 18:19:29.262.

Un second test réel, dans la SessionId Windows 6, a ensuite négocié la version 2 :

1. `client-protocol-version-v1.hex`, 22 octets envoyés à 18:30:00.330 ;
2. `server-protocol-version-v2-jsonl.hex`, 69 octets reçus à 18:30:00.331 ;
3. `server-language-request-v2-jsonl.hex`, 55 octets reçus à 18:30:00.368 ;
4. `first-speak-v2-jsonl.hex`, 94 octets reçus à 18:30:00.445.

Un test ultérieur a déplacé les réponses vers le processus Python et observé :

- `client-language-v2-jsonl.hex`, 68 octets envoyés par Python et transmis au DVC ;
- confirmation du plugin : `forwarded 68 IPC bytes to DVC` ;
- réponse associée à la requête serveur `language`, valeur `fr`.

Le 8 août 2026, `character-mode-speak-v2-jsonl.hex` a été capturé pendant un
test réel. Il contient l'annonce « Quitter q », avec activation du mode caractère
avant `q`, puis retour au mode par défaut.

`pause-speech-v2-jsonl.hex` conserve le message réel `pause_speech` avec
`switch=true`, reçu le 7 août 2026 pendant le test du client Python.

`server-supported-commands-v2-jsonl.hex` conserve la requête réelle de l'attribut
`supportedCommands`, reçue à plusieurs reprises pendant les sessions v2.

Le 8 août 2026 à 06:21, le premier test du listener `NVDA-BRAILLE` a capturé
quatre blocs legacy réels : `braille-server-protocol-version-v1.hex`, puis les
requêtes `numRows`, `numCols` et `gestureMap`. Le client braille utilisateur
n'était pas encore lancé : ces blocs ont été reçus après XON mais aucune réponse
Linux ne leur a été envoyée.

Après correction de la transition legacy/JSON, le test de 06:35 a reçu de
nombreux messages `display` de 40 cellules. `first-braille-display-v2-jsonl.hex`
conserve le premier bloc JSON Lines exact de 159 octets reçu à 06:35:24.200.

Le handshake commence donc dans le framing legacy v1. Après l'annonce legacy
`protocolVersion=2` du client, le serveur bascule immédiatement vers JSON Lines.
Les fichiers JSON Lines incluent leur séparateur final LF `0a`.

Sources complètes :

- `logs/rdaccess-v2-2da84e8-session.log` ;
- journal NVDA de la session Windows v2 initiale ;
- `logs/rdaccess-v2-json-2da84e8-session6.log` ;
- journal NVDA de la session Windows JSON v2 ;
- `logs/rdaccess-client-python-handshake.log` ;
- `logs/rdaccess-speech-python-handshake.log`.
