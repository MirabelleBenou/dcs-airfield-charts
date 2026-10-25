"""Detection des pistes dans la mosaique (cadrage 4.2, 'Pistes').

Repere de travail : (x, z) du jeu en metres ; une piste = axe (point + azimut grille), largeur, deux extremites.
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from skimage.morphology import skeletonize

from . import vector

HALF_WIDTH_MIN_M = 15.0           # demi-largeur minimale du revetement candidat
SEGMENT_MIN_M = 800.0             # segment droit minimal
HOUGH_GAP_M = 60.0                # ecart tolere
HOUGH_THETA = math.pi / 720
HOUGH_VOTES = 200                 # seuil d'accumulation (non fixe par le cadrage)
MERGE_ANGLE_DEG, MERGE_OFFSET_M = 2.0, 30.0
MARK_CLOSE_OPEN_M, MARK_BLOCK_MIN_M2 = 1.5, 50.0
MARK_BLOCKS_MIN = 2
PROFILE_STEP_M = 25.0
PROFILE_MAX_RATIO = 2.2
THRESH_STEP_M = 4.0
THRESH_EDGE_M = 3.0
THRESH_FRACTION, THRESH_MIN_LEN_M = 0.22, 16.0
THRESH_SEARCH_M = 900.0
THRESH_PAVED_FRACTION = 0.30
THRESH_WIDEN_M = 6.0
EXTEND_GAP_M = HOUGH_GAP_M          # interruption toleree en prolongeant le candidat le long de l'axe
EXTEND_STEP_M = 4.0
EXTEND_MAX_M = 900.0                # prolongement maximal au-dela de chaque bout de la candidate de Hough (addendum 2)


@dataclass
class Runway:
    p0: tuple            # (x, z) extremite A
    p1: tuple            # (x, z) extremite B
    width: float         # m
    n_blocks: int = 0
    axis_dir: tuple = field(default=(0.0, 0.0))   # vecteur unitaire (dx, dz) de A vers B

    @property
    def length(self):
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1])

    @property
    def center(self):
        return ((self.p0[0] + self.p1[0]) / 2, (self.p0[1] + self.p1[1]) / 2)


# ---------- echantillonnage ----------
class Sampler:
    """Acces aux masques par coordonnees (x, z)."""

    def __init__(self, georef):
        self.g = georef

    def rc(self, x, z):
        g = self.g
        r = np.rint((g.x_top - np.asarray(x)) / g.mpp - 0.5).astype(np.int64)
        c = np.rint((np.asarray(z) - g.z_left) / g.mpp - 0.5).astype(np.int64)
        return r, c

    def at(self, mask, x, z):
        r, c = self.rc(x, z)
        ok = (r >= 0) & (r < mask.shape[0]) & (c >= 0) & (c < mask.shape[1])
        out = np.zeros(r.shape, bool)
        out[ok] = mask[r[ok], c[ok]]
        return out


# ---------- candidats ----------
def candidate_lines(pav, georef):
    """Droites candidates (cadrage 4.2.1) : liste de dicts {p, d, half_width, u0, u1} en (x, z)."""
    mpp = georef.mpp
    h, w = pav.shape
    p2 = cv2.resize(pav.view(np.uint8) * 255, (w // 2, h // 2), interpolation=cv2.INTER_AREA) > 127
    m2 = mpp * 2
    dist = cv2.distanceTransform(p2.view(np.uint8), cv2.DIST_L2, 5) * m2
    core = skeletonize(p2) & (dist >= HALF_WIDTH_MIN_M)
    lines = cv2.HoughLinesP(core.view(np.uint8) * 255, 1, HOUGH_THETA, HOUGH_VOTES,
                            minLineLength=SEGMENT_MIN_M / m2, maxLineGap=HOUGH_GAP_M / m2)
    if lines is None:
        return [], dist, m2
    segs = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        pts = []
        for X, Y in ((x1, y1), (x2, y2)):
            pts.append(georef.rc_to_xz(2 * Y + 0.5, 2 * X + 0.5))
        segs.append(pts)
    return merge_segments(segs), dist, m2


def _dir(ang):
    """Vecteur unitaire (dx, dz) pour un angle `ang` mesure par atan2(dz, dx)."""
    return math.cos(ang), math.sin(ang)


def merge_segments(segs):
    """Fusionne les doublons (angle < 2 deg, decalage < 30 m) ; retourne des droites {p, d, u0, u1} (x, z)."""
    n = len(segs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    info = []
    for a, b in segs:
        ang = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
        info.append((ang, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))
    for i in range(n):
        for j in range(i + 1, n):
            da = abs(info[i][0] - info[j][0])
            da = min(da, math.pi - da)
            if math.degrees(da) >= MERGE_ANGLE_DEG:
                continue
            dirx, dirz = _dir(info[i][0])
            dx, dz = info[j][1][0] - info[i][1][0], info[j][1][1] - info[i][1][1]
            if abs(-dx * dirz + dz * dirx) < MERGE_OFFSET_M:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in groups.values():
        pts = np.array([p for i in idx for p in segs[i]], float)
        c = pts.mean(axis=0)
        d = np.linalg.svd(pts - c)[2][0]
        t = (pts - c) @ d
        out.append({'p': (float(c[0]), float(c[1])), 'd': (float(d[0]), float(d[1])),
                    'u0': float(t.min()), 'u1': float(t.max())})
    return out


def marking_blocks(img_clear, mpp):
    """Blocs de marquage plein : marquage clair, fermeture puis ouverture 1,5 m, blocs >= 50 m2.
    Retourne (centres (N, 2) en indices (r, c), aires en m2)."""
    m = vector.close_mask(img_clear, MARK_CLOSE_OPEN_M, mpp)
    m = vector.open_mask(m, MARK_CLOSE_OPEN_M, mpp)
    n, _, stats, cent = cv2.connectedComponentsWithStats(m.view(np.uint8), connectivity=8)
    area = stats[1:, cv2.CC_STAT_AREA] * mpp * mpp
    keep = area >= MARK_BLOCK_MIN_M2
    return cent[1:][keep][:, ::-1], area[keep]            # (r, c)


def _frame(line):
    px, pz = line['p']
    dx, dz = line['d']
    return px, pz, dx, dz, -dz, dx                      # origine, direction, normale


def half_width_along(line, dist, m2, georef):
    """Demi-largeur mediane du revetement le long de l'axe candidat (carte de distance reduite x2)."""
    px, pz, dx, dz, nx, nz = _frame(line)
    u = np.arange(line['u0'], line['u1'], 5.0)
    xs, zs = px + u * dx, pz + u * dz
    r = np.rint((georef.x_top - xs) / m2 * 2 / 2 / (m2 / 2) * 0 + (georef.x_top - xs) / m2 - 0.5).astype(int)
    c = np.rint((zs - georef.z_left) / m2 - 0.5).astype(int)
    ok = (r >= 0) & (r < dist.shape[0]) & (c >= 0) & (c < dist.shape[1])
    vals = dist[r[ok], c[ok]]
    vals = vals[vals >= HALF_WIDTH_MIN_M]
    return float(np.median(vals)) if vals.size else HALF_WIDTH_MIN_M


def count_blocks(line, hw, blocks_xz):
    px, pz, dx, dz, nx, nz = _frame(line)
    rel = blocks_xz - np.array([px, pz])
    u = rel[:, 0] * dx + rel[:, 1] * dz
    v = rel[:, 0] * nx + rel[:, 1] * nz
    return int(np.sum((np.abs(v) <= hw) & (u >= line['u0'] - 50) & (u <= line['u1'] + 50)))


def refine_axis(line, hw, sam, pav):
    """Recalage de l'axe (cadrage 4.2.3). Retourne (ligne recalee, largeur)."""
    px, pz, dx, dz, nx, nz = _frame(line)
    width0 = 2 * hw
    half_prof = PROFILE_MAX_RATIO * width0 / 2 * 1.5
    step = sam.g.mpp
    v = np.arange(-half_prof, half_prof + step / 2, step)
    i0 = int(np.argmin(np.abs(v)))
    us, mids, widths = [], [], []
    for u in np.arange(line['u0'], line['u1'] + 1e-6, PROFILE_STEP_M):
        xs = px + u * dx + v * nx
        zs = pz + u * dz + v * nz
        prof = sam.at(pav, xs, zs)
        if not prof[i0]:
            continue
        a = i0
        while a > 0 and prof[a - 1]:
            a -= 1
        b = i0
        while b < len(v) - 1 and prof[b + 1]:
            b += 1
        if a == 0 or b == len(v) - 1:
            continue
        w = (b - a + 1) * step
        if w > PROFILE_MAX_RATIO * width0:
            continue
        us.append(u)
        mids.append((v[a] + v[b]) / 2)
        widths.append(w)
    if len(us) < 3:
        return line, width0
    us, mids = np.array(us), np.array(mids)
    slope, icpt = np.polyfit(us, mids, 1)
    # nouvelle droite : v = icpt + slope * u  (dans le repere de la candidate)
    nd = np.array([dx + slope * nx, dz + slope * nz])
    nd /= np.linalg.norm(nd)
    p0 = np.array([px + icpt * nx, pz + icpt * nz])
    return {'p': (float(p0[0]), float(p0[1])), 'd': (float(nd[0]), float(nd[1])),
            'u0': line['u0'], 'u1': line['u1']}, float(np.median(widths))


def paved_fraction(line, width, sam, pav, clear, us):
    """Part de la largeur (+- w/2) couverte de revetement ou de marquage clair, aux abscisses `us` de l'axe."""
    px, pz, dx, dz, nx, nz = _frame(line)
    vv = np.arange(-width / 2, width / 2 + 1e-6, sam.g.mpp)
    UU, VV = np.meshgrid(us, vv, indexing='ij')
    xs, zs = px + UU * dx + VV * nx, pz + UU * dz + VV * nz
    return (sam.at(pav, xs, zs) | sam.at(clear, xs, zs)).mean(axis=1)


def extend_range(line, width, sam, pav, clear):
    """Prolonge [u0, u1] de la candidate le long de l'axe recale tant que revetement ou marquage couvre >= 30 %
    de la largeur, en tolerant des interruptions de 60 m (les segments de Hough s'arretent aux carrefours,
    ou le squelette devie)."""
    out = []
    for sign, start in ((-1, line['u0']), (1, line['u1'])):
        u, last_ok = start, start
        while abs(u - last_ok) <= EXTEND_GAP_M and abs(u - start) < EXTEND_MAX_M:
            u += sign * EXTEND_STEP_M
            if paved_fraction(line, width, sam, pav, clear, np.array([u]))[0] >= THRESH_PAVED_FRACTION:
                last_ok = u
        out.append(last_ok)
    return out[0], out[1]


def threshold_ends(line, width, sam, pav, clear):
    """Extremites = barres de seuil (cadrage 4.2.4). Retourne (u_debut, u_fin) le long de `line` ou None."""
    px, pz, dx, dz, nx, nz = _frame(line)
    half = width / 2
    inner = np.arange(-(half - THRESH_EDGE_M), half - THRESH_EDGE_M + 1e-6, sam.g.mpp)
    outer = np.arange(-half, half + 1e-6, sam.g.mpp)
    us = np.arange(line['u0'] - THRESH_SEARCH_M, line['u1'] + THRESH_SEARCH_M + 1e-6, THRESH_STEP_M)
    UU, VV = np.meshgrid(us, inner, indexing='ij')
    frac = sam.at(clear, px + UU * dx + VV * nx, pz + UU * dz + VV * nz).mean(axis=1)
    UO, VO = np.meshgrid(us, outer, indexing='ij')
    xs, zs = px + UO * dx + VO * nx, pz + UO * dz + VO * nz
    paved = (sam.at(pav, xs, zs) | sam.at(clear, xs, zs)).mean(axis=1)
    valid = paved >= THRESH_PAVED_FRACTION
    hit = valid & (frac >= THRESH_FRACTION)
    runs, i = [], 0
    while i < len(us):
        if hit[i]:
            j = i
            while j + 1 < len(us) and hit[j + 1]:
                j += 1
            if (j - i + 1) * THRESH_STEP_M >= THRESH_MIN_LEN_M:
                runs.append((us[i], us[j]))
            i = j + 1
        else:
            i += 1
    if len(runs) < 2:
        return None, runs
    return (runs[0][0] - THRESH_WIDEN_M, runs[-1][1] + THRESH_WIDEN_M), runs


def detect_runways(img, pav, georef, clear=None):
    """Pistes d'une mosaique. Retourne une liste de Runway (triee par position)."""
    mpp = georef.mpp
    sam = Sampler(georef)
    if clear is None:
        clear = vector.clear_marking_mask(img)
    cands, dist, m2 = candidate_lines(pav, georef)
    rc, _ = marking_blocks(clear, mpp)
    blocks = np.array([georef.rc_to_xz(r, c) for r, c in rc]).reshape(-1, 2)
    runways = []
    for ln in cands:
        hw = half_width_along(ln, dist, m2, georef)
        nb = count_blocks(ln, hw, blocks)
        if nb < MARK_BLOCKS_MIN:
            continue
        ln2, width = refine_axis(ln, hw, sam, pav)
        ln2['u0'], ln2['u1'] = extend_range(ln2, width, sam, pav, clear)
        ln2, width = refine_axis(ln2, width / 2, sam, pav)
        ends, _ = threshold_ends(ln2, width, sam, pav, clear)
        if ends is None:
            continue
        px, pz, dx, dz, _, _ = _frame(ln2)
        a, b = ends
        runways.append(Runway((px + a * dx, pz + a * dz), (px + b * dx, pz + b * dz), width, nb, (dx, dz)))
    return runways
