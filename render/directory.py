"""Repertoire des heliports (lot ac-heliports, cadrage 5) : sites, series, groupes, formes, placement, villes, plans, pages.

Les pages sont dessinees en pixels, directement a 1748 x 2480 px (A5, 300 dpi) : fond de plan = mosaique F10 de la capture
(icone et nom de l'heliport effaces, puis adoucie), cercles et numeros des aires, nord, barre de 100 m.
"""
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from . import dcsdata, geo, georef
from .fonts import FONT_DIR

FT = 3.28084
PAGE_PX = (1748, 2480)
INK, GREY, RED, WHITE = (20, 20, 20), (105, 105, 105), (170, 35, 35), (255, 255, 255)
TOWNS_PATH = Path(__file__).resolve().parent / 'data' / 'towns.json'
Image.MAX_IMAGE_PIXELS = None

# ---- geometrie de la page (px)
MARGIN_X = 90
TILE_W, TILE_GAP = 765, 39
TILE_H = 1006
ROW0_Y = 250
ROW_PITCH = TILE_H + TILE_GAP              # 1045
BAND_H = 87
PLAN_DX, PLAN_DY = 24, 106                 # plan : decalage depuis le coin du tuile
BELOW_H = 183                              # place sous le plan (positions, ville, dimensions)
PLAN_SIDE_PX = 717                         # plan d'une vignette simple : 260 m de terrain de cote
PLAN_SIDE_M = 260.0
PX_PER_M = PLAN_SIDE_PX / PLAN_SIDE_M
GROUP_LINK_M = 300.0                       # aires reliees de proche en proche a 300 m ou moins
GROUP_PAD_M = 20.0
TWO_GAP_PX = 22                            # espace entre les deux plans d'une vignette a deux groupes
CIRCLE_W = 5
NUM_SIZE, NUM_STROKE = 48, 3
SCALE_100M_PX = 100.0 * PX_PER_M

# ---- formes
SIMPLE, WIDE, TALL, TWO = 'simple', 'double largeur', 'double hauteur', 'deux groupes'
FORM_POSITIONS = {SIMPLE: (1, 2, 3, 4), WIDE: (1, 3), TWO: (1, 3), TALL: (1, 2)}
CELL_RC = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
OTHERS = 'AUTRES'
COMPASS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


class DirectoryError(Exception):
    """Cas hors des formes du cadrage 5.3 (arret du cadrage 7)."""


# ---------- polices ----------
_fonts = {}
FONT_FILES = {'regular': 'BarlowCondensed-Regular.ttf', 'medium': 'BarlowCondensed-Medium.ttf',
              'semibold': 'BarlowCondensed-SemiBold.ttf', 'bold': 'BarlowCondensed-Bold.ttf'}


def font(weight, size):
    key = (weight, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONT_DIR / FONT_FILES[weight]), size)
    return _fonts[key]


# ---------- sites ----------
@dataclass
class Aire:
    name: str
    x: float
    z: float
    length: float
    width: float


@dataclass
class Site:
    name: str                  # nom pydcs
    folder: Path               # capture
    x: float                   # position de l'aerodrome (repere DCS)
    z: float
    lat: float
    lon: float
    elev_ft: int
    aires: list                # Aire, ordre naturel des noms
    series: str = ''
    groups: list = field(default_factory=list)       # listes d'indices dans `aires`
    form: str = SIMPLE
    page: int = 0
    town: str = ''
    theatre: str = 'Syria'
    drawn: list = field(default_factory=list)       # noms des aires dessinees (cercle et numero) dans les plans
    blue_mosaic: tuple = None                       # (pixels bleus, plus gros amas) a moins de 20 m du point, fond efface


def series_of(name):
    """Nom pydcs prive de ses chiffres finaux ; sans chiffres finaux : serie « AUTRES »."""
    s = re.sub(r'\d+$', '', name)
    return s if s != name else OTHERS


def series_key(series):
    return (series == OTHERS, dcsdata.natural_key(series))


def load_towns(theatre):
    d = json.loads(TOWNS_PATH.read_text(encoding='utf-8'))
    return [(n, float(a), float(b)) for n, a, b in d['theatres'][theatre]]


def nearest_town(lat, lon, towns):
    """Ville la plus proche (cadrage 5.4) : « 6,5 km N de Nazareth » ; direction du site vue de la ville."""
    best = None
    for name, tlat, tlon in towns:
        dx = (lon - tlon) * 111.2 * math.cos(math.radians(lat))
        dy = (lat - tlat) * 111.2
        d = math.hypot(dx, dy)
        if best is None or d < best[0]:
            best = (d, dx, dy, name)
    d, dx, dy, name = best
    return f'{km_text(d)} {direction8(dx, dy)} de {name}'


def km_text(d):
    return f'{d:.1f}'.replace('.', ',') + ' km'


def direction8(dx, dy):
    """Direction (8 points) d'apres atan2(dx, dy) : dx vers l'est, dy vers le nord."""
    ang = math.degrees(math.atan2(dx, dy)) % 360.0
    return COMPASS[int((ang + 22.5) // 45) % 8]


def list_sites(cfg, theatre, log=print):
    """Les captures `kind: heliport` de out/<theatre>/ appariees a pydcs, sauf celles dont l'aerodrome a une radio de tour."""
    terrain = dcsdata.load_terrain(theatre)
    proj = geo.Projection.for_theatre(theatre)
    root = Path(cfg.work_dir) / 'out' / theatre.lower()
    sites, seen = [], {}
    for folder in sorted(root.iterdir(), key=lambda p: dcsdata.natural_key(p.name)):
        if not (folder / 'base.json').is_file():
            continue
        bj = georef.load_base_json(folder)
        if bj.get('kind') != 'heliport':
            continue
        center = (bj.get('extent_rn4') or {}).get('center')
        ap = dcsdata.match_airport(terrain, center[0], center[1]) if center else None
        if ap is None:
            log(f'{folder.name} : aucun aerodrome pydcs, ignore')
            continue
        if getattr(ap, 'atc_radio', None) is not None:
            continue                                         # a sa chart
        if ap.name in seen:
            raise DirectoryError(f'{folder.name} et {seen[ap.name]} designent le meme aerodrome pydcs ({ap.name})')
        seen[ap.name] = folder.name
        facts = dcsdata.airport_facts(ap)
        aires = [Aire(s.name, s.x, s.z, float(s.length), float(s.width)) for s in facts.slots]
        aires.sort(key=lambda a: dcsdata.natural_key(a.name))
        lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
        site = Site(ap.name, folder, ap.position.x, ap.position.y, lat, lon, int(round(float(bj['y_max_rn4']) * FT)), aires)
        site.series = series_of(site.name)
        site.theatre = theatre
        sites.append(site)
    sites.sort(key=lambda s: (series_key(s.series), dcsdata.natural_key(s.name)))
    return sites


# ---------- groupes et formes ----------
def group_aires(aires, link_m=GROUP_LINK_M):
    """Groupes d'aires reliees de proche en proche a `link_m` ou moins (liens simples). Retourne des listes d'indices,
    triees par le premier nom (ordre naturel)."""
    n = len(aires)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if math.hypot(aires[i].x - aires[j].x, aires[i].z - aires[j].z) <= link_m:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = [sorted(g, key=lambda i: dcsdata.natural_key(aires[i].name)) for g in groups.values()]
    out.sort(key=lambda g: dcsdata.natural_key(aires[g[0]].name))
    return out


def group_extent(aires, idx, pad_m=GROUP_PAD_M):
    """(x_min, x_max, z_min, z_max) : boite des centres, elargie de la demi-longueur de chaque aire, plus `pad_m`."""
    xs0 = [aires[i].x - aires[i].length / 2 for i in idx]
    xs1 = [aires[i].x + aires[i].length / 2 for i in idx]
    zs0 = [aires[i].z - aires[i].length / 2 for i in idx]
    zs1 = [aires[i].z + aires[i].length / 2 for i in idx]
    return min(xs0) - pad_m, max(xs1) + pad_m, min(zs0) - pad_m, max(zs1) + pad_m


def plan_px_size(form, which=0):
    """Taille (largeur, hauteur) en px du plan d'une vignette ; which : 0 ou 1 (vignette a deux groupes)."""
    full_w = 2 * TILE_W + TILE_GAP - 2 * PLAN_DX
    if form == SIMPLE:
        return PLAN_SIDE_PX, PLAN_SIDE_PX
    if form == WIDE:
        return full_w, PLAN_SIDE_PX
    if form == TALL:
        return PLAN_SIDE_PX, 2 * TILE_H + TILE_GAP - PLAN_DY - BELOW_H
    w0 = (full_w - TWO_GAP_PX + 1) // 2
    return (w0 if which == 0 else full_w - TWO_GAP_PX - w0), PLAN_SIDE_PX


def classify(aires, groups):
    """Forme de la vignette (cadrage 5.3). Arret (DirectoryError) hors des formes prevues."""
    ext = [group_extent(aires, g) for g in groups]
    ns = [e[1] - e[0] for e in ext]
    ew = [e[3] - e[2] for e in ext]
    if len(groups) == 1:
        if ns[0] <= PLAN_SIDE_M and ew[0] <= PLAN_SIDE_M:
            return SIMPLE
        if ns[0] <= PLAN_SIDE_M and ew[0] * PX_PER_M <= plan_px_size(WIDE)[0]:
            return WIDE
        if ew[0] <= PLAN_SIDE_M and ns[0] * PX_PER_M <= plan_px_size(TALL)[1]:
            return TALL
        raise DirectoryError(f'groupe de {ns[0]:.0f} x {ew[0]:.0f} m (N-S x E-O) : hors des formes de 5.3')
    if len(groups) == 2:
        if all(a <= PLAN_SIDE_M for a in ns + ew):
            return TWO
        raise DirectoryError(f'deux groupes dont un depasse {PLAN_SIDE_M:.0f} m ({ns} x {ew})')
    raise DirectoryError(f'{len(groups)} groupes : hors des formes de 5.3')


def group_label(aires, idx):
    """Etiquette « premier-dernier » des aires d'un groupe (« 01–03 ») ; une aire seule : son nom."""
    first, last = aires[idx[0]].name, aires[idx[-1]].name
    return first if first == last else f'{first}–{last}'


def dims_text(aires):
    """« 42×34 » ou « 42×34 · 30×23 » : dimensions distinctes (m), dans l'ordre des aires."""
    seen = []
    for a in aires:
        d = f'{int(round(a.length))}×{int(round(a.width))}'
        if d not in seen:
            seen.append(d)
    return ' · '.join(seen)


# ---------- placement dans la grille ----------
def footprint(form, pos):
    r, c = CELL_RC[pos]
    if form == SIMPLE:
        return {(r, c)}
    if form in (WIDE, TWO):
        return {(r, 0), (r, 1)}
    return {(0, c), (1, c)}


def place_forms(forms):
    """Pages d'une serie : `forms` = formes des sites dans l'ordre. Chaque site prend la premiere position, apres celle
    du site precedent, ou sa forme tient dans des cases libres ; sinon il ouvre une page neuve (pas de remplissage en
    arriere). Retourne une liste de pages, chacune une liste de (indice de site, position 1..4)."""
    pages, occ, last = [], None, 0
    for k, form in enumerate(forms):
        pos = None
        if occ is not None:
            for p in FORM_POSITIONS[form]:
                if p > last and not (footprint(form, p) & occ):
                    pos = p
                    break
        if pos is None:
            pages.append([])
            occ, last, pos = set(), 0, FORM_POSITIONS[form][0]
        occ |= footprint(form, pos)
        last = pos
        pages[-1].append((k, pos))
    return pages


def build_pages(sites):
    """Prepare les sites (groupes, forme) et les pages du recueil : liste de dicts
    {'series', 'rank', 'count', 'entries': [(site, position)]} dans l'ordre ; chaque serie commence sur une page neuve."""
    for s in sites:
        s.groups = group_aires(s.aires)
        s.form = classify(s.aires, s.groups)
    pages = []
    by_series = {}
    for s in sites:
        by_series.setdefault(s.series, []).append(s)
    for series in sorted(by_series, key=series_key):
        group = by_series[series]
        sp = place_forms([s.form for s in group])
        for k, entries in enumerate(sp):
            pages.append({'series': series, 'rank': k + 1, 'count': len(sp),
                          'entries': [(group[i], pos) for i, pos in entries]})
    return pages


# ---------- fond de plan ----------
def erase_label(img, r, c, name):
    """Efface l'icone et le nom que la F10 imprime au point de l'heliport (cadrage 5.4) : disque de 14 px et barre
    verticale de +-18 x +-5 px autour de (r, c) ; rectangle du texte de +-9 px en hauteur, de 8 px a droite du point
    jusqu'au dernier pixel bleu, blanc ou noir du cartouche (cherche dans r +-12, c-14 ... c+16+11 x len(nom)) ; inpainting
    OpenCV TELEA, rayon 5. Pixels de la mosaique ; le calcul se fait sur une fenetre autour du point."""
    h, w = img.shape[:2]
    pad = 40
    wr0, wr1 = max(0, r - 18 - pad), min(h, r + 19 + pad)
    wc0, wc1 = max(0, c - 14 - pad), min(w, c + 16 + 11 * len(name) + pad)
    win = np.ascontiguousarray(img[wr0:wr1, wc0:wc1])
    rr, cc = r - wr0, c - wc0
    hh, ww = win.shape[:2]
    r0, r1 = max(0, rr - 12), min(hh, rr + 12)
    c0, c1 = max(0, cc - 14), min(ww, cc + 16 + 11 * len(name))
    sub = win[r0:r1, c0:c1].astype(np.int16)
    R, G, B = sub[..., 0], sub[..., 1], sub[..., 2]
    core = ((B > 150) & (R < 110)) | ((R > 228) & (G > 228) & (B > 228)) | ((R < 55) & (G < 55) & (B < 55))
    full = np.zeros((hh, ww), np.uint8)
    cv2.circle(full, (cc, rr), 14, 1, -1)
    full[max(0, rr - 18):min(hh, rr + 19), max(0, cc - 5):min(ww, cc + 6)] = 1
    cols = np.where(core.any(0))[0]
    if len(cols):
        last = c0 + cols.max()
        full[max(0, rr - 9):min(hh, rr + 10), cc + 8:min(ww, last + 3)] = 1
    out = img.copy()
    out[wr0:wr1, wc0:wc1] = cv2.inpaint(win, full, 5, cv2.INPAINT_TELEA)
    return out


def crop_plan(img, g, center_xz, w_px, h_px, fill=(222, 222, 222)):
    """Fond du plan : la mosaique centree sur `center_xz` (x nord, z est), a l'echelle commune PX_PER_M ; hors de la
    mosaique : `fill`. Retourne une image PIL RGB de w_px x h_px."""
    xc, zc = center_xz
    half_z, half_x = w_px / 2 / PX_PER_M, h_px / 2 / PX_PER_M
    u0, u1 = (zc - half_z - g.z_left) / g.mpp, (zc + half_z - g.z_left) / g.mpp
    v0, v1 = (g.x_top - (xc + half_x)) / g.mpp, (g.x_top - (xc - half_x)) / g.mpp
    wc0, wc1 = int(math.floor(u0)) - 2, int(math.ceil(u1)) + 2
    wr0, wr1 = int(math.floor(v0)) - 2, int(math.ceil(v1)) + 2
    H, W = img.shape[:2]
    win = np.empty((wr1 - wr0, wc1 - wc0, 3), np.uint8)
    win[:] = fill
    a0, a1, b0, b1 = max(wr0, 0), min(wr1, H), max(wc0, 0), min(wc1, W)
    if a1 > a0 and b1 > b0:
        win[a0 - wr0:a1 - wr0, b0 - wc0:b1 - wc0] = img[a0:a1, b0:b1]
    return Image.fromarray(win).resize((w_px, h_px), Image.BICUBIC, box=(u0 - wc0, v0 - wr0, u1 - wc0, v1 - wr0))


def soften(im):
    """Saturation a 25 %, luminosite + 12 %."""
    return ImageEnhance.Brightness(ImageEnhance.Color(im).enhance(0.25)).enhance(1.12)


def blue_clusters(rgb, center_px, radius_px):
    """R4 : (pixels bleus restants, plus gros amas 8-connexe) dans le disque de rayon `radius_px` ; bleu : B > 150 et R < 110."""
    h, w = rgb.shape[:2]
    cx, cy = center_px
    yy, xx = np.ogrid[:h, :w]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius_px ** 2
    blue = (rgb[..., 2] > 150) & (rgb[..., 0] < 110) & disk
    if not blue.any():
        return 0, 0
    n, _, st, _ = cv2.connectedComponentsWithStats(blue.view(np.uint8), connectivity=8)
    return int(blue.sum()), int(st[1:, cv2.CC_STAT_AREA].max())


# ---------- dessin ----------
def aa_circle(arr, cx, cy, r, color, width):
    """Anneau de rayon exterieur `r` et d'epaisseur `width` px (vers l'interieur), anti-crenele ; (cx, cy) : coordonnees
    continues du plan (le pixel i couvre [i, i + 1[)."""
    sh = 4
    cv2.circle(arr, (int(round((cx - 0.5) * (1 << sh))), int(round((cy - 0.5) * (1 << sh)))),
               int(round((r - width / 2 + 0.5) * (1 << sh))), color, width - 1, cv2.LINE_AA, sh)


def draw_text(d, xy, s, weight, size, fill, anchor='ls', **kw):
    d.text(xy, s, font=font(weight, size), fill=fill, anchor=anchor, **kw)


def draw_plan(site, img, g, center_xz, w_px, h_px, first, stats):
    """Un plan : fond adouci, cercles et numeros des aires, nord, barre de 100 m (premier plan de la vignette).
    `stats` : liste ou s'ajoute (nom, pixels bleus, plus gros amas) pour le point de l'heliport, si le plan le contient."""
    bg = crop_plan(img, g, center_xz, w_px, h_px)
    xc, zc = center_xz
    half_z, half_x = w_px / 2 / PX_PER_M, h_px / 2 / PX_PER_M

    def to_px(x, z):
        return (z - (zc - half_z)) * PX_PER_M, ((xc + half_x) - x) * PX_PER_M
    px, py = to_px(site.x, site.z)
    if 0 <= px < w_px and 0 <= py < h_px:
        stats.append((site.name, ) + blue_clusters(np.asarray(bg), (px, py), 20.0 * PX_PER_M))
    arr = np.array(soften(bg))
    labels = []
    for a in site.aires:
        cx, cy = to_px(a.x, a.z)
        r = a.length / 2 * PX_PER_M
        if cx + r < 0 or cx - r > w_px or cy + r < 0 or cy - r > h_px:
            continue
        aa_circle(arr, cx, cy, r, RED, CIRCLE_W)
        labels.append((a.name, cx + r * math.sqrt(0.5), cy - r * math.sqrt(0.5)))
        site.drawn.append(a.name)
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    for name, lx, ly in labels:
        draw_text(d, (lx, ly), name, 'bold', NUM_SIZE, RED, 'ls', stroke_width=NUM_STROKE, stroke_fill=WHITE)
    # nord : fleche et N, en haut a droite
    ax = w_px - 45
    d.polygon([(ax, 28), (ax - 14, 68), (ax, 60), (ax + 15, 68)], fill=INK)
    draw_text(d, (ax, 95), 'N', 'bold', 30, INK, 'ms', stroke_width=5, stroke_fill=WHITE)
    if first:                                                  # barre de 100 m, en bas a gauche
        by1 = h_px - 22
        d.rectangle([12, by1 - 44, 12 + 372, by1], fill=WHITE)
        x0 = 22
        x1 = x0 + SCALE_100M_PX
        yb = by1 - 16
        d.line([(x0, yb), (x1, yb)], fill=INK, width=4)
        for xt in (x0, (x0 + x1) / 2, x1):
            d.line([(xt, yb - 8), (xt, yb + 8)], fill=INK, width=4)
        draw_text(d, (x1 + 10, by1 - 3), '100 m', 'semibold', 31, INK, 'ls')
    d.rectangle([0, 0, w_px - 1, h_px - 1], outline=GREY, width=2)
    return im


def tile_box(form, pos):
    """(x, y, w, h) de la vignette sur la page."""
    r, c = CELL_RC[pos]
    x = MARGIN_X + (c * (TILE_W + TILE_GAP) if form != WIDE and form != TWO else 0)
    y = ROW0_Y + r * ROW_PITCH
    w = 2 * TILE_W + TILE_GAP if form in (WIDE, TWO) else TILE_W
    h = 2 * TILE_H + TILE_GAP if form == TALL else TILE_H
    return x, y, w, h


def aires_count_text(n):
    return f'{n} AIRE' if n == 1 else f'{n} AIRES'


def draw_tile(page, d, site, pos, img, g, stats):
    x, y, w, h = tile_box(site.form, pos)
    d.rectangle([x, y, x + w - 1, y + h - 1], outline=INK, width=3)
    d.rectangle([x, y, x + w - 1, y + BAND_H - 1], fill=INK)
    draw_text(d, (x + PLAN_DX, y + 65), site.name, 'bold', 56, WHITE, 'ls')
    draw_text(d, (x + w - PLAN_DX, y + 57), aires_count_text(len(site.aires)), 'semibold', 36, WHITE, 'rs')
    px0, py0 = x + PLAN_DX, y + PLAN_DY
    plan_bottom = None
    for k, idx in enumerate(site.groups):
        e = group_extent(site.aires, idx)
        center = ((e[0] + e[1]) / 2, (e[2] + e[3]) / 2)
        which = k if site.form == TWO else 0
        w_px, h_px = plan_px_size(site.form, which)
        pim = draw_plan(site, img, g, center, w_px, h_px, k == 0, stats)
        ox = px0 + (0 if k == 0 else plan_px_size(site.form, 0)[0] + TWO_GAP_PX)
        if site.form == TWO:
            pd = ImageDraw.Draw(pim)
            lab = group_label(site.aires, idx)
            tw = font('bold', 40).getlength(lab)
            pd.rectangle([2, 2, 2 + 15 + tw + 56, 2 + 50], fill=WHITE)
            draw_text(pd, (2 + 15, 2 + 41), lab, 'bold', 40, INK, 'ls')
        page.paste(pim, (ox, py0))
        plan_bottom = py0 + h_px
    base1, base2 = plan_bottom + 58, plan_bottom + 108
    draw_text(d, (x + PLAN_DX, base1), f"{geo.dm(site.lat, 'N', 'S', 2)}  {geo.dm(site.lon, 'E', 'W', 3)}", 'bold', 39, INK, 'ls')
    draw_text(d, (x + w - PLAN_DX, base1), f'ÉLÉV {geo.thin(site.elev_ft)} FT', 'bold', 39, INK, 'rs')
    draw_text(d, (x + PLAN_DX, base2), site.town, 'regular', 36, INK, 'ls')
    draw_text(d, (x + w - PLAN_DX, base2), f'AIRES {dims_text(site.aires)} M', 'regular', 36, INK, 'rs')
    if site.form == TWO:
        g1, g2 = site.groups
        e1, e2 = group_extent(site.aires, g1), group_extent(site.aires, g2)
        c1 = ((e1[0] + e1[1]) / 2, (e1[2] + e1[3]) / 2)
        c2 = ((e2[0] + e2[1]) / 2, (e2[2] + e2[3]) / 2)
        proj = geo.Projection.for_theatre(site.theatre)
        la1, lo1 = proj.to_latlon(*c1)
        la2, lo2 = proj.to_latlon(*c2)
        dx = (lo2 - lo1) * 111.2 * math.cos(math.radians(la2))
        dy = (la2 - la1) * 111.2
        txt = f'{group_label(site.aires, g2)} à {km_text(math.hypot(dx, dy))} {direction8(dx, dy)} de {group_label(site.aires, g1)}'
        draw_text(d, (x + w / 2, base1), txt, 'bold', 35, GREY, 'ms')


def page_frame(theatre, right_bold, right_grey):
    """Page blanche avec l'en-tete (a gauche « SYRIA » et « RÉPERTOIRE DES HÉLIPORTS » ; a droite deux lignes) et le pied."""
    page = Image.new('RGB', PAGE_PX, WHITE)
    d = ImageDraw.Draw(page)
    x0, x1 = MARGIN_X, PAGE_PX[0] - MARGIN_X + 1
    draw_text(d, (x0, 147), theatre.upper(), 'bold', 56, INK, 'ls')
    draw_text(d, (x0, 195), 'RÉPERTOIRE DES HÉLIPORTS', 'medium', 44, INK, 'ls')
    draw_text(d, (x1, 155), right_bold, 'bold', 57, INK, 'rs')
    draw_text(d, (x1, 194), right_grey, 'regular', 35, GREY, 'rs')
    d.rectangle([x0, 219, x1 - 1, 222], fill=INK)
    d.rectangle([x0, 2330, x1 - 1, 2331], fill=GREY)
    return page, d


def footer(d, left):
    x1 = PAGE_PX[0] - MARGIN_X + 1
    draw_text(d, (MARGIN_X, 2379), left, 'medium', 29, GREY, 'ls')
    draw_text(d, (x1, 2379), 'PAS POUR LA NAVIGATION RÉELLE', 'bold', 29, INK, 'rs')


FOOT_LEFT = 'Tous les plans à la même échelle · aires et numéros : DCS (pydcs) · fond : carte F10 · WGS84'
INDEX_ROWS_PER_PAGE = 50
INDEX_Y0, INDEX_PITCH = 300, 40


def page_title(first, last):
    return first if first == last else f'{first} – {last}'


def index_page_count(n_sites):
    return max(1, math.ceil(n_sites / INDEX_ROWS_PER_PAGE))


def draw_index_page(theatre, sites, k, n_pages):
    """Page d'index : une ligne par site (nom, aires, position, ville la plus proche, numero de page)."""
    chunk = sites[k * INDEX_ROWS_PER_PAGE:(k + 1) * INDEX_ROWS_PER_PAGE]
    page, d = page_frame(theatre, page_title(chunk[0].name, chunk[-1].name), f'INDEX · {k + 1} / {n_pages}')
    x1 = PAGE_PX[0] - MARGIN_X + 1
    cols = {'name': MARGIN_X, 'aires': 520, 'pos': 590, 'town': 960, 'page': x1}
    y = 262
    for key, label, anchor in (('name', 'NOM', 'ls'), ('aires', 'AIRES', 'rs'), ('pos', 'POSITION', 'ls'),
                               ('town', 'VILLE LA PLUS PROCHE', 'ls'), ('page', 'PAGE', 'rs')):
        draw_text(d, (cols[key], y), label, 'semibold', 26, GREY, anchor)
    d.rectangle([MARGIN_X, 272, x1 - 1, 273], fill=INK)
    for i, s in enumerate(chunk):
        y = INDEX_Y0 + 20 + i * INDEX_PITCH
        draw_text(d, (cols['name'], y), s.name, 'bold', 31, INK, 'ls')
        draw_text(d, (cols['aires'], y), str(len(s.aires)), 'regular', 31, INK, 'rs')
        draw_text(d, (cols['pos'], y), f"{geo.dm(s.lat, 'N', 'S', 2)}  {geo.dm(s.lon, 'E', 'W', 3)}", 'regular', 31, INK, 'ls')
        draw_text(d, (cols['town'], y), s.town, 'regular', 31, INK, 'ls')
        draw_text(d, (cols['page'], y), str(s.page), 'bold', 31, INK, 'rs')
        d.rectangle([MARGIN_X, y + 9, x1 - 1, y + 9], fill=(222, 222, 222))
    footer(d, "Numéro de page : page du recueil (PDF) · position : aérodrome pydcs · ville : liste towns.json")
    return page


def draw_directory_page(theatre, pg, cache, stats):
    """Page du recueil : en-tete (premier et dernier nom, serie · rang / total), les vignettes, le pied."""
    names = [s.name for s, _ in pg['entries']]
    page, d = page_frame(theatre, page_title(names[0], names[-1]), f"{pg['series']} · {pg['rank']} / {pg['count']}")
    for site, pos in pg['entries']:
        img, g = cache(site)
        draw_tile(page, d, site, pos, img, g, stats)
    footer(d, FOOT_LEFT)
    return page


def load_site_mosaic(site):
    """Mosaique de la capture, repere, avec l'icone et le nom de l'heliport effaces (pixels de la mosaique)."""
    bj = georef.load_base_json(site.folder)
    g = georef.georef_from_base(bj)
    img = np.asarray(Image.open(site.folder / 'mosaic.png').convert('RGB'))
    r, c = g.xz_to_rc(site.x, site.z)
    r, c = int(round(r)), int(round(c))
    img = erase_label(img, r, c, site.name)
    rad = int(math.ceil(20.0 / g.mpp))
    site.blue_mosaic = blue_clusters(img[max(0, r - rad - 2):r + rad + 3, max(0, c - rad - 2):c + rad + 3],
                                     (c - max(0, c - rad - 2), r - max(0, r - rad - 2)), 20.0 / g.mpp)
    return img, g
