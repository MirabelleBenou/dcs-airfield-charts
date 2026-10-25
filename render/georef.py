"""Georeferencement de la mosaique : repere image <-> repere DCS (x Nord, z Est, metres).

Ligne r  <-> x = x_top - (r + 0,5) * mpp ;  colonne c <-> z = z_left + (c + 0,5) * mpp.
La mosaique des captures anterieures au lot rendu-v1 (base.json sans `georef_version`) est decalee :
le rendu applique lui-meme la correction (cadrage 4.1) ; un base.json `georef_version` 2 est deja corrige.
"""
import json
from pathlib import Path
from typing import NamedTuple


class Georef(NamedTuple):
    x_top: float
    z_left: float
    mpp: float
    width: int
    height: int

    def rc_to_xz(self, r, c):
        return self.x_top - (r + 0.5) * self.mpp, self.z_left + (c + 0.5) * self.mpp

    def xz_to_rc(self, x, z):
        return (self.x_top - x) / self.mpp - 0.5, (z - self.z_left) / self.mpp - 0.5


def margin_shift(mpp, margins):
    """Decalage (dx, dz) en metres a retrancher de la couverture : margins = [gauche, haut, droite, bas] (px).
    Le recadrage de l'ecran n'est pas centre : x diminue de (M_T - M_B)/2 mpp, z de (M_R - M_L)/2 mpp."""
    ml, mt, mr, mb = margins
    return (mt - mb) / 2 * mpp, (mr - ml) / 2 * mpp


def correct_coverage(cover, mpp, margins):
    """Couverture {x_min, x_max, z_min, z_max} corrigee du decalage de recadrage (cadrage 4.1)."""
    dx, dz = margin_shift(mpp, margins)
    return {'x_min': cover['x_min'] - dx, 'x_max': cover['x_max'] - dx,
            'z_min': cover['z_min'] - dz, 'z_max': cover['z_max'] - dz}


def georef_from_base(base):
    """Georef d'un dict base.json (v1 : corrige ici ; v2 : tel quel)."""
    cover = base['mosaic_coverage_m']
    mpp = base['m_per_px']
    if base.get('georef_version', 1) < 2:
        cover = correct_coverage(cover, mpp, base['margins_px_lthr_b'])
    w, h = base['mosaic_px']
    return Georef(cover['x_max'], cover['z_min'], mpp, int(w), int(h))


def load_base_json(folder):
    return json.loads((Path(folder) / 'base.json').read_text(encoding='utf-8'))
