"""Projection DCS <-> lat/lon (pydcs), convergence de la grille, declinaison magnetique (IGRF-14), formats de cartes."""
import importlib
import math
from datetime import datetime, timedelta

import numpy as np
from pyproj import CRS, Geod, Transformer

GEOD = Geod(ellps='WGS84')
GRID_PROBE_M = 500.0


def theatre_module(theatre):
    """Nom du module pydcs d'un theatre : Syria -> syria (PersianGulf -> persiangulf ...)."""
    return theatre.replace(' ', '').lower()


class Projection:
    """Projection transverse de Mercator du theatre (parametres de pydcs, +axis=neu)."""

    def __init__(self, params):
        self.params = params
        crs = params.to_crs()
        wgs = CRS('WGS84')
        self._fwd = Transformer.from_crs(crs, wgs)          # (x, z) -> (lat, lon)
        self._inv = Transformer.from_crs(wgs, crs)          # (lat, lon) -> (x, z)

    @classmethod
    def for_theatre(cls, theatre):
        mod = importlib.import_module(f'dcs.terrain.{theatre_module(theatre)}.projection')
        return cls(mod.PARAMETERS)

    def to_latlon(self, x, z):
        lat, lon = self._fwd.transform(x, z)
        return lat, lon

    def to_xz(self, lat, lon):
        x, z = self._inv.transform(lat, lon)
        return x, z

    def convergence(self, x, z):
        """Azimut vrai (degres, geodesique WGS84) de la direction nord grille en (x, z) : deux points a +-500 m en x."""
        lat0, lon0 = self.to_latlon(x - GRID_PROBE_M, z)
        lat1, lon1 = self.to_latlon(x + GRID_PROBE_M, z)
        az, _, _ = GEOD.inv(lon0, lat0, lon1, lat1)
        return az if abs(az) <= 180 else az - 360 * math.copysign(1, az)


def year_to_datetime(year):
    """Date de la declinaison : 1er juillet de l'annee ; annee decimale -> 1er janvier + fraction de l'annee."""
    y = float(year)
    yi = int(math.floor(y))
    if y == yi:
        return datetime(yi, 7, 1)
    start, end = datetime(yi, 1, 1), datetime(yi + 1, 1, 1)
    return start + timedelta(seconds=(y - yi) * (end - start).total_seconds())


def declination(lat, lon, year, h_km=0.0):
    """Declinaison magnetique IGRF-14 en degres, positive vers l'Est."""
    import ppigrf
    be, bn, _ = ppigrf.igrf(lon, lat, h_km, year_to_datetime(year))
    return float(np.degrees(np.arctan2(np.ravel(be)[0], np.ravel(bn)[0])))


def norm360(a):
    return a % 360.0


def dm(value, pos, neg, deg_width=2):
    """Degres et minutes decimales : N37°58.7' ; E032°33.7'."""
    hemi = pos if value >= 0 else neg
    v = abs(value)
    d = int(v)
    m = round((v - d) * 60, 1)
    if m >= 60.0:
        d, m = d + 1, 0.0
    return f"{hemi}{d:0{deg_width}d}°{m:04.1f}'"


def arp_text(lat, lon):
    return f"ARP {dm(lat, 'N', 'S', 2)} {dm(lon, 'E', 'W', 3)}"


def var_text(decl, year):
    """VAR 3°E (1985) : declinaison arrondie au degre ; annee entiere ou decimale."""
    r = int(round(abs(decl)))
    return f"VAR {r}°{'E' if decl >= 0 else 'W'}", year_label(year)


def year_label(year):
    y = float(year)
    return f"({int(y)})" if y == int(y) else f"({y:g})"


def thin(n):
    """Entier groupe par espaces : 11 089."""
    return f"{int(round(n)):,}".replace(',', ' ')
