"""Lot ac-variante-contexte : la page 10-8 « VICINITY » (render/vicinity.py), le halo des textes posés sur le fond F10, les outils qui
lisent les noms de pages (republish, update_coords, fiches). Les tests fabriquent de petites captures (quelques centaines de pixels) et
une analyse synthétique : ils s'exécutent sans les échantillons, sauf ceux qui lancent `build_chart.py` sur Konya (DCS_CHARTS_SAMPLES)."""
import base64
import html
import io
import json
import math
import os
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pytest
from PIL import Image
from pypdf import PdfReader
from shapely.geometry import box

from render.fonts import pt
from render import (analysis, build_chart, compose, dcsdata, directory, export, fiches, geo, georef, page, republish, runways, svgdoc,
                    update_coords, vicinity)
from render.svgdoc import Doc, with_halo

THEATRE = 'GermanyColdWar'
LAT0, LON0 = 52.4, 11.8                         # ARP : sur une minute de latitude et de longitude (des graduations dans un petit cadre)
FRAME = (11.0, 38.0, 126.0, 140.0)
MPP, MW, MH = 20.0, 240, 300                    # mosaïque de test : 240 x 300 px à 20 m/px (4 800 x 6 000 m)
TEXT_RE = re.compile(r'<text\b[^>]*>.*?</text>', re.S)
HALO07_RE = re.compile(r'<text\b[^>]*stroke-width="0\.7"[^>]*class="halo"[^>]*>.*?</text>', re.S)
LAYER_RE = re.compile(r'<g inkscape:groupmode="layer" id="[^"]*" inkscape:label="([^"]*)"[^>]*>(.*?)\n</g>', re.S)
WHITE_RECT = 'rx="1" fill-opacity="0.85"'
HALO_LAYERS = ('Navaids', 'Runway bearings', 'Towns', 'Parking numbers')


def quiet(*a):
    pass


def flat(pdf_page):
    """Le texte d'une page de PDF, en capitales, sans blancs (l'en-tête est écrit lettre espacée)."""
    return re.sub(r'\s+', '', pdf_page.extract_text().upper())


def layers_of(svg):
    return {html.unescape(k): v for k, v in LAYER_RE.findall(svg)}


def texts_of(chunk):
    """[(x, y, contenu, est un halo)] des <text> d'un morceau de SVG."""
    return [(re.search(r' x="([^"]*)"', t).group(1), re.search(r' y="([^"]*)"', t).group(1),
             html.unescape(re.sub(r'<[^>]+>', '', t)), 'class="halo"' in t.split('>')[0]) for t in TEXT_RE.findall(chunk)]


def halo_report(svg):
    """Textes posés sur le fond F10, par calque : [(calque, textes, halos, textes sans halo identique juste avant eux)].
    Posés sur le fond : caps, libellés de balises, villes, graduation (calque `Frame & grid` avant les rectangles blancs), numéros
    d'aire. Exemptés (posés sur un aplat) : désignations de piste, étiquettes jaunes de taxiway, nord et échelle."""
    out = []
    for name, chunk in layers_of(svg).items():
        if name == 'Frame & grid':
            chunk = chunk.split(WHITE_RECT)[0]
        elif name not in HALO_LAYERS:
            continue
        ts = texts_of(chunk)
        missing = [t[2] for k, t in enumerate(ts) if not t[3] and not (k > 0 and ts[k - 1][3] and ts[k - 1][:3] == t[:3])]
        out.append((name, sum(1 for t in ts if not t[3]), sum(1 for t in ts if t[3]), missing))
    return out


# ---------- une analyse synthétique et une petite capture élargie ----------
@pytest.fixture(scope='module')
def proj():
    return geo.Projection.for_theatre(THEATRE)


def make_data(proj, runway=True, beacon=True):
    """ChartData synthétique : une piste est-ouest de 2 000 m (09 / 27), une balise NDB, un taxiway A, centrée sur l'ARP."""
    ax, az = proj.to_xz(LAT0, LON0)
    g = georef.Georef(ax + MH * MPP / 2, az - MW * MPP / 2, MPP, MW, MH)
    rws, ends, ways = [], [], []
    if runway:
        rws = [runways.Runway((ax, az - 1000.0), (ax, az + 1000.0), 40.0, axis_dir=(0.0, 1.0))]
        ends = [analysis.End('09', 90.0, 90.0, rws[0].p0, 0), analysis.End('27', 270.0, 270.0, rws[0].p1, 0)]
        ways = [{'ref': 'A', 'pts_xz': np.array([[ax - 120.0, az - 700.0], [ax - 120.0, az + 700.0]]), 'length': 1400.0}]
    beacons = [analysis.Beacon('NDB', 'ABC', '350', ax + 300.0, az + 500.0)] if beacon else []
    return analysis.ChartData(
        theatre=THEATRE, base='Testbase', airport_name='Testbase', icao='TEST', year=1985.0, lat=LAT0, lon=LON0, arp=(ax, az),
        elev_ft=100, tower={'vhf': '126.100', 'uhf': '252.800', 'fm': '41.200', 'hf': '3.775'}, beacons=beacons, decl=2.0, conv=0.0,
        runways=rws, ends=ends, parkings=[], georef=g, pav_rings=[], bld_rings=[],
        masks={'pav': np.zeros((MH, MW), bool), 'bld': np.zeros((MH, MW), bool), 'clear': None},
        osm_ways=ways, osm_kept=['A'] if ways else [], projection=proj, icao_known=True)


def write_capture(folder, data, size=(MW, MH), color=None, seed=1):
    """Capture élargie de test : base.json (georef_version 2) et mosaic.png couvrant l'emprise du georef de `data` en `size` pixels
    (sans pixel (222, 222, 222) sauf si `color`)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    g = data.georef
    w, h = size
    mpp = g.width * g.mpp / w
    cover = {'x_min': g.x_top - h * mpp, 'x_max': g.x_top, 'z_min': g.z_left, 'z_max': g.z_left + w * mpp}
    (folder / 'base.json').write_text(json.dumps({'base': data.base, 'mosaic_coverage_m': cover, 'm_per_px': mpp, 'georef_version': 2,
                                                  'mosaic_px': [w, h], 'margins_px_lthr_b': [0, 0, 0, 0]}), encoding='utf-8')
    if color is None:
        a = np.random.default_rng(seed).integers(0, 200, (h, w, 3), dtype=np.uint8)
    else:
        a = np.empty((h, w, 3), np.uint8)
        a[:] = color
    Image.fromarray(a).save(folder / 'mosaic.png')
    return folder


def town(dx_m, dy_m, name):
    """Une ville à dx_m à l'est et dy_m au nord de l'ARP, aux mêmes degrés que la formule des distances (111,2 km par degré)."""
    lat = LAT0 + dy_m / 1000 / 111.2
    return (name, lat, LON0 + dx_m / 1000 / (111.2 * math.cos(math.radians(LAT0))))


class _Arp:
    lat, lon, theatre = LAT0, LON0, THEATRE


@pytest.fixture(scope='module')
def built(proj, tmp_path_factory):
    """La 10-8 de la base synthétique : une ville dans le cadre, des renvois (dont deux dans la même direction) et une ville à 20,4 km."""
    root = tmp_path_factory.mktemp('vic')
    data = make_data(proj)
    folder = write_capture(root / 'contexte' / 'Testbase', data)
    towns = [town(1000, 500, 'Innenstadt'), town(0, 6000, 'Nordburg'), town(12000, 0, 'Ostheim'), town(15000, 0, 'Ostdorf'),
             town(0, 20400, 'Fernstadt'), town(-9000, -9000, 'Suedwest')]
    logs = []
    pg = vicinity.build_vicinity_page(data, FRAME, folder, log=logs.append, towns=towns)
    return data, folder, pg, logs, towns


# ---------- 2.1 où la page existe ----------
class _Cfg:
    def __init__(self, w):
        self.work_dir = str(w)


def test_context_folder_needs_base_json_and_mosaic(tmp_path):
    cfg = _Cfg(tmp_path)
    d = tmp_path / 'out' / 'germanycoldwar' / 'contexte' / 'Celle'
    assert vicinity.context_folder(cfg, 'GermanyColdWar', 'Celle') is None
    d.mkdir(parents=True)
    (d / 'base.json').write_text('{}')
    assert vicinity.context_folder(cfg, 'GermanyColdWar', 'Celle') is None          # sans mosaic.png
    (d / 'mosaic.png').write_bytes(b'x')
    assert vicinity.context_folder(cfg, 'GermanyColdWar', 'Celle') == d
    assert vicinity.context_folder(cfg, 'GermanyColdWar', 'Kastrup') is None
    assert vicinity.context_folder(cfg, 'Syria', 'Celle') is None


def test_context_georef_is_the_one_of_its_own_base_json_and_refuses_another_size(proj, tmp_path):
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data)
    g = vicinity.context_georef(folder)
    assert g == data.georef
    Image.new('RGB', (10, 10)).save(folder / 'mosaic.png')
    with pytest.raises(analysis.ChartError, match='differente de base.json'):
        vicinity.context_georef(folder)


# ---------- 2.2 le cadre et l'échelle ----------
def test_scale_fills_the_frame_with_the_wider_mosaic(built):
    data, folder, pg, logs, _ = built
    g = vicinity.context_georef(folder)
    s = max(FRAME[2] / (g.width * g.mpp), FRAME[3] / (g.height * g.mpp))
    assert pg.info['scale'] == pytest.approx(s) and pg.info['frame'] == FRAME
    view = vicinity.plan_view(g, FRAME)
    assert view.s == pytest.approx(s) and (view.ox, view.oy) == (FRAME[0] + FRAME[2] / 2, FRAME[1] + FRAME[3] / 2)
    assert (view.xc, view.zc) == pytest.approx((g.x_top - g.height * g.mpp / 2, g.z_left + g.width * g.mpp / 2))
    assert f'10-8 plan : echelle {s:.5f} mm/m (1:{1000 / s:,.0f}), cadre 126.0 x 140.0 mm' in logs


@pytest.mark.parametrize('size', [(MW * 2, MH), (MW, MH * 2)])
def test_scale_is_the_larger_ratio_and_the_frame_stays_inside_a_wide_or_tall_mosaic(proj, tmp_path, size):
    data = make_data(proj)
    data.georef = georef.Georef(data.georef.x_top, data.georef.z_left, MPP * 0.5, size[0] * 2, size[1] * 2)
    folder = write_capture(tmp_path / 'c', data, size=size)
    g = vicinity.context_georef(folder)
    view = vicinity.plan_view(g, FRAME)
    W, H = g.width * g.mpp, g.height * g.mpp
    assert view.s == pytest.approx(max(FRAME[2] / W, FRAME[3] / H))
    assert FRAME[2] / view.s <= W + 1e-6 and FRAME[3] / view.s <= H + 1e-6          # le cadre est dans la mosaïque
    assert FRAME[2] / view.s == pytest.approx(W) or FRAME[3] / view.s == pytest.approx(H)     # et la remplit dans un sens


# ---------- 2.3 le fond ----------
def test_background_is_one_image_covering_exactly_the_frame(built):
    _, _, pg, _, _ = built
    chunk = layers_of(pg.svg)['F10 map']
    imgs = re.findall(r'<image\b[^>]*>', chunk)
    assert len(imgs) == 1 and pg.svg.count('<image') == 1
    fx, fy, fw, fh = FRAME
    assert f'x="{svgdoc.f(fx)}" y="{svgdoc.f(fy)}" width="{svgdoc.f(fw)}" height="{svgdoc.f(fh)}"' in imgs[0]
    href = re.search(r' href="data:image/jpeg;base64,([^"]*)"', imgs[0]).group(1)
    im = Image.open(io.BytesIO(base64.b64decode(href)))
    assert im.format == 'JPEG' and im.size == (round(fw * page.FOND_PX_MM), round(fh * page.FOND_PX_MM))
    assert ' xlink:href="data:image/jpeg;base64,' in imgs[0]


def test_ground_has_no_gray_pixel_inside_the_mosaic_and_gray_outside_it(proj, tmp_path):
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data)
    g = vicinity.context_georef(folder)
    view = vicinity.plan_view(g, FRAME)
    im = Image.open(folder / 'mosaic.png').convert('RGB')
    a = np.asarray(vicinity.ground_image(view, FRAME, g, im))
    assert a.shape == (round(FRAME[3] * page.FOND_PX_MM), round(FRAME[2] * page.FOND_PX_MM), 3)
    assert not (a == 222).all(axis=2).any()                                      # aucun pixel gris : le cadre est dans la mosaïque
    big = compose.View(view.s * 0.5, view.xc, view.zc, view.ox, view.oy)         # un cadre deux fois trop grand : du gris autour
    b = np.asarray(vicinity.ground_image(big, FRAME, g, im))
    assert (b[0, 0] == 222).all() and (b[-1, -1] == 222).all()
    assert (b == 222).all(axis=2).sum() > 0.5 * b.shape[0] * b.shape[1]


@pytest.mark.parametrize('color', [(100, 150, 80), (200, 30, 30), (20, 20, 20)])
def test_background_is_softened_then_lightened_by_045(proj, tmp_path, color):
    """P3 : l'éclaircissement 0,45 vers le blanc, vérifié sur une petite mosaïque unie."""
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data, color=color)
    g = vicinity.context_georef(folder)
    view = vicinity.plan_view(g, FRAME)
    got = np.asarray(vicinity.background_image(folder, g, view, FRAME)).reshape(-1, 3).astype(int)
    soft = directory.soften(Image.new('RGB', (4, 4), color)).getpixel((0, 0))
    want = np.array([int(round(0.55 * c + 0.45 * 255)) for c in soft])
    assert vicinity.LIGHTEN == 0.45 and np.abs(got - want).max() <= 1
    ground = vicinity.ground_image(view, FRAME, g, Image.open(folder / 'mosaic.png').convert('RGB'))
    plain = np.asarray(directory.soften(ground)).reshape(-1, 3).astype(int)
    assert (got >= plain).all() and (got > plain).any()                           # plus clair, jamais plus sombre


def test_lighten_is_image_blend_towards_white():
    im = Image.new('RGB', (3, 3), (10, 100, 200))
    want = Image.blend(im, Image.new('RGB', im.size, (255, 255, 255)), 0.45)
    assert (np.asarray(vicinity.lighten(im)) == np.asarray(want)).all()


def test_the_wider_mosaic_never_enters_the_analysis_and_is_resampled_without_a_window(proj, tmp_path, monkeypatch):
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data)
    monkeypatch.setattr(analysis, 'analyze', lambda *a, **k: pytest.fail('analysis.analyze sur la mosaique elargie'))
    for name in ('pavement_mask', 'building_mask', 'clear_marking_mask'):
        monkeypatch.setattr(analysis.vector, name, lambda *a, **k: pytest.fail('masque calcule sur la mosaique elargie'))
    windows = []
    real = np.empty
    monkeypatch.setattr(np, 'empty', lambda *a, **k: windows.append(a[0]) or real(*a, **k))
    vicinity.build_vicinity_page(data, FRAME, folder, log=quiet, towns=[])
    assert not any(isinstance(w, tuple) and len(w) == 3 and w[0] >= MH for w in windows)   # aucune copie de la mosaïque entière


# ---------- 2.4 ce qui est dessiné ----------
def test_drawn_and_not_drawn(built):
    _, _, pg, _, _ = built
    lay = layers_of(pg.svg)
    for absent in ('Pavement', 'Buildings', 'Parking', 'Parking numbers', 'Insets'):
        assert absent not in lay
    for tag in ('layer-pavement', 'layer-buildings', 'layer-parking', 'layer-insets'):
        assert tag not in pg.svg
    assert len(re.findall(r'<polygon ', lay['Runways'])) == 1
    assert sorted(t[2] for t in texts_of(lay['Runway bearings']) if not t[3]) == ['090.0°', '270.0°']      # les deux extrémités
    assert [t[2] for t in texts_of(lay['Navaids']) if not t[3]] == ['NDB ABC', '350']
    assert [t[2] for t in texts_of(lay['Taxiway labels'])] == ['A']
    assert [t[2] for t in texts_of(lay['Runway markings']) if t[2] in ('09', '27')] == ['09', '27']


def test_runways_and_markings_are_those_of_the_10_9_for_the_same_view(proj, tmp_path):
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data)
    pg = vicinity.build_vicinity_page(data, FRAME, folder, log=quiet, towns=[])
    view = vicinity.plan_view(vicinity.context_georef(folder), FRAME)
    lay = {k: [] for k in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Parking')}
    page.draw_scene(view, data, [], [], [], lay)
    got = layers_of(pg.svg)
    assert got['Runways'].strip().split('\n') == lay['Runways']
    assert got['Runway markings'].strip().split('\n') == lay['Runway markings']


def test_a_grass_runway_is_an_outline(proj, tmp_path):
    data = make_data(proj)
    data.runways[0].surface = 'herbe'
    folder = write_capture(tmp_path / 'c', data)
    pg = vicinity.build_vicinity_page(data, FRAME, folder, log=quiet, towns=[])
    poly = re.search(r'<polygon [^>]*>', layers_of(pg.svg)['Runways']).group(0)
    assert 'fill="#FFFFFF"' in poly and 'stroke="#1A1A1A"' in poly


def test_header_legend_footer_and_runway_table(built):
    data, _, pg, _, _ = built
    t = [html.unescape(x) for x in re.findall(r'<text[^>]*>([^<]*)</text>', pg.svg)]
    assert '10-8' in t and 'VICINITY' in t and '10-9' not in t
    assert 'AÉRODROME' not in t and "BASE D'HÉLICOPTÈRES" not in t
    assert 'TOWN WITHIN 20 KM: DISTANCE FROM ARP · BACKGROUND: F10 MAP' in t
    assert not any('FOND : CARTE F10' in x or 'OUVERT' in x for x in t)               # ni la légende des parkings ni celle des héliports
    assert page.footer_text(data, True) in t and 'PAS POUR LA NAVIGATION RÉELLE' in t
    assert 'TESTBASE TOWER' in t and 'BALISES' in t
    assert 'PISTE' in t and 'CAP MAG' in t                                            # le tableau des pistes
    legend = layers_of(pg.svg)['Runway table']
    assert re.search(r'<polygon points="[^"]*" fill="#1A1A1A"/>', legend)               # le triangle du renvoi


def test_north_and_scale_sit_on_their_white_rectangles(built):
    _, _, pg, _, _ = built
    grid = layers_of(pg.svg)['Frame & grid']
    assert grid.count(WHITE_RECT) == 2 and grid.count('fill="#FFFFFF" rx="1"') == 2
    assert len(pg.info['white']) == 2 and pg.info['corners'][0] != pg.info['corners'][1]


# ---------- 2.5 le halo ----------
def test_with_halo_puts_a_white_copy_just_before_the_text():
    t = svgdoc.text(1.5, 2.5, 'ABC', 2.0, 'bold', svgdoc.INK, 'middle')
    h = with_halo(t)
    halo, plain = TEXT_RE.findall(h)
    assert plain == t and h == halo + t
    for needle in ('fill="#FFFFFF"', 'stroke="#FFFFFF"', 'stroke-width="0.7"', 'stroke-linejoin="round"'):
        assert needle in halo
    assert 'paint-order' not in h
    g = page.cap_element('090.0°', 10.0, 20.0, -5.0)
    hg = with_halo(g)
    assert hg.startswith('<g transform="translate(10 20) rotate(-5)">') and hg.endswith('</g>') and hg.count('<text') == 2
    assert [x[3] for x in texts_of(hg)] == [True, False]


def test_every_text_on_the_background_of_the_10_8_has_its_halo(built):
    """P5 (10-8) : le compte des textes posés sur le fond = le compte des halos, calque par calque."""
    _, _, pg, _, _ = built
    rows = {r[0]: r for r in halo_report(pg.svg)}
    assert {'Navaids', 'Runway bearings', 'Towns', 'Frame & grid'} <= set(rows)
    for name, n_text, n_halo, missing in rows.values():
        assert n_text == n_halo and n_text > 0 and not missing, (name, n_text, n_halo, missing)
    assert rows['Runway bearings'][1] == 2                                            # les deux extrémités de la piste
    assert rows['Towns'][1] == 1 + len(pg.info['towns']['renvois'])                  # un nom dans le cadre, un texte par renvoi


def make_f10_data(proj):
    """Une base d'hélicoptères sans piste, sur le fond F10 (mode de la 10-9 qui reçoit le halo), avec une balise et des graduations."""
    d = make_data(proj, runway=False, beacon=False)
    d.georef = georef.Georef(d.arp[0] + 150.0, d.arp[1] - 150.0, 1.0, 300, 300)
    d.heli_mode = True
    d.parkings = [analysis.Parking('1', d.arp[0] + 20.0, d.arp[1] + 20.0, 'heli', length=20.0),
                  analysis.Parking('2', d.arp[0] - 20.0, d.arp[1] - 20.0, 'heli', length=20.0)]
    d.masks = {'pav': np.zeros((300, 300), bool), 'bld': np.zeros((300, 300), bool), 'clear': None}
    d.f10_img = np.random.default_rng(3).integers(0, 200, (300, 300, 3), dtype=np.uint8)
    d.beacons = [analysis.Beacon('NDB', 'ABC', '350', d.arp[0] + 60.0, d.arp[1] - 80.0)]
    return d


def test_the_10_9_on_the_f10_background_has_the_halo(proj):
    """P5 (10-9 en fond F10) : mêmes comptes ; les numéros d'aire gardent leur halo d'origine."""
    d = make_f10_data(proj)
    assert d.on_f10
    (pg,) = compose.build_pages(d, log=quiet)
    rows = {r[0]: r for r in halo_report(pg.svg)}
    assert {'Navaids', 'Frame & grid', 'Parking numbers'} <= set(rows)
    for name, n_text, n_halo, missing in rows.values():
        assert n_text == n_halo and n_text > 0 and not missing, (name, n_text, n_halo, missing)
    assert rows['Parking numbers'][1] == 2


def test_the_10_9_f10_page_only_differs_by_its_halos(proj, monkeypatch):
    """Les halos retirés, la page est celle du moteur sans halo : le halo est la seule différence."""
    d = make_f10_data(proj)
    (pg,) = compose.build_pages(d, log=quiet)
    assert len(HALO07_RE.findall(pg.svg)) > 0
    stripped = HALO07_RE.sub('', pg.svg)
    assert stripped.count('class="halo"') == 2                                        # les halos (0,6 mm) des numéros d'aire, déjà là
    monkeypatch.setattr(compose, 'with_halo', lambda s: s)
    (plain,) = compose.build_pages(d, log=quiet)
    assert plain.svg == stripped


def test_pages_that_are_not_on_the_f10_background_have_no_halo_and_no_towns(proj):
    d = make_data(proj)
    d.parkings = [analysis.Parking('1', d.arp[0] + 50, d.arp[1] + 50, 'open')]
    for pg in compose.build_pages(d, log=quiet):
        assert not HALO07_RE.findall(pg.svg) and 'layer-towns' not in pg.svg           # (les numéros de parking ont leur halo de 0,6 mm)


# ---------- 2.6 les villes ----------
def test_town_distance_follows_the_nearest_town_formula_and_the_20_km_limit(proj):
    d = make_data(proj)
    for dx, dy in ((10000, 0), (0, 12340), (7000, 7000), (-8000, 5000)):
        t = town(dx, dy, 'X')
        (r,) = vicinity.towns_near(d, [t], max_km=1e9)
        assert directory.km_text(r[0]) in directory.nearest_town(LAT0, LON0, [t])
    assert vicinity.towns_near(d, [town(20400, 0, 'Loin')]) == []                    # 20,4 km : ignorée
    assert vicinity.towns_near(d, [town(0, 20400, 'Loin')]) == []
    assert [x[1] for x in vicinity.towns_near(d, [town(19900, 0, 'Pres')])] == ['Pres']
    pile = ('Pile', LAT0 + 20.0 / 111.2, LON0)                                          # exactement 20,0 km : retenue (<= 20,0)
    (r,) = vicinity.towns_near(d, [pile], max_km=20.0 + 1e-9)
    assert r[0] == pytest.approx(20.0)
    assert vicinity.towns_near(d, [('Dehors', LAT0 + 20.0001 / 111.2, LON0)]) == []
    assert [x[1] for x in vicinity.towns_near(d, [town(0, 6000, 'B'), town(0, 3000, 'A'), town(1000, 0, 'C')])] == ['C', 'A', 'B']


def test_a_town_ignored_at_20_4_km_is_nowhere_on_the_page(built):
    _, _, pg, logs, _ = built
    assert 'Fernstadt' not in [p['name'] for p in pg.info['positions']]
    assert 'FERNSTADT' not in pg.svg.upper()


def test_a_theatre_without_towns_has_none(proj):
    d = make_data(proj)
    d.theatre = 'Nowhere'
    assert vicinity.towns_near(d) == []


def test_a_town_inside_the_frame_has_a_dot_and_its_name(built):
    _, _, pg, _, _ = built
    lay = layers_of(pg.svg)['Towns']
    dots = re.findall(r'<circle cx="([^"]*)" cy="([^"]*)" r="([^"]*)" fill="#1A1A1A"/>', lay)
    assert len(dots) == 1 and float(dots[0][2]) == vicinity.TOWN_DOT_R == 0.4         # 0,8 mm de diamètre
    ((nm, (X, Y), anchor),) = pg.info['towns']['inside']
    assert nm == 'Innenstadt' and (float(dots[0][0]), float(dots[0][1])) == pytest.approx((X, Y), abs=1e-3)
    fx, fy, fw, fh = FRAME
    assert fx < X < fx + fw and fy < Y < fy + fh
    names = [t for t in texts_of(lay) if t[2] == 'INNENSTADT' and not t[3]]
    assert len(names) == 1                                                            # son nom en capitales
    assert f'font-size="{svgdoc.f(vicinity.TOWN_SIZE)}"' in lay and 'font-weight="bold"' in lay
    assert vicinity.TOWN_SIZE == page.NAVAID_LINES[0][0]                              # la taille des libellés de balises


def test_a_town_outside_the_frame_gets_a_triangle_on_the_edge_and_its_text(built):
    _, _, pg, _, _ = built
    fx, fy, fw, fh = FRAME
    lay = layers_of(pg.svg)['Towns']
    ref = {n: (km, side, off) for n, km, side, off in pg.info['towns']['renvois']}
    assert ref['Nordburg'][:2] == (6, 'T') and ref['Ostheim'][:2] == (12, 'R')
    assert ref['Suedwest'][0] == int(math.hypot(9000, 9000) / 1000 + 0.5) - 0 or ref['Suedwest'][0] in (17, 18)
    shown = [t[2] for t in texts_of(lay) if not t[3]]
    assert 'NORDBURG 6 km' in shown and 'OSTHEIM 12 km' in shown
    polys = [[tuple(map(float, p.split(','))) for p in m.split()] for m in re.findall(r'<polygon points="([^"]*)" fill="#1A1A1A"/>', lay)]
    assert len(polys) == len(ref)
    for pts in polys:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        assert fx < min(xs) and max(xs) < fx + fw and fy < min(ys) and max(ys) < fy + fh           # à l'intérieur du cadre
        assert min(min(xs) - fx, fx + fw - max(xs), min(ys) - fy, fy + fh - max(ys)) < 1.0         # contre le bord
        assert math.dist(pts[1], pts[2]) == pytest.approx(2 * vicinity.TRI_HALF, abs=0.01)
        mid = ((pts[1][0] + pts[2][0]) / 2, (pts[1][1] + pts[2][1]) / 2)
        assert math.dist(pts[0], mid) == pytest.approx(vicinity.TRI_LEN, abs=0.01) == pytest.approx(1.6, abs=0.01)
    assert vicinity.renvoi_text({'name': 'Nordburg', 'km': 6}) == 'NORDBURG 6 km'
    assert vicinity.draw_towns(Doc(), [{'name': 'X', 'dist': 14.5, 'km': 15, 'xy': (500.0, 100.0), 'inside': False}], FRAME,
                               (74.0, 108.0), [], quiet)['renvois'][0][1] == 15


def test_the_distance_in_a_renvoi_is_rounded_to_the_nearest_km():
    for d, n in ((12.49, 12), (12.5, 13), (0.4, 0), (19.99, 20)):
        assert int(d + 0.5) == n
    assert vicinity.renvoi_text({'name': 'Burg', 'km': int(12.5 + 0.5)}) == 'BURG 13 km'


def test_the_triangle_points_towards_the_town_from_the_edge_the_segment_cuts():
    A = (74.0, 108.0)
    fx, fy, fw, fh = FRAME
    for T in ((74.0, -500.0), (900.0, 108.0), (74.0, 900.0), (-700.0, 108.0), (400.0, 300.0), (-300.0, -200.0)):
        P, side, u = vicinity.edge_exit(FRAME, A, T)
        want = (T[0] - A[0], T[1] - A[1])
        L = math.hypot(*want)
        assert u == pytest.approx((want[0] / L, want[1] / L))
        assert {'R': P[0] == pytest.approx(fx + fw), 'L': P[0] == pytest.approx(fx),
                'B': P[1] == pytest.approx(fy + fh), 'T': P[1] == pytest.approx(fy)}[side]
        pl = vicinity.place_renvoi(FRAME, A, T, 'X 5 km', [])
        tip, b1, b2 = pl['tri']
        bc = ((b1[0] + b2[0]) / 2, (b1[1] + b2[1]) / 2)
        d = (tip[0] - bc[0], tip[1] - bc[1])
        assert (d[0] * u[0] + d[1] * u[1]) / math.hypot(*d) == pytest.approx(1.0)            # la pointe va vers la ville


def test_a_renvoi_that_hits_something_slides_along_its_edge(built):
    _, _, pg, _, _ = built
    ref = {n: (km, side, off) for n, km, side, off in pg.info['towns']['renvois']}
    assert ref['Ostheim'][1] == ref['Ostdorf'][1] == 'R'
    assert ref['Ostdorf'][2] != ref['Ostheim'][2]                                           # même direction : le second glisse
    A, T = (74.0, 108.0), (900.0, 108.0)
    first = vicinity.place_renvoi(FRAME, A, T, 'OSTHEIM 12 km', [])
    assert first['offset'] == 0.0 and first['side'] == 'R'
    again = vicinity.place_renvoi(FRAME, A, T, 'OSTDORF 15 km', [first['geom']])
    assert again['offset'] != 0 and again['side'] == 'R' and not again['geom'].intersects(first['geom'])
    fx, fy, fw, fh = FRAME
    assert abs(again['tri'][0][0] - first['tri'][0][0]) < 1e-9                                # il glisse le long du bord : même abscisse
    wall = box(fx + fw - 12, 100, fx + fw, 116)                                               # un obstacle sur la place d'origine
    moved = vicinity.place_renvoi(FRAME, A, T, 'X 5 km', [wall])
    assert moved is not None and moved['offset'] != 0 and not moved['geom'].intersects(wall)


def test_a_renvoi_without_room_is_omitted_and_logged():
    fx, fy, fw, fh = FRAME
    everything = box(fx - 5, fy - 5, fx + fw + 5, fy + fh + 5)
    assert vicinity.place_renvoi(FRAME, (74.0, 108.0), (900.0, 108.0), 'X 5 km', [everything]) is None
    doc, logs = Doc(), []
    pos = [{'name': 'Perdue', 'dist': 14.0, 'km': 14, 'xy': (900.0, 108.0), 'inside': False}]
    info = vicinity.draw_towns(doc, pos, FRAME, (74.0, 108.0), [everything], logs.append)
    assert info['omitted'] == ['Perdue'] and info['renvois'] == []
    assert logs == ['10-8 : renvoi omis : Perdue', '10-8 : villes : 0 dans le cadre, 0 renvois']
    assert 'Towns' not in doc.layers


def test_the_omitted_renvoi_is_journalised_by_the_page_too(proj, tmp_path, monkeypatch):
    data = make_data(proj)
    folder = write_capture(tmp_path / 'c', data)
    monkeypatch.setattr(vicinity, 'SLIDE_STEP_MM', 1e6)                                        # plus de glissement : deux renvois qui se touchent
    logs = []
    pg = vicinity.build_vicinity_page(data, FRAME, folder, log=logs.append, towns=[town(10000, 333, 'Ostheim'), town(19000, 633, 'Ostdorf')])
    assert '10-8 : renvoi omis : Ostdorf' in logs and '10-8 : villes : 0 dans le cadre, 1 renvois' in logs
    assert pg.info['towns']['omitted'] == ['Ostdorf']


def test_the_journal_counts_the_towns(built):
    _, _, pg, logs, _ = built
    n_in, n_ref = len(pg.info['towns']['inside']), len(pg.info['towns']['renvois'])
    assert f'10-8 : villes : {n_in} dans le cadre, {n_ref} renvois' in logs
    assert (n_in, n_ref) == (1, 4)                                                            # Innenstadt ; Nordburg, Ostheim, Ostdorf, Suedwest
    assert not any('renvoi omis' in x for x in logs)


def test_a_renvoi_avoids_the_north_and_scale_rectangles_and_the_caps(built):
    _, _, pg, _, _ = built
    lay = layers_of(pg.svg)['Towns']
    polys = [[tuple(map(float, p.split(','))) for p in m.split()] for m in re.findall(r'<polygon points="([^"]*)" fill="#1A1A1A"/>', lay)]
    for (a, b, w, h) in pg.info['white']:
        for pts in polys:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            assert max(xs) < a or min(xs) > a + w or max(ys) < b or min(ys) > b + h
    blockers = [box(a, b, a + w, b + h) for a, b, w, h in pg.info['white']]
    pl = vicinity.place_renvoi(FRAME, (74.0, 108.0), (74.0, -500.0), 'X 5 km', blockers)
    assert pl is not None and not any(pl['geom'].intersects(b) for b in blockers)


def test_the_corners_of_the_north_and_the_scale_avoid_a_town_in_the_frame(proj, tmp_path):
    d = make_data(proj)
    folder = write_capture(tmp_path / 'c', d)
    view = vicinity.plan_view(vicinity.context_georef(folder), FRAME)
    base = vicinity.build_vicinity_page(d, FRAME, folder, log=quiet, towns=[]).info['corners']
    fx, fy, fw, fh = FRAME
    lat, lon = proj.to_latlon(*view.ground(fx + 8.0, fy + fh - 15.0))                           # dans le coin choisi pour le nord
    pg = vicinity.build_vicinity_page(d, FRAME, folder, log=quiet, towns=[('Coin', lat, lon)])
    assert pg.info['positions'][0]['inside'] and pg.info['corners'] != base


def test_a_town_name_stays_inside_the_frame(proj, tmp_path):
    d = make_data(proj)
    folder = write_capture(tmp_path / 'c', d)
    view = vicinity.plan_view(vicinity.context_georef(folder), FRAME)
    fx, fy, fw, fh = FRAME
    lat, lon = proj.to_latlon(*view.ground(fx + fw - 2.0, fy + 20.0))                         # contre le bord droit
    pg = vicinity.build_vicinity_page(d, FRAME, folder, log=quiet, towns=[('Randdorf', lat, lon)])
    ((nm, xy, anchor),) = pg.info['towns']['inside']
    x, y = float(re.search(r'<text x="([^"]*)"', layers_of(pg.svg)['Towns']).group(1)), 0
    r = page.text_rect(x, 0.0, 'RANDDORF', vicinity.TOWN_SIZE, 'bold', anchor)
    assert anchor == 'end' and fx < r[0] and r[2] < fx + fw


# ---------- le nom de page 10-8 dans les outils ----------
def test_every_tool_that_reads_page_names_accepts_10_8():
    assert republish._PAGE_NAME_ANY.match('X_10-8')['suffix'] == '10-8'
    assert update_coords._PAGE_NAME.match('X_10-8')['suffix'] == '10-8'
    assert fiches._CHART_PNG.match('X_10-8.png')['suffix'] == '10-8'
    assert republish._PAGE_NAME_ANY.match('X_10-9A') and republish._PAGE_NAME_ANY.match('X_11-2')
    assert not republish._PAGE_NAME_ANY.match('X_10-8A') and not update_coords._PAGE_NAME.match('X_11-1')
    names = sorted(['X_11-1', 'X_10-9A', 'X_10-8', 'X_10-9'], key=lambda n: dcsdata.natural_key(n.split('_')[1]))
    assert names == ['X_10-8', 'X_10-9', 'X_10-9A', 'X_11-1']


def konya_latlon():
    p = geo.Projection.for_theatre('Syria')
    ap = next(a for a in dcsdata.load_terrain('Syria').airports.values() if a.name == 'Konya')
    return p.to_latlon(ap.position.x, ap.position.y)


def doc_svg(*parts):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="148mm" height="210mm" viewBox="0 0 148 210">\n<g>\n'
            + '\n'.join(parts) + '\n</g>\n</svg>')


RULE = '<line x1="6" y1="22.8" x2="142" y2="22.8" stroke="#1A1A1A" stroke-width="0.25"/>'


def three_fields(svg, lat, lon, var_field):
    """Le même SVG avec les cinq textes de la ligne d'en-tête (y = 21,2) remplacés par les trois de la version AC-V1.4.1."""
    t = lambda x, s_: svgdoc.text(x, 21.2, s_, pt(7.2), 'bold')
    row = '\n'.join([t(6, 'ÉLÉV. 3392 FT'), t(51.3, geo.arp_text(lat, lon)), t(96.7, var_field)])
    spans = [m.span() for m in re.finditer(r'<text [^>]* y="21.2"[^>]*>.*?</text>', svg)]
    return svg[:spans[0][0]] + row + svg[spans[-1][1]:]


def vicinity_zip(tmp_path, three=False):
    """Zip synthétique d'une chart `Konya` : sa 10-8 (page réelle de la base synthétique, ligne d'en-tête à l'ARP de Konya), sa 10-9
    (cinq champs, une barre de 1 050 m sans fin, nord à t = 0), leurs recueils, une entrée d'un autre type."""
    lat, lon = konya_latlon()
    data = make_data(geo.Projection.for_theatre(THEATRE))
    folder = write_capture(tmp_path / 'cap', data)
    p8 = vicinity.build_vicinity_page(data, FRAME, folder, log=quiet, towns=[town(1000, 500, 'Innenstadt')])
    arp_old = geo.arp_text(data.lat, data.lon)
    svg8 = p8.svg.replace(escape(arp_old), escape(geo.arp_text(lat, lon)))
    assert svg8 != p8.svg
    bar, _ = page.scale_block(94.422, 172.4, 38.0 / 800)                      # barre de 750 m, sans l'étiquette de fin (page publiée avant le lot)
    bar = [e for e in bar if not (e.startswith('<text') and e.endswith('>750 M</text>'))]
    var, yl = geo.var_text(3.29, 1985)
    frame_layer = ('<g inkscape:groupmode="layer" id="layer-frame-grid" inkscape:label="Frame &amp; grid">\n'
                   + '\n'.join([*page.north_arrows(21.0, 156.6, 0.0, 3.29), svgdoc.text(21.0, 160.0, var, pt(5.9), 'bold', svgdoc.INK, 'middle'),
                               svgdoc.text(21.0, 162.6, yl, pt(5.4), 'regular', svgdoc.INK2, 'middle')]) + '\n</g>')
    var_field = f'{var} {yl}'
    svg9 = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *page.header_row('ÉLÉV. 3392 FT', lat, lon, var_field), RULE,
                   *bar, frame_layer)
    if three:                                                                    # AC-V1.4.1 : trois champs, la forme que update_coords relit
        svg8, svg9 = (three_fields(v, lat, lon, var_field) for v in (svg8, svg9))
    root = tmp_path / 'old'
    files = {}
    for name, svg in (('Konya_10-8', svg8), ('Konya_10-9', svg9)):
        s, p, n = export.export_page(svg, root / name)
        files[name] = (s.read_bytes(), p.read_bytes(), n.read_bytes())
    export.merge_pdfs([root / 'Konya_10-8.pdf', root / 'Konya_10-9.pdf'], root / 'Konya_charts.pdf')
    path = tmp_path / 'release.zip'
    with zipfile.ZipFile(path, 'w') as z:
        for name, (s, p, n) in files.items():
            z.writestr(f'pack/png/{name}.png', n)
            z.writestr(f'pack/svg/{name}.svg', s)
            z.writestr(f'pack/pdf/{name}.pdf', p)
        z.writestr('pack/pdf/Konya_charts.pdf', (root / 'Konya_charts.pdf').read_bytes(), zipfile.ZIP_STORED)
        z.writestr('pack/notes.txt', b'unchanged')
    return path, files


def test_republish_keeps_a_10_8_as_it_is_and_first_in_the_collection(tmp_path, capsys):
    """P8 : republish.py accepte un zip qui contient une 10-8."""
    zin, files = vicinity_zip(tmp_path)
    out = tmp_path / 'new.zip'
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 0
    summary = capsys.readouterr().out
    assert 'pages reexportees : 1' in summary and 'pages 10-8 (abords) : 1 recopiees telles quelles' in summary
    assert 'recueils refaits : 1' in summary
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        for sub in ('svg', 'png', 'pdf'):
            n = f'pack/{sub}/Konya_10-8.{sub}'
            assert a.read(n) == b.read(n)                                                       # aucune retouche, aucune réexportation
        assert a.read('pack/notes.txt') == b.read('pack/notes.txt')
        assert b.read('pack/svg/Konya_10-9.svg') != a.read('pack/svg/Konya_10-9.svg')             # la 10-9 est retouchée (fin de barre)
        rec = PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf')))
        assert len(rec.pages) == 2
        assert 'VICINITY' in flat(rec.pages[0])                                  # la 10-8 ouvre le recueil
        assert 'VICINITY' not in flat(rec.pages[1])


def test_republish_stops_if_the_10_8_entries_are_missing(tmp_path, capsys):
    zin = tmp_path / 'v.zip'
    with zipfile.ZipFile(zin, 'w') as z:
        z.writestr('p/svg/Konya_10-8.svg', b'x')
        z.writestr('p/png/Konya_10-8.png', b'x')
        z.writestr('p/pdf/Konya_charts.pdf', b'x')
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(tmp_path / 'o.zip')]) == 1
    assert 'Konya_10-8' in capsys.readouterr().err and not (tmp_path / 'o.zip').exists()


def test_update_coords_accepts_a_10_8(tmp_path):
    """P8 : update_coords.py accepte un zip qui contient une 10-8 (même ligne d'en-tête que la 10-9)."""
    zin, files = vicinity_zip(tmp_path, three=True)
    out = tmp_path / 'u.zip'
    lines, n_pages, n_charts, n_copied = update_coords.update_zip('Syria', zin, out, log=quiet)
    assert set(lines) == {'Konya_10-8', 'Konya_10-9'} and n_pages == 2 and n_charts == 1
    with zipfile.ZipFile(out) as z:
        rec = PdfReader(io.BytesIO(z.read('pack/pdf/Konya_charts.pdf')))
        assert len(rec.pages) == 2 and 'VICINITY' in flat(rec.pages[0])


@pytest.fixture
def fiches_zips(tmp_path, monkeypatch):
    """Comme le paquet synthétique de test_fiches, avec une 10-8 sur la première chart."""
    def png(im):
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        return buf.getvalue()
    pg = png(Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
    af, he = tmp_path / 'airfields.zip', tmp_path / 'helicopters.zip'
    with zipfile.ZipFile(af, 'w') as z:
        for stem, arp, suffixes in (('Alpha_Field', "ARP N50°00.0' E008°00.0'", ('10-9', '10-8', '10-9A')),
                                    ('Beta', "ARP N50°30.0' E008°30.0'", ('10-9',))):
            for s in suffixes:
                z.writestr(f'pack/png/{stem}_{s}.png', pg)
                z.writestr(f'pack/svg/{stem}_{s}.svg', f'<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">{arp}</text></svg>')
                z.writestr(f'pack/pdf/{stem}_{s}.pdf', b'%PDF')
    with zipfile.ZipFile(he, 'w') as z:
        z.writestr('pack/heliports/GermanyColdWar_heliports_directory_p04.jpg', b'jpeg')
    aj = tmp_path / 'airfields.json'
    aj.write_text(json.dumps({'format': 'x', 'theatres': {'GermanyCW': [['Alpha', 50.0, 8.0, 'a'], ['Beta Base', 50.5, 8.5, 'h'],
                                                                       ['H FRG 1', 51.0, 9.0, 'h']]}}), encoding='utf-8')
    monkeypatch.setattr(fiches, 'heliport_pages', lambda theatre, zpath, airfields: {
        'H FRG 1': (4, [0.05, 0.1, 0.4, 0.4], (he, 'pack/heliports/GermanyColdWar_heliports_directory_p04.jpg'))})
    monkeypatch.setattr(fiches, 'read_member', lambda ref, _real=fiches.read_member: (pg if ref[1].endswith('.jpg') else _real(ref)))
    return af, he, aj


def test_fiches_include_the_10_8_among_the_pages_of_the_chart_first(fiches_zips):
    """P8 : fiches.py accepte un zip qui contient des 10-8, en tête des pages de la chart ; le rattachement reste fait par la 10-9."""
    af, he, aj = fiches_zips
    pkg = fiches.build_package('GermanyColdWar', af, he, aj, 'AC-TEST')
    assert [p['titre'] for p in pkg.index['fiches']['Alpha']['pages']] == ['10-8', '10-9', '10-9A']
    assert [p['fichier'] for p in pkg.index['fiches']['Alpha']['pages']][0] == 'Alpha_Field_10-8.png'
    assert [p['titre'] for p in pkg.index['fiches']['Beta Base']['pages']] == ['10-9']
    assert (pkg.n_charts, pkg.n_chart_pages) == (2, 4)


def test_fiches_do_not_attach_a_chart_that_has_a_10_8_but_no_10_9(fiches_zips, tmp_path):
    af, he, aj = fiches_zips
    only8 = tmp_path / 'only8.zip'
    with zipfile.ZipFile(only8, 'w') as z:
        z.writestr('pack/png/Gamma_10-8.png', b'x')
    with pytest.raises(fiches.FichesError, match='Gamma : pas de page 10-9'):
        fiches.attach_charts(fiches.chart_pages([only8]), [['Alpha', 50.0, 8.0, 'a']])


# ---------- build_chart.py : la 10-8 en tête du recueil (échantillons) ----------
@pytest.fixture
def konya_work(samples, tmp_path):
    """Dossier de travail neuf où Konya (échantillons) est lié ; `out/syria/contexte/` est à écrire (aucune écriture dans les échantillons)."""
    w = tmp_path / 'work'
    (w / 'out' / 'syria').mkdir(parents=True)
    os.symlink(samples / 'syria' / 'Konya', w / 'out' / 'syria' / 'Konya', target_is_directory=True)
    (w / 'osm').mkdir()
    os.symlink(samples / 'syria' / 'osm', w / 'osm' / 'syria', target_is_directory=True)
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'work_dir': str(w), 'dcs_install': str(samples / 'dcs_install')}), encoding='utf-8')
    return w, cfg


def run_chart(cfg, out, *extra):
    return build_chart.main(['x', 'Syria', 'Konya', '--config', str(cfg), '--out', str(out), *extra])


def test_build_chart_writes_the_10_8_first_and_leaves_the_other_pages_alone(konya_work, tmp_path, capsys):
    """P1 / P2 : avec sa capture élargie, Konya a une 10-8 en tête du recueil ; ses autres pages sont identiques à l'octet à celles
    qu'elle a sans la capture élargie."""
    w, cfg = konya_work
    assert run_chart(cfg, tmp_path / 'without') == 0
    capsys.readouterr()
    data = analysis.analyze(__import__('config').load_config(["x", "--config", str(cfg)])[0], 'Syria', 'Konya', 1985, log=quiet)
    write_capture(w / 'out' / 'syria' / 'contexte' / 'Konya', data, size=(round(data.georef.width / 20), round(data.georef.height / 20)))
    assert run_chart(cfg, tmp_path / 'with') == 0
    printed = capsys.readouterr().out
    assert '10-8 plan : echelle' in printed and '10-8 : villes :' in printed
    a, b = tmp_path / 'without' / 'syria', tmp_path / 'with' / 'syria'
    names_a = sorted(p.name for p in a.glob('*.svg'))
    assert 'LTAN_Konya_10-8.svg' in [p.name for p in b.glob('*.svg')] and 'LTAN_Konya_10-8.svg' not in names_a
    for n in names_a:
        assert (a / n).read_bytes() == (b / n).read_bytes()                                       # la 10-9 et les autres, à l'octet
    assert len(PdfReader(str(b / 'LTAN_Konya_charts.pdf')).pages) == len(PdfReader(str(a / 'LTAN_Konya_charts.pdf')).pages) + 1
    assert 'VICINITY' in flat(PdfReader(str(b / 'LTAN_Konya_charts.pdf')).pages[0])
    assert printed.index('10-8 : LTAN_Konya_10-8.pdf') < printed.index('10-9 : LTAN_Konya_10-9.pdf')


def test_build_chart_writes_no_10_8_without_the_capture_or_for_approaches_only(konya_work, tmp_path, capsys):
    w, cfg = konya_work
    assert run_chart(cfg, tmp_path / 'without') == 0
    assert not any('10-8' in p.name for p in (tmp_path / 'without' / 'syria').iterdir())
    assert '10-8' not in capsys.readouterr().out
    data = analysis.analyze(__import__('config').load_config(['x', '--config', str(cfg)])[0], 'Syria', 'Konya', 1985, log=quiet)
    write_capture(w / 'out' / 'syria' / 'contexte' / 'Konya', data, size=(100, 80))
    assert run_chart(cfg, tmp_path / 'only', '--approches-seulement') == 0
    assert not any('10-8' in p.name or '10-9' in p.name for p in (tmp_path / 'only' / 'syria').iterdir())
