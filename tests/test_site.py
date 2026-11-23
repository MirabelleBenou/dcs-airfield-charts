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

REAL_COMPOSE_GUIDE = site.compose_guide


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
    data = guide_data()                                           # un guide fabrique, sur les sources et les images du faux site
    data['sources'] = {'k109': {'file': 'svg/ABCD_First_10-9.svg'}, 'k111': {'file': 'svg/ABCD_First_10-8.svg'},
                       'k111h': {'file': 'svg/Second_10-9.svg'}, 'naq': {'file': 'svg/Second_10-8.svg'},
                       'dir': {'file': 'heliports/Syria_heliports_directory_p04.jpg'}}
    monkeypatch.setattr(site, 'compose_guide', lambda zips, edition: (data, guide_composed(data)))
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


# ---------- le guide de lecture : une page par chapitre (donnees fabriquees par le test) ----------
import re

from render import guide


def gfig(x=10.0, y=20.0, w=30.0, h=40.0, mark='f'):
    return guide.Figure(els=[f'<rect id="{mark}-r" class="num" x="{x}" y="{y}" width="{w}" height="{h}"/>', f'<text>{mark}</text>'],
                        rects=[(x, y, x + w, y + h)])


def guide_data():
    pin = lambda n, title, text='': {'n': n, 'title': title, 'text': text}
    return {
        'labels': {'parts': {'1': 'PART 1 · READING THE CHARTS', '2': 'PART 2 · FLYING WITH THE CHARTS', 'annex': 'APPENDICES',
                             'contents': 'X'}},
        'sources': {'k109': {'zip': 'airfields', 'file': 'svg/LTAN_Konya_10-9.svg'},
                    'k111': {'zip': 'airfields', 'file': 'svg/LTAN_Konya_11-1.svg'},
                    'k111h': {'zip': 'helicopters', 'file': 'svg/LTAN_Konya_11-1H.svg'},
                    'naq': {'zip': 'helicopters', 'file': 'svg/Naqoura_10-9.svg'},
                    'dir': {'zip': 'helicopters', 'file': 'heliports/Syria_heliports_directory_p11.jpg', 'size': [1748, 2480]}},
        'pages': [
            {'id': 'cover', 'kind': 'cover', 'title': 'Cover'},
            {'id': 'contents', 'kind': 'contents', 'title': 'Contents'},
            {'id': 'ch1', 'kind': 'text', 'part': 1, 'num': '1', 'title': 'About these charts',
             'paragraphs': ['**Lead.** Body with `code` & <b>tags</b>.', 'Second.']},
            {'id': 'ch2', 'kind': 'strip', 'part': 1, 'num': '2', 'title': 'The pages of an airfield',
             'thumbs': [{'source': 'k109', 'code': '10-9', 'name': 'Airfield', 'line': 'The <runway>.'},
                        {'source': 'k111', 'code': '11-1', 'name': 'Approach', 'line': 'The approach.'}],
             'text': 'After the **strip**.'},
            {'id': 'ch4', 'kind': 'anatomy', 'part': 1, 'num': '4', 'title': 'Airfield — 10-9 (Konya)', 'source': 'k109',
             'zoom': {'caption': 'THE HEADER, ENLARGED'},
             'pins': [pin(1, 'Code', 'The **ICAO** code.'), pin(2, 'Title'), pin(3, 'Zoomed <one>', 'In the zoom.')],
             'use': ['First `a`.', 'Second.'], 'note': 'A note.'},
            {'id': 'ch6a', 'kind': 'anatomy', 'part': 1, 'num': '6', 'title': 'A helicopter base — 10-9 (Naqoura)', 'source': 'naq',
             'pins': [pin(1, 'Pad', 'A pad.')], 'use': ['One.']},
            {'id': 'ch6b', 'kind': 'anatomy', 'part': 1, 'num': '6', 'title': 'The heliport directory (Syria, page 11)', 'source': 'dir',
             'pins': [pin(1, 'Box', 'The box.')], 'use': ['One.']},
            {'id': 'ch9', 'kind': 'zoom_only', 'part': 1, 'num': '9', 'title': 'Minimum sector altitude', 'source': 'k111',
             'caption': 'THE MSA BOX', 'pins': [pin(1, 'Sector', 'A sector.')], 'text': 'Each value is **1000 ft** above.'},
            {'id': 'ch10', 'kind': 'flight', 'part': 2, 'num': '10', 'title': 'A flight with the charts',
             'steps': [{'thumbs': ['k109'], 'text': '**Open** the 10-9.'}, {'thumbs': ['k109', 'k111'], 'text': 'Then the 11-1.'}]},
            {'id': 'ch11-0', 'kind': 'overview', 'part': 2, 'num': '', 'title': '11.0 Overview', 'caption': 'Which approach',
             'header': ['Aircraft', 'NDB', 'ILS'], 'rows': [['Mi-24P', 'yes', '—'], ['F-5E', '—', 'yes']],
             'note': 'Read the **equipment** line.'},
            {'id': 'ch11-1', 'kind': 'fiche', 'part': 2, 'num': '', 'title': '11.1 Mi-24P', 'items': ['First **item**.', 'Second item.'],
             'example': 'k111h', 'example_caption': 'Konya 11-1H, reduced'},
            {'id': 'ch11-5', 'kind': 'fiche', 'part': 2, 'num': '', 'title': '11.5 MiG-21bis', 'items': ['Only item.']},
            {'id': 'annex', 'kind': 'annex', 'part': 'annex', 'num': '', 'title': 'Appendices',
             'sections': [{'heading': 'Abbreviations', 'paragraphs': [[['ADF', 'direction finder'], ['ARP', 'reference <point>']]]},
                          {'heading': 'Notes', 'paragraphs': ['A **note** paragraph.']}]},
        ]}


def guide_composed(data=None):
    data = data or guide_data()
    figures = []
    for s in data['pages']:
        if s['kind'] in ('cover', 'contents'):
            continue
        figs = {}
        if s['kind'] == 'anatomy':
            figs['page'] = gfig(mark=s['id'] + 'p')
            if s.get('zoom'):
                figs['zoom'] = gfig(5.0, 6.0, 70.0, 20.0, mark=s['id'] + 'z')
        elif s['kind'] == 'zoom_only':
            figs['zoom'] = gfig(mark=s['id'] + 'z')
        figures.append((s['id'], figs))
    return type('Composed', (), {'figures': figures})()


GUIDE_IMAGES = {f'img/syria/{n}': ((1, 1), (1400, 1960), (240, 336)) for n in
                ('LTAN_Konya_10-9', 'LTAN_Konya_11-1', 'LTAN_Konya_11-1H', 'Naqoura_10-9', 'directory_p11')}


def gctx():
    return site.Context('AC-V1.11.0', 'MirabelleBenou/dcs-airfield-charts', 6, [])


@pytest.fixture(scope='module')
def gpages():
    pages, line = site.guide_pages(gctx(), guide_data(), guide_composed(), GUIDE_IMAGES, css='a{}')
    return {p.id: p for p in pages}, line


def test_guide_has_one_page_per_entry_except_cover_and_contents(gpages):
    pages, line = gpages
    assert list(pages) == ['ch1', 'ch2', 'ch4', 'ch6a', 'ch6b', 'ch9', 'ch10', 'ch11-0', 'ch11-1', 'ch11-5', 'annex']
    assert pages['ch1'].path == 'guide/index.html' and pages['ch1'].url == 'guide/'
    assert pages['ch4'].path == 'guide/ch4/index.html' and pages['ch4'].url == 'guide/ch4/'
    assert pages['ch11-0'].path == 'guide/ch11-0/index.html'
    assert pages['ch4'].title == '4. Airfield — 10-9 (Konya)' and pages['ch11-1'].title == '11.1 Mi-24P'
    assert pages['annex'].title == 'Appendices'
    assert line == 'guide : 11 pages, 5 figures SVG, 6 pastilles, 6 vignettes'


def test_guide_page_frame_title_breadcrumb_and_part_line(gpages):
    h = gpages[0]['ch4'].html
    assert '<title>Reading guide — DCS Airfield Charts</title>' in h and 'data-root="../../"' in h
    assert '<a href="../../syria/" data-nav="syria">' in h and '<a href="../../guide/" data-nav="guide" aria-current="page">Reading guide</a>' in h
    assert '<div class="crumb"><a href="../../">Home</a> › <a href="../">Reading guide</a> › 4. Airfield — 10-9 (Konya)</div>' in h
    assert '<p class="gpart">PART 1 · READING THE CHARTS</p><h1 class="pt">4. Airfield — 10-9 (Konya)</h1>' in h
    first = gpages[0]['ch1'].html
    assert 'data-root="../"' in first and '<div class="crumb"><a href="../">Home</a> › <a href="./">Reading guide</a> › 1. About these charts</div>' in first
    assert '<p class="gpart">APPENDICES</p>' in gpages[0]['annex'].html and '<p class="gpart">PART 2 · FLYING WITH THE CHARTS</p>' in gpages[0]['ch10'].html


def test_guide_toc_parts_entries_ch6b_and_pdf_button(gpages):
    h = gpages[0]['ch4'].html
    toc = re.findall(r'<nav class="toc"[^>]*>(.*?)</nav>', h, re.S)
    assert len(toc) == 2 and toc[0] == toc[1]                                  # le meme sommaire, replie et deplie
    t = toc[0]
    assert t.count('<h4>') == 3 and '<h4>READING THE CHARTS</h4>' in t and '<h4>FLYING WITH THE CHARTS</h4>' in t and '<h4>APPENDICES</h4>' in t
    assert t.count('<a href=') == 11 - 1 and t.count('<a class="btn pdf"') == 1   # dix pages (ch6b exclue), puis le bouton PDF
    assert '<a href="../ch6a/"><span>6</span>A helicopter base — 10-9 (Naqoura)</a>' in t and 'ch6b' not in t
    assert '<a href="./" aria-current="page"><span>4</span>Airfield — 10-9 (Konya)</a>' in t
    assert '<a href="../ch11-0/"><span>·</span>Overview</a>' in t and '<span>·</span>Mi-24P</a>' in t     # sans le numero de fiche
    assert '<a href="../"><span>1</span>About these charts</a>' in t
    assert t.endswith('<a class="btn pdf" href="https://raw.githubusercontent.com/MirabelleBenou/dcs-airfield-charts/AC-V1.11.0/'
                      'downloads/DCS_Airfield_Charts_Reading_Guide.pdf">PDF <small>A4 · 6 MB</small></a>')
    on_b = gpages[0]['ch6b'].html
    assert on_b.count('aria-current="page"><span>6</span>A helicopter base') == 2           # ch6a reste marquee sur ch6b
    assert re.findall(r'<a href="[^"]*"[^>]*aria-current="page"><span>', on_b).__len__() == 2
    assert 'aria-current="page"><span>6</span>The heliport' not in on_b


def test_guide_previous_and_next(gpages):
    pages = gpages[0]
    first, last = pages['ch1'].html, pages['annex'].html
    assert '<div class="pn"><span></span><a class="btn" href="ch2/">2. The pages of an airfield ›</a></div>' in first
    assert '<div class="pn"><a class="btn" href="../ch11-5/">‹ 11.5 MiG-21bis</a></div>' in last
    mid = pages['ch6a'].html
    assert '<a class="btn" href="../ch4/">‹ 4. Airfield — 10-9 (Konya)</a><a class="btn" href="../ch6b/">6. The heliport directory (Syria, page 11) ›</a>' in mid
    assert (pages['ch1'].prev, pages['ch1'].next) == ('', 'ch2/')
    assert (pages['ch2'].prev, pages['ch2'].next) == ('../', '../ch4/')
    assert (pages['annex'].prev, pages['annex'].next) == ('../ch11-5/', '')


def test_guide_anatomy_body_figure_pins_zoom_and_use(gpages):
    h = gpages[0]['ch4'].html
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="9.400 19.400 31.200 41.200" '
           'role="img" aria-label="Airfield — 10-9 (Konya)">\n<rect id="ch4p-r" class="num" x="10.0" y="20.0" width="30.0" height="40.0"/>\n'
           '<text>ch4p</text>\n</svg>')
    assert f'<div class="anat under"><figure>{svg}</figure><ol class="pins">' in h
    assert ('<li><span class="bd">1</span><b>Code</b><span class="tx">The <b>ICAO</b> code.</span></li>'
            '<li><span class="bd">2</span><b>Title</b></li>'
            '<li><span class="bd">3</span><b>Zoomed &lt;one&gt;</b><span class="tx">In the zoom.</span></li></ol></div>') in h
    assert h.count('<li><span class="bd">') == 3                              # toutes les pastilles, celles du zoom aussi
    assert ('<figure class="zoomf"><figcaption>THE HEADER, ENLARGED</figcaption><svg xmlns=' in h
            and 'viewBox="4.400 5.400 71.200 21.200"' in h and 'ch4z' in h)
    assert '<p class="note">A note.</p>' in h
    assert '<h3 class="use">How to use it</h3><ol class="use"><li>First <code>a</code>.</li><li>Second.</li></ol>' in h
    assert h.index('class="anat under"') < h.index('class="zoomf"') < h.index('class="note"') < h.index('How to use it') < h.index('class="pn"')


def test_guide_text_strip_flight_overview_fiche_annex_bodies(gpages):
    pages = gpages[0]
    t = pages['ch1'].html
    assert '<p><b>Lead.</b> Body with <code>code</code> &amp; &lt;b&gt;tags&lt;/b&gt;.</p>\n  <p>Second.</p>' in t
    s = pages['ch2'].html
    assert ('<div class="strip"><figure><img class="thumb" src="../../img/syria/LTAN_Konya_10-9_t.webp" alt="10-9" loading="lazy">'
            '<figcaption><b>10-9</b><span class="nm">Airfield</span>The &lt;runway&gt;.</figcaption></figure>') in s
    assert s.count('<figure>') == 2 and s.index('</div>\n  <p>After the <b>strip</b>.</p>') > s.index('class="strip"')
    f = pages['ch10'].html
    assert f.count('<li><div class="imgs">') == 2 and f.count('class="sn"') == 2
    assert ('<li><div class="imgs"><img class="thumb" src="../../img/syria/LTAN_Konya_10-9_t.webp" alt="10-9" loading="lazy"></div>'
            '<div><span class="sn">1</span><p><b>Open</b> the 10-9.</p></div></li>') in f
    assert f.count('<img class="thumb"') == 3 and '<ol class="steps">' in f
    o = pages['ch11-0'].html
    assert o.count('<tr>') == 3 and '<thead><tr><th>Aircraft</th><th>NDB</th><th>ILS</th></tr></thead>' in o
    assert ('<tbody><tr><th scope="row">Mi-24P</th><td>yes</td><td class="no">—</td></tr>'
            '<tr><th scope="row">F-5E</th><td class="no">—</td><td>yes</td></tr></tbody>') in o
    assert '<p class="capt">Which approach</p><div class="tbl"><table class="ov">' in o and o.count('class="note"') == 1
    assert '<p class="note">Read the <b>equipment</b> line.</p>' in o
    e = pages['ch11-1'].html
    assert ('<div class="anat"><figure class="exf"><img class="thumb" src="../../img/syria/LTAN_Konya_11-1H.webp" '
            'alt="Konya 11-1H, reduced" loading="lazy"><figcaption class="capt">Konya 11-1H, reduced</figcaption></figure>'
            '<div><p>First <b>item</b>.</p><p>Second item.</p></div></div>') in e
    n = pages['ch11-5'].html
    assert 'class="exf"' not in n and '<p>Only item.</p>' in n and '<div class="anat">' not in n
    a = pages['annex'].html
    assert ('<h3 class="use">Abbreviations</h3>\n  <dl class="abbr"><dt>ADF</dt><dd>direction finder</dd><dt>ARP</dt>'
            '<dd>reference &lt;point&gt;</dd></dl>') in a
    assert '<h3 class="use">Notes</h3>\n  <p>A <b>note</b> paragraph.</p>' in a


def test_guide_zoom_only_and_directory_pages(gpages):
    z = gpages[0]['ch9'].html
    assert re.search(r'<div class="anat under"><figure><figcaption class="capt">THE MSA BOX</figcaption><svg ', z)
    assert '<p>Each value is <b>1000 ft</b> above.</p>' in z and z.count('<svg ') == 1 and 'zoomf' not in z.split('</style>')[-1]
    assert gpages[0]['ch6b'].html.count('<svg ') == 1 and gpages[0]['ch6a'].html.count('<li><span class="bd">') == 1


def test_guide_image_name_comes_from_the_source_of_guide_json():
    defs = guide_data()['sources']
    t = site.THEATRES[0]
    assert site.source_image(defs, 'k111h', t) == ('LTAN_Konya_11-1H', '11-1H')
    assert site.source_image(defs, 'naq', t) == ('Naqoura_10-9', '10-9')
    assert site.source_image(defs, 'dir', t) == ('directory_p11', 'directory')
    assert site.source_image(defs, 'dir', t)[0] == site.dir_image_name(11)
    with pytest.raises(site.SiteError, match='nowhere'):
        site.source_image(defs, 'nowhere', t)
    with pytest.raises(site.SiteError, match='ni la forme'):
        site.source_image({'x': {'file': 'png/foo.png'}}, 'x', t)


def test_guide_image_missing_from_the_site_stops():
    images = {k: v for k, v in GUIDE_IMAGES.items() if not k.endswith('LTAN_Konya_11-1')}
    with pytest.raises(site.SiteError, match='LTAN_Konya_11-1.webp'):
        site.guide_pages(gctx(), guide_data(), guide_composed(), images, css='a{}')


def test_guide_figures_must_match_the_pages_of_the_data_file():
    comp = guide_composed()
    comp.figures = comp.figures[:-1]
    with pytest.raises(site.SiteError, match='figures'):
        site.guide_pages(gctx(), guide_data(), comp, GUIDE_IMAGES, css='a{}')


def test_figure_svg_viewbox_has_three_decimals_and_keeps_the_order():
    f = guide.Figure(els=['<a/>', '<b/>', '<c/>'], rects=[(1.0, 2.0, 3.5, 4.25)])
    out = site.figure_svg(f, 'T <1>')
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                          'viewBox="0.400 1.400 3.700 3.450" role="img" aria-label="T &lt;1&gt;">')
    assert out.index('<a/>') < out.index('<b/>') < out.index('<c/>') and out.endswith('</svg>')


def test_md_escapes_then_marks_up():
    assert site.md('a **b** `c` <i> & "q"') == 'a <b>b</b> <code>c</code> &lt;i&gt; &amp; &quot;q&quot;'
    assert site.md('**x `y`**') == '<b>x <code>y</code></b>'


def test_class_collision_between_a_figure_and_the_site_css_stops():
    css = '.a .halo,.b{x:y} @media (max-width:9px){.num{x:y}} @font-face{src:url(fonts/f.ttf)} /* .gone */ h1.pt{}'
    assert site.css_classes(css) == {'halo', 'b', 'a', 'num', 'pt'}
    assert site.svg_classes('<text class="halo num">1</text><g class="x"/>') == {'halo', 'num', 'x'}
    site.check_classes(['<text class="x">1</text>'], css)
    with pytest.raises(site.SiteError, match=r'\.halo, \.num'):
        site.check_classes(['<text class="halo">1</text><text class="num">2</text>'], css)
    assert not {'halo', 'num'} & site.css_classes((site.DATA / 'site.css').read_text(encoding='utf-8'))
    comp = guide_composed()
    with pytest.raises(site.SiteError, match=r'\.num'):                         # gfig() porte class="num"
        site.guide_pages(gctx(), guide_data(), comp, GUIDE_IMAGES, css='.num{color:red}')


def test_duplicate_id_in_a_page_stops_but_the_same_id_in_two_pages_is_fine(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'index.html').write_text('<svg id="g1-x"></svg><p id="p"></p><svg><g id="g2-x"/></svg>')
    (tmp_path / 'b.html').write_text('<svg id="g1-x"></svg>')
    site.check_ids(tmp_path)
    (tmp_path / 'a' / 'index.html').write_text('<svg id="g1-x"></svg><p><svg id="g1-x"></svg></p>')
    with pytest.raises(site.SiteError, match=r'a/index.html : id="g1-x"'):
        site.check_ids(tmp_path)


def test_site_css_declares_the_barlow_faces_of_the_svg_and_the_site_ships_them():
    css = (site.DATA / 'site.css').read_text(encoding='utf-8')
    for w, f in ((400, 'Regular'), (500, 'Medium'), (600, 'SemiBold'), (700, 'Bold')):
        assert f"@font-face{{font-family:'Barlow Condensed';font-weight:{w};src:url(fonts/BarlowCondensed-{f}.ttf)" in css
    assert "@font-face{font-family:'Barlow Condensed SemiBold';font-weight:100 900;src:url(fonts/BarlowCondensed-SemiBold.ttf)" in css
    assert '.anat figure svg,.zoomf svg{display:block;width:100%;height:auto}' in css
    assert 'BarlowCondensed-Bold.ttf' in site.BARLOW and (site.FONTS / 'BarlowCondensed-Bold.ttf').is_file()


def test_fake_site_writes_the_guide_pages_and_prints_their_line(fake):
    lines = build(fake)
    out = fake[0] / 'site'
    assert (out / 'guide' / 'index.html').is_file() and (out / 'guide' / 'ch4' / 'index.html').is_file()
    assert (out / 'guide' / 'ch11-0' / 'index.html').is_file() and (out / 'guide' / 'annex' / 'index.html').is_file()
    assert lines[-2] == 'guide : 11 pages, 5 figures SVG, 6 pastilles, 6 vignettes' and lines[-1].startswith('site : ')
    assert (out / 'assets' / 'fonts' / 'BarlowCondensed-Bold.ttf').is_file()
    assert not (out / 'guide' / 'index.html').read_text(encoding='utf-8').count('Read the guide')
    home = (out / 'index.html').read_text(encoding='utf-8')
    assert 'href="guide/">Read the guide</a>' in home                  # l'accueil ne change pas
    assert 'Figure drawn' not in (out / 'guide' / 'index.html').read_text(encoding='utf-8')


def test_fake_site_with_a_guide_is_deterministic(fake):
    build(fake, 'one')
    build(fake, 'two')
    assert site.listing(fake[0] / 'one') == site.listing(fake[0] / 'two')
    assert any(p.startswith('guide/ch4/') for p, _ in site.listing(fake[0] / 'one'))


def test_guide_error_stops_the_site_and_leaves_nothing(fake, monkeypatch):
    def boom(zips, edition, data=None):
        raise guide.GuideError('page ch4 : la cible a disparu')
    monkeypatch.setattr(site, 'compose_guide', REAL_COMPOSE_GUIDE)
    monkeypatch.setattr(guide, 'compose', boom)
    with pytest.raises(site.SiteError, match='guide : page ch4 : la cible a disparu'):
        build(fake)
    assert not (fake[0] / 'site').exists()
    assert not [p for p in fake[0].iterdir() if p.name.startswith('.site-')]


def test_guide_is_composed_from_the_syria_zips_of_the_zips_folder(fake, monkeypatch):
    tmp, zips, downloads = fake
    seen = {}

    def spy(z, edition, data=None):
        seen.update(z=z, edition=edition)
        raise guide.GuideError('stop')
    monkeypatch.setattr(site, 'compose_guide', REAL_COMPOSE_GUIDE)
    monkeypatch.setattr(guide, 'compose', spy)
    with pytest.raises(site.SiteError):
        build(fake)
    assert seen['edition'] == 'AC-V1.11.0'
    assert {k: v.name for k, v in seen['z'].items()} == {'airfields': 'DCS_Airfield_Charts_Syria_1985_airfields.zip',
                                                         'helicopters': 'DCS_Airfield_Charts_Syria_1985_helicopters.zip'}


def test_pins_grid_is_under_the_figure_for_anatomy_and_zoom_only_but_not_for_fiches(gpages):
    pages = gpages[0]
    for pid in ('ch4', 'ch6a', 'ch6b', 'ch9'):
        assert '<div class="anat under">' in pages[pid].html and '<div class="anat">' not in pages[pid].html, pid
    assert '<div class="anat">' in pages['ch11-1'].html and 'anat under' not in pages['ch11-1'].html
    for pid in ('ch1', 'ch2', 'ch10', 'ch11-0', 'ch11-5', 'annex'):
        assert 'class="anat' not in pages[pid].html.split('</style>')[-1], pid
    css = (site.DATA / 'site.css').read_text(encoding='utf-8')
    assert '.anat.under ol.pins{display:block;columns:2;column-gap:32px}' in css
