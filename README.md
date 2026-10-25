# DCS Airfield Charts

Plans d'aérodromes pour DCS World, tirés des données du jeu.

**État : outillage de capture.** La carte F10 de DCS (mode Alt) dessine tout le revêtement d'un aérodrome en aplat, ainsi que les bâtiments, les lignes de guidage et les numéros de parking. Ce dépôt contient l'outil qui capture cette carte, base par base, dans le repère terrain de DCS (`x` Nord, `z` Est, en mètres). La conversion en plan vectoriel est le chantier suivant.

## Contenu

| Chemin | Rôle |
|---|---|
| `capture/hook/ChartsCaptureHook.lua` | hook d'export DCS : petit serveur local (`127.0.0.1:47123`) qui place la caméra F10 à la verticale d'un point, axes alignés sur la grille DCS ; relâche la caméra après 5 s sans ordre |
| `capture/f10capture.py` | capture d'une base entière : emprise lue dans son `.rn4`, altitude caméra adaptée au terrain, étalonnage de l'échelle, grille d'images, mosaïque ; index d'un théâtre |
| `capture/f10probe.py` | la sonde d'origine (liaison, stabilisation, échelle, alignement) |

## Installation (Windows)

1. Copier `ChartsCaptureHook.lua` dans `Saved Games\DCS...\Scripts\ChartsCapture\`.
2. Ajouter en fin de `Saved Games\DCS...\Scripts\Export.lua` :
   `dofile(lfs.writedir()..[[Scripts\ChartsCapture\ChartsCaptureHook.lua]])`
3. Python 3.12+ avec `numpy`, `mss`, `pillow`.
4. Lancer une mission sur le théâtre voulu, ouvrir la F10 en mode Alt, masquer étiquettes et unités, DCS au premier plan ; puis `python -I capture/f10capture.py capture <Théâtre> <Base>`.

Les chemins (`ROOT`, `TERRAINS`) et la taille d'écran sont pour l'instant écrits en tête des scripts.

## Ce qu'on a mesuré

- Image alignée sur la grille DCS (écart < 0,01°), raccords entre images ≤ 1 px, environ 1 s par image.
- Échelle : 0,33555 mm/px par mètre d'altitude caméra. **DCS maintient la caméra F10 à au moins ~500 m du sol** ; d'où l'altitude `max(1350, sol + 700)` et un étalonnage à chaque base.

## Licence

MIT — voir `LICENSE`.

## Remerciements

Le principe — piloter la caméra F10 depuis `Export.lua` et capturer l'écran — vient du générateur de cartes de [DCS Olympus](https://github.com/Pax1601/DCSOlympus). Aucun code n'en est repris.

DCS World est une marque d'Eagle Dynamics SA. Ce projet n'est ni fait ni soutenu par Eagle Dynamics.
