"""Relief de DCS en tuiles web : la grille du relief d'un theatre, une couche d'ombrage transparent (`hillshade`) et une couche
d'altitude au format Terrain-RGB (`elevation`), en tuiles XYZ Web Mercator du zoom 6 au zoom 11, et un zip qui porte les deux.

Usage : python -I render/build_relief.py <Theatre> --entrees <zip ou csv> [<zip ou csv> ...] --edition <texte> --out <dossier>
                                         [--processus <n>] [--grille-seule]
Entrees : les zips du releve du relief au pas de 100 m (dans un zip, seuls les membres out/relief/<nom DCS du theatre>/*_100m_export.csv
sont lus : CSV `i,j,x,z,alt_m,surface`, lus sans decompresser le zip), ou des CSV nus. Theatre : Syria, GermanyColdWar ou Caucasus.
Sortie : <out>/relief_<cle>.npy et .json (la grille), <out>/tiles/{hillshade,elevation}/{z}/{x}/{y}.{webp,png} et
<out>/DCS_Airfield_Charts_<Theatre>_relief.zip avec son <zip>.json. L'emprise des tuiles est le rectangle de la grille (elle peut
deborder de `terrain.bounds` de pydcs, qui ne commande que la regle « noeud manquant dans l'emprise du theatre : refus »).
--grille-seule : n'ecrit que relief_<cle>.npy et .json, puis s'arrete (ni tuiles, ni zip ; --edition n'est pas exigee).
Codes : 0 fait, 1 usage, 2 refus sur les donnees (point hors noeud, bord commun en desaccord, noeud manquant dans l'emprise...)
ou dossier de sortie qu'on ne peut pas preparer (anciennes tuiles impossibles a effacer).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

from render import relief                                             # noqa: E402

T_DEBUT = time.perf_counter()


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def _log(msg):
    print(f'[{time.perf_counter() - T_DEBUT:8.1f} s] {msg}', flush=True)


def parse_args(argv):
    ap = _Parser(prog='build_relief.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='Syria, GermanyColdWar ou Caucasus')
    ap.add_argument('--entrees', nargs='+', required=True, metavar='ZIP_OU_CSV')
    ap.add_argument('--edition', default=None, help='texte d\'edition porte par relief.json (exigee sans --grille-seule)')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    ap.add_argument('--processus', type=int, default=1, help='processus qui se partagent les blocs (defaut 1)')
    ap.add_argument('--grille-seule', action='store_true', help='n\'ecrire que la grille (relief_<cle>.npy et .json)')
    a = ap.parse_args(argv)
    if a.edition is None and not a.grille_seule:
        raise _Usage('--edition : exigee (sauf avec --grille-seule)')
    if a.processus < 1:
        raise _Usage('--processus : un entier >= 1')
    if relief.nom_dcs(a.theatre) is None:
        raise _Usage(f'theatre inconnu : {a.theatre} (Syria, GermanyColdWar ou Caucasus)')
    for e in a.entrees:
        if not Path(e).is_file():
            raise _Usage(f'entree introuvable : {e}')
    return a


def _memoire_pointe():
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v / 1024.0 if sys.platform != 'darwin' else v / (1024.0 * 1024.0)       # Mio
    except ImportError:
        return None


def _log_memoire():
    m = _memoire_pointe()
    if m is not None:
        _log(f'pointe de memoire de ce processus : {m:.0f} Mio')


def main(argv=None):
    try:
        a = parse_args(sys.argv[1:] if argv is None else argv)
    except _Usage as e:
        print(f'usage : {e}', file=sys.stderr)
        return 1
    theatre = relief.nom_dcs(a.theatre)
    cle = relief.cle_theatre(a.theatre)
    out = Path(a.out)
    t0 = time.perf_counter()
    emprise = relief.emprise_theatre(theatre)
    _log(f'{theatre} ({cle}) : emprise x {emprise[0]:g} a {emprise[2]:g}, z {emprise[1]:g} a {emprise[3]:g}')
    try:
        grille = relief.construire_grille(a.entrees, theatre, emprise=emprise, log=_log)
    except relief.RefusDonnees as e:
        print(f'refus : {e}', file=sys.stderr)
        return 2
    _log(f'phase lecture et grille : {time.perf_counter() - t0:.1f} s')
    t0 = time.perf_counter()
    npy, doc = relief.ecrire_grille(grille, out, theatre, cle)
    x0, z0, entrees = grille.x0, grille.z0, grille.entrees
    emprise_tuiles = relief.emprise_grille(grille)
    _log(f'phase ecriture de la grille : {time.perf_counter() - t0:.1f} s ({npy.name}, forme {doc["forme"]}, altitudes de {doc["alt_min_m"]} a {doc["alt_max_m"]} m)')
    if a.grille_seule:
        _log(f'sha256 des octets float32 de la grille : {grille.sha256_octets()}')
        _log_memoire()
        _log('fait (grille seule)')
        return 0
    del grille
    t0 = time.perf_counter()
    _log(f'emprise des tuiles = rectangle de la grille : x {emprise_tuiles[0]:g} a {emprise_tuiles[2]:g}, z {emprise_tuiles[1]:g} a '
         f'{emprise_tuiles[3]:g}')
    try:
        res = relief.produire_tuiles(npy, theatre, x0, z0, emprise_tuiles, out, processus=a.processus, log=_log)
    except relief.ErreurSortie as e:
        print(f'erreur : {e}', file=sys.stderr)
        return 2
    _log(f'phase tuiles : {time.perf_counter() - t0:.1f} s (rendu {res["ms"]["rendu"] / 1000:.1f} s, pyramide '
         f'{(res["ms"]["pyramide"] + res["ms"]["haut"]) / 1000:.1f} s, codage {res["ms"]["codage"] / 1000:.1f} s, '
         f'cumules sur les processus)')
    t0 = time.perf_counter()
    info = relief.ecrire_zip(out, theatre, cle, a.edition, res['bounds'], entrees)
    _log(f'phase zip : {time.perf_counter() - t0:.1f} s ({info["name"]}, {info["bytes"]} octets, sha256 {info["sha256"]})')
    for couche, par_zoom in info['tiles_per_zoom'].items():
        _log(f'tuiles {couche} : ' + ', '.join(f'z{z} {n}' for z, n in par_zoom.items()) + f' (total {sum(par_zoom.values())})')
    _log_memoire()
    _log('fait')
    return 0


if __name__ == '__main__':
    sys.exit(main())
