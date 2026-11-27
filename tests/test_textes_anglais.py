"""Lot ac-textes-anglais : tout texte ecrit sur une page produite par `render/` est en anglais (pages 10-9, 10-9A..., 10-8, 11-x,
repertoire des heliports), avec un point decimal et sans separateur des milliers ; les outils de relecture (`update_coords`,
`republish`, `fiches`) lisent l'anglais et refusent clairement une page publiee en francais.
Les tests qui rendent les bases de Syria demandent DCS_CHARTS_SAMPLES ; les autres sont purs ou synthetiques."""
import html
import io
import re
import zipfile

import pytest
from PIL import Image, ImageDraw

import test_coords
import test_directory_line
import test_terrains_herbe
import test_vicinity
from test_coords import old_zip                    # noqa: F401  (fixture)
from test_vicinity import built, proj              # noqa: F401  (fixtures)

from render import analysis, approach, compose, directory, fiches, geo, layout, page, republish, update_coords


def quiet(*a):
    pass


# ---------- le detecteur de francais (P2) ----------
ACCENTS = re.compile('[àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ]')
FRENCH_WORDS = {'DE', 'DU', 'DES', 'DONT', 'EN', 'SEUIL', 'PISTE', 'AIRES', 'BALISES', 'OUVERT', 'ABRI', 'NORD', 'FOND', 'CARTE',
                'PAS', 'RÉELLE', 'REELLE'}


def page_texts(svg):
    return [html.unescape(t) for t in re.findall(r'<text\b[^>]*>(.*?)</text>', svg, re.S)]


def french_in(svg, names=()):
    """[(texte, raison)] des <text> d'une page qui portent une lettre accentuee francaise ou un des mots francais de la liste ;
    les noms de villes et de bases (`names`, et tout texte `<NOM> km`) sont exclus."""
    out = []
    for t in page_texts(svg):
        if any(n.lower() in t.lower() for n in names) or re.search(r' km$', t):
            continue
        if ACCENTS.search(t):
            out.append((t, 'accent'))
        words = set(re.findall(r'[A-Za-zÀ-ÿ]+', t)) & FRENCH_WORDS
        if words:
            out.append((t, sorted(words)))
    return out


def test_the_detector_finds_french():
    assert french_in('<text x="1" y="2">PAS POUR LA NAVIGATION RÉELLE</text>')
    assert french_in('<text x="1" y="2">PISTE EN HERBE</text>')
    assert french_in('<text x="1" y="2">Données : beacons.lua</text>')
    assert french_in('<text x="1" y="2">1,2 NM du seuil</text>') == []            # minuscules : c'est la table qui est testee ailleurs
    assert french_in('<text x="1" y="2">NOT FOR REAL-WORLD NAVIGATION</text>') == []
    assert french_in('<text x="1" y="2">DE LA VILLE</text>', names=('de la ville',)) == []


# ---------- les pages rendues (echantillons) ----------
@pytest.fixture(scope='module')
def rendered(work):
    out = {}
    for base in ('Konya', 'Paphos', 'At_Tanf', 'Der_Salman', 'Adana'):
        data = analysis.analyze(work, 'Syria', base, 1985, log=quiet)
        out[base] = (data, {p.id: p for p in compose.build_pages(data, log=quiet)})
    return out


def _names(data):
    return (data.airport_name, data.base, data.theatre)


def test_p2_no_french_on_the_rendered_pages(rendered):
    seen = 0
    for base, (data, pages) in rendered.items():
        for pid, pg in pages.items():
            assert french_in(pg.svg, _names(data)) == [], (base, pid)
            seen += 1
    ids = {pid for _, pages in rendered.values() for pid in pages}
    assert {'10-9', '10-9A', '11-1'} <= ids and seen >= 8
    assert any(rendered[b][0].on_f10 for b in rendered)              # la page sur fond F10 (legende `BACKGROUND: F10 MAP`) en est


def test_p2_no_french_on_a_10_8(built):
    data, _, pg, _, _ = built
    assert pg.id == '10-8' and french_in(pg.svg, _names(data)) == []


def test_p1_10_9_of_konya_has_the_table_texts(rendered):
    data, pages = rendered['Konya']
    t = set(page_texts(pages['10-9'].svg))
    want = {'AIRFIELD', 'NAVAIDS', 'RWY', 'MAG HDG', 'LENGTH × WIDTH', 'THR ELEV', 'ILS', 'OPEN', 'SHELTER (HAS, HANGAR)',
            'HELICOPTER ONLY', f'ELEV {data.elev_ft} FT', f'PARKING: {len(data.parkings)} ({data.n_shelter} SHELTERED)',
            'DCS AIRFIELD CHARTS · data: F10 map and DCS World files (Eagle Dynamics) · taxiways © OpenStreetMap contributors',
            'NOT FOR REAL-WORLD NAVIGATION'}
    assert want <= t, want - t
    assert '11037 × 188 FT (3364 × 57 M)' in t                      # pas de separateur des milliers


def test_p1_10_9a_of_paphos_and_konya(rendered):
    for base in ('Konya', 'Paphos'):
        data, pages = rendered[base]
        t = set(page_texts(pages['10-9A'].svg))
        assert {'PARKING', 'PARKING NUMBERS AS IN DCS', 'OPEN', 'SHELTER (HAS, HANGAR)', 'HELICOPTER ONLY',
                'NOT FOR REAL-WORLD NAVIGATION', f'ELEV {data.elev_ft} FT'} <= t, base
        assert not any(x.startswith('PARKING:') for x in t)


def test_p1_helicopter_pages(rendered):
    data, pages = rendered['At_Tanf']
    t = set(page_texts(pages['10-9'].svg))
    assert data.on_f10
    assert {'HELICOPTER BASE', f'HELIPADS: {len(data.parkings)}', 'HELIPAD (DCS NUMBER) · BACKGROUND: F10 MAP'} <= t
    data, pages = rendered['Der_Salman']
    t = set(page_texts(pages['10-9'].svg))
    assert not data.on_f10 and {'HELICOPTER BASE', 'HELIPAD (DCS NUMBER)', f'HELIPADS: {len(data.parkings)}'} <= t


def test_p1_approach_page_of_konya(rendered):
    data, pages = rendered['Konya']
    pg = pages['11-1']
    t = page_texts(pg.svg)
    for s in ('NAVAIDS', 'CRS', 'THR', 'GP', 'TCH', 'LOC', 'NM FROM THR', 'ALTITUDE FT', 'GRID NORTH (GAME NORTH) UP',
              'PROFILE — 3.0° GLIDE PATH', 'THR 01L 3396 FT', '3.0°', 'KNY 390 · 1.8 NM FROM THR',
              'Not a real approach chart: DCS gives no procedure and no minima. Course: cockpit compass heading (grid − 1985 declination).',
              '3° glide path assumed, from the glide slope antenna (353 m past the threshold); terrain not shown. Data: beacons.lua, DCS mission editor.'):
        assert s in t, s
    assert not any(x in ('CAP', 'SEUIL', 'PENTE', 'BALISES') for x in t)


# ---------- les nombres (P3) ----------
COMMA_DECIMAL = re.compile(r'\d,\d')
THOUSANDS = re.compile(r'\d[   ]\d{3}(?!\d)')


def _numbers_ok(texts, where):
    for t in texts:
        if t.startswith('MGRS '):                                    # la reference MGRS est faite de groupes de chiffres, pas d'un nombre
            continue
        assert not COMMA_DECIMAL.search(t), (where, t)
        assert not THOUSANDS.search(t), (where, t)


def test_p3_no_decimal_comma_nor_thousands_separator_on_the_pages(rendered, built):
    for base, (data, pages) in rendered.items():
        for pid, pg in pages.items():
            _numbers_ok(page_texts(pg.svg), (base, pid))
    _numbers_ok(page_texts(built[2].svg), '10-8')


def test_p3_the_number_helpers():
    assert approach.nm_text(0.4 * approach.NM) == '0.4' and approach.nm_text(3.4 * approach.NM) == '3.4'
    assert directory.km_text(6.5) == '6.5 km' and directory.km_text(0.3) == '0.3 km' and directory.km_text(53.2) == '53.2 km'
    assert geo.thin(11089) == '11089' and geo.thin(2006.4) == '2006' and geo.thin(820) == '820'
    assert layout.ratio_text(0.1515) == '1:6601' and layout.ratio_text(0.01) == '1:100000'
    assert directory.nearest_town(32.7, 35.3, [('Nazareth', 32.7, 35.3)]).endswith(' of Nazareth')
    assert re.fullmatch(r'\d+\.\d km [NESW]{1,2} of Nazareth', directory.nearest_town(32.76, 35.3, [('Nazareth', 32.7, 35.3)]))


def test_p3_the_runway_table_writes_feet_without_separator():
    out = test_terrains_herbe._table('dur')
    assert '2953 × 66 FT (900 × 20 M)' in out
    for head in ('RWY', 'MAG HDG', 'LENGTH × WIDTH', 'THR ELEV', 'ILS'):
        assert f'>{head}</text>' in out, head
    assert not THOUSANDS.search(out) and 'GRASS RUNWAY' not in out
    assert 'GRASS RUNWAY' in test_terrains_herbe._table('herbe')


# ---------- le repertoire des heliports (P1, P2) ----------
def test_the_directory_pages_are_in_english(work, monkeypatch):
    written = []
    real = directory.draw_text

    def spy(d, xy, s, *a, **kw):
        written.append(s)
        return real(d, xy, s, *a, **kw)

    monkeypatch.setattr(directory, 'draw_text', spy)
    sites = directory.list_sites(work, 'Syria', log=quiet)
    towns = directory.load_towns('Syria')
    for s in sites:
        s.town = directory.nearest_town(s.lat, s.lon, towns)
    pages = directory.build_pages(sites)
    n_index = directory.index_page_count(len(sites))
    for k, pg in enumerate(pages):
        for site, _ in pg['entries']:
            site.page = n_index + k + 1
    directory.draw_index_page('Syria', sites, 0, n_index)
    index = list(written)
    del written[:]
    cache_val = {}

    def cache(site):
        if site.name not in cache_val:
            cache_val.clear()
            cache_val[site.name] = directory.load_site_mosaic(site)
        return cache_val[site.name]

    for pg in pages[:2]:
        directory.draw_directory_page('Syria', pg, cache, [])
    plans = list(written)
    for s in ('HELIPORT DIRECTORY', 'NAME', 'PADS', 'POSITION', 'NEAREST TOWN', 'PAGE', 'NOT FOR REAL-WORLD NAVIGATION',
              'Page: page of the PDF collection · position: DCS airfield reference point · nearest town: towns.json list'):
        assert s in index, s
    assert 'All plans at the same scale · pads and numbers: DCS mission editor · background: F10 map · WGS84' in plans
    assert any(re.fullmatch(r'\d+ PADS?', x) for x in plans) and any(re.fullmatch(r'ELEV \d+ FT', x) for x in plans)
    assert any(re.fullmatch(r'PADS \d+×\d+ M', x) for x in plans)
    assert any(re.fullmatch(r'\d+\.\d km [NESW]{1,2} of .+', x) for x in plans)
    for x in index + plans:
        assert not ACCENTS.search(x) and not (set(re.findall(r'[A-Za-z]+', x)) & FRENCH_WORDS), x
        assert not COMMA_DECIMAL.search(x) and not THOUSANDS.search(x), x


def test_the_directory_series_without_digits_is_others():
    assert directory.OTHERS == 'OTHERS' and directory.series_of('Sanliurfa Heliport') == 'OTHERS'
    assert directory.aires_count_text(1) == '1 PAD' and directory.aires_count_text(11) == '11 PADS'
    assert directory.dims_line([directory.Aire('01', 0.0, 0.0, 42.0, 34.0)]) == 'PADS 42×34 M'


def test_the_two_group_line_reads_p2_km_dir_of_p1(monkeypatch):
    from render import fiches as f
    sites, pages, _ = f.replay_directory('Syria')
    site = next(s for s in sites if s.form == directory.TWO)
    pos = next(p for pg in pages for s, p in pg['entries'] if s is site)
    written = []
    real = directory.draw_text
    monkeypatch.setattr(directory, 'draw_text', lambda d, xy, s, *a, **kw: (written.append(s), real(d, xy, s, *a, **kw))[1])
    monkeypatch.setattr(directory, 'draw_plan', lambda site, img, g, c, w, h, first, stats: Image.new('RGB', (w, h), (222, 222, 222)))
    site.town, site.lat, site.lon = '1.0 km N of X', 0.0, 0.0
    page_im = Image.new('RGB', directory.PAGE_PX, (255, 255, 255))
    directory.draw_tile(page_im, ImageDraw.Draw(page_im), site, pos, None, None, [])
    assert any(re.fullmatch(r'\S+ \d+\.\d km [NESW]{1,2} of \S+', x) for x in written), written


# ---------- les outils (P6) ----------
def test_p6_update_coords_reads_english_and_refuses_french():
    index = update_coords.arp_index('Syria')
    new, fields = update_coords.rewrite_page(test_coords.svg_page(test_coords.konya_arp()), 'Konya_10-9', index)
    assert fields[0] == 'ELEV 3392 FT' and len(fields) == 5
    with pytest.raises(update_coords.UpdateError, match=r"Konya_10-9 : l'elevation .*ÉLÉV\. 3392 FT.*page publiee en francais"):
        update_coords.rewrite_page(test_coords.svg_page(test_coords.konya_arp(), elev='ÉLÉV. 3392 FT'), 'Konya_10-9', index)


def test_p6_the_elevation_check():
    assert page.elevation_text_problem('ELEV 159 FT') is None and page.elevation_text_problem('ELEV 0 FT') is None and page.elevation_text_problem('ELEV -12 FT') is None
    for bad in ('ÉLÉV. 159 FT', 'ELEV. 159 FT', 'ELEV 159 M', 'ELEV 1 159 FT', 'ELEV  159 FT', ''):
        assert 'page publiee en francais' in page.elevation_text_problem(bad), bad


def test_p6_republish_five_field_header_refuses_a_french_elevation():
    index = update_coords.arp_index('Syria')
    lat, lon = geo.Projection.for_theatre('Syria').to_latlon(*_konya_xz())
    var = 'VAR 3°E (1985)'
    row = lambda elev: '\n'.join(page.header_row(elev, lat, lon, var))
    svg = lambda elev: ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 148 210">\n<g>\n'
                        + row(elev) + '\n</g>\n</svg>')
    out, fields, how = republish.check_or_rewrite_header(svg('ELEV 3392 FT'), 'Konya_10-9A', index)
    assert how == 'verifie' and fields[0] == 'ELEV 3392 FT'
    with pytest.raises(republish.RepublishError, match=r'Konya_10-9A : l.elevation .*page publiee en francais'):
        republish.check_or_rewrite_header(svg('ÉLÉV. 3392 FT'), 'Konya_10-9A', index)


def _konya_xz():
    ap = next(a for a in __import__('render.dcsdata', fromlist=['x']).load_terrain('Syria').airports.values() if a.name == 'Konya')
    return ap.position.x, ap.position.y


def _french(zin, zout, member_re):
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(zout, 'w') as b:
        for i in a.infolist():
            data = a.read(i.filename)
            if re.search(member_re, i.filename):
                data = re.sub(r'ELEV (\d+) FT', r'ÉLÉV. \1 FT', data.decode('utf-8')).encode('utf-8')
            b.writestr(i, data)
    return zout


def test_p6_update_coords_stops_on_a_french_zip(old_zip, tmp_path, capsys):          # noqa: F811
    zin, _ = old_zip
    fr = _french(zin, tmp_path / 'fr.zip', r'svg/Konya_10-9A\.svg$')
    assert update_coords.main(['Syria', '--zip', str(fr), '--out', str(tmp_path / 'u.zip')]) == 1
    err = capsys.readouterr().err
    assert 'Konya_10-9A' in err and 'page publiee en francais' in err and not (tmp_path / 'u.zip').exists()


def test_p6_republish_stops_on_a_french_10_8(tmp_path, capsys):
    zin, _ = test_vicinity.vicinity_zip(tmp_path)
    fr = _french(zin, tmp_path / 'fr.zip', r'svg/Konya_10-8\.svg$')
    assert republish.main(['Syria', '--zip', str(fr), '--out', str(tmp_path / 'r.zip')]) == 1
    err = capsys.readouterr().err
    assert 'Konya_10-8' in err and 'page publiee en francais' in err and not (tmp_path / 'r.zip').exists()


def test_p6_republish_stops_on_a_french_directory(tmp_path, capsys):
    tiles, _ = republish.directory_tiles('Syria')
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no not in test_directory_line.SYRIA_PAGES:
            continue
        im = pages.setdefault(no, Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
        directory.draw_line2(ImageDraw.Draw(im), x, w, base2, town.replace(' of ', ' de '), dims.replace('PADS', 'AIRES'), 36)
    zin = test_directory_line.make_zip(tmp_path / 'fr.zip', pages)
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(tmp_path / 'r.zip')]) == 1
    err = capsys.readouterr().err
    assert 'repertoire page' in err and 'publie en francais' in err and not (tmp_path / 'r.zip').exists()


def test_p6_fiches_reads_the_arp_of_an_english_page(old_zip):                                      # noqa: F811
    _, pages = old_zip
    lat, lon = fiches.read_arp(pages['Konya_10-9'])
    assert (lat, lon) == pytest.approx(geo.Projection.for_theatre('Syria').to_latlon(*_konya_xz()), abs=0.01)
    assert 'ELEV 3392 FT' in pages['Konya_10-9']
