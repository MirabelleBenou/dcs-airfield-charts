"""Guide de lecture des charts : un PDF A4 portrait, en anglais, fabrique a partir des zips d'une release.

Usage : python -I render/build_guide.py --zips <zip airfields de Syria> <zip helicopters de Syria> --edition <AC-Vx.y.z>
                                         --out <fichier.pdf> [--pins <pastilles.txt>]
Entrees : les zips *airfields* et *helicopters* de la release de Syria (Konya, Naqoura, l'annuaire des heliports).
Sortie : le PDF (refuse d'ecraser un fichier existant) ; avec --pins, la liste des pastilles et de leur cible resolue.
L'outil s'arrete, sans rien ecrire, quand une page qu'il annote n'a plus ce qu'une pastille vise, quand un chiffre cite par
un texte du guide n'est plus sur sa page, ou quand un texte deborde de sa zone.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import guide                                              # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='build_guide.py', description=__doc__.split('\n')[0])
    ap.add_argument('--zips', nargs=2, required=True, metavar=('AIRFIELDS_ZIP', 'HELICOPTERS_ZIP'))
    ap.add_argument('--edition', required=True, help='version des charts, ex. AC-V1.9.0')
    ap.add_argument('--out', required=True, help='fichier PDF a ecrire')
    ap.add_argument('--pins', help='fichier texte : la liste des pastilles et leur cible resolue')
    return ap.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    out = Path(args.out)
    pins_path = Path(args.pins) if args.pins else None
    try:
        for target in (out, pins_path):
            if target is not None and target.exists():
                raise guide.GuideError(f'{target} existe deja : l\'outil n\'ecrase rien')
        g = guide.build({'airfields': args.zips[0], 'helicopters': args.zips[1]}, args.edition)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(g.pdf)
        if pins_path is not None:
            pins_path.parent.mkdir(parents=True, exist_ok=True)
            pins_path.write_text(guide.pins_report(g), encoding='utf-8')
    except (guide.GuideError, OSError) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    print(guide.summary(g))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
