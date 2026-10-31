"""Pages A5 : composition du SVG (calques Inkscape) d'un chart 10-9 et de ses pages de parkings (cadrage 4.4 et 4.5)."""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import geo, layout, vector
from .fonts import CAP_HEIGHT, pt, text_width
from .svgdoc import (BAND, BUILD, INK, INK2, PAVE, TAXI, Doc, circle, f, line, path_d, rect, slug, text)

PAGE_W, PAGE_H, MARGIN = 148.0, 210.0, 6.0
X0, X1 = MARGIN, PAGE_W - MARGIN
FRAME_X, FRAME_W = 11.0, 126.0
FT = 3.28084
VERSION = 'v1'
PLAN_MARGIN_M = 80.0
FOND_S_MAX = 0.2        # lot heliports-fond (4.2) : echelle maximale du plan sur fond F10 (mm/m, 1:5 000)
FOND_PX_MM = 1748 / 148  # resolution du fond F10 = celle du PNG (px par mm)
AIRE_RED = '#AA2323'
AIRE_STROKE = 0.4
ROW_PITCH, LAST_ROW_Y = 3.46, 194.2
LEGEND_Y, FOOTER_RULE_Y, FOOTER_Y = 198.9, 201.2, 203.7
SYM_PLAN_R = 0.45       # rayon (mm) des trois symboles de parking sur le 10-9 (addendum 3)
SYM_INSET_R = 0.6       # rayon (mm) dans les encarts
STROKE_SHELTER, STROKE_HELI = 0.2, 0.25
CORNER_PAD = 2.0
CORNER_PAD_Y = 5.5
FRAME_CELL = layout.CELL_MM


@dataclass
class View:
    s: float                      # mm par metre
    xc: float
    zc: float
    ox: float                     # position page (mm) du point (xc, zc)
    oy: float

    def pt(self, x, z):
        return self.ox + (z - self.zc) * self.s, self.oy - (x - self.xc) * self.s

    def pts(self, arr):
        a = np.asarray(arr, float)
        return np.stack([self.ox + (a[:, 1] - self.zc) * self.s, self.oy - (a[:, 0] - self.xc) * self.s], axis=1)

    def ground(self, X, Y):
        return self.xc - (Y - self.oy) / self.s, self.zc + (X - self.ox) / self.s


@dataclass
class PageOut:
    id: str
    svg: str
    info: dict = field(default_factory=dict)


# ---------- geometrie des pistes ----------
def rw_axes(rw):
    d = np.array(rw.p1) - np.array(rw.p0)
    L = float(np.hypot(*d))
    u = d / L
    n = np.array([-u[1], u[0]])
    return u, n, L


def runway_polygon(rw, grow=0.0):
    u, n, L = rw_axes(rw)
    p0, p1 = np.array(rw.p0) - u * grow, np.array(rw.p1) + u * grow
    h = rw.width / 2 + grow
    return [p0 + n * h, p1 + n * h, p1 - n * h, p0 - n * h]


def rot_box(cx, cy, w, h, ang_deg):
    """Rectangle w x h (mm) centre en (cx, cy), tourne de ang_deg (sens horaire a l'ecran, comme rotate() en SVG) :
    quatre coins. La ligne de base du texte suit (cos a, sin a)."""
    a = math.radians(ang_deg)
    bx, by = math.cos(a), math.sin(a)
    ux, uy = by, -bx                                  # haut du texte
    return [(cx + sx * bx * w / 2 + sy * ux * h / 2, cy + sx * by * w / 2 + sy * uy * h / 2)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ---------- scene (plan et encarts) ----------
def draw_scene(view, data, rings_pav, rings_bld, parkings, layers, sym_r=SYM_PLAN_R, mark_w=0.30, designators=True, boxes=None):
    """Remplit `layers` (dict calque -> liste de chaines) : revetement, pistes, marquages, batiments, parkings."""
    s = view.s
    if rings_pav:
        d = path_d([view.pts(r) for r in rings_pav])
        layers['Pavement'].append(f'<path d="{d}" fill="{PAVE}" fill-rule="evenodd"/>')
    runs, marks = [], []
    for i, rw in enumerate(data.runways):
        poly = [view.pt(*p) for p in runway_polygon(rw)]
        runs.append('<polygon points="' + ' '.join(f'{f(a)},{f(b)}' for a, b in poly) + f'" fill="{INK}"/>')
        if boxes is not None:
            boxes.append(('runway', poly))
        u, n, L = rw_axes(rw)
        P0 = np.array(rw.p0)
        a, b = view.pt(*(P0 + u * 200)), view.pt(*(P0 + u * (L - 200)))
        if L > 400:
            marks.append(line(a[0], a[1], b[0], b[1], '#FFFFFF', mark_w,
                              f'stroke-dasharray="{f(30 * s)} {f(20 * s)}"'))
        for k, (pos, sgn) in enumerate(((P0, 1.0), (np.array(rw.p1), -1.0))):
            ds = []
            half = rw.width / 2
            for side in (-1, 1):
                for j in range(4):
                    lat = side * (4.0 + j * max(half - 8.0, 3.0) / 3.0)
                    q0 = pos + u * sgn * 6.0 + n * lat
                    q1 = pos + u * sgn * 36.0 + n * lat
                    A, B = view.pt(*q0), view.pt(*q1)
                    ds.append(f'M{f(A[0])} {f(A[1])}L{f(B[0])} {f(B[1])}')
            marks.append(f'<path d="{"".join(ds)}" fill="none" stroke="#FFFFFF" stroke-width="{f(mark_w * 0.7)}"/>')
            ds = []
            for side in (-1, 1):
                q = pos + u * sgn * 300.0 + n * side * rw.width * 0.2
                A, B = view.pt(*(q - u * sgn * 22.0)), view.pt(*(q + u * sgn * 22.0))
                ds.append(f'M{f(A[0])} {f(A[1])}L{f(B[0])} {f(B[1])}')
            marks.append(f'<path d="{"".join(ds)}" fill="none" stroke="#FFFFFF" stroke-width="{f(mark_w * 1.5)}"/>')
    if designators:
        for e in data.ends:
            rw = data.runways[e.rw]
            u, n, L = rw_axes(rw)
            inward = np.array([math.cos(math.radians(e.az_grid)), math.sin(math.radians(e.az_grid))])
            q = np.array(e.pos) + inward * 75.0
            X, Y = view.pt(*q)
            fs = min(pt(8.0), rw.width * s * 0.8 / CAP_HEIGHT)
            if fs < pt(3.0):
                continue
            marks.append(f'<g transform="translate({f(X)} {f(Y)}) rotate({f(e.az_grid, 2)})">'
                         + text(0, fs * CAP_HEIGHT / 2, e.name, fs, 'bold', '#FFFFFF', 'middle') + '</g>')
            if boxes is not None:
                boxes.append(('designator', rot_box(X, Y, text_width(e.name, 'bold', fs), fs * CAP_HEIGHT, e.az_grid)))
    layers['Runways'].extend(runs)
    layers['Runway markings'].extend(marks)
    if rings_bld:
        d = path_d([view.pts(r) for r in rings_bld])
        layers['Buildings'].append(f'<path d="{d}" fill="{BUILD}" fill-rule="evenodd"/>')
    for p in parkings:
        X, Y = view.pt(p.x, p.z)
        r = sym_r
        if p.kind == 'open':
            layers['Parking'].append(circle(X, Y, r, INK))
        elif p.kind == 'shelter':
            layers['Parking'].append(circle(X, Y, r, '#FFFFFF', INK, STROKE_SHELTER))
        else:
            layers['Parking'].append(circle(X, Y, r, '#FFFFFF', INK, STROKE_HELI))


def fond_circles(view, parkings, layers):
    """Lot heliports-fond (4.4) : un cercle sans remplissage par aire, a la taille de l'aire (bord exterieur du trait)."""
    for p in parkings:
        X, Y = view.pt(p.x, p.z)
        r = p.length / 2 * view.s
        layers['Parking'].append(circle(X, Y, r - AIRE_STROKE / 2, 'none', AIRE_RED, AIRE_STROKE))


def fond_number_elems(parkings, view, size):
    """Lot heliports-fond (4.4) : numero DCS (gras, rouge, halo blanc) au point haut-droit du cercle de l'aire."""
    out = []
    for p in parkings:
        X, Y = view.pt(p.x, p.z)
        r = p.length / 2 * view.s
        x, y = X + r * 0.7071 + 0.2, Y - r * 0.7071 - 0.2
        sz = size * 1.15
        out.append(text(x, y, p.name, sz, 'bold', '#FFFFFF', 'start',
                        extra='stroke="#FFFFFF" stroke-width="0.6" stroke-linejoin="round" class="halo"'))
        out.append(text(x, y, p.name, sz, 'bold', AIRE_RED, 'start', extra='class="num"'))
    return out, len(parkings)


def number_elems(parkings, view, size, dot_r, clip=None):
    """Numeros (halo + texte) places par la regle des 8 positions. Retourne (chaines, nombre place)."""
    pts_mm = [view.pt(p.x, p.z) for p in parkings]
    w = [text_width(p.name, 'semibold', size) for p in parkings]
    h = size * CAP_HEIGHT
    place = layout.place_numbers(pts_mm, w, h, dot_r, clip)
    out = []
    for p, (pos, rect_, anc) in zip(parkings, place):
        x = rect_[0] if anc == 'start' else rect_[2] if anc == 'end' else (rect_[0] + rect_[2]) / 2
        y = rect_[3]
        out.append(text(x, y, p.name, size, 'semibold', '#FFFFFF', anc,
                        extra='stroke="#FFFFFF" stroke-width="0.5" stroke-linejoin="round" class="halo"'))
        out.append(text(x, y, p.name, size, 'semibold', INK, anc, extra='class="num"'))
    return out, len(parkings)


# ---------- grille et reperes ----------
def edge_ticks(view, frame, proj, data_conv=None):
    """Graduations lat/lon a la minute sur les quatre bords. Retourne (gauche, droite, haut, bas) :
    listes de (position mm sur le bord, libelle)."""
    fx, fy, fw, fh = frame
    N = 80

    def samples(p0, p1):
        pts = []
        for t in np.linspace(0, 1, N):
            X, Y = p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t
            x, z = view.ground(X, Y)
            lat, lon = proj.to_latlon(x, z)
            pts.append((X, Y, lat, lon))
        return pts

    def crossings(pts, idx, fmt, coord):
        res = []
        vals = np.array([p[idx] for p in pts])
        lo, hi = vals.min(), vals.max()
        for m in range(int(math.floor(lo * 60)), int(math.ceil(hi * 60)) + 1):
            v = m / 60.0
            for a, b in zip(pts[:-1], pts[1:]):
                if (a[idx] - v) * (b[idx] - v) <= 0 and a[idx] != b[idx]:
                    t = (v - a[idx]) / (b[idx] - a[idx])
                    pos = a[coord] + (b[coord] - a[coord]) * t
                    deg, mi = divmod(m, 60)
                    res.append((pos, fmt(deg, mi)))
                    break
        return res

    lat_fmt = lambda d, m: f'{d:02d}-{m:02d}'
    lon_fmt = lambda d, m: f'{d:03d}-{m:02d}'
    left = crossings(samples((fx, fy), (fx, fy + fh)), 2, lat_fmt, 1)
    right = crossings(samples((fx + fw, fy), (fx + fw, fy + fh)), 2, lat_fmt, 1)
    top = crossings(samples((fx, fy), (fx + fw, fy)), 3, lon_fmt, 0)
    bot = crossings(samples((fx, fy + fh), (fx + fw, fy + fh)), 3, lon_fmt, 0)
    return left, right, top, bot


def north_block(cx, cy, conv, decl, year):
    """Bloc nord : nord vrai et nord magnetique (fleches tournees de -convergence et de -convergence + declinaison),
    `VAR 3°E` et `(1985)`. (cx, cy) : pied des fleches ; hauteur totale ~ 20 mm."""
    out = []
    L = 12.0
    for ang, sw, solid in ((-conv, 0.3, False), (-conv + decl, 0.6, True)):
        g = [f'<g transform="translate({f(cx)} {f(cy)}) rotate({f(ang, 3)})">',
             line(0, 0, 0, -L, INK, sw),
             (f'<polygon points="0,{f(-L - 1.6)} {f(-1.0)},{f(-L + 1.0)} {f(1.0)},{f(-L + 1.0)}" fill="{INK}"/>' if solid else
              f'<polyline points="{f(-0.9)},{f(-L + 0.9)} 0,{f(-L - 0.2)} {f(0.9)},{f(-L + 0.9)}" fill="none" stroke="{INK}" stroke-width="0.3"/>'),
             '</g>']
        out.append(''.join(g))
    var, yl = geo.var_text(decl, year)
    out.append(text(cx, cy + 3.4, var, pt(5.9), 'bold', INK, 'middle'))
    out.append(text(cx, cy + 6.0, yl, pt(5.4), 'regular', INK2, 'middle'))
    return out


SCALE_MAX_MM = 38.0
SCALE_STEPS = ((100.0, 500.0), (50.0, 200.0), (25.0, 100.0))    # lot heliports (4.3) : (pas en m, pas en pieds)


def scale_plan(s_mm_per_m):
    """Barre d'echelle : (longueur en m, pas en m, pas en pieds). Regle d'origine (graduations de 250 m et de 1 000 ft,
    pas = None) tant que 38 mm couvrent au moins 500 m. Lot heliports (4.3) : en dessous, le premier des pas (100 m,
    500 ft), (50 m, 200 ft), (25 m, 100 ft) tel que 38 mm / s >= 2 x pas ; longueur = le plus grand multiple du pas qui
    tient dans 38 mm (au moins un pas si 38 mm ne couvrent pas meme un pas)."""
    span = SCALE_MAX_MM / s_mm_per_m
    if span >= 500.0:
        return math.floor(span / 250.0) * 250.0, None, None
    mstep, fstep = SCALE_STEPS[-1]
    for ms, fs in SCALE_STEPS:
        if span >= 2 * ms:
            mstep, fstep = ms, fs
            break
    return max(mstep, math.floor(span / mstep) * mstep), mstep, fstep


def scale_block(x, y, s_mm_per_m):
    """Echelle en pieds (graduations de 1 000 ft) et en metres (graduations de 250 m) ; (x, y) : debut de la barre.
    Barre de moins de 500 m (lot heliports, 4.3) : graduations du pas de `scale_plan`, chacune porte sa valeur."""
    L_m, mstep, fstep = scale_plan(s_mm_per_m)
    Lmm = L_m * s_mm_per_m
    out = [line(x, y, x + Lmm, y, INK, 0.35)]
    size = pt(5.3)
    if mstep is not None:
        n = int(round(L_m / mstep))
        for i in range(n + 1):
            xm = x + i * mstep * s_mm_per_m
            out.append(line(xm, y, xm, y + 1.6, INK, 0.3))
            out.append(text(xm, y + 4.2, f'{int(i * mstep)}' + (' M' if i == n else ''), size, 'regular', INK, 'middle'))
        n_ft = int(L_m * FT // fstep)
        for k in range(n_ft + 1):
            xf = x + k * fstep / FT * s_mm_per_m
            out.append(line(xf, y, xf, y - 1.6, INK, 0.3))
            out.append(text(xf, y - 2.3, f'{int(k * fstep)}' + (' FT' if k == n_ft else ''), size, 'regular', INK, 'middle'))
        return out, Lmm
    # metres, sous la barre
    for i in range(int(L_m // 250) + 1):
        xm = x + i * 250 * s_mm_per_m
        major = (i % 2 == 0)
        out.append(line(xm, y, xm, y + (1.6 if major else 1.0), INK, 0.3))
        if major:
            out.append(text(xm, y + 4.2, f'{int(i * 250)}' + (' M' if abs(i * 250 - L_m) < 1 else ''), size, 'regular', INK, 'middle'))
    # pieds, sur la barre
    n_ft = int(L_m * FT // 1000)
    for k in range(n_ft + 1):
        xf = x + k * 1000 / FT * s_mm_per_m
        out.append(line(xf, y, xf, y - 1.6, INK, 0.3))
        out.append(text(xf, y - 2.3, f'{k * 1000}' + (' FT' if k == n_ft else ''), size, 'regular', INK, 'middle'))
    return out, Lmm


# ---------- en-tete, bandeaux, tableau, pied ----------
HEADER_ROW_Y = 21.2
HEADER_ROW_GAP_MIN = 4.0       # mm : ecart minimal entre deux champs de la ligne d'en-tete


def header_row(elev_text, lat, lon, var_text):
    """Les cinq champs de la ligne d'en-tete (elevation, ARP en degres et minutes decimales, degres-minutes-secondes, MGRS,
    declinaison), en elements SVG : le premier en X0, le dernier finissant en X1, les ecarts egaux (ValueError sous 4 mm)."""
    items = [elev_text, geo.arp_text(lat, lon), geo.dms(lat, 'N', 'S', 2) + ' ' + geo.dms(lon, 'E', 'W', 3),
             'MGRS ' + geo.mgrs(lat, lon), var_text]
    widths = [text_width(s, 'bold', pt(7.2)) for s in items]
    gap = (X1 - X0 - sum(widths)) / (len(items) - 1)
    if gap < HEADER_ROW_GAP_MIN:
        raise ValueError(f"ligne d'en-tete : ecart de {gap:.2f} mm entre les champs (minimum {HEADER_ROW_GAP_MIN:g} mm) : {items}")
    out, x = [], X0
    for s, w in zip(items, widths):
        out.append(text(x, HEADER_ROW_Y, s, pt(7.2), 'bold'))
        x += w + gap
    return out


def header(doc, data, index, kind, bands=True):
    L = 'Header'
    name = data.airport_name.upper()
    if not (data.heli_mode and not data.icao_known):      # lot heliports (4.1) : sans OACI, le coin gauche d'une base d'helicopteres reste vide
        doc.add(L, text(X0, 11.4, data.icao, pt(13), 'bold'))
    doc.add(L, text(X0, 14.7, f'{geo.theatre_display(data.theatre)} · DCS', pt(6.5), 'regular', INK2))
    doc.add(L, text(PAGE_W / 2, 11.9, name, pt(15), 'bold', INK, 'middle', spacing=1.1))
    doc.add(L, text(PAGE_W / 2, 15.0, kind, pt(6.5), 'regular', INK2, 'middle', spacing=0.8))
    doc.add(L, text(X1, 11.4, index, pt(13), 'bold', INK, 'end'))
    doc.add(L, text(X1, 14.7, VERSION, pt(6.5), 'regular', INK2, 'end'))
    doc.add(L, line(X0, 17.1, X1, 17.1, INK, 0.5))
    var, yl = geo.var_text(data.decl, data.year)
    for el in header_row(f'ÉLÉV. {data.elev_ft} FT', data.lat, data.lon, f'{var} {yl}'):
        doc.add(L, el)
    doc.add(L, line(X0, 22.8, X1, 22.8, INK, 0.25))
    if not bands:
        return 22.8
    # tour
    doc.add(L, rect(X0, 23.8, X1 - X0, 5.2, BAND))
    doc.add(L, text(7.5, 27.4, f'{data.airport_name.upper()} TOWER', pt(7.6), 'bold'))
    for label, key, xl, xv in (('VHF', 'vhf', 44.0, 50.5), ('UHF', 'uhf', 67.9, 74.5), ('FM', 'fm', 92.0, 98.5),
                               ('HF', 'hf', 116.0, 122.5)):
        doc.add(L, text(xl, 27.4, label, pt(5.6), 'regular', INK2))
        doc.add(L, text(xv, 27.4, data.tower[key], pt(7.6), 'bold'))
    bottom = 29.0
    if data.beacons:
        doc.add(L, rect(X0, 30.0, X1 - X0, 5.2, BAND))
        doc.add(L, text(7.5, 33.6, 'BALISES', pt(7.6), 'bold'))
        gap_lv, gap_it = 1.4, 6.0
        items = [(b.kind, f'{b.callsign} {b.text}') for b in data.beacons]
        size_l, size_v = pt(5.6), pt(7.6)
        total = sum(text_width(k, 'regular', size_l) + gap_lv + text_width(v, 'bold', size_v) for k, v in items) \
            + gap_it * (len(items) - 1)
        avail = X1 - 44.0 - 1.5
        k = 1.0
        if total > avail:
            gap_it = max(2.0, gap_it - (total - avail) / max(1, len(items) - 1))
            total = sum(text_width(k_, 'regular', size_l) + gap_lv + text_width(v, 'bold', size_v) for k_, v in items) \
                + gap_it * (len(items) - 1)
            if total > avail:
                k = avail / total
                size_l, size_v = size_l * k, size_v * k
        x = 44.0
        for kind_, val in items:
            doc.add(L, text(x, 33.6, kind_, size_l, 'regular', INK2))
            x += text_width(kind_, 'regular', size_l) + gap_lv
            doc.add(L, text(x, 33.6, val, size_v, 'bold'))
            x += text_width(val, 'bold', size_v) + gap_it
        bottom = 35.2
    return bottom


def footer(doc, data, osm):
    L = 'Footer'
    doc.add(L, line(X0, FOOTER_RULE_Y, X1, FOOTER_RULE_Y, INK, 0.5))
    s = 'DCS AIRFIELD CHARTS · données : carte F10 et fichiers de DCS World (Eagle Dynamics), via pydcs'
    if osm:
        s += ' · taxiways © OpenStreetMap contributors'
    doc.add(L, text(X0, FOOTER_Y, s, pt(6.1), 'regular', INK2))
    doc.add(L, text(X1, FOOTER_Y, 'PAS POUR LA NAVIGATION RÉELLE', pt(6.1), 'bold', INK, 'end'))


def legend(doc, y, x_left=None, x_right=None, note=None, sym_r=SYM_PLAN_R, heli=False, fond=False):
    """Legende des parkings (symboles dessines, pas de glyphes de police) ; alignee a gauche (x_left) ou a droite (x_right) ;
    `note` : texte secondaire aligne a droite sur la meme ligne.
    `heli` (lot heliports, 4.1) : base d'helicopteres, un seul element (l'aire d'helicoptere), sans texte secondaire."""
    L = 'Runway table'
    size = pt(6.3)
    if fond:                                           # lot heliports-fond (4.6) : cercle rouge de 1,3 mm, aligne a droite sur x_right
        t = "AIRE D'H\u00c9LICOPT\u00c8RE (NUM\u00c9RO DCS) \u00b7 FOND : CARTE F10"
        x = x_right - (4.6 + text_width(t, 'regular', size))
        doc.add(L, circle(x + 1.5, y - 1.0, 1.3, 'none', AIRE_RED, AIRE_STROKE))
        doc.add(L, text(x + 4.4, y, t, size, 'regular', INK2))
        return
    items = [('open', 'OUVERT'), ('shelter', 'ABRI (HAS, HANGAR)'), ('heli', 'H\u00c9LICOPT\u00c8RE SEUL')]
    if heli:
        items, note = [('heli', "AIRE D'H\u00c9LICOPT\u00c8RE (NUM\u00c9RO DCS)")], None
    widths = [3.8 + text_width(t, 'regular', size) for _, t in items]
    gap = 7.0
    total = sum(widths) + gap * (len(items) - 1)
    x = x_left if x_left is not None else x_right - total
    for (kind, t), w in zip(items, widths):
        cx, cy = x + 1.2, y - 1.0
        if kind == 'open':
            doc.add(L, circle(cx, cy, sym_r, INK))
        elif kind == 'shelter':
            h = sym_r * 1.9
            doc.add(L, rect(cx - h, cy - h, 2 * h, 2 * h, BUILD, extra='rx="0.6"'))
            doc.add(L, circle(cx, cy, sym_r, '#FFFFFF', INK, STROKE_SHELTER))
        else:
            doc.add(L, circle(cx, cy, sym_r, '#FFFFFF', INK, STROKE_HELI))
        doc.add(L, text(x + 3.6, y, t, size, 'regular', INK2))
        x += w + gap
    if note:
        doc.add(L, text(X1, y, note, size, 'regular', INK2, 'end'))


def runway_table(doc, data, frame_bottom_unused, head_y, rows_y):
    L = 'Runway table'
    cols = [6.0, 20.0, 40.0, 68.0, 94.0]
    heads = ['PISTE', 'CAP MAG', 'LONGUEUR × LARGEUR', 'ÉLÉV. SEUIL', 'ILS']
    if data.runways:                                   # lot heliports (4.2) : sans piste, ni tableau ni en-tetes
        for x, h in zip(cols, heads):
            doc.add(L, text(x, head_y, h, pt(5.2), 'semibold', INK2, spacing=0.4))
    n_total, n_shel = len(data.parkings), data.n_shelter
    count = f'AIRES : {n_total}' if data.heli_mode else f'PARKINGS : {n_total} DONT {n_shel} EN ABRI'
    doc.add(L, text(X1, head_y, count, pt(6.6), 'bold', INK, 'end'))
    for e, y in zip(data.ends, rows_y):
        rw = data.runways[e.rw]
        Lm, Wm = rw.length, rw.width
        dims = f'{geo.thin(Lm * FT)} × {geo.thin(Wm * FT)} FT   ({int(round(Lm))} × {int(round(Wm))} M)'
        dims = f'{geo.thin(Lm * FT)} × {geo.thin(Wm * FT)} FT ({int(round(Lm))} × {int(round(Wm))} M)'
        doc.add(L, text(cols[0], y, e.name, pt(7.2), 'bold'))
        doc.add(L, text(cols[1], y, f'{e.mag:05.1f}°', pt(7.2), 'bold'))
        doc.add(L, text(cols[2], y, dims, pt(6.9), 'regular'))
        thr = f'{e.thr_elev_ft} FT' if e.thr_elev_ft is not None else '—'
        doc.add(L, text(cols[3], y, thr, pt(6.9), 'regular'))
        doc.add(L, text(cols[4], y, e.ils if e.ils else '—', pt(7.2), 'bold' if e.ils else 'regular'))


# ---------- masques -> cellules ----------
def cells_from_mask(mask, g, frame_ground, nx, ny):
    """Grille booleenne (ny, nx) : cellule occupee si le masque la recouvre (>= 3 %).
    frame_ground = (x_hi, x_lo, z_lo, z_hi) : emprise au sol du cadre."""
    x_hi, x_lo, z_lo, z_hi = frame_ground
    r0, r1 = int(math.floor((g.x_top - x_hi) / g.mpp)), int(math.ceil((g.x_top - x_lo) / g.mpp))
    c0, c1 = int(math.floor((z_lo - g.z_left) / g.mpp)), int(math.ceil((z_hi - g.z_left) / g.mpp))
    sub = np.zeros((r1 - r0, c1 - c0), np.uint8)
    a0, a1, b0, b1 = max(r0, 0), min(r1, mask.shape[0]), max(c0, 0), min(c1, mask.shape[1])
    if a1 > a0 and b1 > b0:
        sub[a0 - r0:a1 - r0, b0 - c0:b1 - c0] = mask[a0:a1, b0:b1].view(np.uint8) * 255
    return cv2.resize(sub, (nx, ny), interpolation=cv2.INTER_AREA) > 8
