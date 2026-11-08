"""Republication des charts deja publiees, sans les captures F10 : toutes les retouches que le moteur sait faire sur un SVG publie.

Usage : python -I render/republish.py <Theatre> --zip <zip de release> --out <nouveau zip> [--ajouter <dossier de pages 11-x>]
Entree : un zip de la release (airfields ou helicopters). Chaque `…/svg/<page>.svg` est une page de chart. Retouches :
  0. caps magnetiques et fleches du nord de la page `10-9` (lot caps-cockpit), AVANT toutes les autres : le nord du jeu est la
     grille ; la fleche fine passe a 0, la pleine a la declinaison d = p - t relue sur les deux fleches (t, p : leurs rotations),
     et chaque texte de cap, sur le plan et dans le tableau, devient (ancien + t) % 360 ; une page deja convertie (t = 0) ne change pas ;
     un cap dont le texte change est replace par la regle du 2.1 d'apres son nouveau texte (la place le long de la piste calculee pour
     l'ancien texte n'est plus une position de la regle) ; un cap dont le texte ne change pas garde sa place si elle est libre ;
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
  5. le repertoire des heliports d'un zip *helicopters* (entrees `<dossier>heliports/<Theatre>_heliports_directory_p<NN>.jpg` et
     `<dossier><Theatre>_heliports_directory.pdf`), sans les captures : voir `retouch_directory` ; une vignette dont la ligne de la
     ville, a 36 px, touche les dimensions ou en est a moins de 24 px est reecrite plus petit sur sa page JPEG et dans le PDF.
  6. les pages d'approche `<stem>_11-<n>` (lot ac-approche-ils) : acceptees et recopiees telles quelles, aucune retouche ; leur PDF
     entre dans le recueil de la chart, apres les pages 10-9* (ordre naturel des suffixes). `--ajouter <dossier>` ajoute au zip les
     pages 11-x d'un dossier (la sortie, a plat, de `build_chart.py --approches-seulement` ; les autres fichiers du dossier sont ignores et
     comptes au resume) : voir `find_added_pages`.
Chaque page 10-9* est ensuite reexportee (SVG, PDF, PNG ; une page avec `<image>` : PNG a 256 couleurs, /Interpolate dans le PDF) et le
recueil `pdf/<stem>_charts.pdf` de chaque chart est refait. Sortie : un nouveau zip, memes entrees dans le meme ordre ; les
entrees hors charts sont recopiees a l'octet, sauf, pour 5, les JPEG des pages touchees et le PDF du repertoire (un zip sans page de
chart ni repertoire des heliports aux noms ci-dessus est recopie tel quel). Un fichier de sortie qui existe
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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from PIL import Image, ImageDraw, JpegImagePlugin                     # noqa: E402
from pypdf import PdfReader, PdfWriter                                # noqa: E402

from render import dcsdata, directory, export, fiches, geo, page, update_coords   # noqa: E402
from render.svgdoc import INK, INK2, f                                # noqa: E402
from render.update_coords import ROW_Y, UpdateError, _PAGE_NAME, _PAGE_SVG, _TEXT, _Y   # noqa: E402

RepublishError = UpdateError
_PAGE_NAME_ANY = re.compile(r'^(?P<stem>.+)_(?P<suffix>10-9[A-Z]*|11-\d+)$')       # lot ac-approche-ils : `update_coords._PAGE_NAME` + les pages 11-x
_APPROACH = re.compile(r'^11-\d+$')
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


# ---------- 0. caps magnetiques et fleches du nord (lot caps-cockpit) ----------
_ARROW = re.compile(r'^<g transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+)\)">')
_CAP_TEXT = re.compile(r'^\d{3}\.\d°$')
_YEAR = re.compile(r'^\(([\d.]+)\)$')
TABLE_CAP_X = '20'                 # colonne CAP MAG du tableau des pistes (page.runway_table : cols[1] = 20.0)


def convert_cap(label, t):
    """Cap publie -> cap du cockpit : (ancien + t) % 360, `05.1f` + `°` (cadrage 2.3, point 3). t = 0 : le cap est rendu tel quel
    (une page deja convertie ne change pas, meme un `360.0°` que le moteur peut ecrire)."""
    if not _CAP_TEXT.match(label):
        raise RepublishError(f'texte de cap illisible : {label!r}')
    if t == 0:
        return label
    return f'{(float(label[:-1]) + t) % 360.0:05.1f}°'


def read_north(svg_text, name):
    """(t, p, cx, cy, arrows) du bloc du nord publie : t, p = rotations des fleches fine et pleine (deg), (cx, cy) leur pied,
    arrows = [(debut, fin, ligne)] fine puis pleine. RepublishError si le bloc n'a pas exactement deux fleches (une fine, une
    pleine) de la forme du moteur, ou si le texte `VAR …` / `(annee)` n'est pas celui que le moteur ecrit pour d = p - t."""
    fg = _layer_lines(svg_text, 'Frame & grid')
    found = [(st, en, ln) for st, en, ln in fg if ln.startswith('<g transform="translate(')]
    if len(found) != 2:
        raise RepublishError(f'{name} : {len(found)} fleches du nord (attendu : 2)')
    thin = [a for a in found if '<polyline' in a[2] and '<polygon' not in a[2]]
    solid = [a for a in found if '<polygon' in a[2] and '<polyline' not in a[2]]
    if len(thin) != 1 or len(solid) != 1:
        raise RepublishError(f'{name} : les fleches du nord ne sont pas une fine et une pleine')
    ms = [_ARROW.match(a[2]) for a in (thin[0], solid[0])]
    if not all(ms):
        raise RepublishError(f'{name} : fleche du nord illisible : {found[0][2]!r}')
    if (ms[0].group(1), ms[0].group(2)) != (ms[1].group(1), ms[1].group(2)):
        raise RepublishError(f'{name} : les deux fleches du nord n\'ont pas le meme pied')
    cx, cy = float(ms[0].group(1)), float(ms[0].group(2))
    t, p = float(ms[0].group(3)), float(ms[1].group(3))
    for (_, _, ln), want in zip((thin[0], solid[0]), page.north_arrows(cx, cy, t, p)):
        if ln != want:
            raise RepublishError(f'{name} : fleche du nord differente de celle du moteur : {ln!r}')
    var_lines, year_lines = [], []
    for _, _, ln in fg:
        m = _TEXT.match(ln)
        if not m or not ln.startswith('<text '):
            continue
        a = _attrs(m.group(1))
        if abs(float(a['x']) - cx) >= EPS or a.get('text-anchor') != 'middle':
            continue
        txt = html.unescape(m.group(2))
        if abs(float(a['y']) - (cy + 3.4)) < EPS and txt.startswith('VAR'):
            var_lines.append(ln)
        elif abs(float(a['y']) - (cy + 6.0)) < EPS and txt.startswith('('):
            year_lines.append((ln, txt))
    if len(var_lines) != 1 or len(year_lines) != 1:
        raise RepublishError(f'{name} : {len(var_lines)} texte(s) VAR et {len(year_lines)} annee(s) sous les fleches (attendu : 1 et 1)')
    ym = _YEAR.match(year_lines[0][1])
    if not ym:
        raise RepublishError(f'{name} : annee du bloc du nord illisible : {year_lines[0][1]!r}')
    want = page.north_block(cx, cy, p - t, float(ym.group(1)))
    if not (_same_text(var_lines[0], want[2]) and _same_text(year_lines[0][0], want[3])):
        raise RepublishError(f'{name} : le texte VAR publie {var_lines[0]!r} n\'est pas celui que le moteur ecrit pour '
                             f'd = p - t = {p - t:.3f} et l\'annee {ym.group(1)}')
    return t, p, cx, cy, [thin[0], solid[0]]


def retouch_north_and_caps(svg_text, name):
    """(nouveau SVG, details) : cadrage 2.3, points 1 a 3, sur une page `10-9`. details = {'t', 'p', 'thin', 'solid', 'caps'} :
    'thin' et 'solid' = rotations ecrites (texte), 'caps' = [(calque, ancien, nouveau)] dans l'ordre des calques (plan, puis tableau).
    RepublishError : nombre de fleches, texte VAR, caps du plan et du tableau en nombre different."""
    t, p, cx, cy, arrows = read_north(svg_text, name)
    d = p - t
    new_arrows = page.north_arrows(cx, cy, 0.0, d)
    edits = [(st, en, new) for (st, en, _), new in zip(arrows, new_arrows)]
    caps_plan = [(st, en, ln, _CAP_G.match(ln)) for st, en, ln in _layer_lines(svg_text, 'Runway bearings')]
    for _, _, ln, m in caps_plan:
        if not m:
            raise RepublishError(f'{name} : element inattendu dans le calque Runway bearings : {ln}')
    caps_table = []
    for st, en, ln in _layer_lines(svg_text, 'Runway table'):
        m = _TEXT.match(ln)
        if m and ln.startswith('<text ') and _attrs(m.group(1)).get('x') == TABLE_CAP_X and _CAP_TEXT.match(html.unescape(m.group(2))):
            caps_table.append((st, en, ln, m))
    if len(caps_plan) != len(caps_table):
        raise RepublishError(f'{name} : {len(caps_plan)} caps sur le plan et {len(caps_table)} dans le tableau (attendu : autant)')
    changes = []
    for layer, items in (('Runway bearings', caps_plan), ('Runway table', caps_table)):
        for st, en, ln, m in items:
            old = html.unescape(m.group(4) if layer == 'Runway bearings' else m.group(2))
            new = convert_cap(old, t)
            changes.append((layer, old, new))
            if new != old:
                tail = '>' + html.escape(old, quote=False) + '</text>'
                a = ln.rindex(tail)
                edits.append((st, en, ln[:a] + '>' + html.escape(new, quote=False) + '</text>' + ln[a + len(tail):]))
    for st, en, rep in sorted(edits, reverse=True):
        svg_text = svg_text[:st] + rep + svg_text[en:]
    return svg_text, {'t': t, 'p': p, 'thin': _ARROW.match(new_arrows[0]).group(3), 'solid': _ARROW.match(new_arrows[1]).group(3),
                      'caps': changes}


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
            end = page.CapEnd(c.get('old', c['label']), thr, u, w / 2, az)      # la place publiee a ete calculee pour l'ancien texte
            if abs(page.cap_norm(az - 90.0) - c['rot']) > CAP_CHECK_MM:
                continue
            for cx, cy, rot in page.cap_candidates(end):
                if abs(cx - c['X']) <= CAP_CHECK_MM and abs(cy - c['Y']) <= CAP_CHECK_MM and abs(rot - c['rot']) <= CAP_CHECK_MM:
                    if c['label'] == end.label:                                 # texte inchange : la place publiee est reverifiee
                        end.current = (c['X'], c['Y'], c['rot'])
                    end.label = c['label']                                      # texte change : place d'origine ou le long de la piste, selon la regle
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


def retouch_labels(svg_text, name, old_labels=None):
    """(nouveau SVG, deplacements, sans place) : cadrage 2.4. deplacements : [(calque, texte, avant, apres)]. old_labels : les textes
    de cap du plan avant `retouch_north_and_caps` (meme ordre), pour retrouver la place publiee d'un cap deja pose le long d'une piste."""
    runways = []
    for _, _, ln in _layer_lines(svg_text, 'Runways'):
        m = re.match(r'^<polygon points="([^"]+)" fill="#1A1A1A"/>$', ln)
        if not m:
            raise RepublishError(f'{name} : element inattendu dans le calque Runways : {ln}')
        runways.append([tuple(float(v) for v in p.split(',')) for p in m.group(1).split()])
    runway_geoms = [page.elem_geom(f'<polygon points="{" ".join(f"{x},{y}" for x, y in p)}" fill="#1A1A1A"/>') for p in runways]
    cap_lines = _layer_lines(svg_text, 'Runway bearings')
    caps = _read_caps(cap_lines, name)
    if old_labels is not None:
        if len(old_labels) != len(caps):
            raise RepublishError(f'{name} : {len(caps)} caps sur le plan, {len(old_labels)} avant la conversion')
        for c, o in zip(caps, old_labels):
            c['old'] = o
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
        moved = moved or (abs(cx - c['X']) > CAP_CHECK_MM or abs(cy - c['Y']) > CAP_CHECK_MM or abs(rot - c['rot']) > CAP_CHECK_MM)
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


# ---------- 5. repertoire des heliports (lot ac-repertoire-ville) ----------
_DIR_JPG = re.compile(r'^(?P<dir>(?:.*/)?)heliports/(?P<theatre>[^/]+)_heliports_directory_p(?P<no>\d+)\.jpg$')
_DIR_PDF = re.compile(r'^(?P<dir>(?:.*/)?)(?P<theatre>[^/]+)_heliports_directory\.pdf$')
DIR_PAIR_MAX = 6.0                 # ecart absolu moyen (RGB, 0-255) : au plus 6 = la bande est cette ligne


def band_box(x, w, base2):
    """Bande de controle de la deuxieme ligne d'une vignette (x, w) : (x0, y0, x1, y1), bornes incluses."""
    return x + directory.PLAN_DX - 4, base2 - 34, x + w - directory.PLAN_DX + 4, base2 + 10


def line_band(x, w, base2, town, dims, size):
    """La bande redessinee sur fond blanc (ville a `size`, dimensions a 36 px, par `directory.draw_line2`) : tableau RGB."""
    x0, y0, x1, y1 = band_box(x, w, base2)
    top = 34                                                # la bande commence 34 px au-dessus de la ligne de base
    canvas = Image.new('RGB', (x + w, top + 11), (255, 255, 255))
    directory.draw_line2(ImageDraw.Draw(canvas), x, w, top, town, dims, size)
    return np.asarray(canvas, dtype=np.int16)[:, x0:x1 + 1]


def band_gap(arr, x, w, base2, town, dims, size):
    """Ecart absolu moyen (RGB, 0-255) entre la bande publiee de `arr` (page, hauteur x largeur x 3) et la ligne redessinee."""
    x0, y0, x1, y1 = band_box(x, w, base2)
    cur = arr[y0:y1 + 1, x0:x1 + 1].astype(np.int16)
    return float(np.abs(cur - line_band(x, w, base2, town, dims, size)).mean())


def directory_tiles(theatre):
    """[(numero de page, site, x, w, base2, ville, dimensions, taille)] des vignettes du repertoire, rejouees depuis pydcs seul
    (`fiches.replay_directory`) ; la ville comme `build_directory.py` : `nearest_town` au point de l'aerodrome pydcs projete en
    latitude / longitude. Retourne aussi le nombre de pages d'index."""
    sites, pages, n_index = fiches.replay_directory(theatre)
    proj = geo.Projection.for_theatre(theatre)
    towns = directory.load_towns(theatre)
    out = []
    for k, pg in enumerate(pages):
        for site, pos in pg['entries']:
            x, y, w, _ = directory.tile_box(site.form, pos)
            lat, lon = proj.to_latlon(site.x, site.z)
            town = directory.nearest_town(lat, lon, towns)
            dims = directory.dims_line(site.aires)
            out.append((n_index + k + 1, site.name, x, w, directory.tile_base2(site.form, y), town, dims,
                        directory.town_size(town, dims, w)))
    return out, n_index


@dataclass
class DirectoryResult:
    tiles: int = 0                                            # vignettes du repertoire
    controlled: int = 0                                       # celles dont la taille de la ville est sous 36 px
    retouched: int = 0
    already: int = 0
    pages: list = field(default_factory=list)                 # numeros des pages touchees
    rows: list = field(default_factory=list)                  # [(page, site, ville, taille, ecart a 36 px, ecart a la taille, decision)]


def _directory_entries(infos, theatre):
    """({numero: info} des JPEG, info du PDF) du repertoire, ou (None, None) si le zip n'a aucune de ces entrees ; RepublishError
    si le zip en a une partie seulement ou si leur theatre n'est pas `theatre`."""
    key = theatre.replace(' ', '').lower()
    jpgs, pdfs, folders = {}, [], set()
    for info in infos:
        m = _DIR_JPG.match(info.filename)
        if m:
            if m['theatre'].lower() != key:
                raise RepublishError(f"{info.filename} : repertoire d'un autre theatre que {theatre}")
            n = int(m['no'])
            if n in jpgs:
                raise RepublishError(f'{info.filename} : page {n} en double')
            jpgs[n] = info
            folders.add(m['dir'])
            continue
        m = _DIR_PDF.match(info.filename)
        if m and m['theatre'].lower() == key:
            pdfs.append(info)
            folders.add(m['dir'])
    if not jpgs and not pdfs:
        return None, None
    if not jpgs or len(pdfs) != 1:
        raise RepublishError(f'repertoire des heliports incomplet : {len(jpgs)} JPEG et {len(pdfs)} PDF')
    if len(folders) != 1:
        raise RepublishError(f'repertoire des heliports reparti sur plusieurs dossiers : {sorted(folders)}')
    return jpgs, pdfs[0]


def encode_like(im, original):
    """Octets JPEG de `im` avec les tables de quantification, le sous-echantillonnage (et la densite) de `original` (image Pillow JPEG)."""
    buf = io.BytesIO()
    kw = {}
    if original.info.get('dpi'):
        kw['dpi'] = original.info['dpi']
    im.save(buf, 'JPEG', qtables=original.quantization, subsampling=JpegImagePlugin.get_sampling(original), **kw)
    return buf.getvalue()


def _page_image(pdf_page, name):
    """(nom, objet) de l'unique image d'une page du PDF ; RepublishError s'il n'y en a pas exactement une."""
    xo = pdf_page.get('/Resources', {}).get('/XObject', {})
    imgs = [(k, v.get_object()) for k, v in xo.items() if v.get_object().get('/Subtype') == '/Image']
    if len(imgs) != 1:
        raise RepublishError(f'{name} : {len(imgs)} images (attendu : une)')
    return imgs[0]


def check_directory_pdf(pdf_bytes, n_jpegs, name):
    """Le PDF du repertoire (`PdfReader`) : `n_jpegs` pages, chacune avec exactement une image ; RepublishError sinon."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) != n_jpegs:
        raise RepublishError(f'{name} : {len(reader.pages)} pages, mais {n_jpegs} JPEG')
    for k, pg in enumerate(reader.pages, 1):
        _page_image(pg, f'{name} page {k}')
    return reader


def rewrite_directory_pdf(pdf_bytes, new_jpegs, n_jpegs, name, out_path):
    """Ecrit dans `out_path` le PDF du repertoire dont la page `k` (1 a n) porte l'image du JPEG `p<k>` : les pages de `new_jpegs`
    ({k: octets}) recoivent ces octets (DCTDecode, memes dimensions, meme MediaBox), les autres gardent leur image a l'octet.
    RepublishError si le PDF n'a pas `n_jpegs` pages ou si une page n'a pas exactement une image."""
    reader = check_directory_pdf(pdf_bytes, n_jpegs, name)
    writer = PdfWriter(clone_from=reader)
    for k, data in new_jpegs.items():
        pg = writer.pages[k - 1]
        _, img = _page_image(pg, f'{name} page {k}')
        w, h = Image.open(io.BytesIO(data)).size
        if img.get('/Filter') != '/DCTDecode' or (img.get('/Width'), img.get('/Height')) != (w, h):
            raise RepublishError(f'{name} page {k} : image {img.get("/Filter")} {img.get("/Width")} x {img.get("/Height")}, '
                                 f'attendu DCTDecode {w} x {h}')
        img._data = data
    with open(out_path, 'xb') as fh:
        writer.write(fh)
    check = PdfReader(str(out_path))
    if len(check.pages) != n_jpegs:
        raise RepublishError(f'{name} : {len(check.pages)} pages ecrites au lieu de {n_jpegs}')
    for k, (a, b) in enumerate(zip(reader.pages, check.pages), 1):
        if list(a.mediabox) != list(b.mediabox):
            raise RepublishError(f'{name} page {k} : MediaBox changee')
        want = new_jpegs[k] if k in new_jpegs else _page_image(a, name)[1]._data
        if _page_image(b, name)[1]._data != want:
            raise RepublishError(f'{name} page {k} : image ecrite differente de celle attendue')


def retouch_directory(theatre, zin, infos, tmp):
    """Repertoire des heliports d'un zip : (`DirectoryResult`, {entree: fichier ecrit sous `tmp`}). Les pages JPEG et le PDF du
    repertoire ne sont lus que si une vignette a une ville sous 36 px ; sinon, ou sans ces entrees, aucune entree n'est reecrite.

    Pour chaque vignette dont la taille de la ville (`directory.town_size`) est sous 36 px : la bande de la deuxieme ligne est
    comparee a la ligne redessinee a 36 px (ecart <= 6 : a retoucher), sinon a la taille retenue (<= 6 : deja retouchee), sinon
    RepublishError. Retouche : bande en blanc, puis `directory.draw_line2`. Le JPEG se reencode avec les tables et le
    sous-echantillonnage de l'original ; le PDF recoit les octets du nouveau JPEG."""
    res = DirectoryResult()
    jpgs, pdf = _directory_entries(infos, theatre)
    if jpgs is None:
        return res, {}
    tiles, n_index = directory_tiles(theatre)
    n_plans = max(t[0] for t in tiles)
    if sorted(jpgs) != list(range(1, len(jpgs) + 1)) or len(jpgs) != n_plans:
        raise RepublishError(f'repertoire : {len(jpgs)} JPEG (pages {min(jpgs)} a {max(jpgs)}), {n_plans} pages rejouees '
                             f"({n_index} d'index + {n_plans - n_index} de plans)")
    res.tiles = len(tiles)
    by_page = {}
    for t in tiles:
        if t[7] < directory.LINE_SIZE:
            by_page.setdefault(t[0], []).append(t)
    new, new_jpegs = {}, {}
    for no in sorted(by_page):
        info = jpgs[no]
        original = Image.open(io.BytesIO(zin.read(info)))
        if original.format != 'JPEG' or original.size != directory.PAGE_PX:
            raise RepublishError(f'{info.filename} : {original.format} {original.size}, attendu JPEG {directory.PAGE_PX}')
        im = original.convert('RGB')
        arr = np.asarray(im)
        work, d = im.copy(), None
        for _, site, x, w, base2, town, dims, size in by_page[no]:
            res.controlled += 1
            g36 = band_gap(arr, x, w, base2, town, dims, directory.LINE_SIZE)
            gs = band_gap(arr, x, w, base2, town, dims, size)
            if g36 <= DIR_PAIR_MAX:
                decision = 'retouchee'
                x0, y0, x1, y1 = band_box(x, w, base2)
                if d is None:
                    d = ImageDraw.Draw(work)
                d.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
                directory.draw_line2(d, x, w, base2, town, dims, size)
                res.retouched += 1
            elif gs <= DIR_PAIR_MAX:
                decision = 'deja retouchee'
                res.already += 1
            else:
                raise RepublishError(f'repertoire page {no}, {site} : la bande ne correspond ni a la ligne a 36 px '
                                     f'(ecart {g36:.2f}) ni a celle a {size} px (ecart {gs:.2f}), seuil {DIR_PAIR_MAX}')
            res.rows.append((no, site, town, size, g36, gs, decision))
        if d is not None:
            new_jpegs[no] = encode_like(work, original)
            res.pages.append(no)
    pdf_bytes = zin.read(pdf)
    check_directory_pdf(pdf_bytes, len(jpgs), pdf.filename)          # arret meme si aucune page n'est touchee
    if not new_jpegs:
        return res, {}
    for no, data in new_jpegs.items():
        p = Path(tmp) / f'dir_p{no:02d}.jpg'
        p.write_bytes(data)
        new[jpgs[no].filename] = p
    pdf_p = Path(tmp) / 'dir.pdf'
    rewrite_directory_pdf(pdf_bytes, new_jpegs, len(jpgs), pdf.filename, pdf_p)
    new[pdf.filename] = pdf_p
    return res, new


# ---------- une page ----------
def retouch_page(svg_text, name, suffix, index):
    """(nouveau SVG, details) : toutes les retouches ; details = {fields, header, bar, image, xlink, moves, unplaced, north}."""
    svg, north = svg_text, None
    if suffix == '10-9':                                     # lot caps-cockpit : avant toutes les autres retouches
        svg, north = retouch_north_and_caps(svg, name)
    svg, fields, header = check_or_rewrite_header(svg, name, index)
    svg, bar = complete_scale_bar(svg, name, suffix)
    moves, unplaced = [], []
    if suffix == '10-9':
        try:
            old_caps = [old for layer, old, _ in north['caps'] if layer == 'Runway bearings']
            svg, moves, unplaced = retouch_labels(svg, name, old_caps)
        except page.PlacementError as e:
            raise RepublishError(f'{name} : {e}') from None
    has_image = '<image' in svg
    svg, n_xlink = add_xlink_href(svg, name)
    return svg, {'fields': fields, 'header': header, 'bar': bar, 'image': has_image, 'xlink': n_xlink, 'moves': moves, 'unplaced': unplaced,
                'north': north}


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
    north: dict = field(default_factory=dict)         # {page: details de retouch_north_and_caps} : pages 10-9
    directory: DirectoryResult = field(default_factory=lambda: DirectoryResult())   # repertoire des heliports (zip *helicopters*)
    approach: list = field(default_factory=list)     # noms des pages 11-x du zip, recopiees telles quelles
    added: dict = field(default_factory=dict)        # {stem: [pages 11-x ajoutees]} (`--ajouter`)
    ignored: list = field(default_factory=list)      # noms des fichiers du dossier `--ajouter` qui ne sont pas des pages 11-x


def find_added_pages(folder, ignored=None):
    """Pages 11-x d'un dossier (`--ajouter`) : {page: {'svg', 'pdf', 'png': chemin}}, dans l'ordre naturel des noms. Le dossier est
    la sortie de `build_chart.py --approches-seulement` (a plat) ; il peut porter d'autres fichiers (pages 10-9, recueil...) : tout
    ce qui n'est pas un fichier `<stem>_11-<n>.{svg,pdf,png}` est ignore et ajoute a la liste `ignored` si elle est donnee.
    RepublishError : dossier absent, un des trois fichiers d'une page manquant, aucune page."""
    folder = Path(folder)
    if not folder.is_dir():
        raise RepublishError(f'{folder} : dossier introuvable')
    found = {}
    for p in sorted(folder.iterdir()):
        m = _PAGE_NAME_ANY.match(p.stem) if p.is_file() and p.suffix in ('.svg', '.pdf', '.png') else None
        if not m or not _APPROACH.match(m['suffix']):
            if ignored is not None:
                ignored.append(p.name)
            continue
        found.setdefault(p.stem, {})[p.suffix[1:]] = p
    for name, files in found.items():
        for ext in ('svg', 'pdf', 'png'):
            if ext not in files:
                raise RepublishError(f'{name} : fichier {ext} manquant dans {folder}')
    if not found:
        raise RepublishError(f"{folder} : aucune page d'approche 11-x")
    return dict(sorted(found.items(), key=lambda kv: dcsdata.natural_key(kv[0])))


def republish_zip(theatre, zip_in, zip_out, add_dir=None):
    """Calcule les nouvelles pages et les recueils, puis ecrit `zip_out`. Retourne un `Result`. `add_dir` : dossier de pages 11-x a
    ajouter (`find_added_pages`) ; chaque page entre dans le zip juste avant le recueil de sa chart (pdf, png, svg)."""
    zip_in, zip_out = Path(zip_in), Path(zip_out)
    if zip_out.exists():
        raise RepublishError(f'{zip_out} existe deja (pas d\'ecrasement)')
    res = Result()
    added = find_added_pages(add_dir, res.ignored) if add_dir is not None else {}
    index = update_coords.arp_index(theatre)
    with zipfile.ZipFile(zip_in) as zin, tempfile.TemporaryDirectory(prefix='republish_') as tmp:
        tmp = Path(tmp)
        infos = zin.infolist()
        names = {i.filename for i in infos}
        pages = []                                 # [(dir, page, stem, suffix)] dans l'ordre du zip
        for info in infos:
            m = _PAGE_SVG.match(info.filename)
            if not m:
                continue
            pm = _PAGE_NAME_ANY.match(m['page'])
            if not pm:
                raise RepublishError(f'{info.filename} : nom de page inattendu (attendu <stem>_10-9[A-Z]* ou <stem>_11-<n>)')
            pages.append((m['dir'], m['page'], pm['stem'], pm['suffix']))
        new = {}                                   # nom d'entree -> fichier temporaire
        for n, (d, name, _, suffix) in enumerate(pages):
            if _APPROACH.match(suffix):                  # page d'approche : recopiee telle quelle
                for sub, ext in (('png', 'png'), ('pdf', 'pdf')):
                    if f'{d}{sub}/{name}.{ext}' not in names:
                        raise RepublishError(f'{name} : entree {d}{sub}/{name}.{ext} absente du zip')
                res.approach.append(name)
                continue
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
            if det['north']:
                res.north[name] = det['north']
            res.unplaced.extend((name, u) for u in det['unplaced'])
        res.pages = len(pages) - len(res.approach)
        charts = {}
        for n, (d, name, stem, suffix) in enumerate(pages):
            charts.setdefault((d, stem), []).append((suffix, n))
        zip_names = {(d, name) for d, name, _, _ in pages}
        inserts = {}                                   # entree du recueil -> [(nom d'entree, fichier)] a ecrire juste avant
        by_stem = {}
        for (d, stem) in charts:
            by_stem.setdefault(stem, []).append(d)
        for name, files in added.items():
            stem = _PAGE_NAME_ANY.match(name)['stem']
            if stem not in by_stem:
                raise RepublishError(f'{name} : la chart {stem} n\'est pas dans le zip')
            if len(by_stem[stem]) != 1:
                raise RepublishError(f'{name} : la chart {stem} est dans plusieurs dossiers du zip : {sorted(by_stem[stem])}')
            d = by_stem[stem][0]
            if (d, name) in zip_names:
                raise RepublishError(f'{name} : la page existe deja dans le zip')
            entry = f'{d}pdf/{stem}_charts.pdf'
            for sub in ('pdf', 'png', 'svg'):
                inserts.setdefault(entry, []).append((f'{d}{sub}/{name}.{sub}', files[sub]))
            res.added.setdefault(stem, []).append(name)
        for k, ((d, stem), members) in enumerate(charts.items()):
            members = members + [(_PAGE_NAME_ANY.match(a)['suffix'], a) for a in res.added.get(stem, [])]
            members.sort(key=lambda t: dcsdata.natural_key(t[0]))
            entry = f'{d}pdf/{stem}_charts.pdf'
            if entry not in names:
                raise RepublishError(f'{stem} : recueil {entry} absent du zip')
            sources = []
            for _, n in members:
                if isinstance(n, str):                 # page ajoutee : son PDF du dossier
                    sources.append(added[n]['pdf'])
                elif _APPROACH.match(pages[n][3]):     # page 11-x du zip : son PDF, tel quel
                    pdf_p = tmp / f'approach{n}.pdf'
                    pdf_p.write_bytes(zin.read(f'{d}pdf/{pages[n][1]}.pdf'))
                    sources.append(pdf_p)
                else:
                    sources.append(new[f'{d}pdf/{pages[n][1]}.pdf'])
            out = tmp / f'recueil{k}.pdf'
            export.merge_pdfs(sources, out)
            before = len(PdfReader(io.BytesIO(zin.read(entry))).pages)
            after = len(PdfReader(str(out)).pages)
            if before + len(res.added.get(stem, [])) != after:
                raise RepublishError(f'{stem} : recueil de {after} pages, l\'ancien en avait {before}'
                                     + (f' et {len(res.added[stem])} pages ajoutees' if res.added.get(stem) else ''))
            new[entry] = out
        res.charts = len(charts)
        res.directory, dir_new = retouch_directory(theatre, zin, infos, tmp)
        new.update(dir_new)
        try:
            with zipfile.ZipFile(zip_out, 'x') as zout:
                for info in infos:
                    for extra_name, extra_path in inserts.get(info.filename, []):       # pages ajoutees : juste avant le recueil
                        ez = zipfile.ZipInfo(extra_name, info.date_time)
                        ez.compress_type, ez.external_attr, ez.create_system = info.compress_type, info.external_attr, info.create_system
                        with open(extra_path, 'rb') as src, zout.open(ez, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
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
    ap.add_argument('--ajouter', metavar='DOSSIER', help="dossier de pages d'approche 11-x (la sortie de build_chart.py --approches-seulement) a ajouter aux charts du zip")
    ap.add_argument('--moves', help='fichier TSV (nouveau) : une ligne par etiquette deplacee ou retiree (page, calque, texte, avant, apres)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    if args.moves and Path(args.moves).exists():
        print(f'ARRET : {args.moves} existe deja (pas d\'ecrasement)', file=sys.stderr)
        return 1
    try:
        res = republish_zip(args.theatre, args.zip, args.out, args.ajouter)
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
    conv = [n for n in res.north.values() if n['t'] != 0]
    ncaps = sum(1 for n in res.north.values() for _, o, w in n['caps'] if o != w)
    print(f"nord et caps : {len(res.north)} pages 10-9, dont {len(conv)} converties ({len(res.north) - len(conv)} deja a t = 0) ; "
          f"textes de cap reecrits : {ncaps} sur {sum(len(n['caps']) for n in res.north.values())}")
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
    if res.approach or res.added or res.ignored:
        print(f"pages d'approche 11-x : {len(res.approach)} recopiees telles quelles ; {sum(len(v) for v in res.added.values())} ajoutees")
        for stem, names in res.added.items():
            print(f'  {stem} : {len(names)} ajoutee(s) ({", ".join(n[len(stem) + 1:] for n in names)})')
        if res.ignored:
            print(f'  fichiers du dossier --ajouter ignores (pas des pages 11-x) : {len(res.ignored)}')
    dr = res.directory
    if dr.tiles:
        print(f"repertoire des heliports : {dr.tiles} vignettes ; controlees (ville sous 36 px) : {dr.controlled} ; retouchees : {dr.retouched} ; "
              f"deja retouchees : {dr.already} ; pages touchees : {len(dr.pages)}" + ''.join(f' p{n:02d}' for n in dr.pages))
        for no, site, town, size, g36, gs, decision in dr.rows:
            print(f'  p{no:02d} {site} : {town} -> {size} px ({decision} ; ecart a 36 px {g36:.2f}, a {size} px {gs:.2f})')
    else:
        print('repertoire des heliports : aucun dans ce zip')
    print(f'entrees recopiees telles quelles : {res.copied}')
    if not res.pages and not res.approach:
        print('aucune page de chart dans ce zip : toutes les entrees sont recopiees telles quelles')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
