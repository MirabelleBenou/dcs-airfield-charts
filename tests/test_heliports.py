"""Lot ac-heliports : mode helicoptere, heliports a radio, barre d'echelle graduee, repertoire des heliports.
Executes quand DCS_CHARTS_SAMPLES designe le dossier `echantillons/` du lot (sauf les tests purs)."""
import html
import re

import numpy as np
import pytest

from render import analysis, compose, directory, page, runways


def texts(svg):
    return [html.unescape(t) for t in re.findall(r'<text[^>]*>([^<]*)</text>', svg)]


@pytest.fixture(scope='session')
def heli(work):
    out = {}
    for base in ('Der_Salman', 'Kharab_Ishk', 'At_Tanf'):
        data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
        out[base] = (data, compose.build_pages(data, log=lambda *a: None))
    return out


@pytest.fixture(scope='session')
def sites(work):
    s = directory.list_sites(work, 'Syria', log=lambda *a: None)
    towns = directory.load_towns('Syria')
    for x in s:
        x.town = directory.nearest_town(x.lat, x.lon, towns)
    return {x.name: x for x in s}


# ---------- mode helicoptere ----------
def test_helicopter_mode_der_salman(heli):
    data, pages = heli['Der_Salman']
    assert data.heli_mode and not data.icao_known
    svg = pages[0].svg
    t = texts(svg)
    assert "BASE D'HÉLICOPTÈRES" in t and 'AÉRODROME' not in t
    assert 'AIRES : 20' in t and not any(x.startswith('PARKINGS') for x in t)
    assert "AIRE D'HÉLICOPTÈRE (NUMÉRO DCS)" in t
    assert not any(x in ('OUVERT', 'HÉLICOPTÈRE SEUL') or x.startswith('ABRI') for x in t)
    assert 'Der_Salman' not in t                                    # le coin gauche reste vide
    assert [e.name for e in data.ends] == ['09', '27'] and len(data.runways) == 1
    assert data.runways[0].length == pytest.approx(202, abs=10)


def test_second_pass_is_what_finds_the_helicopter_runway(work, monkeypatch):
    monkeypatch.setattr(runways, 'HELI_SPARE_SEGMENT_MIN_M', None)
    with pytest.raises(analysis.ChartError):
        analysis.analyze(work, 'Syria', 'Der_Salman', 1985, log=lambda *a: None)


def test_every_helicopter_area_has_exactly_one_number(heli):
    for base in ('Der_Salman', 'Kharab_Ishk', 'At_Tanf'):
        data, pages = heli[base]
        nums = re.findall(r'<text[^>]*class="num"[^>]*>([^<]*)</text>', pages[0].svg)
        assert sorted(nums) == sorted(p.name for p in data.parkings)


# ---------- heliports a radio ----------
def test_kharab_ishk_has_no_runway_table(heli):
    data, pages = heli['Kharab_Ishk']
    assert data.runways == [] and data.ends == []
    t = texts(pages[0].svg)
    for head in ('PISTE', 'CAP MAG', 'LONGUEUR × LARGEUR', 'ÉLÉV. SEUIL', 'ILS'):
        assert head not in t
    assert not any(re.fullmatch(r'\d{3}\.\d°', x) for x in t)         # aucune etiquette de cap
    assert 'AIRES : 11' in t and "BASE D'HÉLICOPTÈRES" in t


def test_heliport_without_radio_is_refused(work):
    with pytest.raises(analysis.ChartError, match='helistation'):
        analysis.analyze(work, 'Syria', 'H_med_00', 1985, log=lambda *a: None)


def test_at_tanf_scale_bar_and_north_are_inside_the_frame(heli):
    data, pages = heli['At_Tanf']
    fx, fy, fw, fh = pages[0].info['frame']
    svg = pages[0].svg
    bar = re.findall(r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)" stroke="#1A1A1A" stroke-width="0.35"/>', svg)[-1]
    x1, y1, x2, y2 = map(float, bar)
    assert fx <= x1 < x2 <= fx + fw and fy <= y1 <= fy + fh


# ---------- barre d'echelle ----------
def _labels(s):
    elems, L = page.scale_block(0.0, 0.0, s)
    return elems, L


def test_scale_bar_300_m():
    s = 38.0 / 300.0
    assert page.scale_plan(s) == (300.0, 100.0, 500.0)
    elems, L = _labels(s)
    t = [html.unescape(re.sub(r'<[^>]+>', '', e)) for e in elems if e.startswith('<text')]
    assert t == ['0', '100', '200', '300 M', '0', '500 FT']
    assert L == pytest.approx(38.0)


def test_scale_bar_800_m_keeps_the_original_rule():
    s = 38.0 / 800.0
    assert page.scale_plan(s) == (750.0, None, None)
    elems, L = _labels(s)
    t = [html.unescape(re.sub(r'<[^>]+>', '', e)) for e in elems if e.startswith('<text')]
    assert t == ['0', '500', '0', '1000', '2000 FT']


@pytest.mark.parametrize('span, expect', [(499, (100.0, 500.0, 400.0)), (199, (50.0, 200.0, 150.0)), (99, (25.0, 100.0, 75.0)),
                                           (250, (100.0, 500.0, 200.0)), (60, (25.0, 100.0, 50.0))])
def test_scale_steps(span, expect):
    mstep_fstep_len = page.scale_plan(38.0 / span)
    assert (mstep_fstep_len[1], mstep_fstep_len[2], mstep_fstep_len[0]) == expect


# ---------- repertoire : effacement du label ----------
def test_label_erased_on_a_sample(work):
    from PIL import Image
    from render import georef
    folder = work.work_dir / 'out' / 'syria' / 'H_med_00'
    g = georef.georef_from_base(georef.load_base_json(folder))
    img = np.asarray(Image.open(folder / 'mosaic.png').convert('RGB'))
    s = [x for x in directory.list_sites(work, 'Syria', log=lambda *a: None) if x.name == 'HMed00'][0]
    r, c = (int(round(v)) for v in g.xz_to_rc(s.x, s.z))
    rad = 20.0 / g.mpp

    def blue(im):
        return directory.blue_clusters(im[r - 60:r + 61, c - 60:c + 61], (60, 60), rad)
    out = directory.erase_label(img, r, c, 'HMed00')
    n0, big0 = blue(img)
    n1, big1 = blue(out)
    assert n0 > 3 and big0 > 3                                     # l'icone et le nom sont bien la
    assert big1 <= 3 and n1 < n0
    assert not np.array_equal(img[r - 20:r + 20, c - 20:c + 60], out[r - 20:r + 20, c - 20:c + 60])
    assert np.array_equal(img[:r - 200], out[:r - 200])            # rien d'autre ne bouge


# ---------- repertoire : groupes et formes ----------
def test_forms_on_samples(sites):
    for name in ('HMed00', 'HI14', 'HI15'):
        s = sites[name]
        s.groups = directory.group_aires(s.aires)
        assert directory.classify(s.aires, s.groups) == directory.SIMPLE
    s = sites['HI16']
    assert directory.classify(s.aires, directory.group_aires(s.aires)) == directory.WIDE
    s = sites['HI17']
    gs = directory.group_aires(s.aires)
    assert len(gs) == 2 and directory.classify(s.aires, gs) == directory.TWO
    assert [directory.group_label(s.aires, g) for g in gs] == ['01–03', '04–06']


def _synthetic(points, length=42.0, width=34.0):
    return [directory.Aire(f'{i + 1:02d}', x, z, length, width) for i, (x, z) in enumerate(points)]


def test_tall_form_synthetic():
    aires = _synthetic([(0.0, 0.0), (250.0, 0.0), (500.0, 0.0)])            # 3 aires en colonne, reliees a 250 m
    groups = directory.group_aires(aires)
    assert len(groups) == 1
    assert directory.classify(aires, groups) == directory.TALL


def test_three_groups_stop():
    aires = _synthetic([(0.0, 0.0), (1000.0, 0.0), (2000.0, 0.0)])
    with pytest.raises(directory.DirectoryError):
        directory.classify(aires, directory.group_aires(aires))


def test_group_too_large_stops():
    aires = _synthetic([(0.0, 0.0), (250.0, 0.0), (250.0, 250.0), (0.0, 250.0), (250.0, 500.0), (500.0, 500.0)])
    with pytest.raises(directory.DirectoryError):
        directory.classify(aires, directory.group_aires(aires))


def test_series_and_order():
    assert directory.series_of('HMed01') == 'HMed' and directory.series_of('H_med_orig_04') == 'H_med_orig_'
    assert directory.series_of('Sanliurfa Heliport') == directory.OTHERS
    names = ['HMed10', 'HMed02', 'HI01', 'Sanliurfa Heliport', 'HS22']
    order = sorted(names, key=lambda n: (directory.series_key(directory.series_of(n)), directory.dcsdata.natural_key(n)))
    assert order[0] == 'HI01' and order[-1] == 'Sanliurfa Heliport'


# ---------- repertoire : villes ----------
def test_nearest_town_two_known_points(sites):
    assert sites['HMed01'].town == '0,3 km SW de Xylofagou'
    assert sites['HI14'].town == '6,5 km N de Nazareth'


def test_direction8():
    assert directory.direction8(0, 1) == 'N' and directory.direction8(1, 0) == 'E' and directory.direction8(-1, -1) == 'SW'
    assert directory.direction8(1, 1) == 'NE' and directory.direction8(0, -1) == 'S' and directory.direction8(-1, 0.1) == 'W'


# ---------- repertoire : placement ----------
def test_placement_five_simple_then_wide():
    pages = directory.place_forms([directory.SIMPLE] * 5 + [directory.WIDE])
    assert pages == [[(0, 1), (1, 2), (2, 3), (3, 4)], [(4, 1), (5, 3)]]


def test_placement_no_backfill_and_tall():
    f = directory.SIMPLE
    pages = directory.place_forms([f, directory.TALL, f, f])
    assert pages == [[(0, 1), (1, 2), (2, 3)], [(3, 1)]]            # la case 4 est prise par la colonne ; le suivant ouvre une page
    pages = directory.place_forms([directory.TALL, f, f])
    assert pages == [[(0, 1), (1, 2), (2, 4)]]


# ---------- repertoire : page ----------
def test_directory_page_smoke(sites):
    ss = [sites[n] for n in ('HMed00', 'HMed01', 'HMed02', 'HMed03')]
    pages = directory.build_pages(ss)
    assert len(pages) == 1 and pages[0]['series'] == 'HMed'
    stats = []
    cache = {}

    def get(site):
        if site.name not in cache:
            cache[site.name] = directory.load_site_mosaic(site)
        return cache[site.name]
    im = directory.draw_directory_page('Syria', pages[0], get, stats)
    assert im.size == (1748, 2480)
    a = np.asarray(im).astype(int)
    red = (abs(a[..., 0] - 170) < 12) & (abs(a[..., 1] - 35) < 12) & (abs(a[..., 2] - 35) < 12)
    assert red.sum() > 1500
    assert all(sorted(set(s.drawn)) == [x.name for x in s.aires] for s in ss)


# ---------- lot ac-heliports-fond : plan sur fond F10 ----------
def _layer(svg, label):
    m = re.search(r'<g inkscape:groupmode="layer" id="[^"]*" inkscape:label="' + re.escape(html.escape(label)) + r'"[^>]*>(.*?)\n</g>', svg, re.S)
    return m.group(1) if m else ''


def test_at_tanf_is_on_f10_background(heli):
    data, pages = heli['At_Tanf']
    assert data.on_f10 and len(pages) == 1 and pages[0].id == '10-9'
    assert pages[0].info['mode'] == 1 and pages[0].info['scale'] == pytest.approx(0.2, abs=1e-5)
    svg = pages[0].svg
    fx, fy, fw, fh = pages[0].info['frame']
    layer = _layer(svg, 'F10 map')
    assert layer.count('<image') == 1 and '<path' not in layer
    assert f'width="{page.f(fw)}" height="{page.f(fh)}"' in layer and 'preserveAspectRatio="none"' in layer
    assert 'href="data:image/jpeg;base64,' in layer
    assert '<path' not in _layer(svg, 'Pavement') and '<path' not in _layer(svg, 'Buildings')
    assert len(re.findall(r'<circle[^>]*stroke="#AA2323"', _layer(svg, 'Parking'))) == 2
    assert 'clip-path=' in re.search(r'<g [^>]*inkscape:label="F10 map"[^>]*>', svg).group(0)


def test_kharab_ishk_circles_and_numbers(heli):
    data, pages = heli['Kharab_Ishk']
    svg = pages[0].svg
    assert len(re.findall(r'<circle[^>]*stroke="#AA2323"', _layer(svg, 'Parking'))) == 11
    assert len(re.findall(r'class="num"', svg)) == 11
    for p in data.parkings:
        assert p.length > 0


def test_circle_radii_follow_the_area_length(heli):
    data, pages = heli['Kharab_Ishk']
    s = pages[0].info['scale']
    radii = sorted(float(r) for r in re.findall(r'<circle cx="[-\d.]+" cy="[-\d.]+" r="([\d.]+)" fill="none" stroke="#AA2323" stroke-width="0.4"/>',
                                                 _layer(pages[0].svg, 'Parking')))
    want = sorted(p.length / 2 * s - 0.2 for p in data.parkings)
    assert len(radii) == len(want) and all(abs(a - b) <= 0.01 for a, b in zip(radii, want))


def test_corner_blocks_sit_on_white_rectangles(heli):
    data, pages = heli['At_Tanf']
    grid = _layer(pages[0].svg, 'Frame & grid')
    assert len(re.findall(r'<rect [^>]*fill="#FFFFFF" rx="1" fill-opacity="0.85"/>', grid)) == 2


def test_der_salman_is_not_on_f10_background(heli):
    data, pages = heli['Der_Salman']
    assert not data.on_f10 and data.f10_img is None
    assert 'F10 map' not in pages[0].svg and '#AA2323' not in pages[0].svg


def test_f10_background_has_no_blue_cluster_near_the_airport(heli):
    data, pages = heli['At_Tanf']
    fx, fy, fw, fh = pages[0].info['frame']
    view = compose.plan_view_f10(data, pages[0].info['frame'])
    im = np.asarray(compose.fond_image(view, pages[0].info['frame'], data, soften=False))
    cx, cy = (round((v) * page.FOND_PX_MM) for v in (view.pt(*data.arp)[0] - fx, view.pt(*data.arp)[1] - fy))
    n, big = directory.blue_clusters(im, (cx, cy), 20.0 * view.s * page.FOND_PX_MM)
    assert big <= 3
