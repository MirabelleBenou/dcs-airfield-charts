"""Pages d'approche aux instruments (lot ac-approches-v1) : la page avion `11-x` et la page helicoptere `11-xH` d'un sens de piste.

Composition des maquettes validees de Konya : cartouche (en-tete de la 10-9 du terrain, deux bandes de paires `page.pair_row`),
vue en plan sur le relief de DCS (nord de la grille en haut, procedure en noir, interrompue en tirets, balises, deux points
cotes, boite MSA en haut a droite, echelle, moyens requis), profil, tableau, texte de l'interrompue, encadre « NO MINIMA »,
legende, pied de page. Les etiquettes sont placees par regle (`Placer`) : aucune ne chevauche une trajectoire, une autre
etiquette, la boite MSA ou l'echelle. La geometrie vient de `procedure.py` ; ce module ne fait que dessiner.
"""
import base64
import io
import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from . import compose, geo, page, procedure
from .fonts import CAP_HEIGHT, pt, text_width
from .page import PageOut, View, X0, X1
from .procedure import FT, NM, TAN3
from .svgdoc import BAND, INK, INK2, Doc, circle, f, line, rect, text, with_halo

BAND1_Y, BAND2_Y, BAND_H = 30.0, 36.2, 5.2            # sous le bandeau de la tour, recopie de la 10-9
BAND_TEXT_X, PAIRS_X = 7.5, 28.0
PLAN_Y = 43.4
PLAN_BOTTOM = {'avion': 125.2, 'helico': 131.2}
FOOTER_TEXT = 'DCS AIRFIELD CHARTS · data: DCS World files and terrain (Eagle Dynamics), via pydcs · non-commercial use'
PX_PER_MM = 12
TINTS = ((200, (246, 244, 237)), (600, (238, 233, 218)), (1600, (229, 220, 197)), (2600, (219, 206, 178)),
         (3600, (207, 190, 158)), (None, (192, 172, 138)))     # teintes du relief : bornes au-dessus de l'AD ELEV (ft)
MSA_W, MSA_H = 27.0, 30.0
LABEL_PAD = 0.35                  # ecart minimal entre une etiquette et un obstacle (mm)
LINE_GAP = 0.95                   # interligne d'un bloc d'etiquette (mm)
SPOT_NEAR_NM = {'avion': 5.0, 'helico': 3.0}


# ---------- etiquettes ----------
def block_size(lines):
    w = max(text_width(s, wt, sz) for s, sz, wt, _ in lines)
    h = sum(sz * CAP_HEIGHT for _, sz, _, _ in lines) + LINE_GAP * (len(lines) - 1)
    return w, h


def block_elems(lines, left, top, w, anchor):
    """Les <text> (avec halo) d'un bloc dont le coin haut-gauche est (left, top)."""
    x = left if anchor == 'start' else left + w if anchor == 'end' else left + w / 2
    out, y = [], top
    for s, sz, wt, fill in lines:
        y += sz * CAP_HEIGHT
        out.append(with_halo(text(x, y, s, sz, wt, fill, anchor)))
        y += LINE_GAP
    return out


DIRS = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 15)]


class Placer:
    """Placement par regle : autour du point d'ancrage, de la plus courte distance a la plus longue, dans les douze directions
    rangees de la plus proche de la direction preferee a la plus eloignee (tous les 15 degres) ; la premiere place qui est dans le cadre et ne touche
    ni un obstacle (trajectoires, symboles, boites), ni une etiquette deja posee."""

    def __init__(self, frame, margin=0.8):
        fx, fy, fw, fh = frame
        self.inner = box(fx + margin, fy + margin, fx + fw - margin, fy + fh - margin)
        self.obstacles = []
        self.labels = []
        self.failed = []

    def block(self, g):
        self.obstacles.append(g)

    def free(self, g):
        if not self.inner.contains(g):
            return False
        gp = g.buffer(LABEL_PAD)
        return not any(gp.intersects(o) for o in self.obstacles) and not any(gp.intersects(o) for o in self.labels)

    def place(self, name, lines, X, Y, pref=(1.0, 0.0), d0=1.4, steps=30, pitch=1.0):
        """Retourne les elements du bloc ; s'il n'a aucune place libre, la position la plus proche (comptee dans `failed`)."""
        w, h = block_size(lines)
        n = math.hypot(*pref) or 1.0
        px, py = pref[0] / n, pref[1] / n
        dirs = sorted(DIRS, key=lambda d: (-(d[0] * px + d[1] * py), d[1], d[0]))
        first = None
        for k in range(steps):
            d = d0 + k * pitch
            for dx, dy in dirs:
                sx = abs(dx) * w / 2 + abs(dy) * h / 2
                cx, cy = X + dx * (d + sx), Y + dy * (d + sx)
                g = box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                anchor = 'start' if dx > 0.4 else 'end' if dx < -0.4 else 'middle'
                if first is None:
                    first = (cx, cy, anchor, g)
                if self.free(g):
                    self.labels.append(g)
                    return block_elems(lines, cx - w / 2, cy - h / 2, w, anchor)
        cx, cy, anchor, g = first
        self.failed.append(name)
        self.labels.append(g)
        return block_elems(lines, cx - w / 2, cy - h / 2, w, anchor)

    def place_rotated(self, name, s, size, weight, pts, rot, offs):
        """Texte tourne de `rot` degres, centre en l'un des `pts` (deja decales) : le premier libre."""
        h = size * CAP_HEIGHT
        w = text_width(s, weight, size)
        first = None
        for cx, cy in pts:
            for o in offs:
                X, Y = cx + o[0], cy + o[1]
                g = Polygon(page.rot_box(X, Y, w, h, rot))
                if first is None:
                    first = (X, Y, g)
                if self.free(g):
                    self.labels.append(g)
                    return with_halo(_rot_text(s, X, Y, rot, size, weight))
        X, Y, g = first
        self.failed.append(name)
        self.labels.append(g)
        return with_halo(_rot_text(s, X, Y, rot, size, weight))


def _rot_text(s, cx, cy, rot, size, weight, fill=INK):
    return (f'<g transform="translate({f(cx)} {f(cy)}) rotate({f(rot, 2)})">'
            + text(0, size * CAP_HEIGHT / 2, s, size, weight, fill, 'middle') + '</g>')


# ---------- primitives ----------
def polyline(pts, sw, extra=''):
    d = ' '.join(f'{f(x)},{f(y)}' for x, y in pts)
    return f'<polyline points="{d}" fill="none" stroke="{INK}" stroke-width="{f(sw)}" stroke-linejoin="round"{(" " + extra) if extra else ""}/>'


def arrow(X, Y, az_grid, size=1.8):
    """Fleche pleine, pointe en (X, Y), dans la direction grille `az_grid`."""
    dX, dY = math.sin(math.radians(az_grid)), -math.cos(math.radians(az_grid))
    bx, by = X - dX * size, Y - dY * size
    rX, rY = -dY, dX
    return (f'<polygon points="{f(X)},{f(Y)} {f(bx + rX * size * 0.42)},{f(by + rY * size * 0.42)} '
            f'{f(bx - rX * size * 0.42)},{f(by - rY * size * 0.42)}" fill="{INK}"/>')


def malte(X, Y, r=1.3, w=0.6):
    """Croix de Malte du FAF."""
    d = ''.join(f'M{f(X)},{f(Y)} L{f(X + a * r - b * w * r)},{f(Y + b * r + a * w * r)} L{f(X + a * r + b * w * r)},{f(Y + b * r - a * w * r)} Z '
                for a, b in ((0, -1), (1, 0), (0, 1), (-1, 0)))
    return f'<path d="{d.strip()}" fill="{INK}"/>'


def malte_geom(X, Y, r=1.3):
    return Point(X, Y).buffer(r)


def beacon_text(b):
    return f'{b.callsign} {b.freq}'


# ---------- relief ----------
def relief_image(alt, x0, z0, view, frame, elev_ft):
    """Le fond de la vue en plan : le relief de la grille en teintes (bornes au-dessus de l'AD ELEV), ombrage leger ; PNG en
    data URI, et la fenetre de la grille (G, i_hi, j_lo) pour les points cotes."""
    fx, fy, fw, fh = frame
    s = view.s
    W, H = int(round(fw * PX_PER_MM)), int(round(fh * PX_PER_MM))
    x_top, z_l = view.ground(fx, fy)
    x_bot, z_r = view.ground(fx + fw, fy + fh)
    i_hi = int(math.ceil((x_top - x0) / 100)) + 3
    i_lo = int(math.floor((x_bot - x0) / 100)) - 3
    j_lo = int(math.floor((z_l - z0) / 100)) - 3
    j_hi = int(math.ceil((z_r - z0) / 100)) + 3
    G = np.full((i_hi - i_lo + 1, j_hi - j_lo + 1), np.nan)
    a0, a1 = max(i_lo, 0), min(i_hi, alt.shape[0] - 1)
    b0, b1 = max(j_lo, 0), min(j_hi, alt.shape[1] - 1)
    if a0 <= a1 and b0 <= b1:
        G[a0 - i_lo:a1 - i_lo + 1, b0 - j_lo:b1 - j_lo + 1] = np.asarray(alt[a0:a1 + 1, b0:b1 + 1], dtype=np.float64)
    G = np.nan_to_num(G, nan=0.0)[::-1, :]                       # ligne 0 = le nord (i_hi)
    gy, gx = np.gradient(gaussian_filter(G, 1.2), 100.0)
    shade = np.clip(1.0 + (-gx * 0.7 - gy * 0.7) * 1.6, 0.80, 1.08)
    scale = 100 * s * PX_PER_MM
    left = (z_l - (z0 + 100 * j_lo)) / 100 * scale + 0.5 * scale
    top = ((x0 + 100 * i_hi) - x_top) / 100 * scale + 0.5 * scale

    def up(arr):
        im = Image.fromarray(arr.astype(np.float32), 'F').resize((int(round(arr.shape[1] * scale)), int(round(arr.shape[0] * scale))),
                                                                Image.BICUBIC)
        return np.asarray(im.crop((int(round(left)), int(round(top)), int(round(left)) + W, int(round(top)) + H)), dtype=np.float64)
    Gf = up(gaussian_filter(G, 0.6) * FT)
    sh = up(shade)
    base = round(elev_ft / 100.0) * 100
    img = np.zeros(Gf.shape + (3,), np.float64)
    lo = -1e9
    for d, c in TINTS:
        hi = base + d if d is not None else 1e9
        img[(Gf >= lo) & (Gf < hi)] = c
        lo = hi
    img *= sh[..., None]
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(buf, 'PNG', optimize=True)
    el = (f'<image x="{f(fx)}" y="{f(fy)}" width="{f(fw)}" height="{f(fh)}" preserveAspectRatio="none" '
          f'xlink:href="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"/>')
    return el, G, i_hi, j_lo


def spot_heights(G, i_hi, j_lo, x0, z0, view, frame, reserved, thr, az, near_nm):
    """Les deux points cotes : le plus haut du cadre, le plus haut a `near_nm` NM au plus de l'axe de la finale."""
    fx, fy, fw, fh = frame
    ii, jj = np.mgrid[0:G.shape[0], 0:G.shape[1]]
    xs = x0 + 100.0 * (i_hi - ii)
    zs = z0 + 100.0 * (j_lo + jj)
    X = view.ox + (zs - view.zc) * view.s
    Y = view.oy - (xs - view.xc) * view.s
    inside = (X > fx + 3) & (X < fx + fw - 3) & (Y > fy + 3) & (Y < fy + fh - 3)
    for r in reserved:
        b0, b1, b2, b3 = r.bounds
        inside &= ~((X > b0 - 2) & (X < b2 + 2) & (Y > b1 - 2) & (Y < b3 + 2))
    u = procedure.unit(az)
    axd = np.abs((zs - thr[1]) * u[0] - (xs - thr[0]) * u[1])
    out = []
    for msk in (inside, inside & (axd < near_nm * NM)):
        if not msk.any():
            continue
        k = np.unravel_index(np.argmax(np.where(msk, G, -np.inf)), G.shape)
        p = (float(X[k]), float(Y[k]), int(round(G[k] * FT / 10.0)) * 10)
        if p not in out:
            out.append(p)
    return out


# ---------- vue en plan : cadrage ----------
def fit_view(paths, points, frame, reserved, margin=4.0):
    """La vue (echelle, centre) : la plus grande echelle (de 1 a 0,5 fois celle qui remplit le cadre) et le plus petit
    decalage du centre pour lesquels toute la procedure est dans le cadre (marge `margin`) sans toucher les boites
    reservees (boite MSA, echelle, moyens requis, mention du nord)."""
    fx, fy, fw, fh = frame
    allp = [p for pts in paths for p in pts] + list(points)
    xmin, xmax = min(p[0] for p in allp), max(p[0] for p in allp)
    zmin, zmax = min(p[1] for p in allp), max(p[1] for p in allp)
    s0 = min((fh - 2 * margin - 8) / max(xmax - xmin, 1.0), (fw - 2 * margin - 16) / max(zmax - zmin, 1.0))
    xc, zc = (xmin + xmax) / 2, (zmin + zmax) / 2
    inner = box(fx + margin, fy + margin, fx + fw - margin, fy + fh - margin)
    res = unary_union([r.buffer(1.5) for r in reserved])
    offs = sorted({(dx, dy) for dx in range(-40, 41, 2) for dy in range(-30, 31, 2)}, key=lambda o: (o[0] ** 2 + o[1] ** 2, o))
    cx, cy = fx + fw / 2, fy + fh / 2
    for k in range(0, 11):
        s = s0 * (1.0 - 0.05 * k)
        view = View(s, xc, zc, cx, cy)
        geoms = [LineString([view.pt(*p) for p in pts]) for pts in paths if len(pts) > 1] + [Point(view.pt(*p)) for p in points]
        g0 = unary_union(geoms).buffer(1.0)
        b = g0.bounds
        for dx, dy in offs:
            if b[0] + dx < inner.bounds[0] or b[2] + dx > inner.bounds[2] or b[1] + dy < inner.bounds[1] or b[3] + dy > inner.bounds[3]:
                continue
            g = affinity.translate(g0, dx, dy)
            if not g.intersects(res):
                return View(s, xc, zc, cx + dx, cy + dy)
    return View(s0 * 0.5, xc, zc, cx, cy)


# ---------- la page ----------
@dataclass
class Site:
    """Ce que la page dit du terrain, hors procedure."""
    header: list                  # lignes d'en-tete (10-9 : titre et numero de page remplaces)
    elev_ft: int
    year: float
    runways: list                 # [(p0, p1, largeur m)] de toutes les pistes
    beacons: list                 # balises utilisables du terrain (procedure.Balise)
    alt: object
    x0: float
    z0: float


def _required(P):
    e = P.e
    kind = 'ADF' if e.ref.kind == 'NDB' else e.ref.kind
    s = ('ILS, ' if P.ils else '') + kind + (' OR ADF' if e.ndb is not None else '')
    return s + ' REQUIRED'


def title_avion(P):
    e = P.e
    return ('ILS OR ' if P.ils else '') + e.ref.kind + (' OR NDB' if e.ndb is not None else '') + f' RWY {e.sens}'


def title_helico(P):
    return f'COPTER NDB RWY {P.e.sens}'


def _bands(doc, b1_label, b1, b2_label, b2):
    L = 'Header'
    for y, lab, pairs in ((BAND1_Y, b1_label, b1), (BAND2_Y, b2_label, b2)):
        doc.add(L, rect(X0, y, X1 - X0, BAND_H, BAND))
        doc.add(L, text(BAND_TEXT_X, y + 3.6, lab, pt(7.6), 'bold'))
        page.pair_row(doc, L, pairs, PAIRS_X, y + 3.6, X1 - PAIRS_X - 1.5)


def _radial_label(P):
    return 'R' if P.e.ref.kind != 'NDB' else 'QDR'


def _out_label(P):
    return f'R-{P.qdr_out:03d}' if P.e.ref.kind != 'NDB' else f'QDR {P.qdr_out:03d}°'


def build_page(site, P, index, log=print):
    """La page `index` (11-x ou 11-xH) de la procedure P : PageOut ; info : textes, echelle, etiquettes sans place."""
    heli = P.famille == 'helico'
    e = P.e
    ref = e.ref
    cs = ref.callsign
    doc = Doc()
    doc.add('Background', rect(0, 0, page.PAGE_W, page.PAGE_H, '#FFFFFF'))
    title = title_helico(P) if heli else title_avion(P)
    for ln in site.header:
        doc.add('Header', ln)
    var, yl = geo.var_text(e.decl, site.year)
    kmh = procedure.HELI_KMH
    spd = f'{int(procedure.HELI_KT)} KT · {kmh} KM/H'
    if heli:
        _bands(doc, 'CAT H', [('NDB', beacon_text(ref)), ('FINAL CRS', f'{P.crs:03d}° RWY AXIS'), ('FAF', 'END OF BASE TURN'),
                              ('ALT', f'{P.zone_ft} FT'), ('QFE', f'{procedure.qfe_m(P.zone_ft, site.elev_ft)} M'), ('IAS', spd)],
               'LANDING', [('RWY', e.sens), ('THR', f'{e.thr_ft} FT · {int(round(e.thr_ft / FT))} M'), ('OR', 'HELIPAD VISUALLY · 10-9')])
    else:
        b1 = ([('LOC', e.ils['loc'])] if P.ils else []) + [(ref.kind, beacon_text(ref))] + \
            ([('NDB', beacon_text(e.ndb))] if e.ndb is not None else []) + [('FINAL CRS', f'{P.crs:03d}° RWY AXIS'), ('THR', f'{e.thr_ft} FT')]
        if ref.dme:
            faf = f'D{P.faf_dme:.1f} {cs}' + (f' · R-{P.rad_faf:03d}' if P.rad_faf is not None else '') + f' · {P.int_ft} FT'
        else:
            faf = f'{P.faf_nm:.1f} NM FROM THR · {P.int_ft} FT'
        b2 = [('FAF', faf)]
        if e.ndb is not None:
            b2.append(('NDB', f'FAF: QDM {P.qdm_faf:03d}° · MAPt: THR, TIMED'))
        elif ref.kind == 'NDB':
            b2.append(('NDB', f'FAF: ESTABLISHED INBOUND, QDM {P.qdm_faf:03d}°'))
        if P.ils:
            b2.append(('GP', f'3.0° · TCH {P.tch} FT'))
        elif ref.dme:
            b2.append(('MAPt', f'D{P.thr_dme:.1f} {cs}'))
        else:
            b2.append(('MAPt', 'THR, TIMED'))
        _bands(doc, f'RWY {e.sens}', b1, 'FIXES', b2)

    # ---- vue en plan
    fx, fy = X0, PLAN_Y
    fw, fh = X1 - X0, PLAN_BOTTOM[P.famille] - PLAN_Y
    frame = (fx, fy, fw, fh)
    doc.add_clip('clip-plan', fx, fy, fw, fh)
    bx_, by_ = fx + fw - MSA_W - 1.5, fy + 1.5
    msa_box = box(bx_, by_, bx_ + MSA_W, by_ + MSA_H)
    scale_box = box(fx + 1.5, fy + fh - (12.3 if heli else 6.2), fx + 33.5, fy + fh - 0.8)
    req = _required(P) if not heli else 'ADF REQUIRED'
    req_el = text(fx + 2.0, fy + 4.2, req, pt(6.4), 'bold', INK, 'start')
    north = 'GRID NORTH (GAME NORTH) UP · RELIEF READ IN DCS'
    north_el = text(fx + fw - 2.0, fy + fh - 2.1, north, pt(5.0), 'regular', INK2, 'end')
    reserved = [msa_box, scale_box, page.elem_geom(req_el), page.elem_geom(north_el)]
    paths = [P.hold_pts, P.out_pts, [P.IF or P.FAF, P.FAF, e.thr], P.mis_pts] + [[r[0], r[1]] for r in site.runways]
    pts_extra = [b.pos for b in ([ref] if heli else site.beacons)]
    view = fit_view(paths, pts_extra, frame, reserved)
    s = view.s
    V = lambda p: view.pt(*p)
    img, G, i_hi, j_lo = relief_image(site.alt, site.x0, site.z0, view, frame, site.elev_ft)
    doc.add('F10 map', img)
    pl = Placer(frame)
    for r in reserved:
        pl.block(r)

    def draw(layer, el, geom=True):
        doc.add(layer, el)
        if geom:
            pl.block(page.elem_geom(el))

    # pistes
    for p0, p1, w in site.runways:
        a, b = V(p0), V(p1)
        draw('Runways', line(a[0], a[1], b[0], b[1], INK, max(0.8, w * s)))
    # procedure
    LB = 'Runway bearings'
    draw(LB, polyline([V(p) for p in P.hold_pts], 0.4))
    draw(LB, polyline([V(p) for p in P.out_pts], 0.55))
    if not heli:
        draw(LB, polyline([V(P.IF), V(P.FAF)], 0.55))
        draw(LB, polyline([V(P.FAF), V(e.thr)], 0.85))
    else:
        draw(LB, polyline([V(P.arc[-1]), V(e.thr)], 0.85))
    mis = [e.thr, ref.pos] if P.mis_direct else P.mis_pts
    draw(LB, polyline([V(p) for p in mis], 0.4, 'stroke-dasharray="1.4 0.8"'))
    # fleches
    if P.mode == 'base':
        hm = ((P.hold_ob[0] + P.hold_hb[0]) / 2, (P.hold_ob[1] + P.hold_hb[1]) / 2)
        draw(LB, arrow(*V(hm), P.hold_inb_az + 180, 1.6))
        hi = ((P.hold_ha[0] + ref.pos[0]) / 2, (P.hold_ha[1] + ref.pos[1]) / 2)
        draw(LB, arrow(*V(hi), P.hold_inb_az, 1.6))
        draw(LB, arrow(*V((ref.pos[0] * 0.3 + P.T[0] * 0.7, ref.pos[1] * 0.3 + P.T[1] * 0.7)), P.out_az, 2.0))
    else:
        o0 = P.a1[-1]
        draw(LB, arrow(*V(((o0[0] + P.T[0]) / 2, (o0[1] + P.T[1]) / 2)), P.out_az, 2.0))
    fin_len = P.faf_nm
    draw(LB, arrow(*V(procedure.axis_point(e.thr, e.az, fin_len * 0.35)), e.az, 2.2))
    # graduations NM le long de l'axe, du seuil au IF (avion)
    if not heli:
        uX, uY = math.sin(math.radians(e.az)), -math.cos(math.radians(e.az))
        for dn in range(1, int(P.if_nm) + 1):
            X, Y = V(procedure.axis_point(e.thr, e.az, dn))
            draw('Runway markings', line(X - uY * 0.9, Y + uX * 0.9, X + uY * 0.9, Y - uX * 0.9, INK, 0.3))
    fX, fY = V(P.FAF)
    doc.add(LB, malte(fX, fY))
    pl.block(malte_geom(fX, fY))
    # balises
    shown = [ref] if heli else [ref] + ([e.ndb] if e.ndb is not None else []) + [b for b in site.beacons if b is not ref and b is not e.ndb]
    fr = box(fx + 1.5, fy + 1.5, fx + fw - 1.5, fy + fh - 1.5)
    shown = [b for b in shown if fr.contains(Point(*V(b.pos)))]
    for b in shown:
        for el in compose.navaid_symbol_elems(b.kind, *V(b.pos)):
            draw('Navaids', el)
    # points cotes
    spots = spot_heights(G, i_hi, j_lo, site.x0, site.z0, view, frame, reserved, e.thr, e.az, SPOT_NEAR_NM[P.famille])
    for X, Y, h in spots:
        draw('Towns', f'<polygon points="{f(X)},{f(Y - 0.9)} {f(X - 0.8)},{f(Y + 0.5)} {f(X + 0.8)},{f(Y + 0.5)}" fill="{INK}"/>')

    # ---- etiquettes (par regle)
    side = 1.0 if P.turn == 'LEFT' else -1.0                    # le cote du virage, a gauche (+1) ou a droite du sens d'atterrissage
    rg = procedure.right_of(e.az)
    away = (-side * -rg[0], -side * -rg[1])                     # cote oppose au virage (repere du jeu)
    awayX, awayY = away[1], -away[0]                           # sur la page
    # cap de la finale, le long de l'axe
    rot = page.cap_norm(e.az - 90.0)
    fin_pts = [V(procedure.axis_point(e.thr, e.az, P.faf_nm * t)) for t in (0.6, 0.45, 0.75, 0.3, 0.85)]
    hh = pt(7.2) * CAP_HEIGHT
    offs = [(awayX * (hh / 2 + 1.2 + k * 0.8), awayY * (hh / 2 + 1.2 + k * 0.8)) for k in range(4)] + \
           [(-awayX * (hh / 2 + 1.2 + k * 0.8), -awayY * (hh / 2 + 1.2 + k * 0.8)) for k in range(4)]
    doc.add(LB, pl.place_rotated('cap', f'{P.crs:03d}°', pt(7.2), 'bold', fin_pts, rot, offs))
    q = lambda name, lines, p, pref, **kw: [doc.add(LB, el) for el in pl.place(name, lines, *p, pref, **kw)]
    B, R_ = 'bold', 'regular'
    if heli:
        zone, hgt = P.zone_ft, procedure.qfe_m(P.zone_ft, site.elev_ft)
        q('FAF', [('FAF', pt(6.0), B, INK), (f'{zone} FT', pt(6.0), B, INK), (f'QFE {hgt} M', pt(5.0), B, INK2),
                  (f'QDM {cs} {P.qdm_faf:03d}°', pt(5.0), B, INK)], (fX, fY), (awayX, awayY), d0=1.8)
        q('MAPt', [('MAPt', pt(5.6), B, INK), (f'THR {e.sens}', pt(5.0), R_, INK2), (f'QDM {cs} {P.qdm_thr:03d}°', pt(5.0), B, INK)],
          V(e.thr), (awayX, awayY))
    else:
        if ref.dme:
            l2 = f'FAF D{P.faf_dme:.1f}'
        else:
            l2 = f'FAF {P.faf_nm:.1f} NM'
        lines = [(l2, pt(6.0), B, INK), (f'{P.int_ft}', pt(6.0), B, INK)]
        if P.rad_faf is not None:
            lines.append((f'R-{P.rad_faf:03d}', pt(5.0), R_, INK2))
        if P.qdm_faf is not None:
            lines.append((f'NDB: QDM {P.qdm_faf:03d}°' if e.ndb is not None else f'QDM {P.qdm_faf:03d}°', pt(5.0), R_, INK2))
        q('FAF', lines, (fX, fY), (awayX, awayY), d0=1.8)
        iX, iY = V(P.IF)
        q('IF', [(f'IF D{P.if_dme:.1f}' if ref.dme else f'IF {P.if_nm:.1f} NM', pt(5.6), B, INK), (f'{P.init_ft}', pt(5.6), B, INK)],
          (iX, iY), (awayX, awayY))
        lines = [('MAPt', pt(5.6), B, INK), (f'THR {e.sens}' + (f' · D{P.thr_dme:.1f}' if ref.dme else ''), pt(5.0), R_, INK2)]
        if P.rad_thr is not None:
            lines.append((f'R-{P.rad_thr:03d}', pt(5.0), R_, INK2))
        if P.qdm_thr is not None:
            lines.append((f'NDB: QDM {P.qdm_thr:03d}°' if e.ndb is not None else f'QDM {P.qdm_thr:03d}°', pt(5.0), R_, INK2))
        q('MAPt', lines, V(e.thr), (awayX, awayY))
    # attente
    hc = procedure.centroid(P.hold_pts)
    if P.mode == 'base':
        legs = [((P.hold_ha[0] + ref.pos[0]) / 2, (P.hold_ha[1] + ref.pos[1]) / 2),
                ((P.hold_ob[0] + P.hold_hb[0]) / 2, (P.hold_ob[1] + P.hold_hb[1]) / 2)]
        hX, hY = V(hc)
        leg = max(legs, key=lambda m: math.dist(V(m), V(P.T)))     # la branche la plus loin de l'eloignement
        lX, lY = V(leg)
        if heli:
            lines = [(f'HOLD {P.hold_ft} FT · QFE {procedure.qfe_m(P.hold_ft, site.elev_ft)} M', pt(6.2), B, INK),
                     (f'INBOUND {P.qdr_out:03d}° · {P.hold_turns}', pt(5.2), R_, INK2), (f'1 MIN · {spd}', pt(5.2), R_, INK2)]
        else:
            lines = [(f'HOLD {P.hold_ft}', pt(6.2), B, INK), (f'INBOUND {P.qdr_out:03d}° · {P.hold_turns}', pt(5.2), R_, INK2),
                     ('1 MIN · MAX IAS 230 KT', pt(5.2), R_, INK2)]
        q('HOLD', lines, (lX, lY), (lX - hX, lY - hY), d0=1.6)
        # eloignement
        mo = (ref.pos[0] * 0.45 + P.T[0] * 0.55, ref.pos[1] * 0.45 + P.T[1] * 0.55)
        mX, mY = V(mo)
        cX, cY = V(P.C)
        n_ = procedure.right_of(P.out_az)
        nX, nY = n_[1], -n_[0]
        if nX * (mX - cX) + nY * (mY - cY) < 0:
            nX, nY = -nX, -nY
        if heli:
            lines = [(f'{P.qdr_out:03d}°', pt(6.6), B, INK), (f'{procedure.mmss(P.t_bt)} AT {spd}', pt(5.2), R_, INK2)]
        else:
            lim = (f'{P.init_ft} · LIMIT D{P.out_dme:.1f}' if ref.dme else f'{P.init_ft}')
            lines = [(_out_label(P), pt(6.6), B, INK), (lim, pt(5.2), R_, INK2),
                     (f'CAT A-B {procedure.mmss(P.t_ab)} · C-D {procedure.mmss(P.t_cd)}', pt(5.2), R_, INK2)]
        q('OUT', lines, (mX, mY), (nX, nY))
        bm = P.arc[len(P.arc) // 2]
        bX, bY = V(bm)
        q('TURN', [(f'BASE TURN ({P.turn})' + ('' if heli else ' · MAX IAS 185 KT'), pt(5.2), R_, INK2)], (bX, bY), (bX - cX, bY - cY))
    else:
        o0 = P.a1[-1]
        mo = (o0[0] * 0.4 + P.T[0] * 0.6, o0[1] * 0.4 + P.T[1] * 0.6)
        mX, mY = V(mo)
        iX, iY = V(P.IF)
        lim = (f'{P.init_ft} · LIMIT D{P.out_dme:.1f}' if ref.dme else f'{P.init_ft}')
        lines = [(f'{P.qdr_out:03d}°', pt(6.6), B, INK), (lim, pt(5.2), R_, INK2),
                 (f'CAT A-B {procedure.mmss(P.t_ab)} · C-D {procedure.mmss(P.t_cd)}', pt(5.2), R_, INK2),
                 (f'HOLD {P.hold_ft}: SAME PATTERN, 1 MIN', pt(5.2), B, INK), (f'{P.hold_turns} · MAX IAS 230 KT', pt(5.2), R_, INK2)]
        q('OUT', lines, (mX, mY), (mX - iX, mY - iY))
        bm = P.a2[len(P.a2) // 2]
        bX, bY = V(bm)
        c2 = ((P.a2[0][0] + P.a2[-1][0]) / 2, (P.a2[0][1] + P.a2[-1][1]) / 2)
        cX, cY = V(c2)
        q('TURN', [(f'RACETRACK ({P.turn}) · MAX IAS 185 KT', pt(5.2), R_, INK2)], (bX, bY), (bX - cX, bY - cY))
    # libelles de balises : groupes de symboles a 6 mm au plus
    pos = [V(b.pos) for b in shown]
    for grp in page.navaid_groups(pos):
        gb = [shown[k] for k in grp]
        if len(gb) == 1:
            b = gb[0]
            l1, l2 = f'{b.kind} {b.callsign}', b.freq + (f' {b.channel}' if b.channel else '')
        else:
            calls = {x.callsign for x in gb}
            l1 = (' · '.join(x.kind for x in gb) + f' {gb[0].callsign}') if len(calls) == 1 else ' · '.join(f'{x.kind} {x.callsign}' for x in gb)
            l2 = ' · '.join(x.freq + (f' {x.channel}' if x.channel else '') for x in gb)
        xs, ys = [pos[k][0] for k in grp], [pos[k][1] for k in grp]
        cxy = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        ext = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 1.5
        lines = [(l1, pt(6.0), B, INK), (l2, pt(5.2), R_, INK2)]
        for el in pl.place('navaid ' + l1, lines, *cxy, (1.0, 0.0), d0=ext + 0.8):
            doc.add('Navaids', el)
    for X, Y, h in spots:
        for el in pl.place(f'spot {h}', [(f'{h}', pt(5.6), B, INK)], X, Y, (1.0, 0.0), d0=1.0):
            doc.add('Towns', el)

    # ---- boite MSA, echelle, moyens requis, nord
    I = 'Insets'
    mx, my, mr = bx_ + MSA_W / 2, by_ + 17.6, 8.0
    doc.add(I, rect(bx_, by_, MSA_W, MSA_H, '#FFFFFF', INK, 0.3, 'fill-opacity="0.92"'))
    doc.add(I, circle(mx, my, mr, 'none', INK, 0.3))
    for b_mag in (0, 90, 180, 270):
        g = b_mag + e.decl
        dX, dY = math.sin(math.radians(g)), -math.cos(math.radians(g))
        doc.add(I, line(mx, my, mx - dX * mr, my - dY * mr, INK, 0.3))
        doc.add(I, text(mx - dX * (mr + 2.4), my - dY * (mr + 2.4) + 0.8, f'{b_mag or 360:03d}°', pt(4.4), 'regular', INK2, 'middle'))
    for sct in P.msa['sectors']:
        mid = (sct['from'] + sct['to']) / 2 + e.decl
        dX, dY = math.sin(math.radians(mid)), -math.cos(math.radians(mid))
        cx_, cy_ = mx - dX * mr * 0.55, my - dY * mr * 0.55 + pt(6.0) * CAP_HEIGHT / 2
        if heli:
            doc.add(I, text(cx_, cy_ - 0.8, str(sct['msa_ft']), pt(5.6), 'bold', INK, 'middle'))
            doc.add(I, text(cx_, cy_ + 1.5, f"{procedure.m_up10(sct['msa_ft'])} M", pt(4.2), 'regular', INK2, 'middle'))
        else:
            doc.add(I, text(cx_, cy_, str(sct['msa_ft']), pt(6.0), 'bold', INK, 'middle'))
    msa_title = f'MSA 25 NM ({int(procedure.km(25))} KM) {cs}' if heli else f'MSA 25 NM {cs}'
    doc.add(I, text(mx, by_ + 3.6, msa_title, pt(4.8 if heli else 5.2), 'bold', INK, 'middle'))
    FG = 'Frame & grid'
    sb = scale_box.bounds
    doc.add(FG, rect(sb[0], sb[1], sb[2] - sb[0], sb[3] - sb[1], '#FFFFFF', None, 0, 'fill-opacity="0.9"'))
    bar_y = fy + fh - 3.0
    bx0 = fx + 4.0
    nb = 5 if s * NM * 5 < 22 else 2 if s * NM * 2 < 22 else 1
    doc.add(FG, line(bx0, bar_y, bx0 + nb * NM * s, bar_y, INK, 0.35))
    for k in range(nb + 1):
        doc.add(FG, line(bx0 + k * NM * s, bar_y - 1.2, bx0 + k * NM * s, bar_y + 1.2, INK, 0.3))
    doc.add(FG, text(bx0 + nb * NM * s + 1.5, bar_y + 0.9, f'{nb} NM', pt(5.6), 'regular', INK2))
    if heli:
        kb = bar_y - 4.4
        nk = 4 if s * 4000 < 22 else 2 if s * 2000 < 22 else 1
        doc.add(FG, line(bx0, kb, bx0 + nk * 1000 * s, kb, INK, 0.35))
        for k in range(nk + 1):
            doc.add(FG, line(bx0 + k * 1000 * s, kb - (1.2 if k % 2 == 0 else 0.7), bx0 + k * 1000 * s, kb, INK, 0.3))
        doc.add(FG, text(bx0 + nk * 1000 * s + 1.5, kb + 0.9, f'{nk} KM', pt(5.6), 'regular', INK2))
    doc.add(FG, with_halo(north_el))
    doc.add(FG, with_halo(req_el))
    doc.add(FG, rect(fx, fy, fw, fh, 'none', INK, 0.35))

    # ---- profil, tableau, interrompue, legende
    prof_failed = (_profile_helico if heli else _profile_avion)(doc, site, P, frame, log)
    L = 'Footer'
    doc.add(L, line(X0, page.FOOTER_RULE_Y, X1, page.FOOTER_RULE_Y, INK, 0.5))
    doc.add(L, text(X0, page.FOOTER_Y, FOOTER_TEXT, pt(6.1), 'regular', INK2))
    doc.add(L, text(X1, page.FOOTER_Y, page.FOOTER_RIGHT, pt(6.1), 'bold', INK, 'end'))
    svg = doc.render()
    failed = list(pl.failed) + list(prof_failed)
    return PageOut(index, svg, {'title': title, 'scale_mm_per_nm': s * NM, 'failed': failed, 'frame': frame})


def _profile_place(doc, pl, layer, s, size, weight, fill, x, y, anchor, alts):
    """Une etiquette du profil : a sa place (x, y, ancre) ou a l'une des places de repli `alts` [(dx, dy)] ; elle evite les
    traits du profil et les autres etiquettes."""
    w, h = text_width(s, weight, size), size * CAP_HEIGHT
    first = None
    for dx, dy in [(0.0, 0.0)] + list(alts):
        x0 = x + dx - (0 if anchor == 'start' else w / 2 if anchor == 'middle' else w)
        g = box(x0, y + dy - h, x0 + w, y + dy)
        if first is None:
            first = (dx, dy, g)
        if pl.free(g):
            pl.labels.append(g)
            doc.add(layer, text(x + dx, y + dy, s, size, weight, fill, anchor))
            return True
    dx, dy, g = first
    pl.labels.append(g)
    pl.failed.append(f'profil {s}')
    doc.add(layer, text(x + dx, y + dy, s, size, weight, fill, anchor))
    return False


ALTS = sorted({(dx, dy) for dx in (0, -3, 3, -6, 6, -9, 9, -12, 12, -16, 16, -20, 20) for dy in (0, -1.5, 1.5, -3, 3, -4.5, 4.5, -6, 6)} - {(0, 0)},
              key=lambda o: (o[0] ** 2 + (2 * o[1]) ** 2, o))          # places de repli d'une etiquette du profil, de la plus proche a la plus loin


def table_columns(faf_nm, max_cols=8):
    """Colonnes du tableau (NM du seuil) : le FAF, puis une colonne par NM entier jusqu'a 1 NM inclus ; au-dela de `max_cols`
    colonnes, une par 2 NM, en gardant toujours le FAF et 1 NM (addendum 2, 2.5)."""
    ints = [d for d in range(int(math.floor(faf_nm)), 0, -1) if d < faf_nm - 1e-6]
    if 1 + len(ints) > max_cols:
        ints = [d for d in ints if d % 2 == 1]
    return [faf_nm] + ints


def _profile_avion(doc, site, P, frame, log):
    e = P.e
    ref = e.ref
    cs = ref.callsign
    R = 'Runway table'
    fx, fy, fw, fh = frame
    PT_, PB = fy + fh + 2.4, fy + fh + 39.0
    pl = Placer((X0, PT_, X1 - X0, PB - PT_), margin=0.6)
    doc.add('Frame & grid', rect(X0, PT_, X1 - X0, PB - PT_, 'none', INK, 0.35))
    doc.add(R, text(X0 + 2.0, PT_ + 3.6, 'PROFILE', pt(6.4), 'bold'))
    proc = 'base turn' if P.mode == 'base' else 'racetrack'
    lim = (f' or D{P.out_dme:.1f} {cs}' if ref.dme else '')
    l1 = (f'Hold {P.hold_ft}; outbound {_out_label(P)} descending to {P.init_ft}, CAT A-B {procedure.mmss(P.t_ab)} · '
          f'CAT C-D {procedure.mmss(P.t_cd)}{lim}; {P.turn.lower()} {proc} to the runway axis')
    vs = ' · '.join(f'{g} KT {int(round(procedure.vs_ftmin(g, 3.0) / 10.0)) * 10}' for g in (100, 120, 140, 160))
    if e.ndb is not None:
        l2 = f'NDB (no DME): FAF when established inbound, QDM {P.qdm_faf:03d}°; 3.0° = VS FT/MIN {vs}'
    elif not ref.dme:
        tm = ' · '.join(f'{g} KT {procedure.mmss(P.faf_nm / g * 60)}' for g in (100, 120, 140, 160))
        l2 = f'No DME: FAF when established inbound; FAF to MAPt {P.faf_nm:.1f} NM: {tm}; 3.0° = VS FT/MIN {vs}'
    else:
        l2 = f'3.0° = VS FT/MIN {vs}'
    for y_, s_ in ((PT_ + 7.4, l1), (PT_ + 10.9, l2)):
        doc.add(R, text(X0 + 2.0, y_, s_, pt(5.4), 'regular', INK2))
        pl.block(page.elem_geom(text(X0 + 2.0, y_, s_, pt(5.4), 'regular', INK2)))
    pl.block(box(X0, PT_, X0 + 20, PT_ + 4))
    ay = PB - 6.4
    rw_nm = math.dist(e.thr, e.end) / NM
    ext = max(P.v_par_nm, rw_nm, 0.0) + 0.7
    pitch = (X1 - X0 - 34.0) / (P.if_nm + 0.6 + ext)
    x_thr = X1 - 3.0 - ext * pitch
    xd = lambda d: x_thr - d * pitch
    lo, hi = e.thr_ft, max(P.hold_ft, P.mis_ft) + 300
    ky = (ay - (PT_ + 15.0)) / (hi - lo)
    ya = lambda a: ay - (a - lo) * ky

    def ln(x1, y1, x2, y2, c, sw, extra=''):
        el = line(x1, y1, x2, y2, c, sw, extra)
        doc.add(R, el)
        pl.block(page.elem_geom(el))
    ln(xd(P.if_nm + 0.6), ay, X1 - 2.0, ay, INK2, 0.35)
    ln(x_thr, ay, x_thr + rw_nm * pitch, ay, INK, 1.2)
    for dn in range(int(P.if_nm), 0, -1):
        ln(xd(dn), ay - 1.1, xd(dn), ay + 1.1, INK2, 0.3)
        _profile_place(doc, pl, R, str(dn), pt(5.4), 'regular', INK2, xd(dn), ay + 3.1, 'middle', [])
    _profile_place(doc, pl, R, 'NM', pt(5.4), 'regular', INK2, xd(P.if_nm + 0.6) - 1.0, ay + 3.1, 'end', [(-2, 0)])
    _profile_place(doc, pl, R, f'THR {e.sens} {e.thr_ft}', pt(5.0), 'regular', INK2, x_thr, ay + 5.6, 'middle', [(4, 0), (8, 0), (-4, 0)])
    d_v = -P.v_par_nm
    ln(xd(d_v), ay, xd(d_v), PT_ + 15.5, INK2, 0.25, 'stroke-dasharray="0.6 0.6"')
    _profile_place(doc, pl, R, cs, pt(6.0), 'bold', INK, xd(d_v), PT_ + 14.6, 'middle', [(3, 0), (-3, 0), (5, 0), (-5, 0)])
    ln(xd(d_v), ya(P.hold_ft), xd(P.if_nm), ya(P.init_ft), INK2, 0.35)
    ln(xd(P.if_nm), ya(P.init_ft), xd(P.faf_nm), ya(P.int_ft), INK, 0.6)
    ln(xd(P.faf_nm), ya(P.int_ft), x_thr, ya(e.thr_ft + P.tch), INK, 0.8)
    doc.add(R, malte(xd(P.faf_nm), ya(P.int_ft), 1.1))
    pl.block(malte_geom(xd(P.faf_nm), ya(P.int_ft), 1.1))
    d_mis = d_v if P.mis_direct else -procedure.MIS_STRAIGHT_NM
    y_t = ya(e.thr_ft + P.tch)
    ln(x_thr, y_t, xd(d_mis), ya(e.thr_ft + P.tch + (P.mis_ft - e.thr_ft - P.tch) * 0.75), INK, 0.4, 'stroke-dasharray="1.4 0.8"')
    side_hold = 'end' if d_v >= P.if_nm / 2 else 'start'
    _profile_place(doc, pl, R, f'HOLD {P.hold_ft}', pt(5.6), 'bold', INK, xd(d_v) + (-1.2 if side_hold == 'end' else 1.2),
                   ya(P.hold_ft) + 3.2, side_hold, ALTS)
    mid_x = (xd(d_v) + xd(P.if_nm)) / 2
    _profile_place(doc, pl, R, f'{_out_label(P)} outbound, {"base turn" if P.mode == "base" else "racetrack"}', pt(5.0), 'regular', INK2,
                   mid_x, (ya(P.hold_ft) + ya(P.init_ft)) / 2 - 1.4, 'middle', ALTS)
    _profile_place(doc, pl, R, (f'IF D{P.if_dme:.1f} {P.init_ft}' if ref.dme else f'IF {P.init_ft}'), pt(5.6), 'bold', INK,
                   xd(P.if_nm) - 1.5, ya(P.init_ft) + 1.0, 'end', ALTS)
    _profile_place(doc, pl, R, (f'FAF D{P.faf_dme:.1f} {P.int_ft}' if ref.dme else f'FAF {P.int_ft}'), pt(5.6), 'bold', INK,
                   xd(P.faf_nm) + 1.5, ya(P.int_ft) + 3.4, 'start', ALTS)
    gx = P.faf_nm * 0.5
    _profile_place(doc, pl, R, 'GP 3.0°' if P.ils else '3.0°', pt(5.8), 'bold', INK, xd(gx), ya(P.gp_alt(gx)) - 2.0, 'middle', ALTS)
    _profile_place(doc, pl, R, 'MAPt', pt(5.4), 'bold', INK, x_thr - 2.6, ya(e.thr_ft + P.tch) - 2.4, 'end', ALTS)
    # tableau : distance, altitude, DME
    TY = PB + 2.2
    doc.add(R, rect(X0, TY, X1 - X0, 4.6, BAND))
    cols = table_columns(P.faf_nm)
    doc.add(R, text(8.0, TY + 3.2, 'NM FROM THR', pt(5.4), 'regular', INK2))
    cx0 = 34.0
    cp = (X1 - 6.0 - cx0) / max(1, len(cols) - 1)
    for n_, d in enumerate(cols):
        x = cx0 + n_ * cp
        doc.add(R, text(x, TY + 3.2, f'{d:.1f} FAF' if n_ == 0 else str(int(d)), pt(7.0), 'bold', INK, 'middle'))
        a_ft = P.int_ft if n_ == 0 else int(math.ceil(round(P.gp_alt(d) / 10.0, 9))) * 10      # a la dizaine superieure
        doc.add(R, text(x, TY + 8.8, str(a_ft), pt(7.2), 'regular', INK, 'middle'))
        if ref.dme:
            doc.add(R, text(x, TY + 13.8, f'{P.dme(procedure.axis_point(e.thr, e.az, d)):.1f}', pt(7.2), 'regular', INK, 'middle'))
    doc.add(R, text(8.0, TY + 8.8, 'ALTITUDE FT', pt(5.4), 'regular', INK2))
    if ref.dme:
        doc.add(R, text(8.0, TY + 13.8, f'{"DME" if ref.kind == "VOR/DME" else ref.kind} {cs}', pt(5.4), 'regular', INK2))
    rule_y = TY + (15.8 if ref.dme else 10.8)
    doc.add(R, line(X0, rule_y, X1, rule_y, INK, 0.35))
    y = rule_y + 4.0
    doc.add(R, text(X0, y, 'MISSED APPROACH', pt(6.0), 'bold'))
    if P.mis_direct:
        mtxt = f'Climb on {P.crs:03d}° to {cs}, climbing to {P.hold_ft} FT, and join the holding.'
    else:
        mtxt = (f'Climb on {P.crs:03d}° to {P.mis_ft} FT, turn {"right" if P.mis_right else "left"} direct to {cs}, '
                f'climbing to {P.hold_ft} FT, and join the holding.')
    doc.add(R, text(X0 + 22.0, y, mtxt, pt(5.6), 'regular', INK))
    doc.add(R, rect(X0, y + 1.8, 58.0, 5.6, 'none', INK, 0.35))
    doc.add(R, text(X0 + 29.0, y + 5.6, 'NO MINIMA — NOT PUBLISHED BY DCS', pt(6.0), 'bold', INK, 'middle'))
    var, yl = geo.var_text(e.decl, site.year)
    doc.add(R, text(X1, y + 4.0, f'BRGS MAG · ALT, ELEV FT · DIST NM · {var} {yl}', pt(5.2), 'regular', INK2, 'end'))
    doc.add(R, text(X1, y + 6.8, 'Altitudes: highest ground + 1000 FT (2000 FT from 1500 M), ground read in DCS', pt(5.2), 'regular', INK2, 'end'))
    note = 'Not a real approach chart: DCS gives no procedure and no minima. Final on the runway axis; tracks offset from the runways.'
    if e.ndb is not None:
        note += f' NDB users: same tracks on NDB {e.ndb.callsign}, MAPt at THR timed.'
    doc.add(R, text(X0, y + 10.2, note, pt(4.9), 'regular', INK2))
    return pl.failed


def _profile_helico(doc, site, P, frame, log):
    e = P.e
    ref = e.ref
    cs = ref.callsign
    R = 'Runway table'
    fx, fy, fw, fh = frame
    PT_, PB = fy + fh + 2.4, fy + fh + 33.0
    pl = Placer((X0, PT_, X1 - X0, PB - PT_), margin=0.6)
    doc.add('Frame & grid', rect(X0, PT_, X1 - X0, PB - PT_, 'none', INK, 0.35))
    doc.add(R, text(X0 + 2.0, PT_ + 3.6, 'PROFILE', pt(6.4), 'bold'))
    pl.block(box(X0, PT_, X0 + 20, PT_ + 4))
    zone, hgt = P.zone_ft, procedure.qfe_m(P.zone_ft, site.elev_ft)
    spd = f'{int(procedure.HELI_KT)} KT · {procedure.HELI_KMH} KM/H'
    l1 = (f'Hold, outbound and {P.turn.lower()} base turn level at {zone} FT, QFE {hgt} M; outbound from {cs}: QDR {P.qdr_out:03d}° '
          f'{procedure.mmss(P.t_bt)} at {spd}')
    ax_m = abs(P.v_lat_m)
    if ax_m > procedure.AXIS_TOL_NM * NM:
        l2 = (f'Final on the runway axis {P.crs:03d}°; NDB {cs} is {int(round(ax_m, -1))} M {"E" if _east(e) else "W"} of the axis: '
              f'check QDM {cs} {P.qdm_faf:03d}° at FAF, {P.qdm_thr:03d}° at THR')
    else:
        l2 = f'Final on the runway axis {P.crs:03d}°; QDM {cs} {P.qdm_faf:03d}° at FAF, {P.qdm_thr:03d}° at THR'
    for y_, s_ in ((PT_ + 7.4, l1), (PT_ + 10.6, l2)):
        doc.add(R, text(X0 + 2.0, y_, s_, pt(5.4), 'regular', INK2))
        pl.block(page.elem_geom(text(X0 + 2.0, y_, s_, pt(5.4), 'regular', INK2)))
    ay = PB - 6.4
    d_end = P.faf_nm
    rw_nm = math.dist(e.thr, e.end) / NM
    d_ndb = -P.v_par_nm
    ext = max(P.v_par_nm, rw_nm) + 0.7
    pitch = (X1 - X0 - 18.0) / (d_end + 0.6 + max(ext, 0.7) + max(0.0, d_ndb - d_end))
    x_thr = X1 - 3.0 - max(ext, 0.7) * pitch
    xd = lambda d: x_thr - d * pitch
    lo, hi = e.thr_ft, zone + 900
    ky = (ay - (PT_ + 13.0)) / (hi - lo)
    ya = lambda a: ay - (a - lo) * ky

    def ln(x1, y1, x2, y2, c, sw, extra=''):
        el = line(x1, y1, x2, y2, c, sw, extra)
        doc.add(R, el)
        pl.block(page.elem_geom(el))
    ln(xd(max(d_end, d_ndb) + 0.6), ay, X1 - 2.0, ay, INK2, 0.35)
    ln(x_thr, ay, x_thr + rw_nm * pitch, ay, INK, 1.2)
    for dn in range(int(d_end), 0, -1):
        ln(xd(dn), ay - 1.1, xd(dn), ay + 1.1, INK2, 0.3)
        _profile_place(doc, pl, R, str(dn), pt(5.4), 'regular', INK2, xd(dn), ay + 3.1, 'middle', [])
    _profile_place(doc, pl, R, 'NM', pt(5.4), 'regular', INK2, xd(max(d_end, d_ndb) + 0.6) - 1.0, ay + 3.1, 'end', [(-2, 0)])
    for dk in range(1, int(d_end * NM / 1000.0) + 1):
        ln(xd(dk * 1000.0 / NM), ay - 0.7, xd(dk * 1000.0 / NM), ay, INK2, 0.25)
        _profile_place(doc, pl, R, str(dk), pt(4.6), 'regular', INK2, xd(dk * 1000.0 / NM), ay - 1.4, 'middle', [])
    _profile_place(doc, pl, R, 'KM', pt(4.6), 'regular', INK2, xd(max(d_end, d_ndb) + 0.6) - 1.0, ay - 1.4, 'end', [(-2, 0)])
    _profile_place(doc, pl, R, f'THR {e.sens} {e.thr_ft} FT · {int(round(e.thr_ft / FT))} M', pt(5.0), 'regular', INK2, x_thr, ay + 5.6,
                   'middle', [(4, 0), (8, 0), (-4, 0), (12, 0)])
    ln(xd(d_ndb), ay, xd(d_ndb), PT_ + 5.0, INK2, 0.25, 'stroke-dasharray="0.6 0.6"')
    _profile_place(doc, pl, R, cs, pt(6.0), 'bold', INK, xd(d_ndb), PT_ + 4.2, 'middle', [(3, 0), (-3, 0), (5, 0), (-5, 0)])
    YL = ya(zone)
    ln(min(xd(d_ndb), xd(d_end)), YL, max(xd(d_ndb), xd(d_end)), YL, INK2, 0.35)
    ln(xd(d_end), ya(zone), x_thr, ya(e.thr_ft + P.tch), INK, 0.8)
    doc.add(R, malte(xd(d_end), ya(zone), 1.1))
    pl.block(malte_geom(xd(d_end), ya(zone), 1.1))
    d_mis = d_ndb if P.mis_direct else -procedure.HELI_MIS_STRAIGHT_NM
    ln(x_thr, ya(e.thr_ft + P.tch), xd(d_mis), ya(P.mis_ft), INK, 0.4, 'stroke-dasharray="1.4 0.8"')
    _profile_place(doc, pl, R, f'{zone} FT · QFE {hgt} M', pt(5.6), 'bold', INK, xd(d_ndb) - 1.0, YL - 1.0, 'end', ALTS)
    _profile_place(doc, pl, R, f'hold · QDR {P.qdr_out:03d} outbound · {P.turn.lower()} base turn', pt(5.0), 'regular', INK2,
                   (xd(d_ndb) + xd(d_end)) / 2, YL - 1.2, 'middle', ALTS)
    _profile_place(doc, pl, R, f'FAF {zone}', pt(6.0), 'bold', INK, xd(d_end) - 1.8, ya(zone) + 0.2, 'end', ALTS)
    _profile_place(doc, pl, R, f'QFE {hgt} M', pt(4.8), 'regular', INK2, xd(d_end) - 1.8, ya(zone) + 2.6, 'end', ALTS)
    _profile_place(doc, pl, R, f'{P.slope:.1f}°', pt(5.8), 'bold', INK, xd(d_end * 0.45), ya(P.gp_alt(d_end * 0.45)) - 2.0, 'middle', ALTS)
    _profile_place(doc, pl, R, 'MAPt', pt(5.4), 'bold', INK, x_thr - 1.0, ya(e.thr_ft + P.tch) - 1.4, 'end', ALTS)
    _profile_place(doc, pl, R, 'missed', pt(5.0), 'regular', INK2, xd(d_mis) - 1.0, ya(P.mis_ft) + 2.6, 'end', ALTS)
    # tableau : la finale sans DME, au chrono et au variometre
    TY = PB + 2.2
    gss = [70, 80, 90, 100, 110]
    doc.add(R, rect(X0, TY, X1 - X0, 7.4, BAND))
    doc.add(R, text(8.0, TY + 3.2, 'GS KT', pt(5.4), 'regular', INK2))
    cx0 = 44.0
    cp = (X1 - 8.0 - cx0) / (len(gss) - 1)
    for n_, g in enumerate(gss):
        x = cx0 + n_ * cp
        doc.add(R, text(x, TY + 3.2, str(g), pt(7.0), 'bold', INK, 'middle'))
        doc.add(R, text(x, TY + 6.2, f'{procedure.kmh_label(g)} KM/H', pt(5.0), 'regular', INK2, 'middle'))
        doc.add(R, text(x, TY + 11.6, procedure.mmss(d_end / g * 60), pt(7.2), 'regular', INK, 'middle'))
        v = procedure.vs_ftmin(g, P.slope)
        doc.add(R, text(x, TY + 16.6, f'{int(round(v / 10.0)) * 10} · {procedure.ftmin_to_ms(v):.1f}', pt(7.2), 'regular', INK, 'middle'))
    doc.add(R, text(8.0, TY + 11.6, f'FAF TO MAPt {d_end:.1f} NM · {procedure.km(d_end):.1f} KM', pt(5.4), 'regular', INK2))
    doc.add(R, text(8.0, TY + 16.6, f'VS FT/MIN · M/S ({P.slope:.1f}°)', pt(5.4), 'regular', INK2))
    doc.add(R, line(X0, TY + 18.6, X1, TY + 18.6, INK, 0.35))
    y = TY + 22.4
    doc.add(R, text(X0, y, 'MISSED APPROACH', pt(6.0), 'bold'))
    if P.mis_direct:
        mtxt = f'Climb straight ahead on {P.crs:03d}°, then direct to NDB {cs} at {P.mis_ft} FT (QFE {hgt} M) and join the holding.'
    else:
        mtxt = (f'Climb straight ahead on {P.crs:03d}° to {P.mis_ft} FT (QFE {hgt} M), then turn {"right" if P.mis_right else "left"} '
                f'direct to NDB {cs} and join the holding.')
    doc.add(R, text(X0 + 22.0, y, mtxt, pt(5.6), 'regular', INK))
    doc.add(R, rect(X0, y + 1.8, 58.0, 5.6, 'none', INK, 0.35))
    doc.add(R, text(X0 + 29.0, y + 5.6, 'NO MINIMA — NOT PUBLISHED BY DCS', pt(6.0), 'bold', INK, 'middle'))
    var, yl = geo.var_text(e.decl, site.year)
    doc.add(R, text(X1, y + 4.0, f'BRGS MAG · ALT FT (QNH) · QFE M (HEIGHT ABOVE AD ELEV) · MSA FT AND M · DIST NM AND KM · {var} {yl}',
                    pt(5.2), 'regular', INK2, 'end'))
    doc.add(R, text(X1, y + 6.8, f'At MAPt: land on RWY {e.sens}, or proceed visually to a helipad (10-9).', pt(5.2), 'regular', INK2, 'end'))
    doc.add(R, text(X0, y + 10.2, 'Not a real approach chart: DCS gives no procedure and no minima. Helicopters, NDB only (no DME, no VOR). '
                    'One altitude for the whole procedure: highest ground + 1000 FT, ground read in DCS.', pt(4.9), 'regular', INK2))
    return pl.failed


def _east(e):
    """Le NDB est-il a l'est de l'axe (composante z de son ecart lateral) ?"""
    al, cr = procedure.along_cross(e.ref.pos, e.thr, e.az)
    u = procedure.unit(e.az)
    lat = (e.ref.pos[0] - e.thr[0] - al * u[0], e.ref.pos[1] - e.thr[1] - al * u[1])
    return lat[1] > 0
