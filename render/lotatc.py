"""Dessins LotAtc de chaque aerodrome cartographie (lot ac-lotatc-v1).

A partir des zips *airfields* et *helicopters* d'une release : pour chaque chart, un calque LotAtc « plan » (revetement,
pistes, indicatifs de piste, noms de taxiways) et un calque « places » (places de parking), aux positions du jeu. Le
rattachement et le calage sont ceux de `plans.build_package`, sans modification. Aucune chart n'est rendue de nouveau.
La commande est `build_lotatc.py`.
"""
import json
import math
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import LinearRing, Polygon

from . import fiches, geo, plans

NS_SVG = plans.NS_SVG
AUTHOR = 'DCS Airfield Charts'
LAYER_VERSION = 'hotfixes/220'
FONT = 'LotAtc Sans'
MIN_AREA_M2 = 400.0
SIMPLIFY_M = 2.0
DESIGNATOR_OFFSET_M = 120.0
DECIMALS = 7
KINDS = ('pavement', 'runway', 'designator', 'taxiway', 'stand')

# (lineWidth, color, colorBg, brushStyle, couleur de police ou None)
STYLES = {
    'pavement': (1, '#ffa8a8a8', '#00000000', 0, None),
    'runway': (1, '#ffffffff', '#66ffffff', 1, None),
    'designator': (1, '#00000000', '#00000000', 0, '#ffffffff'),
    'taxiway': (1, '#00000000', '#00000000', 0, '#ffffd200'),
    'stand': (4, '#ff4fc3f7', '#ff4fc3f7', 1, '#ff4fc3f7'),
}

README_TEXT = """DCS Airfield Charts - LotAtc drawings - {theatre} - {edition}

One LotAtc drawing layer per file, for every airfield and helicopter base charted in this theatre:
- <base>.json: the airfield plan - pavement outline, runways, runway designators and taxiway names;
- <base>_parking.json: the parking stands, named as in the DCS mission editor.
Helicopter bases have no plan file, only their parking stands.

Install, on the LotAtc server: copy the files you want into
Saved Games/DCS/Mods/services/LotAtc/userdb/drawings/blue/ (and/or red/). LotAtc loads them at start, one layer per file.
A controller can also load a single file from the Draw tab of the LotAtc client.

Positions are read from DCS World: the F10 map for the plan, the mission editor for the parking stands.
They match the game, not the real airfields. Not for real-world navigation.
Data: F10 map and DCS World files (Eagle Dynamics). Non-commercial use only.
Source: https://github.com/MirabelleBenou/dcs-airfield-charts
"""

_NUM = re.compile(r'-?\d+(?:\.\d+)?')
_TRANSLATE = re.compile(r'^translate\(\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?:\s*,\s*|\s+)(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*\)')


class LotatcError(plans.PlansError):
    """Cas d'arret du cadrage 6 : l'outil s'arrete sans rien ecrire."""


@dataclass
class Layer:
    name_file: str
    text: str
    kind: str                                     # 'plan' ou 'parking'
    counts: dict = field(default_factory=dict)    # dessins par type
    pavement_points: int = 0


# ---------- repere ----------
def page_to_game(X, Y, S, OX, OY):
    """Millimetres de la page -> metres du jeu (x nord, y est) : inverse exact du contrat dcs-plans-v1."""
    return (OY - Y) / S, (X - OX) / S


def to_latlon(proj, x, y):
    lat, lon = proj.to_latlon(x, y)
    return round(lat, DECIMALS), round(lon, DECIMALS)


def _pairs(text):
    nums = [float(v) for v in _NUM.findall(text or '')]
    return list(zip(nums[0::2], nums[1::2]))


def _tag(name):
    return f'{{{NS_SVG}}}{name}'


def _text_of(el):
    return ''.join(el.itertext()).strip()


# ---------- dessins ----------
def _style(kind):
    lw, color, bg, brush, fcolor = STYLES[kind]
    d = {'lineWidth': lw, 'color': color, 'colorBg': bg, 'brushStyle': brush}
    if fcolor is not None:
        d['font'] = {'font': FONT, 'color': fcolor}
    return d


def polygon_drawing(name, latlons, kind):
    d = {'type': 'polygon', 'name': name, 'shared': False, 'visible': True,
         'points': [{'latitude': la, 'longitude': lo} for la, lo in latlons]}
    d.update(_style(kind))
    return d


def point_drawing(name, latlon, text, kind):
    d = {'type': 'point', 'name': name, 'shared': False, 'visible': True,
         'latitude': latlon[0], 'longitude': latlon[1], 'text': text}
    d.update(_style(kind))
    return d


# ---------- revetement ----------
def pavement_rings(d, conv):
    """Anneaux (listes de (x, y) en metres, sans la repetition finale) d'un attribut `d` : decoupe avant chaque M ou m,
    morceau de moins de 6 nombres ignore, simplification a 2 m, anneau sous 400 m2 omis."""
    out = []
    for piece in re.split(r'(?=[Mm])', d or ''):
        nums = [float(v) for v in _NUM.findall(piece)]
        if len(nums) < 6:
            continue
        pts = [conv(X, Y) for X, Y in zip(nums[0::2], nums[1::2])]
        simp = LinearRing(pts).simplify(SIMPLIFY_M, preserve_topology=False)
        if simp.is_empty:
            continue
        coords = list(simp.coords)
        if len(coords) < 4:
            continue
        ring = coords[:-1]
        if Polygon(ring).area >= MIN_AREA_M2:
            out.append(ring)
    return out


# ---------- pistes ----------
def runway_axis(pts):
    """Axe d'un rectangle de 4 points : milieux des deux plus petits cotes (tri par (longueur, j)), dans cet ordre."""
    sides = sorted((math.dist(pts[j], pts[(j + 1) % 4]), j) for j in range(4))[:2]
    mids = []
    for _, j in sides:
        a, b = pts[j], pts[(j + 1) % 4]
        mids.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    return mids[0], mids[1]


def designator_position(start, axes):
    """Position de l'indicatif : 120 m au-dela de l'extremite d'axe la plus proche de `start`, sur le prolongement de
    l'axe ; sans axe, `start`."""
    if not axes:
        return start
    best = None
    for a, b in axes:
        for end, other in ((a, b), (b, a)):               # extremite « debut » avant « fin »
            dist = math.dist(start, end)
            if best is None or dist < best[0]:
                best = (dist, end, other)
    _, end, other = best
    length = math.dist(other, end)
    if length == 0:
        return end
    return (end[0] + (end[0] - other[0]) / length * DESIGNATOR_OFFSET_M,
            end[1] + (end[1] - other[1]) / length * DESIGNATOR_OFFSET_M)


# ---------- plan ----------
def plan_drawings(root, S, OX, OY, proj):
    """(dessins, comptes par type, nombre de points de revetement) du calque plan d'une chart (SVG lu par parse_svg)."""
    conv = lambda X, Y: page_to_game(X, Y, S, OX, OY)
    ll = lambda p: to_latlon(proj, p[0], p[1])
    drawings, counts, pav_points = [], dict.fromkeys(KINDS, 0), 0

    lay = plans.layer(root, 'Pavement')
    if lay is not None:
        k = 0
        for path in lay.iter(_tag('path')):
            for ring in pavement_rings(path.get('d'), conv):
                k += 1
                pav_points += len(ring)
                drawings.append(polygon_drawing(f'pavement {k}', [ll(p) for p in ring], 'pavement'))
        counts['pavement'] = k

    axes = []
    lay = plans.layer(root, 'Runways')
    if lay is not None:
        k = 0
        for poly in lay.iter(_tag('polygon')):
            pts = [conv(X, Y) for X, Y in _pairs(poly.get('points'))]
            k += 1
            drawings.append(polygon_drawing(f'runway {k}', [ll(p) for p in pts], 'runway'))
            if len(pts) == 4:
                axes.append(runway_axis(pts))
        counts['runway'] = k

    lay = plans.layer(root, 'Runway markings')
    if lay is not None:
        for el in lay.iter():
            m = _TRANSLATE.match(el.get('transform') or '')
            if not m:
                continue
            txt = next((_text_of(t) for t in el if t.tag == _tag('text') and _text_of(t)), None)
            if txt is None:
                continue
            pos = designator_position(conv(float(m[1]), float(m[2])), axes)
            drawings.append(point_drawing(f'RWY {txt}', ll(pos), txt, 'designator'))
            counts['designator'] += 1

    lay = plans.layer(root, 'Taxiway labels')
    if lay is not None:
        rect = None
        for el in lay:
            if el.tag == _tag('rect'):
                rect = el
            elif el.tag == _tag('text') and _text_of(el):
                if rect is None:
                    raise LotatcError(f'texte de taxiway {_text_of(el)!r} sans rect avant lui')
                try:
                    x, y, w, h = (float(rect.get(a)) for a in ('x', 'y', 'width', 'height'))
                except (TypeError, ValueError):
                    raise LotatcError('rect de taxiway sans x, y, width ou height lisible') from None
                pos = conv(x + w / 2, y + h / 2)
                txt = _text_of(el)
                drawings.append(point_drawing(f'TWY {txt}', ll(pos), txt, 'taxiway'))
                counts['taxiway'] += 1
                rect = None
    return drawings, counts, pav_points


def parking_drawings(airdrome, proj):
    return [point_drawing(f'stand {p["parking_id"]}', to_latlon(proj, p['x'], p['y']), str(p['parking_id']), 'stand')
            for p in airdrome['parkings']]


# ---------- calques ----------
def layer_dict(stem, which, drawings):
    suffix = ' (DCS)' if which == 'plan' else ' parking (DCS)'
    return {'author': AUTHOR, 'drawings': drawings, 'enable': 'true',
            'id': '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'dcs-airfield-charts/lotatc/{stem}/{which}')) + '}',
            'name': stem.replace('_', ' ') + suffix, 'shared': False, 'timestamp': '', 'type': 'layer',
            'version': LAYER_VERSION}


def layer_text(layer):
    return json.dumps(layer, ensure_ascii=False, indent=1) + '\n'


@dataclass
class Result:
    theatre: str                                   # nom canonique
    key: str
    edition: str
    airfields: int = 0
    layers: list = field(default_factory=list)     # [Layer], plans et places
    counts: dict = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    pavement_points: int = 0


def build(theatre, zip_airfields, zip_helicopters, airfields_json, parkings_json, edition):
    """Calcule tout en memoire (rien n'est ecrit) ; PlansError, FichesError ou LotatcError au premier cas d'arret."""
    canon, key = fiches.resolve_theatre(theatre)
    pkg = plans.build_package(theatre, zip_airfields, zip_helicopters, airfields_json, parkings_json, edition, log=lambda *a: None)
    airdromes = {a['airdromeId']: a for a in plans.load_parkings(parkings_json, key)}
    charts = fiches.chart_pages([zip_airfields, zip_helicopters])
    proj = geo.Projection.for_theatre(canon)
    res = Result(canon, key, edition)
    for aid, plan in sorted(pkg.plans.items(), key=lambda t: t[1].stem):
        ref = next(r for suffix, r in charts[plan.stem] if suffix == '10-9')
        try:
            root = plans.parse_svg(fiches.read_member(fiches.svg_member(ref)))
            drawings, counts, pav = plan_drawings(root, plan.S, plan.OX, plan.OY, proj)
        except fiches.FichesError as e:
            raise LotatcError(f'{plan.stem} : {e}') from None
        res.airfields += 1
        if drawings:
            res.layers.append(Layer(f'{plan.stem}.json', layer_text(layer_dict(plan.stem, 'plan', drawings)), 'plan'))
            for k, v in counts.items():
                res.counts[k] += v
            res.pavement_points += pav
        stands = parking_drawings(airdromes[aid], proj)
        res.counts['stand'] += len(stands)
        res.layers.append(Layer(f'{plan.stem}_parking.json', layer_text(layer_dict(plan.stem, 'parking', stands)), 'parking'))
    return res


# ---------- sorties ----------
def readme_text(theatre, edition):
    return README_TEXT.format(theatre=geo.theatre_display(theatre), edition=edition)


def zip_path(out_root, theatre):
    canon, _ = fiches.resolve_theatre(theatre)
    return Path(out_root) / f'DCS_Airfield_Charts_{canon}_1985_lotatc.zip'


def check_targets(out_root, theatre):
    """Refus avant toute ecriture : dossier `<out>/<cle>/` non vide, ou zip deja present."""
    _, key = fiches.resolve_theatre(theatre)
    folder = Path(out_root) / key
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise LotatcError(f'dossier de sortie deja present et non vide : {folder}')
    if zip_path(out_root, theatre).exists():
        raise LotatcError(f'zip deja present : {zip_path(out_root, theatre)}')


def write(res, out_root):
    """Ecrit `<out>/<cle>/` (les calques) et le zip (tout a la racine, calques tries par nom puis README.txt, DEFLATED,
    date fixe) ; sur une OSError, ce qui a ete ecrit est retire. Retourne (dossier, zip)."""
    check_targets(out_root, res.theatre)
    folder = Path(out_root) / res.key
    zpath = zip_path(out_root, res.theatre)
    files = sorted((l.name_file, l.text.encode('utf-8')) for l in res.layers)
    files.append(('README.txt', readme_text(res.theatre, res.edition).encode('utf-8')))
    made, written = [], []
    try:
        for d in reversed([folder, *folder.parents]):
            if not d.exists():
                d.mkdir()
                made.append(d)
        for name, blob in files[:-1]:
            (folder / name).write_bytes(blob)
            written.append(folder / name)
        ztmp = zpath.with_name(zpath.name + '.tmp')
        written.append(ztmp)
        with zipfile.ZipFile(ztmp, 'w') as z:
            for name, blob in files:
                info = zipfile.ZipInfo(name, fiches.ZIP_DATE)
                info.external_attr = 0o644 << 16
                z.writestr(info, blob, compress_type=zipfile.ZIP_DEFLATED)
        ztmp.replace(zpath)
        written[-1] = zpath
    except OSError as e:
        for f in written:
            try:
                f.unlink()
            except OSError:
                pass
        for d in reversed(made):
            try:
                d.rmdir()
            except OSError:
                pass
        raise LotatcError(f'ecriture impossible, rien n\'est laisse : {e}') from None
    return folder, zpath


def summary(res, zpath):
    size = Path(zpath).stat().st_size
    c = res.counts
    return '\n'.join([
        f'theatre : {res.theatre} ({res.key}) · edition {res.edition}',
        f'aerodromes : {res.airfields}',
        f'calques plan : {sum(1 for l in res.layers if l.kind == "plan")}',
        f'calques places : {sum(1 for l in res.layers if l.kind == "parking")}',
        f'revetement : {c["pavement"]} anneaux ({res.pavement_points} points)',
        f'pistes : {c["runway"]}',
        f'indicatifs : {c["designator"]}',
        f'taxiways : {c["taxiway"]}',
        f'places : {c["stand"]}',
        f'zip : {Path(zpath).name} · {size} octets ({size / 1e6:.1f} Mo)',
    ])
