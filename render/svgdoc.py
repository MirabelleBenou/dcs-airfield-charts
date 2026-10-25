"""Construction du SVG : calques Inkscape (addendum 1), primitives, texte."""
import re
from xml.sax.saxutils import escape

from . import fonts

INK, INK2 = '#1A1A1A', '#5A5A5A'
PAVE, BUILD, BAND, TAXI = '#CCCCCC', '#4D4D4D', '#F2F2F2', '#FFCC00'
NS_INKSCAPE = 'http://www.inkscape.org/namespaces/inkscape'

LAYERS = ['Background', 'Pavement', 'Runways', 'Runway markings', 'Buildings', 'Navaids', 'Parking', 'Parking numbers',
          'Runway bearings', 'Taxiway labels', 'Insets', 'Frame & grid', 'Header', 'Runway table', 'Footer']
CLIPPED = set(LAYERS[1:10])          # calques 2 a 10 : decoupes au cadre du plan


def f(v, nd=3):
    s = f'{v:.{nd}f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def slug(label):
    return 'layer-' + re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')


def text(x, y, s, size, weight='regular', fill=INK, anchor='start', spacing=0.0, extra=''):
    fam, w = fonts.FAMILY[weight]
    attrs = f'font-family="{fam}" font-size="{f(size)}" fill="{fill}"'
    if w == 'bold':
        attrs += ' font-weight="bold"'
    if anchor != 'start':
        attrs += f' text-anchor="{anchor}"'
    if spacing:
        attrs += f' letter-spacing="{f(spacing)}"'
        if anchor == 'middle':
            x += spacing / 2          # Cairo ajoute l'espacement apres chaque lettre, y compris la derniere
        elif anchor == 'end':
            x += spacing
    return f'<text x="{f(x)}" y="{f(y)}" {attrs}{(" " + extra) if extra else ""}>{escape(s)}</text>'


def rect(x, y, w, h, fill='none', stroke=None, sw=0.25, extra=''):
    a = f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" fill="{fill}"'
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{f(sw)}"'
    return a + (f' {extra}' if extra else '') + '/>'


def line(x1, y1, x2, y2, stroke=INK, sw=0.25, extra=''):
    return (f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" stroke="{stroke}" stroke-width="{f(sw)}"'
            + (f' {extra}' if extra else '') + '/>')


def circle(cx, cy, r, fill='none', stroke=None, sw=0.25, extra=''):
    a = f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{fill}"'
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{f(sw)}"'
    return a + (f' {extra}' if extra else '') + '/>'


def path_d(rings, nd=2):
    """Contours (listes de (X, Y) mm) -> attribut d d'un seul <path>."""
    parts = []
    for r in rings:
        if len(r) < 3:
            continue
        pts = ' '.join(f'{f(a, nd)} {f(b, nd)}' for a, b in r)
        parts.append(f'M{pts}Z')
    return ''.join(parts)


class Doc:
    """Page SVG A5 : chaque enfant direct de <svg> hors <defs> est un calque Inkscape."""

    def __init__(self, width=148.0, height=210.0, title=''):
        self.w, self.h, self.title = width, height, title
        self.defs = []
        self.layers = {}
        self.clip_id = None

    def layer(self, label):
        return self.layers.setdefault(label, [])

    def add(self, label, s):
        self.layer(label).append(s)

    def add_clip(self, cid, x, y, w, h):
        self.defs.append(f'<clipPath id="{cid}"><rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}"/></clipPath>')
        self.clip_id = cid

    def render(self):
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'xmlns:inkscape="{NS_INKSCAPE}" width="{f(self.w)}mm" height="{f(self.h)}mm" '
               f'viewBox="0 0 {f(self.w)} {f(self.h)}">']
        if self.defs:
            out.append('<defs>' + ''.join(self.defs) + '</defs>')
        for label in LAYERS:
            content = self.layers.get(label)
            if not content:
                continue
            clip = f' clip-path="url(#{self.clip_id})"' if (label in CLIPPED and self.clip_id) else ''
            out.append(f'<g inkscape:groupmode="layer" id="{slug(label)}" inkscape:label="{escape(label)}"{clip}>')
            out.extend(content)
            out.append('</g>')
        out.append('</svg>')
        return '\n'.join(out)
