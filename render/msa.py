"""Altitude minimale de secteur (MSA, *minimum sector altitude*) d'un terrain, calculee sur le relief de DCS.

Fonction pure : la grille du relief (`alt`, float32, absent = NaN, case (i, j) au point x = x0 + 100 i, z = z0 + 100 j ; repere DCS :
x vers le Nord, z vers l'Est), le centre (xa, za), la convergence `gamma` de la grille et la declinaison `decl` (degres, positive
vers l'Est) en entree ; ni pydcs ni projection (les tests n'en ont pas besoin).

Regle : le disque de 25 NM (46 300,0 m) autour du centre est coupe en quatre secteurs par relevement magnetique **du noeud vers le
centre** (000-090, 090-180, 180-270, 270-360) ; la MSA d'un secteur est 1 000 ft au-dessus du sol le plus haut du secteur (2 000 ft
s'il atteint 1 500,0 m), arrondie a la centaine de pieds au-dessus ; celle du terrain est le maximum des quatre. Le sol seul :
ni batiments, ni mats, ni autres obstacles.
"""
import math

import numpy as np

PAS_M = 100.0                    # pas de la grille
RAYON_NM = 25
M_PAR_NM = 1852.0
RAYON_M = RAYON_NM * M_PAR_NM    # 46 300,0 m
M_PAR_FT = 0.3048
MARGE_FT = 1000
MARGE_MONTAGNE_FT = 2000
SEUIL_MONTAGNE_M = 1500.0
ARRONDI_FT = 100
SECTEURS = ((0, 90), (90, 180), (180, 270), (270, 360))


class DisqueIncomplet(Exception):
    """Le disque n'est pas entierement releve : `manquants` noeuds du disque hors de la grille ou NaN, `secteurs_vides` : les
    secteurs (0 a 3) sans aucun noeud."""

    def __init__(self, manquants, secteurs_vides=()):
        self.manquants, self.secteurs_vides = int(manquants), tuple(secteurs_vides)
        super().__init__(f'{self.manquants} noeud(s) du disque de {RAYON_NM} NM manquant(s)'
                         + (f', secteur(s) sans noeud : {list(self.secteurs_vides)}' if self.secteurs_vides else ''))


def arrondir_msa_ft(h_m, marge_ft):
    """MSA en pieds (entier, multiple de 100) d'un sol de `h_m` metres : 100 x ceil(arrondi((h / 0,3048 + marge) / 100, 6)). L'arrondi a
    six decimales evite qu'un pied de trop apparaisse quand la valeur tombe juste sur une centaine (306 = 304,8 m en float32)."""
    return ARRONDI_FT * int(math.ceil(round((h_m / M_PAR_FT + marge_ft) / ARRONDI_FT, 6)))


def numero_secteur(dx, dz, gamma, decl):
    """Secteur (0 a 3, tableau d'entiers) des noeuds a (dx, dz) du centre : q = (degres(atan2(dz, dx)) + gamma - decl + 180) mod 360,
    le relevement magnetique du noeud vers le centre ; k = floor(q / 90), et k = 4 (par l'arrondi de q) vaut 0."""
    q = (np.degrees(np.arctan2(dz, dx)) + gamma - decl + 180.0) % 360.0
    k = np.floor(q / 90.0).astype(np.int64)
    k[k >= 4] = 0
    return k


def msa_terrain(alt, x0, z0, xa, za, gamma, decl):
    """La MSA du terrain centre en (xa, za) : {'msa_ft': int, 'sectors': [quatre dicts de 'from' a 'msa_ft']} (les champs du fichier
    `dcs-msa-v1`, sans arrondi des positions). DisqueIncomplet si un noeud du disque est hors du tableau ou NaN, ou si un secteur n'a
    aucun noeud. `alt` peut etre un `np.load(..., mmap_mode='r')`."""
    ni, nj = alt.shape
    x0, z0, xa, za = float(x0), float(z0), float(xa), float(za)
    ia, ib = math.floor((xa - RAYON_M - x0) / PAS_M) - 1, math.ceil((xa + RAYON_M - x0) / PAS_M) + 1
    ja, jb = math.floor((za - RAYON_M - z0) / PAS_M) - 1, math.ceil((za + RAYON_M - z0) / PAS_M) + 1
    i = np.arange(ia, ib + 1, dtype=np.int64)
    j = np.arange(ja, jb + 1, dtype=np.int64)
    dx = (x0 + PAS_M * i) - xa                                   # float64
    dz = (z0 + PAS_M * j) - za
    DX, DZ = dx[:, None], dz[None, :]
    disque = np.hypot(DX, DZ) <= RAYON_M
    # les altitudes de la fenetre ; hors du tableau : NaN (manquant)
    vals = np.full((i.size, j.size), np.nan, np.float32)
    i_lo, i_hi, j_lo, j_hi = max(ia, 0), min(ib, ni - 1), max(ja, 0), min(jb, nj - 1)
    if i_lo <= i_hi and j_lo <= j_hi:
        vals[i_lo - ia:i_hi - ia + 1, j_lo - ja:j_hi - ja + 1] = np.asarray(alt[i_lo:i_hi + 1, j_lo:j_hi + 1], dtype=np.float32)
    manquants = int((disque & np.isnan(vals)).sum())
    if manquants:
        raise DisqueIncomplet(manquants)
    k = numero_secteur(DX, DZ, float(gamma), float(decl))
    secteurs = []
    vides = []
    for n, (de, a) in enumerate(SECTEURS):
        masque = disque & (k == n)
        if not masque.any():
            vides.append(n)
            continue
        v = np.where(masque, vals, np.float32(-np.inf))
        ii, jj = divmod(int(np.argmax(v)), v.shape[1])          # le premier maximum : i croissant puis j croissant
        h = float(vals[ii, jj])
        montagne = h >= SEUIL_MONTAGNE_M
        secteurs.append({'from': de, 'to': a, 'terrain_max_m': round(h, 1), 'peak_x': float(x0 + PAS_M * int(i[ii])),
                         'peak_y': float(z0 + PAS_M * int(j[jj])), 'mountain': bool(montagne),
                         'msa_ft': arrondir_msa_ft(h, MARGE_MONTAGNE_FT if montagne else MARGE_FT)})
    if vides:
        raise DisqueIncomplet(0, vides)
    return {'msa_ft': max(s['msa_ft'] for s in secteurs), 'sectors': secteurs}
