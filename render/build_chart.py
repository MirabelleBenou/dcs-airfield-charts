"""Rendu d'un chart d'aerodrome A5 (10-9 et pages de parkings) a partir d'une base capturee.

Usage : python -I render/build_chart.py <Theatre> <Base> [--annee 1985] [--icao LTAN] [--osm <fichier>]
                                         [--out <dossier>] [--config <chemin>]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'capture'))

import config as capture_config                                       # noqa: E402
from render import analysis, compose, export                           # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_chart.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre')
    ap.add_argument('base')
    ap.add_argument('--annee', type=float, default=None, help='annee de la declinaison (defaut : config, puis 1985)')
    ap.add_argument('--icao')
    ap.add_argument('--osm', help='fichier OSM (defaut : <osm_dir>/<theatre>/<Base>.json)')
    ap.add_argument('--out', help='dossier de sortie (defaut : <work_dir>/charts)')
    return ap.parse_args(argv)


def main(argv):
    cfg, rest = capture_config.load_config(argv)
    args = parse_args(rest[1:])
    year = args.annee if args.annee is not None else cfg.annee
    osm_dir = cfg.osm_dir
    out_root = Path(args.out) if args.out else Path(cfg.work_dir) / 'charts'
    data = analysis.analyze(cfg, args.theatre, args.base, year, args.icao, args.osm, osm_dir, log=print)
    pages = compose.build_pages(data, log=print)
    folder = out_root / args.theatre.lower()
    stem = f'{data.icao}_{args.base}'
    pdfs = []
    for pg in pages:
        svg, pdf, png = export.export_page(pg.svg, folder / f'{stem}_{pg.id}')
        pdfs.append(pdf)
        print(f'{pg.id} : {pdf.name}, {png.name}, {svg.name}')
    export.merge_pdfs(pdfs, folder / f'{stem}_charts.pdf')
    print(f'recueil : {stem}_charts.pdf ({len(pdfs)} pages)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
