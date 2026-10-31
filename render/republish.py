"""Republication des charts deja publiees, sans les captures F10 : toutes les retouches que le moteur sait faire sur un SVG publie.

Usage : python -I render/republish.py <Theatre> --zip <zip de release> --out <nouveau zip>
Entree : un zip de la release (airfields ou helicopters). Chaque `…/svg/<page>.svg` est une page de chart. Retouches :
  1. la ligne d'en-tete (textes a y = 21.2) : a cinq champs, elle est verifiee egale a `page.header_row` et laissee telle
     quelle ; a trois champs (AC-V1.4.1), elle est reecrite comme `update_coords.py` ;
  2. la fin de la barre d'echelle : la page `10-9` a exactement une barre (ligne horizontale de 0,35 mm) ; si aucune etiquette
     de metres ne finit par ` M`, la graduation de fin recoit `page.scale_end_label` (750 M, 1250 M, 1750 M). Une page de
     parkings (`10-9A`, `10-9B`…) n'a pas de barre ; en trouver une est une erreur ;
  3. les etiquettes de la page `10-9` (lot caps-balises-graduations) : caps de piste, libelles de balises, graduations lat/lon,
     placees par les memes fonctions que le moteur (`page.place_caps`, `place_navaid_labels`, `place_graduations`) d'apres la
     geometrie relue dans le SVG ; pour chaque cap, la place d'origine recalculee depuis le seuil, l'axe et l'echelle du plan
     doit etre la place publiee a 0,01 mm pres (sinon arret) ;
  4. `xlink:href` sur une `<image>` qui ne l'a pas (page dessinee sur le fond F10).
Chaque page est ensuite reexportee (SVG, PDF, PNG ; une page avec `<image>` : PNG a 256 couleurs, /Interpolate dans le PDF) et le
recueil `pdf/<stem>_charts.pdf` de chaque chart est refait. Sortie : un nouveau zip, memes entrees dans le meme ordre ; les
entrees hors charts sont recopiees a l'octet (un zip sans page de chart est recopie tel quel). Un fichier de sortie qui existe
deja : arret. Au moindre doute, rien n'est ecrit.
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from pypdf import PdfReader                                           # noqa: E402

from render import dcsdata, export, page, update_coords               # noqa: E402
from render.svgdoc import INK, INK2, f                                # noqa: E402
from render.update_coords import ROW_Y, UpdateError, _PAGE_NAME, _PAGE_SVG, _TEXT, _Y   # noqa: E402

RepublishError = UpdateError
BAR_WIDTH = '0.35'
_LINE = re.compile(r'<line\b([^>]*?)/>')
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
_IMAGE = re.compile(r'<image\b[^>]*>')
_HREF = re.compile(r'(?<![\w:])href="([^"]*)"')
EPS = 0.0015                       # mm : deux nombres ecrits a trois decimales


def _attrs(s):
    return dict(_ATTR.findall(s))


# ---------- 1. ligne d'en-tete ----------
def header_elements(svg_text, name):
    """[(debut, fin, texte lu)] des <text> a y = 21.2 : trois (AC-V1.4.1) ou cinq (AC-V1.5.0), sinon RepublishError."""
    found = []
    for m in _TEXT.finditer(svg_text):
        y = _Y.search(m.group(1))
        if y and y.group(1) == ROW_Y:
            found.append((m.start(), m.end(), html.unescape(m.group(2))))
    if len(found) not in (3, 5):
        raise RepublishError(f'{name} : {len(found)} textes a y = {ROW_Y} (attendu : 3 ou 5)')
    return found


def check_or_rewrite_header(svg_text, name, index):
    """(nouveau SVG, [champs], 'reecrit' | 'verifie') : trois champs -> `update_coords.rewrite_page` ; cinq champs -> egaux,
    element par element et a l'octet, a ceux de `page.header_row`, sinon RepublishError."""
    found = header_elements(svg_text, name)
    if len(found) == 3:
        new, fields = update_coords.rewrite_page(svg_text, name, index)
        return new, fields, 'reecrit'
    texts = [t for _, _, t in found]
    if not texts[1].startswith('ARP '):
        raise RepublishError(f"{name} : le deuxieme texte de la ligne ne commence pas par « ARP » : {texts[1]!r}")
    hits = index.get(texts[1], [])
    if len(hits) != 1:
        raise RepublishError(f'{name} : {texts[1]!r} designe {len(hits)} aerodromes pydcs '
                             f'({", ".join(n for n, _, _ in hits) or "aucun"}) au lieu d\'un seul')
    _, lat, lon = hits[0]
    try:
        expected = page.header_row(texts[0], lat, lon, texts[4])
    except ValueError as e:
        raise RepublishError(f'{name} : {e}') from None
    for k, (s, e, _) in enumerate(found):
        if svg_text[s:e] != expected[k]:
            raise RepublishError(f"{name} : le champ {k + 1} de la ligne d'en-tete differe de page.header_row : "
                                 f'{svg_text[s:e]!r} au lieu de {expected[k]!r}')
    for k in range(4):
        sep = svg_text[found[k][1]:found[k + 1][0]]
        if sep.strip():
            raise RepublishError(f'{name} : separateur inattendu dans la ligne d\'en-tete : {sep!r}')
    return svg_text, texts, 'verifie'


# ---------- 2. fin de la barre d'echelle ----------
def find_bars(svg_text):
    """Les lignes horizontales de 0,35 mm : [(debut, fin, attributs)]."""
    out = []
    for m in _LINE.finditer(svg_text):
        a = _attrs(m.group(1))
        if a.get('stroke-width') == BAR_WIDTH and a.get('y1') is not None and a.get('y1') == a.get('y2'):
            out.append((m.start(), m.end(), a))
    return out


def complete_scale_bar(svg_text, name, suffix):
    """(nouveau SVG, None | (texte, x, y)) : la page `10-9` a exactement une barre, une page de parkings n'en a aucune
    (RepublishError sinon). Si la barre n'a pas d'etiquette de metres finissant par ` M`, l'etiquette de fin est inseree juste
    apres la ligne de la graduation de fin ; une barre qui a deja son « M » ne change pas."""
    bars = find_bars(svg_text)
    if suffix != '10-9':
        if bars:
            raise RepublishError(f'{name} : page de parkings avec {len(bars)} barre(s) d\'echelle (attendu : aucune)')
        return svg_text, None
    if len(bars) != 1:
        raise RepublishError(f'{name} : {len(bars)} barres d\'echelle (attendu : une)')
    _, _, bar = bars[0]
    x0_bar, x_end, y = float(bar['x1']), float(bar['x2']), float(bar['y1'])
    if x_end <= x0_bar:
        raise RepublishError(f'{name} : barre d\'echelle de longueur nulle ou inversee')
    labels = []
    for m in _TEXT.finditer(svg_text):
        a = _attrs(m.group(1))
        if a.get('text-anchor') != 'middle' or 'x' not in a or 'y' not in a:
            continue
        if abs(float(a['y']) - (y + 4.2)) < EPS and x0_bar - EPS <= float(a['x']) <= x_end + EPS:
            labels.append((float(a['x']), html.unescape(m.group(2))))
    if any(t.endswith(' M') for _, t in labels):
        return svg_text, None
    xs = {t: [x for x, tt in labels if tt == t] for t in ('0', '500')}
    if len(xs['0']) != 1 or len(xs['500']) != 1:
        raise RepublishError(f'{name} : etiquettes « 0 » et « 500 » de la barre introuvables ou multiples '
                             f'(etiquettes de metres : {[t for _, t in labels]})')
    length = (x_end - xs['0'][0]) * 500 / (xs['500'][0] - xs['0'][0])
    L = round(length / 250) * 250
    if abs(length - L) > 0.1 or L <= 0:
        raise RepublishError(f'{name} : longueur de la barre {length:.2f} m, pas un multiple de 250 m')
    if L % 500 == 0:
        raise RepublishError(f'{name} : la barre de {L} m finit sur une graduation majeure sans etiquette « M »')
    ticks = []
    for m in _LINE.finditer(svg_text):
        a = _attrs(m.group(1))
        if (a.get('x1') is not None and a.get('x1') == a.get('x2') and abs(float(a['x1']) - x_end) < EPS
                and abs(float(a['y1']) - y) < EPS and float(a['y2']) > float(a['y1'])):
            ticks.append((m.end(), float(a['x1'])))
    if len(ticks) != 1:
        raise RepublishError(f'{name} : {len(ticks)} graduations de fin de barre (attendu : une)')
    end, xm = ticks[0]
    el = page.scale_end_label(xm, y, L)
    return svg_text[:end] + '\n' + el + svg_text[end:], (f'{L} M', xm, y + 4.2)


# ---------- 3. etiquettes : caps, balises, graduations ----------
_NAV_DOT = re.compile(r'^<circle cx="([-\d.]+)" cy="([-\d.]+)" r="0\.3" fill="#1A1A1A"/>$')
_NAV_SHAPE = re.compile(r'^<(polygon|circle) ')
_NAV_TEXT = re.compile(r'^<text x="([-\d.]+)" y="([-\d.]+)" [^>]*>(.*)</text>$')
_CAP_G = re.compile(r'^<g transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+)\)"><text [^>]*>(.*)</text></g>$')
_RECT_WHITE = re.compile(r'<g inkscape:groupmode="layer" id="layer-inset-[^"]*" inkscape:label="[^"]*">\n(<rect [^>]*fill="#FFFFFF"/>)')
_GRAD_TEXT = re.compile(r'^<text x="([-\d.]+)" y="([-\d.]+)" font-family="Barlow Condensed" font-size="([-\d.]+)" fill="#5A5A5A"'
                        r'(?: text-anchor="(\w+)")?>(\d{2,3}-\d{2})</text>$')
CAP_CHECK_MM = 0.01                # controle du cadrage 2.4 : place d'origine recalculee = place publiee, a 0,01 mm (et 0,01 deg) pres


def _layer_lines(svg_text, label):
    """[(debut, fin, ligne)] des lignes du calque `label` (son contenu est un element par ligne), ou [] s'il n'existe pas. Le
    calque finit a la ligne `</g>` qui lui correspond."""
    m = re.search(r'<g inkscape:groupmode="layer" id="[^"]*" inkscape:label="' + re.escape(label.replace('&', '&amp;')) + r'"[^>]*>\n',
                  svg_text)
    if not m:
        return []
    depth, pos, out = 1, m.end(), []
    for t in re.finditer(r'<g\b|</g>', svg_text[pos:]):
        depth += 1 if t.group(0) == '<g' else -1
        if depth == 0:
            body_end = pos + t.start()
            break
    else:
        raise RepublishError(f'calque {label} : balise fermante introuvable')
    i = pos
    for line in svg_text[pos:body_end].rstrip('\n').split('\n'):
        out.append((i, i + len(line), line))
        i += len(line) + 1
    return out


def _clip_frame(svg_text, name):
    m = re.findall(r'<clipPath id="clip-plan"><rect x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)" height="([-\d.]+)"/></clipPath>', svg_text)
    if len(m) != 1:
        raise RepublishError(f'{name} : {len(m)} cadres « clip-plan » (attendu : un)')
    return tuple(float(v) for v in m[0])


def _runway_ends(pts, name):
    """Polygone de piste (quatre points) -> (A, B, largeur) : A et B milieux des deux petits cotes (les seuils)."""
    if len(pts) != 4:
        raise RepublishError(f'{name} : un polygone du calque Runways a {len(pts)} points (attendu : 4)')
    e = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    L = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in e]
    if not (max(L[1], L[3]) < min(L[0], L[2])) or abs(L[1] - L[3]) > 0.01:
        raise RepublishError(f'{name} : un polygone du calque Runways n\'est pas un rectangle allonge ({[round(v, 3) for v in L]})')
    mid = lambda k: ((e[k][0][0] + e[k][1][0]) / 2, (e[k][0][1] + e[k][1][1]) / 2)
    return mid(3), mid(1), L[1]


def _scale_of(svg_text, name):
    """Echelle du plan (mm par metre) lue sur la barre : premiere et derniere etiquette de metres."""
    bars = find_bars(svg_text)
    if len(bars) != 1:
        raise RepublishError(f'{name} : {len(bars)} barres d\'echelle (attendu : une) : echelle du plan illisible')
    a = bars[0][2]
    x0, x1, y = float(a['x1']), float(a['x2']), float(a['y1'])
    vals = []
    for m in _TEXT.finditer(svg_text):
        t = _attrs(m.group(1))
        if t.get('text-anchor') == 'middle' and t.get('fill') == INK and abs(float(t['y']) - (y + 4.2)) < EPS \
                and x0 - EPS <= float(t['x']) <= x1 + EPS:
            v = html.unescape(m.group(2))
            v = v[:-2] if v.endswith(' M') else v
            if not v.isdigit():
                raise RepublishError(f'{name} : etiquette de metres illisible : {m.group(2)!r}')
            vals.append((float(t['x']), int(v)))
    vals.sort()
    if len(vals) < 2 or vals[-1][1] == vals[0][1]:
        raise RepublishError(f'{name} : moins de deux etiquettes de metres sur la barre : echelle du plan illisible')
    return (vals[-1][0] - vals[0][0]) / (vals[-1][1] - vals[0][1]), (x0, x1, y)


def _same_text(published, expected):
    """Deux elements <text> identiques, sauf x et y qui peuvent differer de 0,0015 mm (deux arrondis a trois decimales)."""
    a, b = _attrs(re.match(r'<text ([^>]*)>', published).group(1)), _attrs(re.match(r'<text ([^>]*)>', expected).group(1))
    return (set(a) == set(b) and all(a[k] == b[k] for k in a if k not in ('x', 'y'))
            and abs(float(a['x']) - float(b['x'])) < EPS and abs(float(a['y']) - float(b['y'])) < EPS
            and published.split('>', 1)[1] == expected.split('>', 1)[1])


def _read_navaids(lines, name):
    """Calque Navaids -> (symboles [(type, X, Y)], groupes [{members, lines, xs, ys, texts: [(debut, fin, ligne)]}]).
    Un symbole = sa forme (polygone ou cercle pointille) puis son point central (cercle de 0,3) ; les deux textes d'un libelle
    suivent le premier symbole de leur groupe ; le libelle publie doit etre a la place « a droite » que donne le moteur."""
    syms, label_after = [], {}
    shape = None
    k = 0
    while k < len(lines):
        _, _, line = lines[k]
        if _NAV_DOT.match(line):
            if shape is None:
                raise RepublishError(f'{name} : point central de balise sans symbole : {line}')
            m = _NAV_DOT.match(line)
            syms.append(('NDB' if shape.startswith('<circle') else 'VOR', float(m.group(1)), float(m.group(2))))
            shape = None
        elif _NAV_SHAPE.match(line):
            if shape is not None:
                raise RepublishError(f'{name} : deux symboles de balise de suite : {line}')
            shape = line
        elif _NAV_TEXT.match(line):
            if not syms or shape is not None or k + 1 >= len(lines) or not _NAV_TEXT.match(lines[k + 1][2]):
                raise RepublishError(f'{name} : libelle de balise mal place : {line}')
            label_after[len(syms) - 1] = (lines[k], lines[k + 1])
            k += 1
        else:
            raise RepublishError(f'{name} : element inattendu dans le calque Navaids : {line}')
        k += 1
    if shape is not None:
        raise RepublishError(f'{name} : symbole de balise sans point central')
    groups = []
    for grp in page.navaid_groups([(X, Y) for _, X, Y in syms]):
        if (grp[0] in label_after) != True or any(i in label_after for i in grp[1:]):
            raise RepublishError(f'{name} : les libelles de balises ne suivent pas les premiers symboles des groupes')
        l1, l2 = label_after[grp[0]]
        texts = []
        for _, _, ln in (l1, l2):
            texts.append(html.unescape(_NAV_TEXT.match(ln).group(3)))
        xs, ys = [syms[i][1] for i in grp], [syms[i][2] for i in grp]
        current = None
        for pos in page.NAVAID_POSITIONS:                   # la place publiee est l'une des quatre du moteur (une page deja retouchee)
            expected = page.navaid_label_elems(texts, pos, xs, ys)
            if all(_same_text(ln, exp) for (_, _, ln), exp in zip((l1, l2), expected)):
                current = pos
                break
        if current is None:
            raise RepublishError(f'{name} : libelle de balise different de ceux du moteur : {l1[2]!r}')
        groups.append({'members': grp, 'lines': tuple(texts), 'xs': xs, 'ys': ys, 'span': (l1[0], l2[1]), 'elems': [l1[2], l2[2]],
                       'current': current})
    return syms, groups


def _read_caps(lines, name):
    out = []
    for st, en, line in lines:
        m = _CAP_G.match(line)
        if not m:
            raise RepublishError(f'{name} : element inattendu dans le calque Runway bearings : {line}')
        out.append({'span': (st, en), 'line': line, 'X': float(m.group(1)), 'Y': float(m.group(2)), 'rot': float(m.group(3)),
                    'label': html.unescape(m.group(4))})
    return out


def _cap_end_perpendicular(c, runways, s):
    """CapEnd d'un cap a sa place d'origine (perpendiculaire a l'axe, avant le seuil), ou None s'il n'en a pas la forme."""
    best = None
    for k, (A, B, w) in enumerate(runways):
        dx, dy = B[0] - A[0], B[1] - A[1]
        L = math.hypot(dx, dy)
        ax, ay = dx / L, dy / L
        vx, vy = c['X'] - A[0], c['Y'] - A[1]
        t = vx * ax + vy * ay
        dist = abs(ax * vy - ay * vx)
        if dist > 0.05 or 0.0 <= t <= L:
            continue
        gap = -t if t < 0 else t - L
        thr, far = (A, B) if t < 0 else (B, A)
        if best is None or gap < best[0]:
            best = (gap, thr, far, w)
    if best is None:
        return None
    _, thr, far, w = best
    d = math.hypot(far[0] - thr[0], far[1] - thr[1])
    u1 = (math.sin(math.radians(c['rot'])), -math.cos(math.radians(c['rot'])))
    az = c['rot'] if (u1[0] * (far[0] - thr[0]) + u1[1] * (far[1] - thr[1])) / d > 0 else c['rot'] + 180.0
    u = (math.sin(math.radians(az)), -math.cos(math.radians(az)))
    return page.CapEnd(c['label'], thr, u, w / 2, az)


def _cap_end_parallel(c, runways):
    """CapEnd d'un cap deja pose le long de la piste (republication d'une page deja retouchee) : un cap parallele a l'axe, sur une
    des positions candidates du cadrage 2.1 (a 0,01 mm pres) du seuil le plus proche d'un des polygones ; None sinon."""
    for A, B, w in runways:
        for thr, far in ((A, B), (B, A)):
            d = math.hypot(far[0] - thr[0], far[1] - thr[1])
            u = ((far[0] - thr[0]) / d, (far[1] - thr[1]) / d)
            az = math.degrees(math.atan2(u[0], -u[1])) % 360.0
            end = page.CapEnd(c['label'], thr, u, w / 2, az)
            if abs(page.cap_norm(az - 90.0) - c['rot']) > CAP_CHECK_MM:
                continue
            for cx, cy, rot in page.cap_candidates(end):
                if abs(cx - c['X']) <= CAP_CHECK_MM and abs(cy - c['Y']) <= CAP_CHECK_MM and abs(rot - c['rot']) <= CAP_CHECK_MM:
                    end.current = (c['X'], c['Y'], c['rot'])
                    return end
    return None


def _cap_ends(caps, runways, s, name):
    """Un CapEnd par cap publie : le seuil est le milieu du petit cote du polygone dont l'axe passe par l'etiquette, du cote de
    l'etiquette ; le sens d'atterrissage est celui de l'angle publie qui va du seuil vers l'interieur de la piste.
    Controle (cadrage 2.4) : la place d'origine recalculee par `page.cap_original` est la place publiee a 0,01 mm pres. Un cap
    parallele a son axe sur une position candidate du 2.1 est un cap deja place (republication d une page deja retouchee)."""
    ends = []
    for c in caps:
        end = _cap_end_perpendicular(c, runways, s)
        if end is None:
            end = _cap_end_parallel(c, runways)
            if end is None:
                raise RepublishError(f'{name} : cap {c["label"]} : aucune piste dont l\'axe passe par l\'etiquette, et pas sur une '
                                     f'position le long d\'une piste ({c["X"]}, {c["Y"]}, {c["rot"]})')
            ends.append(end)
            continue
        cx, cy, rot = page.cap_original(end, s)
        if abs(cx - c['X']) > CAP_CHECK_MM or abs(cy - c['Y']) > CAP_CHECK_MM or abs(rot - c['rot']) > CAP_CHECK_MM:
            raise RepublishError(f'{name} : cap {c["label"]} : la place d\'origine recalculee depuis la geometrie du SVG '
                                 f'({cx:.3f}, {cy:.3f}, {rot:.2f}) differe de la place publiee ({c["X"]}, {c["Y"]}, {c["rot"]})')
        ends.append(end)
    return ends


def retouch_labels(svg_text, name):
    """(nouveau SVG, deplacements) : cadrage 2.4. deplacements : [(calque, texte, avant, apres)]."""
    runways = []
    for _, _, ln in _layer_lines(svg_text, 'Runways'):
        m = re.match(r'^<polygon points="([^"]+)" fill="#1A1A1A"/>$', ln)
        if not m:
            raise RepublishError(f'{name} : element inattendu dans le calque Runways : {ln}')
        runways.append([tuple(float(v) for v in p.split(',')) for p in m.group(1).split()])
    runway_geoms = [page.elem_geom(f'<polygon points="{" ".join(f"{x},{y}" for x, y in p)}" fill="#1A1A1A"/>') for p in runways]
    cap_lines = _layer_lines(svg_text, 'Runway bearings')
    caps = _read_caps(cap_lines, name)
    nav_lines = _layer_lines(svg_text, 'Navaids')
    syms, groups = _read_navaids(nav_lines, name)
    fg = _layer_lines(svg_text, 'Frame & grid')
    labels, spans = [], []
    for st, en, ln in fg:
        m = _GRAD_TEXT.match(ln)
        if m:
            lab = page.GradLabel(float(m.group(1)), float(m.group(2)), m.group(5), m.group(4) or 'start')
            if f(float(m.group(3))) != f(page.GRAD_SIZE) or lab.anchor not in ('start', 'middle') or page.grad_element(lab) != ln:
                raise RepublishError(f'{name} : libelle de graduation different de celui du moteur : {ln!r}')
            labels.append(lab)
            spans.append((st, en))
    if not (caps or groups or labels):
        return svg_text, [], []                               # rien a placer sur cette page
    frame = _clip_frame(svg_text, name)
    covers = [page.elem_geom(m.group(1)) for m in _RECT_WHITE.finditer(svg_text)]
    edits, moves = [], []

    taxi = [page.elem_geom(ln) for _, _, ln in _layer_lines(svg_text, 'Taxiway labels') if ln.startswith('<rect ')]

    # --- caps
    sym_geoms = [page.elems_geom(_sym_elems(k, X, Y)) for k, X, Y in syms]
    nav_orig = sym_geoms + [page.elems_geom(page.navaid_label_elems(g['lines'], 'right', g['xs'], g['ys'])) for g in groups]
    if caps:
        if len(caps) != 2 * len(runways):
            raise RepublishError(f'{name} : {len(caps)} caps pour {len(runways)} pistes (attendu : deux par piste)')
        s, _ = _scale_of(svg_text, name)
        ends = _cap_ends(caps, [_runway_ends(p, name) for p in runways], s, name)
        placed = page.place_caps(ends, s, frame, runway_geoms, nav_orig, covers, page.grad_blockers(labels) + taxi)
    else:
        ends, placed = [], []
    cap_polys = []
    for c, (cx, cy, rot, poly, moved) in zip(caps, placed):
        if moved:
            edits.append((c['span'][0], c['span'][1], page.cap_element(c['label'], cx, cy, rot)))
            moves.append(('Runway bearings', c['label'], f'{c["X"]},{c["Y"]},{c["rot"]}', f'{f(cx)},{f(cy)},{f(rot, 2)}'))
            cap_polys.append(poly)
        else:
            cap_polys.append(page.cap_box(c['label'], c['X'], c['Y'], c['rot']))

    # --- balises
    res = page.place_navaid_labels([(g['lines'], g['xs'], g['ys']) for g in groups], frame, runway_geoms, cap_polys)
    nav_geoms = list(sym_geoms)
    unplaced = []
    for g, (pos, elems, free) in zip(groups, res):
        if not free:
            unplaced.append(g['lines'][0])
        if pos != g['current']:
            edits.append((g['span'][0], g['span'][1], '\n'.join(elems)))
            moves.append(('Navaids', f'{g["lines"][0]} / {g["lines"][1]}', f'{g["current"]} ({_xy(g["elems"][0])})', f'{pos} ({_xy(elems[0])})'))
            nav_geoms.append(page.elems_geom(elems))
        else:
            nav_geoms.append(page.elems_geom(g['elems']))

    # --- graduations
    if labels:
        north, scale = _north_scale(fg, svg_text, name)
        res = page.place_graduations(labels, frame, {'encart': covers, 'nord': north, 'echelle': scale, 'cap': cap_polys,
                                                    'balise': nav_geoms, 'taxi': taxi})
        for lab, (st, en), (x, y, reason) in zip(labels, spans, res):
            if x is None:
                edits.append((st - 1, en, ''))                 # le libelle et son saut de ligne ; le trait reste
                moves.append(('Frame & grid', lab.s, f'{lab.x},{lab.y}', f'retire ({reason})'))
            elif reason == 'glisse':
                edits.append((st, en, page.grad_element(lab, x, y)))
                moves.append(('Frame & grid', lab.s, f'{lab.x},{lab.y}', f'{f(x)},{f(y)}'))
    for st, en, rep in sorted(edits, reverse=True):
        svg_text = svg_text[:st] + rep + svg_text[en:]
    return svg_text, moves, unplaced


def _xy(el):
    a = _attrs(re.match(r'<text ([^>]*)>', el).group(1))
    return f'{a["x"]},{a["y"]}'


def _sym_elems(kind, X, Y):
    from render import compose
    return compose.navaid_symbol_elems(kind, X, Y)


def _north_scale(fg, svg_text, name):
    """Geometries du bloc du nord (deux fleches, textes VAR) et de l'echelle (barre, reperes, etiquettes), lues dans `Frame & grid`."""
    arrows = [ln for _, _, ln in fg if ln.startswith('<g transform="translate(')]
    if len(arrows) != 2:
        raise RepublishError(f'{name} : {len(arrows)} fleches du nord (attendu : 2)')
    cxy = {re.match(r'<g transform="translate\(([-\d.]+) ([-\d.]+)\)', a).groups() for a in arrows}
    if len(cxy) != 1:
        raise RepublishError(f'{name} : les deux fleches du nord n\'ont pas le meme pied')
    cx, cy = (float(v) for v in next(iter(cxy)))
    north = list(arrows)
    scale = []
    _, (x0, x1, y) = _scale_of(svg_text, name)
    for _, _, ln in fg:
        m = _TEXT.match(ln)
        if m:
            a = _attrs(m.group(1))
            x_, y_ = float(a['x']), float(a['y'])
            if abs(x_ - cx) < EPS and y_ > cy and a.get('fill') in (INK, INK2) and a.get('text-anchor') == 'middle' \
                    and (html.unescape(m.group(2)).startswith('VAR') or html.unescape(m.group(2)).startswith('(')):
                north.append(ln)
            elif a.get('fill') == INK and x0 - EPS <= x_ <= x1 + EPS and (abs(y_ - (y + 4.2)) < EPS or abs(y_ - (y - 2.3)) < EPS):
                scale.append(ln)
        elif ln.startswith('<line '):
            a = _attrs(ln)
            if a.get('x1') == a.get('x2') and abs(float(a['y1']) - y) < EPS and x0 - EPS <= float(a['x1']) <= x1 + EPS:
                scale.append(ln)
            elif a.get('stroke-width') == BAR_WIDTH and a.get('y1') == a.get('y2') and abs(float(a['y1']) - y) < EPS:
                scale.append(ln)
    if len(north) != 4:
        raise RepublishError(f'{name} : {len(north)} elements du bloc du nord (attendu : 4)')
    return [page.elems_geom(north)], [page.elems_geom(scale)]


# ---------- 4. xlink:href ----------
def add_xlink_href(svg_text, name):
    """(nouveau SVG, nombre d'<image> corrigees) : `xlink:href` (meme valeur) juste apres `href` sur chaque <image> qui n'en a pas."""
    n = 0

    def fix(m):
        nonlocal n
        el = m.group(0)
        if ' xlink:href="' in el:
            return el
        h = _HREF.search(el)
        if not h:
            raise RepublishError(f'{name} : <image> sans href')
        n += 1
        return el[:h.end()] + f' xlink:href="{h.group(1)}"' + el[h.end():]
    return _IMAGE.sub(fix, svg_text), n


# ---------- une page ----------
def retouch_page(svg_text, name, suffix, index):
    """(nouveau SVG, details) : toutes les retouches ; details = {fields, header, bar, image, xlink, moves, unplaced}."""
    svg, fields, header = check_or_rewrite_header(svg_text, name, index)
    svg, bar = complete_scale_bar(svg, name, suffix)
    moves, unplaced = [], []
    if suffix == '10-9':
        try:
            svg, moves, unplaced = retouch_labels(svg, name)
        except page.PlacementError as e:
            raise RepublishError(f'{name} : {e}') from None
    has_image = '<image' in svg
    svg, n_xlink = add_xlink_href(svg, name)
    return svg, {'fields': fields, 'header': header, 'bar': bar, 'image': has_image, 'xlink': n_xlink, 'moves': moves, 'unplaced': unplaced}


@dataclass
class Result:
    pages: int = 0
    charts: int = 0
    copied: int = 0
    rewritten: int = 0
    verified: int = 0
    bars: list = field(default_factory=list)          # [(page, texte, x, y)]
    f10: list = field(default_factory=list)           # noms des pages avec <image>
    lines: dict = field(default_factory=dict)         # {page: [champs]}
    moves: list = field(default_factory=list)         # [(page, calque, texte, avant, apres)] : etiquettes deplacees ou retirees
    unplaced: list = field(default_factory=list)      # [(page, libelle de balise)] : aucune position libre, reste a droite


def republish_zip(theatre, zip_in, zip_out):
    """Calcule les nouvelles pages et les recueils, puis ecrit `zip_out`. Retourne un `Result`."""
    zip_in, zip_out = Path(zip_in), Path(zip_out)
    if zip_out.exists():
        raise RepublishError(f'{zip_out} existe deja (pas d\'ecrasement)')
    index = update_coords.arp_index(theatre)
    res = Result()
    with zipfile.ZipFile(zip_in) as zin, tempfile.TemporaryDirectory(prefix='republish_') as tmp:
        tmp = Path(tmp)
        infos = zin.infolist()
        names = {i.filename for i in infos}
        pages = []                                 # [(dir, page, stem, suffix)] dans l'ordre du zip
        for info in infos:
            m = _PAGE_SVG.match(info.filename)
            if not m:
                continue
            pm = _PAGE_NAME.match(m['page'])
            if not pm:
                raise RepublishError(f'{info.filename} : nom de page inattendu (attendu <stem>_10-9[A-Z]*)')
            pages.append((m['dir'], m['page'], pm['stem'], pm['suffix']))
        new = {}                                   # nom d'entree -> fichier temporaire
        for n, (d, name, _, suffix) in enumerate(pages):
            svg_old = zin.read(f'{d}svg/{name}.svg').decode('utf-8')
            svg_new, det = retouch_page(svg_old, name, suffix, index)
            for sub, ext in (('png', 'png'), ('pdf', 'pdf')):
                if f'{d}{sub}/{name}.{ext}' not in names:
                    raise RepublishError(f'{name} : entree {d}{sub}/{name}.{ext} absente du zip')
            work = tmp / f'p{n}'
            svg_p, pdf_p, png_p = export.export_page(svg_new, work / 'page')
            if svg_p.read_bytes() != svg_new.encode('utf-8'):
                raise RepublishError(f'{name} : le SVG ecrit differe du SVG calcule')
            new[f'{d}svg/{name}.svg'], new[f'{d}pdf/{name}.pdf'], new[f'{d}png/{name}.png'] = svg_p, pdf_p, png_p
            res.lines[name] = det['fields']
            if det['header'] == 'reecrit':
                res.rewritten += 1
            else:
                res.verified += 1
            if det['bar']:
                res.bars.append((name, *det['bar']))
            if det['image']:
                res.f10.append(name)
            res.moves.extend((name, *m) for m in det['moves'])
            res.unplaced.extend((name, u) for u in det['unplaced'])
        res.pages = len(pages)
        charts = {}
        for n, (d, name, stem, suffix) in enumerate(pages):
            charts.setdefault((d, stem), []).append((suffix, n))
        for k, ((d, stem), members) in enumerate(charts.items()):
            members.sort(key=lambda t: dcsdata.natural_key(t[0]))
            entry = f'{d}pdf/{stem}_charts.pdf'
            if entry not in names:
                raise RepublishError(f'{stem} : recueil {entry} absent du zip')
            out = tmp / f'recueil{k}.pdf'
            export.merge_pdfs([new[f'{d}pdf/{pages[n][1]}.pdf'] for _, n in members], out)
            before = len(PdfReader(io.BytesIO(zin.read(entry))).pages)
            after = len(PdfReader(str(out)).pages)
            if before != after:
                raise RepublishError(f'{stem} : recueil de {after} pages, l\'ancien en avait {before}')
            new[entry] = out
        res.charts = len(charts)
        try:
            with zipfile.ZipFile(zip_out, 'x') as zout:
                for info in infos:
                    zi = zipfile.ZipInfo(info.filename, info.date_time)
                    zi.compress_type, zi.external_attr, zi.create_system = info.compress_type, info.external_attr, info.create_system
                    zi.comment = info.comment
                    if info.filename in new:
                        with open(new[info.filename], 'rb') as src, zout.open(zi, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                    else:
                        with zin.open(info) as src, zout.open(zi, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                        res.copied += 1
        except BaseException:
            zip_out.unlink(missing_ok=True)
            raise
    return res


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='republish.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zip', required=True, help='zip de release (airfields ou helicopters)')
    ap.add_argument('--out', required=True, help='nouveau zip (ne doit pas exister)')
    ap.add_argument('--moves', help='fichier TSV (nouveau) : une ligne par etiquette deplacee ou retiree (page, calque, texte, avant, apres)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    if args.moves and Path(args.moves).exists():
        print(f'ARRET : {args.moves} existe deja (pas d\'ecrasement)', file=sys.stderr)
        return 1
    try:
        res = republish_zip(args.theatre, args.zip, args.out)
    except (RepublishError, dcsdata.DataError, FileNotFoundError, zipfile.BadZipFile) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    if args.moves:
        with open(args.moves, 'x', encoding='utf-8') as fh:
            fh.write('page\tcalque\ttexte\tavant\tapres\n')
            for m in res.moves:
                fh.write('\t'.join(m) + '\n')
    print(f'{args.zip} -> {args.out}')
    print(f'pages reexportees : {res.pages}')
    print(f"en-tetes reecrits : {res.rewritten} ; verifies : {res.verified}")
    print(f"barres completees : {len(res.bars)}")
    for name, text, _, _ in res.bars:
        print(f'  {name} : {text}')
    kinds = {k: [m for m in res.moves if m[1] == k] for k in ('Runway bearings', 'Navaids', 'Frame & grid')}
    caps = kinds['Runway bearings']
    print(f"caps deplaces : {len(caps)} sur {len({m[0] for m in caps})} pages")
    navs = kinds['Navaids']
    print(f"libelles de balises deplaces : {len(navs)} sur {len({m[0] for m in navs})} pages"
          + (f" ; sans position libre (restent a droite) : {len(res.unplaced)}" if res.unplaced else ''))
    for n, u in res.unplaced:
        print(f'  {n} : {u}')
    grads = kinds['Frame & grid']
    slid = [m for m in grads if not m[4].startswith('retire')]
    print(f"graduations glissees : {len(slid)} ; retirees : {len(grads) - len(slid)} (sur {len({m[0] for m in grads})} pages)")
    print(f"pages a fond F10 : {len(res.f10)}" + ''.join(f'\n  {n}' for n in res.f10))
    print(f'recueils refaits : {res.charts}')
    print(f'entrees recopiees telles quelles : {res.copied}')
    if not res.pages:
        print('aucune page de chart dans ce zip : toutes les entrees sont recopiees telles quelles')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
