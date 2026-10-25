"""Synthese du modele magnetique WMM a partir des fichiers .COF de DCS (coefficients de Schmidt semi-normalises,
geodesique -> geocentrique). Sert a verifier l'IGRF-14 du rendu (critere C9). Bibliotheque standard + numpy."""
import math
from pathlib import Path

import numpy as np

A_KM, F = 6378.137, 1 / 298.257223563
E2 = F * (2 - F)
R_REF = 6371.2


def read_cof(path):
    """Retourne (epoque, {(n, m): (g, h, gdot, hdot)})."""
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    epoch = float(lines[0].split()[0])
    coef = {}
    for ln in lines[1:]:
        p = ln.split()
        if len(p) != 6:
            continue
        coef[(int(p[0]), int(p[1]))] = tuple(float(v) for v in p[2:])
    return epoch, coef


def declination(path, lat_deg, lon_deg, year, h_km=0.0):
    """Declinaison (deg, Est positif) du modele du fichier `path` a l'instant `year` (annee decimale)."""
    epoch, coef = read_cof(path)
    nmax = max(n for n, _ in coef)
    dt = year - epoch
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    # geodesique -> geocentrique
    sl, cl = math.sin(lat), math.cos(lat)
    rc = A_KM / math.sqrt(1 - E2 * sl * sl)
    p = (rc + h_km) * cl
    z = (rc * (1 - E2) + h_km) * sl
    r = math.hypot(p, z)
    lat_gc = math.asin(z / r)
    theta = math.pi / 2 - lat_gc
    x, s = math.cos(theta), math.sin(theta)
    # polynomes de Legendre, Schmidt semi-normalises, et derivees / theta
    P = np.zeros((nmax + 1, nmax + 1))
    dP = np.zeros((nmax + 1, nmax + 1))
    P[0, 0] = 1.0
    for n in range(1, nmax + 1):
        for m in range(n + 1):
            if n == m:
                P[n, m] = s * P[n - 1, m - 1]
                dP[n, m] = s * dP[n - 1, m - 1] + x * P[n - 1, m - 1]
            elif n == 1 and m == 0:
                P[n, m] = x * P[n - 1, m]
                dP[n, m] = x * dP[n - 1, m] - s * P[n - 1, m]
            else:
                p2 = P[n - 2, m] if m <= n - 2 else 0.0
                d2 = dP[n - 2, m] if m <= n - 2 else 0.0
                k = ((n - 1) ** 2 - m ** 2) / ((2 * n - 1) * (2 * n - 3))
                P[n, m] = x * P[n - 1, m] - k * p2
                dP[n, m] = x * dP[n - 1, m] - s * P[n - 1, m] - k * d2
    snorm = np.zeros((nmax + 1, nmax + 1))
    snorm[0, 0] = 1.0
    for n in range(1, nmax + 1):
        snorm[n, 0] = snorm[n - 1, 0] * (2 * n - 1) / n
        j = 2.0
        for m in range(1, n + 1):
            snorm[n, m] = snorm[n, m - 1] * math.sqrt((n - m + 1) * j / (n + m))
            j = 1.0
    P *= snorm
    dP *= snorm
    br = bt = bp = 0.0
    for (n, m), (g, h, gd, hd) in coef.items():
        g, h = g + dt * gd, h + dt * hd
        ar = (R_REF / r) ** (n + 2)
        c, sn = math.cos(m * lon), math.sin(m * lon)
        br += ar * (n + 1) * (g * c + h * sn) * P[n, m]
        bt -= ar * (g * c + h * sn) * dP[n, m]
        bp += ar * m * (g * sn - h * c) * P[n, m] / s
    bx, by, bz = -bt, bp, -br                                # nord, est, bas (geocentriques)
    psi = lat_gc - lat
    bx_gd = bx * math.cos(psi) - bz * math.sin(psi)
    return math.degrees(math.atan2(by, bx_gd))
