"""Pages d'approche aux instruments `11-x` (avion) et `11-xH` (helicoptere) d'un theatre, a partir des zips de la release precedente.

Usage : python -I render/build_approaches.py <Theatre> --zips <airfields.zip> <helicopters.zip> --beacons <beacons.lua>
                                              --grille <relief_<cle>.npy> --out <dossier> [--annee 1985]

Pour chaque terrain charte (une page `<stem>_10-9.svg` du zip *airfields*) qui a au moins une balise utilisable (VOR/DME, VOR,
NDB, TACAN ou RSBN de `beacons.lua`) a 3 NM au plus de sa position dans le jeu (cas A) : une page avion par sens de piste et, s'il a
un NDB, une page helicoptere par sens. La geometrie (seuils, pistes, en-tete) est relue sur la page 10-9, calee sur les places
du jeu (`plans.fit`) ; aucune capture F10. Les pages ILS `11-x` de la base de ces terrains sont remplacees, leur recueil
`pdf/<stem>_charts.pdf` est refait ; les pages `11-xH` vont dans le zip *helicopters* (et dans le recueil du terrain). Toute
autre entree est recopiee a l'octet. Les deux zips de sortie portent les noms de ceux de la base, sous `<out>/`.
Codes : 0 fait, 1 usage, 2 arret (rien n'est ecrit).
"""
import argparse
import html
import io
import math
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import approach_ifr, build_msa, dcsdata, export, fiches, geo, plans, procedure  # noqa: E402
from render.procedure import NM, Balise, Entree, GeometrieImpossible                              # noqa: E402

CASE_A_M = 5556.0                 # 3 NM
OPTION3_M = 1000.0                # NDB a 1 000 m au plus de la reference VOR/DME ou VOR : « OR NDB »
TYPES = {'VOR_DME': 'VOR/DME', 'VORTAC': 'VOR/DME', 'VOR': 'VOR', 'AIRPORT_HOMER': 'NDB', 'AIRPORT_HOMER_WITH_MARKER': 'NDB',
         'HOMER': 'NDB', 'ILS_FAR_HOMER': 'NDB', 'ILS_NEAR_HOMER': 'NDB', 'TACAN': 'TACAN', 'AIRPORT_TACAN': 'TACAN',
         'RSBN': 'RSBN'}
NAME_TOL_DEG = 0.15               # cap d'une extremite contre le MAG HDG du tableau de la 10-9 (affiche au dixieme)
DESIGNATOR_M = 75.0               # `page.draw_scene` : le numero de piste est a 75 m du seuil
DESIGNATOR_TOL_MM = 0.05
SUFFIX_ORDER = {'L': 0, 'C': 1, 'R': 2}
_SVG = re.compile(r'^(?P<dir>.*/)?svg/(?P<page>[^/]+)\.svg$')
_PAGE = re.compile(r'^(?P<stem>.+)_(?P<suffix>10-8|10-9[A-Z]*|11-\d+H?)$')
_ILS_SUFFIX = re.compile(r'^11-\d+$')
_TEXT = re.compile(r'<text\b([^>]*)>(.*?)</text>', re.S)
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
_LAYER = r'<g inkscape:groupmode="layer" id="[^"]*" inkscape:label="{}"[^>]*>\n(.*?)\n</g>'


class Arret(Exception):
    """Cas d'arret du cadrage 7 : rien n'est ecrit (code 2)."""


# ---------- lecture des pages de la base ----------
def layer_text(svg, label):
    """Contenu (texte brut) du calque `label` d'une page de ce moteur, ou None."""
    m = re.search(_LAYER.format(re.escape(html.escape(label, quote=False))), svg, re.S)
    return m.group(1) if m else None


def texts(fragment):
    """[(x, y, attributs, texte)] des <text> d'un fragment SVG, dans l'ordre."""
    out = []
    for m in _TEXT.finditer(fragment or ''):
        a = dict(_ATTR.findall(m.group(1)))
        out.append((float(a.get('x', 0)), float(a.get('y', 0)), a, html.unescape(m.group(2))))
    return out


@dataclass
class Header:
    lines: list                   # elements SVG du calque Header de la 10-9, tels quels (sans le bandeau des balises)
    icao: str
    name: str
    elev_ft: int
    lat: float
    lon: float


_ELEV = re.compile(r'^ELEV (-?\d+) FT$')


def read_header(svg, stem):
    """L'en-tete de la 10-9 : lignes OACI, nom, ELEV, ARP, MGRS, VAR, bandeau de la tour (tout ce qui est au-dessus de y = 29,6 mm).
    Arret si l'en-tete ne se relit pas."""
    body = layer_text(svg, 'Header')
    if body is None:
        raise Arret(f'{stem} : 10-9 sans calque Header')
    lines = []
    for ln in body.split('\n'):
        a = dict(_ATTR.findall(ln))
        y = a.get('y', a.get('y1'))
        if y is None:
            raise Arret(f'{stem} : element d\'en-tete illisible : {ln[:80]}')
        if float(y) < 29.6:
            lines.append(ln)
    tx = texts('\n'.join(lines))
    find = lambda x, y, anchor: [t for t in tx if abs(t[0] - x) < 0.6 and abs(t[1] - y) < 1e-6 and t[2].get('text-anchor', 'start') == anchor]
    kind = find(74.4, 15.0, 'middle')
    index = find(142.0, 11.4, 'end')
    name = find(74.55, 11.9, 'middle')
    icao = find(6.0, 11.4, 'start')
    elev = [t for t in tx if _ELEV.match(t[3])]
    if len(kind) != 1 or len(index) != 1 or index[0][3] != '10-9' or len(name) != 1 or len(elev) != 1:
        raise Arret(f'{stem} : en-tete de la 10-9 illisible (titre, numero de page, nom ou ELEV)')
    try:
        lat, lon = fiches.read_arp(svg)
    except fiches.FichesError as e:
        raise Arret(f'{stem} : {e}') from None
    if not any(t[3].startswith('MGRS ') for t in tx) or not any(t[3].startswith('VAR ') for t in tx):
        raise Arret(f'{stem} : en-tete de la 10-9 sans MGRS ou VAR')
    return Header(lines, icao[0][3] if icao else '', name[0][3], int(_ELEV.match(elev[0][3]).group(1)), lat, lon)


def header_row_lines(items):
    """Les cinq champs de la ligne d'en-tete (textes donnes), places comme `page.header_row` : le premier en X0, le dernier
    finissant en X1, ecarts egaux (4 mm au moins)."""
    from render import page
    from render.fonts import pt, text_width
    from render.svgdoc import text
    widths = [text_width(s, 'bold', pt(7.2)) for s in items]
    gap = (page.X1 - page.X0 - sum(widths)) / (len(items) - 1)
    if gap < page.HEADER_ROW_GAP_MIN:
        raise Arret(f"ligne d'en-tete : ecart de {gap:.2f} mm entre les champs : {items}")
    out, x = [], page.X0
    for s, w in zip(items, widths):
        out.append(text(x, page.HEADER_ROW_Y, s, pt(7.2), 'bold'))
        x += w + gap
    return out


def header_lines(hd, index, title, elev_m=False):
    """Les lignes d'en-tete de la 10-9, seuls le titre et le numero de page changent ; `elev_m` (page helicoptere, addendum 1,
    2.3) : l'elevation porte aussi les metres (`ELEV 3407 FT · 1038 M`), la ligne est replacee comme `page.header_row`."""
    from render import page
    from render.fonts import pt
    from render.svgdoc import INK, INK2, text
    old_kind = text(page.PAGE_W / 2, 15.0, '', pt(6.5), 'regular', INK2, 'middle', spacing=0.8).split('>')[0]
    old_index = text(page.X1, 11.4, '10-9', pt(13), 'bold', INK, 'end')
    row = [ln for ln in hd.lines if f'y="{page.HEADER_ROW_Y:g}"' in ln and ln.startswith('<text')]
    items = [t[3] for t in texts('\n'.join(row))]
    if len(items) != 5 or header_row_lines(items) != row:
        raise Arret(f'ligne d\'en-tete de la 10-9 illisible : {items}')
    if elev_m:
        new_row = header_row_lines([f'{items[0]} · {int(round(hd.elev_ft * 0.3048))} M'] + items[1:])
    out, n_kind, n_index = [], 0, 0
    for ln in hd.lines:
        if ln == old_index:
            out.append(text(page.X1, 11.4, index, pt(13), 'bold', INK, 'end'))
            n_index += 1
        elif ln.startswith(old_kind + '>'):
            out.append(text(page.PAGE_W / 2, 15.0, title, pt(6.5), 'regular', INK2, 'middle', spacing=0.8))
            n_kind += 1
        elif elev_m and ln in row:
            out.append(new_row[row.index(ln)])
        else:
            out.append(ln)
    if (n_kind, n_index) != (1, 1):
        raise Arret(f'en-tete de la 10-9 : titre ({n_kind}) ou numero de page ({n_index}) introuvable')
    return out


@dataclass
class Runway:
    corners: list                 # quatre coins (x, z) du polygone de la 10-9
    p0: tuple
    p1: tuple
    width: float
    grass: bool


@dataclass
class End:
    name: str
    pos: tuple
    other: tuple
    az: float                     # azimut grille du sens d'atterrissage
    rw: int


_POLY = re.compile(r'<polygon points="([^"]+)"([^>]*)/>')


def read_runways(svg, S, OX, OY, stem):
    """Pistes du calque Runways de la 10-9, ramenees dans le repere du jeu par le calage (X = OX + z S, Y = OY - x S)."""
    body = layer_text(svg, 'Runways')
    out = []
    for m in _POLY.finditer(body or ''):
        pts = [tuple(float(v) for v in p.split(',')) for p in m.group(1).split()]
        if len(pts) != 4:
            raise Arret(f'{stem} : piste de la 10-9 a {len(pts)} sommets')
        xz = [((OY - Y) / S, (X - OX) / S) for X, Y in pts]
        p0 = ((xz[0][0] + xz[3][0]) / 2, (xz[0][1] + xz[3][1]) / 2)
        p1 = ((xz[1][0] + xz[2][0]) / 2, (xz[1][1] + xz[2][1]) / 2)
        out.append(Runway(xz, p0, p1, math.dist(xz[0], xz[3]), 'stroke=' in m.group(2)))
    return out


def read_designators(svg):
    """[(nom, X, Y)] des numeros de piste (calque Runway markings)."""
    body = layer_text(svg, 'Runway markings') or ''
    out = []
    for m in re.finditer(r'<g transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\([-\d.]+\)">(.*?)</g>', body, re.S):
        t = texts(m.group(3))
        if len(t) == 1:
            out.append((t[0][3], float(m.group(1)), float(m.group(2))))
    return out


def read_table(svg):
    """[(nom, MAG HDG)] des lignes du tableau des pistes de la 10-9."""
    tx = texts(layer_text(svg, 'Runway table'))
    names = {round(y, 3): s for x, y, a, s in tx if abs(x - 6.0) < 1e-6 and a.get('font-weight') == 'bold' and s != 'RWY'}
    out = []
    for x, y, a, s in tx:
        if abs(x - 20.0) < 1e-6 and s.endswith('°') and round(y, 3) in names:
            out.append((names[round(y, 3)], float(s[:-1])))
    return out


def number_of(name):
    m = re.match(r'^(\d+)([LCR]?)$', name)
    return (m.group(1), m.group(2)) if m else (name, '')


def name_ends(runways, svg, S, OX, OY, decl, stem):
    """Les extremites des pistes, nommees comme sur la 10-9 : par le numero dessine a 75 m du seuil, sinon par le tableau des
    pistes (MAG HDG au dixieme ; pistes paralleles : L, C, R de gauche a droite dans le sens d'atterrissage). Arret si une
    extremite ne se nomme pas."""
    ends = []
    for i, r in enumerate(runways):
        ends.append(End('', r.p0, r.p1, procedure.az_of(r.p0, r.p1), i))
        ends.append(End('', r.p1, r.p0, procedure.az_of(r.p1, r.p0), i))
    des = read_designators(svg)
    for e in ends:
        q = procedure.offset(e.pos, e.az, DESIGNATOR_M)
        X, Y = OX + q[1] * S, OY - q[0] * S
        hits = [n for n, dx, dy in des if math.hypot(dx - X, dy - Y) < DESIGNATOR_TOL_MM]
        if len(hits) == 1:
            e.name = hits[0]
    table = read_table(svg)
    used = {e.name for e in ends if e.name}
    rest = [e for e in ends if not e.name]
    groups = {}
    for e in rest:
        m = procedure.mag(e.az, decl)
        rows = [n for n, h in table if n not in used and min(abs(h - m), 360 - abs(h - m)) <= NAME_TOL_DEG]
        if not rows:
            raise Arret(f'{stem} : extremite de piste au cap {m:.1f} sans nom dans le tableau de la 10-9')
        groups.setdefault(tuple(sorted(rows)), []).append(e)
    for rows, es in groups.items():
        if len(rows) != len(es):
            raise Arret(f'{stem} : {len(es)} extremites pour les lignes {list(rows)} du tableau de la 10-9')
        if len(es) == 1:
            es[0].name = rows[0]
            continue
        ref = es[0]
        es.sort(key=lambda e: procedure.along_cross(e.pos, ref.pos, ref.az)[1])     # de gauche a droite
        rws = sorted(rows, key=lambda n: SUFFIX_ORDER.get(number_of(n)[1], 9))
        if any(number_of(n)[1] not in SUFFIX_ORDER for n in rws):
            raise Arret(f'{stem} : pistes paralleles sans L, C ou R : {list(rows)}')
        for e, n in zip(es, rws):
            e.name = n
    names = [e.name for e in ends]
    if len(set(names)) != len(names):
        raise Arret(f'{stem} : noms d\'extremites en double : {sorted(names)}')
    return ends


@dataclass
class IlsPage:
    page: str
    sens: str
    thr_ft: int
    tch: int
    s_gs: float
    loc: str


def read_ils_page(svg, page_name):
    """La page ILS de la base : (sens, THR, TCH, position du glide, LOC). Arret si elle ne se relit pas."""
    tx = texts(layer_text(svg, 'Header'))
    vals = {}
    for (x0, y0, a0, s0), (x1, y1, a1, s1) in zip(tx, tx[1:]):
        if abs(y0 - 27.4) < 1e-6 and abs(y1 - 27.4) < 1e-6 and a0.get('fill') == '#5A5A5A':
            vals[s0] = s1
    kind = [s for x, y, a, s in tx if abs(y - 15.0) < 1e-6 and s.startswith('ILS ')]
    m_gs = re.search(r'glide slope antenna \((-?\d+) m past the threshold\)', svg)
    try:
        return IlsPage(page_name, kind[0][4:], int(vals['THR'].split()[0]), int(vals['TCH'].split()[0]), float(m_gs.group(1)),
                       vals['LOC'])
    except (IndexError, KeyError, ValueError, AttributeError):
        raise Arret(f'{page_name} : page ILS de la base illisible (sens, THR, TCH, LOC ou glide)') from None


# ---------- balises ----------
def usable_beacons(path):
    """Balises utilisables de `beacons.lua` (4.1, 2) : [procedure.Balise], dans l'ordre du fichier."""
    out = []
    for b in dcsdata.parse_beacons_lua(path).values():
        kind = TYPES.get(b.type)
        if kind is None:
            continue
        ch = ''
        if kind in ('VOR/DME', 'VOR'):
            freq = f'{b.frequency / 1e6:.2f}'
            ch = f'CH{b.channel}X' if (kind == 'VOR/DME' and b.channel) else ''
        elif kind == 'NDB':
            freq = f'{b.frequency / 1000:.0f}'
        elif kind == 'TACAN':
            freq = f'{b.channel}X'
        else:
            freq = f'CH{b.channel}'
        out.append(Balise(kind, b.callsign, freq, b.x, b.z, ch))
    return out


def pick_reference(beacons, thr, az):
    """4.3.1 : le premier type dans l'ordre VOR/DME, VOR, NDB, TACAN, RSBN ; a type egal, la balise devant le seuil et dans
    l'axe a 0,3 NM pres (la plus proche du seuil s'il y en a plusieurs), a defaut la plus proche du seuil."""
    for kind in procedure.KINDS:
        cands = [b for b in beacons if b.kind == kind]
        if not cands:
            continue
        inline = []
        for b in cands:
            al, cr = procedure.along_cross(b.pos, thr, az)
            if al < 0 and abs(cr) < procedure.AXIS_TOL_NM * NM:
                inline.append(b)
        return min(inline or cands, key=lambda b: math.dist(b.pos, thr))
    return None


def option3_ndb(beacons, ref):
    if ref.kind not in ('VOR/DME', 'VOR'):
        return None
    ndbs = [b for b in beacons if b.kind == 'NDB' and math.dist(b.pos, ref.pos) <= OPTION3_M]
    return min(ndbs, key=lambda b: math.dist(b.pos, ref.pos)) if ndbs else None


# ---------- le plan du theatre ----------
@dataclass
class Sens:
    number: str                   # '01'
    end: End                      # l'extremite retenue
    ref: Balise = None
    avion: object = None          # procedure.Procedure
    helico: object = None
    index: str = ''               # '11-1'
    index_h: str = ''             # '11-1H'
    ecart: str = ''               # raison du sens ecarte (page avion)
    ecart_h: str = ''


@dataclass
class Terrain:
    stem: str
    folder: str                   # dossier racine du zip (`<nom du zip>/`)
    header: Header
    airport: str
    pos: tuple                    # position dans le jeu (x, z)
    gap: float                    # ecart du calage (mm)
    runways: list
    ends: list
    beacons: list                 # balises utilisables a 3 NM au plus
    ils: dict                     # sens -> IlsPage (pages ILS de la base)
    ils_names: list               # noms des pages 11-x de la base
    sens: list = field(default_factory=list)
    table: dict = field(default_factory=dict)       # nom d'extremite -> MAG HDG du tableau de la 10-9

    @property
    def case_a(self):
        return bool(self.beacons)


@dataclass
class Plan:
    theatre: str
    terrains: list
    kept_ils: list                # pages ILS des terrains hors du cas A, gardees
    log: list


def _beacon_label(b):
    return f'{b.kind} {b.callsign}'


def charted(zip_af):
    """{stem: (dossier, {suffixe: nom d'entree svg})} des charts du zip *airfields* ; seulement celles qui ont une 10-9."""
    out = {}
    with zipfile.ZipFile(zip_af) as z:
        for name in z.namelist():
            m = _SVG.match(name)
            if not m:
                continue
            pm = _PAGE.match(m['page'])
            if not pm:
                raise Arret(f'{name} : nom de page inattendu')
            out.setdefault(pm['stem'], (m['dir'] or '', {}))[1][pm['suffix']] = name
    return {k: v for k, v in out.items() if '10-9' in v[1]}


def read_terrain(z, stem, folder, pages, terrain_dcs, proj, beacons, year):
    svg = z.read(pages['10-9']).decode('utf-8')
    hd = read_header(svg, stem)
    x, zz = proj.to_xz(hd.lat, hd.lon)
    ap = min(terrain_dcs.airports.values(), key=lambda a: (math.hypot(a.position.x - x, a.position.y - zz), a.id))
    root = plans.parse_svg(svg.encode('utf-8'))
    try:
        circles = plans.parking_circles(root)
        S, OX, OY, _, gap = plans.fit([(p.position.x, p.position.y) for p in ap.parking_slots], [(c[0], c[1]) for c in circles])
    except plans.PlansError as e:
        raise Arret(f'{stem} : calage de la 10-9 impossible ({e})') from None
    rws = read_runways(svg, S, OX, OY, stem)
    lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
    decl_ap = geo.declination(lat, lon, year)
    ends = name_ends(rws, svg, S, OX, OY, decl_ap, stem) if rws else []
    pos = (ap.position.x, ap.position.y)
    near = [b for b in beacons if math.dist(b.pos, pos) <= CASE_A_M]
    ils, ils_names = {}, []
    for suffix, name in sorted(pages.items(), key=lambda kv: dcsdata.natural_key(kv[0])):
        if _ILS_SUFFIX.match(suffix):
            p = read_ils_page(z.read(name).decode('utf-8'), f'{stem}_{suffix}')
            ils[p.sens] = p
            ils_names.append(f'{stem}_{suffix}')
    return Terrain(stem, folder, hd, ap.name, pos, gap, rws, ends, near, ils, ils_names, table=dict(read_table(svg)))


def choose_sens(t):
    """Les sens du terrain (4.1, 4) : par numero de piste, l'extremite qui a une page ILS dans la base (la plus proche de sa
    balise de reference s'il y en a plusieurs), a defaut celle dont le seuil est le plus proche de sa balise de reference."""
    groups = {}
    for e in t.ends:
        groups.setdefault(number_of(e.name)[0], []).append(e)
    out = []
    for num, es in groups.items():
        cands = [e for e in es if e.name in t.ils] or es
        best = None
        for e in sorted(cands, key=lambda e: dcsdata.natural_key(e.name)):
            ref = pick_reference(t.beacons, e.pos, e.az)
            d = math.dist(ref.pos, e.pos)
            if best is None or d < best[0] - 1e-6:
                best = (d, e, ref)
        out.append(Sens(num, best[1], best[2]))
    out.sort(key=lambda s: dcsdata.natural_key(s.end.name))
    return out


def thr_ft_of(t, e, alt, x0, z0):
    if e.name in t.ils:
        return t.ils[e.name].thr_ft
    try:
        return int(round(procedure.ground_m(alt, x0, z0, e.pos) * procedure.FT))
    except ValueError as err:
        raise Arret(f'{t.stem} {e.name} : altitude du seuil : {err}') from None


def entree(t, e, ref, ndb, ils, proj, alt, x0, z0, year):
    """L'entree de `procedure` : declinaison et convergence a la balise (radiales, QDM, MSA), declinaison au seuil (CRS de
    finale, interrompue, eloignement de l'hippodrome) : 4.2.4, « au point dont on parle »."""
    lat, lon = proj.to_latlon(*ref.pos)
    tlat, tlon = proj.to_latlon(*e.pos)
    return Entree(e.name, e.pos, e.other, e.az, t.runways[e.rw].width, [r.corners for r in t.runways], thr_ft_of(t, e, alt, x0, z0),
                  t.header.elev_ft, geo.declination(lat, lon, year), proj.convergence(*ref.pos), ref, ndb,
                  {'s_gs': ils.s_gs, 'tch': ils.tch, 'loc': ils.loc} if ils is not None else None,
                  geo.declination(tlat, tlon, year))


def _alts(P):
    if P.famille == 'helico':
        return f'zone {P.zone_ft} ft (QFE {procedure.qfe_m(P.zone_ft, P.e.elev_ft)} m), MSA attente {P.hold_msa}'
    return f'attente {P.hold_ft}, initiale {P.init_ft}, intermediaire {P.int_ft}, interrompue {P.mis_ft} ft'


def traversal_text(P):
    if not P.traversals:
        return 'traversées aucune'
    return 'traversées ' + ' '.join(f'{n} {h:.0f} ft' for n, _, h in P.traversals)


def journal_line(t, index, P):
    sch = {'base': 'virage de base', 'racetrack': 'hippodrome'}[P.mode]
    ref = P.e.ref
    return (f'approche {t.stem} {index} {P.e.sens} : {sch}, réf {ref.kind} {ref.callsign}, virage {P.turn}, attente {P.hold_turns}, '
            f'{_alts(P)}, pistes {P.d_hold_rw:.0f} / {P.d_out_rw:.0f} m, {traversal_text(P)}')


def plan_theatre(theatre, zip_af, beacons_path, alt, x0, z0, year=1985.0, log=print, check=True):
    """Calcule toutes les procedures (rien n'est dessine ni ecrit). Arret au premier cas du cadrage 7."""
    canon, _ = fiches.resolve_theatre(theatre)
    terrain_dcs = dcsdata.load_terrain(canon)
    proj = geo.Projection.for_theatre(canon)
    beacons = usable_beacons(beacons_path)
    charts = charted(zip_af)
    terrains, kept, crossing = [], [], []
    with zipfile.ZipFile(zip_af) as z:
        for stem in sorted(charts, key=dcsdata.natural_key):
            folder, pages = charts[stem]
            t = read_terrain(z, stem, folder, pages, terrain_dcs, proj, beacons, year)
            terrains.append(t)
            if not t.case_a:
                kept.extend(t.ils_names)
                continue
            t.sens = choose_sens(t)
            n_a = n_h = 0
            for s in t.sens:
                e = s.end
                ndb3 = option3_ndb(t.beacons, s.ref)
                try:
                    E = entree(t, e, s.ref, ndb3, t.ils.get(e.name), proj, alt, x0, z0, year)
                    s.avion = procedure.avion(E, alt, x0, z0)
                    n_a += 1
                    s.index = f'11-{n_a}'
                except GeometrieImpossible as err:
                    s.ecart = str(err)
                    log(f'approche {stem} {e.name} : écartée, {err}')
                except (ValueError, msa_error()) as err:
                    raise Arret(f'{stem} {e.name} : relief : {err}') from None
                ndbs = [b for b in t.beacons if b.kind == 'NDB']
                if not ndbs:
                    continue
                ndb = min(ndbs, key=lambda b: (math.dist(b.pos, e.pos), b.callsign))
                try:
                    E = entree(t, e, ndb, None, t.ils.get(e.name), proj, alt, x0, z0, year)
                    s.helico = procedure.helico(E, alt, x0, z0)
                    n_h += 1
                    s.index_h = f'11-{n_h}H'
                except GeometrieImpossible as err:
                    s.ecart_h = str(err)
                    log(f'approche {stem} {e.name} (hélicoptère) : écartée, {err}')
                except (ValueError, msa_error()) as err:
                    raise Arret(f'{stem} {e.name} : relief : {err}') from None
            for s in t.sens:
                for idx, P in ((s.index, s.avion), (s.index_h, s.helico)):
                    if P is None:
                        continue
                    log(journal_line(t, idx, P))
                    crossing.extend(runway_problems(stem, idx, P))
    for k in kept:
        log(f'page ILS gardée : {k}')
    pl = Plan(canon, terrains, kept, crossing)
    c = counts(pl)
    log(f'théâtre {canon} : {c["charted"]} terrains chartés, {c["case_a"]} du cas A, {c["pages_a"]} pages avion, '
        f'{c["terrains_h"]} terrains et {c["pages_h"]} pages hélicoptère, {c["ecarts"]} sens écartés (avion) sur {c["sens"]} et '
        f'{c["ecarts_h"]} (hélicoptère) sur {c["tried_h"]}, {c["ils_removed"]} pages ILS retirées, {c["ils_kept"]} gardées')
    if check:
        check_plan(crossing, c)
    return pl


def runway_problems(stem, idx, P):
    """Addendum 1 : les cas d'arret d'une page : un segment strict (virage de base, virages de l'hippodrome, intermediaire) qui
    coupe une piste, une traversee a moins de AD ELEV + 1 000 ft."""
    out = [f'{stem} {idx} {P.e.sens} ({n} qui coupe une piste)' for n, d in P.strict_parts.items() if d <= 0]
    out += [f'{stem} {idx} {P.e.sens} ({n} a {h:.0f} ft au-dessus de l\'AD ELEV)' for n, _, h in P.traversals
            if h < procedure.TRAVERSAL_MIN_FT]
    return out


def check_plan(crossing, c):
    """Arrets du theatre : une traversee hors de l'addendum 1 ; plus de 10 % de sens ecartes, les deux familles comptees
    (addendum 2, 2.1 : ecartes avion + helicoptere sur les pages tentees)."""
    if crossing:
        raise Arret(f'traversée de piste hors de l\'addendum 1 : {"; ".join(crossing)}')
    tried = c['sens'] + c['tried_h']
    if tried and c['ecarts'] + c['ecarts_h'] > 0.10 * tried:
        raise Arret(f'{c["ecarts"] + c["ecarts_h"]} sens écartés sur {tried} pages tentées (plus de 10 %)')


def msa_error():
    from render import msa
    return msa.DisqueIncomplet


def counts(pl):
    ta = [t for t in pl.terrains if t.case_a]
    sens = [s for t in ta for s in t.sens]
    return {'charted': len(pl.terrains), 'case_a': len(ta), 'pages_a': sum(1 for s in sens if s.avion is not None),
            'terrains_h': sum(1 for t in ta if any(s.helico is not None for s in t.sens)),
            'pages_h': sum(1 for s in sens if s.helico is not None), 'sens': len(sens),
            'ecarts': sum(1 for s in sens if s.ecart), 'ecarts_h': sum(1 for s in sens if s.ecart_h),
            'tried_h': sum(1 for s in sens if s.helico is not None or s.ecart_h),
            'ils_removed': sum(len(t.ils_names) for t in ta), 'ils_kept': len(pl.kept_ils)}


def terrain_list(pl):
    """La liste par terrain (R4) : une ligne par terrain charte."""
    out = ['terrain\tpydcs\tcas\tbalises (3 NM)\tpages avion\tpages helicoptere\tsens ecartes\tpages ILS retirees\tpages ILS gardees']
    for t in pl.terrains:
        if t.case_a:
            av = ' '.join(f'{s.index}={s.end.name}' for s in t.sens if s.avion is not None)
            he = ' '.join(f'{s.index_h}={s.end.name}' for s in t.sens if s.helico is not None)
            ec = '; '.join([f'{s.end.name}: {s.ecart}' for s in t.sens if s.ecart] + [f'{s.end.name} (H): {s.ecart_h}' for s in t.sens if s.ecart_h])
            out.append('\t'.join([t.stem, t.airport, 'A', ', '.join(f'{b.kind} {b.callsign}' for b in t.beacons), av, he, ec,
                                  ' '.join(t.ils_names), '']))
        else:
            out.append('\t'.join([t.stem, t.airport, '-', '', '', '', '', '', ' '.join(t.ils_names)]))
    return '\n'.join(out) + '\n'


# ---------- les pages ----------
TEXT_BOX = re.compile(r'<text x="([^"]+)" y="([^"]+)" ([^>]*)>(.*?)</text>', re.S)


def text_overlaps(svg):
    """Chevauchements des boites de texte d'une page (R6) : paires de <text> dont les boites se coupent (aire > 0,01 mm2), le halo
    (copie blanche juste avant son texte) et les textes tournes compris."""
    from render import page as pg
    boxes = []
    for m in re.finditer(r'<g transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+)\)">(.*?)</g>|' + TEXT_BOX.pattern, svg, re.S):
        if m.group(1) is not None:
            if 'class="halo"' in m.group(4) and m.group(4).count('<text') == 1:
                continue
            g = pg.elem_geom(m.group(0))
            boxes.append((g, html.unescape(re.sub(r'<[^>]+>', '', m.group(4)))))
            continue
        if 'class="halo"' in m.group(0):
            continue
        g = pg.elem_geom(m.group(0))
        boxes.append((g, html.unescape(m.group(8))))
    out = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][0], boxes[j][0]
            if a.intersects(b) and a.intersection(b).area > 0.01:
                out.append((boxes[i][1], boxes[j][1]))
    return out


def render_pages(pl, alt, x0, z0, year, work, log=print):
    """Dessine et exporte toutes les pages dans `work` : {(stem, index): (svg, pdf, png, info)}."""
    out = {}
    for t in pl.terrains:
        if not t.case_a:
            continue
        site = approach_ifr.Site([], t.header.elev_ft, year, [(r.p0, r.p1, r.width) for r in t.runways], t.beacons, alt, x0, z0)
        for s in t.sens:
            for idx, P in ((s.index, s.avion), (s.index_h, s.helico)):
                if P is None:
                    continue
                title = approach_ifr.title_helico(P) if P.famille == 'helico' else approach_ifr.title_avion(P)
                site.header = header_lines(t.header, idx, title, elev_m=P.famille == 'helico')
                po = approach_ifr.build_page(site, P, idx, log)
                ov = text_overlaps(po.svg)
                po.info['overlaps'] = ov
                for name in po.info['failed']:
                    log(f'étiquette sans place libre : {t.stem}_{idx} {name}')
                for a, b in ov:
                    log(f'chevauchement : {t.stem}_{idx} « {a} » / « {b} »')
                base = Path(work) / f'{t.stem}_{idx}'
                svg_p, pdf_p, png_p = export.export_page(po.svg, base)
                out[(t.stem, idx)] = (svg_p, pdf_p, png_p, po.info)
    return out


# ---------- les zips ----------
def _zinfo(name, like):
    zi = zipfile.ZipInfo(name, like.date_time)
    zi.compress_type, zi.external_attr, zi.create_system = like.compress_type, like.external_attr, like.create_system
    return zi


def _write_file(zout, zi, path):
    with open(path, 'rb') as src, zout.open(zi, 'w') as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def _copy(zin, zout, info):
    zi = _zinfo(info.filename, info)
    zi.comment = info.comment
    with zin.open(info) as src, zout.open(zi, 'w') as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def build_recueils(pl, zip_af, rendered, work):
    """Recueils refaits des terrains du cas A : {entree du recueil: (chemin, pages avant, pages apres)}."""
    from pypdf import PdfReader
    out = {}
    with zipfile.ZipFile(zip_af) as z:
        names = set(z.namelist())
        for t in pl.terrains:
            if not t.case_a:
                continue
            entry = f'{t.folder}pdf/{t.stem}_charts.pdf'
            if entry not in names:
                raise Arret(f'{t.stem} : recueil {entry} absent du zip')
            pages = []
            for n in sorted(names):
                m = re.match(rf'^{re.escape(t.folder)}pdf/{re.escape(t.stem)}_(?P<suffix>10-8|10-9[A-Z]*|11-\d+)\.pdf$', n)
                if m and not _ILS_SUFFIX.match(m['suffix']):
                    pages.append((m['suffix'], n))
            pages.sort(key=lambda p: dcsdata.natural_key(p[0]))
            srcs = []
            for k, (_, n) in enumerate(pages):
                p = Path(work) / f'base_{t.stem}_{k}.pdf'
                p.write_bytes(z.read(n))
                srcs.append(p)
            new = [s.index for s in t.sens if s.avion is not None] + [s.index_h for s in t.sens if s.helico is not None]
            srcs += [rendered[(t.stem, i)][1] for i in new]
            dst = Path(work) / f'{t.stem}_charts.pdf'
            export.merge_pdfs(srcs, dst)
            before = len(PdfReader(io.BytesIO(z.read(entry))).pages)
            after = len(PdfReader(str(dst)).pages)
            if after != before - len(t.ils_names) + len(new):
                raise Arret(f'{t.stem} : recueil de {after} pages ({before} dans la base, {len(t.ils_names)} pages ILS retirees, '
                            f'{len(new)} ajoutees)')
            out[entry] = (dst, before, after)
    return out


def write_zips(pl, zip_af, zip_he, rendered, recueils, out_dir):
    """Les deux zips, sous des noms temporaires puis renommes ; tout est retire sur une erreur."""
    out_dir = Path(out_dir)
    targets = [out_dir / Path(zip_af).name, out_dir / Path(zip_he).name]
    temps = [p.with_name(p.name + '.tmp') for p in targets]
    removed = {f'{t.folder}{sub}/{n}.{sub}' for t in pl.terrains if t.case_a for n in t.ils_names for sub in ('svg', 'png', 'pdf')}
    try:
        with zipfile.ZipFile(zip_af) as zin, zipfile.ZipFile(temps[0], 'x') as zout:
            infos = zin.infolist()
            by_recueil = {f'{t.folder}pdf/{t.stem}_charts.pdf': t for t in pl.terrains if t.case_a}
            for info in infos:
                if info.filename in removed:
                    continue
                t = by_recueil.get(info.filename)
                if t is None:
                    _copy(zin, zout, info)
                    continue
                for s in t.sens:
                    if s.avion is None:
                        continue
                    svg_p, pdf_p, png_p, _ = rendered[(t.stem, s.index)]
                    for sub, p in (('pdf', pdf_p), ('png', png_p), ('svg', svg_p)):
                        _write_file(zout, _zinfo(f'{t.folder}{sub}/{t.stem}_{s.index}.{sub}', info), p)
                zi = _zinfo(info.filename, info)
                zi.comment = info.comment
                _write_file(zout, zi, recueils[info.filename][0])
            like = {f'{t.stem}': zin.getinfo(f'{t.folder}pdf/{t.stem}_charts.pdf') for t in pl.terrains if t.case_a}
        with zipfile.ZipFile(zip_he) as zin, zipfile.ZipFile(temps[1], 'x') as zout:
            infos = zin.infolist()
            roots = {i.filename.split('/')[0] + '/' for i in infos if '/' in i.filename}
            if len(roots) != 1:
                raise Arret(f'{Path(zip_he).name} : {len(roots)} dossiers racine (un seul attendu)')
            root = roots.pop()
            names = {i.filename for i in infos}
            for info in infos:
                _copy(zin, zout, info)
            for t in pl.terrains:
                if not t.case_a:
                    continue
                for s in t.sens:
                    if s.helico is None:
                        continue
                    svg_p, pdf_p, png_p, _ = rendered[(t.stem, s.index_h)]
                    for sub, p in (('pdf', pdf_p), ('png', png_p), ('svg', svg_p)):
                        n = f'{root}{sub}/{t.stem}_{s.index_h}.{sub}'
                        if n in names:
                            raise Arret(f'{n} existe deja dans le zip helicopters')
                        _write_file(zout, _zinfo(n, like[t.stem]), p)
        done = []
        try:
            for tmp, dst in zip(temps, targets):
                tmp.replace(dst)
                done.append(dst)
        except BaseException:
            for p in done:                         # les deux zips ensemble, ou rien
                p.unlink(missing_ok=True)
            raise
    except BaseException:
        for p in temps:
            p.unlink(missing_ok=True)
        raise
    return targets


# ---------- la ligne de commande ----------
class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def parse_args(argv):
    ap = _Parser(prog='build_approaches.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='Syria ou GermanyColdWar')
    ap.add_argument('--zips', nargs=2, required=True, metavar=('AIRFIELDS', 'HELICOPTERS'), help='les deux zips de la release')
    ap.add_argument('--beacons', required=True, help='beacons.lua du theatre')
    ap.add_argument('--grille', required=True, metavar='RELIEF_NPY', help='relief_<cle>.npy (le .json est lu a cote)')
    ap.add_argument('--out', required=True, help='dossier de sortie (les deux zips, memes noms que ceux de la base)')
    ap.add_argument('--annee', type=float, default=1985.0, help='annee de la declinaison (defaut 1985)')
    a = ap.parse_args(argv)
    try:
        fiches.resolve_theatre(a.theatre)
    except fiches.FichesError as e:
        raise _Usage(str(e)) from None
    for p in list(a.zips) + [a.beacons, a.grille]:
        if not Path(p).is_file():
            raise _Usage(f'entree introuvable : {p}')
    if not Path(a.out).is_dir():
        raise _Usage(f'dossier de sortie introuvable : {a.out}')
    if 'airfields' not in Path(a.zips[0]).name or 'helicopters' not in Path(a.zips[1]).name:
        raise _Usage('--zips : le zip airfields puis le zip helicopters')
    return a


def run(a, log=print):
    canon, key = fiches.resolve_theatre(a.theatre)
    for z in a.zips:
        dst = Path(a.out) / Path(z).name
        if dst.exists():
            raise Arret(f'fichier de sortie deja present : {dst}')
    try:
        alt, x0, z0 = build_msa.charger_grille(a.grille, canon, key)
    except build_msa._Refus as e:
        raise Arret(str(e)) from None
    pl = plan_theatre(canon, a.zips[0], a.beacons, alt, x0, z0, a.annee, log)
    with tempfile.TemporaryDirectory(prefix='approches_') as work:
        rendered = render_pages(pl, alt, x0, z0, a.annee, work, log)
        recueils = build_recueils(pl, a.zips[0], rendered, work)
        for entry, (_, before, after) in recueils.items():
            log(f'recueil {PurePosixPath(entry).name} : {before} -> {after} pages')
        targets = write_zips(pl, a.zips[0], a.zips[1], rendered, recueils, a.out)
    failed = sum(len(v[3]['failed']) for v in rendered.values())
    ov = sum(len(v[3]['overlaps']) for v in rendered.values())
    log(f'pages dessinées : {len(rendered)} ; étiquettes sans place libre : {failed} ; chevauchements de textes : {ov}')
    for p in targets:
        log(f'écrit : {p}')
    return pl, rendered, targets


def main(argv=None):
    try:
        a = parse_args(sys.argv[1:] if argv is None else argv)
    except _Usage as e:
        print(f'usage : {e}', file=sys.stderr)
        return 1
    try:
        run(a)
    except Arret as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 2
    except Exception as e:                         # addendum 2, 2.6 : toute autre erreur, une ligne, code 2
        print(f'ARRET : {type(e).__name__} : {" ".join(str(e).split())}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
