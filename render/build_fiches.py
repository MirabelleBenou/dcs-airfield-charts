"""Paquet de fiches du Route Planner de DCS Mission Plan : chaque page de chart et chaque page de plans du repertoire des
heliports, en images, avec un `index.json` au contrat `dcs-fiches-v1` (lot ac-paquet-fiches).

Usage : python -I render/build_fiches.py <Theatre> --zips <zip airfields> <zip helicopters> --airfields <airfields.json>
                                         --edition <AC-Vx.y.z> --out <dossier>
Entrees : les zips *airfields* et *helicopters* de la release du theatre, et `airfields.json` de Mission Plan.
Sortie : <out>/<Theatre>/ (index.json et images) et <out>/DCS_Airfield_Charts_<Theatre>_1985_route_planner.zip (tout a la
racine). Un controle de la mise en page du repertoire, obligatoire, s'execute a chaque fois ; s'il echoue, rien n'est ecrit.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import fiches                                             # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_fiches.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zips', nargs=2, required=True, metavar=('AIRFIELDS_ZIP', 'HELICOPTERS_ZIP'))
    ap.add_argument('--airfields', required=True, help='airfields.json de Mission Plan')
    ap.add_argument('--edition', required=True, help='version des charts, ex. AC-V1.4.2')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        pkg = fiches.build_package(args.theatre, args.zips[0], args.zips[1], args.airfields, args.edition)
        _, zpath = fiches.write_package(pkg, args.out, args.theatre)
    except fiches.FichesError as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(fiches.summary(pkg, fiches.resolve_theatre(args.theatre)[0], zpath))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
