"""Lot ac-germany : module pydcs et nom affiche d'un theatre, revetement de l'aerodrome seul.
Les tests purs (tables, masque synthetique) tournent sans echantillons ; les deux derniers executent quand
DCS_CHARTS_SAMPLES designe le dossier `echantillons/` du lot."""
import html
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from render import analysis, dcsdata, directory, geo, georef, page, svgdoc, vector


def texts(svg):
    return [html.unescape(t) for t in re.findall(r'<text[^>]*>([^<]*)</text>', svg)]


# ---------- 4.1 : le module pydcs ----------
def test_pydcs_module_table():
    assert geo.theatre_module('GermanyColdWar') == 'germany'
    assert geo.theatre_module('Syria') == 'syria'
    assert geo.theatre_module('germanycoldwar') == 'germany'            # casse et espaces sans effet sur la table
    assert geo.theatre_module('Persian Gulf') == 'persiangulf'          # hors table : la regle d'avant


def test_germany_cold_war_loads_through_the_table():
    t = dcsdata.load_terrain('GermanyColdWar')
    assert t.name == 'GermanyCW' and len(t.airports) > 100
    proj = geo.Projection.for_theatre('GermanyColdWar')
    lat, lon = proj.to_latlon(0.0, 0.0)
    assert 40 < lat < 60 and 0 < lon < 25


# ---------- 4.2 : le nom affiche ----------
def test_display_name_table():
    assert geo.theatre_display('GermanyColdWar') == 'GERMANY COLD WAR'
    assert geo.theatre_display('Syria') == 'SYRIA'
    assert geo.theatre_display('PersianGulf') == 'PERSIANGULF'         # hors table : theatre.upper()


def _header_texts(theatre):
    data = SimpleNamespace(theatre=theatre, airport_name='Bitburg', icao='ETAD', heli_mode=False, icao_known=True,
                           decl=2.0, year=1985.0, elev_ft=1204, lat=49.9, lon=6.5, tower={}, beacons=[])
    doc = svgdoc.Doc()
    page.header(doc, data, '10-9', 'AÉRODROME', bands=False)
    return texts(doc.render())


def test_chart_header_uses_the_display_name():
    assert 'GERMANY COLD WAR · DCS' in _header_texts('GermanyColdWar')
    assert 'SYRIA · DCS' in _header_texts('Syria')


def test_directory_title_uses_the_display_name(monkeypatch):
    seen = []
    monkeypatch.setattr(directory, 'draw_text', lambda d, xy, s, *a, **k: seen.append(s))
    directory.page_frame('GermanyColdWar', 'x', 'y')
    directory.page_frame('Syria', 'x', 'y')
    assert 'GERMANY COLD WAR' in seen and 'SYRIA' in seen and 'GERMANYCOLDWAR' not in seen


# ---------- 4.3 : le revetement de l'aerodrome ----------
MPP = 0.5
G = georef.Georef(x_top=1000.0, z_left=0.0, mpp=MPP, width=2200, height=700)        # x : ligne, z : colonne
RUNWAY = ((900.0, 100.0), (900.0, 600.0))                  # piste de 500 m le long de z, axe en x = 900 (ligne 199,5)


def _px(m):
    return int(round(m / MPP))


def _block(mask, x, z, wx, wz):
    """Pose un rectangle de revetement de wx x wz m dont le coin nord-ouest est en (x, z)."""
    r0, c0 = _px(1000.0 - x), _px(z)
    mask[r0:r0 + _px(wx), c0:c0 + _px(wz)] = True
    return mask


def _scene():
    m = np.zeros((G.height, G.width), bool)
    _block(m, 905, 100, 10, 500)                           # la piste : bande de 10 m de large sur l'axe
    return m


def _kept(out, x, z):
    r, c = G.xz_to_rc(x, z)
    return bool(out[int(round(r)), int(round(c))])


def test_area_linked_to_the_runway_is_kept():
    m = _scene()
    _block(m, 900, 300, 120, 12)                           # taxiway de 12 m : part de la piste et descend de 120 m vers le sud
    _block(m, 780, 280, 40, 60)                            # aire de stationnement au bout du taxiway, a 100 m de l'axe et plus
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [])
    assert (total, dropped) == (1, 0)                      # une seule composante : piste + taxiway + aire
    assert _kept(out, 800, 310) and _kept(out, 899, 310)


def test_area_within_30_m_of_the_axis_is_kept_and_beyond_is_dropped():
    m = _scene()
    _block(m, 880, 350, 5, 30)                             # separee de la piste, 20 a 25 m de l'axe : gardee
    _block(m, 855, 350, 5, 30)                             # separee, 40 a 45 m de l'axe : ecartee
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [])
    assert (total, dropped) == (3, 1)
    assert _kept(out, 877, 360) and not _kept(out, 852, 360)


def test_runway_band_stops_at_the_ends_of_the_runway():
    m = _scene()
    _block(m, 905, 640, 10, 40)                            # dans le prolongement de l'axe, a 40 m du bout (z = 600) : hors de la bande
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [])
    assert dropped == 1 and not _kept(out, 900, 660)


def test_area_at_59_m_of_a_parking_is_kept_and_61_m_is_dropped():
    m = _scene()
    _block(m, 700, 300, 10, 20)                            # 190 m de l'axe : seul un parking peut la garder
    _block(m, 700, 900, 10, 20)
    parking_near = [(695.0, 300.0 - 59.0)]                 # a 59 m du bord ouest de la premiere aire
    parking_far = [(695.0, 900.0 - 61.0)]                  # a 61 m du bord ouest de la seconde
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], parking_near + parking_far)
    assert (total, dropped) == (3, 1)
    assert _kept(out, 695, 310) and not _kept(out, 695, 910)


def test_isolated_area_at_200_m_is_dropped():
    m = _scene()
    _block(m, 700, 300, 20, 20)                            # a 200 m de l'axe, sans parking a moins de 60 m
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [(950.0, 300.0)])    # un parking a 250 m de l'aire
    assert (total, dropped) == (2, 1)
    assert not _kept(out, 690, 310) and _kept(out, 900, 300)


def test_connectivity_is_eight_neighbours():
    m = _scene()                                           # la piste occupe les lignes 190 a 209, colonnes 200 a 1199 (z 100 a 600)
    m[210, 1200] = True                                    # un pixel en diagonale du coin de la piste, au-dela de son bout
    m[211:221, 1200:1210] = True                           # un bloc accroche a ce pixel : relie a la piste par la diagonale seule
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [])
    assert (total, dropped) == (1, 0) and out[215, 1205]


def test_filter_never_adds_pavement_and_is_a_noop_without_candidates_to_drop():
    m = _scene()
    out, total, dropped = vector.airfield_pavement(m, G, [RUNWAY], [])
    assert (total, dropped) == (1, 0) and np.array_equal(out, m)
    out2, _, _ = vector.airfield_pavement(_block(m.copy(), 700, 300, 20, 20), G, [RUNWAY], [])
    assert not (out2 & ~_block(m.copy(), 700, 300, 20, 20)).any()


def test_constants_are_the_cadrage_values():
    assert (vector.RUNWAY_NEAR_M, vector.PARKING_NEAR_M) == (30.0, 60.0)


# ---------- 4.3 sur des captures reelles (echantillons) ----------
def _mosaic_mask(work, base):
    from PIL import Image
    folder = Path(work.work_dir) / 'out' / 'syria' / base
    return vector.pavement_mask(np.asarray(Image.open(folder / 'mosaic.png').convert('RGB')), georef.georef_from_base(georef.load_base_json(folder)).mpp)


def test_base_without_runway_keeps_its_whole_pavement(work):
    """Mode helicoptere (At Tanf, Kharab Ishk) : aucune piste detectee, la regle ne s'applique pas."""
    for base in ('At_Tanf', 'Kharab_Ishk'):
        log = []
        data = analysis.analyze(work, 'Syria', base, 1985, log=log.append)
        assert not data.runways and 'revetement : aucune piste detectee, masque non filtre' in log, base
        assert np.array_equal(data.masks['pav'], _mosaic_mask(work, base)), base


def test_airfield_pavement_is_a_subset_of_the_full_mask(work, charts):
    data, _ = charts['Konya']
    assert not (data.masks['pav'] & ~_mosaic_mask(work, 'Konya')).any()
