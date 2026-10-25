"""Criteres de reception C1 a C8, C15 et C16 sur les trois echantillons (Konya, Adana, Ramat David), 1985.
Executes quand DCS_CHARTS_SAMPLES designe le dossier `echantillons/` du lot."""
import html
import io
import re
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np
import pytest

from render import analysis, compose, export, svgdoc

NS = {'svg': 'http://www.w3.org/2000/svg', 'ink': 'http://www.inkscape.org/namespaces/inkscape'}
INK_GROUPMODE = '{http://www.inkscape.org/namespaces/inkscape}groupmode'
INK_LABEL = '{http://www.inkscape.org/namespaces/inkscape}label'
SVG = '{http://www.w3.org/2000/svg}'


def texts(svg):
    return [html.unescape(t) for t in re.findall(r'<text[^>]*>([^<]*)</text>', svg)]


def page(charts, base, pid):
    return [p for p in charts[base][1] if p.id == pid][0]


def lengths(data):
    return sorted((round(r.length), r) for r in data.runways)


# ---------- C1 ----------
def test_c1_konya_two_runways_east_one_ok_west_to_the_west(charts):
    data = charts['Konya'][0]
    assert len(data.runways) == 2
    by_name = {e.name: e for e in data.ends}
    r_east = data.runways[by_name['01R'].rw]
    r_west = data.runways[by_name['01L'].rw]
    assert r_east.length == pytest.approx(3380, abs=20)
    assert np.mean([r_west.p0[1], r_west.p1[1]]) < np.mean([r_east.p0[1], r_east.p1[1]])      # 01L/19R la plus a l'ouest


@pytest.mark.xfail(strict=True, reason='ECART C1 (addendum 2) : avec le prolongement borne a 900 m au-dela des bouts de la '
                   'candidate de Hough (1 221 m sur 3 368 m), la piste 01L/19R mesure 3 036 m au lieu de 3 368 m (+- 20)')
def test_c1_konya_west_runway_length(charts):
    data = charts['Konya'][0]
    r_west = data.runways[{e.name: e for e in data.ends}['01L'].rw]
    assert r_west.length == pytest.approx(3368, abs=20)


# ---------- C2 ----------
def test_c2_konya_bearings_and_variation_1985(charts):
    data, pages = charts['Konya']
    mags = {e.name: e.mag for e in data.ends}
    assert mags['01L'] == pytest.approx(13.5, abs=0.2) and mags['01R'] == pytest.approx(13.5, abs=0.2)
    assert mags['19L'] == pytest.approx(193.5, abs=0.2) and mags['19R'] == pytest.approx(193.5, abs=0.2)
    t = texts(pages[0].svg)
    assert 'VAR 3°E (1985)' in t and 'VAR 3°E' in t and '(1985)' in t


def test_c2_konya_2026(work):
    data = analysis.analyze(work, 'Syria', 'Konya', 2026, log=lambda *a: None)
    pages = compose.build_pages(data, log=lambda *a: None)
    mags = {e.name: e.mag for e in data.ends}
    assert mags['01R'] == pytest.approx(11.0, abs=0.2) and mags['19L'] == pytest.approx(191.0, abs=0.2)
    assert 'VAR 6°E (2026)' in texts(pages[0].svg)


# ---------- C3 ----------
def test_c3_konya_header_and_beacons(charts):
    data, pages = charts['Konya']
    t = texts(pages[0].svg)
    assert data.icao == 'LTAN'
    assert "ARP N37°58.7' E032°33.7'" in t
    assert abs(data.elev_ft - 3407) <= 3 and f'ÉLÉV. {data.elev_ft} FT' in t
    assert data.tower == {'vhf': '132.400', 'uhf': '225.000', 'fm': '38.900', 'hf': '3.800'}
    for s in ('132.400', '225.000', '38.900', '3.800', 'KON 88X', 'KNY 111.20 CH49X', 'KNY 390', 'KONYA TOWER'):
        assert s in t, s
    assert [(b.kind, b.callsign, b.text) for b in data.beacons] == [('TACAN', 'KON', '88X'), ('VOR/DME', 'KNY', '111.20 CH49X'),
                                                                    ('NDB', 'KNY', '390')]
    ils = {e.name: e.ils for e in data.ends}
    assert ils == {'01L': 'IKNY 108.90', '01R': None, '19L': None, '19R': None}


# ---------- C4 ----------
def numbers_on_pages(pages):
    return [t for p in pages for t in re.findall(r'<text[^>]*class="num"[^>]*>([^<]*)</text>', p.svg)]


def test_c4_konya_parkings_and_pages(charts):
    data, pages = charts['Konya']
    assert len(data.parkings) == 152 and abs(data.n_shelter - 48) <= 4
    assert pages[0].info['zones'] == 7 and pages[0].info['mode'] == 3
    assert [p.id for p in pages] == ['10-9', '10-9A', '10-9B']


# ---------- C5 ----------
def test_c5_konya_taxiways(charts):
    data, pages = charts['Konya']
    assert data.osm_kept == ['A', 'A1', 'A2', 'A3', 'B', 'B1', 'B2', 'B3', 'C', 'C1', 'C2', 'C3', 'D1', 'D2', 'D3', 'E', 'F', 'H', 'N', 'S']
    labels = pages[0].info['labels']
    assert len(labels) >= 18
    from shapely.geometry import Point, Polygon
    from render.page import rw_axes
    # aucune etiquette sur une piste : on teste les centres sur le rectangle de chaque piste, en coordonnees page
    view_pts = {}
    for nm, (cx, cy) in labels.items():
        view_pts[nm] = (cx, cy)
    svg = pages[0].svg
    root = ET.fromstring(svg.encode('utf-8'))
    rw_layer = [g for g in root if g.get(INK_LABEL) == 'Runways'][0]
    polys = []
    for el in rw_layer.iter():
        if el.tag == SVG + 'polygon':
            pts = [tuple(map(float, p.split(','))) for p in el.get('points').split()]
            polys.append(Polygon(pts))
        elif el.tag == SVG + 'path' and el.get('d'):
            nums = [float(v) for v in re.findall(r'-?\d+\.?\d*', el.get('d'))]
            polys.append(Polygon(list(zip(nums[0::2], nums[1::2]))))
    assert polys, 'surfaces de pistes introuvables dans le calque Runways'
    for nm, xy in view_pts.items():
        assert not any(p.buffer(0.0).contains(Point(xy)) for p in polys), nm


# ---------- C6 ----------
def test_c6_adana(charts):
    data, pages = charts['Adana']
    assert len(data.runways) == 1 and data.runways[0].length == pytest.approx(2752, abs=20)
    mags = {e.name: e.mag for e in data.ends}
    assert set(mags) == {'05', '23'}
    assert mags['05'] == pytest.approx(50.1, abs=0.2) and mags['23'] == pytest.approx(230.2, abs=0.2)
    assert data.icao == 'LTAF'
    assert len(pages) == 1 and pages[0].info['mode'] == 2
    assert len(data.parkings) == 39 and data.n_shelter == 0
    assert data.osm_kept == ['A', 'A1', 'B', 'B1', 'C', 'D', 'D1', 'D2', 'E']
    assert [(b.kind, b.callsign, b.text) for b in data.beacons] == [('VOR/DME', 'ADA', '112.70'), ('NDB', 'ADN', '395')]
    assert {e.name: e.ils for e in data.ends}['05'] == 'IADA 108.70'


# ---------- C7 ----------
def test_c7_ramat_david(charts):
    data, pages = charts['Ramat_David']
    assert sorted(round(r.length) for r in data.runways) == pytest.approx(sorted([2428, 2412, 2404]), abs=25) or \
        all(any(abs(r.length - v) <= 25 for r in data.runways) for v in (2428, 2412, 2404))
    names = sorted(e.name for e in data.ends)
    assert names == ['09', '11', '15', '27', '29', '33']
    mags = {e.name: e.mag for e in data.ends}
    assert mags['11'] == pytest.approx(105.4, abs=0.3) and mags['15'] == pytest.approx(141.6, abs=0.3)
    assert mags['09'] == pytest.approx(83.8, abs=0.3)
    assert data.icao == 'LLRD'
    assert len(data.parkings) == 86 and abs(data.n_shelter - 33) <= 4
    assert [p.id for p in pages] == ['10-9', '10-9A']                 # 10-9 + 1 page
    assert pages[0].info['reduced'] and all(0.70 <= k < 1.0 for k in pages[0].info['reduced'].values())
    assert data.osm_kept == [] and pages[0].info['labels'] == {}
    assert [(b.kind, b.callsign, b.text) for b in data.beacons] == [('VORTAC', 'RMD', '113.70 CH84X'), ('NDB', 'RMD', '368')]
    assert {e.name: e.ils for e in data.ends}['33'] == 'RMD 111.10'


# ---------- C16 ----------
def pydcs_slot_names(work, theatre, base):
    from render import dcsdata
    terrain = dcsdata.load_terrain(theatre)
    import json
    from pathlib import Path
    bj = json.loads((Path(work.work_dir) / 'out' / theatre.lower() / base / 'base.json').read_text(encoding='utf-8'))
    cx, cz = bj['extent_rn4']['center']
    ap = dcsdata.match_airport(terrain, cx, cz)
    return [s.name for s in dcsdata.airport_facts(ap).slots]


@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_c16_every_pydcs_parking_has_exactly_one_number(charts, work, base):
    slots = pydcs_slot_names(work, 'Syria', base)
    drawn = Counter(numbers_on_pages(charts[base][1]))
    assert drawn == Counter(slots)                                     # doublons de slot_name compris
    assert sum(drawn.values()) == len(slots) == len(charts[base][0].parkings)


def test_c16_numbers_are_inside_the_page_and_the_frame(charts):
    for base, (data, pages) in charts.items():
        for p in pages:
            for x, y in re.findall(r'<text x="([-\d.]+)" y="([-\d.]+)"[^>]*class="num"', p.svg):
                assert 6.0 <= float(x) <= 142.0 and 20.0 <= float(y) <= 205.0, (base, p.id)


# ---------- C15 ----------
LABELS = svgdoc.LAYERS


def layer_children(svg):
    root = ET.fromstring(svg.encode('utf-8'))
    return root, [c for c in root if c.tag != SVG + 'defs']


@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_c15_layer_structure(charts, base):
    for p in charts[base][1]:
        root, kids = layer_children(p.svg)
        assert 'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"' in p.svg
        assert kids and all(k.tag == SVG + 'g' and k.get(INK_GROUPMODE) == 'layer' for k in kids), p.id
        labels = [k.get(INK_LABEL) for k in kids]
        assert all(l in LABELS for l in labels), labels
        assert labels == sorted(labels, key=LABELS.index), labels                # ordre de la table
        assert len(set(labels)) == len(labels)
        assert all(len(k) > 0 for k in kids)                                      # un calque vide est omis
        for k in kids:
            if k.get(INK_LABEL) in ('Pavement', 'Runways', 'Runway markings', 'Buildings', 'Navaids', 'Parking',
                                    'Parking numbers', 'Runway bearings', 'Taxiway labels') and p.id == '10-9':
                assert k.get('clip-path'), (p.id, k.get(INK_LABEL))
        if p.id != '10-9' or p.info.get('mode') == 2:
            ins = [k for k in kids if k.get(INK_LABEL) == 'Insets'][0]
            subs = [g.get(INK_LABEL) for g in ins if g.tag == SVG + 'g' and g.get(INK_GROUPMODE) == 'layer']
            assert subs and all(re.fullmatch(r'P\d+', s) for s in subs), subs


def test_c15_pngs_identical_without_layers(charts):
    """Le rendu sans calques (un seul groupe decoupe pour les calques 2 a 10) donne le meme PNG."""
    import cairosvg
    export.ensure_fonts()
    pg = page(charts, 'Adana', '10-9')
    root, kids = layer_children(pg.svg)
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    flat = ET.Element(root.tag, {k: v for k, v in root.attrib.items()})
    clip_ref = None
    for d in root.findall(SVG + 'defs'):
        flat.append(d)
    clipped = ET.Element(SVG + 'g')
    inserted = False
    for k in kids:
        ref = k.get('clip-path')
        if ref:
            clip_ref = ref
            if not inserted:
                flat.append(clipped)
                inserted = True
            for c in list(k):
                clipped.append(c)
        else:
            if k.get(INK_LABEL) == 'Insets' and inserted is False:
                pass
            for c in list(k):
                flat.append(c)
    clipped.set('clip-path', clip_ref)
    # l'ordre des calques non decoupes (Insets, Frame...) suit celui des calques decoupes : meme ordre de dessin
    a = cairosvg.svg2png(bytestring=pg.svg.encode('utf-8'), output_width=1748, output_height=2480)
    b = cairosvg.svg2png(bytestring=ET.tostring(flat, encoding='utf-8'), output_width=1748, output_height=2480)
    from PIL import Image
    ia = np.asarray(Image.open(io.BytesIO(a)).convert('RGB')).astype(int)
    ib = np.asarray(Image.open(io.BytesIO(b)).convert('RGB')).astype(int)
    assert ia.shape == ib.shape == (2480, 1748, 3)
    diff = (np.abs(ia - ib).max(axis=2) > 0).mean()
    assert diff <= 0.001, diff


# ---------- C8 ----------
def test_c8_files(charts, tmp_path):
    from pypdf import PdfReader
    from PIL import Image
    expected = {'Konya': 3, 'Adana': 1, 'Ramat_David': 2}
    for base, (data, pages) in charts.items():
        pdfs = []
        for p in pages:
            svg, pdf, png = export.export_page(p.svg, tmp_path / f'{base}_{p.id}')
            r = PdfReader(str(pdf))
            box = r.pages[0].mediabox
            assert float(box.width) == pytest.approx(419.5, abs=0.5) and float(box.height) == pytest.approx(595.3, abs=0.5)
            fonts = set()
            for pg in r.pages:
                res = pg['/Resources'].get_object()
                for f in res.get('/Font', {}).values():
                    f = f.get_object()
                    desc = f.get('/FontDescriptor')
                    if desc is None and '/DescendantFonts' in f:
                        desc = f['/DescendantFonts'][0].get_object()['/FontDescriptor']
                    desc = desc.get_object()
                    assert any(k in desc for k in ('/FontFile', '/FontFile2', '/FontFile3')), f.get('/BaseFont')
                    fonts.add(str(f.get('/BaseFont')))
            assert fonts, 'aucune police dans le PDF'
            assert Image.open(png).size == (1748, 2480)
            pdfs.append(pdf)
        out = tmp_path / f'{base}_charts.pdf'
        export.merge_pdfs(pdfs, out)
        assert len(PdfReader(str(out)).pages) == expected[base]
