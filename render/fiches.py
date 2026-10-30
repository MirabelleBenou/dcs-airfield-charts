"""Paquet de fiches pour le Route Planner de Mission Plan (lot ac-paquet-fiches, contrat `dcs-fiches-v1`).

A partir des zips *airfields* et *helicopters* d'une release, fabrique, par theatre, un dossier d'images et un `index.json`
qui nomme le terrain DCS de chaque page. Aucune chart n'est redessinee. La logique est ici ; la commande est
`render/build_fiches.py`.

- Une chart est rattachee a son terrain par l'ARP lu dans son SVG (terrain le plus proche d'`airfields.json`).
- Une helistation est rattachee a sa vignette en rejouant la mise en page du repertoire (`render/directory.py`, appele
  tel quel, a partir de pydcs seul), puis la mise en page est controlee contre les pages publiees.
"""
import html
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image

from . import dcsdata, directory, geo

FORMAT = 'dcs-fiches-v1'
MAX_BYTES = 1_000_000
JPEG_QUALITIES = (85, 80, 75, 70, 65, 60)
EARTH_R_M = 6_371_000.0
ARP_MAX_M = 500.0              # le terrain le plus proche est a 500 m au plus ...
NEXT_MIN_M = 1000.0            # ... et le suivant a 1 000 m au moins
PAGE_SIZE = directory.PAGE_PX
BAND_RECT = (60, 10)           # rectangle de controle, au centre de la bande de titre
ZIP_DATE = (2026, 10, 30, 0, 0, 0)    # date fixe : le zip se refabrique a l'identique
DARK_MAX, WHITE_MIN = 80, 200  # moyenne des gris : bande sombre < 80 ; page blanche > 200

# Cle du Route Planner de chaque theatre d'Airfield Charts (insensible a la casse, comme les tables de render/geo.py).
ROUTE_PLANNER_THEATRES = {'GermanyColdWar': 'GermanyCW', 'Syria': 'Syria'}

# Aerodromes pydcs sans piste ni radio de tour qui ne sont pas dans le repertoire publie (jamais captures).
DIRECTORY_EXCLUSIONS = {'Syria': {'HMed22'}}


class FichesError(Exception):
    """Cas d'arret du cadrage 6 : l'outil s'arrete sans rien ecrire."""


def resolve_theatre(name):
    """(nom d'Airfield Charts, cle du Route Planner) ; FichesError hors de la table."""
    key = geo._table_get(ROUTE_PLANNER_THEATRES, name)
    if key is None:
        raise FichesError(f'theatre inconnu : {name} (attendu : {", ".join(ROUTE_PLANNER_THEATRES)})')
    canon = next(n for n in ROUTE_PLANNER_THEATRES if n.replace(' ', '').lower() == name.replace(' ', '').lower())
    return canon, key


# ---------- ARP et rattachement des charts ----------
_ARP = re.compile(r"ARP\s+([NS])\s*(\d+)°\s*(\d+(?:\.\d+)?)'\s+([EW])\s*(\d+)°\s*(\d+(?:\.\d+)?)'")


def read_arp(svg_text):
    """(lat, lon) de la ligne `ARP N49°22.2' E009°26.4'` d'un SVG (degres et minutes decimales ; S et W negatifs) ;
    FichesError si elle manque ou n'est pas unique."""
    found = _ARP.findall(html.unescape(svg_text))
    if not found:
        raise FichesError('aucun texte ARP lisible')
    if len(set(found)) > 1:
        raise FichesError(f'plusieurs ARP differents : {sorted(set(found))}')
    ns, d1, m1, ew, d2, m2 = found[0]
    lat = (int(d1) + float(m1) / 60.0) * (-1 if ns == 'S' else 1)
    lon = (int(d2) + float(m2) / 60.0) * (-1 if ew == 'W' else 1)
    return lat, lon


def distance_m(lat1, lon1, lat2, lon2):
    """Distance sur la sphere de rayon 6 371 km (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def nearest_airfield(lat, lon, airfields):
    """Nom du terrain (liste `[nom, lat, lon, type]`, types `a` et `h`) le plus proche de (lat, lon), a 500 m au plus et
    dont le suivant est a 1 000 m au moins ; FichesError sinon. Retourne (nom, distance en m)."""
    ranked = sorted((distance_m(lat, lon, a[1], a[2]), a[0]) for a in airfields)
    if not ranked:
        raise FichesError('airfields.json : aucun terrain')
    d0, name = ranked[0]
    if d0 > ARP_MAX_M:
        raise FichesError(f'terrain le plus proche : {name} a {d0:.0f} m (plus de {ARP_MAX_M:.0f} m)')
    if len(ranked) > 1 and ranked[1][0] < NEXT_MIN_M:
        raise FichesError(f'ambigu : {name} a {d0:.0f} m et {ranked[1][1]} a {ranked[1][0]:.0f} m (moins de {NEXT_MIN_M:.0f} m)')
    return name, d0


_CHART_PNG = re.compile(r'^(?P<stem>.+)_(?P<suffix>10-9[A-Z]*)\.png$')


def page_suffix_key(suffix):
    return dcsdata.natural_key(suffix)


def chart_pages(zips):
    """Pages de charts des zips : {stem: [(suffixe, (zip, chemin dans le zip))]} dans l'ordre naturel des suffixes.
    Un PNG de `png/` qui n'a pas le nom `<stem>_10-9[A-Z]*.png` ou qui est dans deux zips : FichesError."""
    charts, seen = {}, {}
    for zpath in zips:
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                p = PurePosixPath(info.filename)
                if info.is_dir() or p.parent.name != 'png':
                    continue
                m = _CHART_PNG.match(p.name)
                if not m:
                    raise FichesError(f'{zpath} : PNG au nom inattendu : {info.filename}')
                if p.name in seen:
                    raise FichesError(f'{p.name} est dans {seen[p.name]} et dans {zpath}')
                seen[p.name] = str(zpath)
                charts.setdefault(m['stem'], []).append((m['suffix'], (Path(zpath), info.filename)))
    for pages in charts.values():
        pages.sort(key=lambda t: page_suffix_key(t[0]))
    return charts


def read_member(ref):
    zpath, name = ref
    with zipfile.ZipFile(zpath) as z:
        return z.read(name)


def svg_member(ref):
    """Le SVG de la meme page : `png/<x>.png` -> `svg/<x>.svg`, dans le meme zip."""
    zpath, name = ref
    p = PurePosixPath(name)
    return zpath, str(p.parent.parent / 'svg' / (p.stem + '.svg'))


def attach_charts(charts, airfields):
    """{nom de terrain: [(suffixe, ref)]} : chaque chart (stem) est rattachee par l'ARP de sa page 10-9 ; deux charts sur un
    meme terrain : FichesError."""
    out, owner = {}, {}
    for stem in sorted(charts, key=dcsdata.natural_key):
        pages = charts[stem]
        first = next((ref for suffix, ref in pages if suffix == '10-9'), None)
        if first is None:
            raise FichesError(f'{stem} : pas de page 10-9')
        try:
            lat, lon = read_arp(read_member(svg_member(first)).decode('utf-8'))
            name, _ = nearest_airfield(lat, lon, airfields)
        except FichesError as e:
            raise FichesError(f'{stem} : {e}') from None
        if name in owner:
            raise FichesError(f'{stem} et {owner[name]} designent le meme terrain ({name})')
        owner[name] = stem
        out[name] = pages
    return out


# ---------- images ----------
def encode_page(data, name):
    """(nom, octets, reencode) : la page telle quelle si elle pese 1 000 000 octets au plus ; sinon JPEG de meme taille en
    pixels (qualite 85, optimize, puis 80 ... 60), meme nom en `.jpg`. FichesError si elle depasse encore a 60."""
    im = Image.open(io.BytesIO(data))
    if im.size != PAGE_SIZE:
        raise FichesError(f'{name} : {im.size[0]} x {im.size[1]} px au lieu de {PAGE_SIZE[0]} x {PAGE_SIZE[1]}')
    if len(data) <= MAX_BYTES:
        return name, data, False
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        rgba = im.convert('RGBA')
        flat = Image.new('RGB', rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel('A'))
        im = flat
    else:
        im = im.convert('RGB')
    jname = str(PurePosixPath(name).with_suffix('.jpg'))
    for q in JPEG_QUALITIES:
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=q, optimize=True)
        if buf.tell() <= MAX_BYTES:
            return jname, buf.getvalue(), True
    raise FichesError(f'{name} : plus de {MAX_BYTES} octets en JPEG qualite {JPEG_QUALITIES[-1]}')


# ---------- le repertoire des helistations : rejeu et controle ----------
def replay_directory(theatre):
    """Rejoue la mise en page du repertoire avec `render/directory.py` (appele tel quel) a partir de pydcs seul.
    Retourne (sites, pages, nombre de pages d'index). Sites : aerodromes pydcs sans piste ni radio de tour, hors
    DIRECTORY_EXCLUSIONS ; dossier, elevation et position valent des valeurs neutres (aucune mise en page n'en depend)."""
    terrain = dcsdata.load_terrain(theatre)
    excluded = geo._table_get(DIRECTORY_EXCLUSIONS, theatre) or set()
    sites = []
    for ap in terrain.airports.values():
        if ap.runways or getattr(ap, 'atc_radio', None) is not None or ap.name in excluded:
            continue
        facts = dcsdata.airport_facts(ap)
        aires = [directory.Aire(s.name, s.x, s.z, float(s.length), float(s.width)) for s in facts.slots]
        aires.sort(key=lambda a: dcsdata.natural_key(a.name))
        site = directory.Site(ap.name, None, ap.position.x, ap.position.y, 0.0, 0.0, 0, aires)
        site.series = directory.series_of(site.name)
        site.theatre = theatre
        sites.append(site)
    sites.sort(key=lambda s: (directory.series_key(s.series), dcsdata.natural_key(s.name)))
    pages = directory.build_pages(sites)
    return sites, pages, directory.index_page_count(len(sites))


_DIR_JPG = re.compile(r'^(?P<theatre>.+)_heliports_directory_p(?P<no>\d+)\.jpg$')


def directory_pages(zpath, theatre):
    """{numero de page: (zip, chemin)} des JPEG de `heliports/` du zip *helicopters*."""
    out = {}
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            p = PurePosixPath(info.filename)
            if info.is_dir() or p.parent.name != 'heliports':
                continue
            m = _DIR_JPG.match(p.name)
            if not m or m['theatre'] != theatre:
                raise FichesError(f'{zpath} : fichier inattendu dans heliports/ : {info.filename}')
            out[int(m['no'])] = (Path(zpath), info.filename)
    return out


def band_mean(gray, cx, cy):
    """Moyenne des niveaux de gris du rectangle de 60 x 10 px centre en (cx, cy) ; `gray` : tableau (hauteur, largeur)."""
    w, h = BAND_RECT
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    return float(gray[y0:y0 + h, x0:x0 + w].mean())


def check_page(no, gray, entries):
    """Controle du cadrage 2.3 sur une page publiee (`gray`) et ses vignettes `entries` = [(site, position)] :
    bande de titre sombre sous chaque vignette, page blanche au centre de la bande de chaque case non couverte.
    FichesError qui nomme la page et la case."""
    covered = set()
    for site, pos in entries:
        x, y, w, h = directory.tile_box(site.form, pos)
        m = band_mean(gray, x + w / 2, y + directory.BAND_H / 2)
        r, c = directory.CELL_RC[pos]
        if not m < DARK_MAX:
            raise FichesError(f'page {no}, case (rangee {r}, colonne {c}) : bande de titre de {site.name} non sombre '
                              f'(moyenne {m:.1f}, attendu < {DARK_MAX})')
        covered |= directory.footprint(site.form, pos)
    for pos, (r, c) in directory.CELL_RC.items():
        if (r, c) in covered:
            continue
        x, y, w, h = directory.tile_box(directory.SIMPLE, pos)
        m = band_mean(gray, x + w / 2, y + directory.BAND_H / 2)
        if not m > WHITE_MIN:
            raise FichesError(f'page {no}, case (rangee {r}, colonne {c}) : case vide non blanche '
                              f'(moyenne {m:.1f}, attendu > {WHITE_MIN})')


def heliport_pages(theatre, zpath, airfields):
    """{nom de site: (numero de page, cadre, ref du JPEG)} apres rejeu et controle de la mise en page. FichesError au
    premier controle qui echoue (le nom de la page et de la case sont dans le message)."""
    sites, pages, n_index = replay_directory(theatre)
    jpgs = directory_pages(zpath, theatre)
    if len(pages) != len(jpgs) - n_index:
        raise FichesError(f'{len(pages)} pages de plans rejouees, mais {len(jpgs)} JPEG de heliports/ moins {n_index} '
                          f"pages d'index = {len(jpgs) - n_index}")
    hkeys = {a[0] for a in airfields if a[3] == 'h'}
    out = {}
    for k, pg in enumerate(pages):
        no = n_index + k + 1
        if no not in jpgs:
            raise FichesError(f'page {no} : absente de heliports/')
        gray = np.asarray(Image.open(io.BytesIO(read_member(jpgs[no]))).convert('L'))
        if gray.shape != (PAGE_SIZE[1], PAGE_SIZE[0]):
            raise FichesError(f'page {no} : {gray.shape[1]} x {gray.shape[0]} px')
        check_page(no, gray, pg['entries'])
        for site, pos in pg['entries']:
            if site.name not in hkeys:
                raise FichesError(f"page {no} : {site.name} n'est pas une cle `h` d'airfields.json")
            x, y, w, h = directory.tile_box(site.form, pos)
            cadre = [round(x / PAGE_SIZE[0], 4), round(y / PAGE_SIZE[1], 4),
                     round(w / PAGE_SIZE[0], 4), round(h / PAGE_SIZE[1], 4)]
            if site.name in out:
                raise FichesError(f'{site.name} figure deux fois dans le repertoire')
            out[site.name] = (no, cadre, jpgs[no])
    return out


# ---------- le paquet ----------
@dataclass
class Package:
    index: dict                                   # contrat dcs-fiches-v1
    files: dict = field(default_factory=dict)     # nom de fichier -> octets
    n_charts: int = 0
    n_chart_pages: int = 0
    n_heliports: int = 0
    n_dir_pages: int = 0
    reencoded: list = field(default_factory=list)


def index_json(index):
    return json.dumps(index, indent=1, ensure_ascii=False)


def build_package(theatre, zip_airfields, zip_helicopters, airfields_json, edition, log=print):
    """Calcule tout en memoire (rien n'est ecrit) ; FichesError au premier cas d'arret."""
    canon, rp_key = resolve_theatre(theatre)
    data = json.loads(Path(airfields_json).read_text(encoding='utf-8'))
    airfields = (data.get('theatres') or {}).get(rp_key)
    if not airfields:
        raise FichesError(f'airfields.json : pas de theatre {rp_key}')
    names = [a[0] for a in airfields]
    if len(set(names)) != len(names):
        raise FichesError(f'airfields.json : noms en double dans {rp_key}')

    pkg = Package({'format': FORMAT, 'theatre': rp_key, 'edition': edition, 'fiches': {}})
    fiches = {}

    def add_image(ref):
        name = PurePosixPath(ref[1]).name
        fname, blob, re_enc = encode_page(read_member(ref), name)
        if fname in pkg.files:
            raise FichesError(f'deux pages portent le nom {fname}')
        pkg.files[fname] = blob
        if re_enc:
            pkg.reencoded.append(fname)
        return fname

    charts = attach_charts(chart_pages([zip_airfields, zip_helicopters]), airfields)
    for name, pages in charts.items():
        fiches[name] = {'pages': [{'titre': suffix, 'fichier': add_image(ref)}
                                  for suffix, ref in pages]}
        pkg.n_chart_pages += len(pages)
    pkg.n_charts = len(charts)

    helis = heliport_pages(canon, zip_helicopters, airfields)
    page_files = {}                                  # une page du repertoire est ecrite une fois, citee par chaque vignette
    for name, (no, cadre, ref) in helis.items():
        if name in fiches:
            raise FichesError(f'{name} a une chart et une vignette de repertoire')
        if no not in page_files:
            page_files[no] = add_image(ref)
        fiches[name] = {'pages': [{'titre': str(no), 'fichier': page_files[no], 'cadre': cadre}]}
    pkg.n_heliports = len(helis)
    pkg.n_dir_pages = len({v[0] for v in helis.values()})

    pkg.index['fiches'] = {k: fiches[k] for k in sorted(fiches)}
    return pkg


def write_package(pkg, out_root, theatre):
    """Ecrit `<out>/<Theatre>/` (index + images) et le zip `<out>/DCS_Airfield_Charts_<Theatre>_1985_route_planner.zip`
    (tout a la racine, index en DEFLATED, images en STORED). Dossier non vide ou zip present : FichesError. Retourne
    (dossier, zip)."""
    canon, _ = resolve_theatre(theatre)
    out_root = Path(out_root)
    folder = out_root / canon
    zpath = out_root / f'DCS_Airfield_Charts_{canon}_1985_route_planner.zip'
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise FichesError(f'dossier de sortie deja present et non vide : {folder}')
    if zpath.exists():
        raise FichesError(f'zip deja present : {zpath}')
    folder.mkdir(parents=True, exist_ok=True)
    text = index_json(pkg.index).encode('utf-8')
    (folder / 'index.json').write_bytes(text)
    for name, blob in pkg.files.items():
        (folder / name).write_bytes(blob)
    with zipfile.ZipFile(zpath, 'w') as z:
        for name, blob, comp in [('index.json', text, zipfile.ZIP_DEFLATED)] + [(n, b, zipfile.ZIP_STORED) for n, b in pkg.files.items()]:
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.external_attr = 0o644 << 16
            z.writestr(info, blob, compress_type=comp)
    return folder, zpath


def summary(pkg, theatre, zpath):
    size = Path(zpath).stat().st_size
    return '\n'.join([
        f'theatre : {theatre} ({pkg.index["theatre"]}) · edition {pkg.index["edition"]}',
        f'cles : {len(pkg.index["fiches"])}',
        f'charts : {pkg.n_charts} ({pkg.n_chart_pages} pages)',
        f'helistations : {pkg.n_heliports} (sur {pkg.n_dir_pages} pages du repertoire)',
        f'pages reencodees en JPEG : {len(pkg.reencoded)}' + (f' ({", ".join(pkg.reencoded)})' if pkg.reencoded else ''),
        f'zip : {Path(zpath).name} · {size} octets ({size / 1e6:.1f} Mo)',
    ])
