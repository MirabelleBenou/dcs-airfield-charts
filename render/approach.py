"""Pages d'approche `11-1`, `11-2`... (lot ac-approche-ils) : une page A5 par extremite de piste equipee d'un ILS dans DCS.

Ce n'est pas une carte d'approche reelle : DCS ne donne ni procedure ni minima. La page montre l'ILS tel que `beacons.lua` le
decrit (localizer, antenne de glide), la vue en plan de l'approche finale sur 10 NM (nord de la grille en haut), le profil d'un
plan de 3 degres suppose et le tableau distance / altitude, avec les distances lues sur le DME le plus proche du seuil.
Les antennes viennent de `ChartData.ils_antennas` (`analysis.ils_antenna_info`), le cap de `End.mag` (cap du compas du cockpit).
"""
import math

from shapely.geometry import box

from . import compose, dcsdata, geo, page
from .fonts import CAP_HEIGHT, pt, text_width
from .page import PageOut, View, X0, X1
from .svgdoc import BAND, INK, INK2, Doc, circle, f, line, rect, text

NM = 1852.0                       # metres par mille nautique
SLOPE_DEG = 3.0
TAN_SLOPE = math.tan(math.radians(SLOPE_DEG))
FT = page.FT
LOC_MIN_MHZ, LOC_MAX_MHZ = 108.10, 111.95      # bande des localizers (2.1)
RED = page.AIRE_RED
DME_KINDS = ('VOR/DME', 'TACAN', 'VORTAC', 'DME')

BAND_H = 5.2
ILS_BAND_Y, BAL_BAND_Y = 23.8, 30.0            # memes ordonnees que les bandeaux de `page.header`
BAND_TEXT_X, PAIRS_X = 7.5, 30.0
PLAN_BOTTOM, PLAN_MARGIN = 121.0, 6.0
AXIS_NM = 10.5
SCALE_BAR_NM = 2
PROFILE_TOP, PROFILE_BOTTOM = 125.0, 170.0
PROFILE_AXIS_Y, PROFILE_TOP_PT_Y = 162.0, 134.0
PROFILE_X_THR, PROFILE_PITCH, PROFILE_X_END = 128.0, 11.2, 139.0
TABLE_Y = 173.0
COL_X0, COL_PITCH = 35.5, 11.0
ROW_Y = (182.4, 188.4)


def nm_text(metres):
    """Distance en NM, au dixieme, virgule decimale."""
    return f'{metres / NM:.1f}'.replace('.', ',')


def round_to(v, step):
    return int(math.floor(v / step + 0.5)) * step


def altitude_ft(thr_ft, d_nm, s_gs):
    """2.3 : altitude du plan de 3 degres a `d_nm` NM avant le seuil, depuis l'antenne de glide a `s_gs` m apres le seuil."""
    return thr_ft + (d_nm * NM + s_gs) * TAN_SLOPE * FT


def tch_ft(s_gs):
    return s_gs * TAN_SLOPE * FT


def glide_offset_m(e, gs):
    """2.3 : distance de l'antenne de glide au seuil, le long de l'axe d'atterrissage (m, positive apres le seuil)."""
    az = math.radians(e.az_grid)
    return (gs[0] - e.pos[0]) * math.cos(az) + (gs[1] - e.pos[1]) * math.sin(az)


def pick_glide(e, candidates, L):
    """2.1 : parmi les antennes de glide `(x, z, freq_hz)` a la frequence du localizer, celle de l'extremite `e` (piste de `L` m) :
    celles dont l'abscisse `s_gs` est dans [0 ; L / 2] (un glide est a l'extremite de sa piste : les deux extremites opposees ne
    se prennent pas le glide l'une de l'autre), puis la plus proche de l'axe de la piste (distance perpendiculaire).
    Retourne (antenne choisie ou None, s_gs, distance a l'axe) ; s'il n'y en a pas dans la premiere moitie de la piste, l'antenne la plus proche de
    l'axe parmi toutes (pour le journal), None si aucun candidat."""
    az = math.radians(e.az_grid)
    scored = []
    for c in candidates:
        s_gs = glide_offset_m(e, c)
        perp = abs(-(c[0] - e.pos[0]) * math.sin(az) + (c[1] - e.pos[1]) * math.cos(az))
        scored.append((perp, s_gs, c))
    if not scored:
        return None, None, None
    inside = [t for t in scored if 0.0 <= t[1] <= L / 2.0]
    perp, s_gs, c = min(inside or scored, key=lambda t: t[0])
    return (c if inside else None), s_gs, perp


def eligible_ends(data, log=print):
    """2.1 : [(extremite, antennes, s_gs)] des extremites qui ont une page, dans l'ordre naturel des noms ; les ILS ecartes ont
    une ligne au journal `approche <extremite> : écartée, <raison>`. Les antennes rendues portent le glide choisi
    (`gs`, `gs_freq_hz`)."""
    out = []
    for e in sorted((e for e in data.ends if e.ils), key=lambda e: dcsdata.natural_key(e.name)):
        ant = data.ils_antennas.get(e.name)
        if e.ils.endswith(' (LOC)') or ant is None or not ant.get('gs_candidates'):
            log(f'approche {e.name} : écartée, pas de glide (ILS {e.ils})')
            continue
        mhz = ant['freq_hz'] / 1e6
        if not (LOC_MIN_MHZ - 1e-6 <= mhz <= LOC_MAX_MHZ + 1e-6):
            log(f'approche {e.name} : écartée, fréquence du localizer {mhz:.2f} MHz hors de 108,10 à 111,95 MHz')
            continue
        L = page.rw_axes(data.runways[e.rw])[2]
        gs, s_gs, _ = pick_glide(e, ant['gs_candidates'], L)
        if gs is None:
            log(f'approche {e.name} : écartée, antenne de glide hors de la première moitié de la piste ({s_gs:.0f} m après le seuil, piste de {L:.0f} m)')
            continue
        if e.thr_elev_ft is None:
            log(f'approche {e.name} : écartée, altitude du seuil inconnue')
            continue
        out.append((e, dict(ant, gs=(gs[0], gs[1]), gs_freq_hz=gs[2]), s_gs))
    return out


def dme_of(data, e):
    """2.3 : la balise DME (parmi `data.beacons`) la plus proche du seuil, ou None."""
    cands = [b for b in data.beacons if b.kind in DME_KINDS]
    if not cands:
        return None
    return min(cands, key=lambda b: math.hypot(b.x - e.pos[0], b.z - e.pos[1]))


def rot_text(s, cx, cy, rot, size, weight, fill, extra=''):
    """Texte centre en (cx, cy), tourne de `rot` degres (ligne de base le long de `rot`)."""
    return (f'<g transform="translate({f(cx)} {f(cy)}) rotate({f(rot, 2)})">'
            + text(0, size * CAP_HEIGHT / 2, s, size, weight, fill, 'middle', extra=extra) + '</g>')


def support(w, h, nx, ny):
    """Demi-etendue d'un rectangle w x h (aux axes de la page) selon la direction unitaire (nx, ny)."""
    return abs(nx) * w / 2 + abs(ny) * h / 2


def place_small_label(s, size, X, Y, ext, obstacles, frame, weight='regular'):
    """Libelle d'une antenne (`LOC`, `GS`) : a droite du symbole (de demi-taille `ext`), sinon au-dessus, a gauche, dessous, puis
    en diagonale : la premiere place qui est dans le cadre et ne touche aucun obstacle. Retourne (element, geometrie, libre)."""
    gap = 1.0
    h = size * CAP_HEIGHT
    fx, fy, fw, fh = frame
    inside = box(fx + 0.5, fy + 0.5, fx + fw - 0.5, fy + fh - 0.5)
    d = ext * 0.8 + gap * 0.8
    cands = [('start', X + ext + gap, Y + h / 2), ('middle', X, Y - ext - gap), ('end', X - ext - gap, Y + h / 2),
             ('middle', X, Y + ext + gap + h), ('start', X + d, Y - d), ('start', X + d, Y + d + h),
             ('end', X - d, Y - d), ('end', X - d, Y + d + h)]
    first = None
    for anchor, x, y in cands:
        el = text(x, y, s, size, weight, INK2, anchor)
        g = page.elem_geom(el)
        if first is None:
            first = (el, g)
        if inside.contains(g) and not any(g.intersects(o) for o in obstacles):
            return el, g, True
    return first[0], first[1], False


def build_page(data, e, ant, s_gs, n, log=print):
    """La page `11-<n>` de l'extremite `e` : PageOut."""
    rw = data.runways[e.rw]
    az = math.radians(e.az_grid)
    ug = (math.cos(az), math.sin(az))                    # sens d'atterrissage, repere du jeu (x nord, z est)
    T = e.pos
    far = rw.p1 if math.hypot(rw.p1[0] - T[0], rw.p1[1] - T[1]) > math.hypot(rw.p0[0] - T[0], rw.p0[1] - T[1]) else rw.p0
    before = lambda d_nm: (T[0] - ug[0] * d_nm * NM, T[1] - ug[1] * d_nm * NM)       # point de l'axe a d NM avant le seuil
    thr_ft = e.thr_elev_ft
    dme = dme_of(data, e)
    cap = round(e.mag) % 360
    callsign = ant['callsign']
    loc_text = f'{callsign} {ant["freq_hz"] / 1e6:.2f}'
    tch = round_to(tch_ft(s_gs), 5)

    doc = Doc()
    doc.add('Background', rect(0, 0, page.PAGE_W, page.PAGE_H, '#FFFFFF'))
    page.header(doc, data, f'11-{n}', f'ILS {e.name}', bands=False)

    # ---- bandeaux
    L = 'Header'
    doc.add(L, rect(X0, ILS_BAND_Y, X1 - X0, BAND_H, BAND))
    doc.add(L, text(BAND_TEXT_X, 27.4, f'ILS {e.name}', pt(7.6), 'bold'))
    thr_s = f'{thr_ft} FT' if thr_ft is not None else '—'
    ils_pairs = [('LOC', loc_text), ('CAP', f'{cap:03d}°'), ('SEUIL', thr_s), ('PENTE', f'{int(SLOPE_DEG)},0°'),
                 ('TCH', f'{tch} FT')]
    page.pair_row(doc, L, ils_pairs, PAIRS_X, 27.4, X1 - PAIRS_X - 1.5)
    band_bottom = ILS_BAND_Y + BAND_H
    bal_pairs = [(b.kind, f'{b.callsign} {b.text} · {nm_text(math.hypot(b.x - T[0], b.z - T[1]))} NM du seuil') for b in data.beacons]
    if bal_pairs:
        doc.add(L, rect(X0, BAL_BAND_Y, X1 - X0, BAND_H, BAND))
        doc.add(L, text(BAND_TEXT_X, 33.6, 'BALISES', pt(7.6), 'bold'))
        page.pair_row(doc, L, bal_pairs, PAIRS_X, 33.6, X1 - PAIRS_X - 1.5)
        band_bottom = BAL_BAND_Y + BAND_H

    # ---- vue en plan : cadre et echelle
    fx, fy = X0, band_bottom + 2.0
    fw, fh = X1 - X0, PLAN_BOTTOM - fy
    frame = (fx, fy, fw, fh)
    pts = [T, before(AXIS_NM), rw.p0, rw.p1, ant['loc']] + [(b.x, b.z) for b in data.beacons]
    xmin, xmax = min(p[0] for p in pts), max(p[0] for p in pts)
    zmin, zmax = min(p[1] for p in pts), max(p[1] for p in pts)
    s = min((fw - 2 * PLAN_MARGIN) / max(zmax - zmin, 1e-6), (fh - 2 * PLAN_MARGIN) / max(xmax - xmin, 1e-6))
    view = View(s, (xmin + xmax) / 2, (zmin + zmax) / 2, fx + fw / 2, fy + fh / 2)
    doc.add_clip('clip-plan', fx, fy, fw, fh)
    log(f'approche {e.name} : echelle {s:.5f} mm/m (1 NM = {NM * s:.2f} mm)')
    P = lambda p: view.pt(*p)
    dX, dY = math.sin(az), -math.cos(az)                  # sens d'atterrissage sur la page
    rX, rY = -dY, dX                                      # a droite du sens d'approche
    lX, lY = -rX, -rY                                     # a gauche
    obstacles = []

    def add(layer, el, obstacle=True):
        doc.add(layer, el)
        if obstacle:
            obstacles.append(page.elem_geom(el))

    # piste : l'axe, a la largeur de la piste a l'echelle (0,8 mm au moins)
    a, b = P(T), P(far)
    add('Runways', line(a[0], a[1], b[0], b[1], INK, max(0.8, rw.width * s)))
    # axe prolonge, repere a chaque NM
    p10 = P(before(AXIS_NM))
    add('Runway markings', line(a[0], a[1], p10[0], p10[1], INK, 0.35, 'stroke-dasharray="1.6 0.8"'))
    num_size = pt(5.6)
    for k in range(1, 11):
        c = P(before(k))
        h = 1.6 if k % 5 == 0 else 1.0
        add('Runway markings', line(c[0] - rX * h, c[1] - rY * h, c[0] + rX * h, c[1] + rY * h, INK, 0.35))
        if k % 2 == 0:
            tw, th = text_width(str(k), 'regular', num_size), num_size * CAP_HEIGHT
            off = 1.6 + 0.9 + support(tw, th, rX, rY)
            cx, cy = c[0] + rX * off, c[1] + rY * off
            add('Runway markings', text(cx, cy + th / 2, str(k), num_size, 'regular', INK2, 'middle'))
    # fleche rouge de 7 a 6 NM et son libelle, a gauche du sens d'approche
    tail, tip = P(before(7.0)), P(before(6.0))
    hl, hw = 2.4, 1.0
    base = (tip[0] - dX * hl, tip[1] - dY * hl)
    add('Runway bearings', line(tail[0], tail[1], base[0] + dX * 0.4, base[1] + dY * 0.4, RED, 0.6))
    add('Runway bearings', f'<polygon points="{f(tip[0])},{f(tip[1])} {f(base[0] + rX * hw)},{f(base[1] + rY * hw)} '
                           f'{f(base[0] - rX * hw)},{f(base[1] - rY * hw)}" fill="{RED}"/>')
    sp_size = pt(7.2)
    sp_text = f'{cap:03d}°  {callsign} {ant["freq_hz"] / 1e6:.2f}'
    mid = ((tail[0] + tip[0]) / 2, (tail[1] + tip[1]) / 2)
    off = sp_size * CAP_HEIGHT / 2 + 2.0
    add('Runway bearings', rot_text(sp_text, mid[0] + lX * off, mid[1] + lY * off, page.cap_norm(e.az_grid - 90.0), sp_size, 'bold', RED,
                                    'xml:space="preserve"'))
    # echelle (2 NM, trois marques) et mention du nord, en bas du cadre
    bar_y = PLAN_BOTTOM - 3.0
    bx0 = fx + 4.0
    bar_len = SCALE_BAR_NM * NM * s
    add('Frame & grid', line(bx0, bar_y, bx0 + bar_len, bar_y, INK, 0.35))
    for k in range(SCALE_BAR_NM + 1):
        xk = bx0 + k * NM * s
        add('Frame & grid', line(xk, bar_y - 1.2, xk, bar_y + 1.2, INK, 0.3))
    sc_size = pt(5.6)
    add('Frame & grid', text(bx0 + bar_len + 1.5, bar_y + sc_size * CAP_HEIGHT / 2, f'{SCALE_BAR_NM} NM', sc_size, 'regular', INK2))
    add('Frame & grid', text(fx + fw - 2.9, bar_y + sc_size * CAP_HEIGHT / 2, 'NORD GRILLE (NORD DU JEU) EN HAUT', pt(5.2), 'regular', INK2, 'end'))
    # antennes : localizer (carre blanc de 2 mm) et glide (cercle blanc de 0,8 mm de rayon)
    lp, gp = P(ant['loc']), P(ant['gs'])
    add('Runway bearings', rect(lp[0] - 1.0, lp[1] - 1.0, 2.0, 2.0, '#FFFFFF', INK, 0.35))        # au-dessus des symboles de balise (calque Navaids)
    add('Runway bearings', circle(gp[0], gp[1], 0.8, '#FFFFFF', INK, 0.3))
    # balises du bandeau : symboles, puis libelles un a un (chacun evite tout ce qui est deja pose) ; les libelles LOC et GS, qui ont
    # huit places possibles, viennent apres eux
    shown, groups = compose.navaid_plan(view, frame, data)
    for bcn, X, Y in shown:
        for el in compose.navaid_symbol_elems(bcn.kind, X, Y):
            add('Navaids', el)
    nav_free, nav_labels = [], []
    for grp in groups:
        (pos, elems, free), = page.place_navaid_labels([(grp['lines'], grp['xs'], grp['ys'])], frame, obstacles, [])
        for el in elems:
            doc.add('Navaids', el)
        g = page.elems_geom(elems)
        obstacles.append(g)
        nav_labels.append(g)
        nav_free.append((grp['lines'][0], pos, free))
        if not free:
            log(f'approche {e.name} : libelle de balise {grp["lines"][0]} sans place libre')
    # libelles LOC et GS
    el, g, ok = place_small_label('LOC', pt(5.2), lp[0], lp[1], 1.0, obstacles, frame)
    doc.add('Runway markings', el)
    obstacles.append(g)
    if not ok:
        log(f'approche {e.name} : libelle LOC sans place libre')
    el, g, ok = place_small_label('GS', pt(5.2), gp[0], gp[1], 0.8, obstacles, frame)
    doc.add('Runway markings', el)
    obstacles.append(g)
    if not ok:
        log(f'approche {e.name} : libelle GS sans place libre')
    doc.add('Frame & grid', rect(fx, fy, fw, fh, 'none', INK, 0.35))

    # ---- profil
    px0, pw = X0, X1 - X0
    doc.add('Frame & grid', rect(px0, PROFILE_TOP, pw, PROFILE_BOTTOM - PROFILE_TOP, 'none', INK, 0.35))
    R = 'Runway table'
    doc.add(R, text(px0 + 2.0, PROFILE_TOP + 3.6, f'PROFIL — PLAN DE {int(SLOPE_DEG)},0°', pt(6.4), 'bold'))
    xd = lambda d: PROFILE_X_THR - d * PROFILE_PITCH
    ay = PROFILE_AXIS_Y
    lab_y = ay + 3.3
    doc.add(R, line(xd(10) - 4.0, ay, PROFILE_X_END, ay, INK2, 0.35))
    doc.add(R, line(PROFILE_X_THR, ay, PROFILE_X_END, ay, INK, 1.2))
    for d in range(10, 0, -1):
        doc.add(R, line(xd(d), ay - 1.4, xd(d), ay + 1.4, INK2, 0.35))
        doc.add(R, text(xd(d), lab_y, str(d), pt(5.6), 'regular', INK2, 'middle'))
    doc.add(R, text(xd(10) - text_width('10', 'regular', pt(5.6)) / 2 - 1.2, lab_y, 'NM', pt(5.6), 'regular', INK2, 'end'))
    doc.add(R, text(PROFILE_X_END, lab_y, f'SEUIL {e.name} {thr_s}', pt(5.6), 'regular', INK2, 'end'))
    base_ft = thr_ft if thr_ft is not None else 0
    alt10 = altitude_ft(base_ft, 10, s_gs)
    ky = (ay - PROFILE_TOP_PT_Y) / (alt10 - base_ft)
    yalt = lambda alt: ay - (alt - base_ft) * ky
    doc.add(R, line(xd(10), yalt(alt10), PROFILE_X_THR, yalt(base_ft + tch_ft(s_gs)), RED, 0.6))
    profile_alts = {}
    for d in (10, 8, 6, 4, 2):
        alt = altitude_ft(base_ft, d, s_gs)
        profile_alts[d] = round_to(alt, 10)
        doc.add(R, circle(xd(d), yalt(alt), 0.55, RED))
        doc.add(R, text(xd(d), yalt(alt) - 2.0, geo.thin(profile_alts[d]), pt(6.4), 'bold', INK, 'middle'))

    # ---- tableau
    doc.add(R, rect(X0, TABLE_Y, X1 - X0, 5.0, BAND))
    doc.add(R, text(8.0, TABLE_Y + 3.4, 'NM DU SEUIL', pt(5.6), 'regular', INK2))
    table_alts, dme_dists = {}, {}
    for nm in range(10, 0, -1):
        x = COL_X0 + (10 - nm) * COL_PITCH
        doc.add(R, text(x, TABLE_Y + 3.4, str(nm), pt(7.6), 'bold', INK, 'middle'))
        table_alts[nm] = round_to(altitude_ft(base_ft, nm, s_gs), 10)
        doc.add(R, text(x, ROW_Y[0], geo.thin(table_alts[nm]), pt(7.6), 'regular', INK, 'middle'))
    doc.add(R, text(8.0, ROW_Y[0], 'ALTITUDE FT', pt(5.6), 'regular', INK2))
    rule_y = ROW_Y[0] + 2.2
    if dme is not None:
        doc.add(R, text(8.0, ROW_Y[1], f'{dme.kind} {dme.callsign}', pt(5.6), 'regular', INK2))
        for nm in range(10, 0, -1):
            x = COL_X0 + (10 - nm) * COL_PITCH
            q = before(nm)
            dme_dists[nm] = nm_text(math.hypot(dme.x - q[0], dme.z - q[1]))
            doc.add(R, text(x, ROW_Y[1], dme_dists[nm], pt(7.6), 'regular', INK, 'middle'))
        rule_y = ROW_Y[1] + 2.2
    doc.add(R, line(X0, rule_y, X1, rule_y, INK, 0.35))
    year = f'{int(data.year)}'
    m1 = f"Pas une carte d'approche r\u00e9elle : DCS ne donne ni proc\u00e9dure ni minima. Axe : cap au compas du cockpit (grille \u2212 d\u00e9clinaison {year})."
    m2 = f"Plan de 3\u00b0 suppos\u00e9, depuis l'antenne de glide ({int(round(s_gs))} m apr\u00e8s le seuil) ; relief non repr\u00e9sent\u00e9. Donn\u00e9es : beacons.lua, pydcs."
    doc.add(R, text(X0, rule_y + 3.8, m1, pt(5.4), 'regular', INK2))
    doc.add(R, text(X0, rule_y + 7.0, m2, pt(5.4), 'regular', INK2))
    page.footer(doc, data, False)

    info = {'end': e.name, 'scale': s, 'frame': frame, 's_gs': s_gs, 'tch': tch, 'cap': cap, 'loc': loc_text, 'dme': (dme.kind, dme.callsign) if dme else None,
            'profile_altitudes': profile_alts, 'table_altitudes': table_alts, 'dme_distances': dme_dists, 'navaid_labels': nav_free,
            'navaid_label_geoms': nav_labels, 'arrow': (tail, tip), 'mentions': (m1, m2)}
    return PageOut(f'11-{n}', doc.render(), info)


def build_approach_pages(data, log=print):
    """Les pages `11-1`, `11-2`... de la base : [PageOut], vide s'il n'y a aucune extremite ILS avec glide."""
    pages = []
    for e, ant, s_gs in eligible_ends(data, log):
        pages.append(build_page(data, e, ant, s_gs, len(pages) + 1, log))
    return pages
