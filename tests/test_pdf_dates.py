"""Les PDF ecrits par render/ ne portent aucune date : deux rendus du meme dessin sont identiques a l'octet."""
import base64
import io
import re
import sys
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render import export, guide, republish  # noqa: E402

PLAIN = ('<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297">'
         '<rect x="10" y="10" width="100" height="50" fill="#336699"/></svg>')


def _png_b64():
    im = Image.new('RGB', (64, 64))
    im.putdata([(x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256) for y in range(64) for x in range(64)])
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return base64.b64encode(buf.getvalue()).decode()


WITH_IMAGE = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="210mm" '
              'height="297mm" viewBox="0 0 210 297"><image x="0" y="0" width="210" height="297" '
              f'xlink:href="data:image/png;base64,{_png_b64()}"/><rect x="10" y="10" width="100" height="50"/></svg>')


def dates_in(data):
    """Nombre d'occurrences de CreationDate / ModDate dans les octets bruts et dans chaque flux zlib."""
    n = len(re.findall(rb'CreationDate|ModDate', data))
    for m in re.finditer(rb'stream\r?\n(.*?)endstream', data, re.S):
        try:
            n += len(re.findall(rb'CreationDate|ModDate', zlib.decompress(m.group(1))))
        except zlib.error:
            pass
    return n


def _render_twice(svg, tmp_path):
    out = []
    for k in range(2):
        out.append(export.export_page(svg, tmp_path / f'p{k}'))
        if k == 0:
            time.sleep(1.1)
    return out


@pytest.mark.parametrize('svg', [PLAIN, WITH_IMAGE], ids=['sans_image', 'avec_image'])
def test_export_page_sans_date_et_identique(svg, tmp_path):
    a, b = _render_twice(svg, tmp_path)
    pa, pb = a[1].read_bytes(), b[1].read_bytes()
    assert pa == pb
    assert dates_in(pa) == 0
    assert a[2].read_bytes() == b[2].read_bytes()


def test_export_page_garde_producer(tmp_path):
    from pypdf import PdfReader
    _, pdf, _ = export.export_page(PLAIN, tmp_path / 'p')
    assert PdfReader(str(pdf)).metadata.get('/Producer')


def test_merge_pdfs_sans_date_et_identique(tmp_path):
    a, b = _render_twice(WITH_IMAGE, tmp_path)
    outs = []
    for k, (x, y) in enumerate(((a, b), (a, b))):
        o = tmp_path / f'm{k}.pdf'
        export.merge_pdfs([x[1], y[1]], o)
        outs.append(o.read_bytes())
        time.sleep(1.1) if k == 0 else None
    assert outs[0] == outs[1]
    assert dates_in(outs[0]) == 0


def _dated_directory_pdf(tmp_path):
    """Un PDF a deux pages, une image chacune, tel que CairoSVG l'ecrit (avec sa date), images en JPEG."""
    import cairosvg
    export.ensure_fonts()
    from pypdf import PdfWriter
    pages = []
    for k in range(2):
        p = tmp_path / f'd{k}.pdf'
        cairosvg.svg2pdf(bytestring=WITH_IMAGE.encode(), write_to=str(p))
        export.jpeg_images_in_pdf(p)
        pages.append(p)
    w = PdfWriter()
    for p in pages:
        w.append(str(p))
    w.add_metadata({'/CreationDate': 'D:20200101000000Z', '/ModDate': 'D:20200101000000Z'})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_rewrite_directory_pdf_sans_date_et_identique(tmp_path):
    src = _dated_directory_pdf(tmp_path)
    assert dates_in(src) > 0
    jpg = io.BytesIO()
    Image.new('RGB', (64, 64), (200, 10, 10)).save(jpg, 'JPEG')
    outs = []
    for k in range(2):
        o = tmp_path / f'r{k}.pdf'
        republish.rewrite_directory_pdf(src, {1: jpg.getvalue()}, 2, 'test', o)
        outs.append(o.read_bytes())
        time.sleep(1.1) if k == 0 else None
    assert outs[0] == outs[1]
    assert dates_in(outs[0]) == 0


def test_render_pdf_du_guide_sans_date_et_identique(monkeypatch):
    monkeypatch.setattr(guide, '_LABELS', {'pdf_title': 'Mini guide', 'pdf_author': 'Mini author'})
    outs = []
    for k in range(2):
        outs.append(guide.render_pdf([PLAIN, WITH_IMAGE]))
        time.sleep(1.1) if k == 0 else None
    assert outs[0] == outs[1]
    assert dates_in(outs[0]) == 0
    from pypdf import PdfReader
    meta = PdfReader(io.BytesIO(outs[0])).metadata
    assert meta.get('/Title') == guide.label('pdf_title')
    assert meta.get('/Author') == guide.label('pdf_author')


def test_une_seule_definition():
    from render import build_approaches
    assert not hasattr(build_approaches, 'strip_pdf_dates')
    assert export.strip_pdf_dates
