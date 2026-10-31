"""Republication des charts deja publiees, sans les captures F10 : toutes les retouches que le moteur sait faire sur un SVG publie.

Usage : python -I render/republish.py <Theatre> --zip <zip de release> --out <nouveau zip>
Entree : un zip de la release (airfields ou helicopters). Chaque `…/svg/<page>.svg` est une page de chart. Retouches :
  1. la ligne d'en-tete (textes a y = 21.2) : a cinq champs, elle est verifiee egale a `page.header_row` et laissee telle
     quelle ; a trois champs (AC-V1.4.1), elle est reecrite comme `update_coords.py` ;
  2. la fin de la barre d'echelle : la page `10-9` a exactement une barre (ligne horizontale de 0,35 mm) ; si aucune etiquette
     de metres ne finit par ` M`, la graduation de fin recoit `page.scale_end_label` (750 M, 1250 M, 1750 M). Une page de
     parkings (`10-9A`, `10-9B`…) n'a pas de barre ; en trouver une est une erreur ;
  3. `xlink:href` sur une `<image>` qui ne l'a pas (page dessinee sur le fond F10).
Chaque page est ensuite reexportee (SVG, PDF, PNG ; une page avec `<image>` : PNG a 256 couleurs, /Interpolate dans le PDF) et le
recueil `pdf/<stem>_charts.pdf` de chaque chart est refait. Sortie : un nouveau zip, memes entrees dans le meme ordre ; les
entrees hors charts sont recopiees a l'octet (un zip sans page de chart est recopie tel quel). Un fichier de sortie qui existe
deja : arret. Au moindre doute, rien n'est ecrit.
"""
import argparse
import html
import io
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from pypdf import PdfReader                                           # noqa: E402

from render import dcsdata, export, page, update_coords               # noqa: E402
from render.update_coords import ROW_Y, UpdateError, _PAGE_NAME, _PAGE_SVG, _TEXT, _Y   # noqa: E402

RepublishError = UpdateError
BAR_WIDTH = '0.35'
_LINE = re.compile(r'<line\b([^>]*?)/>')
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
_IMAGE = re.compile(r'<image\b[^>]*>')
_HREF = re.compile(r'(?<![\w:])href="([^"]*)"')
EPS = 0.0015                       # mm : deux nombres ecrits a trois decimales


def _attrs(s):
    return dict(_ATTR.findall(s))


# ---------- 1. ligne d'en-tete ----------
def header_elements(svg_text, name):
    """[(debut, fin, texte lu)] des <text> a y = 21.2 : trois (AC-V1.4.1) ou cinq (AC-V1.5.0), sinon RepublishError."""
    found = []
    for m in _TEXT.finditer(svg_text):
        y = _Y.search(m.group(1))
        if y and y.group(1) == ROW_Y:
            found.append((m.start(), m.end(), html.unescape(m.group(2))))
    if len(found) not in (3, 5):
        raise RepublishError(f'{name} : {len(found)} textes a y = {ROW_Y} (attendu : 3 ou 5)')
    return found


def check_or_rewrite_header(svg_text, name, index):
    """(nouveau SVG, [champs], 'reecrit' | 'verifie') : trois champs -> `update_coords.rewrite_page` ; cinq champs -> egaux,
    element par element et a l'octet, a ceux de `page.header_row`, sinon RepublishError."""
    found = header_elements(svg_text, name)
    if len(found) == 3:
        new, fields = update_coords.rewrite_page(svg_text, name, index)
        return new, fields, 'reecrit'
    texts = [t for _, _, t in found]
    if not texts[1].startswith('ARP '):
        raise RepublishError(f"{name} : le deuxieme texte de la ligne ne commence pas par « ARP » : {texts[1]!r}")
    hits = index.get(texts[1], [])
    if len(hits) != 1:
        raise RepublishError(f'{name} : {texts[1]!r} designe {len(hits)} aerodromes pydcs '
                             f'({", ".join(n for n, _, _ in hits) or "aucun"}) au lieu d\'un seul')
    _, lat, lon = hits[0]
    try:
        expected = page.header_row(texts[0], lat, lon, texts[4])
    except ValueError as e:
        raise RepublishError(f'{name} : {e}') from None
    for k, (s, e, _) in enumerate(found):
        if svg_text[s:e] != expected[k]:
            raise RepublishError(f"{name} : le champ {k + 1} de la ligne d'en-tete differe de page.header_row : "
                                 f'{svg_text[s:e]!r} au lieu de {expected[k]!r}')
    for k in range(4):
        sep = svg_text[found[k][1]:found[k + 1][0]]
        if sep.strip():
            raise RepublishError(f'{name} : separateur inattendu dans la ligne d\'en-tete : {sep!r}')
    return svg_text, texts, 'verifie'


# ---------- 2. fin de la barre d'echelle ----------
def find_bars(svg_text):
    """Les lignes horizontales de 0,35 mm : [(debut, fin, attributs)]."""
    out = []
    for m in _LINE.finditer(svg_text):
        a = _attrs(m.group(1))
        if a.get('stroke-width') == BAR_WIDTH and a.get('y1') is not None and a.get('y1') == a.get('y2'):
            out.append((m.start(), m.end(), a))
    return out


def complete_scale_bar(svg_text, name, suffix):
    """(nouveau SVG, None | (texte, x, y)) : la page `10-9` a exactement une barre, une page de parkings n'en a aucune
    (RepublishError sinon). Si la barre n'a pas d'etiquette de metres finissant par ` M`, l'etiquette de fin est inseree juste
    apres la ligne de la graduation de fin ; une barre qui a deja son « M » ne change pas."""
    bars = find_bars(svg_text)
    if suffix != '10-9':
        if bars:
            raise RepublishError(f'{name} : page de parkings avec {len(bars)} barre(s) d\'echelle (attendu : aucune)')
        return svg_text, None
    if len(bars) != 1:
        raise RepublishError(f'{name} : {len(bars)} barres d\'echelle (attendu : une)')
    _, _, bar = bars[0]
    x0_bar, x_end, y = float(bar['x1']), float(bar['x2']), float(bar['y1'])
    if x_end <= x0_bar:
        raise RepublishError(f'{name} : barre d\'echelle de longueur nulle ou inversee')
    labels = []
    for m in _TEXT.finditer(svg_text):
        a = _attrs(m.group(1))
        if a.get('text-anchor') != 'middle' or 'x' not in a or 'y' not in a:
            continue
        if abs(float(a['y']) - (y + 4.2)) < EPS and x0_bar - EPS <= float(a['x']) <= x_end + EPS:
            labels.append((float(a['x']), html.unescape(m.group(2))))
    if any(t.endswith(' M') for _, t in labels):
        return svg_text, None
    xs = {t: [x for x, tt in labels if tt == t] for t in ('0', '500')}
    if len(xs['0']) != 1 or len(xs['500']) != 1:
        raise RepublishError(f'{name} : etiquettes « 0 » et « 500 » de la barre introuvables ou multiples '
                             f'(etiquettes de metres : {[t for _, t in labels]})')
    length = (x_end - xs['0'][0]) * 500 / (xs['500'][0] - xs['0'][0])
    L = round(length / 250) * 250
    if abs(length - L) > 0.1 or L <= 0:
        raise RepublishError(f'{name} : longueur de la barre {length:.2f} m, pas un multiple de 250 m')
    if L % 500 == 0:
        raise RepublishError(f'{name} : la barre de {L} m finit sur une graduation majeure sans etiquette « M »')
    ticks = []
    for m in _LINE.finditer(svg_text):
        a = _attrs(m.group(1))
        if (a.get('x1') is not None and a.get('x1') == a.get('x2') and abs(float(a['x1']) - x_end) < EPS
                and abs(float(a['y1']) - y) < EPS and float(a['y2']) > float(a['y1'])):
            ticks.append((m.end(), float(a['x1'])))
    if len(ticks) != 1:
        raise RepublishError(f'{name} : {len(ticks)} graduations de fin de barre (attendu : une)')
    end, xm = ticks[0]
    el = page.scale_end_label(xm, y, L)
    return svg_text[:end] + '\n' + el + svg_text[end:], (f'{L} M', xm, y + 4.2)


# ---------- 3. xlink:href ----------
def add_xlink_href(svg_text, name):
    """(nouveau SVG, nombre d'<image> corrigees) : `xlink:href` (meme valeur) juste apres `href` sur chaque <image> qui n'en a pas."""
    n = 0

    def fix(m):
        nonlocal n
        el = m.group(0)
        if ' xlink:href="' in el:
            return el
        h = _HREF.search(el)
        if not h:
            raise RepublishError(f'{name} : <image> sans href')
        n += 1
        return el[:h.end()] + f' xlink:href="{h.group(1)}"' + el[h.end():]
    return _IMAGE.sub(fix, svg_text), n


# ---------- une page ----------
def retouch_page(svg_text, name, suffix, index):
    """(nouveau SVG, details) : toutes les retouches ; details = {fields, header, bar, image}."""
    svg, fields, header = check_or_rewrite_header(svg_text, name, index)
    svg, bar = complete_scale_bar(svg, name, suffix)
    has_image = '<image' in svg
    svg, n_xlink = add_xlink_href(svg, name)
    return svg, {'fields': fields, 'header': header, 'bar': bar, 'image': has_image, 'xlink': n_xlink}


@dataclass
class Result:
    pages: int = 0
    charts: int = 0
    copied: int = 0
    rewritten: int = 0
    verified: int = 0
    bars: list = field(default_factory=list)          # [(page, texte, x, y)]
    f10: list = field(default_factory=list)           # noms des pages avec <image>
    lines: dict = field(default_factory=dict)         # {page: [champs]}


def republish_zip(theatre, zip_in, zip_out):
    """Calcule les nouvelles pages et les recueils, puis ecrit `zip_out`. Retourne un `Result`."""
    zip_in, zip_out = Path(zip_in), Path(zip_out)
    if zip_out.exists():
        raise RepublishError(f'{zip_out} existe deja (pas d\'ecrasement)')
    index = update_coords.arp_index(theatre)
    res = Result()
    with zipfile.ZipFile(zip_in) as zin, tempfile.TemporaryDirectory(prefix='republish_') as tmp:
        tmp = Path(tmp)
        infos = zin.infolist()
        names = {i.filename for i in infos}
        pages = []                                 # [(dir, page, stem, suffix)] dans l'ordre du zip
        for info in infos:
            m = _PAGE_SVG.match(info.filename)
            if not m:
                continue
            pm = _PAGE_NAME.match(m['page'])
            if not pm:
                raise RepublishError(f'{info.filename} : nom de page inattendu (attendu <stem>_10-9[A-Z]*)')
            pages.append((m['dir'], m['page'], pm['stem'], pm['suffix']))
        new = {}                                   # nom d'entree -> fichier temporaire
        for n, (d, name, _, suffix) in enumerate(pages):
            svg_old = zin.read(f'{d}svg/{name}.svg').decode('utf-8')
            svg_new, det = retouch_page(svg_old, name, suffix, index)
            for sub, ext in (('png', 'png'), ('pdf', 'pdf')):
                if f'{d}{sub}/{name}.{ext}' not in names:
                    raise RepublishError(f'{name} : entree {d}{sub}/{name}.{ext} absente du zip')
            work = tmp / f'p{n}'
            svg_p, pdf_p, png_p = export.export_page(svg_new, work / 'page')
            if svg_p.read_bytes() != svg_new.encode('utf-8'):
                raise RepublishError(f'{name} : le SVG ecrit differe du SVG calcule')
            new[f'{d}svg/{name}.svg'], new[f'{d}pdf/{name}.pdf'], new[f'{d}png/{name}.png'] = svg_p, pdf_p, png_p
            res.lines[name] = det['fields']
            if det['header'] == 'reecrit':
                res.rewritten += 1
            else:
                res.verified += 1
            if det['bar']:
                res.bars.append((name, *det['bar']))
            if det['image']:
                res.f10.append(name)
        res.pages = len(pages)
        charts = {}
        for n, (d, name, stem, suffix) in enumerate(pages):
            charts.setdefault((d, stem), []).append((suffix, n))
        for k, ((d, stem), members) in enumerate(charts.items()):
            members.sort(key=lambda t: dcsdata.natural_key(t[0]))
            entry = f'{d}pdf/{stem}_charts.pdf'
            if entry not in names:
                raise RepublishError(f'{stem} : recueil {entry} absent du zip')
            out = tmp / f'recueil{k}.pdf'
            export.merge_pdfs([new[f'{d}pdf/{pages[n][1]}.pdf'] for _, n in members], out)
            before = len(PdfReader(io.BytesIO(zin.read(entry))).pages)
            after = len(PdfReader(str(out)).pages)
            if before != after:
                raise RepublishError(f'{stem} : recueil de {after} pages, l\'ancien en avait {before}')
            new[entry] = out
        res.charts = len(charts)
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
                        res.copied += 1
        except BaseException:
            zip_out.unlink(missing_ok=True)
            raise
    return res


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='republish.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zip', required=True, help='zip de release (airfields ou helicopters)')
    ap.add_argument('--out', required=True, help='nouveau zip (ne doit pas exister)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        res = republish_zip(args.theatre, args.zip, args.out)
    except (RepublishError, dcsdata.DataError, FileNotFoundError, zipfile.BadZipFile) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(f'{args.zip} -> {args.out}')
    print(f'pages reexportees : {res.pages}')
    print(f"en-tetes reecrits : {res.rewritten} ; verifies : {res.verified}")
    print(f"barres completees : {len(res.bars)}")
    for name, text, _, _ in res.bars:
        print(f'  {name} : {text}')
    print(f"pages a fond F10 : {len(res.f10)}" + ''.join(f'\n  {n}' for n in res.f10))
    print(f'recueils refaits : {res.charts}')
    print(f'entrees recopiees telles quelles : {res.copied}')
    if not res.pages:
        print('aucune page de chart dans ce zip : toutes les entrees sont recopiees telles quelles')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
