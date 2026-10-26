"""Faits du jeu : pydcs (aerodromes, pistes, parkings, radios) et beacons.lua (balises).

pydcs est importe (licence LGPL : import d'une bibliotheque, aucun code recopie).
"""
import importlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import geo

MATCH_MAX_M = 3000.0


class DataError(Exception):
    """Fait absent ou introuvable (arret du cadrage 8)."""


def load_terrain(theatre):
    import dcs.terrain.terrain as T
    mod = importlib.import_module(f'dcs.terrain.{geo.theatre_module(theatre)}')
    for obj in vars(mod).values():
        if isinstance(obj, type) and issubclass(obj, T.Terrain) and obj is not T.Terrain:
            return obj()
    raise DataError(f'pydcs : aucun terrain pour {theatre}')


def match_airport(terrain, x, z, max_m=MATCH_MAX_M):
    """Aerodrome pydcs dont l'ARP est le plus proche de (x, z), a moins de `max_m` (sinon None)."""
    best, best_d = None, None
    for ap in terrain.airports.values():
        d = math.hypot(ap.position.x - x, ap.position.y - z)
        if best_d is None or d < best_d:
            best, best_d = ap, d
    if best is None or best_d > max_m:
        return None
    return best


@dataclass
class Beacon:
    id: str
    type: str                  # 'ILS_LOCALIZER', 'VOR_DME', 'AIRPORT_HOMER' ...
    callsign: str
    frequency: float           # Hz (NDB : Hz aussi, 395000.0)
    channel: int = None
    x: float = 0.0
    z: float = 0.0


_BLOCK = re.compile(r'beaconId\s*=\s*\'([^\']+)\'')


def parse_beacons_lua(path):
    """Blocs `beaconId = 'airfield<id>_<n>'` de beacons.lua ; retourne {beaconId: Beacon}."""
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    out = {}
    for blk in re.split(r'\n\t\{\n', text):
        m = _BLOCK.search(blk)
        if not m:
            continue

        def grab(pattern, cast=str):
            mm = re.search(pattern, blk)
            return cast(mm.group(1)) if mm else None
        typ = grab(r'type\s*=\s*BEACON_TYPE_([A-Z_0-9]+)')
        pos = re.search(r'position\s*=\s*\{\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\}', blk)
        if typ is None or pos is None:
            continue
        ch = grab(r'channel\s*=\s*(\d+)', int)
        out[m.group(1)] = Beacon(m.group(1), typ, grab(r"callsign\s*=\s*'([^']*)'") or '',
                                 grab(r'frequency\s*=\s*([-\d.eE+]+)', float) or 0.0, ch,
                                 float(pos.group(1)), float(pos.group(3)))
    return out


def airfield_beacons(beacons, airport_id):
    """Balises `airfield<id>_<n>` d'un aerodrome."""
    pre = f'airfield{airport_id}_'
    return [b for k, b in beacons.items() if k.startswith(pre)]


@dataclass
class Slot:
    name: str
    x: float
    z: float
    heli_only: bool
    airplanes: bool
    length: float = None
    width: float = None


@dataclass
class Approach:
    name: str                  # '01L'
    heading: float             # cap pydcs (degres)
    beacon_ids: list = field(default_factory=list)   # balises dont runway_side == name


@dataclass
class AirportFacts:
    id: int
    name: str
    x: float
    z: float
    atc: dict                  # vhf_high_hz, uhf_hz, vhf_low_hz, hf_hz ; None si pydcs n'a pas de radio de tour
    approaches: list
    slots: list
    rw_refs: list = field(default_factory=list)    # une entree par piste de pydcs : (Approach main, Approach opposite)


def airport_facts(ap):
    """Faits pydcs d'un aerodrome ; DataError si un fait attendu manque."""
    atc = getattr(ap, 'atc_radio', None)
    if atc is None:
        atc_d = None                                  # pas de radio de tour dans pydcs : le bandeau affiche des tirets (lot pistes-v1, 4.6)
    else:
        try:
            atc_d = {'vhf_high_hz': atc.vhf_high_hz, 'uhf_hz': atc.uhf_hz, 'vhf_low_hz': atc.vhf_low_hz, 'hf_hz': atc.hf_hz}
        except AttributeError as e:
            raise DataError(f"pydcs : atc_radio incomplet pour {ap.name} ({e})")
    aps, rw_refs = [], []
    for rw in ap.runways:
        pair = []
        for app in (rw.main, rw.opposite):
            ids = [b.id for b in app.beacons if getattr(b, 'runway_side', None) == app.name]
            pair.append(Approach(app.name, float(app.heading), ids))
        aps.extend(pair)
        rw_refs.append(tuple(pair))
    slots = []
    for s in ap.parking_slots:
        if s.slot_name is None:
            raise DataError(f'pydcs : slot_name absent ({ap.name})')
        slots.append(Slot(str(s.slot_name), s.position.x, s.position.y, bool(s.helicopter and not s.airplanes),
                          bool(s.airplanes), s.length, s.width))
    return AirportFacts(ap.id, ap.name, ap.position.x, ap.position.y, atc_d, aps, slots, rw_refs)


def natural_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', name)]
