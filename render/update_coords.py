"""Republication de la ligne de coordonnees des charts deja publiees, sans les captures F10.

Usage : python -I render/update_coords.py <Theatre> --zip <zip de release> --out <nouveau zip>
Entree : un zip de la release (airfields ou helicopters). Chaque `…/svg/<page>.svg` est une page de chart ; sa ligne d'en-tete
(trois textes a y = 21.2 : ELEV, ARP, VAR) est remplacee par les cinq champs de `page.header_row` (ELEV, ARP, DMS, MGRS, VAR),
puis la page est reexportee (SVG, PDF, PNG) et le recueil `pdf/<stem>_charts.pdf` de chaque chart est refait.
Sortie : un nouveau zip, memes entrees dans le meme ordre ; les entrees hors charts (repertoire des heliports…) sont
recopiees a l'octet (un zip sans page de chart, comme celui des helicopteres de Germany Cold War, est recopie tel quel). Un fichier de sortie qui existe deja : arret. Au moindre doute (page sans ses trois textes, ARP qui ne
designe pas exactement un aerodrome pydcs, ecart de champs trop petit, recueil de longueur differente), rien n'est ecrit.
"""
import argparse
import html
import io
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from pypdf import PdfReader                                           # noqa: E402

from render import dcsdata, export, geo, page                          # noqa: E402

ROW_Y = '21.2'
_TEXT = re.compile(r'<text\b([^>]*)>(.*?)</text>', re.S)
_Y = re.compile(r'\sy="([^"]*)"')
_PAGE_SVG = re.compile(r'^(?P<dir>(?:.*/)?)svg/(?P<page>[^/]+)\.svg$')
_PAGE_NAME = re.compile(r'^(?P<stem>.+)_(?P<suffix>10-9[A-Z]*)$')


class UpdateError(Exception):
    """Arret de l'outil : rien n'est ecrit."""


def row_elements(svg_text, page_name):
    """[(debut, fin, texte lu)] des elements <text> dont y vaut 21.2 : exactement trois (ELEV, ARP, VAR) ; sinon UpdateError."""
    found = []
    for m in _TEXT.finditer(svg_text):
        y = _Y.search(m.group(1))
        if y and y.group(1) == ROW_Y:
            found.append((m.start(), m.end(), html.unescape(m.group(2))))
    if len(found) != 3:
        raise UpdateError(f'{page_name} : {len(found)} textes a y = {ROW_Y} (attendu : 3)')
    if not found[1][2].startswith('ARP '):
        raise UpdateError(f"{page_name} : le deuxieme texte de la ligne ne commence pas par « ARP » : {found[1][2]!r}")
    return found


def arp_index(theatre):
    """{texte ARP: [(nom, lat, lon)]} des aerodromes pydcs du theatre (position projetee comme analysis.py)."""
    terrain = dcsdata.load_terrain(theatre)
    proj = geo.Projection.for_theatre(theatre)
    out = {}
    for ap in terrain.airports.values():
        lat, lon = proj.to_latlon(ap.position.x, ap.position.y)
        out.setdefault(geo.arp_text(lat, lon), []).append((ap.name, lat, lon))
    return out


def rewrite_page(svg_text, page_name, index):
    """(nouveau SVG, [cinq champs]) : les trois textes remplaces par `page.header_row`, le reste identique a l'octet."""
    (s0, e0, elev), (s1, e1, arp), (s2, e2, var) = row_elements(svg_text, page_name)
    sep1, sep2 = svg_text[e0:s1], svg_text[e1:s2]
    if sep1 != sep2 or sep1.strip():
        raise UpdateError(f'{page_name} : les trois textes de la ligne ne sont pas separes de la meme facon '
                          f'({sep1!r}, {sep2!r})')
    hits = index.get(arp, [])
    if len(hits) != 1:
        raise UpdateError(f'{page_name} : {arp!r} designe {len(hits)} aerodromes pydcs '
                          f'({", ".join(n for n, _, _ in hits) or "aucun"}) au lieu d\'un seul')
    _, lat, lon = hits[0]
    try:
        elements = page.header_row(elev, lat, lon, var)
    except ValueError as e:
        raise UpdateError(f'{page_name} : {e}') from None
    fields = [html.unescape(_TEXT.fullmatch(el).group(2)) for el in elements]
    return svg_text[:s0] + sep1.join(elements) + svg_text[e2:], fields


def line_of(fields):
    return ' | '.join(fields)


def update_zip(theatre, zip_in, zip_out, log=print):
    """Calcule les nouvelles pages et les recueils, puis ecrit `zip_out`. Retourne (lignes {page: [champs]}, nombre de pages,
    nombre de recueils, nombre d'entrees recopiees)."""
    zip_in, zip_out = Path(zip_in), Path(zip_out)
    if zip_out.exists():
        raise UpdateError(f'{zip_out} existe deja (pas d\'ecrasement)')
    index = arp_index(theatre)
    with zipfile.ZipFile(zip_in) as zin, tempfile.TemporaryDirectory(prefix='update_coords_') as tmp:
        tmp = Path(tmp)
        infos = zin.infolist()
        names = {i.filename for i in infos}
        pages, lines = [], {}                      # pages : [(dir, page, stem, suffix)] dans l'ordre du zip
        for info in infos:
            m = _PAGE_SVG.match(info.filename)
            if not m:
                continue
            pm = _PAGE_NAME.match(m['page'])
            if not pm:
                raise UpdateError(f'{info.filename} : nom de page inattendu (attendu <stem>_10-9[A-Z]*)')
            pages.append((m['dir'], m['page'], pm['stem'], pm['suffix']))
        # pages : SVG, PDF, PNG
        new = {}                                   # nom d'entree -> fichier temporaire
        for n, (d, name, _, _) in enumerate(pages):
            svg_old = zin.read(f'{d}svg/{name}.svg').decode('utf-8')
            svg_new, fields = rewrite_page(svg_old, name, index)
            for sub, ext in (('png', 'png'), ('pdf', 'pdf')):
                if f'{d}{sub}/{name}.{ext}' not in names:
                    raise UpdateError(f'{name} : entree {d}{sub}/{name}.{ext} absente du zip')
            work = tmp / f'p{n}'
            svg_p, pdf_p, png_p = export.export_page(svg_new, work / 'page')
            if svg_p.read_bytes() != svg_new.encode('utf-8'):
                raise UpdateError(f'{name} : le SVG ecrit differe du SVG calcule')
            new[f'{d}svg/{name}.svg'], new[f'{d}pdf/{name}.pdf'], new[f'{d}png/{name}.png'] = svg_p, pdf_p, png_p
            lines[name] = fields
        # recueils : par chart, ses pages dans l'ordre naturel des suffixes
        charts = {}
        for n, (d, name, stem, suffix) in enumerate(pages):
            charts.setdefault((d, stem), []).append((suffix, n))
        for k, ((d, stem), members) in enumerate(charts.items()):
            members.sort(key=lambda t: dcsdata.natural_key(t[0]))
            entry = f'{d}pdf/{stem}_charts.pdf'
            if entry not in names:
                raise UpdateError(f'{stem} : recueil {entry} absent du zip')
            out = tmp / f'recueil{k}.pdf'
            export.merge_pdfs([new[f'{d}pdf/{pages[n][1]}.pdf'] for _, n in members], out)
            before = len(PdfReader(io.BytesIO(zin.read(entry))).pages)
            after = len(PdfReader(str(out)).pages)
            if before != after:
                raise UpdateError(f'{stem} : recueil de {after} pages, l\'ancien en avait {before}')
            new[entry] = out
        # le nouveau zip : memes entrees, meme ordre, meme methode de compression
        copied = 0
        try:
            with zipfile.ZipFile(zip_out, 'x') as zout:
                for info in infos:
                    zi = zipfile.ZipInfo(info.filename, info.date_time)
                    zi.compress_type, zi.external_attr, zi.create_system = info.compress_type, info.external_attr, info.create_system
                    zi.comment = info.comment
                    if info.filename in new:
                        with open(new[info.filename], 'rb') as src, zout.open(zi, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                    else:
                        with zin.open(info) as src, zout.open(zi, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                        copied += 1
        except BaseException:
            zip_out.unlink(missing_ok=True)
            raise
    return lines, len(pages), len(charts), copied


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='update_coords.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zip', required=True, help='zip de release (airfields ou helicopters)')
    ap.add_argument('--out', required=True, help='nouveau zip (ne doit pas exister)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        lines, n_pages, n_charts, copied = update_zip(args.theatre, args.zip, args.out)
    except (UpdateError, dcsdata.DataError, FileNotFoundError, zipfile.BadZipFile) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(f'{args.zip} -> {args.out}')
    print(f'pages mises a jour : {n_pages}')
    print(f'recueils refaits : {n_charts}')
    print(f'entrees recopiees telles quelles : {copied}')
    for first in list(lines)[:1]:
        print(f'{first} : {line_of(lines[first])}')
    if not lines:
        print('aucune page de chart dans ce zip : toutes les entrees sont recopiees telles quelles')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
