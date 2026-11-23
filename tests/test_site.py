"""Tests du site statique (lot ac-site-pages) : regles du cadrage sur des cas synthetiques, sans les zips de la release.
Le passage complet sur les vraies entrees se prouve par la commande `render/build_site.py`."""
import io
import json
import os
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from render import site


# ---------- nom et OACI ----------
def test_split_name_prefix_in_header():
    assert site.split_name('LCRA_Akrotiri', ['LCRA', 'SYRIA · DCS', 'AKROTIRI']) == ('LCRA', 'Akrotiri')


def test_split_name_underscores_become_spaces():
    assert site.split_name('OJRW_H4_Emergency', ['OJRW']) == ('OJRW', 'H4 Emergency')


def test_split_name_prefix_absent_from_header():
    # quatre caracteres [A-Z0-9] suivis de `_`, mais le Header n'a pas ce texte : pas d'OACI, le nom est le stem
    assert site.split_name('Marj_as_Sultan_North', ['MARJ-X']) == ('', 'Marj as Sultan North')
    assert site.split_name('HC01_Foo_Bar', ['SYRIA · DCS']) == ('', 'HC01 Foo Bar')


def test_split_name_stem_without_prefix():
    assert site.split_name('Abu_al-Duhur', ['ABU']) == ('', 'Abu al-Duhur')
    assert site.split_name('Tal_Siman', ['TAL_']) == ('', 'Tal Siman')       # `Tal_` n'a pas quatre caracteres [A-Z0-9]


def test_split_name_prefix_must_be_exact_text():
    assert site.split_name('LCRA_Akrotiri', ['LCRA ']) == ('', 'LCRA Akrotiri')
    assert site.split_name('LCRA_Akrotiri', ['ELCRA']) == ('', 'LCRA Akrotiri')


def test_header_texts_reads_only_the_header_layer():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
           b'<g inkscape:label="Other"><text>NOPE</text></g>'
           b'<g inkscape:groupmode="layer" inkscape:label="Header"><text x="1">LCRA</text><line/><text>A &amp; B</text></g></svg>')
    assert site.header_texts(svg) == ['LCRA', 'A & B']


# ---------- ordre des pages ----------
def test_page_order():
    pages = ['11-2H', '11-1H', '11-10', '10-9B', '11-2', '10-9', '11-1', '10-8', '10-9A']
    assert sorted(pages, key=site.page_order_key) == ['10-8', '10-9', '10-9A', '10-9B', '11-1', '11-2', '11-10', '11-1H', '11-2H']


def test_page_order_unknown_name_stops():
    with pytest.raises(site.SiteError):
        site.page_order_key('12-1')


# ---------- radiobalises ----------
@pytest.mark.parametrize('beacon, chip', [
    ({'type': 'TACAN', 'callsign': 'AKR', 'channel': 107, 'frequency': None, 'runway': None}, 'TACAN 107X AKR'),
    ({'type': 'AIRPORT_TACAN', 'callsign': 'XYZ', 'channel': 59, 'frequency': None, 'runway': None}, 'TACAN 59X XYZ'),
    ({'type': 'VORTAC', 'callsign': 'ABC', 'channel': None, 'frequency': 113_700_000, 'runway': None}, 'VORTAC 113.70 ABC'),
    ({'type': 'VOR_DME', 'callsign': 'ADA', 'channel': None, 'frequency': 112_700_000, 'runway': None}, 'VOR/DME 112.70 ADA'),
    ({'type': 'VOR', 'callsign': 'VVV', 'channel': None, 'frequency': 108_050_000, 'runway': None}, 'VOR 108.05 VVV'),
    ({'type': 'DME', 'callsign': 'DDD', 'channel': None, 'frequency': 114_000_000, 'runway': None}, 'DME 114.00 DDD'),
    ({'type': 'ILS_LOCALIZER', 'callsign': 'IAK', 'channel': None, 'frequency': 109_700_000, 'runway': '28'}, 'ILS 28 109.70 IAK'),
    ({'type': 'PRMG_LOCALIZER', 'callsign': 'PRM', 'channel': 21, 'frequency': 108_000_000, 'runway': '09'}, 'PRMG 09 ch21 PRM'),
    ({'type': 'RSBN', 'callsign': 'RS', 'channel': 7, 'frequency': None, 'runway': None}, 'RSBN ch7 RS'),
    ({'type': 'AIRPORT_HOMER', 'callsign': 'AK', 'channel': None, 'frequency': 365_000, 'runway': None}, 'NDB 365 AK'),
    ({'type': 'AIRPORT_HOMER_WITH_MARKER', 'callsign': 'BOD', 'channel': None, 'frequency': 351_000, 'runway': None}, 'NDB 351 BOD'),
    ({'type': 'HOMER', 'callsign': 'HHH', 'channel': None, 'frequency': 410_000, 'runway': None}, 'NDB 410 HHH'),
    ({'type': 'ILS_FAR_HOMER', 'callsign': 'FF', 'channel': None, 'frequency': 330_000, 'runway': '28'}, 'NDB 330 FF (outer)'),
    ({'type': 'ILS_NEAR_HOMER', 'callsign': 'NN', 'channel': None, 'frequency': 331_000, 'runway': '28'}, 'NDB 331 NN (inner)'),
])
def test_beacon_chip_table(beacon, chip):
    assert site.beacon_chip(beacon) == chip


@pytest.mark.parametrize('beacon', [
    {'type': 'ILS_GLIDESLOPE', 'callsign': 'IAK', 'frequency': 109_700_000, 'runway': '28'},
    {'type': 'PRMG_GLIDESLOPE', 'callsign': 'PRM', 'frequency': 108_000_000, 'runway': '09'},
    {'type': 'SOMETHING_ELSE', 'callsign': 'ZZ', 'frequency': 1},
])
def test_beacon_without_chip(beacon):
    assert site.beacon_chip(beacon) is None


def test_beacon_null_values_are_omitted():
    assert site.beacon_chip({'type': 'ILS_LOCALIZER', 'callsign': 'ICKR', 'channel': None, 'frequency': 333_950_000, 'runway': None}) \
        == 'ILS 333.95 ICKR'
    assert site.beacon_chip({'type': 'ILS_LOCALIZER', 'callsign': None, 'channel': None, 'frequency': 109_100_000, 'runway': '04'}) \
        == 'ILS 04 109.10'
    assert site.beacon_chip({'type': 'TACAN', 'callsign': 'AKR', 'channel': None, 'frequency': None, 'runway': None}) == 'TACAN AKR'


def test_beacon_chips_order_and_distinct():
    beacons = [{'type': 'ILS_LOCALIZER', 'callsign': 'IAK', 'frequency': 109_700_000, 'runway': '28'},
               {'type': 'ILS_GLIDESLOPE', 'callsign': 'IAK', 'frequency': 109_700_000, 'runway': '28'},
               {'type': 'TACAN', 'callsign': 'AKR', 'channel': 107},
               {'type': 'AIRPORT_HOMER', 'callsign': 'AK', 'frequency': 365_000},
               {'type': 'HOMER', 'callsign': 'AK', 'frequency': 365_000}]
    assert site.beacon_chips(beacons) == ['ILS 28 109.70 IAK', 'TACAN 107X AKR', 'NDB 365 AK']
    assert site.beacon_chips([]) == [] and site.beacon_chips(None) == []


# ---------- PDF ----------
def test_pdf_name_choice(tmp_path):
    (tmp_path / 'LCRA_Akrotiri_charts.pdf').write_bytes(b'x')
    (tmp_path / 'Ramstein_charts.pdf').write_bytes(b'x')
    (tmp_path / 'Both_charts.pdf').write_bytes(b'x')
    (tmp_path / 'EDXX_Both_charts.pdf').write_bytes(b'x')
    mk = lambda stem, name: site.Base(stem, name, '', '', 'af', [])
    assert site.pdf_name(mk('LCRA_Akrotiri', 'Akrotiri'), tmp_path) == 'LCRA_Akrotiri_charts.pdf'      # le stem
    assert site.pdf_name(mk('ETAR_Ramstein', 'Ramstein'), tmp_path) == 'Ramstein_charts.pdf'           # le nom avec des _
    assert site.pdf_name(mk('EDXX_Both', 'Both'), tmp_path) == 'EDXX_Both_charts.pdf'                  # le stem d'abord
    with pytest.raises(site.SiteError):
        site.pdf_name(mk('XXXX_Missing', 'Missing'), tmp_path)


# ---------- slugs ----------
def test_slugify():
    assert site.slugify('Akrotiri') == 'akrotiri'
    assert site.slugify('Abu al-Duhur') == 'abu-al-duhur'
    assert site.slugify('H_med_orig_01') == 'h-med-orig-01'
    assert site.slugify('Düsseldorf') == 'dusseldorf'
    assert site.slugify('  Tha\'lah (North) ') == 'tha-lah-north'


def test_slug_collision_stops():
    site.check_slugs(['HI16', 'HI17', 'H_med_01'], 'x')
    with pytest.raises(site.SiteError):
        site.check_slugs(['H_med_01', 'H med 01'], 'x')
    with pytest.raises(site.SiteError):
        site.check_slugs(['???'], 'x')                                  # aucun slug


# ---------- recherche ----------
ENTRIES = [
    {'n': 'Akrotiri', 'i': 'LCRA', 'a': '', 't': 'af'},
    {'n': 'Adana', 'i': 'LTAF', 'a': 'Adana Sakirpasa', 't': 'af'},
    {'n': 'Kharab Ishk', 'i': '', 'a': '', 't': 'heli'},
    {'n': 'HI11', 'i': '', 'a': '', 't': 'hp'},
    {'n': 'HI12', 'i': '', 'a': '', 't': 'hp'},
    {'n': 'HI01', 'i': '', 'a': '', 't': 'hp'},
    {'n': 'Hi1 Base', 'i': 'HIBA', 'a': '', 't': 'af'},
    {'n': 'Latakia', 'i': 'OSLK', 'a': 'Bassel Al-Assad', 't': 'af'},
]


def names(q):
    return [e['n'] for e in site.rank(ENTRIES, q)]


def test_rank_name_start_first():
    assert names('akro')[0] == 'Akrotiri'


def test_rank_icao_exact_before_name_start():
    assert names('lcra') == ['Akrotiri']
    es = ENTRIES + [{'n': 'Lcra Town', 'i': '', 'a': '', 't': 'af'}]
    assert [e['n'] for e in site.rank(es, 'lcra')] == ['Akrotiri', 'Lcra Town']


def test_rank_heliport_after_airfield_at_equal_rank():
    assert names('hi1') == ['Hi1 Base', 'HI11', 'HI12']


def test_rank_word_start_then_icao_start_then_content():
    assert names('ishk') == ['Kharab Ishk']                             # debut d'un mot
    assert names('lta') == ['Adana']                                    # debut de l'OACI (rang 2)
    assert names('taki') == ['Latakia']                                 # contenu du nom (rang 3)
    es = [{'n': 'Zulu Lta', 'i': '', 'a': '', 't': 'af'}, {'n': 'Alta', 'i': 'XLTA', 'a': '', 't': 'af'}, {'n': 'Lta Field', 'i': '', 'a': '', 't': 'af'}]
    assert [e['n'] for e in site.rank(es, 'lta')] == ['Lta Field', 'Zulu Lta', 'Alta']   # debut du nom, debut d'un mot, contenu


def test_rank_alias_counts_only_for_content():
    assert names('sakirpasa') == ['Adana']                              # contenu : l'alias compte
    assert names('adana s') == ['Adana']
    assert names('bassel') == ['Latakia']
    assert [e['n'] for e in site.rank([{'n': 'Zeta', 'i': '', 'a': 'Akrotiri Old', 't': 'af'}], 'akro')] == ['Zeta']   # rang 3, mais trouve
    # l'alias ne donne ni rang 1 ni rang 2 : « Akrotiri Old » en alias reste derriere un vrai debut de nom
    es = [{'n': 'Zeta', 'i': '', 'a': 'Akrotiri Old', 't': 'af'}, {'n': 'Akrotiri', 'i': '', 'a': '', 't': 'af'}]
    assert [e['n'] for e in site.rank(es, 'akro')] == ['Akrotiri', 'Zeta']


def test_rank_limit_and_empty_query():
    es = [{'n': f'Base {i:02d}', 'i': '', 'a': '', 't': 'af'} for i in range(30)]
    assert len(site.rank(es, 'base')) == 12
    assert site.rank(es, '  ') == [] and site.rank(es, '') == []


# ---------- un petit site fabrique ----------
def png(w=40, h=56, color=(250, 250, 250)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Zips minuscules + un `load_theatre` de remplacement : le site complet (pages, liens, taille) sans pydcs."""
    zips = tmp_path / 'zips'
    zips.mkdir()
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    (downloads / site.GUIDE_PDF).write_bytes(b'%PDF' + b'0' * 1_200_000)
    datas = {}
    for t in site.THEATRES:
        zp = zips / site.zip_name(t, 'airfields')
        with zipfile.ZipFile(zp, 'w') as z:
            for stem in ('ABCD_First', 'Second'):
                for code in ('10-8', '10-9', '10-9A'):
                    z.writestr(f'x/png/{stem}_{code}.png', png())
            z.writestr('x/png/' + f'{t.tile_stem}_{t.tile_page}.png', png())
            z.writestr('heliports/dir.jpg', png())
        for kind in ('helicopters', 'lotatc'):
            (zips / site.zip_name(t, kind)).write_bytes(b'PK' + b'0' * 10)
        refs = lambda stem, codes: [(c, (zp, f'x/png/{stem}_{c}.png')) for c in codes]
        tile = site.Base(t.tile_stem, 'Tile Base', 'TILE', '', 'af', refs(t.tile_stem, [t.tile_page]))
        tile.slug, tile.pdf, tile.chips = 'tile-base', 'tile.pdf', []
        a = site.Base('ABCD_First', 'First', 'ABCD', 'Old First', 'af', refs('ABCD_First', ['10-8', '10-9', '10-9A']))
        a.slug, a.pdf, a.chips, a.lotatc = 'first', 'ABCD_First_charts.pdf', ['ILS 28 109.70 IAK'], [('drawing', 'ABCD_First.json')]
        b = site.Base('Second', 'Second', '', '', 'heli', refs('Second', ['10-8', '10-9']))
        b.slug, b.pdf = 'second', 'Second_charts.pdf'
        h1 = site.Heliport('HX01', 'hx01', 1, "N32°56.7' E035°12.3'", '1.2 km SW of Yarka', 4, (zp, 'heliports/dir.jpg'))
        h2 = site.Heliport('HX02', 'hx02', 3, "N32°37.4' E035°22.7'", '7.7 km N of Ein Harod', 4, (zp, 'heliports/dir.jpg'))
        h1.mates, h2.mates = [h2], [h1]
        datas[t.pydcs] = site.TheatreData(t, [a, b, tile], [h1, h2], {4: h1.ref}, {'ABCD_First.json': b'{"a":1}'},
                                          {'airfields': 340_400_000, 'helicopters': 106_000_000, 'lotatc': 12})
        folder = downloads / t.folder
        folder.mkdir()
    monkeypatch.setattr(site, 'load_theatre', lambda t, z, d, log=print: datas[t.pydcs])
    return tmp_path, zips, downloads


def build(fake, name='site', **kw):
    tmp, zips, downloads = fake
    return site.build_site(zips, 'AC-V1.11.0', 'MirabelleBenou/dcs-airfield-charts', tmp / name, downloads=downloads, workers=1, **kw)


def test_fake_site_builds_with_every_page(fake):
    lines = build(fake)
    out = fake[0] / 'site'
    for rel in ('index.html', '404.html', 'search.json', 'guide/index.html', 'syria/index.html', 'syria/first/index.html',
                'syria/heliports/hx01/index.html', 'germanycoldwar/index.html', 'assets/site.css', 'assets/site.js',
                'assets/fonts/oswald-600.woff2', 'assets/fonts/BarlowCondensed-Medium.ttf', 'assets/insigne-airfield-charts.svg',
                'lotatc/syria/ABCD_First.json', 'img/syria/ABCD_First_10-9.webp', 'img/syria/ABCD_First_10-9_t.webp',
                'img/syria/directory_p04.webp', 'img/syria/directory_p04_t.webp'):
        assert (out / rel).is_file(), rel
    assert (out / 'lotatc/syria/ABCD_First.json').read_bytes() == b'{"a":1}'
    assert lines[-1].startswith('site : ') and 'Syria (Syria) : 3 charts (2 airfields, 1 helicopter base), 6 pages, 2 heliports, 1 directory page shown, 1 LotAtc file' in lines[0]
    assert Image.open(out / 'img/syria/ABCD_First_10-9.webp').size == (1400, 1960)
    assert Image.open(out / 'img/syria/ABCD_First_10-9_t.webp').size == (240, 336)
    assert not [p for p in fake[0].iterdir() if p.name.startswith('.site-')]


def test_fake_site_pages_content(fake):
    build(fake)
    out = fake[0] / 'site'
    first = (out / 'syria/first/index.html').read_text(encoding='utf-8')
    assert '<title>First (ABCD) — DCS Airfield Charts</title>' in first
    assert first.count('class="pg-card"') == 3
    assert 'href="../../lotatc/syria/ABCD_First.json" download' in first and '_parking.json' not in first
    assert 'https://github.com/MirabelleBenou/dcs-airfield-charts/releases/download/AC-V1.11.0/DCS_Airfield_Charts_Syria_1985_airfields.zip' in first
    assert 'https://raw.githubusercontent.com/MirabelleBenou/dcs-airfield-charts/AC-V1.11.0/downloads/syria/ABCD_First_charts.pdf' in first
    assert 'aria-current="page">Syria</a>' in first and 'aria-current="page">Cold War Germany' not in first
    second = (out / 'syria/second/index.html').read_text(encoding='utf-8')
    assert 'no radio navaid' in second and 'helicopters zip' in second and 'LotAtc' not in second
    hp = (out / 'syria/heliports/hx01/index.html').read_text(encoding='utf-8')
    assert 'also on this page: <a href="../hx02/">HX02</a>' in hp and '1 pad<' in hp
    home = (out / 'index.html').read_text(encoding='utf-8')
    assert '2 theatres · 6 airfields and helicopter bases · 4 heliports' in home
    assert '1 helicopter base' in home and '2 airfields' in home
    assert 'A 10-9 chart of Cold War Germany' in home and 'A4 · 1 MB' in home
    idx = (out / 'syria/index.html').read_text(encoding='utf-8')
    assert idx.count('<tr data-t=') == 3 + 2 and 'Airfields zip <small>PDF · PNG · SVG · 340 MB</small>' in idx
    entries = json.loads((out / 'search.json').read_text(encoding='utf-8'))
    assert entries[0] == {'n': 'First', 'i': 'ABCD', 'a': 'Old First', 'th': 'Syria', 't': 'af', 'u': 'syria/first/',
                          'm': 'Airfield · 3 pages · Syria'}
    assert any(e['m'] == 'Heliport · 1 pad · 1.2 km SW of Yarka · Syria' for e in entries)
    nf = (out / '404.html').read_text(encoding='utf-8')
    assert '<base href="/dcs-airfield-charts/">' in nf and 'Page not found' in nf


def test_fake_site_is_deterministic(fake):
    build(fake, 'one')
    build(fake, 'two')
    assert site.listing(fake[0] / 'one') == site.listing(fake[0] / 'two')


def test_output_must_not_exist(fake):
    build(fake)
    with pytest.raises(site.SiteError):
        build(fake)


def test_size_threshold_stops_and_leaves_nothing(fake, monkeypatch):
    monkeypatch.setattr(site, 'MAX_SITE_BYTES', 10_000)
    with pytest.raises(site.SiteError, match='MiB'):
        build(fake)
    assert not (fake[0] / 'site').exists()
    assert not [p for p in fake[0].iterdir() if p.name.startswith('.site-')]


def test_broken_link_stops_and_leaves_nothing(fake, monkeypatch):
    real = site.base_html
    monkeypatch.setattr(site, 'base_html', lambda ctx, d, b: real(ctx, d, b) + '<a href="../nowhere/">x</a>')
    with pytest.raises(site.SiteError, match='nowhere'):
        build(fake)
    assert not (fake[0] / 'site').exists()
    assert not [p for p in fake[0].iterdir() if p.name.startswith('.site-')]


def test_check_links_accepts_directories_anchors_and_absolute(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'site.css').write_text("a{background:url(data:image/svg+xml,x)} @font-face{src:url(fonts/f.ttf)}")
    (tmp_path / 'assets' / 'fonts').mkdir()
    (tmp_path / 'assets' / 'fonts' / 'f.ttf').write_bytes(b'x')
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'index.html').write_text('<a href="../">up</a><a href="#top">t</a><a href="https://example.org/x">e</a>'
                                               '<a href="../b/?f=hp#x">b</a><img src="../assets/fonts/f.ttf">')
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b' / 'index.html').write_text('<a href="../a/">a</a>')
    (tmp_path / 'index.html').write_text('<a href="a/">a</a><button data-l="a/index.html"></button>')
    site.check_links(tmp_path)
    (tmp_path / 'b' / 'index.html').write_text('<a href="../a/missing.html">a</a>')
    with pytest.raises(site.SiteError, match='missing.html'):
        site.check_links(tmp_path)


def test_missing_zip_stops_before_writing(fake):
    tmp, zips, downloads = fake
    (zips / site.zip_name(site.THEATRES[1], 'lotatc')).unlink()
    with pytest.raises(site.SiteError, match='lotatc'):
        build(fake)
    assert not (tmp / 'site').exists()


def test_bad_edition_and_repo(fake):
    tmp, zips, downloads = fake
    with pytest.raises(site.SiteError):
        site.build_site(zips, 'latest', 'a/b', tmp / 'x', downloads=downloads)
    with pytest.raises(site.SiteError):
        site.build_site(zips, 'AC-V1.0.0', 'ab', tmp / 'x', downloads=downloads)


# ---------- le script dans un vrai navigateur (ignore sans Playwright ni Chromium) ----------
def test_search_filter_and_viewer_in_chromium(fake):
    sync_api = pytest.importorskip('playwright.sync_api')
    build(fake)
    out = fake[0] / 'site'
    handler = partial(SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:                                         # navigateur absent
                pytest.skip(f'Chromium indisponible : {e}')
            page = browser.new_page()
            errors = []
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base)
            page.fill('.search input', 'fir')
            page.wait_for_selector('.results a')
            assert page.locator('.results a').first.inner_text().startswith('First')
            page.goto(base + 'syria/?f=hp')
            assert page.locator('#list tbody tr').count() == 2
            page.click('.seg button[data-f="af"]')
            assert page.locator('#list tbody tr').count() == 2             # First et Tile Base
            page.fill('.filter', 'zzz')
            assert page.locator('#list .empty').count() == 1
            page.goto(base + 'syria/first/')
            page.click('.pg-card[data-k="1"]')
            assert page.locator('.lb-top .cd').inner_text() == '10-9'
            assert '2 / 3' in page.locator('.lb-top .lb').inner_text()
            page.keyboard.press('ArrowRight')
            assert page.locator('.lb-top .cd').inner_text() == '10-9A'
            page.keyboard.press('Escape')
            assert page.locator('.lb-wrap').count() == 0
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
