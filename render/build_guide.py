"""Guide de lecture des charts : un PDF A4 portrait, en anglais, fabrique a partir des zips d'une release.

Usage : python -I render/build_guide.py --zips <zip airfields de Syria> <zip helicopters de Syria> --edition <AC-Vx.y.z>
                                         --out <fichier.pdf> [--pins <pastilles.txt>]
Entrees : les zips *airfields* et *helicopters* de la release de Syria (Konya, Naqoura, l'annuaire des heliports).
Sortie : le PDF (refuse d'ecraser un fichier existant) ; avec --pins, la liste des pastilles et de leur cible resolue.
L'outil s'arrete, sans rien ecrire, quand une page qu'il annote n'a plus ce qu'une pastille vise, quand un chiffre cite par
un texte du guide n'est plus sur sa page, ou quand un texte deborde de sa zone.
"""
import argparse
import os
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


def check_destination(path, name):
    """Une destination valide : elle n'existe pas (lien compris) et son dossier existe ou peut etre cree sous un dossier
    existant et inscriptible. GuideError sinon."""
    if os.path.lexists(path):
        raise guide.GuideError(f'{name} {path} existe deja : l\'outil n\'ecrase rien')
    anc = path.parent
    while not os.path.lexists(anc) and anc != anc.parent:
        anc = anc.parent
    if not anc.is_dir():
        raise guide.GuideError(f'{name} {path} : {anc} n\'est pas un dossier')
    if not os.access(anc, os.W_OK | os.X_OK):
        raise guide.GuideError(f'{name} {path} : le dossier {anc} n\'est pas inscriptible')


def check_destinations(out, pins):
    """Les deux destinations (dossiers parents compris) se controlent avant toute ecriture."""
    if pins is not None and out.resolve() == pins.resolve():
        raise guide.GuideError(f'--pins et --out designent le meme fichier ({out.resolve()}) : l\'outil n\'ecrase rien')
    check_destination(out, '--out')
    if pins is not None:
        check_destination(pins, '--pins')


def write_all(files):
    """Ecrit chaque (chemin, octets) en creation exclusive ; si une ecriture echoue, tout ce que cet appel a cree
    (fichiers, puis dossiers) est retire : un arret n'ecrit rien."""
    made_files, made_dirs = [], []
    try:
        for path, data in files:
            missing, d = [], path.parent
            while not d.exists():
                missing.append(d)
                d = d.parent
            for m in reversed(missing):
                m.mkdir()
                made_dirs.append(m)
            with open(path, 'xb') as f:
                made_files.append(path)
                f.write(data)
    except BaseException:
        for f in made_files:
            try:
                f.unlink()
            except OSError:
                pass
        for d in reversed(made_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        raise


def main(argv):
    args = parse_args(argv)
    out = Path(args.out)
    pins_path = Path(args.pins) if args.pins else None
    try:
        check_destinations(out, pins_path)
        g = guide.build({'airfields': args.zips[0], 'helicopters': args.zips[1]}, args.edition)
        files = [(out, g.pdf)]
        if pins_path is not None:
            files.append((pins_path, guide.pins_report(g).encode('utf-8')))
        write_all(files)
    except (guide.GuideError, OSError) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    except Exception as e:                                    # jamais une trace Python : l'arret nomme l'erreur
        print(f'ARRET : erreur inattendue ({type(e).__name__} : {e})', file=sys.stderr)
        return 1
    print(guide.summary(g))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
