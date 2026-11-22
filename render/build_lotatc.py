"""Dessins LotAtc de chaque aerodrome cartographie d'un theatre, pour les controleurs aeriens (lot ac-lotatc-v1).

Usage : python -I render/build_lotatc.py <Theatre> --zips <zip airfields> <zip helicopters> --airfields <airfields.json>
                                         --parkings <downloads/parkings/<cle>.json> --edition <AC-Vx.y.z> --out <dossier>
Entrees : les zips *airfields* et *helicopters* de la release du theatre, `airfields.json` de Mission Plan et les places de
`downloads/parkings/`.
Sortie : <out>/<cle>/ (un calque LotAtc par fichier : le plan <base>.json, les places <base>_parking.json) et
<out>/DCS_Airfield_Charts_<Theatre>_1985_lotatc.zip (les calques et README.txt a la racine). Un arret n'ecrit rien.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import fiches, lotatc                                     # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_lotatc.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zips', nargs=2, required=True, metavar=('AIRFIELDS_ZIP', 'HELICOPTERS_ZIP'))
    ap.add_argument('--airfields', required=True, help='airfields.json de Mission Plan')
    ap.add_argument('--parkings', required=True, help='downloads/parkings/<cle>.json')
    ap.add_argument('--edition', required=True, help='version des charts, ex. AC-V1.10.0')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        lotatc.check_targets(args.out, args.theatre)
        res = lotatc.build(args.theatre, args.zips[0], args.zips[1], args.airfields, args.parkings, args.edition)
        _, zpath = lotatc.write(res, args.out)
    except (fiches.FichesError, OSError) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(lotatc.summary(res, zpath))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
