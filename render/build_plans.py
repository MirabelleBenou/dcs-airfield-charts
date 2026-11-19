"""Plans d'aerodrome de DCS Mission Plan : le plan 10-9 de chaque chart, reduit au revetement, aux pistes, a leurs marquages
et aux batiments, avec un `index.json` au contrat `dcs-plans-v1` ; et, en option, les places en abri (lot ac-plans-v1).

Usage : python -I render/build_plans.py <Theatre> --zips <zip airfields> <zip helicopters> --airfields <airfields.json>
                                        --parkings <downloads/parkings/<cle>.json> --edition <AC-Vx.y.z> --out <dossier>
                                        [--shelters <fichier>]
Entrees : les zips *airfields* et *helicopters* de la release du theatre, `airfields.json` de Mission Plan et les places de
`downloads/parkings/`.
Sortie : <out>/<cle>/ (index.json et un SVG par aerodrome) et <out>/DCS_Airfield_Charts_<Theatre>_1985_plans.zip (tout a la
racine) ; avec --shelters, le fichier `downloads/parkings/shelters/<cle>.json`. Un arret n'ecrit rien.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import fiches, plans                                      # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_plans.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='GermanyColdWar ou Syria')
    ap.add_argument('--zips', nargs=2, required=True, metavar=('AIRFIELDS_ZIP', 'HELICOPTERS_ZIP'))
    ap.add_argument('--airfields', required=True, help='airfields.json de Mission Plan')
    ap.add_argument('--parkings', required=True, help='downloads/parkings/<cle>.json')
    ap.add_argument('--edition', required=True, help='version des charts, ex. AC-V1.7.1')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    ap.add_argument('--shelters', help='fichier des places en abri a ecrire (downloads/parkings/shelters/<cle>.json)')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        plans.check_targets(args.out, args.theatre, args.shelters)
        pkg = plans.build_package(args.theatre, args.zips[0], args.zips[1], args.airfields, args.parkings, args.edition)
        _, zpath = plans.write_package(pkg, args.out, args.theatre, args.shelters)
    except fiches.FichesError as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(plans.summary(pkg, fiches.resolve_theatre(args.theatre)[0], zpath))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
