"""Vectorisation de la mosaique F10 : revetement, marquage, batiments (cadrage 4.2).

Aucune constante par base. Les seuils viennent du cadrage ; les diametres de fermeture/ouverture
sont des diametres d'element structurant (disque), exprimes en metres puis convertis en pixels.
"""
import cv2
import numpy as np

PAVEMENT_RGB, PAVEMENT_TOL = (153, 149, 131), 12
BUILDING_RGB, BUILDING_TOL = (78, 74, 58), 14
PAVEMENT_CLOSE_M, PAVEMENT_OPEN_M = 2.0, 2.5
PAVEMENT_HOLE_M2, PAVEMENT_MIN_M2 = 400.0, 1500.0
PAVEMENT_SIMPLIFY_M = 0.8
BUILDING_OPEN_M, BUILDING_NEAR_M = 1.0, 60.0
BUILDING_SIMPLIFY_M, BUILDING_MIN_M2 = 0.5, 40.0
# Lot « ac-germany » : revetement de l'aerodrome (cadrage 4.3). Apres la detection des pistes, on ne garde que les composantes
# connexes (8-connexite) du masque qui touchent le tampon de RUNWAY_NEAR_M autour du segment de chaque piste detectee (une bande de
# RUNWAY_NEAR_M de chaque cote de l'axe, prolongee de RUNWAY_NEAR_M au-dela de chaque extremite ; addendum 1), ou qui sont a
# PARKING_NEAR_M au plus d'un point de parking du jeu (bretelles d'autoroute, echangeurs : revetement gris de la F10 hors aerodrome).
RUNWAY_NEAR_M, PARKING_NEAR_M = 30.0, 60.0


def near_color(img, rgb, tol):
    """Masque des pixels dont chaque canal est a au plus `tol` de `rgb`."""
    m = np.abs(img[:, :, 0].astype(np.int16) - rgb[0]) <= tol
    for k in (1, 2):
        m &= np.abs(img[:, :, k].astype(np.int16) - rgb[k]) <= tol
    return m


def clear_marking_mask(img):
    """Marquage clair : R > 200 et G > 200 et |R - G| < 8 et 130 < B < 178."""
    r, g, b = (img[:, :, k].astype(np.int16) for k in range(3))
    return (r > 200) & (g > 200) & (np.abs(r - g) < 8) & (b > 130) & (b < 178)


def disk(diameter_m, mpp):
    """Disque de diametre `diameter_m` (au moins 3 px de cote)."""
    n = max(3, int(round(diameter_m / mpp)))
    if n % 2 == 0:
        n += 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))


def close_mask(mask, diameter_m, mpp):
    return cv2.morphologyEx(mask.view(np.uint8), cv2.MORPH_CLOSE, disk(diameter_m, mpp)).astype(bool)


def open_mask(mask, diameter_m, mpp):
    return cv2.morphologyEx(mask.view(np.uint8), cv2.MORPH_OPEN, disk(diameter_m, mpp)).astype(bool)


def fill_small_holes(mask, max_area_m2, mpp):
    """Comble les trous (fond non relie au bord) de moins de `max_area_m2`."""
    bg = (~mask).view(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bg, connectivity=4)
    h, w = mask.shape
    x, y = stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP]
    bw, bh, area = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT], stats[:, cv2.CC_STAT_AREA]
    touches = (x == 0) | (y == 0) | (x + bw == w) | (y + bh == h)
    fill = (~touches) & (area < max_area_m2 / (mpp * mpp))
    fill[0] = False
    return mask | fill[lab]


def keep_components(mask, min_area_m2, mpp, connectivity=8):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.view(np.uint8), connectivity=connectivity)
    min_px = min_area_m2 / (mpp * mpp)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_px
    return keep[lab]


def pavement_mask(img, mpp):
    """Revetement : couleur +-12, fermeture 2 m, trous < 400 m2 combles, ouverture 2,5 m, composantes >= 1 500 m2."""
    m = near_color(img, PAVEMENT_RGB, PAVEMENT_TOL)
    m = close_mask(m, PAVEMENT_CLOSE_M, mpp)
    m = fill_small_holes(m, PAVEMENT_HOLE_M2, mpp)
    m = open_mask(m, PAVEMENT_OPEN_M, mpp)
    return keep_components(m, PAVEMENT_MIN_M2, mpp)


def _window(r0, r1, c0, c1, shape):
    """Fenetre (r0, r1, c0, c1) ramenee dans l'image `shape` ; None si elle est vide."""
    r0, c0 = max(int(r0), 0), max(int(c0), 0)
    r1, c1 = min(int(r1), shape[0]), min(int(c1), shape[1])
    return (r0, r1, c0, c1) if r1 > r0 and c1 > c0 else None


def airfield_pavement(mask, georef, runway_segments, parking_xz, runway_near_m=RUNWAY_NEAR_M, parking_near_m=PARKING_NEAR_M):
    """Revetement de l'aerodrome (lot « ac-germany », cadrage 4.3) : ne garde de `mask` que les composantes connexes
    (8-connexite) qui touchent le tampon de `runway_near_m` autour du segment d'une piste detectee, extremites comprises
    (`runway_segments` : [((x, z) extremite A, (x, z) extremite B)] ; bande de `runway_near_m` de chaque cote de l'axe, prolongee
    de `runway_near_m` au-dela de chaque extremite : addendum 1), ou qui sont a `parking_near_m` au plus d'un point de
    parking (`parking_xz` : [(x, z)], toutes categories). Retourne (masque filtre, composantes avant, composantes ecartees)."""
    h, w = mask.shape
    n, lab = cv2.connectedComponents(mask.view(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    mpp = georef.mpp
    for (ax, az), (bx, bz) in runway_segments:
        a, b = np.array(georef.xz_to_rc(ax, az)), np.array(georef.xz_to_rc(bx, bz))        # (r, c) en pixels
        d = b - a
        L = float(np.hypot(*d))
        if L == 0.0:
            continue
        nrm = np.array([-d[1], d[0]]) / L * (runway_near_m / mpp)                          # normale a l'axe, en pixels
        ext = d / L * (runway_near_m / mpp)                                                # prolongement au-dela des extremites
        a, b = a - ext, b + ext
        quad = np.array([a + nrm, b + nrm, b - nrm, a - nrm])
        win = _window(np.floor(quad[:, 0].min()), np.ceil(quad[:, 0].max()) + 1, np.floor(quad[:, 1].min()), np.ceil(quad[:, 1].max()) + 1, mask.shape)
        if win is None:
            continue
        r0, r1, c0, c1 = win
        band = np.zeros((r1 - r0, c1 - c0), np.uint8)
        pts = np.stack([quad[:, 1] - c0, quad[:, 0] - r0], axis=1)                         # (colonne, ligne) dans la fenetre
        cv2.fillPoly(band, [np.round(pts * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
        keep[np.unique(lab[r0:r1, c0:c1][band > 0])] = True
    rad = parking_near_m / mpp
    for x, z in parking_xz:
        r, c = georef.xz_to_rc(x, z)
        win = _window(np.floor(r - rad), np.ceil(r + rad) + 1, np.floor(c - rad), np.ceil(c + rad) + 1, mask.shape)
        if win is None:
            continue
        r0, r1, c0, c1 = win
        rr, cc = np.ogrid[r0:r1, c0:c1]
        disk_px = (rr - r) ** 2 + (cc - c) ** 2 <= rad * rad
        keep[np.unique(lab[r0:r1, c0:c1][disk_px])] = True
    keep[0] = False                                                                        # le fond n'est pas une composante
    total = n - 1
    kept = int(keep.sum())
    return keep[lab], total, total - kept


def distance_to_mask_m(mask, mpp):
    """Distance (m) de chaque pixel au plus proche pixel de `mask` (0 dans le masque)."""
    return cv2.distanceTransform((~mask).view(np.uint8), cv2.DIST_L2, 3) * mpp


def building_mask(img, pav, mpp):
    """Batiments : couleur +-14, ouverture 1 m, composantes (>= 40 m2) a moins de 60 m du revetement ; puis les trous
    (composantes 4-connexes du complement, <= 40 m2, hors bord de la mosaique) sont combles : ce sont les numeros de
    parking imprimes par la carte F10 dans les abris. Le nombre de batiments renvoye est celui d'avant le comblement."""
    max_hole_m2 = 40.0
    m = near_color(img, BUILDING_RGB, BUILDING_TOL)
    m = open_mask(m, BUILDING_OPEN_M, mpp)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.view(np.uint8), connectivity=8)
    if n <= 1:
        return m, n
    dist = distance_to_mask_m(pav, mpp)
    dmin = np.full(n, np.inf)
    flat = lab.ravel()
    order = np.argsort(flat, kind='stable')
    sl = flat[order]
    starts = np.searchsorted(sl, np.arange(n))
    dflat = dist.ravel()[order]
    dmin[1:] = np.minimum.reduceat(dflat, starts[1:])
    keep = np.zeros(n, bool)
    area = stats[:, cv2.CC_STAT_AREA] * mpp * mpp
    keep[1:] = (dmin[1:] < BUILDING_NEAR_M) & (area[1:] >= BUILDING_MIN_M2)
    mk = keep[lab]
    del lab, flat, order, sl, dflat, dist            # memoire : la mosaique de Damascus pese des centaines de Mpx
    # trous : composantes 4-connexes du complement, de moins de 40 m2 et qui ne touchent pas le bord
    nb, labb, stb, _ = cv2.connectedComponentsWithStats((~mk).view(np.uint8), connectivity=4)
    h, w = mk.shape
    x, y = stb[:, cv2.CC_STAT_LEFT], stb[:, cv2.CC_STAT_TOP]
    touches = (x == 0) | (y == 0) | (x + stb[:, cv2.CC_STAT_WIDTH] == w) | (y + stb[:, cv2.CC_STAT_HEIGHT] == h)
    hole = (~touches) & (stb[:, cv2.CC_STAT_AREA] * mpp * mpp <= max_hole_m2)
    hole[0] = False
    return mk | hole[labb], int(keep.sum())


def mask_rings(mask, georef, eps_m, window=None):
    """Contours d'un masque (exterieurs et trous), simplifies a `eps_m` (Douglas-Peucker), en (x, z) metres.
    window = (r0, r1, c0, c1) restreint le calcul a une fenetre de la mosaique (les contours sont recoupes
    par le bord de la fenetre)."""
    r0, c0 = 0, 0
    sub = mask
    if window is not None:
        r0, r1, c0, c1 = window
        sub = mask[r0:r1, c0:c1]
    cnts, _ = cv2.findContours(np.ascontiguousarray(sub).view(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    eps = eps_m / georef.mpp
    rings = []
    for c in cnts:
        a = cv2.approxPolyDP(c, eps, True).reshape(-1, 2).astype(float)
        if len(a) < 3:
            continue
        cols, rows = a[:, 0] + c0, a[:, 1] + r0
        rings.append(np.stack([georef.x_top - (rows + 0.5) * georef.mpp, georef.z_left + (cols + 0.5) * georef.mpp], axis=1))
    return rings
