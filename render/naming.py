"""Noms et caps des extremites de piste (cadrage 4.3). Fonctions pures, testables sur des cas synthetiques."""
import math
import re

LEFT_RIGHT = ('L', 'C', 'R')


def angdiff(a, b):
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def landing_azimuth(a, b):
    """Azimut grille (degres, [0, 360)) du sens d'atterrissage de a vers b ; points (x, z) : atan2(dz, dx)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def number_of(name):
    m = re.match(r'(\d+)', name)
    return m.group(1).zfill(2) if m else name


def name_ends(runways, approaches):
    """runways : liste de (p0, p1) en (x, z). approaches : liste de (nom pydcs, cap pydcs).
    Retourne, pour chaque piste i, [(nom_extremite_p0, azimut_p0), (nom_extremite_p1, azimut_p1)].

    Le cap pydcs le plus proche de l'azimut grille d'atterrissage donne le numero ; entre extremites de meme numero
    (pistes paralleles), la plus a gauche vue dans le sens d'atterrissage prend L, puis C, puis R."""
    ends = []
    for i, (p0, p1) in enumerate(runways):
        for k, (a, b) in enumerate(((p0, p1), (p1, p0))):
            az = landing_azimuth(a, b)
            best = min(approaches, key=lambda ap: (angdiff(az, ap[1]), ap[0]))
            ends.append({'rw': i, 'end': k, 'pos': a, 'az': az, 'num': number_of(best[0]), 'letter': ''})
    groups = {}
    for e in ends:
        groups.setdefault(e['num'], []).append(e)
    for grp in groups.values():
        if len(grp) < 2:
            continue
        mean_az = math.degrees(math.atan2(sum(math.sin(math.radians(e['az'])) for e in grp),
                                          sum(math.cos(math.radians(e['az'])) for e in grp))) % 360.0
        ar = math.radians(mean_az)
        left = (math.sin(ar), -math.cos(ar))                      # gauche en (x, z)
        grp.sort(key=lambda e: -(e['pos'][0] * left[0] + e['pos'][1] * left[1]))
        letters = LEFT_RIGHT if len(grp) <= 3 else None
        for j, e in enumerate(grp):
            e['letter'] = letters[j if len(grp) == 3 else (0 if j == 0 else 2)] if letters else str(j + 1)
    out = [[None, None] for _ in runways]
    for e in ends:
        out[e['rw']][e['end']] = (e['num'] + e['letter'], e['az'])
    return out


def sort_key(name):
    m = re.match(r'(\d+)([A-Z]?)', name)
    return (int(m.group(1)), 'LCR'.find(m.group(2)) if m.group(2) else -1) if m else (999, 0)
