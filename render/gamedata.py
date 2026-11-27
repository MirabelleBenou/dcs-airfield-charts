"""Chargeur des faits du jeu : `render/data/game/<Theatre>.json` (format `dcs-airfields-v1`, releve dans l'editeur de mission
de DCS et importe par `tools/import_gamedata.py`).

`load_terrain(theatre)` rend un objet qui offre ce que le code lisait de pydcs, avec les memes noms d'attributs :
`airports` (nom -> aerodrome, par id croissant), `bounds` (`top`, `left`, `bottom`, `right`), et pour chaque aerodrome
`id`, `name`, `position.x`, `position.y` (y = le z du jeu), `runways` (dans l'ordre de pydcs : indices 1, 2, ..., n-1 de
l'editeur, puis 0), `atc_radio`, `parking_slots` (par `crossroad_index`).
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from . import geo

GAME_DIR = Path(__file__).resolve().parent / 'data' / 'game'
FORMAT = 'dcs-airfields-v1'

# nom de theatre reconnu par geo.theatre_module -> nom du dossier du theatre dans le jeu (et du fichier)
GAME_THEATRES = {'germany': 'GermanyColdWar', 'syria': 'Syria', 'caucasus': 'Caucasus'}

# Bornes des theatres (x, z du jeu), de pydcs 84f6335 (LGPL-3.0) : les valeurs seules, aucun code recopie.
BOUNDS = {
    'GermanyColdWar': (200000, -600000, -1100000, -300000),
    'Syria': (-320000, -579986, 300000, 579998),
    'Caucasus': (380000, -560000, -600000, 1130000),
}

_HEAD_DIGITS = re.compile(r'^(\d+)')


class Point:
    __slots__ = ('x', 'y')

    def __init__(self, x, y):
        self.x = x
        self.y = y


class Bounds:
    def __init__(self, top, left, bottom, right):
        self.top, self.left, self.bottom, self.right = top, left, bottom, right


class Beacon:
    __slots__ = ('id',)

    def __init__(self, bid):
        self.id = bid


class RunwayEnd:
    def __init__(self, name, heading, beacons):
        self.name = name
        self.heading = heading
        self.beacons = beacons


class Runway:
    def __init__(self, rid, name, main, opposite):
        self.id = rid
        self.name = name
        self.main = main
        self.opposite = opposite


class AtcRadio:
    def __init__(self, vhf_low_hz, vhf_high_hz, uhf_hz, hf_hz):
        self.vhf_low_hz = vhf_low_hz
        self.vhf_high_hz = vhf_high_hz
        self.uhf_hz = uhf_hz
        self.hf_hz = hf_hz


class ParkingSlot:
    def __init__(self, slot_name, crossroad_idx, position, helicopter, airplanes, length, width, height):
        self.slot_name = slot_name
        self.crossroad_idx = crossroad_idx
        self.position = position
        self.helicopter = helicopter
        self.airplanes = airplanes
        self.length = length
        self.width = width
        self.height = height


class Airport:
    def __init__(self, aid, name, position, runways, atc_radio, parking_slots):
        self.id = aid
        self.name = name
        self.position = position
        self.runways = runways
        self.atc_radio = atc_radio
        self.parking_slots = parking_slots

    def __repr__(self):
        return f'Airport({self.name})'


class Terrain:
    def __init__(self, name, dcs_version, airports, bounds):
        self.name = name                    # nom du theatre dans le jeu (GermanyColdWar, Syria, Caucasus)
        self.dcs_version = dcs_version
        self.airports = airports
        self.bounds = bounds


def _error(msg):
    from .dcsdata import DataError
    return DataError(msg)


def game_theatre(theatre):
    """Nom du theatre dans le jeu (GermanyColdWar, Syria, Caucasus) d'un nom qu'accepte `geo.theatre_module` ; DataError
    sinon."""
    name = GAME_THEATRES.get(geo.theatre_module(str(theatre)))
    if name is None:
        raise _error(f'le jeu : aucun fichier de faits pour le theatre {theatre} (GermanyColdWar, Syria ou Caucasus)')
    return name


@lru_cache(maxsize=None)
def _document(name):
    path = GAME_DIR / f'{name}.json'
    if not path.is_file():
        raise _error(f'le jeu : {path} absent (theatre {name})')
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('format') != FORMAT or doc.get('theatre') != name:
        raise _error(f'le jeu : {path} n\'est pas un fichier {FORMAT} de {name}')
    return doc


def heading(end_name):
    """Cap d'un bout de piste : les chiffres de tete de son nom x 10 ('05' -> 50, '27L' -> 270)."""
    m = _HEAD_DIGITS.match(end_name)
    if not m:
        raise _error(f'le jeu : bout de piste sans numero ({end_name!r})')
    return int(m.group(1)) * 10


def pydcs_order(items):
    """Ordre de parcours Lua d'une table indicee depuis 0 (celui de pydcs) : 1, 2, ..., n-1, puis 0."""
    return list(items[1:]) + list(items[:1])


def _airport(a):
    beacons = pydcs_order(a['beacons'])
    runways = []
    for r in pydcs_order(a['runways']):
        rid = r['id']

        def end(side, rid=rid):
            # une piste d'id -1 n'a aucune balise (regle de pydcs)
            bs = [] if rid == -1 else [Beacon(b['id']) for b in beacons if b['runway_id'] == rid and b['side'] == side]
            return RunwayEnd(side, heading(side), bs)
        runways.append(Runway(None if rid == -1 else rid, r['name'], end(r['start']), end(r['end'])))
    atc = a['atc']
    radio = None if atc is None else AtcRadio(atc['vhf_low_hz'], atc['vhf_high_hz'], atc['uhf_hz'], atc['hf_hz'])
    slots = [ParkingSlot(s['name'], s['crossroad_index'], Point(float(s['x']), float(s['z'])), bool(s['heli']),
                         bool(s['plane']), float(s['length']), float(s['width']),
                         None if s['height'] is None else float(s['height']))
             for s in sorted(a['stands'], key=lambda s: s['crossroad_index'])]
    return Airport(a['id'], a['name'], Point(float(a['x']), float(a['z'])), runways, radio, slots)


def load_terrain(theatre):
    """Terrain du theatre (objets neufs a chaque appel) ; DataError pour un theatre hors des trois ou un fichier absent."""
    name = game_theatre(theatre)
    doc = _document(name)
    airports = {}
    for a in sorted(doc['airdromes'], key=lambda a: a['id']):
        if a['name'] in airports:
            raise _error(f'le jeu : deux aerodromes nommes {a["name"]} ({name})')
        airports[a['name']] = _airport(a)
    return Terrain(name, doc['dcs_version'], airports, Bounds(*BOUNDS[name]))
