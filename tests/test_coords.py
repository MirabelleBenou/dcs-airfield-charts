"""Formats DMS et MGRS, ligne d'en-tete a cinq champs, republication des charts publiees
(render/update_coords.py, sur un petit zip synthetique)."""
import html
import io
import re
import zipfile
from types import SimpleNamespace

import pytest

from render import dcsdata, export, fiches, geo, page, svgdoc, update_coords
from render.fonts import pt, text_width

MGRS_CAS = [
    (52.38827901650516, 11.826442332722703, "32U PD 9233 0798"),  # Mahlwinkel (ARP pydcs)
    (37.9783, 32.5617, "36S VH 6150 0349"),
    (0.5, -78.5, "17N QA 7826 5531"),
    (-51.8227, -58.4472, "21F VC 0025 5769"),
    (60.39, 5.32, "32V KN 9723 0051"),        # exception de Norvege
    (78.22, 15.65, "33X WG 1481 8300"),       # Svalbard
    (79.0, 8.0, "31X FH 0638 7453"),          # Svalbard
    (36.236, -115.034, "11S PA 7667 1191"),
    (48.0, 6.0001, "32U KU 7623 2065"),       # juste apres une limite de zone
    (-33.86, 151.21, "56H LH 3441 5192"),
]
TEXT = re.compile(r'<text\b([^>]*)>(.*?)</text>', re.S)


def texts_at(svg, y):
    return [html.unescape(m.group(2)) for m in TEXT.finditer(svg) if f' y="{y}"' in m.group(1)]


def x_of(element):
    return float(re.search(r' x="([^"]*)"', element).group(1))


# ---------- geo.dms ----------
def test_dms_rounds_to_the_second():
    assert geo.dms(52.38827901650516, 'N', 'S', 2) == "N52°23'18\""
    assert geo.dms(11.826442332722703, 'E', 'W', 3) == "E011°49'35\""
    assert geo.dms(37.97775, 'N', 'S', 2) == "N37°58'40\""


def test_dms_carries_seconds_into_minutes_and_degrees():
    assert geo.dms(10 + 22 / 60 + 59.6 / 3600, 'N', 'S', 2) == "N10°23'00\""       # 59,6" -> minute suivante
    assert geo.dms(10 + 59 / 60 + 59.6 / 3600, 'N', 'S', 2) == "N11°00'00\""       # 59'59,6" -> degre suivant
    assert geo.dms(32 + 59 / 60 + 59.6 / 3600, 'E', 'W', 3) == "E033°00'00\""
    assert geo.dms(49 + 22 / 60 + 59.4 / 3600, 'N', 'S', 2) == "N49°22'59\""       # 59,4" : pas de retenue


def test_dms_hemispheres_and_widths():
    assert geo.dms(-51.8227, 'N', 'S', 2) == "S51°49'22\""
    assert geo.dms(-57.7667, 'E', 'W', 3) == "W057°46'00\""
    assert geo.dms(5.0, 'E', 'W', 3) == "E005°00'00\""
    assert geo.dms(5.0, 'N', 'S', 2) == "N05°00'00\""
    assert geo.dms(0.0, 'E', 'W', 3) == "E000°00'00\""
    s = geo.dms(-0.5, 'E', 'W', 3)
    assert s == "W000°30'00\"" and "'" in s and s.endswith('"')


# ---------- geo.mgrs ----------
@pytest.mark.parametrize('lat, lon, expected', MGRS_CAS)
def test_mgrs_reference_values(lat, lon, expected):
    assert geo.mgrs(lat, lon) == expected


@pytest.mark.parametrize('lat', [84.0, 85.0, -80.5, -90.0, 90.0])
def test_mgrs_out_of_bounds(lat):
    with pytest.raises(ValueError):
        geo.mgrs(lat, 10.0)


def test_mgrs_band_limits():
    assert geo.mgrs(83.99, 10.0).split()[0].endswith('X')
    assert geo.mgrs(-80.0, 10.0).split()[0].endswith('C')


# ---------- page.header_row ----------
def row_args(lat=52.38827901650516, lon=11.826442332722703):
    return 'ÉLÉV. 159 FT', lat, lon, 'VAR 0°W (1985)'


def test_header_row_order_and_content():
    els = page.header_row(*row_args())
    assert [html.unescape(TEXT.fullmatch(e).group(2)) for e in els] == [
        'ÉLÉV. 159 FT', "ARP N52°23.3' E011°49.6'", "N52°23'18\" E011°49'35\"", 'MGRS 32U PD 9233 0798', 'VAR 0°W (1985)']
    for e in els:
        assert ' y="21.2"' in e and 'font-weight="bold"' in e and f'font-size="{svgdoc.f(pt(7.2))}"' in e
        assert 'text-anchor' not in e


def test_header_row_equal_gaps_and_ends_at_x1():
    els = page.header_row(*row_args())
    strings = [html.unescape(TEXT.fullmatch(e).group(2)) for e in els]
    xs = [x_of(e) for e in els]
    ws = [text_width(s, 'bold', pt(7.2)) for s in strings]
    assert xs[0] == pytest.approx(page.X0, abs=0.01)
    gaps = [xs[i + 1] - (xs[i] + ws[i]) for i in range(4)]
    assert max(gaps) - min(gaps) < 0.01 and min(gaps) >= 4.0
    assert xs[-1] + ws[-1] == pytest.approx(page.X1, abs=0.01)


def test_header_row_too_wide_raises():
    with pytest.raises(ValueError):
        page.header_row('ÉLÉV. ' + '8' * 40 + ' FT', *row_args()[1:])


def test_header_row_gap_limit_is_four_mm(monkeypatch):
    base = sum(text_width(s, 'bold', pt(7.2)) for s in ['ÉLÉV. 159 FT', "ARP N52°23.3' E011°49.6'",
                                                         "N52°23'18\" E011°49'35\"", 'MGRS 32U PD 9233 0798', 'VAR 0°W (1985)'])
    monkeypatch.setattr(page, 'X1', page.X0 + base + 4 * 4.01)
    page.header_row(*row_args())
    monkeypatch.setattr(page, 'X1', page.X0 + base + 4 * 3.99)
    with pytest.raises(ValueError):
        page.header_row(*row_args())


def header_data(**kw):
    d = dict(theatre='Syria', airport_name='Konya', icao='LTAN', heli_mode=False, icao_known=True, decl=3.3, year=1985.0,
             elev_ft=3392, lat=37.97775, lon=32.56149, tower={}, beacons=[])
    d.update(kw)
    return SimpleNamespace(**d)


def test_page_header_writes_the_five_fields():
    doc = svgdoc.Doc()
    page.header(doc, header_data(), '10-9', 'AÉRODROME', bands=False)
    got = texts_at(doc.render(), '21.2')
    assert len(got) == 5
    assert got[:3] == ['ÉLÉV. 3392 FT', "ARP N37°58.7' E032°33.7'", "N37°58'40\" E032°33'41\""]
    assert got[3] == 'MGRS ' + geo.mgrs(37.97775, 32.56149) and got[3].startswith('MGRS 36S ') and got[4] == 'VAR 3°E (1985)'


def test_page_header_and_update_coords_share_header_row(monkeypatch):
    """K7 : les deux passent par `page.header_row`."""
    calls = []
    marker = ['<text x="0" y="21.2">A</text>'] * 5

    def fake(*args):
        calls.append(args)
        return marker
    monkeypatch.setattr(page, 'header_row', fake)
    doc = svgdoc.Doc()
    page.header(doc, header_data(), '10-9', 'AÉRODROME', bands=False)
    assert len(calls) == 1 and calls[0][0] == 'ÉLÉV. 3392 FT'
    svg = svg_page("ARP N37°58.7' E032°33.7'")
    new, _ = update_coords.rewrite_page(svg, 'Konya_10-9', update_coords.arp_index('Syria'))
    assert len(calls) == 2 and texts_at(new, '21.2') == ['A'] * 5


# ---------- update_coords ----------
def svg_page(arp, elev='ÉLÉV. 3392 FT', var='VAR 3°E (1985)', body='<rect x="1" y="1" width="2" height="2"/>'):
    t = lambda x, s: svgdoc.text(x, 21.2, s, pt(7.2), 'bold')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="148mm" height="210mm" '
            'viewBox="0 0 148 210">\n<g>\n' + body + '\n' + t(6, elev) + '\n' + t(51.3, arp) + '\n' + t(96.7, var) + '\n'
            '<line x1="6" y1="22.8" x2="142" y2="22.8" stroke="#1A1A1A" stroke-width="0.25"/>\n</g>\n</svg>')


def konya_arp():
    proj = geo.Projection.for_theatre('Syria')
    ap = next(a for a in dcsdata.load_terrain('Syria').airports.values() if a.name == 'Konya')
    return geo.arp_text(*proj.to_latlon(ap.position.x, ap.position.y))


@pytest.fixture(scope='module')
def old_zip(tmp_path_factory):
    """Zip synthetique : deux pages d'un chart (10-9 et 10-9A, ARP de Konya), leur recueil, et une entree d'un autre type."""
    root = tmp_path_factory.mktemp('coords')
    pages = {'Konya_10-9': svg_page(konya_arp()), 'Konya_10-9A': svg_page(konya_arp(), body='<circle cx="9" cy="9" r="3"/>')}
    files = {}
    for name, svg in pages.items():
        s, p, n = export.export_page(svg, root / 'old' / name)
        files[name] = (s.read_bytes(), p.read_bytes(), n.read_bytes())
    export.merge_pdfs([root / 'old' / f'{n}.pdf' for n in pages], root / 'old' / 'Konya_charts.pdf')
    path = root / 'release.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('pack/heliports/p01.jpg', b'\xff\xd8jpeg-bytes\x00\x01')
        for name, (s, p, n) in files.items():
            z.writestr(f'pack/png/{name}.png', n)
            z.writestr(f'pack/svg/{name}.svg', s)
            z.writestr(f'pack/pdf/{name}.pdf', p)
        z.writestr('pack/pdf/Konya_charts.pdf', (root / 'old' / 'Konya_charts.pdf').read_bytes(), zipfile.ZIP_STORED)
        z.writestr('pack/notes.txt', b'unchanged')
    return path, pages


def test_update_coords_rewrites_pages_and_keeps_the_rest(old_zip, tmp_path, capsys):
    zin, pages = old_zip
    out = tmp_path / 'new.zip'
    assert update_coords.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 0
    summary = capsys.readouterr().out
    assert 'pages mises a jour : 2' in summary and 'recueils refaits : 1' in summary
    assert 'entrees recopiees telles quelles : 2' in summary
    assert "Konya_10-9 : ÉLÉV. 3392 FT | " + konya_arp() in summary and ' | MGRS 36S ' in summary
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        assert [i.compress_type for i in a.infolist()] == [i.compress_type for i in b.infolist()]
        for n in ('pack/heliports/p01.jpg', 'pack/notes.txt'):
            assert a.read(n) == b.read(n)
        for name, old in pages.items():
            new = b.read(f'pack/svg/{name}.svg').decode('utf-8')
            fields = texts_at(new, '21.2')
            assert len(fields) == 5 and fields[0] == 'ÉLÉV. 3392 FT' and fields[1] == konya_arp() and fields[4] == 'VAR 3°E (1985)'
            assert fields[3].startswith('MGRS 36S ')
            # hors de la ligne, le SVG est identique a l'octet
            head, tail = old.index('<text x="6" y="21.2"'), old.index('<line x1="6" y1="22.8"')
            assert new[:head] == old[:head] and new[new.index('<line x1="6" y1="22.8"'):] == old[tail:]
            assert b.read(f'pack/png/{name}.png') != a.read(f'pack/png/{name}.png')
            assert b.read(f'pack/pdf/{name}.pdf').startswith(b'%PDF')
        from pypdf import PdfReader
        assert len(PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf'))).pages) == 2


def test_update_coords_png_differs_only_in_the_header_rows(old_zip, tmp_path):
    import numpy as np
    from PIL import Image
    zin, _ = old_zip
    out = tmp_path / 'new.zip'
    assert update_coords.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 0
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        po = np.asarray(Image.open(io.BytesIO(a.read('pack/png/Konya_10-9.png'))).convert('RGBA'))
        pn = np.asarray(Image.open(io.BytesIO(b.read('pack/png/Konya_10-9.png'))).convert('RGBA'))
    rows = np.nonzero(np.any(po != pn, axis=(1, 2)))[0]
    assert len(rows) and rows[0] >= 190 and rows[-1] <= 279


def test_update_coords_refuses_to_overwrite(old_zip, tmp_path, capsys):
    zin, _ = old_zip
    out = tmp_path / 'exists.zip'
    out.write_bytes(b'x')
    assert update_coords.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 1
    assert 'existe deja' in capsys.readouterr().err and out.read_bytes() == b'x'


def test_update_coords_page_without_three_texts_stops(tmp_path):
    index = update_coords.arp_index('Syria')
    svg = svg_page(konya_arp()).replace('<text x="96.7" y="21.2"', '<text x="96.7" y="21.3"')
    with pytest.raises(update_coords.UpdateError, match='Konya_10-9'):
        update_coords.rewrite_page(svg, 'Konya_10-9', index)


def test_update_coords_arp_must_designate_one_airfield():
    index = update_coords.arp_index('Syria')
    with pytest.raises(update_coords.UpdateError, match='aucun'):
        update_coords.rewrite_page(svg_page("ARP N10°00.0' E010°00.0'"), 'Nowhere_10-9', index)
    twice = {konya_arp(): [('A', 37.9, 32.5), ('B', 37.9, 32.5)]}
    with pytest.raises(update_coords.UpdateError, match='2 aerodromes'):
        update_coords.rewrite_page(svg_page(konya_arp()), 'Konya_10-9', twice)


def test_update_coords_gap_too_small_stops():
    index = update_coords.arp_index('Syria')
    with pytest.raises(update_coords.UpdateError, match='Konya_10-9'):
        update_coords.rewrite_page(svg_page(konya_arp(), elev='ÉLÉV. ' + '8' * 40 + ' FT'), 'Konya_10-9', index)


def test_update_coords_second_text_must_be_the_arp():
    index = update_coords.arp_index('Syria')
    with pytest.raises(update_coords.UpdateError, match='ARP'):
        update_coords.rewrite_page(svg_page('RWY 01/19'), 'Konya_10-9', index)


def test_fiches_reads_a_single_arp_on_an_updated_page():
    index = update_coords.arp_index('Syria')
    new, _ = update_coords.rewrite_page(svg_page(konya_arp()), 'Konya_10-9', index)
    lat, lon = fiches.read_arp(new)
    assert (lat, lon) == pytest.approx((37.97775, 32.56149), abs=1e-3)        # ARP lu en minutes decimales : a 0,1' pres
