"""Barlow Condensed (SIL OFL, render/fonts/) : mesure du texte et noms de familles pour le SVG.

CairoSVG n'a que deux graisses (normal, gras) : le texte courant est `Barlow Condensed` (Cairo associe
`normal` a la graisse Medium), le gras `Barlow Condensed` en gras, le semi-gras la famille `Barlow Condensed SemiBold`.
"""
from pathlib import Path

from fontTools.ttLib import TTFont

FONT_DIR = Path(__file__).resolve().parent / 'fonts'
PT = 0.3528                                   # mm par point
FILES = {'regular': 'BarlowCondensed-Medium.ttf', 'medium': 'BarlowCondensed-Medium.ttf',
         'semibold': 'BarlowCondensed-SemiBold.ttf', 'bold': 'BarlowCondensed-Bold.ttf'}
FAMILY = {'regular': ('Barlow Condensed', 'normal'), 'medium': ('Barlow Condensed Medium', 'normal'),
          'semibold': ('Barlow Condensed SemiBold', 'normal'), 'bold': ('Barlow Condensed', 'bold')}
CAP_HEIGHT = 0.70                             # hauteur des capitales / corps (Barlow : 700 / 1000)

_fonts = {}


def _font(weight):
    f = _fonts.get(weight)
    if f is None:
        tt = TTFont(FONT_DIR / FILES[weight])
        f = (tt.getBestCmap(), tt['hmtx'], tt['head'].unitsPerEm)
        _fonts[weight] = f
    return f


def text_width(text, weight, size_mm, spacing=0.0):
    """Largeur du texte en mm (avances des glyphes + espacement de lettres ; pas de crenage, comme Cairo)."""
    cmap, hmtx, upm = _font(weight)
    w = 0.0
    for ch in text:
        g = cmap.get(ord(ch)) or cmap.get(ord('?'))
        w += hmtx[g][0] if g else 0
    return w / upm * size_mm + spacing * len(text)


def pt(v):
    """Points -> mm."""
    return v * PT
