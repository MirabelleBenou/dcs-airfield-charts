"""Plans d'aerodrome de DCS Mission Plan et abris des places de parking (lot ac-plans-v1).

A partir des zips *airfields* et *helicopters* d'une release : pour chaque chart, le plan 10-9 reduit au revetement, aux
pistes, a leurs marquages et aux batiments, avec l'echelle et le decalage qui le posent sur le repere du jeu (contrat
`dcs-plans-v1`), et les places dessinees en abri. Aucune chart n'est rendue de nouveau. La commande est `build_plans.py`.
"""
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import dcsdata, fiches, page

FORMAT = 'dcs-plans-v1'
SHELTERS_FORMAT = 'dcs-shelters-v1'
NS_SVG = 'http://www.w3.org/2000/svg'
NS_INKSCAPE = 'http://www.inkscape.org/namespaces/inkscape'
NS_XLINK = 'http://www.w3.org/1999/xlink'
LABEL = f'{{{NS_INKSCAPE}}}label'
KEPT_LAYERS = ('Pavement', 'Runways', 'Runway markings', 'Buildings')
PARKING_LAYER = 'Parking'
ITERATIONS = 10
MAX_GAP_MM = 0.01
SHELTER_FILL = '#FFFFFF'

ET.register_namespace('', NS_SVG)
ET.register_namespace('inkscape', NS_INKSCAPE)
ET.register_namespace('xlink', NS_XLINK)


class PlansError(fiches.FichesError):
    """Cas d'arret du cadrage 6 : l'outil s'arrete sans rien ecrire."""


@dataclass
class Plan:
    airdrome_id: int
    stem: str
    S: float
    OX: float
    OY: float
    gap: float
    n_places: int
    shelters: list
    svg: bytes


@dataclass
class Package:
    theatre: str                                   # cle du Route Planner
    edition: str
    plans: dict = field(default_factory=dict)      # airdromeId -> Plan


# ---------- lecture du SVG ----------
def parse_svg(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise PlansError(f'SVG illisible : {e}') from None


def layer(root, label):
    """Le groupe de premier niveau d'`inkscape:label` donne, ou None ; deux groupes du meme nom : PlansError."""
    found = [e for e in root if e.get(LABEL) == label]
    if len(found) > 1:
        raise PlansError(f'calque {label} en double')
    return found[0] if found else None


def parking_circles(root):
    """[(cx, cy, fill, stroke-width)] des `circle` de la couche Parking, dans l'ordre du document."""
    lay = layer(root, PARKING_LAYER)
    if lay is None:
        raise PlansError('pas de couche Parking')
    out = []
    for c in lay.iter(f'{{{NS_SVG}}}circle'):
        try:
            cx, cy = float(c.get('cx')), float(c.get('cy'))
        except (TypeError, ValueError):
            raise PlansError('rond sans cx ou cy lisible') from None
        sw = c.get('stroke-width')
        try:
            sw = float(sw) if sw is not None else None
        except ValueError:
            raise PlansError(f'rond de la couche Parking : stroke-width illisible ({sw!r})') from None
        out.append((cx, cy, c.get('fill'), sw))
    return out


_PARKING_TXT = re.compile(r'PARKING:\s*(\d+)\s*\((\d+)\s*SHELTERED\)')


def header_counts(root):
    """(n, k) du texte `PARKING: <n> (<k> SHELTERED)`, ou None (helistations, `HELIPADS: <n>`) ; deux textes differents : PlansError."""
    found = set()
    for t in root.iter(f'{{{NS_SVG}}}text'):
        m = _PARKING_TXT.search(''.join(t.itertext()))
        if m:
            found.add((int(m[1]), int(m[2])))
    if len(found) > 1:
        raise PlansError(f'plusieurs en-tetes PARKING differents : {sorted(found)}')
    return next(iter(found)) if found else None


# ---------- calage ----------
def _predict(params, places):
    S, OX, OY = params
    return np.column_stack([OX + places[:, 1] * S, OY - places[:, 0] * S])


def _nearest(pred, circles):
    d = np.hypot(pred[:, None, 0] - circles[None, :, 0], pred[:, None, 1] - circles[None, :, 1])
    return d.argmin(axis=1)


def fit(places, circles):
    """Moindres carres de `X = OX + y*S`, `Y = OY - x*S` entre les places (x, y en m) et les centres de ronds (X, Y en mm).
    Retourne (S, OX, OY, appariement) ; l'appariement donne, pour chaque place, l'indice de son rond. PlansError si les
    comptes different, si l'appariement n'est pas un a un, si S <= 0 ou si l'ecart depasse 0,01 mm (le message porte
    les deux comptes et l'ecart)."""
    places = np.asarray(places, dtype=float).reshape(-1, 2)
    circles = np.asarray(circles, dtype=float).reshape(-1, 2)
    n, m = len(places), len(circles)
    if n != m:
        raise PlansError(f'{m} ronds pour {n} places')
    if n < 2:
        raise PlansError(f'{n} place : calage impossible')
    ext_y = places[:, 1].max() - places[:, 1].min()
    if ext_y <= 0:
        raise PlansError('places alignees sur un meme y : calage impossible')
    S = (circles[:, 0].max() - circles[:, 0].min()) / ext_y
    params = (S, circles[:, 0].min() - S * places[:, 1].min(), circles[:, 1].min() + S * places[:, 0].max())
    for _ in range(ITERATIONS):
        pair = _nearest(_predict(params, places), circles)
        tgt = circles[pair]
        A = np.zeros((2 * n, 3))
        A[:n, 0], A[:n, 1] = places[:, 1], 1.0                 # X = OX + y*S
        A[n:, 0], A[n:, 2] = -places[:, 0], 1.0                # Y = OY - x*S
        b = np.concatenate([tgt[:, 0], tgt[:, 1]])
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        params = tuple(float(v) for v in sol)
    S, OX, OY = round(params[0], 9), round(params[1], 4), round(params[2], 4)
    pair = _nearest(_predict((S, OX, OY), places), circles)
    gap = float(np.hypot(*(_predict((S, OX, OY), places) - circles[pair]).T).max())
    if len(set(pair.tolist())) != n:
        raise PlansError(f'appariement non un a un : {m} ronds, {n} places, {len(set(pair.tolist()))} ronds distincts apparies, '
                         f'ecart maximal {gap:.4f} mm')
    if not S > 0:
        raise PlansError(f'echelle S = {S} non positive ({m} ronds, {n} places), ecart maximal {gap:.4f} mm')
    if gap > MAX_GAP_MM:
        raise PlansError(f'ecart maximal {gap:.4f} mm au-dela de {MAX_GAP_MM} mm ({m} ronds, {n} places)')
    return S, OX, OY, pair.tolist(), gap


# ---------- abris ----------
def is_shelter(circle):
    """Rond dessine comme un abri par `page.py` : remplissage `#FFFFFF` et `stroke-width` de `page.STROKE_SHELTER`."""
    _, _, fill, sw = circle
    return fill is not None and fill.upper() == SHELTER_FILL and sw is not None and abs(sw - page.STROKE_SHELTER) < 1e-9


def check_header(root, n_places, n_shelters, stem=''):
    """Controle : `PARKING: <n> (<k> SHELTERED)` = places et abris ; sans ce texte (helistations), aucun abri."""
    h = header_counts(root)
    if h is None:
        if n_shelters:
            raise PlansError(f'{n_shelters} abris sur une chart sans en-tete PARKING')
    elif h != (n_places, n_shelters):
        raise PlansError(f'en-tete PARKING: {h[0]} ({h[1]} SHELTERED) contre {n_places} places et {n_shelters} abris')


# ---------- reduction ----------
def reduce_svg(root):
    """Octets du plan reduit : racine (viewBox, width, height) et, dans l'ordre, les couches Pavement, Runways, Runway
    markings et Buildings, sans `clip-path`, sans `text` ni `g` qui en reste vide."""
    new = ET.Element(root.tag)
    for k in ('width', 'height', 'viewBox'):
        if root.get(k) is not None:
            new.set(k, root.get(k))
    for label in KEPT_LAYERS:
        lay = layer(root, label)
        if lay is None:
            continue
        lay.attrib.pop('clip-path', None)
        _strip_text(lay)
        if len(lay) == 0:                      # couche videe par le retrait des textes : retiree, comme une couche absente
            continue
        new.append(lay)
    body = ET.tostring(new, encoding='unicode', short_empty_elements=True)
    return ("<?xml version='1.0' encoding='UTF-8'?>\n" + body + '\n').encode('utf-8')


def _strip_text(el):
    """Retire les `text` sous `el` et les `g` que ce retrait laisse vides."""
    tag_text, tag_g = f'{{{NS_SVG}}}text', f'{{{NS_SVG}}}g'
    for child in list(el):
        if child.tag == tag_text:
            _drop(el, child)
        elif child.tag == tag_g:
            had = len(child) > 0
            _strip_text(child)
            if had and len(child) == 0:
                _drop(el, child)


def _drop(parent, child):
    """Retire `child` en gardant son espacement final (`tail`) pour que la sortie reste lisible."""
    idx = list(parent).index(child)
    tail = child.tail or ''
    if idx > 0:
        prev = parent[idx - 1]
        prev.tail = (prev.tail or '') if tail.strip() == '' and (prev.tail or '') else (prev.tail or '') + tail
    else:
        parent.text = parent.text or tail
    parent.remove(child)


# ---------- le paquet ----------
def load_parkings(path, rp_key):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('theatre') != rp_key:
        raise PlansError(f'{path} : theatre {data.get("theatre")} au lieu de {rp_key}')
    return data['airdromes']


def airdrome_by_name(airdromes, name):
    found = [a for a in airdromes if a['name'] == name]
    if len(found) != 1:
        raise PlansError(f'{name} : {len(found)} aerodromes de ce nom dans downloads/parkings (un seul attendu)')
    return found[0]


def plan_for_chart(svg_bytes, airdrome):
    """Plan d'une chart : calage sur les places de l'aerodrome, abris, controle de l'en-tete, SVG reduit."""
    root = parse_svg(svg_bytes)
    circles = parking_circles(root)
    places = [(p['x'], p['y']) for p in airdrome['parkings']]
    S, OX, OY, pair, gap = fit(places, [(c[0], c[1]) for c in circles])
    shelters = sorted(p['parking'] for p, j in zip(airdrome['parkings'], pair) if is_shelter(circles[j]))
    check_header(root, len(places), len(shelters))
    return Plan(airdrome['airdromeId'], '', S, OX, OY, gap, len(places), shelters, reduce_svg(root))


def build_package(theatre, zip_airfields, zip_helicopters, airfields_json, parkings_json, edition, log=print):
    """Calcule tout en memoire (rien n'est ecrit) ; PlansError ou FichesError au premier cas d'arret."""
    canon, rp_key = fiches.resolve_theatre(theatre)
    data = json.loads(Path(airfields_json).read_text(encoding='utf-8'))
    airfields = (data.get('theatres') or {}).get(rp_key)
    if not airfields:
        raise PlansError(f'airfields.json : pas de theatre {rp_key}')
    airdromes = load_parkings(parkings_json, rp_key)
    pkg = Package(rp_key, edition)
    owner = {}
    charts = fiches.chart_pages([zip_airfields, zip_helicopters])
    for stem in sorted(charts, key=dcsdata.natural_key):
        ref = next((r for suffix, r in charts[stem] if suffix == '10-9'), None)
        if ref is None:
            raise PlansError(f'{stem} : pas de page 10-9')
        try:
            try:
                svg = fiches.read_member(fiches.svg_member(ref))
            except KeyError as e:
                raise PlansError(f'fichier absent du zip ({e})') from None
            lat, lon = fiches.read_arp(svg.decode('utf-8'))
            name, _ = fiches.nearest_airfield(lat, lon, airfields)
            ad = airdrome_by_name(airdromes, name)
            aid = ad['airdromeId']
            plan = None if aid in owner else plan_for_chart(svg, ad)
        except fiches.FichesError as e:
            raise PlansError(f'{stem} : {e}') from None
        if plan is None:
            raise PlansError(f'{stem} et {owner[aid]} designent le meme airdromeId ({aid}, {name})')
        owner[aid] = stem
        plan.stem = stem
        pkg.plans[aid] = plan
    if not pkg.plans:
        raise PlansError('aucune chart')
    return pkg


# ---------- sorties ----------
def index_dict(pkg):
    return {'format': FORMAT, 'theatre': pkg.theatre, 'edition': pkg.edition,
            'plans': {str(aid): {'fichier': f'{aid}.svg', 'S': p.S, 'OX': p.OX, 'OY': p.OY}
                      for aid, p in sorted(pkg.plans.items())}}


def index_json(pkg):
    return json.dumps(index_dict(pkg), indent=1, ensure_ascii=False) + '\n'


def shelters_text(pkg):
    """Fichier d'abris : un aerodrome par ligne, comme les fichiers voisins de `export_parkings.py`."""
    dumps = lambda o: json.dumps(o, ensure_ascii=False, separators=(',', ':'))
    source = f'DCS Airfield Charts {pkg.edition}: parking stands drawn as hardened shelters on the 10-9 charts'
    out = ['{' + f'"format":{dumps(SHELTERS_FORMAT)},"theatre":{dumps(pkg.theatre)},"edition":{dumps(pkg.edition)},'
           f'"source":{dumps(source)},"airdromes":' + '{']
    items = sorted(pkg.plans.items())
    for i, (aid, p) in enumerate(items):
        out.append(f'"{aid}":{dumps(sorted(p.shelters))}' + (',' if i < len(items) - 1 else ''))
    out.append('}}')
    return '\n'.join(out) + '\n'


def zip_path(out_root, theatre):
    canon, _ = fiches.resolve_theatre(theatre)
    return Path(out_root) / f'DCS_Airfield_Charts_{canon}_1985_plans.zip'


def check_targets(out_root, theatre, shelters_path=None):
    """Refus avant toute ecriture : dossier `<out>/<cle>/` non vide, zip deja present ; fichier d'abris deja present, sans
    dossier parent, ou place sous `<out>/<cle>/`."""
    _, rp_key = fiches.resolve_theatre(theatre)
    folder = Path(out_root) / rp_key
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise PlansError(f'dossier de sortie deja present et non vide : {folder}')
    if zip_path(out_root, theatre).exists():
        raise PlansError(f'zip deja present : {zip_path(out_root, theatre)}')
    if shelters_path is not None:
        sp = Path(shelters_path)
        if sp.exists() or sp.is_symlink():
            raise PlansError(f'fichier d\'abris deja present : {sp}')
        if folder.resolve() in (sp.parent.resolve() / sp.name).parents:
            raise PlansError(f'fichier d\'abris sous le dossier de sortie : {sp}')
        if not sp.parent.is_dir():
            raise PlansError(f'dossier du fichier d\'abris absent ou pas un dossier : {sp.parent}')


def write_package(pkg, out_root, theatre, shelters_path=None):
    """Ecrit `<out>/<cle>/` (index et SVG), le zip (tout a la racine, DEFLATED, date fixe) et, si demande, le fichier
    d'abris : tout sous des noms temporaires, renomme a la fin ; sur une OSError, ce qui a ete ecrit est retire et
    PlansError est levee. Retourne (dossier, zip)."""
    check_targets(out_root, theatre, shelters_path)
    folder = Path(out_root) / pkg.theatre
    zpath = zip_path(out_root, theatre)
    files = [('index.json', index_json(pkg).encode('utf-8'))] + [(f'{aid}.svg', p.svg) for aid, p in sorted(pkg.plans.items())]
    made_dirs, temps, done = [], [], []                 # dossiers crees, fichiers temporaires, fichiers renommes
    try:
        for d in reversed([folder, *folder.parents]):
            if not d.exists():
                d.mkdir()
                made_dirs.append(d)
        for name, blob in files:
            tmp = folder / (name + '.tmp')
            temps.append(tmp)
            tmp.write_bytes(blob)
        ztmp = zpath.with_name(zpath.name + '.tmp')
        temps.append(ztmp)
        with zipfile.ZipFile(ztmp, 'w') as z:
            for name, blob in files:
                info = zipfile.ZipInfo(name, fiches.ZIP_DATE)
                info.external_attr = 0o644 << 16
                z.writestr(info, blob, compress_type=zipfile.ZIP_DEFLATED)
        targets = [(folder / (n + '.tmp'), folder / n) for n, _ in files] + [(ztmp, zpath)]
        if shelters_path is not None:
            sp = Path(shelters_path)
            stmp = sp.with_name(sp.name + '.tmp')
            temps.append(stmp)
            stmp.write_bytes(shelters_text(pkg).encode('utf-8'))
            targets.append((stmp, sp))
        for src, dst in targets:
            src.replace(dst)
            done.append(dst)
    except OSError as e:
        for f in temps + done:
            try:
                f.unlink()
            except OSError:
                pass
        for d in reversed(made_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        raise PlansError(f'ecriture impossible, rien n\'est laisse : {e}') from None
    return folder, zpath


def summary(pkg, theatre, zpath):
    size = Path(zpath).stat().st_size
    plans = pkg.plans.values()
    return '\n'.join([
        f'theatre : {theatre} ({pkg.theatre}) · edition {pkg.edition}',
        f'plans : {len(pkg.plans)}',
        f'places : {sum(p.n_places for p in plans)}',
        f'places en abri : {sum(len(p.shelters) for p in plans)}',
        f'ecart maximal : {max(p.gap for p in plans):.4f} mm',
        f'zip : {Path(zpath).name} · {size} octets ({size / 1e6:.1f} Mo)',
    ])
