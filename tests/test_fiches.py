"""Tests du paquet de fiches (lot ac-paquet-fiches) : cas synthetiques, sans les zips de la release. Le rejeu reel du
repertoire se prouve par la commande `render/build_fiches.py`."""
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from render import directory, fiches

PAGE_W, PAGE_H = directory.PAGE_PX


def png_bytes(arr_or_im, **kw):
    im = arr_or_im if isinstance(arr_or_im, Image.Image) else Image.fromarray(arr_or_im)
    buf = io.BytesIO()
    im.save(buf, 'PNG', **kw)
    return buf.getvalue()


def white_page():
    return Image.new('RGB', (PAGE_W, PAGE_H), (255, 255, 255))


def svg_with(arp):
    return f'<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">{arp}</text></svg>'


# ---------- table des theatres ----------
def test_resolve_theatre():
    assert fiches.resolve_theatre('GermanyColdWar') == ('GermanyColdWar', 'GermanyCW')
    assert fiches.resolve_theatre('germanycoldwar') == ('GermanyColdWar', 'GermanyCW')
    assert fiches.resolve_theatre('SYRIA') == ('Syria', 'Syria')


def test_resolve_theatre_unknown_stops():
    with pytest.raises(fiches.FichesError):
        fiches.resolve_theatre('Caucasus')


# ---------- ARP ----------
def test_read_arp_north_east():
    lat, lon = fiches.read_arp(svg_with("ARP N49°22.2' E009°26.4'"))
    assert lat == pytest.approx(49 + 22.2 / 60)
    assert lon == pytest.approx(9 + 26.4 / 60)


def test_read_arp_south_west_negative():
    lat, lon = fiches.read_arp(svg_with("ARP S51°41.5' W057°46.0'"))
    assert lat == pytest.approx(-(51 + 41.5 / 60))
    assert lon == pytest.approx(-(57 + 46.0 / 60))


def test_read_arp_entities_and_missing():
    lat, lon = fiches.read_arp(svg_with('ARP N33&#176;48.0&apos; E036&#176;30.0&apos;'))
    assert (lat, lon) == pytest.approx((33.8, 36.5))
    with pytest.raises(fiches.FichesError):
        fiches.read_arp(svg_with('RWY 09/27'))


def test_read_arp_roundtrips_with_geo_dm():
    from render import geo
    text = f'<text>{geo.arp_text(36.2345, 37.4567)}</text>'
    lat, lon = fiches.read_arp(text)
    assert lat == pytest.approx(36.2345, abs=0.001)
    assert lon == pytest.approx(37.4567, abs=0.001)


# ---------- rattachement ----------
LAT0, LON0 = 50.0, 8.0
M_LAT = 1 / (fiches.EARTH_R_M * np.pi / 180)          # degres de latitude par metre


def field_list(*offsets_m):
    return [[f'T{i}', LAT0 + off * M_LAT, LON0, 'a'] for i, off in enumerate(offsets_m)]


def test_distance_m():
    assert fiches.distance_m(LAT0, LON0, LAT0 + 1000 * M_LAT, LON0) == pytest.approx(1000, abs=0.5)


def test_attach_close():
    name, d = fiches.nearest_airfield(LAT0, LON0, field_list(300, 5000))
    assert name == 'T0' and d == pytest.approx(300, abs=1)


def test_attach_too_far():
    with pytest.raises(fiches.FichesError, match='plus de 500'):
        fiches.nearest_airfield(LAT0, LON0, field_list(800, 5000))


def test_attach_ambiguous():
    with pytest.raises(fiches.FichesError, match='ambigu'):
        fiches.nearest_airfield(LAT0, LON0, field_list(100, 900))


def test_attach_types_a_and_h_both_count():
    lst = [['A', LAT0, LON0, 'a'], ['H', LAT0 + 400 * M_LAT, LON0, 'h']]
    with pytest.raises(fiches.FichesError, match='ambigu'):
        fiches.nearest_airfield(LAT0, LON0, lst)


# ---------- images ----------
def heavy_page():
    """Page degradee finement bruitee : un PNG de plusieurs Mo, mais un JPEG de quelques centaines de Ko."""
    rng = np.random.default_rng(1)
    y, x = np.mgrid[0:PAGE_H, 0:PAGE_W]
    g = 120 + 60 * np.sin(x / 90.0) + 50 * np.cos(y / 70.0)
    arr = np.stack([g, g * 0.9, g * 0.8], -1) + rng.integers(-3, 4, (PAGE_H, PAGE_W, 3))
    return np.clip(arr, 0, 255).astype(np.uint8)


def test_encode_page_small_is_kept_as_is():
    data = png_bytes(white_page())
    name, out, re_enc = fiches.encode_page(data, 'X_10-9.png')
    assert (name, out, re_enc) == ('X_10-9.png', data, False)


def test_encode_page_exactly_at_limit_is_kept(monkeypatch):
    data = png_bytes(heavy_page())
    monkeypatch.setattr(fiches, 'MAX_BYTES', len(data))
    assert fiches.encode_page(data, 'X_10-9.png')[2] is False
    monkeypatch.setattr(fiches, 'MAX_BYTES', len(data) - 1)
    assert fiches.encode_page(data, 'X_10-9.png')[2] is True


def test_encode_page_reencodes_over_one_mb():
    data = png_bytes(heavy_page())
    assert len(data) > 1_000_000
    name, out, re_enc = fiches.encode_page(data, 'Naqoura_10-9.png')
    assert re_enc and name == 'Naqoura_10-9.jpg'
    assert len(out) <= 1_000_000
    im = Image.open(io.BytesIO(out))
    assert im.format == 'JPEG' and im.size == (PAGE_W, PAGE_H)
    first = io.BytesIO()
    Image.open(io.BytesIO(data)).convert('RGB').save(first, 'JPEG', quality=85, optimize=True)
    assert out == first.getvalue()                          # sous la limite des la qualite 85 : pas de recul


def test_encode_page_quality_steps_down(monkeypatch):
    data = png_bytes(heavy_page())
    assert list(fiches.JPEG_QUALITIES) == [85, 80, 75, 70, 65, 60]
    sizes = {}
    for q in fiches.JPEG_QUALITIES:
        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert('RGB').save(buf, 'JPEG', quality=q, optimize=True)
        sizes[q] = buf.getvalue()
    monkeypatch.setattr(fiches, 'MAX_BYTES', len(sizes[75]))   # 85 et 80 depassent, 75 passe
    assert len(sizes[80]) > len(sizes[75])
    _, out, _ = fiches.encode_page(data, 'X_10-9.png')
    assert out == sizes[75]


def test_encode_page_too_heavy_stops(monkeypatch):
    data = png_bytes(heavy_page())
    monkeypatch.setattr(fiches, 'MAX_BYTES', 1000)
    with pytest.raises(fiches.FichesError, match='qualite 60'):
        fiches.encode_page(data, 'X_10-9.png')


def test_encode_page_wrong_size_stops():
    with pytest.raises(fiches.FichesError, match='px'):
        fiches.encode_page(png_bytes(Image.new('RGB', (100, 100), 'white')), 'X_10-9.png')


# ---------- controle de la bande ----------
def fake_site(name, form=directory.SIMPLE):
    return directory.Site(name, None, 0.0, 0.0, 0.0, 0.0, 0, [], form=form)


def page_gray(tiles, empty_dark=()):
    """Page grise (L) : blanche, avec une bande noire a chaque vignette (site, position) de `tiles`."""
    arr = np.full((PAGE_H, PAGE_W), 255, np.uint8)
    for site, pos in tiles:
        x, y, w, h = directory.tile_box(site.form, pos)
        arr[y:y + directory.BAND_H, x:x + w] = 20
    for pos in empty_dark:
        x, y, w, h = directory.tile_box(directory.SIMPLE, pos)
        arr[y:y + directory.BAND_H, x:x + w] = 20
    return arr


def test_band_mean_rectangle():
    arr = np.full((PAGE_H, PAGE_W), 255, np.uint8)
    arr[100:110, 200:260] = 0
    assert fiches.band_mean(arr, 230, 105) == 0
    assert fiches.band_mean(arr, 230 + 60, 105) == 255


def test_check_page_ok():
    entries = [(fake_site('A'), 1), (fake_site('B'), 4)]
    fiches.check_page(7, page_gray(entries), entries)


def test_check_page_wide_and_tall_cover_cells():
    wide = [(fake_site('W', directory.WIDE), 1), (fake_site('S'), 3)]
    fiches.check_page(8, page_gray(wide), wide)
    tall = [(fake_site('T', directory.TALL), 1), (fake_site('U', directory.TALL), 2)]
    fiches.check_page(9, page_gray(tall), tall)


def test_check_page_band_not_dark_names_page_and_cell():
    entries = [(fake_site('A'), 1), (fake_site('B'), 2)]
    gray = page_gray(entries[:1])                              # la vignette B n'a pas de bande
    with pytest.raises(fiches.FichesError, match=r'page 12, case \(rangee 0, colonne 1\)'):
        fiches.check_page(12, gray, entries)


def test_check_page_empty_cell_not_white_names_page_and_cell():
    entries = [(fake_site('A'), 1)]
    gray = page_gray(entries, empty_dark=(4,))
    with pytest.raises(fiches.FichesError, match=r'page 5, case \(rangee 1, colonne 1\)'):
        fiches.check_page(5, gray, entries)


# ---------- le rejeu reel de la mise en page (pydcs) ----------
@pytest.mark.parametrize('theatre,n_sites,n_pages,n_index', [('GermanyColdWar', 108, 30, 3), ('Syria', 141, 44, 3)])
def test_replay_directory_counts(theatre, n_sites, n_pages, n_index):
    sites, pages, idx = fiches.replay_directory(theatre)
    assert (len(sites), len(pages), idx) == (n_sites, n_pages, n_index)
    assert 'HMed22' not in {s.name for s in sites}
    assert sum(len(p['entries']) for p in pages) == n_sites


# ---------- le paquet de bout en bout (zips synthetiques) ----------
@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    af = tmp_path / 'airfields.zip'
    he = tmp_path / 'helicopters.zip'
    page = png_bytes(white_page())
    with zipfile.ZipFile(af, 'w') as z:
        for stem, arp, suffixes in (('Alpha_Field', "ARP N50°00.0' E008°00.0'", ('10-9', '10-9A', '10-9B')),
                                    ('Beta', "ARP N50°30.0' E008°30.0'", ('10-9',))):
            for s in suffixes:
                z.writestr(f'pack/png/{stem}_{s}.png', page)
                z.writestr(f'pack/svg/{stem}_{s}.svg', svg_with(arp))
                z.writestr(f'pack/pdf/{stem}_{s}.pdf', b'%PDF')
    with zipfile.ZipFile(he, 'w') as z:
        z.writestr('pack/heliports/GermanyColdWar_heliports_directory_p04.jpg', b'jpeg')
    airfields = {'format': 'x', 'theatres': {'GermanyCW': [['Alpha', 50.0, 8.0, 'a'], ['Beta Base', 50.5, 8.5, 'h'],
                                                           ['H FRG 1', 51.0, 9.0, 'h']]}}
    aj = tmp_path / 'airfields.json'
    aj.write_text(json.dumps(airfields), encoding='utf-8')
    cadre = [0.0515, 0.1008, 0.4376, 0.4056]
    monkeypatch.setattr(fiches, 'heliport_pages', lambda theatre, zpath, airfields: {
        'H FRG 1': (4, cadre, (he, 'pack/heliports/GermanyColdWar_heliports_directory_p04.jpg'))})
    monkeypatch.setattr(fiches, 'read_member', lambda ref, _real=fiches.read_member: (
        page if ref[1].endswith('.jpg') else _real(ref)))
    return af, he, aj


def test_build_package_index_shape(synthetic):
    af, he, aj = synthetic
    pkg = fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')
    assert list(pkg.index) == ['format', 'theatre', 'edition', 'fiches']
    assert pkg.index['format'] == 'dcs-fiches-v1' and pkg.index['theatre'] == 'GermanyCW'
    assert pkg.index['edition'] == 'AC-V1.4.2'
    f = pkg.index['fiches']
    assert list(f) == ['Alpha', 'Beta Base', 'H FRG 1']
    assert f['Alpha'] == {'pages': [{'titre': '10-9', 'fichier': 'Alpha_Field_10-9.png'},
                                    {'titre': '10-9A', 'fichier': 'Alpha_Field_10-9A.png'},
                                    {'titre': '10-9B', 'fichier': 'Alpha_Field_10-9B.png'}]}
    page = f['H FRG 1']['pages'][0]
    assert list(page) == ['titre', 'fichier', 'cadre']
    assert page['titre'] == '4' and page['cadre'] == [0.0515, 0.1008, 0.4376, 0.4056]
    assert (pkg.n_charts, pkg.n_chart_pages, pkg.n_heliports) == (2, 4, 1)
    assert set(pkg.files) == {'Alpha_Field_10-9.png', 'Alpha_Field_10-9A.png', 'Alpha_Field_10-9B.png', 'Beta_10-9.png',
                              'GermanyColdWar_heliports_directory_p04.jpg'}


def test_index_json_format(synthetic):
    af, he, aj = synthetic
    pkg = fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')
    text = fiches.index_json(pkg.index)
    assert json.loads(text) == pkg.index
    assert text.startswith('{\n "format": "dcs-fiches-v1",\n "theatre": "GermanyCW",\n "edition": "AC-V1.4.2",\n "fiches": {\n  "Alpha"')


def test_write_package_zip_at_root_and_no_overwrite(synthetic, tmp_path):
    af, he, aj = synthetic
    pkg = fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')
    out = tmp_path / 'out'
    folder, zpath = fiches.write_package(pkg, out, 'germanycoldwar')
    assert folder == out / 'GermanyColdWar'
    assert zpath.name == 'DCS_Airfield_Charts_GermanyColdWar_1985_route_planner.zip'
    with zipfile.ZipFile(zpath) as z:
        infos = {i.filename: i for i in z.infolist()}
    assert set(infos) == set(pkg.files) | {'index.json'}
    assert all('/' not in n for n in infos)
    assert infos['index.json'].compress_type == zipfile.ZIP_DEFLATED
    assert all(i.compress_type == zipfile.ZIP_STORED for n, i in infos.items() if n != 'index.json')
    assert sorted(p.name for p in folder.iterdir()) == sorted(set(pkg.files) | {'index.json'})
    with pytest.raises(fiches.FichesError, match='non vide'):
        fiches.write_package(pkg, out, 'GermanyColdWar')


def test_write_package_existing_empty_folder_is_fine(synthetic, tmp_path):
    af, he, aj = synthetic
    pkg = fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')
    (tmp_path / 'out2' / 'GermanyColdWar').mkdir(parents=True)
    fiches.write_package(pkg, tmp_path / 'out2', 'GermanyColdWar')


def test_two_charts_on_same_airfield_stop(synthetic, tmp_path):
    af, he, aj = synthetic
    with zipfile.ZipFile(af, 'a') as z:
        z.writestr('pack/png/Gamma_10-9.png', png_bytes(white_page()))
        z.writestr('pack/svg/Gamma_10-9.svg', svg_with("ARP N50°00.1' E008°00.0'"))
    with pytest.raises(fiches.FichesError, match='meme terrain'):
        fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')


def test_chart_too_far_from_any_airfield_stops(synthetic):
    af, he, aj = synthetic
    with zipfile.ZipFile(af, 'a') as z:
        z.writestr('pack/png/Far_10-9.png', png_bytes(white_page()))
        z.writestr('pack/svg/Far_10-9.svg', svg_with("ARP N45°00.0' E003°00.0'"))
    with pytest.raises(fiches.FichesError, match='Far'):
        fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')


def test_chart_without_arp_stops(synthetic):
    af, he, aj = synthetic
    with zipfile.ZipFile(af, 'a') as z:
        z.writestr('pack/png/NoArp_10-9.png', png_bytes(white_page()))
        z.writestr('pack/svg/NoArp_10-9.svg', svg_with('nothing here'))
    with pytest.raises(fiches.FichesError, match='NoArp'):
        fiches.build_package('GermanyColdWar', af, he, aj, 'AC-V1.4.2')


def test_unknown_theatre_stops(synthetic):
    af, he, aj = synthetic
    with pytest.raises(fiches.FichesError):
        fiches.build_package('Caucasus', af, he, aj, 'AC-V1.4.2')
