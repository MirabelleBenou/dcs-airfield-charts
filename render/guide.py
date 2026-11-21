"""Guide de lecture des charts : un PDF A4 portrait, en anglais, fabrique a partir des zips d'une
release. Il annote les vraies pages de Konya (LTAN, Syria), la 10-9 de Naqoura et une page de l'annuaire des heliports,
puis dit quel appareil peut voler quelle approche. La logique est ici ; la commande est `render/build_guide.py`.

- Les textes, les pastilles et leurs cibles vivent dans `render/data/guide/guide.json`, pas dans le code ; les textes fixes
  (ligne de partie, pied de page, « HOW TO USE IT », titre du PDF) sont sous `labels`.
- Une pastille vise soit un TEXTE d'une page SVG (couche + texte exact d'un element `text`, halos ignores, transformations
  appliquees ; le point dessine est choisi dans la boite du texte : `at` = start, middle ou end), soit un POINT (couche
  rendue seule : il doit y avoir de l'encre a moins de 0,5 mm), soit, pour l'annuaire (une image sans texte), un point de
  la page, sans controle de cible. Le disque d'une pastille ne recouvre aucun texte de la page source ; le point dessine
  est controle la ou il est dessine ; une pastille « zoom » dont le point sort de la zone agrandie arrete l'outil.
- Chaque chiffre cite par un texte du guide est cherche, element `text` par element, dans la page qu'il cite (frontieres de
  mot, casse ignoree).
- Le texte se mesure avec les avances reelles des glyphes (`render.fonts`), espacement des lettres compris ; un texte qui
  deborde de sa zone arrete l'outil, jamais de reduction de corps. Toute erreur de lecture (page, JPEG, donnees) est un arret
  qui nomme la page ou l'entree.
- Un arret ne produit aucun fichier. Mêmes entrees, mêmes octets.
"""
import base64
import io
import json
import math
import re
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from . import export, fonts

DATA_FILE = Path(__file__).resolve().parent / 'data' / 'guide' / 'guide.json'

W, H = 210.0, 297.0                       # A4 portrait, mm
MX0, MX1 = 12.0, 198.0                    # marges des textes
BODY_BOTTOM = 283.5                       # bas des textes (le filet du pied est a 285)
INK, INK2, RULE, PIN, BAND = '#141414', '#5A5A5A', '#BDBDBD', '#C8102E', '#F2F2F2'
FAMILY = 'Barlow Condensed'
PT = fonts.PT                             # mm par point
PAGE_W, PAGE_H = 148.0, 210.0             # page source (A5), mm
S = 0.8                                   # echelle de la page source sur les pages d'anatomie
CX, CY = 12.0, 31.0                       # coin de la page source sur la page A4
COL_X = CX + PAGE_W * S + 7.0             # colonne des legendes
LEGEND_PT, USE_PT, BODY_PT = 7.0, 9.5, 9.5
LINE_ASCENT, LINE_DESCENT = 1.0, 0.2      # boite de ligne de Barlow Condensed (hhea ascent 1000, descent -200 / 1000), en corps
INK_RADIUS_MM = 0.5                       # encre a moins de 0,5 mm d'une cible-point
INK_PX_PER_MM = 10                        # couche rendue seule a 0,1 mm par pixel
FOOTER_GAP_MM = 6.0                       # espace minimal entre les deux moities du pied de page

SVG_NS = 'http://www.w3.org/2000/svg'
INK_NS = 'http://www.inkscape.org/namespaces/inkscape'
XLINK_NS = 'http://www.w3.org/1999/xlink'
ET.register_namespace('', SVG_NS)
ET.register_namespace('inkscape', INK_NS)
ET.register_namespace('xlink', XLINK_NS)
LABEL = f'{{{INK_NS}}}label'


class GuideError(Exception):
    """Cas d'arret du cadrage 6 : l'outil s'arrete sans rien ecrire."""


# Les textes fixes du guide (ligne de partie, pied de page, « HOW TO USE IT », titre et auteur du PDF) sont dans le fichier de
# donnees (`labels`) ; `build` les pose ici le temps de la composition.
_LABELS = {}


def label(*path):
    v = _LABELS
    for k in path:
        if not isinstance(v, dict) or k not in v:
            raise GuideError(f'donnees du guide : textes fixes « labels.{".".join(path)} » manquant')
        v = v[k]
    return v


# --------------------------------------------------------------------------------------------------------- transformations
_TR = re.compile(r'(matrix|translate|rotate|scale)\s*\(([^)]*)\)')


def _mul(a, b):
    """a puis b appliques dans l'ordre SVG (a est la transformation du parent)."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def parse_transform(s):
    """Attribut `transform` -> matrice (a, b, c, d, e, f)."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in _TR.findall(s or ''):
        v = [float(x) for x in re.split(r'[\s,]+', args.strip()) if x]
        if name == 'translate':
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == 'scale':
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == 'matrix':
            t = tuple(v[:6])
        else:
            a = math.radians(v[0])
            c, s = math.cos(a), math.sin(a)
            t = (c, s, -s, c, 0, 0)
            if len(v) >= 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        m = _mul(m, t)
    return m


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


# ------------------------------------------------------------------------------------------------------------- page SVG
def _local(tag):
    return tag.rsplit('}', 1)[-1]


@dataclass
class TextEl:
    layer: str
    text: str
    x: float          # point d'ancrage dans la page, mm (transformations appliquees)
    y: float
    anchor: str
    order: int
    m: tuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)      # transformation de l'element vers la page (parents compris)
    lx: float = 0.0   # ancre locale (avant transformation)
    ly: float = 0.0
    size: float = 0.0           # corps local, mm
    weight: str = 'regular'
    spacing: float = 0.0        # espacement des lettres local, mm

    def width(self):
        """Largeur locale du texte, mm : avances reelles des glyphes + espacement des lettres."""
        return fonts.text_width(self.text, self.weight, self.size, self.spacing)

    def corners(self):
        """La boite du texte : 4 coins dans la page (mm), transformations comprises. Horizontalement : de l'ancre selon le
        `text-anchor` et la largeur mesuree ; verticalement : la boite de ligne de la police (hhea de Barlow Condensed :
        1,0 corps au-dessus de la ligne de base, 0,2 en dessous)."""
        w = self.width()
        x0 = {'middle': self.lx - w / 2, 'end': self.lx - w}.get(self.anchor, self.lx)
        top, bot = self.ly - LINE_ASCENT * self.size, self.ly + LINE_DESCENT * self.size
        return [apply(self.m, x, y) for x, y in ((x0, top), (x0 + w, top), (x0 + w, bot), (x0, bot))]

    def box(self):
        """Boite englobante (x0, y0, x1, y1) de la page, mm."""
        c = self.corners()
        xs, ys = [p[0] for p in c], [p[1] for p in c]
        return (min(xs), min(ys), max(xs), max(ys))

    def point_at(self, where):
        """Point du texte, au milieu de la hauteur des capitales (donc dans la boite) : `start`, `middle` ou `end` de la ligne ; page, mm."""
        w = self.width()
        x0 = {'middle': self.lx - w / 2, 'end': self.lx - w}.get(self.anchor, self.lx)
        fx = {'start': 0.0, 'middle': 0.5, 'end': 1.0}[where]
        return apply(self.m, x0 + fx * w, self.ly - fonts.CAP_HEIGHT * self.size / 2)


def _norm_ws(s):
    return ' '.join(s.split())


def _num(el, name, default, where):
    try:
        return float(el.attrib.get(name, default))
    except ValueError:
        raise GuideError(f'{where} : attribut {name}="{el.attrib.get(name)}" illisible')


class SvgPage:
    """Une page SVG de la release : ses elements `text` par couche, son texte, et ses couches rendues seules."""

    def __init__(self, name, svg_text):
        self.name = name
        self.svg_text = svg_text
        try:
            self.root = ET.fromstring(svg_text.encode('utf-8'))
        except ET.ParseError as e:
            raise GuideError(f'page {name} : SVG illisible ({e})')
        self._texts = None
        self._ink = {}

    def layers(self):
        return [g.attrib.get(LABEL) for g in self.root if _local(g.tag) == 'g']

    def texts(self):
        """Tous les elements `text` hors halos, dans l'ordre du document."""
        if self._texts is None:
            out = []

            def walk(e, layer, m):
                for c in e:
                    where = f'page {self.name}, couche « {layer} »'
                    try:
                        mm = _mul(m, parse_transform(c.attrib.get('transform')))
                    except (ValueError, IndexError):
                        raise GuideError(f'{where} : transformation illisible « {c.attrib.get("transform")} »')
                    if _local(c.tag) == 'text':
                        if c.attrib.get('class') == 'halo':
                            continue
                        txt = _norm_ws(''.join(c.itertext()))
                        where = f'{where}, texte « {txt} »'
                        lx, ly = _num(c, 'x', 0, where), _num(c, 'y', 0, where)
                        x, y = apply(mm, lx, ly)
                        weight = 'bold' if c.attrib.get('font-weight') in ('bold', '700', '800', '900') else 'regular'
                        out.append(TextEl(layer, txt, x, y, c.attrib.get('text-anchor', 'start'), len(out), mm, lx, ly,
                                          _num(c, 'font-size', 0, where), weight, _num(c, 'letter-spacing', 0, where)))
                    else:
                        walk(c, layer, mm)

            for g in self.root:
                if _local(g.tag) == 'g':
                    walk([g], g.attrib.get(LABEL), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
            self._texts = out
        return self._texts

    def page_text(self):
        """Le texte de la page : ses elements `text` (halos exclus), espaces normalises."""
        return _norm_ws(' '.join(t.text for t in self.texts()))

    def find_text(self, layer, text, occurrence=None, where=''):
        """L'element de la couche dont le texte est exactement `text` ; GuideError s'il manque ou s'il est ambigu."""
        found = [t for t in self.texts() if t.layer == layer and t.text == _norm_ws(text)]
        if not found:
            raise GuideError(f'{where}cible introuvable : couche « {layer} », texte « {text} » absent de la page {self.name}')
        if occurrence is None:
            if len(found) > 1:
                raise GuideError(f'{where}cible ambigue : couche « {layer} », texte « {text} » present {len(found)} fois '
                                 f'sur la page {self.name} (donner le numero d\'occurrence)')
            return found[0]
        if not 1 <= occurrence <= len(found):
            raise GuideError(f'{where}cible introuvable : occurrence {occurrence} de « {text} » (couche « {layer} », '
                             f'{len(found)} sur la page {self.name})')
        return found[occurrence - 1]

    def layer_alpha(self, layer):
        """Canal alpha de la couche rendue seule, a 0,1 mm par pixel (tableau numpy hauteur x largeur)."""
        if layer not in self._ink:
            import numpy as np
            from PIL import Image
            root = ET.fromstring(self.svg_text.encode('utf-8'))
            keep = [g for g in root if _local(g.tag) == 'g' and g.attrib.get(LABEL) == layer]
            if not keep:
                raise GuideError(f'couche « {layer} » absente de la page {self.name}')
            for g in list(root):
                if _local(g.tag) != 'defs' and g not in keep:
                    root.remove(g)
            data = ET.tostring(root, encoding='utf-8')
            export.ensure_fonts()
            import cairosvg
            png = cairosvg.svg2png(bytestring=data, output_width=int(PAGE_W * INK_PX_PER_MM),
                                   output_height=int(PAGE_H * INK_PX_PER_MM))
            self._ink[layer] = np.asarray(Image.open(io.BytesIO(png)).convert('RGBA'))[:, :, 3]
        return self._ink[layer]

    def has_ink(self, layer, x, y, radius=INK_RADIUS_MM):
        """Y a-t-il de l'encre (alpha > 0) de la couche a moins de `radius` mm du point (x, y) de la page ?"""
        import numpy as np
        a = self.layer_alpha(layer)
        r = radius * INK_PX_PER_MM
        cx, cy = x * INK_PX_PER_MM, y * INK_PX_PER_MM
        x0, x1 = max(0, int(math.floor(cx - r))), min(a.shape[1], int(math.ceil(cx + r)) + 1)
        y0, y1 = max(0, int(math.floor(cy - r))), min(a.shape[0], int(math.ceil(cy + r)) + 1)
        if x0 >= x1 or y0 >= y1:
            return False
        ys, xs = np.mgrid[y0:y1, x0:x1]
        near = (xs + 0.5 - cx) ** 2 + (ys + 0.5 - cy) ** 2 <= r * r
        return bool((a[y0:y1, x0:x1][near] > 0).any())


# ----------------------------------------------------------------------------------------------------------------- entrees
class Sources:
    """Les deux zips d'une release et les pages que le fichier de donnees en tire."""

    def __init__(self, zips, defs):
        self.defs = defs
        self.zips = {}
        for key, path in zips.items():
            try:
                self.zips[key] = zipfile.ZipFile(path)
            except (OSError, zipfile.BadZipFile) as e:
                raise GuideError(f'zip {key} illisible : {path} ({e})')
        self._members = {k: {PurePosixPath(n).relative_to(PurePosixPath(n).parts[0]).as_posix(): n
                             for n in z.namelist() if '/' in n} for k, z in self.zips.items()}
        self._svg = {}

    def member(self, name):
        d = self.defs[name]
        z = self.zips.get(d['zip'])
        if z is None:
            raise GuideError(f'page {name} : zip « {d["zip"]} » non fourni')
        full = self._members[d['zip']].get(d['file'])
        if full is None:
            raise GuideError(f'page {name} : {d["file"]} absente du zip {d["zip"]}')
        try:
            return z.read(full)
        except (zipfile.BadZipFile, zlib.error, OSError, RuntimeError, EOFError) as e:
            raise GuideError(f'page {name} : {d["file"]} illisible dans le zip {d["zip"]} ({e})')

    def svg(self, name):
        if name not in self._svg:
            try:
                text = self.member(name).decode('utf-8')
            except UnicodeDecodeError as e:
                raise GuideError(f'page {name} : le SVG n\'est pas de l\'UTF-8 ({e})')
            self._svg[name] = SvgPage(name, text)
        return self._svg[name]

    def image(self, name):
        """(octets JPEG, largeur, hauteur) d'une page-image ; les dimensions sont controlees."""
        from PIL import Image
        data = self.member(name)
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:                      # JPEG corrompu ou tronque : l'outil s'arrete en nommant la page
            raise GuideError(f'page {name} : {self.defs[name]["file"]} n\'est pas un JPEG lisible ({e})')
        want = tuple(self.defs[name]['size'])
        if im.size != want:
            raise GuideError(f'page {name} : {self.defs[name]["file"]} mesure {im.size[0]} × {im.size[1]}, '
                             f'attendu {want[0]} × {want[1]}')
        return data, im.size[0], im.size[1]


def load_data(path=DATA_FILE):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise GuideError(f'fichier de donnees du guide illisible : {path} ({e})')


# --------------------------------------------------------------------------------------------------------- texte mesure
def tw(text, weight, size_pt, spacing=0.0):
    """Largeur du texte en mm (avances reelles des glyphes de Barlow Condensed)."""
    return fonts.text_width(text, weight, size_pt * PT, spacing)


def parse_rich(s):
    """`**gras**` -> gras ; les `` `code` `` restent en police normale (pas de chasse fixe). [(texte, graisse)]"""
    s = s.replace('`', '')
    out = []
    for i, part in enumerate(s.split('**')):
        if part:
            out.append((part, 'bold' if i % 2 else 'regular'))
    return out


def _words(runs):
    """Mots de `runs` : chaque mot est une liste de morceaux (texte, graisse) (un mot peut changer de graisse)."""
    words, cur = [], []
    for text, weight in runs:
        for part in re.split(r'(\s+)', text):
            if not part:
                continue
            if part.isspace():
                if cur:
                    words.append(cur)
                    cur = []
            else:
                cur.append((part, weight))
    if cur:
        words.append(cur)
    return words


def wrap_runs(runs, width, size_pt, where, spacing=0.0):
    """Coupe `runs` a `width` mm selon les largeurs reelles des glyphes. Retourne des lignes de segments
    (graisse, texte, x en mm depuis le debut de la ligne)."""
    sp = {w: tw(' ', w, size_pt, spacing) for w in ('regular', 'bold')}
    lines, cur, cur_w = [], [], 0.0
    for word in _words(runs):
        ww = sum(tw(t, w, size_pt, spacing) for t, w in word)
        if ww > width + 1e-6:
            raise GuideError(f'{where} : le mot « {"".join(t for t, _ in word)} » ({ww:.1f} mm) est plus large que '
                             f'la zone ({width:.1f} mm)')
        add = ww + (sp[cur[-1][-1][1]] if cur else 0.0)
        if cur and cur_w + add > width + 1e-6:
            lines.append(cur)
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w += add
    if cur:
        lines.append(cur)
    out = []
    for line in lines:
        segs, x, prev = [], 0.0, None
        for k, word in enumerate(line):
            for j, (txt, w) in enumerate(word):
                if k and j == 0:
                    space = sp[prev]
                    if segs and segs[-1][0] == w and prev == w:
                        segs[-1][1] += ' ' + txt
                    else:
                        segs.append([w, txt, x + space])
                    x += space
                elif segs and segs[-1][0] == w:
                    segs[-1][1] += txt
                else:
                    segs.append([w, txt, x])
                x += tw(txt, w, size_pt, spacing)
                prev = w
        out.append([(w, t, x0) for w, t, x0 in segs])
    return out


def wrap_plain(text, width, size_pt, weight, where, spacing=0.0):
    return wrap_runs([(text, weight)], width, size_pt, where, spacing)


# ------------------------------------------------------------------------------------------------------- dessin (SVG)
def fit(where, text, weight, size_pt, limit, spacing=0.0):
    """Mesure `text` (glyphes reels, espacement des lettres compris) ; GuideError nommant `where` s'il depasse `limit` mm."""
    w = tw(text, weight, size_pt, spacing)
    if w > limit + 1e-6:
        raise GuideError(f'{where} : « {text} » deborde de sa zone ({w:.1f} mm, limite {limit:.1f} mm)')
    return w


def t_el(x, y, s, size_pt, weight='normal', fill=INK, anchor='start', spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ''
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-family="{FAMILY}" font-size="{size_pt * PT:.3f}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{ls}>{escape(s)}</text>')


def draw_lines(lines, x, y, size_pt, lead, fill=INK, spacing=None):
    """Ecrit les lignes (segments de wrap_runs) a partir de (x, y) ; retourne (elements, y apres la derniere ligne)."""
    els = []
    for line in lines:
        for weight, text, dx in line:
            els.append(t_el(x + dx, y, text, size_pt, 'bold' if weight == 'bold' else 'normal', fill, spacing=spacing))
        y += lead
    return els, y


class Page:
    """Une page A4 en cours de composition : des elements SVG et une position verticale."""

    def __init__(self, pid, part, title, cover=False):
        self.id, self.part, self.title, self.cover = pid, part, title, cover
        self.els = []
        self.number = None

    def add(self, *e):
        self.els.extend(e)


def block_text(page, name, runs, x, y, width, bottom, size_pt, lead, fill=INK, spacing=None):
    """Un bloc de texte dans la zone [y, bottom] : le texte qui deborde arrete l'outil (jamais de reduction de corps)."""
    where = f'page {page.id}, bloc « {name} »'
    lines = wrap_runs(runs, width, size_pt, where, spacing or 0.0)
    end = y + lead * (len(lines) - 1) if lines else y
    if end > bottom + 1e-6:
        raise GuideError(f'{where} : le texte deborde de sa zone ({end:.1f} mm, limite {bottom:.1f} mm ; '
                         f'{len(lines)} lignes a {size_pt} pt)')
    els, y2 = draw_lines(lines, x, y, size_pt, lead, fill, spacing)
    page.add(*els)
    return y2


# ------------------------------------------------------------------------------------------------------------- pastilles
PIN_R = 2.1 + 0.175                       # rayon exterieur d'une pastille (disque + liseré blanc), mm de la page A4
DOT_TOLERANCE_MM = 2.0                    # le point dessine : dans la boite du texte ou a 2 mm au plus de son bord
AT_CHOICES = ('start', 'middle', 'end')


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def box_distance(x, y, corners):
    """Distance (mm) du point au quadrilatere `corners` (rectangle eventuellement tourne) ; 0 s'il est dedans."""
    cr = [(corners[(i + 1) % 4][0] - corners[i][0]) * (y - corners[i][1]) -
          (corners[(i + 1) % 4][1] - corners[i][1]) * (x - corners[i][0]) for i in range(4)]
    if all(c >= -1e-9 for c in cr) or all(c <= 1e-9 for c in cr):
        return 0.0
    return min(_seg_dist(x, y, *corners[i], *corners[(i + 1) % 4]) for i in range(4))


@dataclass
class Pin:
    n: int
    on: str                 # 'page' (la page source, a S) ou 'zoom'
    kind: str               # 'text', 'point' ou 'image'
    layer: str
    aim: tuple              # point dessine, mm de la page source
    box: tuple              # quadrilatere controle (4 coins, mm de la page source) ; () si la cible n'est pas un texte
    badge: tuple            # decalage de la pastille, mm de la page A4
    title: str
    text: str
    check: str              # 'texte', 'encre' ou 'aucun'
    page_key: str
    spec_id: str
    target_text: str = ''
    at: str = ''


def check_drawn(pin, x, y):
    """Le point dessine (x, y), mm de la page source, est la ou la cible a ete controlee : dans la boite du texte ou a 2 mm
    au plus de son bord (une cible-encre : a 0,5 mm du point ou l'encre a ete trouvee). Sinon arret nomme."""
    where = f'page {pin.spec_id} ({pin.page_key}), pastille {pin.n} : '
    if pin.check == 'texte':
        d = box_distance(x, y, list(pin.box))
        if d > DOT_TOLERANCE_MM + 1e-9:
            raise GuideError(f'{where}le point dessine ({x:.2f}, {y:.2f}) est a {d:.2f} mm du texte « {pin.target_text} » '
                             f'(limite {DOT_TOLERANCE_MM} mm)')
    elif pin.check == 'encre':
        d = math.hypot(x - pin.aim[0], y - pin.aim[1])
        if d > INK_RADIUS_MM + 1e-9:
            raise GuideError(f'{where}le point dessine ({x:.2f}, {y:.2f}) est a {d:.2f} mm du point ou l\'encre de la '
                             f'couche « {pin.layer} » a ete controlee (limite {INK_RADIUS_MM} mm)')


def _frame(spec):
    """Ce qu'une page d'anatomie montre : (echelle de la page source ou None, (region, echelle) du zoom ou None)."""
    if spec['kind'] == 'zoom_only':
        return None, (list(spec['region']), spec['scale'])
    z = spec.get('zoom')
    return S, ((list(z['region']), z['scale']) if z else None)


def resolve_pins(sources, spec):
    """Resout et controle les pastilles d'une page d'anatomie : retourne la liste des Pin ; GuideError sinon."""
    key = spec['source']
    out = []
    is_image = sources.defs[key].get('size') is not None
    if is_image:
        sources.image(key)                       # le fichier existe et garde ses dimensions
    page_scale, zoom = _frame(spec)
    for p in spec['pins']:
        where = f'page {spec["id"]} ({key}), pastille {p["n"]} : '
        tg = p['target']
        on = p.get('on', 'page')
        if on not in ('page', 'zoom'):
            raise GuideError(f'{where}« on » inconnu : « {on} » (attendu : page ou zoom)')
        if on == 'zoom' and zoom is None:
            raise GuideError(f'{where}pastille « on: zoom » sur une page qui n\'a pas de zoom')
        if on == 'page' and page_scale is None:
            raise GuideError(f'{where}pastille « on: page » sur une page qui ne montre que le zoom')
        if 'nudge' in tg:
            raise GuideError(f'{where}« nudge » n\'existe plus : choisir at = start, middle ou end')
        box, tgt_text, at = (), '', ''
        if tg['kind'] == 'text':
            el = sources.svg(key).find_text(tg['layer'], tg['text'], tg.get('occurrence'), where)
            at = tg.get('at', 'middle')
            if at not in AT_CHOICES:
                raise GuideError(f'{where}« at » inconnu : « {at} » (attendu : start, middle ou end)')
            aim = el.point_at(at)
            box, tgt_text = tuple(el.corners()), el.text
            check = 'texte'
        elif tg['kind'] == 'point':
            aim = tuple(tg['at'])
            if not sources.svg(key).has_ink(tg['layer'], *aim):
                raise GuideError(f'{where}cible introuvable : pas d\'encre de la couche « {tg["layer"]} » a moins de '
                                 f'{INK_RADIUS_MM} mm du point {aim} de la page {key}')
            check = 'encre'
        elif tg['kind'] == 'image':
            if not is_image:
                raise GuideError(f'{where}cible « image » sur une page qui a un SVG')
            aim = tuple(tg['at'])
            check = 'aucun'
        else:
            raise GuideError(f'{where}sorte de cible inconnue : {tg["kind"]}')
        pin = Pin(p['n'], on, tg['kind'], tg.get('layer', ''), tuple(aim), box, tuple(p['badge']), p['title'],
                  p.get('text', ''), check, key, spec['id'], tgt_text, at)
        _check_pin_geometry(sources, spec, pin, page_scale, zoom, is_image, where)
        out.append(pin)
    return out


def _check_pin_geometry(sources, spec, pin, page_scale, zoom, is_image, where):
    """La pastille reste dans sa page (ou dans la zone agrandie) et son disque ne recoupe aucun texte de la page source."""
    if pin.on == 'zoom':
        (x0, y0, x1, y1), z = zoom
        if not (x0 <= pin.aim[0] <= x1 and y0 <= pin.aim[1] <= y1):
            raise GuideError(f'{where}le point vise ({pin.aim[0]:.2f}, {pin.aim[1]:.2f}) sort de la zone agrandie '
                             f'[{x0}, {y0}, {x1}, {y1}]')
        scale, region = z, (x0, y0, x1, y1)
    else:
        if not (0 <= pin.aim[0] <= PAGE_W and 0 <= pin.aim[1] <= PAGE_H):
            raise GuideError(f'{where}le point vise ({pin.aim[0]:.2f}, {pin.aim[1]:.2f}) sort de la page '
                             f'({PAGE_W:g} × {PAGE_H:g} mm)')
        scale, region = page_scale, (0.0, 0.0, PAGE_W, PAGE_H)
    cx, cy = pin.aim[0] + pin.badge[0] / scale, pin.aim[1] + pin.badge[1] / scale
    if pin.on == 'page' and not (0 <= cx <= PAGE_W and 0 <= cy <= PAGE_H):
        raise GuideError(f'{where}la pastille ({cx:.2f}, {cy:.2f}) sort de la page ({PAGE_W:g} × {PAGE_H:g} mm)')
    if is_image:
        return                                           # l'annuaire n'a pas de texte : rien a recouvrir
    r = PIN_R / scale
    for t in sources.svg(spec['source']).texts():
        c = t.corners()
        xs, ys = [q[0] for q in c], [q[1] for q in c]
        if max(xs) < region[0] or min(xs) > region[2] or max(ys) < region[1] or min(ys) > region[3]:
            continue                                     # texte hors de la zone montree
        if box_distance(cx, cy, c) < r:
            raise GuideError(f'{where}le disque de la pastille (centre {cx:.2f}, {cy:.2f} ; rayon {r:.2f} mm de la page) '
                             f'recouvre le texte « {t.text} » (couche « {t.layer} »)')


def check_cited(sources, cited):
    """Chaque chaine citee par un texte du guide est cherchee, element `text` par element `text`, dans la page qu'elle cite :
    casse ignoree, espaces normalises, frontieres (ni lettre, chiffre, `_` ni point avant ou apres). Retourne le nombre."""
    n = 0
    for c in cited:
        try:
            page, strings, block = c['page'], c['strings'], c['block']
        except KeyError as e:
            raise GuideError(f'chiffres cites : entree {c!r} sans la cle {e}')
        texts = [t.text for t in sources.svg(page).texts()]
        for s in strings:
            pat = re.compile(r'(?<![\w.])' + re.escape(_norm_ws(s)) + r'(?![\w.])', re.IGNORECASE)
            if not any(pat.search(t) for t in texts):
                raise GuideError(f'chiffre cite absent de sa page : « {s} » ({block}) n\'est dans aucun element de texte de '
                                 f'la page {page} : le texte du guide contredit la release')
            n += 1
    return n


class Embedder:
    """Insere les pages SVG de la release dans une page A4 (SVG imbrique), ids prefixes pour qu'ils restent uniques."""

    def __init__(self, sources):
        self.sources = sources
        self.count = 0

    def svg(self, key, x, y, w, h, viewbox='0 0 148 210'):
        self.count += 1
        pre = f'g{self.count}-'
        s = re.sub(r'^<\?xml[^>]*>\s*', '', self.sources.svg(key).svg_text)
        s = re.sub(r'\bid="([^"]+)"', lambda m: f'id="{pre}{m.group(1)}"', s)
        s = re.sub(r'url\(#([^)]+)\)', lambda m: f'url(#{pre}{m.group(1)})', s)
        s = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{pre}{m.group(1)}"', s)
        head = 'width="148mm" height="210mm" viewBox="0 0 148 210"'
        if head not in s:
            raise GuideError(f'page {key} : en-tete SVG inattendu (page A5 de 148 × 210 mm attendue)')
        return s.replace(head, f'x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" viewBox="{viewbox}"', 1)

    def image(self, key, x, y, w, h):
        data, _, _ = self.sources.image(key)
        b64 = base64.b64encode(data).decode('ascii')
        return (f'<image x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" preserveAspectRatio="none" '
                f'xlink:href="data:image/jpeg;base64,{b64}"/>')

    def source(self, key, x, y, w, h):
        """La page entiere (SVG ou image) dans le rectangle (x, y, w, h)."""
        if self.sources.defs[key].get('size') is not None:
            return self.image(key, x, y, w, h)
        return self.svg(key, x, y, w, h)


def draw_pin(pg, tx, ty, px, py, n):
    """Le trait de rappel, le point vise et la pastille numerotee (comme la maquette)."""
    if abs(px - tx) + abs(py - ty) > 0:
        pg.add(f'<line x1="{tx:.2f}" y1="{ty:.2f}" x2="{px:.2f}" y2="{py:.2f}" stroke="{PIN}" stroke-width="0.35"/>',
               f'<circle cx="{tx:.2f}" cy="{ty:.2f}" r="0.55" fill="{PIN}"/>')
    pg.add(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="2.1" fill="{PIN}" stroke="#FFFFFF" stroke-width="0.35"/>',
           t_el(px, py + 1.05, str(n), 7.2, 'bold', '#FFFFFF', 'middle'))


def place_pin(pg, p, ox, oy, rx, ry, scale):
    """Dessine la pastille `p` dont le point (rx, ry) de la page source est en (ox, oy) sur la page A4, a l'echelle `scale`.
    Le point dessine est controle la ou il est dessine ; la pastille reste dans la page A4."""
    tx, ty = ox + (p.aim[0] - rx) * scale, oy + (p.aim[1] - ry) * scale
    check_drawn(p, rx + (tx - ox) / scale, ry + (ty - oy) / scale)
    bx, by = tx + p.badge[0], ty + p.badge[1]
    if not (PIN_R <= bx <= W - PIN_R and PIN_R <= by <= H - PIN_R):
        raise GuideError(f'page {p.spec_id} ({p.page_key}), pastille {p.n} : la pastille sort de la page A4')
    draw_pin(pg, tx, ty, bx, by, p.n)


def draw_legend(pg, pins, x, y, width, limit, title_pt=7.2):
    """Colonne de legendes : pastille, titre en capitales grasses, texte en INK2. `limit` : bas de la zone (mm)."""
    for p in pins:
        where = f'page {pg.id}, legende {p.n}'
        pg.add(f'<circle cx="{x + 1.9:.2f}" cy="{y - 1.15:.2f}" r="1.9" fill="{PIN}"/>',
               t_el(x + 1.9, y - 0.2, str(p.n), 6.6, 'bold', '#FFFFFF', 'middle'),
               t_el(x + 5.5, y, p.title.upper(), title_pt, 'bold', spacing=0.15))
        if tw(p.title.upper(), 'bold', title_pt, 0.15) > width:
            raise GuideError(f'{where} : le titre « {p.title} » deborde de la colonne')
        y += 2.9
        if p.text:
            lines = wrap_runs(parse_rich(p.text), width, LEGEND_PT, where)
            els, y = draw_lines(lines, x + 5.5, y, LEGEND_PT, 2.7, INK2)
            pg.add(*els)
        y += 1.25
        if y - 1.25 - 2.7 > limit:
            raise GuideError(f'{where} : les legendes ne tiennent pas dans la colonne (bas a {y:.1f} mm, limite {limit:.1f} mm)')
    return y


def draw_use(pg, items, y0, numbered=None):
    """« HOW TO USE IT » et ses points numerotes (un seul point : un paragraphe sans numero)."""
    numbered = len(items) > 1 if numbered is None else numbered
    heading = label('how_to_use')
    fit('« how to use it »', heading, 'bold', 9, MX1 - MX0, 0.4)
    pg.add(f'<line x1="{MX0}" y1="{y0 - 5:.2f}" x2="{MX1}" y2="{y0 - 5:.2f}" stroke="{RULE}" stroke-width="0.3"/>',
           t_el(MX0, y0, heading, 9, 'bold', spacing=0.4))
    y = y0 + 6
    for i, item in enumerate(items, 1):
        runs = parse_rich(item)
        if numbered:
            pg.add(t_el(MX0, y, f'{i}.', USE_PT, 'bold', INK2))
        x = MX0 + (5 if numbered else 0)
        y = block_text(pg, f'how to use it {i}', runs, x, y, MX1 - x, BODY_BOTTOM, USE_PT, 4.1) + 1.6
    return y


def source_box(pg, emb, key, x, y, w, h):
    """La page source (SVG ou image) avec son bord RULE."""
    pg.add(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="none" stroke="{RULE}" stroke-width="0.3"/>',
           emb.source(key, x, y, w, h))


def layout_anatomy(spec, pins, emb, part_label, title):
    """Une page d'anatomie (1 ou 2 pages A4 : retourne une liste de Page)."""
    pg = Page(spec['id'], part_label, title)
    key = spec['source']
    source_box(pg, emb, key, CX, CY, PAGE_W * S, PAGE_H * S)
    zoom = spec.get('zoom')
    same = bool(zoom) and zoom.get('page', 'same') == 'same'
    zy = CY + PAGE_H * S + 9
    z = None
    if zoom:
        x0, y0, x1, y1 = zoom['region']
        z = zoom['scale']
        zw, zh = (x1 - x0) * z, (y1 - y0) * z
        zx = (W - zw) / 2
        fx0, fy0, fx1, fy1 = zoom.get('frame', zoom['region'])
        lab = zoom['frame_label']
        lw = fit(f'page {spec["id"]}, etiquette du cadre', lab, 'bold', 6.2, PAGE_W * S, 0.2)
        if zoom.get('frame_label_pos') == 'inside':
            lx, ly = CX + fx1 * S - 1.0, CY + fy0 * S + 3.4
            bg = f'<rect x="{lx - lw - 0.8:.2f}" y="{ly - 2.2:.2f}" width="{lw + 1.8:.2f}" height="3.2" fill="#FFFFFF"/>'
        else:
            lx, ly = CX + fx1 * S, CY + fy0 * S - 1.4
            bg = (f'<rect x="{lx - lw - 0.6:.2f}" y="{ly - 2.0:.2f}" width="{lw + 1.2:.2f}" height="2.7" fill="#FFFFFF" '
                  f'fill-opacity="0.9"/>')
        pg.add(bg,
               f'<rect x="{CX + fx0 * S:.2f}" y="{CY + fy0 * S:.2f}" width="{(fx1 - fx0) * S:.2f}" '
               f'height="{(fy1 - fy0) * S:.2f}" fill="none" stroke="{PIN}" stroke-width="0.3" stroke-dasharray="1 0.8"/>',
               t_el(lx, ly, lab, 6.2, 'bold', PIN, 'end', spacing=0.2))
        if same:
            fit(f'page {spec["id"]}, legende du zoom', zoom['caption'], 'bold', 7.5, MX1 - MX0, 0.4)
            pg.add(t_el(MX0, zy - 2.2, zoom['caption'], 7.5, 'bold', INK2, spacing=0.4),
                   f'<rect x="{zx:.2f}" y="{zy:.2f}" width="{zw:.2f}" height="{zh:.2f}" fill="#FFFFFF" stroke="{RULE}" '
                   f'stroke-width="0.3"/>',
                   emb.svg(key, zx, zy, zw, zh, f'{x0} {y0} {x1 - x0} {y1 - y0}'))
    for p in pins:
        if p.on == 'zoom':
            if not same:
                continue
            place_pin(pg, p, zx, zy, x0, y0, z)
        else:
            place_pin(pg, p, CX, CY, 0.0, 0.0, S)
    col = [p for p in pins if p.on == 'page' or same]
    draw_legend(pg, col, COL_X, CY + 1.5, MX1 - COL_X - 5.5, CY + PAGE_H * S + 1)
    y_use = (zy + zh + 12) if same else (CY + PAGE_H * S + 14)
    draw_use(pg, spec['use'], y_use)
    pages = [pg]
    if zoom and not same:
        pages.append(layout_zoom_page(spec, pins, emb, part_label, title, zoom))
    return pages


def layout_zoom_page(spec, pins, emb, part_label, title, zoom):
    """Page suivante d'une anatomie : l'encadre agrandi, ses pastilles et leurs legendes."""
    pg = Page(spec['id'], part_label, title)
    key = spec['source']
    x0, y0, x1, y1 = zoom['region']
    z = zoom['scale']
    zw, zh = (x1 - x0) * z, (y1 - y0) * z
    zx, zy = (W - zw) / 2, 40.0
    fit(f'page {spec["id"]}, legende du zoom', zoom['caption'], 'bold', 7.5, MX1 - MX0, 0.4)
    pg.add(t_el(MX0, zy - 2.2, zoom['caption'], 7.5, 'bold', INK2, spacing=0.4),
           f'<rect x="{zx:.2f}" y="{zy:.2f}" width="{zw:.2f}" height="{zh:.2f}" fill="#FFFFFF" stroke="{RULE}" '
           f'stroke-width="0.3"/>',
           emb.svg(key, zx, zy, zw, zh, f'{x0} {y0} {x1 - x0} {y1 - y0}'))
    zp = [p for p in pins if p.on == 'zoom']
    for p in zp:
        place_pin(pg, p, zx, zy, x0, y0, z)
    y = zy + zh + 12
    half = (MX1 - MX0 - 8) / 2
    for i in range(0, len(zp), 2):
        row = [draw_legend(pg, [p], MX0 + k * (half + 8), y, half - 5.5, BODY_BOTTOM) for k, p in enumerate(zp[i:i + 2])]
        y = max(row)
    return pg


# ------------------------------------------------------------------------------------------------ autres sortes de pages
def thumb(pg, emb, key, x, y, w):
    """Vignette d'une page source avec son bord ; retourne sa hauteur."""
    h = w * PAGE_H / PAGE_W
    source_box(pg, emb, key, x, y, w, h)
    return h


def layout_cover(cfg, emb, edition):
    pg = Page('cover', '', '', cover=True)
    ph = H
    pw = ph * PAGE_W / PAGE_H
    pg.add(f'<g opacity="{cfg["background_opacity"]}">', emb.svg(cfg['background'], (W - pw) / 2, 0, pw, ph), '</g>')
    pg.add(f'<rect x="{MX0}" y="86" width="2.2" height="52" fill="{PIN}"/>')
    y = 100.0
    y = block_text(pg, 'title', [(cfg['title'], 'bold')], MX0 + 8, y, 120, 140, 38, 15.0, INK)
    y = block_text(pg, 'cover', [(cfg['subtitle'], 'regular')], MX0 + 8, y + 2, 160, 160, 16, 6.8, INK2)
    line = cfg['line'].replace('<TAG>', edition)
    block_text(pg, 'line', [(line, 'regular')], MX0 + 8, y + 8, 160, 190, 11, 5.0, INK2)
    return [pg]


def layout_text(spec, part_label, title):
    """Page de texte (chapitre 1) : des paragraphes, un gras d'attaque possible."""
    pg = Page(spec['id'], part_label, title)
    y = 40.0
    for i, para in enumerate(spec['paragraphs'], 1):
        y = block_text(pg, f'paragraphe {i}', parse_rich(para), MX0, y, MX1 - MX0, BODY_BOTTOM, 12, 5.5) + 4.6
    return [pg]


def layout_strip(spec, emb, part_label, title):
    """Chapitre 2 : une bande de vignettes, chacune avec son code et sa ligne, puis le texte."""
    pg = Page(spec['id'], part_label, title)
    n = len(spec['thumbs'])
    gap = 4.0
    w = (MX1 - MX0 - gap * (n - 1)) / n
    y0 = 40.0
    h = w * PAGE_H / PAGE_W
    ybot = y0 + h
    for i, th in enumerate(spec['thumbs']):
        x = MX0 + i * (w + gap)
        thumb(pg, emb, th['source'], x, y0, w)
        y = y0 + h + 6.0
        where = f'page {pg.id}, vignette {th["code"]}'
        fit(where + ', code', th['code'], 'bold', 11, w)
        pg.add(t_el(x, y, th['code'], 11, 'bold', INK))
        y += 4.4
        fit(where + ', nom', th['name'].upper(), 'bold', 7.2, w, 0.2)
        pg.add(t_el(x, y, th['name'].upper(), 7.2, 'bold', INK2, spacing=0.2))
        y += 4.0
        lines = wrap_runs([(th['line'], 'regular')], w, 8, f'page {pg.id}, vignette {th["code"]}')
        els, y = draw_lines(lines, x, y, 8, 3.5, INK)
        pg.add(*els)
        ybot = max(ybot, y)
    block_text(pg, 'texte sous la bande', parse_rich(spec['text']), MX0, ybot + 12, MX1 - MX0, BODY_BOTTOM, 12, 5.5)
    return [pg]


def layout_flight(spec, emb, part_label, title):
    """Chapitre 10 : les etapes d'un vol, chacune a cote de la ou des vignettes des pages qu'elle utilise."""
    pg = Page(spec['id'], part_label, title)
    cols, rows = 2, 4
    gap_x = 10.0
    cw = (MX1 - MX0 - gap_x) / cols
    tw_img = 32.0
    rh = 62.0
    y0 = 36.0
    for i, st in enumerate(spec['steps']):
        cx = MX0 + (i % cols) * (cw + gap_x)
        cy = y0 + (i // cols) * rh
        keys = st['thumbs']
        if len(keys) == 1:
            ih = thumb(pg, emb, keys[0], cx, cy, tw_img)
        else:
            k = 2
            ww = (tw_img - 2.0) / k
            ih = 0.0
            for j, key in enumerate(keys):
                hh = thumb(pg, emb, key, cx + (j % k) * (ww + 2.0), cy + (j // k) * (ww * PAGE_H / PAGE_W + 2.0), ww)
                ih = max(ih, (j // k) * (ww * PAGE_H / PAGE_W + 2.0) + hh)
        x = cx + tw_img + 5.0
        width = cw - tw_img - 5.0
        runs = parse_rich(st['text'])
        if cy + ih > BODY_BOTTOM:
            raise GuideError(f'page {pg.id}, etape {i + 1} : les vignettes passent sous le pied de page '
                             f'({cy + ih:.1f} mm, limite {BODY_BOTTOM:.1f} mm)')
        pg.add(t_el(x, cy + 3.5, f'{i + 1}', 20, 'bold', PIN))
        block_text(pg, f'etape {i + 1}', runs, x, cy + 11.0, width, min(cy + rh - 6, BODY_BOTTOM), 10.5, 4.6)
    return [pg]


def layout_overview(spec, part_label, title):
    """11.0 : le tableau des appareils et sa note."""
    pg = Page(spec['id'], part_label, title)
    fit(f'page {pg.id}, titre du tableau', spec['caption'].upper(), 'bold', 8, MX1 - MX0, 0.4)
    pg.add(t_el(MX0, 38, spec['caption'].upper(), 8, 'bold', INK2, spacing=0.4))
    xs = spec['col_x']
    widths = [xs[i + 1] - xs[i] - 3 for i in range(len(xs) - 1)] + [MX1 - xs[-1] - 1]
    y = 42.0
    head_h = 12.0
    pg.add(f'<rect x="{MX0}" y="{y}" width="{MX1 - MX0}" height="{head_h}" fill="{BAND}"/>')
    for x, wd, hd in zip(xs, widths, spec['header']):
        lines = wrap_runs([(hd, 'bold')], wd - 1.5, 8.5, f'page {pg.id}, en-tete « {hd} »')
        first = y + 5.0 - (len(lines) - 1) * 1.8
        if first < y + 3.0 or first + (len(lines) - 1) * 3.6 > y + head_h - 1.0:
            raise GuideError(f'page {pg.id}, en-tete « {hd} » : {len(lines)} lignes ne tiennent pas dans la bande '
                             f'de {head_h:g} mm')
        els, _ = draw_lines(lines, x + 1.5, first, 8.5, 3.6, INK)
        pg.add(*els)
    y += head_h
    pg.add(f'<line x1="{MX0}" y1="{y}" x2="{MX1}" y2="{y}" stroke="{INK}" stroke-width="0.4"/>')
    row_h = 17.0
    for r, row in enumerate(spec['rows']):
        for c, (x, wd, cell) in enumerate(zip(xs, widths, row)):
            runs = [(cell, 'bold' if c == 0 else 'regular')]
            lines = wrap_runs(runs, wd - 1.5, 10.5, f'page {pg.id}, cellule « {cell} »')
            if (len(lines) - 1) * 4.6 > row_h - 6:
                raise GuideError(f'page {pg.id}, cellule « {cell} » : le texte deborde de sa cellule')
            els, _ = draw_lines(lines, x + 1.5, y + row_h / 2 + 1.3 - (len(lines) - 1) * 2.3, 10.5, 4.6,
                                INK if c == 0 or cell not in ('—',) else INK2)
            pg.add(*els)
        y += row_h
        if y > BODY_BOTTOM:
            raise GuideError(f'page {pg.id}, tableau : la ligne {r + 1} passe sous le pied de page ({y:.1f} mm)')
        pg.add(f'<line x1="{MX0}" y1="{y}" x2="{MX1}" y2="{y}" stroke="{RULE}" stroke-width="0.25"/>')
    block_text(pg, 'note sous le tableau', parse_rich(spec['note']), MX0, y + 9, MX1 - MX0, BODY_BOTTOM, 11, 5.0, INK2)
    return [pg]


def layout_fiche(spec, emb, part_label, title):
    """11.1 a 11.9 : le texte de la fiche a gauche, la page d'exemple reduite (sans pastilles) a droite."""
    pg = Page(spec['id'], part_label, title)
    ex = spec.get('example')
    img_w = 88.0
    width = (MX1 - MX0 - img_w - 8) if ex else (MX1 - MX0)
    y = 38.0
    for i, item in enumerate(spec['items'], 1):
        size = 11 if ex else 13
        y = block_text(pg, f'fiche {i}', parse_rich(item), MX0, y, width, BODY_BOTTOM, size, size * 0.455) + 4.0
    if ex:
        x = MX1 - img_w
        h = thumb(pg, emb, ex, x, 38.0, img_w)
        fit(f'page {pg.id}, legende de la vignette', spec['example_caption'], 'regular', 7.5, img_w)
        if 38.0 + h + 4.5 > BODY_BOTTOM:
            raise GuideError(f'page {pg.id}, vignette : sa legende passe sous le pied de page')
        pg.add(t_el(x, 38.0 + h + 4.5, spec['example_caption'], 7.5, 'normal', INK2))
    return [pg]


def layout_annex(spec, part_label, title):
    pg = Page(spec['id'], part_label, title)
    y = 40.0
    for sec in spec['sections']:
        fit(f'page {pg.id}, titre de section', sec['heading'], 'bold', 14, MX1 - MX0)
        pg.add(t_el(MX0, y, sec['heading'], 14, 'bold', INK))
        pg.add(f'<line x1="{MX0}" y1="{y + 2.2:.2f}" x2="{MX1}" y2="{y + 2.2:.2f}" stroke="{RULE}" stroke-width="0.3"/>')
        y += 9.0
        for i, para in enumerate(sec['paragraphs'], 1):
            if isinstance(para, list):                       # abreviations : [[sigle, sens], ...]
                runs = []
                for k, (sig, meaning) in enumerate(para):
                    runs.append((sig, 'bold'))
                    runs.append((' ' + meaning + (' · ' if k < len(para) - 1 else ''), 'regular'))
            else:
                runs = parse_rich(para)
            y = block_text(pg, f'{sec["heading"]} {i}', runs, MX0, y, MX1 - MX0, BODY_BOTTOM, 11, 5.0) + 3.5
        y += 8
    return [pg]


def layout_zoom_only(spec, pins, emb, part_label, title):
    """Chapitre 9 : l'encadre seul, agrandi, ses pastilles, leurs legendes, puis le texte du chapitre."""
    pg = Page(spec['id'], part_label, title)
    key = spec['source']
    x0, y0, x1, y1 = spec['region']
    z = spec['scale']
    zw, zh = (x1 - x0) * z, (y1 - y0) * z
    zx, zy = MX0, CY + 1.0
    if spec.get('caption'):
        fit(f'page {spec["id"]}, legende du zoom', spec['caption'], 'bold', 7.5, MX1 - MX0, 0.4)
        pg.add(t_el(MX0, zy - 2.4, spec['caption'], 7.5, 'bold', INK2, spacing=0.4))
    pg.add(f'<rect x="{zx:.2f}" y="{zy:.2f}" width="{zw:.2f}" height="{zh:.2f}" fill="#FFFFFF" stroke="{RULE}" '
           f'stroke-width="0.3"/>', emb.svg(key, zx, zy, zw, zh, f'{x0} {y0} {x1 - x0} {y1 - y0}'))
    for p in pins:
        place_pin(pg, p, zx, zy, x0, y0, z)
    lx = zx + zw + 7.0
    draw_legend(pg, pins, lx, zy + 1.5, MX1 - lx - 5.5, zy + zh)
    block_text(pg, 'texte du chapitre', parse_rich(spec['text']), MX0, zy + zh + 14, MX1 - MX0, BODY_BOTTOM, 11, 5.0)
    return [pg]


def layout_contents(spec, entries, part_label, title):
    """Sommaire d'une page : les chapitres et annexes avec leur numero de page (calcule)."""
    pg = Page(spec['id'], part_label, title)
    y = 42.0
    for grp in spec['groups']:
        fit(f'page {pg.id}, titre de groupe', grp['heading'].upper(), 'bold', 9, MX1 - MX0, 0.5)
        pg.add(t_el(MX0, y, grp['heading'].upper(), 9, 'bold', INK2, spacing=0.5))
        y += 3.0
        pg.add(f'<line x1="{MX0}" y1="{y:.2f}" x2="{MX1}" y2="{y:.2f}" stroke="{INK}" stroke-width="0.4"/>')
        y += 8.0
        for e in grp['entries']:
            num, name, ref = e['num'], e['name'], e['ref']
            if ref not in entries:
                raise GuideError(f'page {pg.id}, ligne « {name} » : la page « {ref} » n\'existe pas dans le guide')
            page_no = entries[ref]
            fit(f'page {pg.id}, numero de ligne', num, 'bold', 12, 12.0)
            if y > BODY_BOTTOM:
                raise GuideError(f'page {pg.id}, ligne « {name} » : le sommaire passe sous le pied de page '
                                 f'({y:.1f} mm, limite {BODY_BOTTOM:.1f} mm)')
            right = tw(str(page_no), 'bold', 12)
            if tw(name, 'regular', 12) + 14 > MX1 - MX0 - right - 6:
                raise GuideError(f'page {pg.id}, ligne « {name} » : trop longue pour le sommaire')
            pg.add(t_el(MX0, y, num, 12, 'bold', PIN), t_el(MX0 + 14, y, name, 12, 'normal', INK),
                   t_el(MX1, y, str(page_no), 12, 'bold', INK, 'end'))
            x0 = MX0 + 14 + tw(name, 'regular', 12) + 2
            pg.add(f'<line x1="{x0:.2f}" y1="{y - 0.5:.2f}" x2="{MX1 - right - 2:.2f}" y2="{y - 0.5:.2f}" stroke="{RULE}" '
                   f'stroke-width="0.35" stroke-dasharray="0.3 1.2" stroke-linecap="round"/>')
            y += 8.2
        y += 8.0
    return [pg]


# ----------------------------------------------------------------------------------------------------------- assemblage
def footer_texts(edition):
    """Les deux moities du pied de page ; elles ne se recoupent pas, quelle que soit la longueur de l'edition."""
    left = label('footer_left').format(tag=edition)
    right = label('footer_right')
    wl, wr = tw(left, 'regular', 7), tw(right, 'bold', 7)
    if wl + wr + FOOTER_GAP_MM > MX1 - MX0:
        raise GuideError(f'pied de page : les deux moities ({wl:.1f} + {wr:.1f} mm) ne tiennent pas dans '
                         f'{MX1 - MX0:.0f} mm avec l\'edition « {edition} » ; edition trop longue')
    return left, right


def page_svg(pg, number, edition):
    """La page A4 complete en SVG : en-tete, contenu, pied (sauf la couverture, qui n'en a pas)."""
    e = [f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" width="{W:g}mm" height="{H:g}mm" viewBox="0 0 {W:g} {H:g}">',
         f'<rect width="{W:g}" height="{H:g}" fill="#FFFFFF"/>']
    footer = footer_texts(edition)
    if not pg.cover:
        if tw(pg.title, 'bold', 20) > MX1 - MX0 - 16:
            raise GuideError(f'page {pg.id}, bloc « titre » : « {pg.title} » deborde de sa zone '
                             f'({tw(pg.title, "bold", 20):.1f} mm, limite {MX1 - MX0 - 16:.1f} mm)')
        fit(f'page {pg.id}, ligne de partie', pg.part, 'regular', 8, MX1 - MX0, 0.6)
        e += [t_el(MX0, 14, pg.part, 8, 'normal', INK2, spacing=0.6), t_el(MX0, 22.5, pg.title, 20, 'bold'),
              t_el(MX1, 22.5, str(number), 20, 'bold', INK2, 'end'),
              f'<line x1="{MX0}" y1="25.5" x2="{MX1}" y2="25.5" stroke="{INK}" stroke-width="0.5"/>']
    e += pg.els
    if not pg.cover:
        e += [f'<line x1="{MX0}" y1="285" x2="{MX1}" y2="285" stroke="{INK}" stroke-width="0.3"/>',
              t_el(MX0, 289.5, footer[0], 7, 'normal', INK2),
              t_el(MX1, 289.5, footer[1], 7, 'bold', INK, 'end')]
    e.append('</svg>')
    return '\n'.join(e)


@dataclass
class Guide:
    pdf: bytes
    n_pages: int
    pins: list
    cited: int
    edition: str
    toc: dict


def _title(spec):
    return f'{spec["num"]}. {spec["title"]}' if spec.get('num') else spec['title']


def _part(spec):
    return label('parts', str(spec['part']))


def _why(e):
    return f'cle manquante {e}' if isinstance(e, KeyError) else f'{type(e).__name__} : {e}'


def build(zips, edition, data=None):
    """Compose le guide : retourne un Guide (le PDF en octets) ou leve GuideError ; n'ecrit rien. Toute erreur de lecture
    d'une page de la release ou des donnees devient une GuideError qui nomme la page ou l'entree."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', edition or ''):
        raise GuideError(f'edition invalide : « {edition} » (attendu : une version comme AC-V1.9.0)')
    data = data or load_data()
    try:
        _LABELS.clear()
        _LABELS.update(data['labels'])
    except (KeyError, TypeError, ValueError) as e:
        raise GuideError(f'donnees du guide : textes fixes « labels » illisibles ({_why(e)})')
    try:
        return _build(zips, edition, data)
    finally:
        _LABELS.clear()


def _build(zips, edition, data):
    try:
        defs, specs = data['sources'], data['pages']
        sources = Sources(zips, defs)
        for name in defs:                                 # une page absente des zips arrete l'outil en la nommant
            try:
                sources.member(name)
            except (KeyError, TypeError) as e:
                raise GuideError(f'source {name} : entree illisible ({_why(e)})')
    except (KeyError, TypeError, ValueError) as e:
        raise GuideError(f'donnees du guide illisibles ({_why(e)})')
    footer_texts(edition)                                 # les deux moities du pied de page tiennent
    emb = Embedder(sources)
    pages, refs, pins, cited_n = [], {}, [], 0
    contents_at = None
    for spec in specs:
        pid = spec.get('id', '?') if isinstance(spec, dict) else '?'
        try:
            kind = spec['kind']
            if kind == 'contents':
                contents_at = len(pages)
                pages.append(None)
                continue
            if kind == 'cover':
                made = layout_cover(spec, emb, edition)
            elif kind == 'text':
                made = layout_text(spec, _part(spec), _title(spec))
            elif kind == 'strip':
                made = layout_strip(spec, emb, _part(spec), _title(spec))
            elif kind == 'anatomy':
                ps = resolve_pins(sources, spec)
                pins += ps
                made = layout_anatomy(spec, ps, emb, _part(spec), _title(spec))
            elif kind == 'zoom_only':
                ps = resolve_pins(sources, spec)
                pins += ps
                made = layout_zoom_only(spec, ps, emb, _part(spec), _title(spec))
            elif kind == 'flight':
                made = layout_flight(spec, emb, _part(spec), _title(spec))
            elif kind == 'overview':
                made = layout_overview(spec, _part(spec), _title(spec))
            elif kind == 'fiche':
                made = layout_fiche(spec, emb, _part(spec), _title(spec))
            elif kind == 'annex':
                made = layout_annex(spec, _part(spec), _title(spec))
            else:
                raise GuideError(f'page {pid} : sorte de page inconnue : {kind}')
            cited_n += check_cited(sources, spec.get('cited', []))
            for r in spec.get('refs', [spec['id']]):
                refs[r] = len(pages) + 1
            pages += made
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
            raise GuideError(f'page {pid} : donnees illisibles ({_why(e)})')
    if contents_at is None:
        raise GuideError('le fichier de donnees n\'a pas de sommaire')
    cspec = specs[[s['kind'] for s in specs].index('contents')]
    try:
        pages[contents_at] = layout_contents(cspec, refs, label('parts', 'contents'), cspec['title'])[0]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
        raise GuideError(f'page {cspec.get("id", "contents")} : donnees illisibles ({_why(e)})')
    svgs = [page_svg(pg, i + 1, edition) for i, pg in enumerate(pages)]
    return Guide(render_pdf(svgs), len(pages), pins, cited_n, edition, refs)


def render_pdf(svgs):
    """Une page A4 SVG -> PDF par CairoSVG (images re-encodees en JPEG), assemblees par pypdf ; metadonnees fixes."""
    from pypdf import PdfReader, PdfWriter
    export.ensure_fonts()
    import cairosvg
    writer = PdfWriter()
    with tempfile.TemporaryDirectory() as td:
        for i, svg in enumerate(svgs):
            p = Path(td) / f'p{i:02d}.pdf'
            try:
                cairosvg.svg2pdf(bytestring=svg.encode('utf-8'), write_to=str(p))
            except Exception as e:                  # une image incorporee illisible, un SVG que Cairo refuse
                raise GuideError(f'page {i + 1} du guide : rendu impossible ({type(e).__name__} : {e})')
            if '<image' in svg or 'data:image' in svg:
                try:
                    export.jpeg_images_in_pdf(p)
                except Exception as e:
                    raise GuideError(f'page {i + 1} du guide : images illisibles ({type(e).__name__} : {e})')
            for page in PdfReader(str(p)).pages:
                writer.add_page(page)
    writer.add_metadata({'/Title': label('pdf_title'), '/Author': label('pdf_author')})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def summary(g):
    by = {k: sum(1 for p in g.pins if p.check == k) for k in ('texte', 'encre', 'aucun')}
    return (f'Reading guide {g.edition} : {g.n_pages} pages A4\n'
            f'  pastilles : {len(g.pins)} (controlees par le texte : {by["texte"]} ; par l\'encre : {by["encre"]} ; '
            f'sans controle, annuaire : {by["aucun"]})\n'
            f'  chiffres cites verifies sur leur page : {g.cited}\n'
            f'  taille : {len(g.pdf) / 1e6:.2f} Mo ({len(g.pdf)} octets)')


def pins_report(g):
    """Une ligne par pastille : page, numero, sorte de cible, boite controlee (cible-texte : le rectangle du texte, mm de la
    page source, englobant s'il est tourne), point dessine et son ecart a la boite (0 : dedans)."""
    rows = ['page\tsource\tn\tcible\tboite_controlee_mm\tpoint_dessine_mm\tecart_mm']
    for p in g.pins:
        what = {'text': 'texte', 'point': 'encre', 'image': 'page-image'}[p.kind]
        lay = f' {p.layer}' if p.layer else ''
        if p.check == 'texte':
            xs, ys = [c[0] for c in p.box], [c[1] for c in p.box]
            box = f'({min(xs):.2f}, {min(ys):.2f}, {max(xs):.2f}, {max(ys):.2f})'
            gap = f'{box_distance(p.aim[0], p.aim[1], list(p.box)):.2f}'
            what += f'{lay} « {p.target_text} » ({p.at})'
        elif p.check == 'encre':
            box, gap, what = f'encre a moins de {INK_RADIUS_MM} mm', '0.00', what + lay
        else:
            box, gap = 'sans controle (annuaire)', '-'
        rows.append(f'{p.spec_id}\t{p.page_key}\t{p.n}\t{what}\t{box}\t({p.aim[0]:.2f}, {p.aim[1]:.2f})\t{gap}')
    return '\n'.join(rows) + '\n'
