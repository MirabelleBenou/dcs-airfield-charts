"""Lot ac-variante-contexte : la page 10-8 « VICINITY » (les abords de la base), placee avant la 10-9.

Elle existe pour toute base qui a sa capture elargie (`capture/f10capture.py capture <Theatre> <Base> --marge <m>`, rangee sous
`<work_dir>/out/<theatre en minuscules>/contexte/<Base>/` : `base.json`, `tiles.json`, `mosaic.png`). La mosaique elargie ne sert
qu'au fond : elle n'entre jamais dans `analysis.analyze` ni dans un masque ; elle est lue une fois, reduite a la resolution de la
page, et liberee. Pistes, caps, balises et noms de taxiways sont ceux de la 10-9 de la meme base (`analysis` sur la capture de
base) ; ni revetement, ni batiments, ni parkings, ni encarts. Les villes a 20 km au plus de l'ARP sont marquees (point et nom dans
le cadre, renvoi sur le bord hors du cadre).
"""
import base64
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from . import compose, directory, export, georef
from .analysis import ChartError
from .fonts import CAP_HEIGHT, pt, text_width
from .page import (BEARING_SIZE, FOND_PX_MM, FRAME_CELL, GRAD_SIZE, LAST_ROW_Y, LEGEND_Y, NAVAID_LINES, PAGE_H, PAGE_W, ROW_PITCH,
                   X1, CapEnd, PageOut, View, cap_element, draw_scene, elem_geom, elems_geom, footer, grad_blockers, header,
                   north_block, place_caps, runway_polygon, runway_table, scale_block, scale_plan, text_rect)
from .svgdoc import INK, INK2, Doc, f, rect, text, with_halo

CODE, SUBTITLE = '10-8', 'VICINITY'
LIGHTEN = 0.45                    # fond : Image.blend(fond, blanc, 0,45)
GRAY = (222, 222, 222)            # hors de la mosaique (cas qui ne doit pas arriver : le cadre est rempli par la mosaique)
EDGE_EPS_PX = 0.01
TOWN_MAX_KM = 20.0
TOWN_DOT_R = 0.4                  # point d'une ville : 0,8 mm de diametre
TOWN_SIZE, TOWN_WEIGHT, TOWN_COLOR = NAVAID_LINES[0]      # taille et fonte des libelles de balises
TRI_LEN, TRI_HALF = 1.6, 0.7      # triangle d'un renvoi : 1,6 mm de long, 1,4 mm de base
EDGE_GAP_MM = 0.8                 # pointe du triangle a 0,8 mm du bord du cadre
TEXT_GAP_MM = 0.8
SLIDE_STEP_MM = 1.0
HALO_PAD_MM = 0.35                # le halo depasse le texte de 0,35 mm
INNER_MM = 0.3                    # un renvoi ou un nom reste a 0,3 mm au moins a l'interieur du cadre
LEGEND_TEXT = 'TOWN WITHIN 20 KM: DISTANCE FROM ARP · BACKGROUND: F10 MAP'
SIDES = {'R': ((-1.0, 0.0), (0.0, 1.0)), 'L': ((1.0, 0.0), (0.0, 1.0)),      # bord : (normale vers l'interieur, tangente)
         'T': ((0.0, 1.0), (1.0, 0.0)), 'B': ((0.0, -1.0), (1.0, 0.0))}

Image.MAX_IMAGE_PIXELS = None


# ---------- la capture elargie ----------
def context_folder(cfg, theatre, base):
    """Dossier de la capture elargie de la base (`out/<theatre en minuscules>/contexte/<Base>/`, comme la capture de base :
    meme casse), ou None s'il manque `base.json` ou `mosaic.png`."""
    folder = Path(cfg.work_dir) / 'out' / theatre.lower() / 'contexte' / base
    return folder if (folder / 'base.json').is_file() and (folder / 'mosaic.png').is_file() else None


def context_georef(folder):
    """Georeferencement de la mosaique elargie (`georef.georef_from_base` de son propre base.json) ; ChartError si la taille de
    mosaic.png (lue dans son en-tete) differe de celle de base.json."""
    g = georef.georef_from_base(georef.load_base_json(folder))
    with Image.open(Path(folder) / 'mosaic.png') as im:
        if im.size != (g.width, g.height):
            raise ChartError(f'mosaique elargie {im.size[0]}x{im.size[1]} differente de base.json {g.width}x{g.height} : {folder}')
    return g


def plan_view(g, frame):
    """Cadre rempli par la mosaique elargie : echelle max(fw / W, fh / H) (mm/m), centre = centre de la mosaique."""
    fx, fy, fw, fh = frame
    z0, z1 = g.z_left, g.z_left + g.width * g.mpp
    x1, x0 = g.x_top, g.x_top - g.height * g.mpp
    s = max(fw / (z1 - z0), fh / (x1 - x0))
    return View(s, (x0 + x1) / 2, (z0 + z1) / 2, fx + fw / 2, fy + fh / 2)


# ---------- le fond ----------
def ground_image(view, frame, g, im):
    """La mosaique `im` (PIL RGB) sur le terrain couvert par le cadre, reechantillonnee en bicubique a FOND_PX_MM. Le cadre est dans
    la mosaique : reechantillonnage direct, sans copie de la mosaique (pic memoire). Hors de la mosaique (cas qui ne doit pas
    arriver) : gris (222, 222, 222), sur une fenetre."""
    fx, fy, fw, fh = frame
    s = view.s
    wpx, hpx = int(round(fw * FOND_PX_MM)), int(round(fh * FOND_PX_MM))
    zc_l, xc_t = view.zc - (fw / 2) / s, view.xc + (fh / 2) / s
    u0, u1 = (zc_l - g.z_left) / g.mpp, (zc_l + fw / s - g.z_left) / g.mpp
    v0, v1 = (g.x_top - xc_t) / g.mpp, (g.x_top - (xc_t - fh / s)) / g.mpp
    W, H = im.size
    if u0 >= -EDGE_EPS_PX and v0 >= -EDGE_EPS_PX and u1 <= W + EDGE_EPS_PX and v1 <= H + EDGE_EPS_PX:
        return im.resize((wpx, hpx), Image.BICUBIC, box=(max(u0, 0.0), max(v0, 0.0), min(u1, float(W)), min(v1, float(H))))
    wc0, wc1 = int(math.floor(u0)) - 2, int(math.ceil(u1)) + 2
    wr0, wr1 = int(math.floor(v0)) - 2, int(math.ceil(v1)) + 2
    win = np.empty((wr1 - wr0, wc1 - wc0, 3), np.uint8)
    win[:] = GRAY
    a0, a1, b0, b1 = max(wr0, 0), min(wr1, H), max(wc0, 0), min(wc1, W)
    if a1 > a0 and b1 > b0:
        win[a0 - wr0:a1 - wr0, b0 - wc0:b1 - wc0] = np.asarray(im.crop((b0, a0, b1, a1)))
    return Image.fromarray(win).resize((wpx, hpx), Image.BICUBIC, box=(u0 - wc0, v0 - wr0, u1 - wc0, v1 - wr0))


def lighten(img, amount=LIGHTEN):
    """Eclaircissement du fond : `Image.blend(fond, blanc, amount)`, blanc = (255, 255, 255)."""
    return Image.blend(img, Image.new('RGB', img.size, (255, 255, 255)), amount)


def background_image(folder, g, view, frame):
    """Le fond de la 10-8 : mosaique elargie sur le terrain du cadre, reechantillonnee (bicubique, FOND_PX_MM), adoucie par
    `directory.soften`, puis eclaircie. La mosaique est lue une fois et liberee ; aucune analyse n'y touche. Image PIL RGB."""
    with Image.open(Path(folder) / 'mosaic.png') as im:
        im.load()
        if im.mode != 'RGB':
            im = im.convert('RGB')
        ground = ground_image(view, frame, g, im)
    return lighten(directory.soften(ground))


def image_elem(frame, img):
    """L'unique <image> du calque `F10 map` : exactement le cadre, JPEG qualite 88 en data: base64 (comme `compose.fond_image_elem`)."""
    fx, fy, fw, fh = frame
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=export.JPEG_QUALITY)
    href = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    return f'<image x="{f(fx)}" y="{f(fy)}" width="{f(fw)}" height="{f(fh)}" preserveAspectRatio="none" href="{href}" xlink:href="{href}"/>'


# ---------- les villes ----------
def towns_near(data, towns=None, max_km=TOWN_MAX_KM):
    """Villes a `max_km` au plus de l'ARP (data.lat, data.lon), par la formule de `directory.nearest_town` (111,2 km par degre,
    cosinus de la latitude de l'ARP) : [(distance km, nom, lat, lon)] par distance croissante, puis nom."""
    if towns is None:
        try:
            towns = directory.load_towns(data.theatre)
        except KeyError:
            towns = []
    out = []
    for name, tlat, tlon in towns:
        dx = (data.lon - tlon) * 111.2 * math.cos(math.radians(data.lat))
        dy = (data.lat - tlat) * 111.2
        d = math.hypot(dx, dy)
        if d <= max_km:
            out.append((d, name, tlat, tlon))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def town_positions(data, view, frame, towns=None):
    """Les villes retenues avec leur position sur la page : [{'name', 'km', 'dist', 'xy', 'inside'}] ; `inside` : le point (rayon
    0,4 mm compris) est dans le cadre."""
    fx, fy, fw, fh = frame
    out = []
    for d, name, tlat, tlon in towns_near(data, towns):
        X, Y = view.pt(*data.projection.to_xz(tlat, tlon))
        inside = fx + TOWN_DOT_R <= X <= fx + fw - TOWN_DOT_R and fy + TOWN_DOT_R <= Y <= fy + fh - TOWN_DOT_R
        out.append({'name': name, 'dist': d, 'km': int(d + 0.5), 'xy': (X, Y), 'inside': inside})
    return out


def edge_exit(frame, A, T):
    """Point ou le segment ARP (A) -> ville (T), en mm de page, sort du cadre : (P, bord 'L'/'R'/'T'/'B', direction unitaire). Si l'ARP
    n'est pas dans le cadre, le segment part du centre du cadre."""
    fx, fy, fw, fh = frame
    ax, ay = A
    if not (fx < ax < fx + fw and fy < ay < fy + fh):
        ax, ay = fx + fw / 2, fy + fh / 2
    dx, dy = T[0] - ax, T[1] - ay
    cands = []
    if dx > 0:
        cands.append(((fx + fw - ax) / dx, 'R'))
    elif dx < 0:
        cands.append(((fx - ax) / dx, 'L'))
    if dy > 0:
        cands.append(((fy + fh - ay) / dy, 'B'))
    elif dy < 0:
        cands.append(((fy - ay) / dy, 'T'))
    t, side = min(cands)
    L = math.hypot(dx, dy)
    return (ax + dx * t, ay + dy * t), side, (dx / L, dy / L)


def renvoi_block(P, side, u, label, offset, size=TOWN_SIZE):
    """Un renvoi pose au point P du bord `side`, decale de `offset` mm le long du bord : (sommets du triangle, (x, y, ancre du
    texte)). Le triangle pointe vers la ville (direction u), sa pointe a 0,8 mm du bord ; le texte est a l'interieur, a cote."""
    n_in, e = SIDES[side]
    tip = (P[0] + e[0] * offset + n_in[0] * EDGE_GAP_MM, P[1] + e[1] * offset + n_in[1] * EDGE_GAP_MM)
    bc = (tip[0] - u[0] * TRI_LEN, tip[1] - u[1] * TRI_LEN)
    w = (-u[1], u[0])
    tri = [tip, (bc[0] + w[0] * TRI_HALF, bc[1] + w[1] * TRI_HALF), (bc[0] - w[0] * TRI_HALF, bc[1] - w[1] * TRI_HALF)]
    xs, ys = [p[0] for p in tri], [p[1] for p in tri]
    cx, cy = (tip[0] + bc[0]) / 2, (tip[1] + bc[1]) / 2
    h = size * CAP_HEIGHT
    if side == 'R':
        txt = (min(xs) - TEXT_GAP_MM, cy + h / 2, 'end')
    elif side == 'L':
        txt = (max(xs) + TEXT_GAP_MM, cy + h / 2, 'start')
    elif side == 'T':
        txt = (cx, max(ys) + TEXT_GAP_MM + h, 'middle')
    else:
        txt = (cx, min(ys) - TEXT_GAP_MM, 'middle')
    return tri, txt


def block_geom(tri, txt, label, size=TOWN_SIZE):
    x, y, anchor = txt
    return unary_union([Polygon(tri), box(*text_rect(x, y, label, size, TOWN_WEIGHT, anchor))]).buffer(HALO_PAD_MM)


def place_renvoi(frame, A, T, label, obstacles, size=TOWN_SIZE):
    """Cadrage 2.6 : le renvoi d'une ville hors du cadre, sur le bord que coupe le segment ARP -> ville ; s'il heurte un obstacle
    (geometries shapely : nord, echelle, caps, graduations, autres renvois...) ou sort du cadre, il glisse le long du bord (pas de
    1 mm, a droite puis a gauche de la position d'origine). Retourne {'tri', 'text', 'geom', 'side', 'offset'} ou None (aucune
    place : renvoi omis)."""
    fx, fy, fw, fh = frame
    P, side, u = edge_exit(frame, A, T)
    inner = box(fx + INNER_MM, fy + INNER_MM, fx + fw - INNER_MM, fy + fh - INNER_MM)
    steps = int((fh if side in 'LR' else fw) / SLIDE_STEP_MM)
    for k in range(steps + 1):
        for sgn in ((1.0,) if k == 0 else (1.0, -1.0)):
            off = sgn * k * SLIDE_STEP_MM
            tri, txt = renvoi_block(P, side, u, label, off, size)
            g = block_geom(tri, txt, label, size)
            if inner.contains(g) and not any(g.intersects(o) for o in obstacles):
                return {'tri': tri, 'text': txt, 'geom': g, 'side': side, 'offset': off}
    return None


def place_town_name(frame, xy, name, obstacles, size=TOWN_SIZE):
    """Le nom (capitales) d'une ville dans le cadre : a droite du point, sinon a gauche, au-dessus, au-dessous : la premiere position
    entierement dans le cadre qui ne touche aucun obstacle ; sinon la premiere dans le cadre ; sinon a droite, decale dans le
    cadre. Retourne (x, y, ancre, geometrie)."""
    fx, fy, fw, fh = frame
    X, Y = xy
    h = size * CAP_HEIGHT
    gap = TOWN_DOT_R + TEXT_GAP_MM
    cands = [(X + gap, Y + h / 2, 'start'), (X - gap, Y + h / 2, 'end'), (X, Y - gap, 'middle'), (X, Y + gap + h, 'middle')]
    inner = box(fx + INNER_MM, fy + INNER_MM, fx + fw - INNER_MM, fy + fh - INNER_MM)
    geoms = [box(*text_rect(x, y, name, size, TOWN_WEIGHT, a)).buffer(HALO_PAD_MM) for x, y, a in cands]
    for c, g in zip(cands, geoms):
        if inner.contains(g) and not any(g.intersects(o) for o in obstacles):
            return (*c, g)
    for c, g in zip(cands, geoms):
        if inner.contains(g):
            return (*c, g)
    x, y, a = cands[0]
    r = text_rect(x, y, name, size, TOWN_WEIGHT, a)
    dx = min(0.0, fx + fw - INNER_MM - HALO_PAD_MM - r[2])
    dx = max(dx, fx + INNER_MM + HALO_PAD_MM - (r[0] + dx))
    return (x + dx, y, a, box(*text_rect(x + dx, y, name, size, TOWN_WEIGHT, a)).buffer(HALO_PAD_MM))


def town_elems(pos, name_place):
    """Elements SVG d'une ville dans le cadre : nom (halo, puis texte), puis le point."""
    x, y, a, _ = name_place
    return [with_halo(text(x, y, pos['name'].upper(), TOWN_SIZE, TOWN_WEIGHT, TOWN_COLOR, a)),
            f'<circle cx="{f(pos["xy"][0])}" cy="{f(pos["xy"][1])}" r="{f(TOWN_DOT_R)}" fill="{INK}"/>']


def renvoi_elems(pos, pl):
    """Elements SVG d'un renvoi : triangle plein, puis texte `<NOM> <n> km` (halo, puis texte)."""
    x, y, a = pl['text']
    pts = ' '.join(f'{f(px)},{f(py)}' for px, py in pl['tri'])
    return [f'<polygon points="{pts}" fill="{INK}"/>',
            with_halo(text(x, y, renvoi_text(pos), TOWN_SIZE, TOWN_WEIGHT, TOWN_COLOR, a))]


def renvoi_text(pos):
    return f'{pos["name"].upper()} {pos["km"]} km'


def draw_towns(doc, positions, frame, A, blockers, log=print):
    """Villes de la page : dans le cadre un point et un nom, hors du cadre un renvoi sur le bord. `blockers` : geometries a eviter
    (nord, echelle, caps, graduations, balises, taxiways, pistes). Retourne {'inside': [...], 'renvois': [...], 'omitted': [...]}."""
    blockers = list(blockers)
    ins = [p for p in positions if p['inside']]
    outs = [p for p in positions if not p['inside']]
    blockers += [Point(*p['xy']).buffer(TOWN_DOT_R + 0.2) for p in ins]
    info = {'inside': [], 'renvois': [], 'omitted': []}
    for p in ins:
        pl = place_town_name(frame, p['xy'], p['name'].upper(), blockers)
        blockers.append(pl[3])
        for e in town_elems(p, pl):
            doc.add('Towns', e)
        info['inside'].append((p['name'], p['xy'], pl[2]))
    for p in outs:
        pl = place_renvoi(frame, A, p['xy'], renvoi_text(p), blockers)
        if pl is None:
            log(f'10-8 : renvoi omis : {p["name"]}')
            info['omitted'].append(p['name'])
            continue
        blockers.append(pl['geom'])
        for e in renvoi_elems(p, pl):
            doc.add('Towns', e)
        info['renvois'].append((p['name'], p['km'], pl['side'], pl['offset']))
    log(f'10-8 : villes : {len(ins)} dans le cadre, {len(info["renvois"])} renvois')
    return info


# ---------- la legende ----------
def legend(doc, y, x_right=X1):
    """Legende de la 10-8 : le triangle du renvoi, puis le texte ; alignee a droite sur x_right."""
    size = pt(6.3)
    x = x_right - (4.6 + text_width(LEGEND_TEXT, 'regular', size))
    cx, cy = x + 1.5, y - 1.0
    doc.add('Runway table', f'<polygon points="{f(cx)},{f(cy - 1.0)} {f(cx - 0.9)},{f(cy + 0.8)} {f(cx + 0.9)},{f(cy + 0.8)}" fill="{INK}"/>')
    doc.add('Runway table', text(x + 4.4, y, LEGEND_TEXT, size, 'regular', INK2))


# ---------- la page ----------
def build_vicinity_page(data, frame, folder, log=print, towns=None):
    """La page 10-8 de `data` (analyse de la capture de base) sur le cadre `frame` de sa 10-9, avec la capture elargie de `folder`.
    `towns` : [(nom, lat, lon)] (defaut : `directory.load_towns`). Retourne un `page.PageOut` ; journal prefixe `10-8 `."""
    g = context_georef(folder)
    logp = lambda m: log(f'10-8 {m}')
    fx, fy, fw, fh = frame
    doc = Doc()
    doc.add('Background', rect(0, 0, PAGE_W, PAGE_H, '#FFFFFF'))
    header(doc, data, CODE, SUBTITLE, bands=True)
    doc.add_clip('clip-plan', fx, fy, fw, fh)
    view = plan_view(g, frame)
    s = view.s
    logp(f'plan : echelle {s:.5f} mm/m (1:{1000 / s:,.0f}), cadre {fw:.1f} x {fh:.1f} mm')

    # ---- le fond : l'unique <image> du calque `F10 map`, le cadre exactement
    doc.add('F10 map', image_elem(frame, background_image(folder, g, view, frame)))

    # ---- pistes, marquages et designations (comme la 10-9) ; ni revetement, ni batiments, ni parkings
    lay = {k: [] for k in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Parking')}
    draw_scene(view, data, [], [], [], lay)
    for k in ('Runways', 'Runway markings'):
        for e in lay[k]:
            doc.add(k, e)
    runway_geoms = [elem_geom(e) for e in lay['Runways']]

    # ---- villes (positions) et coins du nord et de l'echelle
    positions = town_positions(data, view, frame, towns)
    nx, ny = int(round(fw / FRAME_CELL)), int(round(fh / FRAME_CELL))
    occ = np.zeros((ny, nx), bool)
    for rw in data.runways:
        occ |= compose.poly_cells([view.pt(*q) for q in runway_polygon(rw, compose.RUNWAY_FREE_M)], (fx, fy), occ.shape)
    shown, ngroups = compose.navaid_plan(view, frame, data)
    avoid_xy = [(X, Y) for _, X, Y in shown] + [p['xy'] for p in positions if p['inside']]
    scale_len = scale_plan(s)[0] * s
    scale_wh = (scale_len + 9.0, compose.SCALE_H)

    def cover(rc):
        a, b = int((rc[0] - fx) / FRAME_CELL), int((rc[2] - fx) / FRAME_CELL)
        c, d = int((rc[1] - fy) / FRAME_CELL), int((rc[3] - fy) / FRAME_CELL)
        return int(occ[max(c, 0):d, max(a, 0):b].sum())
    cn, rn, cs, rs = compose.choose_corners(frame, compose.NORTH_WH, scale_wh, cover, avoid_xy)
    logp(f'coins : nord {cn}, echelle {cs}')
    white = [(r[0] - 1, r[1] - 1, r[2] - r[0] + 2, r[3] - r[1] + 2) for r in (rn, rs)]       # rectangles blancs du nord et de l'echelle
    white_geoms = [box(a, b, a + w, b + h) for a, b, w, h in white]

    # ---- caps aux deux extremites (les rectangles blancs sont des obstacles : un cap ne passe pas dessous), balises
    nav_right = [compose.navaid_label_elems(gp['lines'], 'right', gp['xs'], gp['ys']) for gp in ngroups]
    cap_ends = []
    for e in data.ends:
        rw = data.runways[e.rw]
        az = math.radians(e.az_grid)
        cap_ends.append(CapEnd(f'{e.mag:05.1f}°', view.pt(*e.pos), (math.sin(az), -math.cos(az)), rw.width / 2 * s, e.az_grid))
    grad_specs = compose.graticule_specs(view, frame, data.projection)
    caps = place_caps(cap_ends, s, frame, runway_geoms, compose.navaid_geoms(shown, ngroups, nav_right), white_geoms,
                      grad_blockers([lab for _, lab in grad_specs if lab]))
    for ce, (cx, cy, rot, poly, moved) in zip(cap_ends, caps):
        doc.add('Runway bearings', with_halo(cap_element(ce.label, cx, cy, rot, BEARING_SIZE)))
    cap_polys = [c[3] for c in caps]
    nav_geoms, nav_info = compose.navaids(doc, view, frame, data, runway_geoms, cap_polys, halo=True)

    # ---- noms de taxiways (jeu ou OSM, comme la 10-9)
    placed, taxi_rects = compose.taxiway_labels(doc, data, view, frame, (rn, rs), cap_polys, [], logp)
    taxi_geoms = [elem_geom(r) for r in taxi_rects]

    # ---- cadre, graduation, nord, echelle (sur leurs rectangles blancs)
    nx0 = (rn[0] + rn[2]) / 2
    north_elems = north_block(nx0, rn[1] + 13.0, data.decl, data.year)
    sb, Lmm = scale_block(rs[0] + 1.0, rs[1] + 5.0, s)
    doc.add('Frame & grid', rect(fx, fy, fw, fh, 'none', INK, 0.35))
    grad_res = compose.graticule(doc, view, frame, data.projection,
                                 {'nord': [white_geoms[0]], 'echelle': [white_geoms[1]], 'cap': cap_polys,
                                  'balise': nav_geoms, 'taxi': taxi_geoms}, grad_specs, halo=True)
    doc.add('Frame & grid', rect(rn[0] - 1, rn[1] - 1, rn[2] - rn[0] + 2, rn[3] - rn[1] + 2, '#FFFFFF',
                                 extra='rx="1" fill-opacity="0.85"'))
    for e in north_elems:
        doc.add('Frame & grid', e)
    doc.add('Frame & grid', rect(rs[0] - 1, rs[1] - 1, rs[2] - rs[0] + 2, rs[3] - rs[1] + 2, '#FFFFFF',
                                 extra='rx="1" fill-opacity="0.85"'))
    for e in sb:
        doc.add('Frame & grid', e)

    # ---- villes : ce qu'une ville evite
    grad_geoms, lab_it = [], iter(grad_res)
    for ln, lab in grad_specs:
        grad_geoms.append(elem_geom(ln))
        if lab:
            x, y, _ = next(lab_it)
            if x is not None:
                r = text_rect(x, y, lab.s, GRAD_SIZE, 'regular', lab.anchor)
                grad_geoms.append(box(r[0] - 0.5, r[1] - 0.5, r[2] + 0.5, r[3] + 0.5))
    blockers = white_geoms + cap_polys + grad_geoms + list(nav_geoms) + taxi_geoms + runway_geoms
    A = view.pt(*data.arp)
    town_info = draw_towns(doc, positions, frame, A, blockers, log)

    # ---- tableau des pistes, legende, pied
    n_rows = len(data.ends)
    first_row = LAST_ROW_Y - (n_rows - 1) * ROW_PITCH
    head_y = first_row - 3.8
    rows_y = [first_row + i * ROW_PITCH for i in range(n_rows)]
    runway_table(doc, data, fy + fh, head_y, rows_y)
    legend(doc, LEGEND_Y)
    footer(doc, data, bool(data.osm_ways))
    info = {'scale': s, 'frame': frame, 'corners': (cn, cs), 'labels': placed, 'towns': town_info, 'positions': positions,
            'caps': [(ce.label, c[0], c[1], c[2]) for ce, c in zip(cap_ends, caps)], 'white': white,
            'navaid_positions': [(gp['lines'][0], p, free) for gp, p, free in nav_info]}
    return PageOut(CODE, doc.render(), info)
