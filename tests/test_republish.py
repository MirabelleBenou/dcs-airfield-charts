"""Lot echelle-heliports-fond : fin de la barre d'echelle, pages sur fond F10 (PNG a 256 couleurs, /Interpolate, xlink:href),
outil de republication render/republish.py (sur un petit zip synthetique)."""
import base64
import html
import io
import re
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from render import analysis, compose, dcsdata, export, geo, marque, page, republish, svgdoc, update_coords
from render.fonts import pt

BAR_Y = 172.4
BAR_X = 94.422


def labels(elems):
    return [html.unescape(re.sub(r'<[^>]+>', '', e)) for e in elems if e.startswith('<text')]


def m_labels(elems):
    """Etiquettes de metres : celles sous la barre (y = y + 4.2)."""
    return [html.unescape(re.sub(r'<[^>]+>', '', e)) for e in elems if e.startswith('<text') and f'y="{svgdoc.f(BAR_Y + 4.2)}"' in e]


def block(L, x=BAR_X, y=BAR_Y):
    s = 38.0 / (L + 50)                     # 38 mm couvrent un peu plus de L m : la barre fait L m
    elems, Lmm = page.scale_block(x, y, s)
    return elems, s


# ---------- scale_block : la fin de la barre ----------
@pytest.mark.parametrize('L, expect', [(750, ['0', '500', '750 M']), (1250, ['0', '500', '1000', '1250 M']),
                                       (1750, ['0', '500', '1000', '1500', '1750 M']), (1000, ['0', '500', '1000 M']),
                                       (500, ['0', '500 M']), (1500, ['0', '500', '1000', '1500 M'])])
def test_scale_block_end_label(L, expect):
    elems, s = block(L)
    assert page.scale_plan(s)[0] == L
    assert m_labels(elems) == expect


@pytest.mark.parametrize('L', [750, 1250, 1750])
def test_scale_block_end_label_follows_the_end_tick_line(L):
    elems, s = block(L)
    k = next(i for i, e in enumerate(elems) if e.startswith('<text') and e.endswith(f'>{L} M</text>'))
    xe = BAR_X + L * s
    prev = elems[k - 1]
    assert prev.startswith('<line') and f'x1="{svgdoc.f(xe)}" y1="{svgdoc.f(BAR_Y)}"' in prev and 'stroke-width="0.3"' in prev
    assert elems[k] == svgdoc.text(xe, BAR_Y + 4.2, f'{L} M', pt(5.3), 'regular', svgdoc.INK, 'middle')
    assert elems[k] == page.scale_end_label(xe, BAR_Y, L)


def test_scale_block_1000_m_and_short_bars_are_unchanged():
    elems, s = block(1000)
    assert labels(elems) == ['0', '500', '1000 M', '0', '1000', '2000', '3000 FT']
    for L, expect in ((300, ['0', '100', '200', '300 M']), (150, ['0', '50', '100', '150 M'])):
        elems, _ = page.scale_block(0.0, 0.0, 38.0 / L)
        got = labels(elems)
        assert got[:len(expect)] == expect


# ---------- la retouche egale a ce que scale_block ecrit ----------
FOOT_TEXT = 'DCS AIRFIELD CHARTS · data: F10 map and DCS World files (Eagle Dynamics), via pydcs'
NEW_FOOT_TEXT = 'DCS AIRFIELD CHARTS · data: F10 map and DCS World files (Eagle Dynamics)'      # le moteur, sans pydcs


def foot_layer(new=False, left_x=None, extra=''):
    """Le calque `Footer` d'une page publiee : l'ancienne forme (texte de gauche a x = 6) ou la neuve (x = 10.2, puis le bloc du pictogramme)."""
    x = left_x or ('10.2' if new else '6')
    parts = ['<line x1="6" y1="201.2" x2="142" y2="201.2" stroke="#1A1A1A" stroke-width="0.5"/>',
             f'<text x="{x}" y="203.7" font-family="Barlow Condensed" font-size="2.152" fill="#5A5A5A">{FOOT_TEXT}</text>',
             '<text x="142" y="203.7" font-family="Barlow Condensed" font-size="2.152" fill="#1A1A1A" font-weight="bold" '
             'text-anchor="end">NOT FOR REAL-WORLD NAVIGATION</text>']
    if new:
        parts.append(marque.block())
    return '<g inkscape:groupmode="layer" id="layer-footer" inkscape:label="Footer">\n' + '\n'.join(parts) + extra + '\n</g>'


def doc_svg(*parts, foot=None):
    """Page synthetique ; `foot` : le calque Footer (par defaut, celui de l'ancienne forme ; `False` : aucun)."""
    foot = foot_layer() if foot is None else foot
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="148mm" height="210mm" viewBox="0 0 148 210">\n<g>\n'
            + '\n'.join(parts) + '\n</g>\n' + (foot + '\n' if foot else '') + '</svg>')


def old_bar_elements(elems, L, y=BAR_Y):
    """Les elements de `scale_block` sans l'etiquette de fin : la barre telle que publiee en AC-V1.5.0."""
    out = [e for e in elems if not (e.startswith('<text') and e.endswith(f'>{L} M</text>') and f'y="{svgdoc.f(y + 4.2)}"' in e)]
    assert len(out) == len(elems) - 1
    return out


@pytest.mark.parametrize('L', [750, 1250, 1750])
@pytest.mark.parametrize('x, y', [(BAR_X, BAR_Y), (7.123456, 150.0), (60.0004, 183.1234)])
def test_retouch_equals_scale_block_to_the_byte(L, x, y):
    s = 38.0 / (L + 50)
    elems, _ = page.scale_block(x, y, s)
    old = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *old_bar_elements(elems, L, y), '<circle cx="9" cy="9" r="3"/>')
    want = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *elems, '<circle cx="9" cy="9" r="3"/>')
    got, info = republish.complete_scale_bar(old, 'X_10-9', '10-9')
    assert got == want
    assert info[0] == f'{L} M'


def test_retouch_leaves_a_bar_with_its_m_unchanged():
    for L in (300, 500, 1000, 1500):
        elems, _ = page.scale_block(BAR_X, BAR_Y, 38.0 / (L + 1))
        svg = doc_svg(*elems)
        assert republish.complete_scale_bar(svg, 'X_10-9', '10-9') == (svg, None)


def test_retouch_stops_on_odd_pages():
    elems, _ = block(750)
    old = old_bar_elements(elems, 750)
    # 10-9 sans barre / avec deux barres
    with pytest.raises(republish.RepublishError, match='0 barres'):
        republish.complete_scale_bar(doc_svg('<rect x="1" y="1" width="2" height="2"/>'), 'X_10-9', '10-9')
    with pytest.raises(republish.RepublishError, match='2 barres'):
        republish.complete_scale_bar(doc_svg(*old, old[0]), 'X_10-9', '10-9')
    # longueur hors des multiples de 250 m : « 500 » deplace
    x500 = svgdoc.f(BAR_X + 500 * 38.0 / 800)
    bad = [e.replace(f'x="{x500}"', 'x="125.5"') if e.startswith('<text') else e for e in old]
    assert bad != old
    with pytest.raises(republish.RepublishError, match='multiple de 250'):
        republish.complete_scale_bar(doc_svg(*bad), 'X_10-9', '10-9')
    # graduation de fin introuvable
    no_tick = [e for e in old if not (e.startswith('<line') and f'x1="{svgdoc.f(BAR_X + 750 * 38.0 / 800)}"' in e and 'y2="173.4"' in e)]
    assert len(no_tick) == len(old) - 1
    with pytest.raises(republish.RepublishError, match='graduations de fin'):
        republish.complete_scale_bar(doc_svg(*no_tick), 'X_10-9', '10-9')
    # etiquettes 0 / 500 absentes
    no_zero = [e for e in old if not (e.startswith('<text') and e.endswith('>0</text>') and f'y="{svgdoc.f(BAR_Y + 4.2)}"' in e)]
    with pytest.raises(republish.RepublishError, match='introuvables'):
        republish.complete_scale_bar(doc_svg(*no_zero), 'X_10-9', '10-9')


def test_parkings_page_without_a_bar_passes_and_with_a_bar_stops():
    svg = doc_svg('<rect x="1" y="1" width="2" height="2"/>')
    assert republish.complete_scale_bar(svg, 'X_10-9A', '10-9A') == (svg, None)
    assert republish.complete_scale_bar(svg, 'X_10-9B', '10-9B') == (svg, None)
    elems, _ = block(750)
    with pytest.raises(republish.RepublishError, match='parkings'):
        republish.complete_scale_bar(doc_svg(*old_bar_elements(elems, 750)), 'X_10-9A', '10-9A')
    with pytest.raises(republish.RepublishError, match='parkings'):
        republish.complete_scale_bar(doc_svg(*elems), 'X_10-9B', '10-9B')


# ---------- xlink:href ----------
def tiny_png():
    buf = io.BytesIO()
    Image.effect_noise((64, 80), 60).convert('RGB').save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def test_add_xlink_href():
    href = tiny_png()
    el = f'<image x="1" y="2" width="3" height="4" preserveAspectRatio="none" href="{href}"/>'
    new, n = republish.add_xlink_href(doc_svg(el), 'X_10-9')
    assert n == 1
    assert f'preserveAspectRatio="none" href="{href}" xlink:href="{href}"/>' in new
    again, n2 = republish.add_xlink_href(new, 'X_10-9')
    assert n2 == 0 and again == new
    plain = doc_svg('<rect x="1" y="1" width="2" height="2"/>')
    assert republish.add_xlink_href(plain, 'X_10-9') == (plain, 0)
    with pytest.raises(republish.RepublishError, match='sans href'):
        republish.add_xlink_href(doc_svg('<image x="1"/>'), 'X_10-9')


def _tanf_page(work):
    data = analysis.analyze(work, 'Syria', 'At_Tanf', 1985, log=lambda *a: None)
    (p,) = compose.build_pages(data, log=lambda *a: None)
    return p.svg


def test_engine_writes_both_href_and_xlink_href_on_the_f10_image(work):
    svg = _tanf_page(work)
    (el,) = re.findall(r'<image\b[^>]*>', svg)
    a = re.search(r' href="([^"]*)"', el).group(1)
    b = re.search(r' xlink:href="([^"]*)"', el).group(1)
    assert a == b and a.startswith('data:image/jpeg;base64,')
    assert el.index(' href="') < el.index(' xlink:href="') and 'xmlns:xlink=' in svg[:400]
    assert republish.add_xlink_href(svg, 'At_Tanf_10-9') == (svg, 0)         # le moteur et l'outil ecrivent la meme chose


# ---------- export : PNG a 256 couleurs, /Interpolate ----------
def f10_svg():
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="148mm" height="210mm" '
            'viewBox="0 0 148 210"><rect width="148" height="210" fill="#FFFFFF"/>'
            f'<image x="10" y="10" width="120" height="150" preserveAspectRatio="none" href="{tiny_png()}" xlink:href="{tiny_png()}"/></svg>')


def test_png_of_a_page_with_an_image_is_quantized(tmp_path):
    _, pdf, png = export.export_page(f10_svg(), tmp_path / 'a')
    im = Image.open(png)
    assert im.mode == 'P' and im.size == export.PNG_SIZE
    assert len(im.getcolors(maxcolors=300)) <= 256
    # PDF : /Interpolate true sur l'image rempaquetee en JPEG
    (img,) = [i for p in PdfReader(str(pdf)).pages for i in p.images]
    obj = img.indirect_reference.get_object()
    assert obj['/Filter'] == '/DCTDecode' and obj['/Interpolate'].value is True


def test_png_of_a_page_without_image_stays_rgba(tmp_path):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="148mm" height="210mm" viewBox="0 0 148 210">'
           '<rect width="148" height="210" fill="#FFFFFF"/><circle cx="50" cy="50" r="20" fill="#FF0000"/></svg>')
    _, _, png = export.export_page(svg, tmp_path / 'b')
    im = Image.open(png)
    assert im.mode == 'RGBA' and im.size == export.PNG_SIZE


def test_quantize_png_uses_median_cut(tmp_path, monkeypatch):
    seen = {}
    real = Image.Image.quantize

    def spy(self, colors=256, method=None, *a, **k):
        seen['args'] = (colors, method, a, k, self.mode)
        return real(self, colors, method, *a, **k)
    monkeypatch.setattr(Image.Image, 'quantize', spy)
    p = tmp_path / 'q.png'
    Image.new('RGBA', (20, 20), (10, 20, 30, 255)).save(p)
    export.quantize_png(p)
    assert seen['args'] == (256, Image.Quantize.MEDIANCUT, (), {}, 'RGB')


# ---------- republish sur un petit zip synthetique ----------
def konya_latlon():
    proj = geo.Projection.for_theatre('Syria')
    ap = next(a for a in dcsdata.load_terrain('Syria').airports.values() if a.name == 'Konya')
    return proj.to_latlon(ap.position.x, ap.position.y)


def three_fields(lat, lon, elev='ELEV 3392 FT', var='VAR 3°E (1985)'):
    t = lambda x, s: svgdoc.text(x, 21.2, s, pt(7.2), 'bold')
    return [t(6, elev), t(51.3, geo.arp_text(lat, lon)), t(96.7, var)]


def five_fields(lat, lon, elev='ELEV 3392 FT', var='VAR 3°E (1985)'):
    return page.header_row(elev, lat, lon, var)


RULE = '<line x1="6" y1="22.8" x2="142" y2="22.8" stroke="#1A1A1A" stroke-width="0.25"/>'


def frame_layer(t, p, year=1985, cx=21.0, cy=156.6):
    """Calque `Frame & grid` d'une page 10-9 minimale : le bloc du nord tel que le moteur l'ecrit pour des fleches a t et p degres
    (avant le lot caps-cockpit : t = -convergence ; apres : t = 0)."""
    var, yl = geo.var_text(p - t, year)
    lines = [*page.north_arrows(cx, cy, t, p), svgdoc.text(cx, cy + 3.4, var, pt(5.9), 'bold', svgdoc.INK, 'middle'),
             svgdoc.text(cx, cy + 6.0, yl, pt(5.4), 'regular', svgdoc.INK2, 'middle')]
    return ('<g inkscape:groupmode="layer" id="layer-frame-grid" inkscape:label="Frame &amp; grid">\n' + '\n'.join(lines) + '\n</g>')


OLD_NORTH = (7.29, 10.58)               # fleches d'une page publiee avant le lot : t = -convergence, p = t + declinaison (3,29 deg)


@pytest.fixture(scope='module')
def old_zip(tmp_path_factory):
    """Zip synthetique : Konya_10-9 (trois champs, barre de 750 m sans fin), Konya_10-9A (cinq champs, pas de barre),
    KonyaF_10-9 (cinq champs, barre de 300 m, <image> sans xlink:href), leurs recueils, une entree d'un autre type."""
    root = tmp_path_factory.mktemp('republish')
    lat, lon = konya_latlon()
    bar750, _ = block(750)
    bar300, _ = page.scale_block(BAR_X, BAR_Y, 38.0 / 300)
    img = f'<image x="10" y="30" width="100" height="120" preserveAspectRatio="none" href="{tiny_png()}"/>'
    pages = {
        'Konya_10-9': doc_svg('<rect x="1" y="1" width="2" height="2"/>', *three_fields(lat, lon), RULE, *old_bar_elements(bar750, 750),
                              frame_layer(*OLD_NORTH)),
        'Konya_10-9A': doc_svg('<circle cx="9" cy="9" r="3"/>', *five_fields(lat, lon), RULE, foot=foot_layer(new=True)),      # deja porteuse
        'KonyaF_10-9': doc_svg(img, *five_fields(lat, lon), RULE, *bar300, frame_layer(0.0, 3.29)),
    }
    files = {}
    for name, svg in pages.items():
        s, p, n = export.export_page(svg, root / 'old' / name)
        files[name] = (s.read_bytes(), p.read_bytes(), n.read_bytes())
    export.merge_pdfs([root / 'old' / 'Konya_10-9.pdf', root / 'old' / 'Konya_10-9A.pdf'], root / 'old' / 'Konya_charts.pdf')
    export.merge_pdfs([root / 'old' / 'KonyaF_10-9.pdf'], root / 'old' / 'KonyaF_charts.pdf')
    path = root / 'release.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('pack/heliports/p01.jpg', b'\xff\xd8jpeg-bytes\x00\x01')
        for name, (s, p, n) in files.items():
            z.writestr(f'pack/png/{name}.png', n)
            z.writestr(f'pack/svg/{name}.svg', s)
            z.writestr(f'pack/pdf/{name}.pdf', p)
        z.writestr('pack/pdf/Konya_charts.pdf', (root / 'old' / 'Konya_charts.pdf').read_bytes(), zipfile.ZIP_STORED)
        z.writestr('pack/pdf/KonyaF_charts.pdf', (root / 'old' / 'KonyaF_charts.pdf').read_bytes(), zipfile.ZIP_STORED)
        z.writestr('pack/notes.txt', b'unchanged')
    return path, pages, (lat, lon)


def run(zin, out):
    return republish.main(['Syria', '--zip', str(zin), '--out', str(out)])


def test_republish_zip(old_zip, tmp_path, capsys):
    zin, pages, (lat, lon) = old_zip
    out = tmp_path / 'new.zip'
    assert run(zin, out) == 0
    summary = capsys.readouterr().out
    assert 'pages reexportees : 3' in summary and 'nord et caps : 2 pages 10-9, dont 1 converties (1 deja a t = 0)' in summary and 'en-tetes reecrits : 1 ; verifies : 2' in summary
    assert 'barres completees : 1' in summary and '  Konya_10-9 : 750 M' in summary
    assert 'pages a fond F10 : 1' in summary and '  KonyaF_10-9' in summary
    assert 'recueils refaits : 2' in summary and 'entrees recopiees telles quelles : 2' in summary
    assert 'pictogramme au pied : 2 pages SVG retouchees (0 10-8, 2 10-9*, 0 11-x) ; deja porteuses : 1' in summary
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        assert [i.compress_type for i in a.infolist()] == [i.compress_type for i in b.infolist()]
        for n in ('pack/heliports/p01.jpg', 'pack/notes.txt'):
            assert a.read(n) == b.read(n)
        # page 10-9 : en-tete reecrit a cinq champs, fin de barre completee
        new = b.read('pack/svg/Konya_10-9.svg').decode()
        want = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *five_fields(lat, lon), RULE, *block(750)[0], frame_layer(0.0, 3.29),
                       foot=foot_layer(new=True))
        assert new == want                                  # fleche fine a 0, pleine a la declinaison relue (d = p - t)
        # page de parkings : en-tete deja a cinq champs, rien ne change
        assert b.read('pack/svg/Konya_10-9A.svg') == a.read('pack/svg/Konya_10-9A.svg')
        # page a fond F10 : xlink:href ajoute, PNG a palette
        f10 = b.read('pack/svg/KonyaF_10-9.svg').decode()
        assert ' xlink:href="data:image/png;base64,' in f10 and f10.replace(re.search(r' xlink:href="[^"]*"', f10).group(0), '') == pages['KonyaF_10-9'].replace(foot_layer(), foot_layer(new=True))
        assert Image.open(io.BytesIO(b.read('pack/png/KonyaF_10-9.png'))).mode == 'P'
        assert Image.open(io.BytesIO(b.read('pack/png/Konya_10-9.png'))).mode == 'RGBA'
        assert Image.open(io.BytesIO(b.read('pack/png/Konya_10-9A.png'))).mode == 'RGBA'
        (img,) = [i for p in PdfReader(io.BytesIO(b.read('pack/pdf/KonyaF_10-9.pdf'))).pages for i in p.images]
        assert img.indirect_reference.get_object()['/Interpolate'].value is True
        assert len(PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf'))).pages) == 2
        assert len(PdfReader(io.BytesIO(b.read('pack/pdf/KonyaF_charts.pdf'))).pages) == 1
        # pixels : la page de parkings ne bouge pas, la page 10-9 ne change que pres de l'etiquette ajoutee et de la ligne d'en-tete
        import numpy as np
        o = np.asarray(Image.open(io.BytesIO(a.read('pack/png/Konya_10-9A.png'))).convert('RGBA'))
        n = np.asarray(Image.open(io.BytesIO(b.read('pack/png/Konya_10-9A.png'))).convert('RGBA'))
        assert (o == n).all()


def test_republish_zip_pixels_differ_only_at_the_new_label(old_zip, tmp_path):
    """Page dont l'en-tete est deja a cinq champs : seule l'etiquette ajoutee change (+/- 3 mm autour de son point d'ancrage)."""
    import numpy as np
    zin, pages, (lat, lon) = old_zip
    lat_lon = (lat, lon)
    bar750, _ = block(750)
    svg = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *five_fields(*lat_lon), RULE, *old_bar_elements(bar750, 750))
    new_svg, info = republish.complete_scale_bar(svg, 'Konya_10-9', '10-9')
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as t:
        _, _, po = export.export_page(svg, Path(t) / 'o')
        _, _, pn = export.export_page(new_svg, Path(t) / 'n')
        ao = np.asarray(Image.open(po).convert('RGBA'))
        an = np.asarray(Image.open(pn).convert('RGBA'))
    ys, xs = np.nonzero(np.any(ao != an, axis=2))
    k = export.PNG_SIZE[0] / 148.0
    _, x, y = info
    assert len(ys) and xs.min() >= (x - 3) * k and xs.max() <= (x + 3) * k and ys.min() >= (y - 3) * k and ys.max() <= (y + 3) * k


def test_republish_refuses_to_overwrite(old_zip, tmp_path, capsys):
    zin, _, _ = old_zip
    out = tmp_path / 'exists.zip'
    out.write_bytes(b'x')
    assert run(zin, out) == 1
    assert 'existe deja' in capsys.readouterr().err and out.read_bytes() == b'x'


def _zip_with(tmp_path, svg_by_page, name='bad.zip'):
    """Zip minimal : une entree par page (png, pdf factices : l'outil s'arrete avant de les lire) et un recueil."""
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as z:
        for pg, svg in svg_by_page.items():
            z.writestr(f'p/svg/{pg}.svg', svg)
            z.writestr(f'p/png/{pg}.png', b'x')
            z.writestr(f'p/pdf/{pg}.pdf', b'x')
    return path


def test_republish_stops_on_a_parkings_page_with_a_bar(tmp_path, capsys):
    lat, lon = konya_latlon()
    elems, _ = block(750)
    zin = _zip_with(tmp_path, {'Konya_10-9A': doc_svg(*five_fields(lat, lon), RULE, *old_bar_elements(elems, 750))})
    assert run(zin, tmp_path / 'o.zip') == 1
    err = capsys.readouterr().err
    assert 'ARRET' in err and 'Konya_10-9A' in err and 'parkings' in err and not (tmp_path / 'o.zip').exists()


def test_republish_stops_on_a_10_9_page_without_a_bar(tmp_path, capsys):
    lat, lon = konya_latlon()
    zin = _zip_with(tmp_path, {'Konya_10-9': doc_svg(*five_fields(lat, lon), RULE, frame_layer(0.0, 3.29))})
    assert run(zin, tmp_path / 'o.zip') == 1
    err = capsys.readouterr().err
    assert 'Konya_10-9' in err and '0 barres' in err and not (tmp_path / 'o.zip').exists()


def test_republish_stops_when_the_five_field_header_differs_from_header_row(tmp_path, capsys):
    lat, lon = konya_latlon()
    fields = five_fields(lat, lon)
    fields[2] = fields[2].replace('N37', 'N38')
    zin = _zip_with(tmp_path, {'Konya_10-9A': doc_svg(*fields, RULE)})
    assert run(zin, tmp_path / 'o.zip') == 1
    err = capsys.readouterr().err
    assert 'Konya_10-9A' in err and 'page.header_row' in err and not (tmp_path / 'o.zip').exists()


def test_republish_stops_on_four_header_fields(tmp_path, capsys):
    lat, lon = konya_latlon()
    zin = _zip_with(tmp_path, {'Konya_10-9A': doc_svg(*five_fields(lat, lon)[:4], RULE)})
    assert run(zin, tmp_path / 'o.zip') == 1
    assert '4 textes' in capsys.readouterr().err


def test_republish_copies_a_zip_without_chart_pages(tmp_path, capsys):
    zin = tmp_path / 'h.zip'
    with zipfile.ZipFile(zin, 'w') as z:
        z.writestr('h/heliports/a.jpg', b'a')
        z.writestr('h/dir.pdf', b'b')
    out = tmp_path / 'o.zip'
    assert run(zin, out) == 0
    assert 'aucune page de chart' in capsys.readouterr().out
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ['h/heliports/a.jpg', 'h/dir.pdf'] and z.read('h/dir.pdf') == b'b'


def test_republish_uses_the_engine_functions(monkeypatch):
    """Le moteur et l'outil partagent le code : l'etiquette de fin vient de `page.scale_end_label`, l'en-tete de `page.header_row`."""
    calls = []
    real = page.scale_end_label
    monkeypatch.setattr(page, 'scale_end_label', lambda *a: calls.append(a) or real(*a))
    elems, _ = block(750)                       # scale_block l'appelle
    assert len(calls) == 1
    republish.complete_scale_bar(doc_svg(*old_bar_elements(elems, 750)), 'X_10-9', '10-9')
    assert len(calls) == 2 and calls[0][2] == calls[1][2] == 750


# ---------- lot ac-approches-v1 : les pages helicoptere 11-xH ----------
def test_republish_puts_the_pictogram_on_the_helicopter_approach_pages_and_puts_them_after_the_11_x(tmp_path, capsys):
    """Une page `<stem>_11-<n>H` est traitee comme une page 11-x (son elevation porte les metres) : sa seule retouche est le pied ; une
    page a la forme neuve du pied est recopiee telle quelle, les autres sont retouchees puis reexportees ; leur PDF entre dans le
    recueil apres les 11-x ; une chart qui n'a que des pages 11-xH (zip helicopters) n'a pas de recueil."""
    lat, lon = konya_latlon()
    bar300, _ = page.scale_block(BAR_X, BAR_Y, 38.0 / 300)
    heli = five_fields(lat, lon, elev='ELEV 3392 FT · 1034 M')
    pages = {'Konya_10-9': doc_svg('<rect x="1" y="1" width="2" height="2"/>', *five_fields(lat, lon), RULE, *bar300, frame_layer(0.0, 3.29)),
             'Konya_11-1': doc_svg('<circle cx="5" cy="5" r="1"/>', *five_fields(lat, lon), RULE, foot=foot_layer(new=True)),
             'Konya_11-1H': doc_svg('<circle cx="6" cy="6" r="1"/>', *heli, RULE),
             'Konya_11-2': doc_svg('<circle cx="7" cy="7" r="1"/>', *five_fields(lat, lon), RULE),
             'Other_11-1H': doc_svg('<circle cx="8" cy="8" r="1"/>', *heli, RULE)}
    files = {}
    for name, svg in pages.items():
        s, p, n = export.export_page(svg, tmp_path / 'src' / name)
        files[name] = (s.read_bytes(), p.read_bytes(), n.read_bytes())
    export.merge_pdfs([tmp_path / 'src' / f'{n}.pdf' for n in ('Konya_10-9', 'Konya_11-1', 'Konya_11-2', 'Konya_11-1H')],
                      tmp_path / 'src' / 'rec.pdf')
    zin = tmp_path / 'in.zip'
    with zipfile.ZipFile(zin, 'w') as z:
        for name, (s, p, n) in files.items():
            for sub, blob in (('pdf', p), ('png', n), ('svg', s)):
                z.writestr(f'pack/{sub}/{name}.{sub}', blob)
        z.writestr('pack/pdf/Konya_charts.pdf', (tmp_path / 'src' / 'rec.pdf').read_bytes())
    out = tmp_path / 'out.zip'
    assert run(zin, out) == 0
    summary = capsys.readouterr().out
    assert "pages d'approche 11-x : 4 (1 recopiees telles quelles, 3 retouchees au pied)" in summary
    assert 'pictogramme au pied : 4 pages SVG retouchees (0 10-8, 1 10-9*, 3 11-x) ; deja porteuses : 1' in summary
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        for sub in ('pdf', 'png', 'svg'):                                              # forme neuve : a l'octet
            assert b.read(f'pack/{sub}/Konya_11-1.{sub}') == a.read(f'pack/{sub}/Konya_11-1.{sub}')
        for name in ('Konya_11-1H', 'Konya_11-2', 'Other_11-1H'):                      # ancienne forme : le pied seul change
            assert b.read(f'pack/svg/{name}.svg').decode() == pages[name].replace(foot_layer(), foot_layer(new=True))
            assert b.read(f'pack/png/{name}.png') != a.read(f'pack/png/{name}.png')
        rec = PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf')))
        assert len(rec.pages) == 4
        last = PdfReader(io.BytesIO(b.read('pack/pdf/Konya_11-1H.pdf'))).pages[0].extract_text()
        assert rec.pages[3].extract_text() == last and 'Other_charts' not in ' '.join(b.namelist())
    assert republish._approach_key('11-1H') > republish._approach_key('11-2')


# ---------- lot ac-marque-pied : le pictogramme au pied des charts ----------
def test_block_replaces_the_opening_tag_once_and_keeps_the_rest():
    text = (marque.MARQUE_DIR / marque.PICTO_FILE).read_text(encoding='utf-8').strip()
    assert text.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360" width="360" height="360">')
    b = marque.block()
    assert b.startswith('<svg x="6" y="201.7" width="2.6" height="2.6" viewBox="0 0 360 360">')
    assert b == '<svg x="6" y="201.7" width="2.6" height="2.6" viewBox="0 0 360 360">' + text[len(marque.MARQUE_TAG):]
    assert b.count('<svg') == 1 and '<image' not in b and b.endswith('</svg>')


@pytest.mark.parametrize('content', ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360">\n<circle/></svg>',     # balise differente
                                     '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360" width="360" height="360"/>',
                                     ''])
def test_block_refuses_a_file_that_does_not_start_with_the_expected_tag(tmp_path, content):
    (tmp_path / marque.PICTO_FILE).write_text(content, encoding='utf-8')
    with pytest.raises(marque.MarqueError, match='balise attendue'):
        marque.block(tmp_path)
    with pytest.raises(marque.MarqueError):
        marque.picture(35, tmp_path)


def test_block_strips_the_blanks_of_the_file_and_refuses_a_missing_file(tmp_path):
    (tmp_path / marque.PICTO_FILE).write_text('\n  ' + marque.MARQUE_TAG + '<g/></svg> \n\n', encoding='utf-8')
    assert marque.block(tmp_path) == marque.BLOCK_TAG + '<g/></svg>'
    with pytest.raises(marque.MarqueError, match='illisible'):
        marque.block(tmp_path / 'absent')


def test_engine_footer_layer_equals_the_republish_retouch_of_the_old_form():
    """Le calque Footer qu'ecrit la retouche d'une page a l'ancienne forme est celui de la forme neuve, a l'octet ; la retouche ne
    touche pas au texte de gauche de la page publiee (lot ac-donnees-jeu : « via pydcs » reste)."""
    got, state = republish.retouch_footer(doc_svg('<rect x="1" y="1" width="2" height="2"/>'), 'X_10-9')
    assert state == 'retouchee'
    layer = lambda t: t[t.index('<g inkscape:groupmode="layer" id="layer-footer"'):t.rindex('</svg>')].rstrip('\n')
    assert layer(got) == foot_layer(new=True)
    assert FOOT_TEXT in got and NEW_FOOT_TEXT + '<' not in got


def test_engine_footer_layer_writes_the_new_text():
    """Lot ac-donnees-jeu : le moteur (page.footer) ecrit le texte neuf, sans pydcs, dans la forme neuve."""
    from types import SimpleNamespace
    d = svgdoc.Doc()
    page.footer(d, SimpleNamespace(taxiway_source='jeu', osm_icao=None), False)
    new_form = foot_layer(new=True).replace(FOOT_TEXT, NEW_FOOT_TEXT)
    assert '\n'.join(d.layers['Footer']) == new_form.split('\n', 1)[1].rsplit('\n', 1)[0]
    new_page = d.render()
    layer = lambda t: t[t.index('<g inkscape:groupmode="layer" id="layer-footer"'):t.rindex('</svg>')].rstrip('\n')
    assert layer(new_page) == new_form and 'pydcs' not in new_page


def test_footer_retouch_changes_nothing_but_the_footer_layer():
    old = doc_svg('<rect x="1" y="1" width="2" height="2"/>', RULE)
    got, state = republish.retouch_footer(old, 'X_10-8')
    assert state == 'retouchee'
    assert got == old.replace(foot_layer(), foot_layer(new=True))
    assert got.count('<text x="10.2" y="203.7"') == 1 and got.count('<text x="6" y="203.7"') == 0 and got.count(marque.block()) == 1
    assert got.index(marque.block()) > got.index('NOT FOR REAL-WORLD NAVIGATION') and got.index('NOT FOR') < got.index('</g>\n</svg>')


def test_footer_new_form_is_left_as_it_is_and_the_retouch_is_idempotent():
    new = doc_svg('<rect x="1" y="1" width="2" height="2"/>', foot=foot_layer(new=True))
    assert republish.retouch_footer(new, 'X_11-1') == (new, 'deja')
    once, _ = republish.retouch_footer(doc_svg('<rect x="1" y="1" width="2" height="2"/>'), 'X_11-1')
    assert republish.retouch_footer(once, 'X_11-1') == (once, 'deja')


@pytest.mark.parametrize('foot', [
    foot_layer(left_x='7'),                                              # une troisieme forme : le texte de gauche ailleurs
    foot_layer(new=True, left_x='6'),                                    # le bloc sans le decalage du texte
    foot_layer(extra='\n' + marque.block()),                             # le bloc sans le decalage du texte (ancienne x)
    foot_layer(new=True).replace('<line ', '<rect ').replace('stroke-width="0.5"/>', 'stroke-width="0.5"/>', 1),
    foot_layer(new=True, extra='\n<circle cx="1" cy="1" r="1"/>'),       # un element de trop
    foot_layer().replace('y="203.7"', 'y="203.8"', 1),
    foot_layer(new=True).replace('height="2.6"', 'height="2.7"'),        # un bloc qui n'est pas celui du fichier
    '',                                                                  # pas de calque
], ids=['x7', 'new-x6', 'old-with-block', 'no-line', 'extra-element', 'y', 'other-block', 'none'])
def test_footer_other_forms_stop_and_nothing_is_written(tmp_path, capsys, foot):
    svg = doc_svg('<rect x="1" y="1" width="2" height="2"/>', foot=foot or False)
    with pytest.raises(republish.RepublishError, match='Footer'):
        republish.retouch_footer(svg, 'X_10-8')
    lat, lon = konya_latlon()
    zin = _zip_with(tmp_path, {'Konya_10-9A': doc_svg('<circle cx="9" cy="9" r="3"/>', *five_fields(lat, lon), RULE, foot=foot or False)})
    assert run(zin, tmp_path / 'o.zip') == 1
    assert 'ARRET' in capsys.readouterr().err and not (tmp_path / 'o.zip').exists()


def test_footer_two_layers_stop():
    with pytest.raises(republish.RepublishError, match='2 calques Footer'):
        republish.retouch_footer(doc_svg(foot=foot_layer() + '\n' + foot_layer()), 'X_10-9')


def test_a_10_8_and_an_11_x_with_the_old_form_are_retouched_and_exported_again(tmp_path):
    """10-8 et 11-x : plus recopiees a l'octet quand le pied est a l'ancienne forme (un seul calque change) ; a la forme neuve, recopiees."""
    lat, lon = konya_latlon()
    pages = {'Konya_10-8': doc_svg('<circle cx="5" cy="5" r="1"/>', *five_fields(lat, lon), RULE),
             'Konya_10-9A': doc_svg('<circle cx="6" cy="6" r="1"/>', *five_fields(lat, lon), RULE, foot=foot_layer(new=True)),
             'Konya_11-1': doc_svg('<circle cx="7" cy="7" r="1"/>', *five_fields(lat, lon), RULE)}
    files = {}
    for name, svg in pages.items():
        s_, p_, n_ = export.export_page(svg, tmp_path / 'src' / name)
        files[name] = (s_.read_bytes(), p_.read_bytes(), n_.read_bytes())
    export.merge_pdfs([tmp_path / 'src' / f'{n}.pdf' for n in pages], tmp_path / 'src' / 'rec.pdf')
    zin = tmp_path / 'in.zip'
    with zipfile.ZipFile(zin, 'w') as z:
        for name, (s_, p_, n_) in files.items():
            for sub, blob in (('pdf', p_), ('png', n_), ('svg', s_)):
                z.writestr(f'pack/{sub}/{name}.{sub}', blob)
        z.writestr('pack/pdf/Konya_charts.pdf', (tmp_path / 'src' / 'rec.pdf').read_bytes())
    out = tmp_path / 'out.zip'
    assert run(zin, out) == 0
    again = tmp_path / 'again.zip'
    assert run(out, again) == 0
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b, zipfile.ZipFile(again) as c:
        for name in ('Konya_10-8', 'Konya_11-1'):
            assert b.read(f'pack/svg/{name}.svg').decode() == pages[name].replace(foot_layer(), foot_layer(new=True))
            assert b.read(f'pack/png/{name}.png') != a.read(f'pack/png/{name}.png')
        assert b.read('pack/png/Konya_10-9A.png') == a.read('pack/png/Konya_10-9A.png')
        for n in b.namelist():
            if not n.endswith('.pdf'):
                assert b.read(n) == c.read(n)                                 # un second passage : tout egal a l'octet (hors PDF)
        assert len(PdfReader(io.BytesIO(b.read('pack/pdf/Konya_charts.pdf'))).pages) == 3


# ---------- addendum 1 : `--helicopteres`, les recueils *airfields* qui portent des pages 11-xH ----------
def _heli_fixture(tmp_path, heli_pages=('11-1H',), elev='ELEV 3392 FT · 1034 M', heli_foot_new=True, heli_elev=None, recueil_heli=('11-1H',)):
    """(zip airfields, zip de sortie helicopters) synthetiques : la chart Konya a une 10-9A (entree) et un recueil qui porte en plus ses pages
    11-xH `recueil_heli` (sans entree) ; le zip helicopters a les pages 11-xH `heli_pages` (SVG, PNG, PDF)."""
    lat, lon = konya_latlon()
    root = tmp_path / 'src'
    air = doc_svg('<circle cx="9" cy="9" r="3"/>', *five_fields(lat, lon), RULE, foot=foot_layer(new=True))
    export.export_page(air, root / 'Konya_10-9A')
    heli = {}
    for k, suffix in enumerate(sorted(set(heli_pages) | set(recueil_heli))):
        e = heli_elev if (heli_elev and suffix in heli_pages and k == 0) else elev
        svg = doc_svg(f'<circle cx="{20 + k}" cy="20" r="1"/>', *five_fields(lat, lon, elev=e), RULE, foot=foot_layer(new=heli_foot_new))
        heli[suffix] = export.export_page(svg, root / f'Konya_{suffix}')
    old_heli = {}
    for suffix in recueil_heli:                                  # les pages du recueil publie : celles de la release
        svg = doc_svg(f'<circle cx="{20 + sorted(heli).index(suffix)}" cy="20" r="1"/>', *five_fields(lat, lon, elev=elev), RULE)
        old_heli[suffix] = export.export_page(svg, root / 'old' / f'Konya_{suffix}')
    export.merge_pdfs([root / 'Konya_10-9A.pdf'] + [old_heli[s][1] for s in recueil_heli], root / 'rec.pdf')
    zair, zheli = tmp_path / 'air.zip', tmp_path / 'heli.zip'
    with zipfile.ZipFile(zair, 'w') as z:
        for sub in ('png', 'svg', 'pdf'):
            z.writestr(f'pack/{sub}/Konya_10-9A.{sub}', (root / f'Konya_10-9A.{sub}').read_bytes())
        z.writestr('pack/pdf/Konya_charts.pdf', (root / 'rec.pdf').read_bytes())
    with zipfile.ZipFile(zheli, 'w') as z:
        for suffix in heli_pages:
            for sub in ('png', 'svg', 'pdf'):
                z.writestr(f'h/{sub}/Konya_{suffix}.{sub}', (root / f'Konya_{suffix}.{sub}').read_bytes())
    return zair, zheli


def _rec_texts(path):
    with zipfile.ZipFile(path) as z:
        return [p.extract_text() for p in PdfReader(io.BytesIO(z.read('pack/pdf/Konya_charts.pdf'))).pages]


def test_helicopteres_completes_the_collection_with_the_11_xh_pages_and_is_idempotent(tmp_path, capsys):
    zair, zheli = _heli_fixture(tmp_path)
    out = tmp_path / 'o.zip'
    assert republish.main(['Syria', '--zip', str(zair), '--out', str(out), '--helicopteres', str(zheli)]) == 0
    summary = capsys.readouterr().out
    assert 'recueils completes par --helicopteres : 1 ; pages ajoutees a leur fin : 1' in summary
    assert len(_rec_texts(out)) == len(_rec_texts(zair)) == 2
    with zipfile.ZipFile(zheli) as h:
        assert _rec_texts(out)[1] == PdfReader(io.BytesIO(h.read('h/pdf/Konya_11-1H.pdf'))).pages[0].extract_text()
    with zipfile.ZipFile(zair) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist() and not [n for n in a.namelist() if 'pdf/Konya_11' in n]       # aucune entree 11-xH ajoutee
    again = tmp_path / 'again.zip'
    assert republish.main(['Syria', '--zip', str(out), '--out', str(again), '--helicopteres', str(zheli)]) == 0
    with zipfile.ZipFile(out) as a, zipfile.ZipFile(again) as b:
        assert all(a.read(n) == b.read(n) for n in a.namelist() if not n.endswith('.pdf'))
    assert len(_rec_texts(again)) == 2 and 'pictogramme au pied : 0 pages SVG retouchees' in capsys.readouterr().out


def test_without_helicopteres_a_collection_with_11_xh_pages_stops_and_names_the_option(tmp_path, capsys):
    zair, _ = _heli_fixture(tmp_path)
    out = tmp_path / 'o.zip'
    with pytest.raises(republish.RepublishError, match='--helicopteres'):
        republish.republish_zip('Syria', zair, out)
    assert not out.exists()
    assert republish.main(['Syria', '--zip', str(zair), '--out', str(out)]) == 1
    assert '--helicopteres' in capsys.readouterr().err and not out.exists()


def test_a_collection_without_missing_pages_does_not_need_the_option_and_ignores_it(tmp_path):
    zair, zheli = _heli_fixture(tmp_path, recueil_heli=())
    assert republish.main(['Syria', '--zip', str(zair), '--out', str(tmp_path / 'a.zip')]) == 0
    assert republish.main(['Syria', '--zip', str(zair), '--out', str(tmp_path / 'b.zip'), '--helicopteres', str(zheli)]) == 0


@pytest.mark.parametrize('case, match', [('count', r'1 pages sans entree, le zip de `--helicopteres` a 2'),
                                         ('none', r'1 pages sans entree, le zip de `--helicopteres` a 0'),
                                         ('foot', 'pas le pied de la forme neuve'),
                                         ('text', 'le texte de la page du zip de `--helicopteres` differe')])
def test_helicopteres_refuses_a_zip_that_does_not_match_and_writes_nothing(tmp_path, case, match):
    if case == 'count':
        zair, zheli = _heli_fixture(tmp_path, heli_pages=('11-1H', '11-2H'), recueil_heli=('11-1H',))
    elif case == 'none':
        zair, zheli = _heli_fixture(tmp_path, heli_pages=())
    elif case == 'foot':
        zair, zheli = _heli_fixture(tmp_path, heli_foot_new=False)
    else:
        zair, zheli = _heli_fixture(tmp_path, heli_elev='ELEV 3500 FT · 1067 M')
    out = tmp_path / 'o.zip'
    with pytest.raises(republish.RepublishError, match=match):
        republish.republish_zip('Syria', zair, out, heli_zip=zheli)
    assert not out.exists()


@pytest.mark.parametrize('variant', ['line', 'left', 'right'])
def test_footer_two_elements_on_one_line_stop(variant):
    """Un element par ligne exactement : deux elements sur une meme ligne du calque Footer ne sont pas une forme reconnue."""
    layer = foot_layer()
    if variant == 'line':
        bad = layer.replace('stroke-width="0.5"/>\n', 'stroke-width="0.5"/><circle r="1"/>\n', 1)
    elif variant == 'left':
        bad = layer.replace(FOOT_TEXT + '</text>\n', FOOT_TEXT + '</text><circle r="1"/>\n', 1)
    else:
        bad = layer.replace('NAVIGATION</text>\n', 'NAVIGATION</text><circle r="1"/>\n', 1)
    assert bad != layer
    with pytest.raises(republish.RepublishError, match='Footer'):
        republish.retouch_footer(doc_svg(foot=bad), 'X_10-9')
    nested = layer.replace(FOOT_TEXT, FOOT_TEXT + '<tspan>x</tspan>', 1)
    with pytest.raises(republish.RepublishError, match='Footer'):
        republish.retouch_footer(doc_svg(foot=nested), 'X_10-9')
