"""Assemblage des pages d'un chart : 10-9, puis 10-9A, 10-9B... selon la regle du cadrage 4.5."""
import base64
import io
import math

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon

from . import directory, geo, layout, vector
from .fonts import CAP_HEIGHT, pt, text_width
from .page import (BEARING_SIZE, CapEnd, GradLabel, GRAD_SIZE, FOND_PX_MM, FOND_S_MAX, SYM_INSET_R, SYM_PLAN_R, CORNER_PAD, CORNER_PAD_Y, FRAME_CELL, FRAME_W, FRAME_X, FT, LAST_ROW_Y, LEGEND_Y, PAGE_H, PAGE_W, PLAN_MARGIN_M,
                   ROW_PITCH, X0, X1, PageOut, View, cells_from_mask, draw_scene, edge_ticks, fond_circles, fond_number_elems,
                   footer, header, legend, north_block, number_elems, rot_box, rw_axes, runway_table, runway_polygon, scale_block, scale_plan,
                   elem_geom, elems_geom, grad_element, navaid_groups, navaid_label_elems, place_caps, place_graduations, place_navaid_labels,
                   cap_element)
from .svgdoc import BAND, INK, INK2, TAXI, Doc, circle, f, line, rect, slug, text

NORTH_WH = (16.0, 20.0)
SCALE_H = 10.0
LABEL_H, LABEL_PAD = 3.0, 1.6
RW_EXT_M, RW_EXT_W = 120.0, 8.0
RUNWAY_FREE_M = 20.0
PARKING_FREE_MM = 2.5
NUM_SIZE = pt(6.0)


def ground_bbox(rings, parkings):
    xs = [r[:, 0] for r in rings] + [np.array([p.x for p in parkings])]
    zs = [r[:, 1] for r in rings] + [np.array([p.z for p in parkings])]
    xs, zs = np.concatenate(xs), np.concatenate(zs)
    return float(xs.min()), float(xs.max()), float(zs.min()), float(zs.max())


def plan_view_f10(data, frame):
    """Lot heliports-fond (4.2) : plan centre sur la boite des aires (centre +- longueur / 2) ; 1:5 000 au plus."""
    fx, fy, fw, fh = frame
    xs0 = [p.x - p.length / 2 for p in data.parkings]
    xs1 = [p.x + p.length / 2 for p in data.parkings]
    zs0 = [p.z - p.length / 2 for p in data.parkings]
    zs1 = [p.z + p.length / 2 for p in data.parkings]
    x0, x1, z0, z1 = min(xs0), max(xs1), min(zs0), max(zs1)
    s = min(FOND_S_MAX, fw / (z1 - z0 + 2 * PLAN_MARGIN_M), fh / (x1 - x0 + 2 * PLAN_MARGIN_M))
    return View(s, (x0 + x1) / 2, (z0 + z1) / 2, fx + fw / 2, fy + fh / 2)


def plan_view(data, frame):
    fx, fy, fw, fh = frame
    x0, x1, z0, z1 = ground_bbox(data.pav_rings, data.parkings)
    for rw in data.runways:                      # lot pistes-v1 : le plan montre les pistes en entier (extremites comprises)
        for px, pz in (rw.p0, rw.p1):
            x0, x1, z0, z1 = min(x0, px), max(x1, px), min(z0, pz), max(z1, pz)
    s = min(fw / (z1 - z0 + 2 * PLAN_MARGIN_M), fh / (x1 - x0 + 2 * PLAN_MARGIN_M))
    return View(s, (x0 + x1) / 2, (z0 + z1) / 2, fx + fw / 2, fy + fh / 2)


def fond_image(view, frame, data, soften=True):
    """Lot heliports-fond (4.3) : la mosaique (icone et nom effaces) sur le terrain couvert par le cadre a l'echelle du plan,
    reechantillonnee en bicubique a la resolution du PNG ; hors de la mosaique : gris (222, 222, 222) ; adoucie
    (directory.soften) sauf `soften=False`. Retourne une image PIL RGB."""
    fx, fy, fw, fh = frame
    g, img, s = data.georef, data.f10_img, view.s
    wpx, hpx = int(round(fw * FOND_PX_MM)), int(round(fh * FOND_PX_MM))
    zc_l, xc_t = view.zc - (fw / 2) / s, view.xc + (fh / 2) / s
    u0, u1 = (zc_l - g.z_left) / g.mpp, (zc_l + fw / s - g.z_left) / g.mpp
    v0, v1 = (g.x_top - xc_t) / g.mpp, (g.x_top - (xc_t - fh / s)) / g.mpp
    wc0, wc1 = int(math.floor(u0)) - 2, int(math.ceil(u1)) + 2
    wr0, wr1 = int(math.floor(v0)) - 2, int(math.ceil(v1)) + 2
    H, W = img.shape[:2]
    win = np.empty((wr1 - wr0, wc1 - wc0, 3), np.uint8)
    win[:] = (222, 222, 222)
    a0, a1, b0, b1 = max(wr0, 0), min(wr1, H), max(wc0, 0), min(wc1, W)
    if a1 > a0 and b1 > b0:
        win[a0 - wr0:a1 - wr0, b0 - wc0:b1 - wc0] = img[a0:a1, b0:b1]
    bg = Image.fromarray(win).resize((wpx, hpx), Image.BICUBIC, box=(u0 - wc0, v0 - wr0, u1 - wc0, v1 - wr0))
    return directory.soften(bg) if soften else bg


def fond_image_elem(view, frame, data):
    """L'unique <image> du calque `F10 map` : exactement le cadre, JPEG qualite 88 en data: base64."""
    fx, fy, fw, fh = frame
    buf = io.BytesIO()
    fond_image(view, frame, data).save(buf, 'JPEG', quality=88)
    href = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    return f'<image x="{f(fx)}" y="{f(fy)}" width="{f(fw)}" height="{f(fh)}" preserveAspectRatio="none" href="{href}" xlink:href="{href}"/>'


def poly_cells(poly_mm, origin, shape, grow_mm=0.0):
    """Rasterise un polygone (mm) dans une grille de cellules de CELL_MM ; grow_mm : epaississement."""
    img = np.zeros(shape, np.uint8)
    pts = np.array([[(x - origin[0]) / FRAME_CELL, (y - origin[1]) / FRAME_CELL] for x, y in poly_mm]) * 16
    cv2.fillPoly(img, [pts.astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    if grow_mm > 0:
        n = int(round(grow_mm / FRAME_CELL)) * 2 + 1
        img = cv2.dilate(img, cv2.getStructuringElement(cv2.MORPH_RECT, (n, n)))
    return img > 0


def corner_rects(frame, wh):
    fx, fy, fw, fh = frame
    w, h = wh
    px, py = CORNER_PAD, CORNER_PAD_Y
    return {'BL': (fx + px, fy + fh - py - h, fx + px + w, fy + fh - py),
            'BR': (fx + fw - px - w, fy + fh - py - h, fx + fw - px, fy + fh - py),
            'TL': (fx + px, fy + py, fx + px + w, fy + py + h),
            'TR': (fx + fw - px - w, fy + py, fx + fw - px, fy + py + h)}


def choose_corners(frame, north_wh, scale_wh, cover, park_xy):
    """Un coin pour le nord et un autre pour l'echelle : celui qui recouvre le moins de revetement et aucun parking ;
    deux blocs jamais dans le meme coin. cover(rect) -> surface occupee (cellules)."""
    fx, fy = frame[0], frame[1]
    cn, cs = corner_rects(frame, north_wh), corner_rects(frame, scale_wh)
    pref = ['BL', 'BR', 'TL', 'TR']

    def cost(rc):
        pad = 0.5
        for x, y in park_xy:
            if rc[0] - pad <= x <= rc[2] + pad and rc[1] - pad <= y <= rc[3] + pad:
                return math.inf
        return cover(rc)
    best = None
    for a in pref:
        for b in pref:
            if a == b:
                continue
            c = cost(cn[a]) + cost(cs[b])
            if best is None or c < best[0]:
                best = (c, a, b)
    _, a, b = best
    return a, cn[a], b, cs[b]


def graticule_specs(view, frame, proj):
    """Graduations lat/lon a la minute : [(trait (chaine SVG), libelle GradLabel ou None)] dans l'ordre d'ecriture (bord gauche,
    bord droit sans libelle, haut, bas)."""
    fx, fy, fw, fh = frame
    left, right, top, bot = edge_ticks(view, frame, proj)
    size = GRAD_SIZE
    tl = 1.8
    out = []
    for y, lab in left:
        out.append((line(fx, y, fx + tl, y, INK, 0.3), GradLabel(fx + tl + 0.6, y + size * CAP_HEIGHT / 2, lab, 'start')))
    for y, lab in right:
        out.append((line(fx + fw, y, fx + fw - tl, y, INK, 0.3), None))
    for x, lab in top:
        out.append((line(x, fy, x, fy + tl, INK, 0.3), GradLabel(x, fy + tl + 0.6 + size * CAP_HEIGHT, lab, 'middle')))
    for x, lab in bot:
        out.append((line(x, fy + fh, x, fy + fh - tl, INK, 0.3), GradLabel(x, fy + fh - tl - 0.6, lab, 'middle')))
    return out


def graticule(doc, view, frame, proj, obstacles=None):
    """Traits et libelles des graduations ; les libelles passent par `page.place_graduations` (cadrage 2.3). Retourne la liste
    des resultats (un par libelle) pour le resume du moteur."""
    L = 'Frame & grid'
    specs = graticule_specs(view, frame, proj)
    res = place_graduations([lab for _, lab in specs if lab], frame, obstacles or {})
    it = iter(res)
    for ln, lab in specs:
        doc.add(L, ln)
        if lab:
            x, y, reason = next(it)
            if x is not None:
                doc.add(L, grad_element(lab, x, y))
    return res


def navaid_plan(view, frame, data):
    """Balises du plan : ([(balise, X, Y)], groupes). Les balises dont les symboles sont a 6 mm au plus sur la page (de proche
    en proche) forment un groupe qui n'a qu'une etiquette (lot balises, addendum 1) : ligne 1 : les types (puis l'indicatif
    s'il est commun), ligne 2 : les textes, joints par ' · '. Chaque groupe : {'members', 'lines', 'xs', 'ys'}."""
    fx, fy, fw, fh = frame
    shown = []
    for b in data.beacons:
        X, Y = view.pt(b.x, b.z)
        if fx + 1.5 < X < fx + fw - 1.5 and fy + 1.5 < Y < fy + fh - 1.5:
            shown.append((b, X, Y))
    groups = []
    for grp in navaid_groups([(X, Y) for _, X, Y in shown]):
        gb = [shown[k][0] for k in grp]
        b = gb[0]
        if len(gb) == 1:
            l1, l2 = f'{b.kind} {b.callsign}', b.text
        else:
            calls = {x.callsign for x in gb}
            if len(calls) == 1:
                l1 = ' · '.join(x.kind for x in gb) + f' {b.callsign}'
            else:
                l1 = ' · '.join(f'{x.kind} {x.callsign}' for x in gb)
            l2 = ' · '.join(x.text for x in gb)
        groups.append({'members': grp, 'lines': (l1, l2), 'xs': [shown[k][1] for k in grp], 'ys': [shown[k][2] for k in grp]})
    return shown, groups


def navaid_symbol_elems(kind, X, Y):
    """Symbole d'une balise : cercle pointille (NDB) ou hexagone (les autres), et son point central."""
    if kind == 'NDB':
        return [circle(X, Y, 1.35, '#FFFFFF', INK, 0.3, 'stroke-dasharray="0.5 0.5"'), circle(X, Y, 0.3, INK)]
    r = 1.5
    pts = ' '.join(f'{f(X + r * math.cos(math.radians(60 * k)))},{f(Y + r * math.sin(math.radians(60 * k)))}' for k in range(6))
    return [f'<polygon points="{pts}" fill="#FFFFFF" stroke="{INK}" stroke-width="0.3"/>', circle(X, Y, 0.3, INK)]


def navaid_geoms(shown, groups, label_elems):
    """Geometries (symboles, puis libelles) ; label_elems : liste, un element par groupe, de [ligne 1, ligne 2]."""
    syms = [elems_geom(navaid_symbol_elems(b.kind, X, Y)) for b, X, Y in shown]
    return syms + [elems_geom(e) for e in label_elems]


def navaids(doc, view, frame, data, runways=(), caps=()):
    """Symboles et libelles des balises du plan ; les libelles passent par `page.place_navaid_labels` (cadrage 2.2) : a droite
    du symbole, ou a gauche, dessous, dessus s'ils touchent une piste (`runways`) ou un cap (`caps`, geometries shapely).
    Retourne (geometries des symboles et des libelles, [(groupe, position, libre)])."""
    L = 'Navaids'
    shown, groups = navaid_plan(view, frame, data)
    placed = place_navaid_labels([(g['lines'], g['xs'], g['ys']) for g in groups], frame, runways, caps)
    first = {g['members'][0]: k for k, g in enumerate(groups)}
    for i, (b, X, Y) in enumerate(shown):
        for e in navaid_symbol_elems(b.kind, X, Y):
            doc.add(L, e)
        if i in first:
            for e in placed[first[i]][1]:
                doc.add(L, e)
    return navaid_geoms(shown, groups, [p[1] for p in placed]), [(g, p[0], p[2]) for g, p in zip(groups, placed)]


def inset_cover_elem(box):
    """Le fond blanc d'un encart : cadre et titre (ce que l'encart masque sur le plan)."""
    return rect(box.x - 0.3, box.y - layout.TITLE_MM, box.w + 0.6, box.total_h + 0.3, '#FFFFFF')


def draw_inset(doc, box, data, parkings_all, clip_prefix='clip-inset'):
    """Sous-calque d'un encart : fond, contenu decoupe, cadre, titre."""
    z = box.zone
    g = data.georef
    xc, zc = z.center
    view = View(box.s, xc, zc, box.x + box.w / 2, box.y + box.h / 2)
    half_x, half_z = box.h / 2 / box.s + 2.0, box.w / 2 / box.s + 2.0
    r0 = int(max(0, math.floor((g.x_top - (xc + half_x)) / g.mpp)))
    r1 = int(min(g.height, math.ceil((g.x_top - (xc - half_x)) / g.mpp)))
    c0 = int(max(0, math.floor((zc - half_z - g.z_left) / g.mpp)))
    c1 = int(min(g.width, math.ceil((zc + half_z - g.z_left) / g.mpp)))
    rings_p = rings_b = []
    if r1 > r0 and c1 > c0:
        rings_p = vector.mask_rings(data.masks['pav'], g, vector.PAVEMENT_SIMPLIFY_M, (r0, r1, c0, c1))
        rings_b = vector.mask_rings(data.masks['bld'], g, vector.BUILDING_SIMPLIFY_M, (r0, r1, c0, c1))
    members = [parkings_all[i] for i in z.members]
    lay = {k: [] for k in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Parking')}
    draw_scene(view, data, rings_p, rings_b, members, lay, sym_r=SYM_INSET_R, mark_w=0.35)
    nums, _ = number_elems(members, view, NUM_SIZE, SYM_INSET_R,
                           clip=(box.x, box.y, box.x + box.w, box.y + box.h))
    cid = f'{clip_prefix}-{z.label}'
    doc.defs.append(f'<clipPath id="{cid}"><rect x="{f(box.x)}" y="{f(box.y)}" width="{f(box.w)}" height="{f(box.h)}"/></clipPath>')
    ident = f'layer-inset-{z.label.lower()}'
    out = [f'<g inkscape:groupmode="layer" id="{ident}" inkscape:label="{z.label}">',
           inset_cover_elem(box),
           f'<g clip-path="url(#{cid})">']
    for k in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Parking'):
        out.extend(lay[k])
    out.extend(nums)
    out.append('</g>')
    out.append(rect(box.x, box.y, box.w, box.h, 'none', INK, 0.35))
    out.append(text(box.x, box.y - 0.9, z.title, pt(6.4), 'bold'))
    out.append(text(box.x + box.w, box.y - 0.9, layout.ratio_text(box.s), pt(5.4), 'regular', INK2, 'end'))
    out.append('</g>')
    doc.add('Insets', '\n'.join(out))
    return len(members)


def build_pages(data, log=print):
    g = data.georef
    parkings = data.parkings
    zones = layout.make_zones(parkings)
    osm = bool(data.osm_ways)

    # ---- page 10-9 : cadre
    doc = Doc()
    doc.add('Background', rect(0, 0, PAGE_W, PAGE_H, '#FFFFFF'))
    bottom = header(doc, data, '10-9', "BASE D'HÉLICOPTÈRES" if data.heli_mode else 'AÉRODROME', bands=True)
    n_rows = len(data.ends)
    first_row = LAST_ROW_Y - (n_rows - 1) * ROW_PITCH
    head_y = first_row - 3.8
    frame_bottom = head_y - 3.7
    frame_top = bottom + 2.9
    nx = int(FRAME_W / FRAME_CELL)
    ny = int((frame_bottom - frame_top) / FRAME_CELL)
    frame = (FRAME_X, frame_top, FRAME_W, ny * FRAME_CELL)
    fx, fy, fw, fh = frame
    doc.add_clip('clip-plan', fx, fy, fw, fh)
    view = plan_view_f10(data, frame) if data.on_f10 else plan_view(data, frame)
    s = view.s
    log(f'plan : echelle {s:.5f} mm/m (1:{1000 / s:,.0f}), cadre {fw:.1f} x {fh:.1f} mm')

    # ---- occupation
    x_hi, z_lo = view.ground(fx, fy)
    x_lo, z_hi = view.ground(fx + fw, fy + fh)
    fg = (x_hi, x_lo, z_lo, z_hi)
    pav_c = cells_from_mask(data.masks['pav'], g, fg, nx, ny)
    bld_c = cells_from_mask(data.masks['bld'], g, fg, nx, ny)
    origin = (fx, fy)
    occ = pav_c | bld_c
    park_xy = [view.pt(p.x, p.z) for p in parkings]
    for rw in data.runways:
        poly = [view.pt(*q) for q in runway_polygon(rw, RUNWAY_FREE_M)]
        occ |= poly_cells(poly, origin, occ.shape)
    pk = np.zeros(occ.shape, np.uint8)
    for X, Y in park_xy:
        cx, cy = int((X - fx) / FRAME_CELL), int((Y - fy) / FRAME_CELL)
        if 0 <= cx < nx and 0 <= cy < ny:
            cv2.circle(pk, (cx, cy), int(round(PARKING_FREE_MM / FRAME_CELL)), 1, -1)
    occ |= pk > 0

    # ---- coins : nord et echelle
    scale_len = scale_plan(s)[0] * s                # lot heliports (4.3) : la barre de moins de 500 m a son propre pas
    scale_wh = (scale_len + 9.0, SCALE_H)

    def cover(rc):
        a, b = int((rc[0] - fx) / FRAME_CELL), int((rc[2] - fx) / FRAME_CELL)
        c, d = int((rc[1] - fy) / FRAME_CELL), int((rc[3] - fy) / FRAME_CELL)
        return int((pav_c | bld_c)[max(c, 0):d, max(a, 0):b].sum())
    cn, rn, cs, rs = choose_corners(frame, NORTH_WH, scale_wh, cover, park_xy)
    for rc in (rn, rs):
        a, b = int((rc[0] - fx) / FRAME_CELL), int(math.ceil((rc[2] - fx) / FRAME_CELL))
        c, d = int((rc[1] - fy) / FRAME_CELL), int(math.ceil((rc[3] - fy) / FRAME_CELL))
        occ[max(c, 0):d, max(a, 0):b] = True
    log(f'coins : nord {cn}, echelle {cs}')

    # ---- regle des pages
    def zone_center_mm(z):
        return view.pt(*z.center)
    if data.on_f10:                                  # lot heliports-fond (4.1) : une seule page, ni encarts ni pages de parkings
        decision = {'mode': 1}
        mode = 1
        log(f'{len(zones)} zones ' + ', '.join(f'{z.label}:{z.count}' for z in zones) + ' -> regle 1 (fond F10, forcee)')
    else:
        decision = layout.decide_mode(zones, s, occ, origin, frame, zone_center_mm)
        mode = decision['mode']
        log(f'{len(zones)} zones ' + ', '.join(f'{z.label}:{z.count}' for z in zones) + f' -> regle {mode}'
            + (f' (f={decision["f"]})' if 'f' in decision else '')
            + (' encarts reduits : ' + ', '.join(f'{lab} k={k:.2f}' for lab, k in decision['reduced'].items())
               if decision.get('reduced') else ''))

    # ---- plan
    lay = {k: [] for k in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Parking')}
    scene_boxes = []
    if data.on_f10:                                  # lot heliports-fond (4.3, 4.4) : le fond remplace revetement et batiments
        doc.add('F10 map', fond_image_elem(view, frame, data))
        fond_circles(view, parkings, lay)
    else:
        draw_scene(view, data, data.pav_rings, data.bld_rings, parkings, lay, boxes=scene_boxes)
    for k, v in lay.items():
        for e in v:
            doc.add(k, e)
    n_numbers = 0
    if mode == 1:
        if data.on_f10:
            nums, n_numbers = fond_number_elems(parkings, view, NUM_SIZE)
        else:
            nums, n_numbers = number_elems(parkings, view, NUM_SIZE, SYM_PLAN_R, clip=None)
        for e in nums:
            doc.add('Parking numbers', e)
    # ---- caps de piste (cadrage 2.1), puis libelles de balises (2.2) : les deux voient les pistes, les encarts et les balises
    runway_geoms = [elem_geom(e) for e in lay['Runways']]
    inset_covers = [elem_geom(inset_cover_elem(b)) for b in decision['boxes']] if mode == 2 else []
    shown, ngroups = navaid_plan(view, frame, data)
    nav_right = [navaid_label_elems(g['lines'], 'right', g['xs'], g['ys']) for g in ngroups]
    cap_ends = []
    for e in data.ends:
        rw = data.runways[e.rw]
        az = math.radians(e.az_grid)
        cap_ends.append(CapEnd(f'{e.mag:05.1f}°', view.pt(*e.pos), (math.sin(az), -math.cos(az)), rw.width / 2 * s, e.az_grid))
    caps = place_caps(cap_ends, s, frame, runway_geoms, navaid_geoms(shown, ngroups, nav_right), inset_covers)
    bear_boxes = []
    for e, ce, (cx, cy, rot, poly, moved) in zip(data.ends, cap_ends, caps):
        doc.add('Runway bearings', cap_element(ce.label, cx, cy, rot, BEARING_SIZE))
        bear_boxes.append((e.name, rot_box(cx, cy, text_width(ce.label, 'bold', BEARING_SIZE), BEARING_SIZE * CAP_HEIGHT, rot)))
    cap_polys = [c[3] for c in caps]
    nav_geoms, nav_info = navaids(doc, view, frame, data, runway_geoms, cap_polys)

    # ---- encarts sur le plan
    inset_rects = []
    if mode == 2:
        for b in decision['boxes']:
            n_numbers += draw_inset(doc, b, data, parkings)
            inset_rects.append((b.x - 0.3, b.y - layout.TITLE_MM - 0.3, b.x + b.w + 0.3, b.y + b.h + 0.3))
        for z in zones:
            X, Y = view.pt(*z.center)
            doc.add('Insets', circle(X, Y, 2.5, '#FFFFFF', INK, 0.3))
            doc.add('Insets', text(X, Y + pt(6.5) * CAP_HEIGHT / 2, z.label, pt(6.5), 'bold', INK, 'middle'))

    # ---- etiquettes de taxiways
    placed = {}
    taxi_rects = []
    if data.osm_ways:
        size = pt(6.0)
        ways = []
        for w in data.osm_ways:
            ways.append((w['ref'], view.pts(w['pts_xz']), w['length']))
        forbidden = [(r[0] - 1.5, r[1] - 1.5, r[2] + 1.5, r[3] + 1.5) for r in (rn, rs)] + inset_rects
        for rw in data.runways:
            u, n, L = rw_axes(rw)
            P0, P1 = np.array(rw.p0) - u * RW_EXT_M, np.array(rw.p1) + u * RW_EXT_M
            h = rw.width / 2 + RW_EXT_W / 2
            forbidden.append(Polygon([view.pt(*q) for q in (P0 + n * h, P1 + n * h, P1 - n * h, P0 - n * h)]))
        bounds = (fx + 0.5, fy + 0.5, fx + fw - 0.5, fy + fh - 0.5)
        placed = layout.place_labels(ways, lambda nm: (text_width(nm, 'bold', size) + LABEL_PAD, LABEL_H), forbidden,
                                     park_xy, 0.6, bounds)
        for nm, (cx, cy) in placed.items():
            w = text_width(nm, 'bold', size) + LABEL_PAD
            taxi_rects.append(rect(cx - w / 2, cy - LABEL_H / 2, w, LABEL_H, TAXI, INK, 0.2))
            doc.add('Taxiway labels', taxi_rects[-1])
            doc.add('Taxiway labels', text(cx, cy + size * CAP_HEIGHT / 2, nm, size, 'bold', INK, 'middle'))
        log(f'taxiways OSM gardes : {data.osm_kept}')
        log(f'etiquettes placees : {len(placed)}/{len(data.osm_kept)}')
    else:
        log('taxiways OSM gardes : []')

    # ---- cadre, grille, nord, echelle
    nx0 = (rn[0] + rn[2]) / 2
    north_elems = north_block(nx0, rn[1] + 13.0 if cn[0] == 'B' else rn[1] + 13.0, data.conv, data.decl, data.year)
    sb, Lmm = scale_block(rs[0] + 1.0, rs[1] + 5.0, s)
    doc.add('Frame & grid', rect(fx, fy, fw, fh, 'none', INK, 0.35))
    grad_res = graticule(doc, view, frame, data.projection,
                         {'encart': inset_covers, 'nord': [elems_geom(north_elems)], 'echelle': [elems_geom(sb)], 'cap': cap_polys,
                          'balise': nav_geoms, 'taxi': [elem_geom(r) for r in taxi_rects]})
    if data.on_f10:                                  # lot heliports-fond (4.5) : un rectangle blanc sous chacun des deux blocs
        doc.add('Frame & grid', rect(rn[0] - 1, rn[1] - 1, rn[2] - rn[0] + 2, rn[3] - rn[1] + 2, '#FFFFFF',
                                     extra='rx="1" fill-opacity="0.85"'))
    for e in north_elems:
        doc.add('Frame & grid', e)
    if data.on_f10:
        doc.add('Frame & grid', rect(rs[0] - 1, rs[1] - 1, rs[2] - rs[0] + 2, rs[3] - rs[1] + 2, '#FFFFFF',
                                     extra='rx="1" fill-opacity="0.85"'))
    for e in sb:
        doc.add('Frame & grid', e)

    # ---- tableau, legende, pied
    rows_y = [first_row + i * ROW_PITCH for i in range(n_rows)]
    runway_table(doc, data, frame_bottom, head_y, rows_y)
    legend(doc, LEGEND_Y, x_right=X1, heli=data.heli_mode, fond=data.on_f10)
    footer(doc, data, osm)

    pages = [PageOut('10-9', doc.render(), {'mode': mode, 'zones': len(zones), 'labels': placed, 'n_numbers': n_numbers,
                                          'scale': s, 'frame': frame, 'f': decision.get('f'),
                                          'reduced': decision.get('reduced', {}), 'inset_rects': inset_rects,
                                          'bearing_boxes': bear_boxes, 'bearings_moved': [e.name for e, c in zip(data.ends, caps) if c[4]],
                                          'navaid_positions': [(g['lines'][0], p, free) for g, p, free in nav_info],
                                          'graduations': [(r[0] is None, r[2]) for r in grad_res], 'runway_polys': [b for k, b in scene_boxes if k == 'runway'],
                                          'designator_polys': [b for k, b in scene_boxes if k == 'designator']})]

    # ---- pages de parkings
    if mode == 3:
        for k, pg in enumerate(decision['pages']):
            letter = chr(ord('A') + k)
            d2 = Doc()
            d2.add('Background', rect(0, 0, PAGE_W, PAGE_H, '#FFFFFF'))
            header(d2, data, f'10-9{letter}', 'PARKINGS', bands=False)
            n = 0
            for b in pg:
                n += draw_inset(d2, b, data, parkings)
            legend(d2, 199.3, x_left=X0, note='NUMÉROS DE PARKING TELS QUE DANS DCS', sym_r=SYM_INSET_R, heli=data.heli_mode)
            footer(d2, data, False)
            pages.append(PageOut(f'10-9{letter}', d2.render(), {'insets': [b.zone.label for b in pg], 'n_numbers': n,
                                                              'k': {b.zone.label: b.k for b in pg}}))
    return pages
