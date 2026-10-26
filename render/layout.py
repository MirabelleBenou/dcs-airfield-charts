"""Mise en page : zones de parkings, encarts, regle des pages (cadrage 4.5), placement des etiquettes et des numeros.

Fonctions pures (sans image ni SVG) : les entrees sont des coordonnees (x, z) en metres et des grilles d'occupation.
Unites : mm sur la page, m au sol, echelles en mm/m.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from . import dcsdata, fonts

LINK_M = 250.0                    # lien simple entre parkings d'une meme zone
READ_MM, READ_PERCENTILE = 3.8, 15
READ_MIN, READ_MAX = 0.04, 0.45   # mm/m
NN_FALLBACK_M = 30.0              # zone d'un seul parking : pas de plus proche voisin
INSET_PAD_M = 45.0
TITLE_MM = 3.4
DIRECT_RATIO = 0.80               # echelle du plan / echelle de lecture pour numeroter sur le plan
F_MIN, F_STEP = 0.65, 0.05
CELL_MM = 0.5
PAGE_GAP_MM = 3.0
LABEL_ZONE = 'P'


@dataclass
class Zone:
    index: int
    label: str                    # P1, P2...
    members: list                 # indices dans la liste de parkings
    x0: float
    x1: float
    z0: float
    z1: float
    s_read: float                 # mm/m
    first: str
    last: str

    @property
    def count(self):
        return len(self.members)

    @property
    def center(self):
        return (self.x0 + self.x1) / 2, (self.z0 + self.z1) / 2

    @property
    def title(self):
        return f'{self.label} · {self.first}–{self.last}'


def reading_scale(xz):
    """Echelle de lecture (mm/m) : 3,8 mm / 15e centile de la distance au plus proche voisin, bornee a [0,04 ; 0,45]."""
    pts = np.asarray(xz, float)
    if len(pts) < 2:
        nn = NN_FALLBACK_M
    else:
        d, _ = cKDTree(pts).query(pts, k=2)
        nn = float(np.percentile(d[:, 1], READ_PERCENTILE))
    if nn <= 0:
        return READ_MAX
    return min(READ_MAX, max(READ_MIN, READ_MM / nn))


def make_zones(parkings):
    """Zones par lien simple < 250 m ; P1, P2... par nombre de parkings decroissant. parkings : objets (name, x, z).
    Retourne la liste de Zone ; renseigne parking.zone."""
    n = len(parkings)
    if n == 0:
        return []
    xz = np.array([[p.x, p.z] for p in parkings], float)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in cKDTree(xz).query_pairs(LINK_M - 1e-9):
        parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    raw = []
    for mem in groups.values():
        names = sorted((parkings[i].name for i in mem), key=dcsdata.natural_key)
        raw.append((mem, names))
    raw.sort(key=lambda t: (-len(t[0]), dcsdata.natural_key(t[1][0])))
    zones = []
    for k, (mem, names) in enumerate(raw):
        sub = xz[mem]
        z = Zone(k, f'{LABEL_ZONE}{k + 1}', mem, float(sub[:, 0].min()), float(sub[:, 0].max()),
                 float(sub[:, 1].min()), float(sub[:, 1].max()), reading_scale(sub), names[0], names[-1])
        zones.append(z)
        for i in mem:
            parkings[i].zone = k
    return zones


def ratio_text(s):
    """1:6 600 pour 0,1515 mm/m (1 m = s mm  ->  1 : 1000 / s)."""
    return '1:' + f'{int(round(1000.0 / s)):,}'.replace(',', ' ')


@dataclass
class InsetBox:
    zone: Zone
    s: float                      # echelle du contenu, mm/m
    w: float                      # largeur du cadre, mm
    h: float                      # hauteur du cadre (hors titre), mm
    x: float = 0.0                # coin haut-gauche du cadre sur la page (le titre est au-dessus)
    y: float = 0.0
    k: float = 1.0                # facteur de reduction propre de l'encart (addendum 2), 1 = aucun

    @property
    def total_h(self):
        return self.h + TITLE_MM


def make_inset(zone, f, max_w=None, max_h=None, k_own=1.0):
    """Encart d'une zone a l'echelle de lecture x f x k_own ; ramene a (max_w, max_h) s'il est plus grand."""
    s = zone.s_read * f * k_own
    gx = zone.x1 - zone.x0 + 2 * INSET_PAD_M
    gz = zone.z1 - zone.z0 + 2 * INSET_PAD_M
    w, h = gz * s, gx * s
    min_w = (fonts.text_width(zone.title, 'bold', fonts.pt(6.4)) + fonts.text_width('1:100 000', 'regular', fonts.pt(5.4))
             + 3.0)
    w = max(w, min_w)                  # le cadre doit porter son titre et son echelle numerique
    if max_w and max_h:
        k = min(1.0, max_w / w, (max_h - TITLE_MM) / h)
        if k < 1.0:
            s, w, h = s * k, w * k, h * k
    return InsetBox(zone, s, w, h, k=k_own)


def f_values():
    n = int(round((1.0 - F_MIN) / F_STEP))
    return [round(1.0 - i * F_STEP, 4) for i in range(n + 1)]


# ---------- regle des pages ----------
def decide_mode(zones, s_plan, occ, cell_origin, frame, plan_center_of_zone):
    """Applique la regle du cadrage 4.5. Retourne un dict :
      {'mode': 1} ; {'mode': 2, 'f', 'boxes': [InsetBox avec x, y]} ; {'mode': 3, 'f', 'pages': [[InsetBox...]], 'dir'}.
    occ : grille booleenne d'occupation (True = occupe) du cadre, pas CELL_MM, origine cell_origin = (x, y) mm ;
    frame = (x, y, w, h) mm du cadre ; plan_center_of_zone(zone) -> (X, Y) mm sur la page du centre de la zone."""
    if not zones:
        return {'mode': 1}
    if all(s_plan >= DIRECT_RATIO * z.s_read for z in zones):
        return {'mode': 1}
    for f in f_values():
        boxes = place_in_voids(zones, f, occ, cell_origin, frame, plan_center_of_zone)
        if boxes is not None:
            return {'mode': 2, 'f': f, 'boxes': boxes}
    return {'mode': 3, **plan_pages(zones)}


def sat(occ):
    s = np.zeros((occ.shape[0] + 1, occ.shape[1] + 1), np.int64)
    s[1:, 1:] = np.cumsum(np.cumsum(occ.astype(np.int64), axis=0), axis=1)
    return s


def free_positions(occ_sat, nh, nw):
    """Masque (positions de coin haut-gauche, en cellules) ou le rectangle nh x nw est entierement libre."""
    H, W = occ_sat.shape[0] - 1, occ_sat.shape[1] - 1
    if nh > H or nw > W:
        return None
    ny, nx = H - nh + 1, W - nw + 1
    tot = occ_sat[nh:, nw:] - occ_sat[:ny, nw:] - occ_sat[nh:, :nx] + occ_sat[:ny, :nx]
    return tot == 0


def place_in_voids(zones, f, occ, origin, frame, center_of):
    """Encarts de toutes les zones dans les vides de `occ`, au plus pres de leur zone ; None si l'un ne tient pas."""
    occ = occ.copy()
    boxes = []
    order = sorted(zones, key=lambda z: -z.count)
    for z in order:
        b = make_inset(z, f)
        nw = int(math.ceil(b.w / CELL_MM)) + 2
        nh = int(math.ceil(b.total_h / CELL_MM)) + 2
        ok = free_positions(sat(occ), nh, nw)
        if ok is None or not ok.any():
            return None
        cx, cy = center_of(z)
        ys, xs = np.nonzero(ok)
        px = origin[0] + (xs + nw / 2) * CELL_MM
        py = origin[1] + (ys + nh / 2) * CELL_MM
        k = int(np.argmin((px - cx) ** 2 + (py - cy) ** 2))
        x0, y0 = origin[0] + (xs[k] + 1) * CELL_MM, origin[1] + (ys[k] + 1) * CELL_MM
        b.x, b.y = x0, y0 + TITLE_MM
        boxes.append(b)
        occ[ys[k]:ys[k] + nh, xs[k]:xs[k] + nw] = True
    return boxes


USABLE = (6.0, 25.5, 136.0, 169.0)       # x, y, w, h de la zone utile des pages de parkings (mm)


def shelf_pack(sizes, W, H, gap=PAGE_GAP_MM):
    """Rangement en lignes (etageres), premiere place trouvee, hauteurs decroissantes.
    sizes : liste de (w, h). Retourne [[(i, x, y)...] par page]."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0], i))
    pages = []          # chaque page : {'shelves': [{'y','h','x'}], 'used_h', 'items'}
    for i in order:
        w, h = sizes[i]
        placed = False
        for pg in pages:
            for sh in pg['shelves']:
                if h <= sh['h'] + 1e-9 and sh['x'] + w <= W + 1e-9:
                    pg['items'].append((i, sh['x'], sh['y']))
                    sh['x'] += w + gap
                    placed = True
                    break
            if placed:
                break
            if pg['used_h'] + h <= H + 1e-9:
                sh = {'y': pg['used_h'], 'h': h, 'x': w + gap}
                pg['items'].append((i, 0.0, pg['used_h']))
                pg['shelves'].append(sh)
                pg['used_h'] += h + gap
                placed = True
                break
        if not placed:
            pages.append({'shelves': [{'y': 0.0, 'h': h, 'x': w + gap}], 'used_h': h + gap, 'items': [(i, 0.0, 0.0)]})
    return [pg['items'] for pg in pages]


def pack_both(sizes, W, H):
    """Meilleur des deux rangements (lignes ou colonnes) : le moins de pages ; a egalite, les lignes.
    Retourne (pages, 'rows'|'cols') ; les positions sont en mm depuis le coin de la zone utile."""
    rows = shelf_pack(sizes, W, H)
    cols_t = shelf_pack([(h, w) for w, h in sizes], H, W)
    cols = [[(i, y, x) for (i, x, y) in pg] for pg in cols_t]
    if len(cols) < len(rows):
        return cols, 'cols'
    return rows, 'rows'


K_MIN, K_STEP = 0.70, 0.01


def _layout_pages(zones, f, ks):
    """Encarts a f commun et facteurs propres `ks` (indice de zone -> k) ; retourne (boxes, pages, sens)."""
    _, _, W, H = USABLE
    boxes = [make_inset(z, f, W, H, ks.get(i, 1.0)) for i, z in enumerate(zones)]
    pages, kind = pack_both([(b.w, b.total_h) for b in boxes], W, H)
    return boxes, pages, kind


def _best_f(zones, ks):
    """Le plus grand f de [0,65 ; 1] qui donne le nombre minimal de pages (pour ces facteurs propres)."""
    results = {f: _layout_pages(zones, f, ks) for f in f_values()}
    n_min = min(len(r[1]) for r in results.values())
    f = max(f for f, r in results.items() if len(r[1]) == n_min)
    return f, results[f], n_min


def reduction_plan(zones):
    """Addendum 2 : si, a f = 0,65, il faut encore une page de plus, les plus grands encarts (par aire) sont reduits un par
    un, du plus grand au plus petit, d'un facteur propre k dans [0,70 ; 1], tant que cela fait gagner une page.
    Retourne {indice de zone: k} (vide si aucune reduction ne fait gagner une page)."""
    f0 = F_MIN
    n0 = len(_layout_pages(zones, f0, {})[1])
    if n0 <= 1:
        return {}
    area = [(lambda b: b.w * b.total_h)(make_inset(z, f0, USABLE[2], USABLE[3])) for z in zones]
    order = sorted(range(len(zones)), key=lambda i: -area[i])
    ks = {}
    n_ks = int(round((1.0 - K_MIN) / K_STEP))
    for i in order:
        # le k le moins fort qui fait gagner une page ; sinon k minimal, et l'on passe a l'encart suivant
        for step in range(1, n_ks + 1):
            k = round(1.0 - step * K_STEP, 4)
            trial = dict(ks)
            trial[i] = k
            if len(_layout_pages(zones, f0, trial)[1]) < n0:
                return trial
        ks[i] = K_MIN
    return {}


def plan_pages(zones):
    """Pages 10-9A... : encarts a leur echelle de lecture x f commun (f >= 0,65), le moins de pages possible, puis le plus
    grand f qui garde ce nombre de pages ; reduction propre des plus grands encarts si elle fait gagner une page."""
    ux, uy, W, H = USABLE
    ks = reduction_plan(zones)
    f, (boxes, pages, kind), _ = _best_f(zones, ks)
    out = []
    for pg in pages:
        lst = []
        for i, x, y in pg:
            b = boxes[i]
            b.x, b.y = ux + x, uy + y + TITLE_MM
            lst.append(b)
        lst.sort(key=lambda b: (b.y, b.x))
        out.append(lst)
    return {'f': f, 'pages': out, 'dir': kind, 'reduced': {zones[i].label: k for i, k in sorted(ks.items())}}


# ---------- numeros et etiquettes ----------
POS8 = ('NE', 'E', 'SE', 'N', 'S', 'NW', 'W', 'SW')


def rects_overlap(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def circle_rect_overlap(cx, cy, r, rect):
    nx = min(max(cx, rect[0]), rect[2])
    ny = min(max(cy, rect[1]), rect[3])
    return (nx - cx) ** 2 + (ny - cy) ** 2 < r * r


def number_rect(pos, cx, cy, w, h, off):
    """Rectangle (x0, y0, x1, y1) du texte autour du point (cx, cy), `pos` parmi POS8 ; ancre de texte renvoyee."""
    if pos == 'E':
        return (cx + off, cy - h / 2, cx + off + w, cy + h / 2), 'start'
    if pos == 'W':
        return (cx - off - w, cy - h / 2, cx - off, cy + h / 2), 'end'
    if pos == 'N':
        return (cx - w / 2, cy - off - h, cx + w / 2, cy - off), 'middle'
    if pos == 'S':
        return (cx - w / 2, cy + off, cx + w / 2, cy + off + h), 'middle'
    if pos == 'NE':
        return (cx + off * 0.7, cy - off * 0.7 - h, cx + off * 0.7 + w, cy - off * 0.7), 'start'
    if pos == 'SE':
        return (cx + off * 0.7, cy + off * 0.7, cx + off * 0.7 + w, cy + off * 0.7 + h), 'start'
    if pos == 'NW':
        return (cx - off * 0.7 - w, cy - off * 0.7 - h, cx - off * 0.7, cy - off * 0.7), 'end'
    return (cx - off * 0.7 - w, cy + off * 0.7, cx - off * 0.7, cy + off * 0.7 + h), 'end'


ROW_NEIGHBOR_RATIO = 1.7       # voisins : points a moins de 1,7 x la distance au plus proche voisin
ROW_ANGLE_DEG = 15.0           # deux aretes d'un meme point a moins de 15 deg l'une de l'autre : meme groupe
ROW_MIN_POINTS = 3
ROW_STRAIGHT = 0.6             # ecart maximal a l'axe principal / mediane des distances au plus proche voisin


def _axis_deviation(pts):
    """Axe principal (moindres carres orthogonaux) de pts (N, 2) : (direction unitaire, ecart maximal des points a l'axe)."""
    c = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    u = vt[0]
    return u, float(np.abs(c @ np.array([-u[1], u[0]])).max())


def find_rows(points):
    """Rangees de parkings (regle 4.2) : liste de listes d'indices (tries), de la plus nombreuse a la moins nombreuse.
    1. d_i = distance au plus proche voisin ; voisins de i : points a moins de 1,7 d_i ; arete (i, j) si chacun est voisin de l'autre.
    2. direction d'une arete = angle modulo 180 deg ; deux aretes qui partagent un point et dont les directions different de
       moins de 15 deg vont dans le meme groupe (union de proche en proche).
    3. un groupe est une rangee s'il compte >= 3 points et si l'ecart maximal de ses points a leur axe principal est
       <= 0,6 x la mediane des d_i de ses points.
    4. de la plus nombreuse a la moins nombreuse ; un point deja pris est retire des suivantes ; sous 3 points : plus une rangee."""
    pts = np.asarray(points, float).reshape(-1, 2)
    n = len(pts)
    if n < ROW_MIN_POINTS:
        return []
    d, _ = cKDTree(pts).query(pts, k=2)
    d = d[:, 1]
    tree = cKDTree(pts)
    nbrs = []
    for i in range(n):
        cand = [j for j in tree.query_ball_point(pts[i], d[i] * ROW_NEIGHBOR_RATIO * (1 + 1e-9)) if j != i]
        nbrs.append({j for j in cand if math.hypot(*(pts[j] - pts[i])) < d[i] * ROW_NEIGHBOR_RATIO})
    edges = sorted((i, j) for i in range(n) for j in nbrs[i] if i < j and i in nbrs[j])
    if not edges:
        return []
    ang = [math.degrees(math.atan2(pts[j][1] - pts[i][1], pts[j][0] - pts[i][0])) % 180.0 for i, j in edges]
    parent = list(range(len(edges)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    at = {}
    for k, (i, j) in enumerate(edges):
        at.setdefault(i, []).append(k)
        at.setdefault(j, []).append(k)
    for ks in at.values():
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                diff = abs(ang[ks[a]] - ang[ks[b]])
                if min(diff, 180.0 - diff) < ROW_ANGLE_DEG:
                    parent[find(ks[a])] = find(ks[b])
    groups = {}
    for k, (i, j) in enumerate(edges):
        groups.setdefault(find(k), set()).update((i, j))
    cands = []
    for g in groups.values():
        idx = sorted(g)
        if len(idx) < ROW_MIN_POINTS:
            continue
        _, dev = _axis_deviation(pts[idx])
        if dev <= ROW_STRAIGHT * float(np.median(d[idx])):
            cands.append(idx)
    cands.sort(key=lambda g: (-len(g), g[0]))
    taken, rows = set(), []
    for g in cands:
        rest = [i for i in g if i not in taken]
        if len(rest) < ROW_MIN_POINTS:
            continue
        taken.update(rest)
        rows.append(rest)
    return rows


def _conflicts(rect, placed, points, tree, dot_r, clip):
    """Nombre de conflits d'un rectangle de numero : sortie du clip (1), numeros deja poses recouverts, points recouverts."""
    c = 0
    if clip is not None and not (rect[0] >= clip[0] and rect[1] >= clip[1] and rect[2] <= clip[2] and rect[3] <= clip[3]):
        c += 1
    c += sum(1 for r2 in placed if rects_overlap(rect, r2))
    if tree is not None:
        for j in tree.query_ball_point([(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2],
                                       (rect[2] - rect[0] + rect[3] - rect[1]) / 2 + dot_r + 0.5):
            if circle_rect_overlap(points[j][0], points[j][1], dot_r, rect):
                c += 1
    return c


def _row_rect(p, s, w, h, off):
    """Rectangle du numero d'un point `p` d'une rangee, du cote `s` (vecteur unitaire) : centre en p + s (off + |sx| w/2 + |sy| h/2)."""
    d = off + abs(s[0]) * w / 2 + abs(s[1]) * h / 2
    cx, cy = p[0] + s[0] * d, p[1] + s[1] * d
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def row_normal(pts):
    """Normale unitaire a une rangee, d'abord vers le bas de la page (n_y > 0 ; si n_y = 0, n_x > 0)."""
    u, _ = _axis_deviation(np.asarray(pts, float))
    n = np.array([-u[1], u[0]])
    if n[1] < -1e-9 or (abs(n[1]) <= 1e-9 and n[0] < 0):
        n = -n
    return n


def place_numbers(points, widths, text_h, dot_r, clip=None):
    """Numero de chaque parking. 1) Rangees (find_rows) : les numeros d'une meme rangee vont du meme cote, perpendiculairement
    a la rangee ; un cote n'est retenu que s'il n'a aucun conflit (sortie du clip, numero deja pose, point, recouvrement
    entre numeros de la rangee), le cote bas d'abord ; sinon toute la rangee passe au placement libre.
    2) Placement libre : 8 positions essayees, la premiere sans chevauchement d'un numero deja pose ni d'un point.
    points : [(cx, cy)] mm ; widths : largeur du texte de chacun. Retourne [(pos, rect, anchor)] ; sans position libre,
    la premiere (NE). clip : rectangle hors duquel le numero n'est pas cherche a l'exterieur (inutilise si None)."""
    off = dot_r + 0.35
    placed = []
    out = [None] * len(points)
    tree = cKDTree(np.array(points, float)) if points else None
    for row in find_rows(points):
        n = row_normal([points[i] for i in row])
        for s in (n, -n):
            rects = [_row_rect(points[i], s, widths[i], text_h, off) for i in row]
            score = sum(_conflicts(r, placed, points, tree, dot_r, clip) for r in rects)
            score += sum(1 for a in range(len(rects)) for b in range(a + 1, len(rects)) if rects_overlap(rects[a], rects[b]))
            if score == 0:                       # rangee tout ou rien : un cote sans aucun conflit, sinon placement libre
                for i, rect in zip(row, rects):
                    out[i] = ('ROW', rect, 'middle')
                    placed.append(rect)
                break
    for k, ((cx, cy), w) in enumerate(zip(points, widths)):
        if out[k] is not None:
            continue
        chosen = None
        for pos in POS8:
            rect, anc = number_rect(pos, cx, cy, w, text_h, off)
            if _conflicts(rect, placed, points, tree, dot_r, clip) == 0:
                chosen = (pos, rect, anc)
                break
        if chosen is None:
            rect, anc = number_rect(POS8[0], cx, cy, w, text_h, off)
            chosen = (POS8[0], rect, anc)
        placed.append(chosen[1])
        out[k] = chosen
    return out


def polyline_point(pts, frac):
    """Point a la fraction `frac` de la longueur d'une polyligne ; pts (N, 2)."""
    pts = np.asarray(pts, float)
    seg = np.hypot(*(pts[1:] - pts[:-1]).T)
    tot = seg.sum()
    t = frac * tot
    acc = 0.0
    for i, L in enumerate(seg):
        if acc + L >= t or i == len(seg) - 1:
            u = 0.0 if L == 0 else (t - acc) / L
            return pts[i] + (pts[i + 1] - pts[i]) * min(max(u, 0.0), 1.0)
        acc += L
    return pts[-1]


def _hits(rect, other):
    """Rectangle (x0, y0, x1, y1) contre un rectangle ou une geometrie shapely (piste prolongee, par exemple)."""
    if isinstance(other, tuple):
        return rects_overlap(rect, other)
    from shapely.geometry import box
    return box(*rect).intersects(other)


LABEL_FRACTIONS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88, 0.05, 0.95)


def place_labels(ways, box_wh, forbidden, obstacles_pts, pt_pad=0.6, bounds=None):
    """Etiquettes de taxiways (cadrage 4.4.5) : une par nom, sur la plus longue ligne de ce nom, essayee aux fractions
    LABEL_FRACTIONS ; refusee si elle chevauche une autre etiquette, un rectangle interdit (coins, encarts, pistes
    prolongees) ou un point de parking (+- pt_pad). Non placee : omise.
    ways : liste de (nom, pts_mm (N, 2), longueur) ; box_wh : fonction nom -> (w, h) ; forbidden : rectangles ;
    obstacles_pts : points (M, 2). Retourne {nom: (cx, cy)}."""
    by_name = {}
    for nm, pts, L in ways:
        if nm not in by_name or L > by_name[nm][1]:
            by_name[nm] = (pts, L)
    placed, out = [], {}
    tree = cKDTree(np.asarray(obstacles_pts, float)) if len(obstacles_pts) else None
    for nm in sorted(by_name, key=dcsdata.natural_key):
        pts = by_name[nm][0]
        w, h = box_wh(nm)
        for fr in LABEL_FRACTIONS:
            cx, cy = polyline_point(pts, fr)
            rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            if bounds is not None and not (rect[0] >= bounds[0] and rect[1] >= bounds[1] and rect[2] <= bounds[2] and rect[3] <= bounds[3]):
                continue
            if any(rects_overlap(rect, r) for r in placed) or any(_hits(rect, r) for r in forbidden):
                continue
            if tree is not None:
                grown = (rect[0] - pt_pad, rect[1] - pt_pad, rect[2] + pt_pad, rect[3] + pt_pad)
                idx = tree.query_ball_point([cx, cy], math.hypot(w, h) / 2 + pt_pad + 0.1)
                if any(grown[0] <= obstacles_pts[j][0] <= grown[2] and grown[1] <= obstacles_pts[j][1] <= grown[3] for j in idx):
                    continue
            placed.append(rect)
            out[nm] = (cx, cy)
            break
    return out
