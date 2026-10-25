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


def distance_to_mask_m(mask, mpp):
    """Distance (m) de chaque pixel au plus proche pixel de `mask` (0 dans le masque)."""
    return cv2.distanceTransform((~mask).view(np.uint8), cv2.DIST_L2, 3) * mpp


def building_mask(img, pav, mpp):
    """Batiments : couleur +-14, ouverture 1 m, composantes (>= 40 m2) a moins de 60 m du revetement."""
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
    return keep[lab], int(keep.sum())


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
