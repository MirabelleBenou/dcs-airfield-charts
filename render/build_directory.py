"""Repertoire des heliports A5 : 4 vignettes par page, un plan de la carte F10 par groupe d'aires (lot ac-heliports).

Usage : python -I render/build_directory.py <Theatre> [--out <dossier>] [--config <chemin>]
Ecrit <out>/<theatre>/<Theatre>_heliports_directory.pdf (toutes les pages) et une PNG par page,
<Theatre>_heliports_directory_p<NN>.png (1748 x 2480 px, A5 a 300 dpi).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'capture'))

import config as capture_config                                       # noqa: E402
from render import directory                                          # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_directory.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre')
    ap.add_argument('--out', help='dossier de sortie (defaut : <work_dir>/charts)')
    return ap.parse_args(argv)


def main(argv):
    cfg, rest = capture_config.load_config(argv)
    args = parse_args(rest[1:])
    theatre = args.theatre
    out_root = Path(args.out) if args.out else Path(cfg.work_dir) / 'charts'
    folder = out_root / theatre.lower()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{theatre}_heliports_directory'

    towns = directory.load_towns(theatre)
    sites = directory.list_sites(cfg, theatre)
    for s in sites:
        s.town = directory.nearest_town(s.lat, s.lon, towns)
    pages = directory.build_pages(sites)
    n_index = directory.index_page_count(len(sites))
    for k, pg in enumerate(pages):
        for site, _ in pg['entries']:
            site.page = n_index + k + 1
    print(f'{len(sites)} sites, {len(pages)} pages de plans + {n_index} d\'index = {n_index + len(pages)} pages')

    png_paths = []

    def save(im, no):
        p = folder / f'{stem}_p{no:02d}.png'
        im.save(p)
        png_paths.append(p)

    for k in range(n_index):
        save(directory.draw_index_page(theatre, sites, k, n_index), k + 1)
    stats = []
    cache_key, cache_val = [None], [None]

    def cache(site):
        if cache_key[0] != site.name:
            cache_key[0], cache_val[0] = site.name, directory.load_site_mosaic(site)
        return cache_val[0]

    for k, pg in enumerate(pages):
        im = directory.draw_directory_page(theatre, pg, cache, stats)
        save(im, n_index + k + 1)
        print(f'p{n_index + k + 1:02d} {pg["series"]} {pg["rank"]}/{pg["count"]} : '
              + ', '.join(f'{s.name} ({s.form}, case {pos})' for s, pos in pg['entries']))
        del im
    for s in sites:
        print(f'site {s.name} : {len(s.aires)} aire(s), {len(s.groups)} groupe(s), {s.form}, page {s.page}, {s.town}'
              f' | dessinees {len(s.drawn)}/{len(s.aires)} | bleu fond efface {s.blue_mosaic}')
    for name, n_blue, biggest in stats:
        print(f'bleu {name} : {n_blue} px, plus gros amas {biggest}')

    from PIL import Image
    ims = [Image.open(p) for p in png_paths]
    ims[0].save(folder / f'{stem}.pdf', 'PDF', resolution=300.0, save_all=True, append_images=ims[1:], quality=95)
    print(f'recueil : {stem}.pdf ({len(ims)} pages)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
