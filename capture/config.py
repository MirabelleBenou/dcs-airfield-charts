"""Configuration de l'outil de capture : chemins et taille d'ecran propres a chaque PC.

Bibliotheque standard seulement, importable partout (les scripts de capture, eux, ne
s'importent que sous Windows).

Ordre de recherche du fichier JSON :
  1. option  --config <chemin>  (retiree de argv)
  2. variable d'environnement  DCS_CHARTS_CONFIG
  3. config.json dans le dossier de ce fichier (capture/config.json)

Cles : work_dir (obligatoire), dcs_install (obligatoire), screen (facultatif : [largeur, hauteur]),
annee (facultatif : annee de la declinaison magnetique du rendu, defaut 1985), osm_dir (facultatif : defaut <work_dir>/osm).
Modele : config.example.json.
"""
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

ENV_VAR = 'DCS_CHARTS_CONFIG'
OPTION = '--config'
DEFAULT_PATH = Path(__file__).resolve().parent / 'config.json'
EXAMPLE_NAME = 'capture/config.example.json'


class Config(NamedTuple):
    work_dir: Path                          # sync/, out/ (cree s'il n'existe pas)
    dcs_install: Path                       # dossier d'installation de DCS World
    terrains: Path                          # dcs_install / Mods / terrains (doit exister)
    screen: Optional[Tuple[int, int]]       # None : taille de l'ecran principal, lue par mss
    source: Path                            # fichier de configuration lu
    annee: float = 1985.0                   # rendu : annee de la declinaison magnetique (decimale acceptee)
    osm_dir: Optional[Path] = None          # rendu : noms de taxiways OSM (defaut : work_dir / 'osm')


def _fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _split_option(argv):
    """Retire --config <chemin> (ou --config=<chemin>) de argv. Retourne (chemin ou None, argv restant)."""
    rest, path, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == OPTION:
            if i + 1 >= len(argv):
                _fail(f'Option {OPTION} sans chemin. Modele : {EXAMPLE_NAME}.')
            path = argv[i + 1]
            i += 2
            continue
        if a.startswith(OPTION + '='):
            path = a[len(OPTION) + 1:]
            i += 1
            continue
        rest.append(a)
        i += 1
    return path, rest


def _path(value):
    return Path(value).expanduser()


def load_config(argv, environ=None, default_path=None):
    """Lit la configuration. Retourne (Config, argv sans l'option --config).

    environ et default_path n'existent que pour les tests.
    """
    environ = os.environ if environ is None else environ
    default_path = DEFAULT_PATH if default_path is None else Path(default_path)
    opt, rest = _split_option(list(argv))
    if opt:
        src = _path(opt)
    elif environ.get(ENV_VAR):
        src = _path(environ[ENV_VAR])
    else:
        src = default_path

    hint = f'Voir {EXAMPLE_NAME} (a copier en capture/config.json, ou a indiquer par {OPTION} ou {ENV_VAR}).'
    try:
        text = src.read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail(f'Fichier de configuration introuvable : {src}\n{hint}')
    except OSError as e:
        _fail(f'Fichier de configuration illisible : {src} ({e})\n{hint}')
    try:
        data = json.loads(text)
    except ValueError as e:
        _fail(f'Fichier de configuration invalide (JSON) : {src} ({e})\n{hint}')
    if not isinstance(data, dict):
        _fail(f'Fichier de configuration invalide : {src} doit contenir un objet JSON.\n{hint}')

    for key in ('work_dir', 'dcs_install'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            _fail(f'Fichier de configuration {src} : cle "{key}" manquante ou vide.\n{hint}')
    work_dir, dcs_install = _path(data['work_dir']), _path(data['dcs_install'])

    screen = None
    if data.get('screen') is not None:
        s = data['screen']
        if (not isinstance(s, list) or len(s) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in s)):
            _fail(f'Fichier de configuration {src} : "screen" doit etre [largeur, hauteur] en pixels.\n{hint}')
        screen = (s[0], s[1])

    annee = 1985.0
    if data.get('annee') is not None:
        a = data['annee']
        if isinstance(a, bool) or not isinstance(a, (int, float)) or not 1900 <= a <= 2100:
            _fail(f'Fichier de configuration {src} : "annee" doit etre un nombre (par exemple 1985 ou 1987.5).\n{hint}')
        annee = float(a)
    osm_dir = None
    if data.get('osm_dir') is not None:
        if not isinstance(data['osm_dir'], str) or not data['osm_dir'].strip():
            _fail(f'Fichier de configuration {src} : "osm_dir" doit etre un chemin.\n{hint}')
        osm_dir = _path(data['osm_dir'])

    terrains = dcs_install / 'Mods' / 'terrains'
    if not terrains.is_dir():
        _fail(f'Fichier de configuration {src} : dossier des terrains introuvable : {terrains}\n'
              f'("dcs_install" doit etre le dossier d\'installation de DCS World.)\n{hint}')
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _fail(f'Fichier de configuration {src} : "work_dir" inutilisable : {work_dir} ({e})\n{hint}')
    return Config(work_dir, dcs_install, terrains, screen, src, annee, osm_dir or work_dir / 'osm'), rest
