"""Site statique d'Airfield Charts, a publier sur GitHub Pages (lot ac-site-pages).

Usage : python -I render/build_site.py --zips <dossier> --edition <AC-Vx.y.z> --repo <proprietaire/depot> --out <dossier>
Entrees : les six zips de la release (`DCS_Airfield_Charts_<Theatre>_1985_{airfields,helicopters,lotatc}.zip`, Syria et
GermanyColdWar) dans --zips, et les PDF de `downloads/` du depot (existence seulement).
Sortie : le site complet dans --out (qui ne doit pas exister). Au premier cas d'arret rien n'est ecrit.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import dcsdata, fiches, site                              # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_site.py', description=__doc__.split('\n')[0])
    ap.add_argument('--zips', required=True, help='dossier des zips de la release')
    ap.add_argument('--edition', required=True, help='tag de la release, ex. AC-V1.11.0')
    ap.add_argument('--repo', required=True, help='<proprietaire>/<depot> GitHub')
    ap.add_argument('--out', required=True, help='dossier de sortie (ne doit pas exister)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        lines = site.build_site(args.zips, args.edition, args.repo, args.out)
    except (site.SiteError, fiches.FichesError, dcsdata.DataError) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
