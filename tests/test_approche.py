"""Lot ac-approche-ils : pages d'approche 11-x (render/approach.py), antennes ILS de `ChartData`, `build_chart.py --approches-seulement`,
republication (`republish.py` : pages 11-x recopiees, `--ajouter`) et fiches. Les tests sur les echantillons s'executent avec
DCS_CHARTS_SAMPLES (comme les autres) ; les tests de la republication et des fiches utilisent de petits zips synthetiques."""
import dataclasses
import html
import io
import math
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from shapely.geometry import box

from render import analysis, approach, build_chart, dcsdata, export, fiches, page, republish, svgdoc
from test_republish import RULE, doc_svg, five_fields, konya_latlon

TEXT = re.compile(r'<text x="([^"]+)" y="([^"]+)" ([^>]*)>(.*?)</text>', re.S)


def texts(svg, y=None):
    out = []
    for m in TEXT.finditer(svg):
        if y is None or abs(float(m.group(2)) - y) < 1e-6:
            out.append(html.unescape(m.group(4)))
    return out


@pytest.fixture(scope='module')
def six(charts, work):
    """Les six bases de Syria (echantillons) : {base: (ChartData, [pages 11-x])} ; log des arrets dans `LOGS`."""
    out = {}
    for base in ('Konya', 'Adana', 'Ramat_David'):
        data = charts[base][0]
        out[base] = (data, approach.build_approach_pages(data, log=lambda *a: None))
    for base in ('Paphos', 'Abu_al-Duhur', 'Gulechoba'):
        data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
        out[base] = (data, approach.build_approach_pages(data, log=lambda *a: None))
    return out


# ---------- 2.1 : quelles pages ----------
def test_a1_one_page_per_ils_end_on_the_samples(six):
    ends = {b: [p.info['end'] for p in six[b][1]] for b in six}
    assert ends == {'Konya': ['01L'], 'Adana': ['05'], 'Ramat_David': ['33'], 'Paphos': ['29'], 'Abu_al-Duhur': [], 'Gulechoba': []}
    for b in six:
        assert [p.id for p in six[b][1]] == [f'11-{k + 1}' for k in range(len(six[b][1]))]


def test_compose_adds_the_approach_pages_after_the_10_9_pages(charts):
    for base, last in (('Konya', ['10-9', '10-9A', '10-9B', '11-1']), ('Adana', ['10-9', '11-1']), ('Ramat_David', ['10-9', '10-9A', '11-1'])):
        assert [p.id for p in charts[base][1]] == last


def test_ils_antennas_of_the_samples(six):
    adana = six['Adana'][0].ils_antennas
    assert set(adana) == {'05'}
    a = adana['05']
    assert (a['callsign'], a['freq_hz'], a['gs_freq_hz']) == ('IADA', 108700000.0, 108700000.0)
    assert a['loc'] == pytest.approx((220391.796875, -46974.203125)) and a['gs'] == pytest.approx((218806.6875, -49094.089844))
    for b in ('Abu_al-Duhur', 'Gulechoba'):
        assert six[b][0].ils_antennas == {}


def lb(**kw):
    d = dict(id='x', type='ILS_LOCALIZER', callsign='IAB', frequency=109.5e6, channel=None, x=0.0, z=0.0, direction=0.0)
    d.update(kw)
    return dcsdata.Beacon(**d)


def test_ils_antenna_info_pairs_a_glide_at_the_same_frequency_to_1_hz():
    ends = [analysis.End('05', 50.0, 47.0, (0.0, 0.0), 0), analysis.End('23', 230.0, 227.0, (1000.0, 1000.0), 0)]
    loc = lb(callsign='IAB', x=2000.0 * math.cos(math.radians(50)), z=2000.0 * math.sin(math.radians(50)), direction=230.0)   # sur l'axe de 05
    gs_ok = lb(type='ILS_GLIDESLOPE', callsign='IAB', frequency=109.5e6 + 0.5, x=300.0, z=100.0)
    gs_far = lb(type='ILS_GLIDESLOPE', callsign='IAB', frequency=109.5e6 + 2.0, x=1.0, z=1.0)
    blist = [loc, gs_far, gs_ok]
    att = analysis.attach_ils(ends, blist)
    info = analysis.ils_antenna_info(att, blist)
    assert set(info) == {'05'}
    assert info['05'] == {'callsign': 'IAB', 'freq_hz': 109.5e6, 'loc': (loc.x, loc.z), 'gs': (300.0, 100.0), 'gs_freq_hz': 109.5e6 + 0.5}
    only_far = analysis.ils_antenna_info(analysis.attach_ils(ends, [loc, gs_far]), [loc, gs_far])
    assert only_far['05']['gs'] is None and only_far['05']['gs_freq_hz'] is None
    dup = lb(callsign='ZZZ', x=loc.x + 50.0, z=loc.z + 50.0, direction=230.0)
    info2 = analysis.ils_antenna_info(analysis.attach_ils(ends, [loc, dup, gs_ok]), [loc, dup, gs_ok])
    assert info2['05']['callsign'] == 'IAB'                                  # le deuxieme localizer d'une extremite n'est pas retenu


def with_antennas(data, name, **changes):
    ant = {k: dict(v) for k, v in data.ils_antennas.items()}
    ant[name].update(changes)
    return dataclasses.replace(data, ils_antennas=ant)


def logged(data):
    msgs = []
    pages = approach.eligible_ends(data, log=msgs.append)
    return pages, msgs


def test_eligible_end_without_glide_is_dropped_with_a_log_line(six):
    data = with_antennas(six['Adana'][0], '05', gs=None, gs_freq_hz=None)
    pages, msgs = logged(data)
    assert pages == [] and len(msgs) == 1 and msgs[0].startswith('approche 05 : ecartee, pas de glide')
    ends = [dataclasses.replace(e) for e in data.ends]
    ends[0].ils = 'IADA 108.70 (LOC)'                                          # texte `(LOC)` : pas de glide, meme si une antenne est donnee
    pages, msgs = logged(dataclasses.replace(six['Adana'][0], ends=ends))
    assert pages == [] and msgs[0].startswith('approche 05 : ecartee, pas de glide')


@pytest.mark.parametrize('mhz, kept', [(108.10, True), (111.95, True), (108.05, False), (112.00, False), (118.0, False)])
def test_eligible_end_localizer_band(six, mhz, kept):
    data = with_antennas(six['Adana'][0], '05', freq_hz=mhz * 1e6)
    pages, msgs = logged(data)
    assert bool(pages) is kept
    if not kept:
        assert msgs == [f'approche 05 : ecartee, frequence du localizer {mhz:.2f} MHz hors de 108,10 a 111,95 MHz']


def test_eligible_end_glide_antenna_must_be_on_the_runway(six):
    data = six['Adana'][0]
    e = next(e for e in data.ends if e.name == '05')
    az = math.radians(e.az_grid)
    L = page.rw_axes(data.runways[e.rw])[2]
    for s, kept in ((0.0, True), (L, True), (-5.0, False), (L + 5.0, False), (330.0, True)):
        gs = (e.pos[0] + math.cos(az) * s, e.pos[1] + math.sin(az) * s)
        pages, msgs = logged(with_antennas(data, '05', gs=gs))
        assert bool(pages) is kept, s
        if not kept:
            assert msgs[0].startswith('approche 05 : ecartee, antenne de glide hors de la piste')
        else:
            assert pages[0][2] == pytest.approx(s, abs=1e-6)


def test_two_ils_ends_are_numbered_in_natural_order_of_the_end_names(six):
    data = six['Adana'][0]
    ends = [dataclasses.replace(e) for e in data.ends]
    e23 = next(e for e in ends if e.name == '23')
    e23.ils = 'IAD2 110.10'
    az = math.radians(e23.az_grid)
    ant = {k: dict(v) for k, v in data.ils_antennas.items()}
    ant['23'] = {'callsign': 'IAD2', 'freq_hz': 110.1e6, 'loc': (e23.pos[0] + math.cos(az) * 2700, e23.pos[1] + math.sin(az) * 2700),
                 'gs': (e23.pos[0] + math.cos(az) * 300, e23.pos[1] + math.sin(az) * 300), 'gs_freq_hz': 110.1e6}
    d2 = dataclasses.replace(data, ends=ends[::-1], ils_antennas=ant)               # extremites dans le desordre
    pages = approach.build_approach_pages(d2, log=lambda *a: None)
    assert [(p.id, p.info['end']) for p in pages] == [('11-1', '05'), ('11-2', '23')]
    assert ['ILS 05' in pages[0].svg, 'ILS 23' in pages[1].svg] == [True, True]


# ---------- 2.3 : la geometrie ----------
def test_glide_offset_and_tch(six):
    want = {'Adana': (330.6, 55), 'Konya': (352.5, 60), 'Paphos': (324.7, 55), 'Ramat_David': (218.7, 40)}
    for base, (s_gs, tch) in want.items():
        (p,) = six[base][1]
        assert p.info['s_gs'] == pytest.approx(s_gs, abs=0.2)
        assert p.info['tch'] == tch
        assert approach.tch_ft(p.info['s_gs']) == pytest.approx(p.info['s_gs'] * math.tan(math.radians(3)) * 3.28084)


def test_slope_altitudes():
    assert approach.altitude_ft(56, 10, 330.6) == pytest.approx(56 + (18520 + 330.6) * math.tan(math.radians(3)) * 3.28084)
    assert approach.altitude_ft(0, 0, 100.0) == pytest.approx(approach.tch_ft(100.0))
    assert approach.round_to(3296.9, 10) == 3300 and approach.round_to(3295.0, 10) == 3300 and approach.round_to(3294.9, 10) == 3290
    assert approach.round_to(57.4, 5) == 55 and approach.round_to(57.6, 5) == 60


def test_table_altitudes_and_dme_distances_of_adana_and_paphos(six):
    (a,) = six['Adana'][1]
    assert list(a.info['table_altitudes'].values()) == [3300, 2980, 2660, 2340, 2020, 1710, 1390, 1070, 750, 430]
    assert list(a.info['dme_distances'].values()) == ['6,6', '5,6', '4,6', '3,6', '2,6', '1,6', '0,6', '0,4', '1,4', '2,4']
    assert a.info['profile_altitudes'] == {10: 3300, 8: 2660, 6: 2020, 4: 1390, 2: 750}
    (p,) = six['Paphos'][1]
    assert list(p.info['table_altitudes'].values()) == [3320, 3000, 2690, 2370, 2050, 1730, 1410, 1090, 770, 460]
    assert list(p.info['dme_distances'].values()) == ['9,6', '8,6', '7,6', '6,6', '5,6', '4,6', '3,6', '2,6', '1,6', '0,6']


def test_the_dme_is_the_nearest_dme_beacon_to_the_threshold(six):
    want = {'Adana': ('VOR/DME', 'ADA'), 'Konya': ('VOR/DME', 'KNY'), 'Paphos': ('VOR/DME', 'PHA'), 'Ramat_David': ('VORTAC', 'RMD')}
    for base, w in want.items():
        data = six[base][0]
        e = next(e for e in data.ends if e.name == six[base][1][0].info['end'])
        b = approach.dme_of(data, e)
        assert (b.kind, b.callsign) == w
    data = six['Adana'][0]
    assert approach.dme_of(dataclasses.replace(data, beacons=[b for b in data.beacons if b.kind == 'NDB']), data.ends[0]) is None


def test_page_without_dme_has_two_table_rows(six):
    data = six['Adana'][0]
    d2 = dataclasses.replace(data, beacons=[b for b in data.beacons if b.kind == 'NDB'])
    (p,) = approach.build_approach_pages(d2, log=lambda *a: None)
    assert p.info['dme'] is None and p.info['dme_distances'] == {}
    assert 'NM DU SEUIL' in texts(p.svg) and 'ALTITUDE FT' in texts(p.svg) and texts(p.svg, 188.4) == [p.info['mentions'][0]]       # la mention remonte : plus de ligne DME


# ---------- 2.2, 2.6 : les textes ----------
def test_bands_and_mentions_of_adana(six):
    (p,) = six['Adana'][1]
    row = texts(p.svg, 27.4)
    assert row == ['ILS 05', 'LOC', 'IADA 108.70', 'CAP', '052°', 'SEUIL', '56 FT', 'PENTE', '3,0°', 'TCH', '55 FT']
    bal = texts(p.svg, 33.6)
    assert bal == ['BALISES', 'VOR/DME', 'ADA 112.70 · 3,4 NM du seuil', 'NDB', 'ADN 395 · 0,4 NM du seuil']
    assert p.info['mentions'] == ("Pas une carte d'approche réelle : DCS ne donne ni procédure ni minima. Axe : cap au compas du cockpit (grille − déclinaison 1985).",
                                  "Plan de 3° supposé, depuis l'antenne de glide (331 m après le seuil) ; relief non représenté. Données : beacons.lua, pydcs.")
    allt = texts(p.svg)
    assert all(m in allt for m in p.info['mentions'])
    assert 'PROFIL — PLAN DE 3,0°' in allt and 'SEUIL 05 56 FT' in allt and 'NORD GRILLE (NORD DU JEU) EN HAUT' in allt and '2 NM' in allt
    assert 'ILS 05' in allt and any(t.startswith('052°  IADA 108.70') for t in allt)
    assert '11-1' in allt and 'v1' in allt


def test_cap_is_the_rounded_cockpit_heading(six):
    for base in ('Konya', 'Paphos', 'Ramat_David'):
        (p,) = six[base][1]
        e = next(e for e in six[base][0].ends if e.name == p.info['end'])
        assert p.info['cap'] == round(e.mag) % 360
        assert f'{p.info["cap"]:03d}°' in texts(p.svg, 27.4)


def test_page_is_a_set_of_svgdoc_layers_in_order(six):
    for base in six:
        for p in six[base][1]:
            labels = re.findall(r'inkscape:groupmode="layer" id="[^"]*" inkscape:label="([^"]*)"', p.svg)
            labels = [html.unescape(l) for l in labels]
            assert labels == sorted(labels, key=svgdoc.LAYERS.index) and len(set(labels)) == len(labels)
            assert 'Insets' not in labels and 'Parking' not in labels


# ---------- 2.4 : la vue en plan ----------
def layer_lines(svg, name):
    return [ln for _, _, ln in republish._layer_lines(svg, name)]


def test_plan_labels_do_not_overlap_and_the_arrow_points_to_the_threshold(six):
    G = page.elem_geom
    for base in ('Adana', 'Konya', 'Paphos', 'Ramat_David'):
        (p,) = six[base][1]
        svg = p.svg
        runway = [ln for ln in layer_lines(svg, 'Runways') if ln.startswith('<line')]
        marks = layer_lines(svg, 'Runway markings')
        axis = [ln for ln in marks if 'stroke-dasharray' in ln]
        ticks = [ln for ln in marks if ln.startswith('<line') and 'stroke-dasharray' not in ln]
        bearings = layer_lines(svg, 'Runway bearings')
        antennas = [ln for ln in bearings if ln.startswith('<rect') or ln.startswith('<circle')]
        others = [ln for ln in marks if ln.startswith('<text')] + [ln for ln in bearings if ln.startswith('<g ')] \
            + [ln for ln in layer_lines(svg, 'Frame & grid') if ln.startswith('<text')]
        nav = [ln for ln in layer_lines(svg, 'Navaids') if ln.startswith('<text')]
        pairs = [nav[i:i + 2] for i in range(0, len(nav), 2)]
        geoms = [G('\n'.join(q)) for q in pairs]
        fx, fy, fw, fh = p.info['frame']
        assert pairs and all(free for _, _, free in p.info['navaid_labels'])
        for i, g in enumerate(geoms):
            assert box(fx, fy, fx + fw, fy + fh).contains(g)
            assert not any(g.intersects(o) for j, o in enumerate(geoms) if j != i)
            for el in axis + ticks + runway + antennas + others:
                assert not g.intersects(G(el)), (base, el)
        # la fleche : de la queue a la pointe, vers le debut de l'axe (le seuil)
        tail = tuple(float(v) for v in re.search(r'x1="([-\d.]+)" y1="([-\d.]+)"', next(l for l in bearings if l.startswith('<line'))).groups())
        tip = tuple(float(v) for v in re.search(r'points="([-\d.]+),([-\d.]+)', next(l for l in bearings if l.startswith('<polygon'))).groups())
        thr = tuple(float(v) for v in re.search(r'x1="([-\d.]+)" y1="([-\d.]+)"', axis[0]).groups())
        v1, v2 = (tip[0] - tail[0], tip[1] - tail[1]), (thr[0] - tip[0], thr[1] - tip[1])
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / math.hypot(*v1) / math.hypot(*v2)
        assert cos > 0.9999


def test_plan_scale_fits_the_required_points_with_a_6_mm_margin(six):
    for base in ('Adana', 'Paphos'):
        (p,) = six[base][1]
        data = six[base][0]
        e = next(e for e in data.ends if e.name == p.info['end'])
        fx, fy, fw, fh = p.info['frame']
        s = p.info['scale']
        az = math.radians(e.az_grid)
        far10 = (e.pos[0] - math.cos(az) * 10.5 * 1852, e.pos[1] - math.sin(az) * 10.5 * 1852)
        pts = [e.pos, far10] + [(b.x, b.z) for b in data.beacons]
        xs, zs = [q[0] for q in pts], [q[1] for q in pts]
        ant = data.ils_antennas[e.name]['loc']
        xs.append(ant[0]), zs.append(ant[1])
        rw = data.runways[e.rw]
        xs += [rw.p0[0], rw.p1[0]]
        zs += [rw.p0[1], rw.p1[1]]
        # un des deux axes est serre a la marge de 6 mm
        spans = ((max(zs) - min(zs)) * s, fw - 12.0), ((max(xs) - min(xs)) * s, fh - 12.0)
        assert any(a == pytest.approx(b, abs=0.01) for a, b in spans) and all(a <= b + 1e-6 for a, b in spans)


# ---------- build_chart --approches-seulement (A5) ----------
def test_build_chart_approaches_only_writes_just_the_approach_page(work, tmp_path, capsys):
    rc = build_chart.main(['build_chart.py', 'Syria', 'Paphos', '--config', str(work.source), '--out', str(tmp_path), '--approches-seulement'])
    assert rc == 0
    files = sorted(p.name for p in (tmp_path / 'syria').iterdir())
    assert files == ['LCPH_Paphos_11-1.pdf', 'LCPH_Paphos_11-1.png', 'LCPH_Paphos_11-1.svg']
    out = capsys.readouterr().out
    assert 'recueil' not in out and '10-9' not in out and '11-1 : LCPH_Paphos_11-1.pdf' in out
    assert len(PdfReader(str(tmp_path / 'syria' / 'LCPH_Paphos_11-1.pdf')).pages) == 1


def test_build_chart_approaches_only_writes_nothing_without_ils(work, tmp_path, capsys):
    rc = build_chart.main(['build_chart.py', 'Syria', 'Gulechoba', '--config', str(work.source), '--out', str(tmp_path), '--approches-seulement'])
    assert rc == 0 and not (tmp_path / 'syria').exists()


# ---------- 2.7 : republish et fiches ----------
@pytest.fixture(scope='module')
def zips(six, tmp_path_factory):
    """Un zip synthetique d'une chart `Konya` (page de parkings 10-9A, recueil) ; le zip du meme theatre avec sa page 11-1 ; le dossier a
    plat de la page 11-1 (sortie de `build_chart.py --approches-seulement`)."""
    root = tmp_path_factory.mktemp('approche_zip')
    lat, lon = konya_latlon()
    s, p, n = export.export_page(doc_svg('<circle cx="9" cy="9" r="3"/>', *five_fields(lat, lon), RULE), root / 'Konya_10-9A')
    (page11,) = six['Konya'][1]
    flat = root / 'flat'
    s1, p1, n1 = export.export_page(page11.svg, flat / 'Konya_11-1')
    export.merge_pdfs([p], root / 'Konya_charts0.pdf')
    export.merge_pdfs([p, p1], root / 'Konya_charts1.pdf')

    def build(path, with11):
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('pack/notes.txt', b'unchanged')
            z.writestr('pack/png/Konya_10-9A.png', n.read_bytes())
            z.writestr('pack/svg/Konya_10-9A.svg', s.read_bytes())
            z.writestr('pack/pdf/Konya_10-9A.pdf', p.read_bytes())
            if with11:
                z.writestr('pack/png/Konya_11-1.png', n1.read_bytes())
                z.writestr('pack/svg/Konya_11-1.svg', s1.read_bytes())
                z.writestr('pack/pdf/Konya_11-1.pdf', p1.read_bytes())
            z.writestr('pack/pdf/Konya_charts.pdf', (root / ('Konya_charts1.pdf' if with11 else 'Konya_charts0.pdf')).read_bytes(), zipfile.ZIP_STORED)
    build(root / 'without.zip', False)
    build(root / 'with.zip', True)
    return SimpleNamespace(without=root / 'without.zip', with11=root / 'with.zip', flat=flat, root=root)


def names(path):
    with zipfile.ZipFile(path) as z:
        return [i.filename for i in z.infolist()]


def test_republish_copies_an_approach_page_as_it_is(zips, tmp_path, capsys):
    out = tmp_path / 'o.zip'
    assert republish.main(['Syria', '--zip', str(zips.with11), '--out', str(out)]) == 0
    summary = capsys.readouterr().out
    assert 'pages reexportees : 1' in summary and "pages d'approche 11-x : 1 recopiees telles quelles ; 0 ajoutees" in summary
    with zipfile.ZipFile(zips.with11) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        for n in ('pack/png/Konya_11-1.png', 'pack/svg/Konya_11-1.svg', 'pack/pdf/Konya_11-1.pdf', 'pack/notes.txt'):
            assert a.read(n) == b.read(n)
        rec = PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf')))
        assert len(rec.pages) == 2 and 'ILS 01L' in rec.pages[1].extract_text()


def test_republish_ajouter_inserts_the_pages_before_the_collected_pdf(zips, tmp_path, capsys):
    out = tmp_path / 'o.zip'
    assert republish.main(['Syria', '--zip', str(zips.without), '--out', str(out), '--ajouter', str(zips.flat)]) == 0
    summary = capsys.readouterr().out
    assert "pages d'approche 11-x : 0 recopiees telles quelles ; 1 ajoutees" in summary and '  Konya : 1 ajoutee(s) (11-1)' in summary
    got = names(out)
    old = names(zips.without)
    k = old.index('pack/pdf/Konya_charts.pdf')
    assert got == old[:k] + ['pack/pdf/Konya_11-1.pdf', 'pack/png/Konya_11-1.png', 'pack/svg/Konya_11-1.svg'] + old[k:]
    with zipfile.ZipFile(out) as z:
        for ext in ('pdf', 'png', 'svg'):
            assert z.read(f'pack/{ext}/Konya_11-1.{ext}') == (zips.flat / f'Konya_11-1.{ext}').read_bytes()
        rec = PdfReader(io.BytesIO(z.read('pack/pdf/Konya_charts.pdf')))
        assert len(rec.pages) == 2 and 'ILS 01L' in rec.pages[-1].extract_text()
        assert z.read('pack/notes.txt') == b'unchanged'
    assert sorted(got) == sorted(names(zips.with11))                       # les memes entrees que le zip qui avait deja la page


def stops(zips, tmp_path, folder, match, zin=None):
    out = tmp_path / 'stop.zip'
    with pytest.raises(republish.RepublishError, match=match):
        republish.republish_zip('Syria', zin or zips.without, out, folder)
    assert not out.exists()                                              # au moindre doute, rien n'est ecrit


def copy_flat(zips, tmp_path, name, drop=(), rename=None):
    d = tmp_path / name
    d.mkdir()
    for ext in ('svg', 'pdf', 'png'):
        if ext not in drop:
            src = zips.flat / f'Konya_11-1.{ext}'
            (d / ((rename or 'Konya_11-1') + f'.{ext}')).write_bytes(src.read_bytes())
    return d


def test_republish_ajouter_stops(zips, tmp_path):
    stops(zips, tmp_path, copy_flat(zips, tmp_path, 'other', rename='Other_11-1'), "la chart Other n'est pas dans le zip")
    stops(zips, tmp_path, copy_flat(zips, tmp_path, 'exists'), 'la page existe deja dans le zip', zin=zips.with11)
    for ext in ('svg', 'pdf', 'png'):
        stops(zips, tmp_path, copy_flat(zips, tmp_path, f'no_{ext}', drop=(ext,)), f'fichier {ext} manquant')
    stops(zips, tmp_path, copy_flat(zips, tmp_path, 'bad', rename='Konya_10-9'), "pas une page d'approche")
    stops(zips, tmp_path, tmp_path / 'absent', 'dossier introuvable')
    empty = tmp_path / 'empty'
    empty.mkdir()
    stops(zips, tmp_path, empty, "aucune page d'approche")


def test_republish_without_approach_pages_is_unchanged_by_the_option_code(zips, tmp_path, capsys):
    out = tmp_path / 'o.zip'
    assert republish.main(['Syria', '--zip', str(zips.without), '--out', str(out)]) == 0
    assert "pages d'approche" not in capsys.readouterr().out
    assert names(out) == names(zips.without)


def test_fiches_accept_the_approach_pages_after_the_10_9_pages(tmp_path):
    z = tmp_path / 'a.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        for n in ('Konya_11-2', 'Konya_11-1', 'Konya_10-9B', 'Konya_10-9A', 'Konya_10-9'):
            zf.writestr(f'pack/png/{n}.png', b'x')
    pages = fiches.chart_pages([z])
    assert [s for s, _ in pages['Konya']] == ['10-9', '10-9A', '10-9B', '11-1', '11-2']
    bad = tmp_path / 'b.zip'
    with zipfile.ZipFile(bad, 'w') as zf:
        zf.writestr('pack/png/Konya_12-1.png', b'x')
    with pytest.raises(fiches.FichesError, match='nom inattendu'):
        fiches.chart_pages([bad])
