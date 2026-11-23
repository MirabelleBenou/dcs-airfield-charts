"""Lot ac-approches-v1 : les pages d'approche aux instruments (render/approach_ifr.py) et l'outil `render/build_approaches.py`.

Donnees synthetiques : une 10-9 de Konya fabriquee avec les briques du moteur (en-tete, places de pydcs, pistes), une page ILS,
un `beacons.lua` de trois balises et une grille de relief plate ; les zips refaits sont controles (entrees recopiees a
l'octet, pages ILS retirees, recueil, pages 11-xH dans le zip helicopters, deux passes identiques). Les tests sur les
echantillons (DCS_CHARTS_SAMPLES) relisent la vraie 10-9 du moteur et le vrai `beacons.lua` de Syria."""
import hashlib
import io
import json
import math
import re
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from pypdf import PdfReader
from shapely.geometry import box

from render import approach_ifr, build_approaches as B, dcsdata, export, geo, page, procedure
from render.fonts import pt
from render.procedure import NM, Balise
from render.svgdoc import INK, INK2, Doc, circle, rect, text

S_MM, OX_MM, OY_MM = 0.0325, 74.0 + 282944.1 * 0.0325, 107.0 + 343087.3 * 0.0325
VOR = Balise('VOR/DME', 'KNY', '111.20', 345196.53125, -282509.65625, 'CH49X')
NDB = Balise('NDB', 'KNY', '390', 345109.625, -283155.28125)
TAC = Balise('TACAN', 'KON', '88X', 345432.4375, -282485.28125)
RWYS = [((341728.0, -283685.3), (344874.3, -282494.7), 57.2), ((341643.5, -283489.6), (344802.1, -282297.8), 47.8)]
NAMES = (('01L', '19R'), ('01R', '19L'))
ELEV = 3407


def konya():
    return next(a for a in dcsdata.load_terrain('Syria').airports.values() if a.name == 'Konya')


def page_xy(x, z):
    return OX_MM + z * S_MM, OY_MM - x * S_MM


def corners(p0, p1, w):
    u = np.subtract(p1, p0) / math.dist(p0, p1)
    n = np.array([-u[1], u[0]])
    a, b = np.array(p0), np.array(p1)
    return [tuple(a + n * w / 2), tuple(b + n * w / 2), tuple(b - n * w / 2), tuple(a - n * w / 2)]


def svg_10_9(designators=True):
    """La 10-9 synthetique de Konya : en-tete du moteur (tour, balises), places de pydcs, deux pistes, numeros, tableau."""
    ap = konya()
    proj = geo.Projection.for_theatre('Syria')
    lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
    decl = geo.declination(lat, lon, 1985.0)
    data = SimpleNamespace(airport_name='Konya', icao='LTAN', theatre='Syria', heli_mode=False, icao_known=True, decl=decl,
                           year=1985.0, elev_ft=ELEV, lat=lat, lon=lon,
                           tower={'vhf': '132.400', 'uhf': '225.000', 'fm': '38.900', 'hf': '3.800'},
                           beacons=[SimpleNamespace(kind='TACAN', callsign='KON', text='88X'),
                                    SimpleNamespace(kind='VOR/DME', callsign='KNY', text='111.20 CH49X'),
                                    SimpleNamespace(kind='NDB', callsign='KNY', text='390')])
    doc = Doc()
    doc.add('Background', rect(0, 0, 148, 210, '#FFFFFF'))
    page.header(doc, data, '10-9', 'AIRFIELD')
    doc.add_clip('clip-plan', 11, 38.1, 126, 138)
    for (p0, p1, w), names in zip(RWYS, NAMES):
        pts = ' '.join(f'{X:.3f},{Y:.3f}' for X, Y in (page_xy(*c) for c in corners(p0, p1, w)))
        doc.add('Runways', f'<polygon points="{pts}" fill="#1A1A1A"/>')
        if designators:
            for (a, b), name in (((p0, p1), names[0]), ((p1, p0), names[1])):
                q = procedure.offset(a, procedure.az_of(a, b), 75.0)
                X, Y = page_xy(*q)
                doc.add('Runway markings', f'<g transform="translate({X:.3f} {Y:.3f}) rotate(20.7)">'
                                           + text(0, 0.78, name, 2.2, 'bold', '#FFFFFF', 'middle') + '</g>')
    for s in ap.parking_slots:
        X, Y = page_xy(s.position.x, s.position.y)
        doc.add('Parking', circle(X, Y, 0.45, INK))
    y = 183.82
    for (p0, p1, w), names in zip(RWYS, NAMES):
        for (a, b), name in (((p0, p1), names[0]), ((p1, p0), names[1])):
            doc.add('Runway table', text(6, y, name, pt(7.2), 'bold'))
            doc.add('Runway table', text(20, y, f'{procedure.mag(procedure.az_of(a, b), decl):05.1f}°', pt(7.2), 'bold'))
            y += 3.46
    return doc.render()


def svg_ils():
    doc = Doc()
    doc.add('Header', text(74.4, 15.0, 'ILS 01L', pt(6.5), 'regular', INK2, 'middle', spacing=0.8))
    doc.add('Header', text(7.5, 27.4, 'ILS 01L', pt(7.6), 'bold'))
    page.pair_row(doc, 'Header', [('LOC', 'IKNY 108.90'), ('CRS', '017°'), ('THR', '3396 FT'), ('GP', '3.0°'), ('TCH', '60 FT')], 30.0, 27.4, 110)
    doc.add('Header', text(6.0, 21.2, 'ELEV 3407 FT', pt(7.2), 'bold'))
    doc.add('Runway table', text(6, 197.6, '3° glide path assumed, from the glide slope antenna (353 m past the threshold); terrain not shown.',
                                 pt(5.4), 'regular', INK2))
    return doc.render()


BEACONS_LUA = """beacons = {
\t{
\t\tdisplay_name = _('Konya');
\t\tbeaconId = 'airfield218_0';
\t\ttype = BEACON_TYPE_VOR_DME;
\t\tcallsign = 'KNY';
\t\tfrequency = 111200000.000000;
\t\tchannel = 49;
\t\tposition = { 345196.531250, 1000.0, -282509.656250 };
\t};
\t{
\t\tdisplay_name = _('Konya');
\t\tbeaconId = 'airfield218_1';
\t\ttype = BEACON_TYPE_HOMER;
\t\tcallsign = 'KNY';
\t\tfrequency = 390000.000000;
\t\tposition = { 345109.625000, 1000.0, -283155.281250 };
\t};
\t{
\t\tdisplay_name = _('Konya');
\t\tbeaconId = 'airfield218_2';
\t\ttype = BEACON_TYPE_TACAN;
\t\tcallsign = 'KON';
\t\tchannel = 88;
\t\tposition = { 345432.437500, 1000.0, -282485.281250 };
\t};
\t{
\t\tdisplay_name = _('Far');
\t\tbeaconId = 'world_1';
\t\ttype = BEACON_TYPE_VOR;
\t\tcallsign = 'FAR';
\t\tfrequency = 113000000.000000;
\t\tposition = { 300000.0, 0.0, -250000.0 };
\t};
}
"""


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    """Les entrees synthetiques : zips airfields et helicopters, beacons.lua, grille du relief (plate a 1 038 m)."""
    root = tmp_path_factory.mktemp('approches')
    pages = {'LTAN_Konya_10-9': svg_10_9(), 'LTAN_Konya_11-1': svg_ils()}
    files = {}
    for name, svg in pages.items():
        s, p, n = export.export_page(svg, root / 'base' / name)
        files[name] = (s.read_bytes(), p.read_bytes(), n.read_bytes())
    export.merge_pdfs([root / 'base' / 'LTAN_Konya_10-9.pdf', root / 'base' / 'LTAN_Konya_11-1.pdf'], root / 'base' / 'k.pdf')
    af = root / 'DCS_Airfield_Charts_Syria_1985_airfields.zip'
    d = 'DCS_Airfield_Charts_Syria_1985_airfields/'
    with zipfile.ZipFile(af, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('LTAN_Konya_10-9', 'LTAN_Konya_11-1'):
            s, p, n = files[name]
            z.writestr(f'{d}pdf/{name}.pdf', p)
            z.writestr(f'{d}png/{name}.png', n)
            z.writestr(f'{d}svg/{name}.svg', s)
        z.writestr(f'{d}pdf/LTAN_Konya_charts.pdf', (root / 'base' / 'k.pdf').read_bytes())
        z.writestr(f'{d}notes.txt', b'unchanged')
    he = root / 'DCS_Airfield_Charts_Syria_1985_helicopters.zip'
    h = 'DCS_Airfield_Charts_Syria_1985_helicopters/'
    with zipfile.ZipFile(he, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{h}Syria_heliports_directory.pdf', b'%PDF-directory')
        z.writestr(f'{h}heliports/p01.jpg', b'\xff\xd8jpeg')
    lua = root / 'beacons.lua'
    lua.write_text(BEACONS_LUA, encoding='utf-8')
    x0, z0 = 343000.0 - 60000.0, -283000.0 - 60000.0
    alt = np.full((1201, 1201), 1038.0, np.float32)
    alt[700:710, 700:710] = 1600.0
    npy = root / 'relief_Syria.npy'
    np.save(npy, alt)
    (root / 'relief_Syria.json').write_text(json.dumps({'theatre': 'Syria', 'cle': 'Syria', 'x0': x0, 'z0': z0, 'pas_m': 100.0,
                                                         'forme': [1201, 1201]}), encoding='utf-8')
    return SimpleNamespace(root=root, af=af, he=he, lua=lua, npy=npy, alt=alt, x0=x0, z0=z0, files=files, d=d, h=h)


def run(inp, out, capsys=None):
    out.mkdir(parents=True, exist_ok=True)
    return B.main(['Syria', '--zips', str(inp.af), str(inp.he), '--beacons', str(inp.lua), '--grille', str(inp.npy), '--out', str(out)])


@pytest.fixture(scope='module')
def produced(inputs, tmp_path_factory):
    outs = [tmp_path_factory.mktemp('out1'), tmp_path_factory.mktemp('out2')]
    codes = [run(inputs, o) for o in outs]
    return SimpleNamespace(codes=codes, outs=outs)


# ---------- lecture de la 10-9 ----------
def test_read_header_and_runways_of_the_10_9(inputs):
    svg = svg_10_9()
    hd = B.read_header(svg, 'LTAN_Konya')
    assert (hd.icao, hd.name, hd.elev_ft) == ('LTAN', 'KONYA', ELEV)
    assert not any('NAVAIDS' in ln for ln in hd.lines) and any('KONYA TOWER' in ln for ln in hd.lines)
    ap = konya()
    from render import plans
    root = plans.parse_svg(svg.encode())
    circles = plans.parking_circles(root)
    S, OX, OY, _, gap = plans.fit([(p.position.x, p.position.y) for p in ap.parking_slots], [(c[0], c[1]) for c in circles])
    assert gap <= 0.01 and S == pytest.approx(S_MM, rel=1e-5)
    rws = B.read_runways(svg, S, OX, OY, 'LTAN_Konya')
    for r, (p0, p1, w) in zip(rws, RWYS):
        assert math.dist(r.p0, p0) < 1.0 and math.dist(r.p1, p1) < 1.0 and r.width == pytest.approx(w, abs=0.2)
    ends = B.name_ends(rws, svg, S, OX, OY, 3.29, 'LTAN_Konya')
    assert sorted(e.name for e in ends) == ['01L', '01R', '19L', '19R']
    # sans les numeros dessines : le tableau (cap au dixieme) et l'ordre L / R
    svg2 = svg_10_9(designators=False)
    proj = geo.Projection.for_theatre('Syria')
    decl = geo.declination(*proj.to_latlon(ap.position.x, ap.position.y), 1985.0)
    ends2 = B.name_ends(rws, svg2, S, OX, OY, decl, 'LTAN_Konya')
    assert [(e.name, e.pos) for e in ends2] == [(e.name, e.pos) for e in ends]


def test_header_lines_change_only_the_title_and_page_number():
    hd = B.read_header(svg_10_9(), 'LTAN_Konya')
    lines = B.header_lines(hd, '11-1', 'ILS OR VOR/DME OR NDB RWY 01L')
    diff = [(a, b) for a, b in zip(hd.lines, lines) if a != b]
    assert len(lines) == len(hd.lines) and len(diff) == 2
    assert '>11-1<' in diff[0][1] or '>11-1<' in diff[1][1]
    heli = B.header_lines(hd, '11-1H', 'COPTER NDB RWY 01L', elev_m=True)
    row = [t[3] for t in B.texts('\n'.join(heli)) if abs(t[1] - 21.2) < 1e-6]
    assert row[0] == 'ELEV 3407 FT · 1038 M' and len(row) == 5
    xs = [t[0] for t in B.texts('\n'.join(heli)) if abs(t[1] - 21.2) < 1e-6]
    assert xs[0] == 6.0


def test_read_ils_page():
    p = B.read_ils_page(svg_ils(), 'LTAN_Konya_11-1')
    assert (p.sens, p.thr_ft, p.tch, p.s_gs, p.loc) == ('01L', 3396, 60, 353.0, 'IKNY 108.90')
    with pytest.raises(B.Arret):
        B.read_ils_page('<svg/>', 'X_11-1')


def test_reference_order_and_option_3():
    thr, az = RWYS[0][0], procedure.az_of(RWYS[0][0], RWYS[0][1])
    assert B.pick_reference([NDB, TAC, VOR], thr, az) is VOR
    assert B.pick_reference([NDB, TAC], thr, az) is NDB
    assert B.pick_reference([TAC], thr, az) is TAC
    assert B.option3_ndb([VOR, NDB, TAC], VOR) is NDB
    assert B.option3_ndb([VOR, NDB], TAC) is None
    far = Balise('NDB', 'FA', '300', VOR.x + 1500, VOR.z)
    assert B.option3_ndb([VOR, far], VOR) is None


def test_reference_in_front_on_the_axis_wins_at_equal_type():
    thr, end = RWYS[0][1], RWYS[0][0]                  # 19R
    az = procedure.az_of(thr, end)
    near_side = Balise('NDB', 'A', '300', thr[0] + 200.0, thr[1] + 900.0)        # plus proche, hors de l'axe
    inline = procedure.axis_point(thr, az, 0.6)
    front = Balise('NDB', 'B', '310', inline[0], inline[1])
    assert B.pick_reference([near_side, front], thr, az) is front


# ---------- etiquettes ----------
def test_placer_avoids_obstacles_and_other_labels():
    pl = approach_ifr.Placer((0, 0, 100, 100))
    pl.block(box(52, 40, 70, 60))                      # obstacle a droite du point
    lines = [('HOLD 7200', pt(6.2), 'bold', INK)]
    els = pl.place('a', lines, 50.0, 50.0, (1.0, 0.0))
    g = page.elems_geom([re.sub(r'<text[^>]*class="halo"[^>]*>.*?</text>', '', e) for e in els])
    assert not g.intersects(box(52, 40, 70, 60)) and pl.failed == []
    els2 = pl.place('b', lines, 50.0, 50.0, (1.0, 0.0))
    g2 = page.elems_geom([re.sub(r'<text[^>]*class="halo"[^>]*>.*?</text>', '', e) for e in els2])
    assert not g2.intersects(g)


def test_placer_records_a_label_without_room():
    pl = approach_ifr.Placer((0, 0, 20, 10))
    pl.block(box(0, 0, 20, 10))
    pl.place('x', [('LABEL', pt(6.0), 'bold', INK)], 10, 5)
    assert pl.failed == ['x']


# ---------- les pages ----------
def site_and_procs(inputs):
    rw = [corners(p0, p1, w) for p0, p1, w in RWYS]
    site = approach_ifr.Site(B.header_lines(B.read_header(svg_10_9(), 'LTAN_Konya'), '11-1', 'X'), ELEV, 1985.0,
                             list(RWYS), [VOR, NDB, TAC], inputs.alt, inputs.x0, inputs.z0)
    out = {}
    for sens, (thr, end) in (('01L', RWYS[0][:2]), ('19R', RWYS[0][1::-1])):
        az = procedure.az_of(thr, end)
        ils = {'s_gs': 353.0, 'tch': 60, 'loc': 'IKNY 108.90'} if sens == '01L' else None
        e = procedure.Entree(sens, thr, end, az, 57.2, rw, 3396 if ils else 3407, ELEV, 3.29, -3.97, VOR, NDB, ils)
        out[sens] = procedure.avion(e, inputs.alt, inputs.x0, inputs.z0)
        e = procedure.Entree(sens, thr, end, az, 57.2, rw, 3396 if ils else 3407, ELEV, 3.29, -3.97, NDB, None, ils)
        out[sens + 'H'] = procedure.helico(e, inputs.alt, inputs.x0, inputs.z0)
    return site, out


@pytest.mark.parametrize('key', ['01L', '19RH'])
def test_footer_layer_equals_the_republish_retouch_of_the_old_form(inputs, key):
    """Lot ac-marque-pied : le calque Footer d'une page 11-x (`approach_ifr`) est, a l'octet, celui qu'ecrit la retouche de l'ancienne forme."""
    from render import marque, republish
    site, procs = site_and_procs(inputs)
    po = approach_ifr.build_page(site, procs[key], '11-9H' if key.endswith('H') else '11-9', log=lambda *a: None)
    layer = lambda t: t[t.index('<g inkscape:groupmode="layer" id="layer-footer"'):t.rindex('</svg>')].rstrip('\n')
    old_layer = ('<g inkscape:groupmode="layer" id="layer-footer" inkscape:label="Footer">\n'
                 '<line x1="6" y1="201.2" x2="142" y2="201.2" stroke="#1A1A1A" stroke-width="0.5"/>\n'
                 f'<text x="6" y="203.7" font-family="Barlow Condensed" font-size="2.152" fill="#5A5A5A">{approach_ifr.FOOTER_TEXT}</text>\n'
                 '<text x="142" y="203.7" font-family="Barlow Condensed" font-size="2.152" fill="#1A1A1A" font-weight="bold" '
                 'text-anchor="end">NOT FOR REAL-WORLD NAVIGATION</text>\n</g>')
    assert old_layer in po.svg.replace('<text x="10.2"', '<text x="6"').replace(marque.block() + '\n', '')
    old_page = po.svg.replace(layer(po.svg), old_layer)
    got, state = republish.retouch_footer(old_page, 'X_11-9')
    assert state == 'retouchee' and got == po.svg                                      # la page entiere : seul le calque Footer differait
    assert republish.retouch_footer(po.svg, 'X_11-9') == (po.svg, 'deja')
    assert layer(po.svg).count(marque.block()) == 1 and '<text x="10.2" y="203.7"' in layer(po.svg)


@pytest.mark.parametrize('key', ['01L', '19R', '01LH', '19RH'])
def test_pages_have_no_overlapping_text_and_every_label_placed(inputs, key):
    site, procs = site_and_procs(inputs)
    P = procs[key]
    po = approach_ifr.build_page(site, P, '11-9H' if key.endswith('H') else '11-9', log=lambda *a: None)
    assert po.info['failed'] == []
    assert B.text_overlaps(po.svg) == []
    labels = re.findall(r'inkscape:label="([^"]+)"', po.svg)
    from render.svgdoc import LAYERS
    assert labels == [l.replace('&', '&amp;') for l in LAYERS if l.replace('&', '&amp;') in labels]
    t = ' '.join(B.texts(po.svg)[k][3] for k in range(len(B.texts(po.svg))))
    assert 'NO MINIMA — NOT PUBLISHED BY DCS' in t and 'TRANS ALT' not in t
    if key.endswith('H'):
        assert 'CAT H' in t and '80 KT · 150 KM/H' in t and 'ADF REQUIRED' in t and 'MSA 25 NM (46 KM) KNY' in t
    else:
        assert 'VOR/DME OR ADF REQUIRED' in t and f'FAF D{P.faf_dme:.1f}' in t
    if key == '01L':
        assert approach_ifr.title_avion(P) == 'ILS OR VOR/DME OR NDB RWY 01L' and 'ILS, VOR/DME OR ADF REQUIRED' in t
    if key == '19R':
        assert P.mode == 'racetrack' and 'RACETRACK (' in t


def test_titles():
    e = SimpleNamespace(ref=VOR, ndb=None, sens='09')
    assert approach_ifr.title_avion(SimpleNamespace(e=e, ils=False)) == 'VOR/DME RWY 09'
    e = SimpleNamespace(ref=TAC, ndb=None, sens='27')
    assert approach_ifr.title_avion(SimpleNamespace(e=e, ils=True)) == 'ILS OR TACAN RWY 27'
    assert approach_ifr.title_helico(SimpleNamespace(e=SimpleNamespace(sens='19R'))) == 'COPTER NDB RWY 19R'


# ---------- l'outil, de bout en bout ----------
def entries(path):
    with zipfile.ZipFile(path) as z:
        return [(i.filename, hashlib.sha256(z.read(i.filename)).hexdigest()) for i in z.infolist()]


def test_tool_writes_the_two_zips_and_two_passes_are_identical(inputs, produced):
    assert produced.codes == [0, 0]
    for name in (inputs.af.name, inputs.he.name):
        a, b = (o / name for o in produced.outs)
        assert a.read_bytes() == b.read_bytes()


def test_airfields_zip_replaces_the_ils_page_and_keeps_the_rest(inputs, produced):
    new = produced.outs[0] / inputs.af.name
    old_e, new_e = entries(inputs.af), entries(new)
    d = inputs.d
    removed = {f'{d}{s}/LTAN_Konya_11-1.{s}' for s in ('pdf', 'png', 'svg')}
    kept_old = [e for e in old_e if e[0] not in removed and e[0] != f'{d}pdf/LTAN_Konya_charts.pdf']
    kept_new = [e for e in new_e if e[0] in {k for k, _ in kept_old}]
    assert kept_new == kept_old                       # meme ordre, memes octets
    added = [n for n, _ in new_e if n not in {k for k, _ in old_e}]
    assert added == [f'{d}{s}/LTAN_Konya_11-2.{s}' for s in ('pdf', 'png', 'svg')]
    names = [n for n, _ in new_e]
    assert names.index(f'{d}svg/LTAN_Konya_11-2.svg') < names.index(f'{d}pdf/LTAN_Konya_charts.pdf')
    with zipfile.ZipFile(new) as z:
        svg1 = z.read(f'{d}svg/LTAN_Konya_11-1.svg').decode()
        assert 'ILS OR VOR/DME OR NDB RWY 01L' in svg1 and svg1 != inputs.files['LTAN_Konya_11-1'][0].decode()
        rec = PdfReader(io.BytesIO(z.read(f'{d}pdf/LTAN_Konya_charts.pdf')))
        assert len(rec.pages) == 2 - 1 + 4             # 10-9, 11-1, 11-2, 11-1H, 11-2H
        assert '/CreationDate' not in (rec.metadata or {})
        info = z.getinfo(f'{d}svg/LTAN_Konya_11-2.svg')
        assert info.date_time == z.getinfo(f'{d}pdf/LTAN_Konya_charts.pdf').date_time


def test_helicopters_zip_gets_the_11_xh_pages_under_its_root(inputs, produced):
    new = produced.outs[0] / inputs.he.name
    old_e, new_e = entries(inputs.he), entries(new)
    assert new_e[:len(old_e)] == old_e
    h = inputs.h
    assert [n for n, _ in new_e[len(old_e):]] == [f'{h}{s}/LTAN_Konya_11-{k}H.{s}' for k in (1, 2) for s in ('pdf', 'png', 'svg')]
    with zipfile.ZipFile(new) as z:
        svg = z.read(f'{h}svg/LTAN_Konya_11-1H.svg').decode()
        assert 'COPTER NDB RWY 01L' in svg and 'ELEV 3407 FT · 1038 M' in svg
        assert '/CreationDate' not in (PdfReader(io.BytesIO(z.read(f'{h}pdf/LTAN_Konya_11-1H.pdf'))).metadata or {})


def test_tool_stops_when_an_output_exists(inputs, produced, capsys):
    assert run(inputs, produced.outs[0]) == 2
    assert 'deja present' in capsys.readouterr().err


def test_tool_usage_errors(inputs, tmp_path, capsys):
    assert B.main(['Caucasus', '--zips', str(inputs.af), str(inputs.he), '--beacons', str(inputs.lua), '--grille', str(inputs.npy),
                   '--out', str(tmp_path)]) == 1
    assert B.main(['Syria', '--zips', str(inputs.he), str(inputs.af), '--beacons', str(inputs.lua), '--grille', str(inputs.npy),
                   '--out', str(tmp_path)]) == 1
    assert B.main(['Syria']) == 1
    assert list(tmp_path.iterdir()) == []


def test_plan_journal_and_counts(inputs):
    lines = []
    pl = B.plan_theatre('Syria', inputs.af, inputs.lua, inputs.alt, inputs.x0, inputs.z0, log=lines.append)
    c = B.counts(pl)
    assert (c['charted'], c['case_a'], c['pages_a'], c['pages_h'], c['ecarts'], c['ils_removed']) == (1, 1, 2, 2, 0, 1)
    j = [ln for ln in lines if ln.startswith('approche LTAN_Konya 11-')]
    assert len(j) == 4 and all('traversées' in ln for ln in j)
    assert any(ln.startswith('approche LTAN_Konya 11-1 01L : virage de base, réf VOR/DME KNY') for ln in j)
    assert any(ln.startswith('approche LTAN_Konya 11-2 19R : hippodrome, réf VOR/DME KNY') for ln in j)
    assert 'LTAN_Konya\tKonya\tA' in B.terrain_list(pl)


# ---------- echantillons : la vraie 10-9 du moteur et le vrai beacons.lua ----------
def test_samples_konya_10_9_and_beacons(charts, samples):
    from render import plans
    data, pages = charts['Konya']
    svg = next(p.svg for p in pages if p.id == '10-9')
    ap = konya()
    root = plans.parse_svg(svg.encode())
    circles = plans.parking_circles(root)
    S, OX, OY, _, gap = plans.fit([(p.position.x, p.position.y) for p in ap.parking_slots], [(c[0], c[1]) for c in circles])
    assert gap <= 0.01
    rws = B.read_runways(svg, S, OX, OY, 'Konya')
    ends = {e.name: e for e in B.name_ends(rws, svg, S, OX, OY, data.decl, 'Konya')}
    for e in data.ends:
        assert math.dist(ends[e.name].pos, e.pos) < 1.0
    hd = B.read_header(svg, 'Konya')
    assert hd.elev_ft == data.elev_ft
    beacons = B.usable_beacons(samples / 'dcs_install' / 'Mods' / 'terrains' / 'Syria' / 'beacons.lua')
    near = [b for b in beacons if math.dist(b.pos, (ap.position.x, ap.position.y)) <= B.CASE_A_M]
    assert sorted((b.kind, b.callsign) for b in near) == [('NDB', 'KNY'), ('TACAN', 'KON'), ('VOR/DME', 'KNY')]
    e = ends['01L']
    ref = B.pick_reference(near, e.pos, e.az)
    assert (ref.kind, ref.callsign, ref.channel) == ('VOR/DME', 'KNY', 'CH49X')
    assert B.option3_ndb(near, ref).callsign == 'KNY'


# ---------- addendum 2 ----------
def test_runway_problems_and_theatre_stops():
    e = SimpleNamespace(sens='09')
    ok = SimpleNamespace(e=e, strict_parts={'virage de base': 120.0, 'intermédiaire': 900.0}, traversals=[('attente', 0, 1000.0)])
    assert B.runway_problems('X', '11-1', ok) == []
    low = SimpleNamespace(e=e, strict_parts={'virage de base': 120.0}, traversals=[('attente', 0, 999.0)])
    cut = SimpleNamespace(e=e, strict_parts={'virage de base': 0.0}, traversals=[])
    assert len(B.runway_problems('X', '11-1', low)) == 1 and 'virage de base' in B.runway_problems('X', '11-2', cut)[0]
    with pytest.raises(B.Arret, match='traversée'):
        B.check_plan(B.runway_problems('X', '11-1', low), {'sens': 10, 'tried_h': 0, 'ecarts': 0, 'ecarts_h': 0})
    B.check_plan([], {'sens': 10, 'tried_h': 10, 'ecarts': 1, 'ecarts_h': 1})             # 2 sur 20 : 10 %, pas plus
    with pytest.raises(B.Arret, match='plus de 10 %'):
        B.check_plan([], {'sens': 10, 'tried_h': 10, 'ecarts': 1, 'ecarts_h': 2})         # les deux familles comptent


def test_faf_beyond_the_limit_drops_the_direction_and_keeps_the_numbering(inputs, monkeypatch):
    pl0 = B.plan_theatre('Syria', inputs.af, inputs.lua, inputs.alt, inputs.x0, inputs.z0, log=lambda *a: None, check=False)
    faf = {s.end.name: s.avion.faf_nm for t in pl0.terrains for s in t.sens}
    assert faf['01L'] < faf['19R']
    limit = round((faf['01L'] + faf['19R']) / 2, 2)
    monkeypatch.setattr(procedure, 'FAF_MAX_NM', limit)                # 19R est ecarte, 01L reste
    lines = []
    pl = B.plan_theatre('Syria', inputs.af, inputs.lua, inputs.alt, inputs.x0, inputs.z0, log=lines.append, check=False)
    assert (f"approche LTAN_Konya 19R : écartée, FAF à {faf['19R']:.1f} NM du seuil (relief dans l'axe), au-delà de {limit:g} NM"
            in lines)
    (t,) = [t for t in pl.terrains if t.case_a]
    assert [(s.end.name, s.index, s.index_h) for s in t.sens] == [('01L', '11-1', '11-1H'), ('19R', '', '11-2H')]
    with pytest.raises(B.Arret, match='1 sens écartés sur 4 pages tentées'):
        B.check_plan(pl.log, B.counts(pl))


def test_too_many_dropped_directions_stop_and_write_nothing(inputs, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(procedure, 'FAF_MAX_NM', 1.0)
    assert run(inputs, tmp_path / 'o') == 2
    assert 'plus de 10 %' in capsys.readouterr().err and list((tmp_path / 'o').iterdir()) == []


def test_airfield_without_aircraft_page_keeps_its_helicopter_pages(inputs, monkeypatch, tmp_path):
    monkeypatch.setattr(procedure, 'FAF_MAX_NM', 1.0)                  # les deux sens avion sont ecartes
    monkeypatch.setattr(B, 'check_plan', lambda crossing, c: None)
    out = tmp_path / 'o'
    assert run(inputs, out) == 0
    d, h = inputs.d, inputs.h
    with zipfile.ZipFile(out / inputs.af.name) as z:
        names = z.namelist()
        assert not any('_11-' in n for n in names)                    # la page ILS de la base est retiree, aucune page avion
        assert len(PdfReader(io.BytesIO(z.read(f'{d}pdf/LTAN_Konya_charts.pdf'))).pages) == 1 + 2      # 10-9, 11-1H, 11-2H
    with zipfile.ZipFile(out / inputs.he.name) as z:
        assert f'{h}svg/LTAN_Konya_11-1H.svg' in z.namelist() and f'{h}svg/LTAN_Konya_11-2H.svg' in z.namelist()


def test_unexpected_error_exits_2_with_one_line(inputs, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(B, 'plan_theatre', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom\non two lines')))
    assert run(inputs, tmp_path / 'o') == 2
    err = capsys.readouterr().err
    assert err == 'ARRET : RuntimeError : boom on two lines\n'


def test_second_rename_failure_leaves_nothing(inputs, monkeypatch, tmp_path):
    from pathlib import Path as _P
    real = _P.replace
    calls = []

    def flaky(self, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError('disk full')
        return real(self, target)
    monkeypatch.setattr(_P, 'replace', flaky)
    out = tmp_path / 'o'
    assert run(inputs, out) == 2
    assert list(out.iterdir()) == []


def terrain_with(ends, beacons, ils=()):
    return B.Terrain('T', '', None, 'T', (0.0, 0.0), 0.0, [], ends, beacons, {n: object() for n in ils}, [])


def test_choose_sens_parallel_runways():
    # 01L et 01R paralleles (300 m), au nord ; la balise est dans l'axe de 01R, 1 km avant son seuil
    l = B.End('01L', (0.0, -300.0), (3000.0, -300.0), 0.0, 0)
    r = B.End('01R', (0.0, 0.0), (3000.0, 0.0), 0.0, 1)
    l19 = B.End('19R', (3000.0, -300.0), (0.0, -300.0), 180.0, 0)
    r19 = B.End('19L', (3000.0, 0.0), (0.0, 0.0), 180.0, 1)
    ndb = Balise('NDB', 'N', '400', -1000.0, 0.0)
    sens = B.choose_sens(terrain_with([l, r, l19, r19], [ndb]))
    assert [(s.number, s.end.name) for s in sens] == [('01', '01R'), ('19', '19L')]       # seuil le plus proche de la balise
    sens = B.choose_sens(terrain_with([l, r, l19, r19], [ndb], ils=('01L',)))
    assert [(s.number, s.end.name) for s in sens] == [('01', '01L'), ('19', '19L')]       # la piste qui a une page ILS d'abord
