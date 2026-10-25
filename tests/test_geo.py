"""Projection, declination (C9), formats d'en-tete."""
import pytest

from render import geo

import wmm

SIX_POINTS = {'Konya': (37.97775, 32.56149), 'Adana': (36.98, 35.28), 'Ramat David': (32.667, 35.183),
              'Damas': (33.41, 36.52), 'Bitburg': (49.95, 6.57), 'Paris': (48.85, 2.35)}
IGRF_1985 = (3.29, 3.39, 2.83, 3.00, -2.18, -3.69)       # valeurs du chef de projet
IGRF_2025 = (5.76, 5.70, 5.05, 5.14, 3.04, 1.88)


def test_projection_arp_konya():
    """L'ARP pydcs de Konya, projete en lat/lon, donne l'en-tete attendu et revient au meme point."""
    from render import dcsdata
    terrain = dcsdata.load_terrain('Syria')
    ap = dcsdata.match_airport(terrain, 343259.0, -282828.0)
    assert ap is not None and ap.name == 'Konya'
    proj = geo.Projection.for_theatre('Syria')
    lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
    assert geo.arp_text(lat, lon) == "ARP N37°58.7' E032°33.7'"
    assert (lat, lon) == pytest.approx((37.97775, 32.56149), abs=2e-4)
    x, z = proj.to_xz(lat, lon)
    assert (x, z) == pytest.approx((ap.position.x, ap.position.y), abs=0.01)


def test_convergence_is_small_and_signed():
    proj = geo.Projection.for_theatre('Syria')
    c = proj.convergence(343259.0, -282828.0)
    assert -4.5 < c < -3.5                     # Konya : -3,97 deg (grille tournee vers l'ouest du nord vrai)


def test_formats():
    assert geo.dm(37.97775, 'N', 'S', 2) == "N37°58.7'"
    assert geo.dm(32.56149, 'E', 'W', 3) == "E032°33.7'"
    assert geo.var_text(3.29, 1985) == ('VAR 3°E', '(1985)')
    assert geo.var_text(-2.4, 1987.5) == ('VAR 2°W', '(1987.5)')
    assert geo.thin(11089) == '11 089'


def test_declination_igrf14_values():
    for (name, (lat, lon)), a, b in zip(SIX_POINTS.items(), IGRF_1985, IGRF_2025):
        assert geo.declination(lat, lon, 1985) == pytest.approx(a, abs=0.03), name
        assert geo.declination(lat, lon, 2025) == pytest.approx(b, abs=0.03), name


def test_declination_close_to_dcs_wmm(samples):
    """C9 : IGRF-14 a moins de 0,15 deg de la synthese WMM des fichiers de DCS aux six points."""
    cof = samples / 'dcs_install' / 'Data' / 'MagVar' / 'COF'
    for name, (lat, lon) in SIX_POINTS.items():
        for year, f in ((1985, cof / '1985'), (2025, cof / '2025')):
            d_wmm = wmm.declination(f, lat, lon, year + 0.5)
            assert abs(geo.declination(lat, lon, year) - d_wmm) < 0.15, (name, year)
