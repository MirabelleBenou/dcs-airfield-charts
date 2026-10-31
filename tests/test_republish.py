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

from render import analysis, compose, dcsdata, export, geo, page, republish, svgdoc, update_coords
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
def doc_svg(*parts):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="148mm" height="210mm" viewBox="0 0 148 210">\n<g>\n' + '\n'.join(parts) + '\n</g>\n</svg>')


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


def three_fields(lat, lon, elev='ÉLÉV. 3392 FT', var='VAR 3°E (1985)'):
    t = lambda x, s: svgdoc.text(x, 21.2, s, pt(7.2), 'bold')
    return [t(6, elev), t(51.3, geo.arp_text(lat, lon)), t(96.7, var)]


def five_fields(lat, lon, elev='ÉLÉV. 3392 FT', var='VAR 3°E (1985)'):
    return page.header_row(elev, lat, lon, var)


RULE = '<line x1="6" y1="22.8" x2="142" y2="22.8" stroke="#1A1A1A" stroke-width="0.25"/>'


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
        'Konya_10-9': doc_svg('<rect x="1" y="1" width="2" height="2"/>', *three_fields(lat, lon), RULE, *old_bar_elements(bar750, 750)),
        'Konya_10-9A': doc_svg('<circle cx="9" cy="9" r="3"/>', *five_fields(lat, lon), RULE),
        'KonyaF_10-9': doc_svg(img, *five_fields(lat, lon), RULE, *bar300),
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
    assert 'pages reexportees : 3' in summary and 'en-tetes reecrits : 1 ; verifies : 2' in summary
    assert 'barres completees : 1' in summary and '  Konya_10-9 : 750 M' in summary
    assert 'pages a fond F10 : 1' in summary and '  KonyaF_10-9' in summary
    assert 'recueils refaits : 2' in summary and 'entrees recopiees telles quelles : 2' in summary
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        assert [i.compress_type for i in a.infolist()] == [i.compress_type for i in b.infolist()]
        for n in ('pack/heliports/p01.jpg', 'pack/notes.txt'):
            assert a.read(n) == b.read(n)
        # page 10-9 : en-tete reecrit a cinq champs, fin de barre completee
        new = b.read('pack/svg/Konya_10-9.svg').decode()
        want = doc_svg('<rect x="1" y="1" width="2" height="2"/>', *five_fields(lat, lon), RULE, *block(750)[0])
        assert new == want
        # page de parkings : en-tete deja a cinq champs, rien ne change
        assert b.read('pack/svg/Konya_10-9A.svg') == a.read('pack/svg/Konya_10-9A.svg')
        # page a fond F10 : xlink:href ajoute, PNG a palette
        f10 = b.read('pack/svg/KonyaF_10-9.svg').decode()
        assert ' xlink:href="data:image/png;base64,' in f10 and f10.replace(re.search(r' xlink:href="[^"]*"', f10).group(0), '') == pages['KonyaF_10-9']
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
    zin = _zip_with(tmp_path, {'Konya_10-9': doc_svg(*five_fields(lat, lon), RULE)})
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
