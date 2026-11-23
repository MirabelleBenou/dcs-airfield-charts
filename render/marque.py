"""Le pictogramme de la famille au pied des charts (lot ac-marque-pied) : lecture du fichier, bloc SVG des pages dessinees par le
moteur et par `republish.py`, image du pied du repertoire des heliports."""
from pathlib import Path

MARQUE_DIR = Path(__file__).resolve().parent / 'data' / 'marque'
PICTO_FILE = 'pictogramme-mono.svg'
MARQUE_TAG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360" width="360" height="360">'
BLOCK_TAG = '<svg x="6" y="201.7" width="2.6" height="2.6" viewBox="0 0 360 360">'
TEXT_SHIFT_MM = 4.2          # le texte de gauche du pied des pages SVG part apres le pictogramme : X0 + 4,2 mm


class MarqueError(Exception):
    """Le fichier du pictogramme est illisible ou n'a pas la balise attendue : rien n'est ecrit."""


def source(directory=None):
    """Le texte du fichier du pictogramme (UTF-8, blancs de debut et de fin retires), qui commence par `MARQUE_TAG`."""
    path = Path(directory or MARQUE_DIR) / PICTO_FILE
    try:
        text = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError) as e:
        raise MarqueError(f'marque : {PICTO_FILE} illisible ({type(e).__name__} : {e})') from None
    if not text.startswith(MARQUE_TAG):
        raise MarqueError(f'marque : {PICTO_FILE} ne commence pas par la balise attendue {MARQUE_TAG}')
    return text


def block(directory=None):
    """Le bloc des pages SVG : le texte du fichier dont la balise d'ouverture est remplacee, une seule fois, par `BLOCK_TAG`."""
    return source(directory).replace(MARQUE_TAG, BLOCK_TAG, 1)


def picture(side=35, directory=None):
    """Le pictogramme en image RGBA (`side` x `side` px), rendu par CairoSVG depuis le texte du fichier, balise d'origine."""
    import io

    import cairosvg
    from PIL import Image
    png = cairosvg.svg2png(bytestring=source(directory).encode('utf-8'), output_width=side, output_height=side)
    return Image.open(io.BytesIO(png)).convert('RGBA')
