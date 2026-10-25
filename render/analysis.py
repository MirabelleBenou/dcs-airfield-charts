"""Analyse d'une base capturee : vectorisation + faits du jeu -> ChartData (tout ce qu'il faut pour dessiner)."""
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import dcsdata, geo, georef, naming, runways, vector

FT = 3.28084
Image.MAX_IMAGE_PIXELS = None
SHELTER_HALF_M, SHELTER_SHARE = 6.0, 0.35
THRESHOLD_ELEV_MAX_M, THRESHOLD_ELEV_N = 250.0, 5
OSM_TAXIWAY_REF = re.compile(r'^[A-Z]{1,2}[0-9]{0,2}$')
OSM_ON_PAVEMENT, OSM_PAVEMENT_GROW_M = 0.80, 5.0
BEACON_BANNER = {'TACAN': 'TACAN', 'VORTAC': 'VORTAC', 'VOR_DME': 'VOR/DME', 'VOR': 'VOR',
                 'AIRPORT_HOMER': 'NDB', 'AIRPORT_HOMER_WITH_MARKER': 'NDB', 'HOMER': 'NDB'}
BANNER_ORDER = ['TACAN', 'VORTAC', 'VOR/DME', 'VOR', 'NDB']


class ChartError(Exception):
    pass


@dataclass
class End:
    name: str
    az_grid: float             # sens d'atterrissage, azimut grille (deg)
    mag: float                 # cap magnetique (deg)
    pos: tuple                 # (x, z) du seuil (extremite de piste)
    rw: int
    thr_elev_ft: int = None
    ils: str = None


@dataclass
class Beacon:
    kind: str                  # 'TACAN', 'VORTAC', 'VOR/DME', 'VOR', 'NDB'
    callsign: str
    text: str                  # '88X', '111.20 CH49X', '390'
    x: float = 0.0
    z: float = 0.0


@dataclass
class Parking:
    name: str
    x: float
    z: float
    kind: str                  # 'open', 'shelter', 'heli'
    zone: int = -1


@dataclass
class ChartData:
    theatre: str
    base: str
    airport_name: str
    icao: str
    year: float
    lat: float
    lon: float
    arp: tuple
    elev_ft: int
    tower: dict                # {'vhf': .., 'uhf': .., 'fm': .., 'hf': ..} (MHz, texte 3 decimales)
    beacons: list              # Beacon
    decl: float
    conv: float
    runways: list              # runways.Runway
    ends: list                 # End, tries
    parkings: list
    georef: object
    pav_rings: list
    bld_rings: list
    masks: dict                # 'pav', 'bld' (booleens plein ecran) pour la rasterisation et les fenetres
    osm_ways: list             # [{'ref', 'pts_xz': ndarray}] taxiways gardes
    osm_kept: list             # noms gardes
    osm_icao: str = None
    projection: object = None
    n_shelter: int = 0


def load_rn4_points(folder, cfg, theatre, base):
    """Points .rn4 : rn4_points.json du dossier s'il existe, sinon lecture du .rn4 de l'installation DCS."""
    p = Path(folder) / 'rn4_points.json'
    if p.is_file():
        data = json.loads(p.read_text(encoding='utf-8'))
        return np.array([[q[1], q[2], q[3]] for q in data['points']], float)
    from . import f10import
    f10 = f10import.load()
    rn4 = Path(cfg.terrains) / theatre / 'AirfieldsTaxiways' / f'{base}.rn4'
    if not rn4.is_file():
        for q in (Path(cfg.terrains) / theatre / 'AirfieldsTaxiways').glob('*.rn4'):
            if q.stem.lower() == base.lower():
                rn4 = q
    if not rn4.is_file():
        raise ChartError(f'{base}.rn4 introuvable dans {Path(cfg.terrains) / theatre / "AirfieldsTaxiways"}')
    return f10.rn4_points(rn4)


def fmt_mhz(hz, nd=3):
    return f'{hz / 1e6:.{nd}f}'


def banner_beacons(blist):
    """Balises du bandeau (hors ILS), triees TACAN, VORTAC, VOR/DME, VOR, NDB."""
    out = []
    for b in blist:
        kind = BEACON_BANNER.get(b.type)
        if kind is None:
            continue
        if kind == 'TACAN':
            text = f'{b.channel}X'
        elif kind == 'NDB':
            text = f'{b.frequency / 1000:.0f}'
        else:
            text = f'{b.frequency / 1e6:.2f}' + (f' CH{b.channel}X' if b.channel else '')
        out.append(Beacon(kind, b.callsign, text, b.x, b.z))
    out.sort(key=lambda b: (BANNER_ORDER.index(b.kind), b.callsign))
    return out


def ils_text(blist, ids):
    """`<indicatif LOC> <MHz>` si un localizer est rattache a l'extremite ; ` (LOC)` sans glide ; sinon None."""
    by = [b for b in blist if b.id in ids]
    loc = [b for b in by if b.type in ('ILS_LOCALIZER', 'PRMG_LOCALIZER')]
    if not loc:
        return None
    gs = [b for b in by if b.type in ('ILS_GLIDESLOPE', 'PRMG_GLIDESLOPE')]
    s = f'{loc[0].callsign} {loc[0].frequency / 1e6:.2f}'
    return s if gs else s + ' (LOC)'


def shelter_flags(parkings_xz, bld, g):
    """Part de pixels batiment dans un carre de +-6 m autour de chaque parking."""
    half = SHELTER_HALF_M / g.mpp
    shares = []
    for x, z in parkings_xz:
        r, c = g.xz_to_rc(x, z)
        r0, r1 = int(round(r - half)), int(round(r + half)) + 1
        c0, c1 = int(round(c - half)), int(round(c + half)) + 1
        r0c, r1c, c0c, c1c = max(r0, 0), min(r1, bld.shape[0]), max(c0, 0), min(c1, bld.shape[1])
        if r1c <= r0c or c1c <= c0c:
            shares.append(0.0)
            continue
        shares.append(float(bld[r0c:r1c, c0c:c1c].mean()))
    return shares


def read_osm(path, proj, pav, g, mpp):
    """Fichier OSM de la base -> (icao, [taxiways gardes]). Garde un `taxiway` si ref [A-Z]{1,2}[0-9]{0,2} et
    >= 80 % de sa longueur sur le revetement elargi de 5 m."""
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    ways = []
    grown = None
    for w in d.get('ways', []):
        if w.get('aeroway') != 'taxiway' or not OSM_TAXIWAY_REF.match(w.get('ref', '')):
            continue
        pts = np.array([proj.to_xz(lat, lon) for lat, lon in w['pts']], float)
        if len(pts) < 2:
            continue
        if grown is None:
            grown = vector.distance_to_mask_m(pav, mpp) <= OSM_PAVEMENT_GROW_M
        tot, on = 0.0, 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            L = float(np.hypot(*(b - a)))
            n = max(2, int(math.ceil(L / 1.0)))
            t = (np.arange(n) + 0.5) / n
            xs, zs = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            r = np.rint((g.x_top - xs) / g.mpp - 0.5).astype(int)
            c = np.rint((zs - g.z_left) / g.mpp - 0.5).astype(int)
            ok = (r >= 0) & (r < grown.shape[0]) & (c >= 0) & (c < grown.shape[1])
            hit = np.zeros(n, bool)
            hit[ok] = grown[r[ok], c[ok]]
            tot += L
            on += L * hit.mean()
        if tot > 0 and on / tot >= OSM_ON_PAVEMENT:
            ways.append({'ref': w['ref'], 'pts_xz': pts, 'length': tot})
    return d.get('icao'), ways


def analyze(cfg, theatre, base, year=1985.0, icao=None, osm_path=None, osm_dir=None, log=print):
    out_root = Path(cfg.work_dir) / 'out' / theatre.lower()
    folder = out_root / base
    if not (folder / 'base.json').is_file():
        raise ChartError(f'base capturee introuvable : {folder}')
    bj = georef.load_base_json(folder)
    if bj.get('kind') == 'heliport':
        raise ChartError(f'{base} est une helistation : pas de chart d\'aerodrome')
    g = georef.georef_from_base(bj)
    img = np.asarray(Image.open(folder / 'mosaic.png').convert('RGB'))
    if img.shape[1] != g.width or img.shape[0] != g.height:
        raise ChartError(f'mosaique {img.shape[1]}x{img.shape[0]} differente de base.json {g.width}x{g.height}')

    pts = load_rn4_points(folder, cfg, theatre, base)
    if len(pts) == 0:
        raise ChartError('aucun point .rn4')
    center = ((pts[:, 0].min() + pts[:, 0].max()) / 2, (pts[:, 2].min() + pts[:, 2].max()) / 2)

    terrain = dcsdata.load_terrain(theatre)
    ap = dcsdata.match_airport(terrain, *center)
    if ap is None:
        raise ChartError(f'aucun aerodrome pydcs a moins de {dcsdata.MATCH_MAX_M:.0f} m de {base}')
    facts = dcsdata.airport_facts(ap)
    proj = geo.Projection.for_theatre(theatre)
    beacons_lua = Path(cfg.terrains) / theatre / 'beacons.lua'
    if not beacons_lua.is_file():
        raise ChartError(f'beacons.lua introuvable : {beacons_lua}')
    all_beacons = dcsdata.parse_beacons_lua(beacons_lua)
    blist = dcsdata.airfield_beacons(all_beacons, facts.id)

    log(f'{base} -> {facts.name} (id {facts.id}), mosaique {g.width}x{g.height}, {g.mpp:.4f} m/px')
    pav = vector.pavement_mask(img, g.mpp)
    bld, n_bld = vector.building_mask(img, pav, g.mpp)
    clear = vector.clear_marking_mask(img)
    rws = runways.detect_runways(img, pav, g, clear)
    if not rws:
        raise ChartError('aucune piste detectee')
    del img

    lat, lon = proj.to_latlon(facts.x, facts.z)
    decl = geo.declination(lat, lon, year)
    conv = proj.convergence(facts.x, facts.z)

    names = naming.name_ends([(r.p0, r.p1) for r in rws], [(a.name, a.heading) for a in facts.approaches])
    ends = []
    elev_m = float(np.median(pts[:, 1]))
    pxz = pts[:, [0, 2]]
    for i, r in enumerate(rws):
        for k in range(2):
            nm, az = names[i][k]
            pos = r.p0 if k == 0 else r.p1
            clat, clon = proj.to_latlon(*pos)
            gconv = proj.convergence(*pos)
            true = az + gconv
            mag = (true - decl) % 360.0
            d = np.hypot(pxz[:, 0] - pos[0], pxz[:, 1] - pos[1])
            thr = None
            if d.min() <= THRESHOLD_ELEV_MAX_M:
                thr = int(round(float(np.median(pts[np.argsort(d)[:THRESHOLD_ELEV_N], 1])) * FT))
            ends.append(End(nm, az, mag, pos, i, thr))
    # ILS : balises de piste dont runway_side = nom de l'approche pydcs de meme nom
    side_ids = {a.name: a.beacon_ids for a in facts.approaches}
    for e in ends:
        ids = side_ids.get(e.name)
        if ids is None:
            cand = [a for a in facts.approaches if naming.number_of(a.name) == naming.number_of(e.name)]
            ids = cand[0].beacon_ids if len(cand) == 1 else []
        e.ils = ils_text(blist, ids)
    ends.sort(key=lambda e: naming.sort_key(e.name))

    parkings = []
    shares = shelter_flags([(s.x, s.z) for s in facts.slots], bld, g)
    for s, sh in zip(facts.slots, shares):
        kind = 'shelter' if sh >= SHELTER_SHARE else ('heli' if s.heli_only else 'open')
        parkings.append(Parking(s.name, s.x, s.z, kind))
    n_shelter = sum(1 for p in parkings if p.kind == 'shelter')

    osm_icao, osm_ways = None, []
    osm = Path(osm_path) if osm_path else Path(osm_dir or Path(cfg.work_dir) / 'osm') / theatre.lower() / f'{base}.json'
    if osm.is_file():
        osm_icao, osm_ways = read_osm(osm, proj, pav, g, g.mpp)
    code = icao or osm_icao or base

    tower = {'vhf': fmt_mhz(facts.atc['vhf_high_hz']), 'uhf': fmt_mhz(facts.atc['uhf_hz']),
             'fm': fmt_mhz(facts.atc['vhf_low_hz']), 'hf': fmt_mhz(facts.atc['hf_hz'])}
    pav_rings = vector.mask_rings(pav, g, vector.PAVEMENT_SIMPLIFY_M)
    bld_rings = vector.mask_rings(bld, g, vector.BUILDING_SIMPLIFY_M)
    return ChartData(theatre=theatre, base=base, airport_name=facts.name, icao=code, year=float(year), lat=lat, lon=lon,
                     arp=(facts.x, facts.z), elev_ft=int(round(elev_m * FT)), tower=tower,
                     beacons=banner_beacons(blist), decl=decl, conv=conv, runways=rws, ends=ends, parkings=parkings,
                     georef=g, pav_rings=pav_rings, bld_rings=bld_rings, masks={'pav': pav, 'bld': bld, 'clear': None},
                     osm_ways=osm_ways, osm_kept=sorted({w['ref'] for w in osm_ways}, key=dcsdata.natural_key),
                     osm_icao=osm_icao, projection=proj, n_shelter=n_shelter)
