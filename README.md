# Dreame UI

Application de bureau Linux pour piloter un robot aspirateur Dreame (testé pour le L40s Ultra AE),
portée depuis [retoucheroz/dreamehub](https://github.com/retoucheroz/dreamehub) (macOS, licence MIT).

L'interface HTML d'origine est conservée (traduite en français) ; le backend Swift est réécrit en Python.
Aucun root du robot n'est nécessaire : l'application passe par le cloud Dreame, comme l'app officielle.

## Lancer

```sh
git clone https://github.com/Danux-Be/dreamehub-linux.git
cd dreamehub-linux
python3 -m dreameui              # fenêtre (Qt WebEngine si dispo, sinon WebKitGTK)
python3 -m dreameui --read-only  # aucune commande envoyée au robot
python3 -m dreameui --browser    # dans le navigateur par défaut
./install-desktop.sh             # ajoute « Dreame UI » au menu des applications
```

Dépendances (Arch/CachyOS) : `python-requests python-cryptography python-gobject webkit2gtk-4.1 libsecret`.
Pour l'icône dans la barre système KDE : `sudo pacman -S python-pyqt6-webengine` (l'app l'utilise alors automatiquement ;
fermer la fenêtre la réduit dans la barre système).

À la connexion, choisis **Dreame Home** et la région de ton compte (en général **Europe**).
Les identifiants sont stockés dans le trousseau du système (KWallet / GNOME Keyring) si tu coches « Mémoriser ».

## Fonctions

| | |
|---|---|
| État, batterie, progression, station (eau, sac, détergent), consommables | ✅ |
| Démarrer / pause / arrêt / retour base / localiser / vidage / lavage & séchage des serpillières | ✅ |
| Aspiration, mode, débit d'eau, mode de lavage, lavage auto, eau chaude | ✅ (selon ce que l'appareil signale) |
| Carte 2D/3D en direct, trajet, obstacles + photos | ✅ |
| Historique de nettoyage et cartes associées | ✅ |
| Nettoyage par pièces / zone dessinée sur la carte | ✅ (ajout par rapport à dreamehub) |
| Packs vocaux officiels (toutes les langues du modèle) | ✅ |
| Consommables (% + heures, remise à zéro), statistiques | ✅ |
| Onglet Réglages : volume, Ne pas déranger, CleanGenius, humidité, température d'eau, séchage… | ✅ (selon ce que l'appareil signale) |
| Caméra en direct | ❌ flux Alibaba LinkVisual non documenté publiquement |
| Éditeur d'annonces, Tailscale, « aller à un point » | ❌ pas encore porté |

## Structure

- `dreameui/cloud.py` — client du cloud Dreame/MOVA (authentification, signature, RPC MiOT)
- `dreameui/device.py` — propriétés, commandes et réglages
- `dreameui/extras.py` — consommables, statistiques et réglages supplémentaires (plages bornées)
- `dreameui/voice.py` — catalogue officiel des packs vocaux
- `dreameui/mapdecoder.py`, `dreameui/maps.py` — décodage des cartes, historique, photos d'obstacles
- `dreameui/api.py`, `dreameui/server.py` — API `/api/*` servie sur 127.0.0.1 (jeton CSRF, contrôle Host)
- `dreameui/app.py` — fenêtre de bureau
- `dreameui/resources/` — interface ; `index.original.html` est la version amont, `tools/i18n/` la retraduit

Tests : `python3 -m unittest discover tests`

Logiciel indépendant et non officiel, sans lien avec Dreame ni MOVA. Licence MIT (voir `LICENSE`).
Les images produits MOVA (`dreameui/resources/assets/products/mova/`) sont des visuels tiers, hors licence MIT ;
voir leur `SOURCES.json`. Protocole issu de
[Tasshack/dreame-vacuum](https://github.com/Tasshack/dreame-vacuum) (MIT) ; voir `THIRD_PARTY_NOTICES.md`.
