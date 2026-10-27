"""Export SVG -> PDF et PNG (CairoSVG), recueil PDF (pypdf)."""
import os
import sys
import tempfile
from pathlib import Path

from .fonts import FONT_DIR

PNG_SIZE = (1748, 2480)
JPEG_QUALITY = 88


def ensure_fonts():
    """Rend les polices de render/fonts/ visibles de Cairo (fontconfig) avant son chargement.
    Windows : Cairo y utilise les polices installees ; installer les .ttf de render/fonts/."""
    if sys.platform == 'win32' or os.environ.get('FONTCONFIG_FILE'):
        return
    conf = Path(tempfile.gettempdir()) / 'dcs_airfield_charts_fonts.conf'
    sys_conf = '/etc/fonts/fonts.conf'
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n<fontconfig>\n'
        f'<include ignore_missing="yes">{sys_conf}</include>\n<dir>{FONT_DIR}</dir>\n</fontconfig>\n', encoding='utf-8')
    os.environ['FONTCONFIG_FILE'] = str(conf)


def export_page(svg_text, base_path):
    """Ecrit <base>.svg, <base>.pdf, <base>.png."""
    ensure_fonts()
    import cairosvg
    base_path = Path(base_path)
    base_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path = base_path.with_suffix('.svg')
    svg_path.write_text(svg_text, encoding='utf-8')
    data = svg_text.encode('utf-8')
    pdf_path = base_path.with_suffix('.pdf')
    cairosvg.svg2pdf(bytestring=data, write_to=str(pdf_path))
    if '<image' in svg_text:
        jpeg_images_in_pdf(pdf_path)
    cairosvg.svg2png(bytestring=data, write_to=str(base_path.with_suffix('.png')),
                     output_width=PNG_SIZE[0], output_height=PNG_SIZE[1])
    return svg_path, base_path.with_suffix('.pdf'), base_path.with_suffix('.png')


def jpeg_images_in_pdf(pdf_path, quality=JPEG_QUALITY):
    """Remplace chaque image de chaque page du PDF par la meme image en RGB encodee en JPEG (CairoSVG les ecrit sans
    perte, FlateDecode : 3,8 Mo la page au lieu de 40 ko). Le PDF est reecrit au meme chemin."""
    from pypdf import PdfReader, PdfWriter
    pdf_path = Path(pdf_path)
    writer = PdfWriter(clone_from=str(pdf_path))
    for page in writer.pages:
        for image in page.images:
            image.replace(image.image.convert('RGB'), quality=quality)
    with open(pdf_path, 'wb') as fh:
        writer.write(fh)


def merge_pdfs(paths, out):
    from pypdf import PdfWriter
    w = PdfWriter()
    for p in paths:
        w.append(str(p))
    with open(out, 'wb') as fh:
        w.write(fh)
