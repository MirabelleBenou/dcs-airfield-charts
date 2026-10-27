"""Lot ac-pdf-jpeg : les images des PDF sont en JPEG (CairoSVG les ecrit sans perte), les PDF sans image ne sont pas relus.
Les tests At_Tanf / Der_Salman s'executent avec DCS_CHARTS_SAMPLES ; le test de la fonction seule est pur."""
import base64
import io
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from render import analysis, compose, export


def _images(pdf):
    out = []
    for page in PdfReader(str(pdf)).pages:
        for im in page.images:
            out.append((im.image.size, im.indirect_reference.get_object()))
    return out


def _page(work, base):
    data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
    pages = compose.build_pages(data, log=lambda *a: None)
    assert len(pages) == 1
    return pages[0].svg


def test_jpeg_images_in_pdf_replaces_lossless_image(tmp_path):
    import cairosvg
    buf = io.BytesIO()
    Image.effect_noise((64, 80), 40).convert('RGB').save(buf, 'PNG')
    href = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="125">'
           f'<image x="10" y="10" width="80" height="100" xlink:href="{href}"/></svg>')
    pdf = tmp_path / 'a.pdf'
    cairosvg.svg2pdf(bytestring=svg.encode(), write_to=str(pdf))
    (before,) = _images(pdf)
    assert before[1]['/Filter'] == '/FlateDecode'
    export.jpeg_images_in_pdf(pdf)
    (after,) = _images(pdf)
    assert after[0] == before[0] == (64, 80)
    assert after[1]['/Filter'] == '/DCTDecode' and after[1]['/ColorSpace'] == '/DeviceRGB'


def test_at_tanf_pdf_image_is_jpeg(work, tmp_path):
    svg = _page(work, 'At_Tanf')
    assert '<image' in svg
    _, pdf, _ = export.export_page(svg, tmp_path / 'At_Tanf_10-9')
    images = _images(pdf)
    assert len(images) == 1
    size, obj = images[0]
    assert size == (1488, 1866)
    assert obj['/Filter'] == '/DCTDecode'
    assert pdf.stat().st_size <= 1_000_000
    out = tmp_path / 'At_Tanf_charts.pdf'
    export.merge_pdfs([pdf], out)                                # le recueil reprend la page deja en JPEG
    (rsize, robj), = _images(out)
    assert rsize == (1488, 1866) and robj['/Filter'] == '/DCTDecode'
    assert out.stat().st_size <= 1_000_000


def test_der_salman_pdf_without_image_is_not_rewritten(work, tmp_path, monkeypatch):
    svg = _page(work, 'Der_Salman')
    assert '<image' not in svg
    calls = []
    monkeypatch.setattr(export, 'jpeg_images_in_pdf', lambda *a, **k: calls.append(a))

    def no_pypdf(*a, **k):
        raise AssertionError('pypdf ne doit pas rouvrir un PDF sans image')
    monkeypatch.setattr('pypdf.PdfWriter', no_pypdf)
    _, pdf, _ = export.export_page(svg, tmp_path / 'Der_Salman_10-9')
    assert calls == []                                           # le pas n'est pas execute
    assert _images(pdf) == []
