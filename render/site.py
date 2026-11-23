"""Site statique d'Airfield Charts (lot ac-site-pages) : fabrique, a partir des zips d'une release, les pages a publier sur
GitHub Pages. La logique est ici ; la commande est `render/build_site.py`.

- Les charts, les terrains et les helistations viennent de `render/fiches.py`, appele tel quel.
- Le site est ecrit dans un dossier temporaire puis renomme en `--out` ; au premier cas d'erreur rien ne reste.
- Sortie deterministe : les memes entrees donnent les memes octets.
"""
import hashlib
import html
import io
import json
import os
import posixpath
import re
import shutil
import tempfile
import unicodedata
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from PIL import Image

from . import dcsdata, directory, fiches, geo, guide

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / 'data' / 'site'
MARQUE = DATA / 'marque'
FONTS = Path(__file__).resolve().parent / 'fonts'
INSIGNE = Path(__file__).resolve().parent / 'data' / 'marque' / 'insigne-airfield-charts.svg'
OFL_BARLOW = FONTS / 'OFL.txt'
OFL_OSWALD = Path(__file__).resolve().parent / 'data' / 'marque' / 'OFL-Oswald.txt'
BARLOW = ('BarlowCondensed-Regular.ttf', 'BarlowCondensed-Medium.ttf', 'BarlowCondensed-SemiBold.ttf', 'BarlowCondensed-Bold.ttf')

MAX_SITE_BYTES = 900 * 1024 * 1024
LARGE_PX, LARGE_Q = 1400, 80
THUMB_PX, THUMB_Q = 240, 70
SITE_TITLE = 'DCS Airfield Charts'
GUIDE_PDF = 'DCS_Airfield_Charts_Reading_Guide.pdf'
_TAG = re.compile(r'^AC-V\d+\.\d+\.\d+$')
_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class SiteError(Exception):
    """Cas d'arret du cadrage : l'outil s'arrete sans rien laisser."""


@dataclass(frozen=True)
class Theatre:
    pydcs: str          # nom pydcs, et des zips
    folder: str         # dossier du site et de `downloads/`
    title: str
    navaids: str        # fichier de `downloads/navaids/`
    tile_stem: str      # chart dont la page 10-9 fait la tuile de l'accueil
    tile_page: str


THEATRES = (
    Theatre('Syria', 'syria', 'Syria', 'Syria.json', 'LCRA_Akrotiri', '10-9'),
    Theatre('GermanyColdWar', 'germanycoldwar', 'Cold War Germany', 'GermanyCW.json', 'ETAR_Ramstein', '10-9'),
)


def zip_name(theatre, kind):
    return f'DCS_Airfield_Charts_{theatre.pydcs}_1985_{kind}.zip'


@dataclass
class Base:
    stem: str
    name: str
    icao: str
    alias: str
    kind: str                       # 'af' ou 'heli'
    pages: list                     # [(suffixe, ref)] dans l'ordre du PDF
    slug: str = ''
    pdf: str = ''
    chips: list = field(default_factory=list)
    lotatc: list = field(default_factory=list)      # [(libelle, nom de fichier)]


@dataclass
class Heliport:
    name: str
    slug: str
    pads: int
    pos: str
    town: str
    page_no: int
    ref: tuple
    mates: list = field(default_factory=list)       # les autres heliports de la meme page : [Heliport]


@dataclass
class TheatreData:
    theatre: Theatre
    bases: list
    heliports: list
    dir_pages: dict                 # {numero: ref du JPEG} des pages de l'annuaire montrees
    lotatc_bytes: dict              # {nom de fichier: octets}
    zip_sizes: dict                 # {'airfields': octets, ...}


# ---------- noms, ordre, slugs ----------
_STEM = re.compile(r'^(?P<p>[A-Z0-9]{4})_(?P<rest>.+)$')


def split_name(stem, header_texts):
    """(OACI, nom) : `<P>_<reste>` avec <P> de quatre caracteres [A-Z0-9] ET un texte du calque Header valant exactement <P>
    donne l'OACI <P> et le nom <reste> ; sinon pas d'OACI et le nom est le stem ; `_` devient une espace."""
    m = _STEM.match(stem)
    if m and m['p'] in set(header_texts):
        return m['p'], m['rest'].replace('_', ' ')
    return '', stem.replace('_', ' ')


def header_texts(svg_bytes):
    """Les textes (un par element `text`) du calque `Header` d'un SVG de chart."""
    out = []
    for _, el in ElementTree.iterparse(io.BytesIO(svg_bytes), events=('end',)):
        if el.tag.rsplit('}', 1)[-1] == 'g' and any(k.rsplit('}', 1)[-1] == 'label' and v == 'Header' for k, v in el.attrib.items()):
            for t in el.iter():
                if t.tag.rsplit('}', 1)[-1] == 'text':
                    out.append(''.join(t.itertext()))
            return out
    return out


_PAGE = re.compile(r'^(?P<a>10-8|10-9(?P<l>[A-Z]*)|11-(?P<n>\d+)(?P<h>H?))$')


def page_order_key(suffix):
    """10-8, puis 10-9, 10-9A, 10-9B..., puis les 11-x par numero, puis les 11-xH par numero (l'ordre des PDF)."""
    m = _PAGE.match(suffix)
    if not m:
        raise SiteError(f'page au nom inattendu : {suffix}')
    if suffix == '10-8':
        return (0, 0, '')
    if suffix.startswith('10-9'):
        return (1, 0, m['l'])
    return (3 if m['h'] else 2, int(m['n']), '')


def slugify(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def norm(s):
    s = unicodedata.normalize('NFKD', s)
    s = re.sub('[̀-ͯ]', '', s).lower()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def check_slugs(items, label):
    seen = {}
    for name in items:
        slug = slugify(name)
        if not slug:
            raise SiteError(f'{label} : le nom « {name} » ne donne aucun slug')
        if slug in seen:
            raise SiteError(f'{label} : « {seen[slug]} » et « {name} » donnent le meme slug « {slug} »')
        seen[slug] = name


def page_label(code):
    if code == '10-8':
        return 'Vicinity'
    if code == '10-9':
        return 'Airfield'
    if re.match(r'^10-9[A-Z]$', code):
        return 'Parking'
    if code.endswith('H'):
        return 'Approach · helicopters'
    if code.startswith('11-'):
        return 'Approach · aircraft'
    return ''


# ---------- radiobalises ----------
def beacon_chip(b):
    """La puce d'une radiobalise (table du cadrage 2.2) ou None ; une valeur absente est omise."""
    t, cs, ch, rw, fr = b.get('type'), b.get('callsign'), b.get('channel'), b.get('runway'), b.get('frequency')
    mhz = None if fr is None else f'{fr / 1e6:.2f}'
    khz = None if fr is None else f'{fr / 1e3:.0f}'
    if t in ('TACAN', 'AIRPORT_TACAN'):
        parts = ['TACAN', None if ch is None else f'{ch}X', cs]
    elif t in ('VORTAC', 'VOR_DME', 'VOR', 'DME'):
        parts = [{'VORTAC': 'VORTAC', 'VOR_DME': 'VOR/DME', 'VOR': 'VOR', 'DME': 'DME'}[t], mhz, cs]
    elif t == 'ILS_LOCALIZER':
        parts = ['ILS', rw, mhz, cs]
    elif t == 'PRMG_LOCALIZER':
        parts = ['PRMG', rw, None if ch is None else f'ch{ch}', cs]
    elif t == 'RSBN':
        parts = ['RSBN', None if ch is None else f'ch{ch}', cs]
    elif t in ('AIRPORT_HOMER', 'AIRPORT_HOMER_WITH_MARKER', 'HOMER'):
        parts = ['NDB', khz, cs]
    elif t == 'ILS_FAR_HOMER':
        parts = ['NDB', khz, cs, '(outer)']
    elif t == 'ILS_NEAR_HOMER':
        parts = ['NDB', khz, cs, '(inner)']
    else:
        return None
    return ' '.join(str(p) for p in parts if p not in (None, ''))


def beacon_chips(beacons):
    """Les puces d'un terrain, dans l'ordre du fichier, une par texte distinct."""
    out = []
    for b in beacons or []:
        c = beacon_chip(b)
        if c and c not in out:
            out.append(c)
    return out


# ---------- recherche : classement (miroir de `rank` de assets/site.js) ----------
def rank(entries, q, limit=12):
    """Classement de la maquette : OACI exact, debut du nom, debut d'un mot, debut de l'OACI, contenu (nom, OACI et alias) ;
    les heliports apres, a rang egal ; `limit` resultats."""
    nq = norm(q)
    if not nq:
        return []
    out = []
    for e in entries:
        s, nn = -1, norm(e['n'])
        if e['i'] and e['i'].lower() == nq:
            s = 0
        elif nn.startswith(nq):
            s = 1
        elif any(w.startswith(nq) for w in nn.split(' ')):
            s = 2
        elif e['i'] and e['i'].lower().startswith(nq):
            s = 2
        elif nq in norm(f"{e['n']} {e['i']} {e['a']}"):
            s = 3
        if s >= 0:
            out.append((s + (0.5 if e['t'] == 'hp' else 0), e))
    out.sort(key=lambda x: (x[0], x[1]['n']))
    return [e for _, e in out[:limit]]


# ---------- lecture des entrees ----------
def pluralize(n, one, many=None):
    return f'{n} {one if n == 1 else (many or one + "s")}'


def check_zips(zips_dir):
    zips_dir = Path(zips_dir)
    missing = [zip_name(t, k) for t in THEATRES for k in ('airfields', 'helicopters', 'lotatc')
               if not (zips_dir / zip_name(t, k)).is_file()]
    if missing:
        raise SiteError(f'zips absents de {zips_dir} : {", ".join(missing)}')


def check_marque():
    """Les copies de la marque sont celles qui sont validees (SHA256SUMS a cote)."""
    for line in (MARQUE / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip('*')
        got = hashlib.sha256((MARQUE / name).read_bytes()).hexdigest()
        if got != digest:
            raise SiteError(f'marque : {name} ne correspond pas a SHA256SUMS')


def theatre_terrains(theatre):
    """[nom, lat, lon, 'a' ou 'h'] de chaque aeroport pydcs : `a` s'il a des pistes ou une radio de tour, sinon `h`."""
    terrain = dcsdata.load_terrain(theatre)
    proj = geo.Projection.for_theatre(theatre)
    out = []
    for ap in terrain.airports.values():
        lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
        kind = 'a' if (ap.runways or getattr(ap, 'atc_radio', None) is not None) else 'h'
        out.append([ap.name, lat, lon, kind])
    names = [t[0] for t in out]
    if len(set(names)) != len(names):
        raise SiteError(f'{theatre} : noms de terrain en double dans pydcs')
    return out


def pdf_name(base, folder_dir):
    """`<stem>_charts.pdf` s'il existe, sinon `<nom avec _>_charts.pdf` ; aucun des deux : erreur."""
    for cand in (f'{base.stem}_charts.pdf', f'{base.name.replace(" ", "_")}_charts.pdf'):
        if (folder_dir / cand).is_file():
            return cand
    raise SiteError(f'{base.stem} : aucun PDF dans {folder_dir} ({base.stem}_charts.pdf, {base.name.replace(" ", "_")}_charts.pdf)')


def load_theatre(theatre, zips_dir, downloads, log=print):
    zips_dir = Path(zips_dir)
    z_af, z_he, z_lo = (zips_dir / zip_name(theatre, k) for k in ('airfields', 'helicopters', 'lotatc'))
    terrains = theatre_terrains(theatre.pydcs)
    t_a = [t for t in terrains if t[3] == 'a']
    t_h = [t for t in terrains if t[3] == 'h']
    charts = fiches.chart_pages([z_af, z_he])
    attached = fiches.attach_charts(charts, t_a)
    by_pages = {id(pages): stem for stem, pages in charts.items()}
    if len(attached) != len(charts):
        raise SiteError(f'{theatre.pydcs} : {len(charts)} charts, {len(attached)} rattachees')

    navaids = json.loads((downloads / 'navaids' / theatre.navaids).read_text(encoding='utf-8'))
    nav_by_name = {a['name']: a.get('beacons') for a in navaids['airdromes']}
    folder_dir = downloads / theatre.folder

    with zipfile.ZipFile(z_lo) as zl:
        lo_names = set(zl.namelist())
        lotatc_bytes = {}
        bases = []
        for tname, pages in attached.items():
            stem = by_pages[id(pages)]
            p109 = next(ref for suffix, ref in pages if suffix == '10-9')
            texts = header_texts(fiches.read_member(fiches.svg_member(p109)))
            icao, name = split_name(stem, texts)
            pages = sorted(pages, key=lambda t: page_order_key(t[0]))
            b = Base(stem, name, icao, tname if tname != name else '',
                     'heli' if Path(p109[0]) == z_he else 'af', pages)
            if tname not in nav_by_name:
                raise SiteError(f'{stem} : {tname} absent de downloads/navaids/{theatre.navaids}')
            b.chips = beacon_chips(nav_by_name[tname])
            b.pdf = pdf_name(b, folder_dir)
            for label, fname in (('drawing', f'{stem}.json'), ('parking', f'{stem}_parking.json')):
                if fname in lo_names:
                    b.lotatc.append((label, fname))
                    lotatc_bytes[fname] = zl.read(fname)
            bases.append(b)
    bases.sort(key=lambda b: (b.name.casefold(), b.stem))
    check_slugs([b.name for b in bases], f'{theatre.pydcs}, bases')
    for b in bases:
        b.slug = slugify(b.name)
    if 'heliports' in {b.slug for b in bases}:
        raise SiteError(f'{theatre.pydcs} : une base a le slug « heliports », reserve aux pages des heliports')

    # heliports : annuaire rejoue, controle, puis position et ville
    placed = fiches.heliport_pages(theatre.pydcs, z_he, t_h)
    sites, _, _ = fiches.replay_directory(theatre.pydcs)
    towns = directory.load_towns(theatre.pydcs)
    pos_of = {t[0]: (t[1], t[2]) for t in terrains}
    heliports = []
    for site in sites:
        if site.name not in placed:
            raise SiteError(f'{site.name} : absent de la mise en page du repertoire')
        no, _cadre, ref = placed[site.name]
        lat, lon = pos_of[site.name]
        heliports.append(Heliport(site.name, slugify(site.name), len(site.aires),
                                  f"{geo.dm(lat, 'N', 'S', 2)} {geo.dm(lon, 'E', 'W', 3)}",
                                  directory.nearest_town(lat, lon, towns), no, ref))
    if len(heliports) != len(placed):
        raise SiteError(f'{theatre.pydcs} : {len(placed)} heliports places, {len(heliports)} sites rejoues')
    check_slugs([h.name for h in heliports], f'{theatre.pydcs}, heliports')
    for h in heliports:
        h.mates = [m for m in heliports if m.page_no == h.page_no and m is not h]
    dir_pages = {h.page_no: h.ref for h in heliports}
    sizes = {k: (zips_dir / zip_name(theatre, k)).stat().st_size for k in ('airfields', 'helicopters', 'lotatc')}
    return TheatreData(theatre, bases, heliports, dir_pages, lotatc_bytes, sizes)


# ---------- images ----------
def image_name(stem, page):
    return f'{stem}_{page}'


def dir_image_name(no):
    return f'directory_p{no:02d}'


def _scaled(im, width):
    h = max(1, round(im.height * width / im.width))
    return im.resize((width, h), Image.LANCZOS)


def _rgb(im):
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        rgba = im.convert('RGBA')
        flat = Image.new('RGB', rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel('A'))
        return flat
    return im.convert('RGB')


def make_webps(job):
    """(zip, membre, sortie sans extension) -> ecrit `<sortie>.webp` (1 400 px, qualite 80) et `<sortie>_t.webp` (240 px, 70) ;
    retourne (sortie, largeur et hauteur de la source, du grand, de la vignette)."""
    zpath, member, out = job
    with zipfile.ZipFile(zpath) as z:
        data = z.read(member)
    im = _rgb(Image.open(io.BytesIO(data)))
    big, small = _scaled(im, LARGE_PX), _scaled(im, THUMB_PX)
    big.save(out + '.webp', 'WEBP', quality=LARGE_Q)
    small.save(out + '_t.webp', 'WEBP', quality=THUMB_Q)
    return out, im.size, big.size, small.size


def write_images(jobs, workers):
    """jobs : [(zip, membre, sortie sans extension)] ; retourne {sortie: (source, grand, vignette)}."""
    for _, _, out in jobs:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(make_webps, jobs, chunksize=4))
    else:
        results = [make_webps(j) for j in jobs]
    return {out: (src, big, small) for out, src, big, small in results}


# ---------- HTML ----------
def esc(s):
    return html.escape(str(s), quote=True)


class Context:
    def __init__(self, tag, repo, guide_mb, datas):
        self.tag, self.repo, self.guide_mb, self.datas = tag, repo, guide_mb, datas

    @property
    def raw(self):
        return f'https://raw.githubusercontent.com/{self.repo}/{self.tag}/downloads'

    @property
    def rel(self):
        return f'https://github.com/{self.repo}/releases/download/{self.tag}'


def mb(n_bytes):
    return f'{round(n_bytes / 1e6)} MB'


def page_html(ctx, title, root, body, current=None, base_href=None):
    """Une page complete. `root` : chemin relatif de la page a la racine du site ('', '../', '../../'...)."""
    href_root = root or './'
    nav = ''.join(
        '<a href="{}{}/" data-nav="{}"{}>{}</a>'.format(root, key, key, ' aria-current="page"' if current == key else '', esc(label))
        for key, label in [(t.folder, t.title) for t in THEATRES] + [('guide', 'Reading guide')])
    head_base = f'<base href="{esc(base_href)}">\n' if base_href else ''
    return f'''<!DOCTYPE html>
<html lang="en" data-root="{root}">
<head>
<meta charset="utf-8">
{head_base}<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="icon" href="{root}assets/favicon.ico" sizes="any">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{root}assets/apple-touch-icon.png">
<link rel="stylesheet" href="{root}assets/site.css">
<script src="{root}assets/site.js" defer></script>
</head>
<body>
<header class="bar">
  <a class="brand" href="{href_root}"><img src="{root}assets/symbole.svg" alt=""><span>Airfield Charts</span></a>
  <nav class="main" aria-label="Main">
    {nav}
  </nav>
  <span class="sp"></span>
  <a class="ver" id="ver" href="https://github.com/{esc(ctx.repo)}/releases/tag/{esc(ctx.tag)}">{esc(ctx.tag)}</a>
</header>
<main id="app">
{body}
</main>
<footer><div class="in">
  <span class="sig"><img src="{root}assets/pictogramme.svg" alt="">Squadron Ready Room family</span>
  <span id="fver">Charts {esc(ctx.tag)} · magnetic declination 1985</span>
  <span>Code: MIT · <a href="https://github.com/{esc(ctx.repo)}">GitHub</a></span>
  <span>Map imagery © Eagle Dynamics SA, captured from the DCS World F10 map — non-commercial use only. Not affiliated with or endorsed by Eagle Dynamics. For DCS only, not for real-world navigation.</span>
</div></footer>
</body>
</html>
'''


def guide_card(ctx, root, with_read_button):
    pdf = f'{ctx.raw}/{GUIDE_PDF}'
    read = f'<a class="btn pri" href="{root}guide/">Read the guide</a> ' if with_read_button else ''
    return f'''<div class="card guide"><div class="pg"><img src="{root}assets/insigne-airfield-charts.svg" alt=""></div>
    <div><h3>New to these charts?</h3><p>How to read every page of an airfield, how to fly with them, and which approach pages your aircraft can fly.</p>
    {read}<a class="btn{'' if with_read_button else ' pri'}" href="{pdf}">PDF <small>A4 · {ctx.guide_mb} MB</small></a></div></div>'''


def counts_text(d):
    n_af = sum(1 for b in d.bases if b.kind == 'af')
    n_he = len(d.bases) - n_af
    return n_af, n_he


def home_html(ctx, images):
    total = sum(len(d.bases) for d in ctx.datas)
    n_hp = sum(len(d.heliports) for d in ctx.datas)
    cards = []
    for d in ctx.datas:
        t = d.theatre
        n_af, n_he = counts_text(d)
        thumb = f'img/{t.folder}/{image_name(t.tile_stem, t.tile_page)}_t.webp'
        cards.append(f'''<div class="card theatre"><div class="th"><img src="{thumb}" alt="A {t.tile_page} chart of {esc(t.title)}"></div>
    <div class="tx"><h3>{esc(t.title)}</h3><div class="ct">{pluralize(n_af, 'airfield')} · {pluralize(n_he, 'helicopter base')} · {pluralize(len(d.heliports), 'heliport')}</div>
      <div class="ct">Every page as PDF, PNG (kneeboard) and SVG: <a href="{ctx.rel}/{zip_name(t, 'airfields')}">airfields zip</a> · <a href="{ctx.rel}/{zip_name(t, 'helicopters')}">helicopters zip</a> · <a href="{ctx.rel}/{zip_name(t, 'lotatc')}">LotAtc zip</a></div>
      <div class="act"><a class="btn pri" href="{t.folder}/">Browse {esc(t.title)}</a></div></div></div>''')
    stats = (f'{pluralize(len(ctx.datas), "theatre")} · '
             f'{total} {"airfield and helicopter base" if total == 1 else "airfields and helicopter bases"} · '
             f'{pluralize(n_hp, "heliport")} · magnetic declination 1985')
    return f'''<section class="hero"><div class="in">
    <img class="ins" src="assets/insigne-airfield-charts.svg" alt="Airfield Charts insignia">
    <div><h1>DCS Airfield Charts</h1>
      <p class="tag">Airfield and approach charts for DCS World, built from the game's own data — for print and for the kneeboard.</p>
      <div class="search"><input type="search" placeholder="Name or ICAO — Akrotiri, LCRA…" aria-label="Search an airfield or heliport" autocomplete="off"><ul class="results" hidden></ul></div>
      <div class="stats">{stats}</div>
    </div></div></section>
  <h2>Theatres</h2>
  <div class="grid2">
    {chr(10).join("    " + c if i else c for i, c in enumerate(cards))}
  </div>
  <h2>Reading guide</h2>
  {guide_card(ctx, '', True)}'''


# ---------- le guide de lecture : une page par chapitre ----------
GUIDE_TITLE = f'Reading guide — {SITE_TITLE}'
_SVG_SRC = re.compile(r'^svg/(?P<stem>.+)_(?P<page>[^_/]+)\.svg$')
_DIR_SRC = re.compile(r'^heliports/(?:.*/)?[^/]*_directory_p(?P<no>\d+)\.jpg$')
_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
_PART_PREFIX = re.compile(r'^PART \d+ · ')
_NUM_PREFIX = re.compile(r'^\d+\.\d+ ')


def md(t):
    """Le texte echappe ; `**x**` devient <b>x</b> et `` `x` `` devient <code>x</code> (comme la maquette)."""
    return re.sub(r'`(.+?)`', r'<code>\1</code>', re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', esc(t)))


def gtitle(spec):
    return (f'{spec["num"]}. ' if spec.get('num') else '') + spec['title']


def source_image(defs, key, theatre):
    """(nom de l'image du site sous `img/<dossier>/`, code de la page) d'une source de guide.json : `svg/<stem>_<page>.svg` ->
    image_name(stem, page) ; `heliports/..._directory_p<NN>.jpg` -> dir_image_name(NN)."""
    try:
        f = defs[key]['file']
    except (KeyError, TypeError):
        raise SiteError(f'guide : la source « {key} » n\'est pas dans les sources de guide.json')
    m = _SVG_SRC.match(f)
    if m:
        return image_name(m['stem'], m['page']), m['page']
    m = _DIR_SRC.match(f)
    if m:
        return dir_image_name(int(m['no'])), 'directory'
    raise SiteError(f'guide : la source « {key} » ({f}) n\'a ni la forme svg/<stem>_<page>.svg ni heliports/..._directory_p<NN>.jpg')


def css_classes(css):
    """Les classes que les selecteurs d'une feuille de style visent."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    out = set()
    for prelude in re.findall(r'([^{}]+)\{', css):
        if not prelude.strip().startswith('@'):
            out.update(re.findall(r'\.(-?[_a-zA-Z][\w-]*)', prelude))
    return out


def svg_classes(svg):
    out = set()
    for v in _CLASS_ATTR.findall(svg):
        out.update(v.split())
    return out


def check_classes(figure_svgs, css):
    """Une classe portee par un element d'un SVG de figure et visee par le CSS du site arrete la fabrication."""
    hit = set()
    for svg in figure_svgs:
        hit |= svg_classes(svg)
    hit &= css_classes(css)
    if hit:
        raise SiteError('guide : le CSS du site vise ' + ', '.join(f'.{c}' for c in sorted(hit)) +
                        ', classe portee par un element d\'un SVG de figure')


def figure_svg(fig, title):
    """Le SVG d'une figure : son cadre pour viewBox (trois decimales), puis ses elements dans l'ordre ou ils ont ete dessines."""
    x, y, w, h = fig.frame()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="{x:.3f} {y:.3f} {w:.3f} {h:.3f}" role="img" aria-label="{esc(title)}">\n'
            + '\n'.join(fig.els) + '\n</svg>')


def guide_url(pid, first):
    return 'guide/' if pid == first else f'guide/{pid}/'


def rel_url(target, source):
    """Le lien relatif d'une page du site (`source`, son url) a une autre (`target`), les deux de la forme `guide/` ou `guide/ch2/`."""
    r = posixpath.relpath(target.rstrip('/') or '.', source.rstrip('/') or '.')
    return './' if r == '.' else r + '/'


class _Counts:
    def __init__(self):
        self.figures = self.pins = self.thumbs = 0


def guide_toc(ctx, data, specs, cur, here, first):
    """Le sommaire d'une page : un titre par partie, une entree par page sauf ch6b (ch6a reste marquee sur ch6b), le bouton PDF."""
    labels = data['labels']['parts']
    h, part = '', None
    for p in specs:
        if p['part'] != part:
            part = p['part']
            h += f'<h4>{esc(_PART_PREFIX.sub("", str(labels.get(str(part), ""))))}</h4>'
        if p['id'] == 'ch6b':
            continue
        mark = ' aria-current="page"' if p['id'] == cur or (cur == 'ch6b' and p['id'] == 'ch6a') else ''
        h += (f'<a href="{rel_url(guide_url(p["id"], first), here)}"{mark}>'
              f'<span>{esc(p.get("num") or "·")}</span>{esc(_NUM_PREFIX.sub("", p["title"]))}</a>')
    return h + f'<a class="btn pdf" href="{ctx.raw}/{GUIDE_PDF}">PDF <small>A4 · {ctx.guide_mb} MB</small></a>'


def guide_body(spec, figs, defs, images, root, counts):
    """Le corps d'une page de chapitre (cadrage 2.3)."""
    kind, title = spec['kind'], spec['title']
    theatre = next(t for t in THEATRES if t.pydcs == 'Syria')

    def img(key, big, alt):
        name, _ = source_image(defs, key, theatre)
        stem = f'img/{theatre.folder}/{name}'
        if stem not in images:
            raise SiteError(f'guide, page {spec["id"]} : l\'image {stem}.webp n\'est pas sur le site')
        counts.thumbs += 1
        return f'<img class="thumb" src="{root}{stem}{"" if big else "_t"}.webp" alt="{esc(alt)}" loading="lazy">'

    def svg(name):
        if name not in figs:
            raise SiteError(f'guide, page {spec["id"]} : la figure « {name} » manque')
        counts.figures += 1
        return figure_svg(figs[name], title)

    out = []
    if spec.get('paragraphs'):
        out += [f'<p>{md(t)}</p>' for t in spec['paragraphs']]
    if kind in ('anatomy', 'zoom_only'):
        pins = ''.join(f'<li><span class="bd">{esc(p["n"])}</span><b>{esc(p["title"])}</b>'
                       + (f'<span class="tx">{md(p["text"])}</span>' if p.get('text') else '') + '</li>' for p in spec['pins'])
        counts.pins += len(spec['pins'])
        if kind == 'zoom_only':
            cap = f'<figcaption class="capt">{esc(spec["caption"])}</figcaption>' if spec.get('caption') else ''
            out.append(f'<div class="anat"><figure>{cap}{svg("zoom")}</figure><ol class="pins">{pins}</ol></div>')
        else:
            out.append(f'<div class="anat"><figure>{svg("page")}</figure><ol class="pins">{pins}</ol></div>')
            if spec.get('zoom'):
                out.append(f'<figure class="zoomf"><figcaption>{esc(spec["zoom"]["caption"])}</figcaption>{svg("zoom")}</figure>')
    elif kind == 'strip':
        out.append('<div class="strip">' + ''.join(
            f'<figure>{img(t["source"], False, t["code"])}<figcaption><b>{esc(t["code"])}</b>'
            f'<span class="nm">{esc(t["name"])}</span>{esc(t["line"])}</figcaption></figure>' for t in spec['thumbs']) + '</div>')
        out.append(f'<p>{md(spec["text"])}</p>')
    elif kind == 'flight':
        steps = []
        for n, st in enumerate(spec['steps'], 1):
            imgs = ''.join(img(k, False, source_image(defs, k, theatre)[1]) for k in st['thumbs'])
            steps.append(f'<li><div class="imgs">{imgs}</div><div><span class="sn">{n}</span><p>{md(st["text"])}</p></div></li>')
        out.append('<ol class="steps">' + ''.join(steps) + '</ol>')
    elif kind == 'overview':
        head = ''.join(f'<th>{esc(h)}</th>' for h in spec['header'])
        rows = ''.join('<tr>' + f'<th scope="row">{esc(r[0])}</th>' + ''.join(
            f'<td class="no">{esc(c)}</td>' if c == '—' else f'<td>{esc(c)}</td>' for c in r[1:]) + '</tr>' for r in spec['rows'])
        out.append(f'<p class="capt">{esc(spec["caption"])}</p><div class="tbl"><table class="ov"><thead><tr>{head}</tr></thead>'
                   f'<tbody>{rows}</tbody></table></div>')
        out.append(f'<p class="note">{md(spec["note"])}</p>')
    elif kind == 'fiche':
        items = ''.join(f'<p>{md(t)}</p>' for t in spec['items'])
        if spec.get('example'):
            out.append(f'<div class="anat"><figure class="exf">{img(spec["example"], True, spec["example_caption"])}'
                       f'<figcaption class="capt">{esc(spec["example_caption"])}</figcaption></figure><div>{items}</div></div>')
        else:
            out.append(items)
    elif kind == 'annex':
        for sec in spec['sections']:
            out.append(f'<h3 class="use">{esc(sec["heading"])}</h3>')
            for para in sec['paragraphs']:
                if isinstance(para, list):
                    out.append('<dl class="abbr">' + ''.join(f'<dt>{esc(a)}</dt><dd>{esc(b)}</dd>' for a, b in para) + '</dl>')
                else:
                    out.append(f'<p>{md(para)}</p>')
    elif kind != 'text':
        raise SiteError(f'guide, page {spec["id"]} : sorte de page inconnue : {kind}')
    if spec.get('text') and kind != 'strip':
        out.append(f'<p>{md(spec["text"])}</p>')
    if spec.get('note') and kind != 'overview':
        out.append(f'<p class="note">{md(spec["note"])}</p>')
    if spec.get('use'):
        out.append('<h3 class="use">How to use it</h3><ol class="use">' + ''.join(f'<li>{md(t)}</li>' for t in spec['use']) + '</ol>')
    return '\n  '.join(out)


@dataclass
class GuidePage:
    id: str
    path: str               # chemin du fichier sous la racine du site
    url: str                # url relative a la racine du site
    title: str              # le titre de la page (h1)
    html: str
    prev: str = ''          # url relative a la page du chapitre precedent / suivant, '' s'il n'y en a pas
    next: str = ''


def guide_pages(ctx, data, composed, images, css=None):
    """Les pages du guide (une par entree de guide.json sauf la couverture et le sommaire) ; retourne ([GuidePage], la ligne du
    resume). `composed` : le Composed de `guide.compose` ; `images` : les images du site ; `css` : la feuille de style du site."""
    specs = [s for s in data['pages'] if s['kind'] not in ('cover', 'contents')]
    figures = dict(composed.figures)
    if [i for i, _ in composed.figures] != [s['id'] for s in specs]:
        raise SiteError('guide : les figures composees ne sont pas celles des pages de guide.json')
    first, defs, parts = specs[0]['id'], data['sources'], data['labels']['parts']
    css = (DATA / 'site.css').read_text(encoding='utf-8') if css is None else css
    counts, pages = _Counts(), []
    for k, spec in enumerate(specs):
        pid = spec['id']
        url = guide_url(pid, first)
        root = '../' if pid == first else '../../'
        body = guide_body(spec, figures[pid], defs, images, root, counts)
        pv, nx = (specs[k - 1] if k else None), (specs[k + 1] if k + 1 < len(specs) else None)
        link = lambda p: rel_url(guide_url(p['id'], first), url)
        nav = ((f'<a class="btn" href="{link(pv)}">‹ {esc(gtitle(pv))}</a>' if pv else '<span></span>')
               + (f'<a class="btn" href="{link(nx)}">{esc(gtitle(nx))} ›</a>' if nx else ''))
        toc = guide_toc(ctx, data, specs, pid, url, first)
        main = f'''<div class="crumb"><a href="{root}">Home</a> › <a href="{rel_url('guide/', url)}">Reading guide</a> › {esc(gtitle(spec))}</div>
  <details class="tocm"><summary>Contents</summary><nav class="toc">{toc}</nav></details>
  <div class="gd"><nav class="toc" aria-label="Guide contents">{toc}</nav>
  <article><p class="gpart">{esc(parts.get(str(spec["part"]), ""))}</p><h1 class="pt">{esc(gtitle(spec))}</h1>
  {body}
  <div class="pn">{nav}</div>
  </article></div>'''
        path = 'guide/index.html' if pid == first else f'guide/{pid}/index.html'
        pages.append(GuidePage(pid, path, url, gtitle(spec), page_html(ctx, GUIDE_TITLE, root, main, current='guide'),
                               link(pv) if pv else '', link(nx) if nx else ''))
    check_classes([figure_svg(f, '') for fs in figures.values() for f in fs.values()], css)
    return pages, f'guide : {len(pages)} pages, {counts.figures} figures SVG, {counts.pins} pastilles, {counts.thumbs} vignettes'


def compose_guide(zips_dir, edition):
    """Les donnees du guide et leur composition (figures des pages d'anatomie et de zoom), sur les zips de Syria de `zips_dir`."""
    t = next(t for t in THEATRES if t.pydcs == 'Syria')
    zips = {k: Path(zips_dir) / zip_name(t, k) for k in ('airfields', 'helicopters')}
    try:
        data = guide.load_data()
        return data, guide.compose(zips, edition, data)
    except guide.GuideError as e:
        raise SiteError(f'guide : {e}')


def not_found_html():
    return '''<h1 class="pt">Page not found</h1>
  <p class="note">This page does not exist on the Airfield Charts website.</p>
  <div class="actions"><a class="btn pri" href="./">Back to the home page</a></div>'''


def chip_heli():
    return '<span class="chip heli">helicopter base</span>'


def index_html(ctx, d):
    t = d.theatre
    n_af, n_he = counts_text(d)
    rows = []
    for b in d.bases:
        icao = esc(b.icao) if b.icao else '—'
        key = norm(f'{b.name} {b.icao}')
        rows.append(f'''<tr data-t="{b.kind}" data-k="{esc(key)}">
    <td class="n"><a href="{b.slug}/">{esc(b.name)}</a>{" " + chip_heli() if b.kind == "heli" else ""}</td>
    <td class="i">{icao}</td><td class="p"><div class="pp">{"".join(f"<span>{code}</span>" for code, _ in b.pages)}</div></td>
    <td class="d"><a href="{ctx.raw}/{t.folder}/{esc(b.pdf)}">PDF</a></td></tr>''')
    directory_pdf = f'{ctx.raw}/{t.folder}/{t.pydcs}_heliports_directory.pdf'
    for h in d.heliports:
        rows.append(f'''<tr data-t="hp" data-k="{esc(norm(h.name))}">
    <td class="n"><a href="heliports/{h.slug}/">{esc(h.name)}</a> <span class="chip heli">heliport</span></td><td class="i">—</td>
    <td class="p"><div class="pp"><span>{pluralize(h.pads, 'pad')}</span><span>{esc(h.town)}</span><span>directory p. {h.page_no}</span></div></td><td class="d"><a href="{directory_pdf}">Directory</a></td></tr>''')
    seg = ''.join(f'<button data-f="{k}" aria-pressed="{"true" if k == "all" else "false"}">{lab}</button>'
                  for k, lab in [('all', 'All'), ('af', 'Airfields'), ('heli', 'Helicopter bases'), ('hp', 'Heliports')])
    return f'''<div class="crumb"><a href="../">Home</a> › {esc(t.title)}</div>
  <h1 class="pt">{esc(t.title)}</h1>
  <div class="facts"><span class="chip">{pluralize(n_af, 'airfield')}</span><span class="chip">{pluralize(n_he, 'helicopter base')}</span><span class="chip">{pluralize(len(d.heliports), 'heliport')}</span><span class="chip">magnetic 1985</span></div>
  <div class="actions">
    <a class="btn" href="{ctx.rel}/{zip_name(t, 'airfields')}">Airfields zip <small>PDF · PNG · SVG · {mb(d.zip_sizes['airfields'])}</small></a>
    <a class="btn" href="{ctx.rel}/{zip_name(t, 'helicopters')}">Helicopters zip <small>{mb(d.zip_sizes['helicopters'])}</small></a>
    <a class="btn" href="{directory_pdf}">Heliport directory <small>PDF</small></a>
    <a class="btn" href="{ctx.rel}/{zip_name(t, 'lotatc')}">LotAtc zip <small>all bases</small></a>
  </div>
  <div class="tools"><div class="seg" role="group" aria-label="Show">
    {seg}
  </div><input class="filter" type="search" placeholder="Filter this list" aria-label="Filter this list"></div>
  <div id="list"><table class="idx"><thead><tr><th>Name</th><th>ICAO</th><th class="p">Pages</th><th></th></tr></thead><tbody>
{chr(10).join(rows)}
</tbody></table></div>'''


def base_html(ctx, d, b):
    t = d.theatre
    root = '../../'
    img = lambda suffix, ext: f'{root}img/{t.folder}/{image_name(b.stem, suffix)}{ext}'
    facts = ''.join(f'<span class="chip nav">{esc(c)}</span>' for c in b.chips) or '<span class="chip">no radio navaid</span>'
    zip_kind = 'helicopters' if b.kind == 'heli' else 'airfields'
    pdf = f'{ctx.raw}/{t.folder}/{b.pdf}'
    lot = ''.join(f'<a class="btn" href="{root}lotatc/{t.folder}/{esc(fname)}" download title="LotAtc {label}">LotAtc <small>{label}</small></a>\n    '
                  for label, fname in b.lotatc)
    cards = ''.join(f'''<button class="pg-card" type="button" data-k="{k}" data-l="{img(code, '.webp')}" data-cd="{code}" data-lb="{page_label(code)}" data-alt="{esc(b.name)} {code}">
    <div class="im"><img src="{img(code, '_t.webp')}" alt="{code}" loading="lazy"></div>
    <span class="cd">{code}</span><span class="lb">{page_label(code)}</span></button>'''
                    for k, (code, _) in enumerate(b.pages))
    icao = f'<span class="chip icao">{esc(b.icao)}</span>' if b.icao else ''
    return f'''<div class="crumb"><a href="../../">Home</a> › <a href="../">{esc(t.title)}</a> › {esc(b.name)}</div>
  <h1 class="pt">{esc(b.name)} {icao} {chip_heli() if b.kind == "heli" else ""}</h1>
  <div class="facts">{facts}</div>
  <div class="actions">
    <a class="btn pri" href="{pdf}">PDF · all {len(b.pages)} pages</a>
    {lot}<a class="btn" href="{ctx.rel}/{zip_name(t, zip_kind)}">PNG &amp; SVG <small>{zip_kind} zip</small></a>
  </div>
  <p class="note">PNG pages are 1748 × 2480 px, sized for the DCS kneeboard; they come in the theatre zip.</p>
  <div class="pages" data-pdf="{pdf}">{cards}</div>'''


def heliport_html(ctx, d, h, images):
    t = d.theatre
    root = '../../../'
    directory_pdf = f'{ctx.raw}/{t.folder}/{t.pydcs}_heliports_directory.pdf'
    name = dir_image_name(h.page_no)
    big = f'{root}img/{t.folder}/{name}.webp'
    w, hh = images[f'img/{t.folder}/{name}'][1]
    mates = ', '.join(f'<a href="../{m.slug}/">{esc(m.name)}</a>' for m in h.mates)
    return f'''<div class="crumb"><a href="../../../">Home</a> › <a href="../../">{esc(t.title)}</a> › <a href="../../?f=hp">Heliports</a> › {esc(h.name)}</div>
  <h1 class="pt">{esc(h.name)} <span class="chip heli">heliport</span></h1>
  <div class="facts"><span class="chip">{pluralize(h.pads, 'pad')}</span><span class="chip nav">{esc(h.pos)}</span><span class="chip">{esc(h.town)}</span></div>
  <div class="actions"><a class="btn pri" href="{directory_pdf}">Heliport directory · PDF</a>
    <a class="btn" href="{ctx.rel}/{zip_name(t, 'helicopters')}">JPEG pages <small>helicopters zip</small></a></div>
  <p class="note">The directory draws each heliport on the F10 map, every pad circled with its DCS number, with its elevation and pad sizes.</p>
  <figure class="dirfig"><button type="button" data-v data-l="{big}" data-cd="{esc(h.name)}" data-lb="Heliport directory · page {h.page_no}" data-alt="{esc(h.name)}" data-pdf="{directory_pdf}" aria-label="Enlarge"><img src="{big}" width="{w}" height="{hh}" alt="Heliport directory, page {h.page_no}"></button>
    <figcaption>Heliport directory, page {h.page_no}{" · also on this page: " + mates if mates else ""}</figcaption></figure>'''


def search_entries(ctx):
    out = []
    for d in ctx.datas:
        t = d.theatre
        for b in d.bases:
            kind = 'Helicopter base' if b.kind == 'heli' else 'Airfield'
            out.append({'n': b.name, 'i': b.icao, 'a': b.alias, 'th': t.title, 't': b.kind,
                        'u': f'{t.folder}/{b.slug}/', 'm': f'{kind} · {pluralize(len(b.pages), "page")} · {t.title}'})
        for h in d.heliports:
            out.append({'n': h.name, 'i': '', 'a': '', 'th': t.title, 't': 'hp',
                        'u': f'{t.folder}/heliports/{h.slug}/',
                        'm': f'Heliport · {pluralize(h.pads, "pad")} · {h.town} · {t.title}'})
    return out


# ---------- controle des liens ----------
class _Links(HTMLParser):
    ATTRS = ('href', 'src', 'data-l')

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'base':                      # `<base href>` de la page 404 : un prefixe, pas un lien
            return
        for k, v in attrs:
            if k in self.ATTRS and v:
                self.links.append(v)


def check_links(root):
    """Chaque lien relatif (href, src, data-l) des pages et chaque url() du CSS mene a un fichier du site ; SiteError sinon."""
    root = Path(root).resolve()
    bad = []

    def resolve(page, link):
        u = urlsplit(link)
        if u.scheme or u.netloc or not u.path:
            return None                                   # absolu, ou simple ancre
        path = unquote(u.path)
        target = (page.parent / path).resolve() if not path.startswith('/') else (root / path.lstrip('/')).resolve()
        return target

    for page in sorted(root.rglob('*.html')):
        p = _Links()
        p.feed(page.read_text(encoding='utf-8'))
        for link in p.links:
            target = resolve(page, link)
            if target is None:
                continue
            if target.is_dir():
                target = target / 'index.html'
            if root not in target.parents or not target.is_file():
                bad.append(f'{page.relative_to(root)} : {link}')
    css = root / 'assets' / 'site.css'
    for link in re.findall(r'url\(([^)]+)\)', css.read_text(encoding='utf-8')):
        link = link.strip('\'"')
        if link.startswith('data:'):
            continue
        if not (css.parent / link).is_file():
            bad.append(f'assets/site.css : {link}')
    if bad:
        raise SiteError(f'{len(bad)} lien(s) relatif(s) sans fichier :\n  ' + '\n  '.join(bad[:30]) + ('\n  ...' if len(bad) > 30 else ''))


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.seen, self.dup = set(), []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == 'id' and v is not None:
                if v in self.seen and v not in self.dup:
                    self.dup.append(v)
                self.seen.add(v)


def check_ids(root):
    """Aucune page HTML ne porte deux fois le meme id ; SiteError sinon."""
    root = Path(root).resolve()
    bad = []
    for page in sorted(root.rglob('*.html')):
        p = _Ids()
        p.feed(page.read_text(encoding='utf-8'))
        bad += [f'{page.relative_to(root)} : id="{i}"' for i in p.dup]
    if bad:
        raise SiteError(f'{len(bad)} id en double :\n  ' + '\n  '.join(bad[:30]) + ('\n  ...' if len(bad) > 30 else ''))


# ---------- construction ----------
def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


def copy_assets(site):
    assets = site / 'assets'
    for name in ('symbole.svg', 'pictogramme.svg', 'favicon.svg', 'favicon.ico', 'apple-touch-icon.png'):
        write(assets / name, (MARQUE / name).read_bytes())
    write(assets / 'insigne-airfield-charts.svg', INSIGNE.read_bytes())
    write(assets / 'fonts' / 'oswald-600.woff2', (MARQUE / 'oswald-600.woff2').read_bytes())
    for name in BARLOW:
        write(assets / 'fonts' / name, (FONTS / name).read_bytes())
    write(assets / 'fonts' / 'OFL.txt', OFL_BARLOW.read_bytes())
    write(assets / 'fonts' / 'OFL-Oswald.txt', OFL_OSWALD.read_bytes())
    write(assets / 'site.css', (DATA / 'site.css').read_bytes())
    write(assets / 'site.js', (DATA / 'site.js').read_bytes())


def site_size(site):
    files = [p for p in Path(site).rglob('*') if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def summary_lines(datas, n_files, size, guide_line=None):
    lines = []
    for d in datas:
        n_af, n_he = counts_text(d)
        pages = sum(len(b.pages) for b in d.bases)
        lines.append(f'{d.theatre.title} ({d.theatre.pydcs}) : {pluralize(len(d.bases), "chart")} ({pluralize(n_af, "airfield")}, '
                     f'{pluralize(n_he, "helicopter base")}), {pluralize(pages, "page")}, {pluralize(len(d.heliports), "heliport")}, '
                     f'{pluralize(len(d.dir_pages), "directory page")} shown, {pluralize(len(d.lotatc_bytes), "LotAtc file")}')
    if guide_line:
        lines.append(guide_line)
    lines.append(f'site : {n_files} files, {size / 1048576:.1f} MiB')
    return lines


def build_site(zips_dir, edition, repo, out, downloads=None, workers=None, log=print):
    """Fabrique le site dans un dossier temporaire puis le renomme en `out` (qui ne doit pas exister). SiteError au premier
    cas d'arret ; rien ne reste. Retourne les lignes du resume imprime."""
    out = Path(out)
    downloads = Path(downloads) if downloads else ROOT / 'downloads'
    if not _TAG.match(edition):
        raise SiteError(f'edition « {edition} » : attendu AC-V<x>.<y>.<z>')
    if not _REPO.match(repo):
        raise SiteError(f'depot « {repo} » : attendu <proprietaire>/<depot>')
    if out.exists():
        raise SiteError(f'dossier de sortie deja present : {out}')
    check_zips(zips_dir)
    check_marque()
    guide = downloads / GUIDE_PDF
    if not guide.is_file():
        raise SiteError(f'{GUIDE_PDF} absent de {downloads}')
    if workers is None:
        workers = os.cpu_count() or 1
    log('composition du guide de lecture ...')
    guide_data, guide_composed = compose_guide(zips_dir, edition)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    try:
        log('lecture des zips, des terrains et des annuaires ...')
        datas = []
        for t in THEATRES:
            d = load_theatre(t, zips_dir, downloads, log)
            if not any(b.stem == t.tile_stem for b in d.bases):
                raise SiteError(f'{t.pydcs} : la chart {t.tile_stem} de la tuile est absente')
            datas.append(d)
        ctx = Context(edition, repo, round(guide.stat().st_size / 1e6), datas)

        jobs = []
        for d in datas:
            for b in d.bases:
                for code, (zpath, member) in b.pages:
                    jobs.append((str(zpath), member, str(tmp / 'img' / d.theatre.folder / image_name(b.stem, code))))
            for no, (zpath, member) in sorted(d.dir_pages.items()):
                jobs.append((str(zpath), member, str(tmp / 'img' / d.theatre.folder / dir_image_name(no))))
        log(f'{len(jobs)} pages a convertir en WebP ...')
        done = write_images(jobs, workers)
        images = {str(Path(k).relative_to(tmp)): v for k, v in done.items()}

        copy_assets(tmp)
        for d in datas:
            for name, blob in sorted(d.lotatc_bytes.items()):
                write(tmp / 'lotatc' / d.theatre.folder / name, blob)
        write(tmp / 'index.html', page_html(ctx, SITE_TITLE, '', home_html(ctx, images)))
        gpages, guide_line = guide_pages(ctx, guide_data, guide_composed, images)
        for gp in gpages:
            write(tmp / gp.path, gp.html)
        write(tmp / '404.html', page_html(ctx, f'Page not found — {SITE_TITLE}', '', not_found_html(),
                                          base_href=f'/{repo.split("/")[1]}/'))
        for d in datas:
            t = d.theatre
            write(tmp / t.folder / 'index.html', page_html(ctx, f'{t.title} — {SITE_TITLE}', '../', index_html(ctx, d), current=t.folder))
            for b in d.bases:
                title = f'{b.name}{f" ({b.icao})" if b.icao else ""} — {SITE_TITLE}'
                write(tmp / t.folder / b.slug / 'index.html', page_html(ctx, title, '../../', base_html(ctx, d, b), current=t.folder))
            for h in d.heliports:
                write(tmp / t.folder / 'heliports' / h.slug / 'index.html',
                      page_html(ctx, f'{h.name} — {SITE_TITLE}', '../../../', heliport_html(ctx, d, h, images), current=t.folder))
        write(tmp / 'search.json', json.dumps(search_entries(ctx), ensure_ascii=False, separators=(',', ':')) + '\n')

        check_links(tmp)
        check_ids(tmp)
        n_files, size = site_size(tmp)
        if size > MAX_SITE_BYTES:
            raise SiteError(f'le site pese {size / 1048576:.1f} MiB (plus de {MAX_SITE_BYTES // 1048576} MiB)')
        lines = summary_lines(datas, n_files, size, guide_line)
        os.rename(tmp, out)
        return lines
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def listing(site):
    """[(chemin relatif, sha256)] de chaque fichier du site, trie."""
    site = Path(site)
    return [(p.relative_to(site).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(site.rglob('*')) if p.is_file()]
