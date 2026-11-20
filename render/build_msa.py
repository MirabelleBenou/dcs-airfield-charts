"""MSA (altitude minimale de secteur) de chaque terrain d'un theatre, d'apres le relief de DCS.

Usage : python -I render/build_msa.py <Theatre> --grille <relief_<cle>.npy> --parkings <downloads/parkings/<cle>.json>
                                       --edition <texte> --out <dossier> [--annee 1985]
Entrees : la grille du relief ecrite par `render/build_relief.py` (le `.json` est lu a cote du `.npy`) et les terrains de
`downloads/parkings/<cle>.json`. Theatre : Syria, GermanyColdWar ou Caucasus.
Sortie : <out>/<cle>.json (format `dcs-msa-v1`) : une ligne d'en-tete, puis un terrain par ligne, dans l'ordre du fichier des parkings.
Codes : 0 fait, 1 usage, 2 refus (disque d'un terrain incomplet, grille ou parkings d'un autre theatre, entree illisible).
La regle est celle de `render/msa.py`.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script

import numpy as np                                                    # noqa: E402

from render import geo, msa, relief                                   # noqa: E402

FORMAT = 'dcs-msa-v1'
ATTRIBUTION = ('Terrain: DCS World, Eagle Dynamics SA — terrain elevation read in the game. Non-commercial use only. '
               'Not for real-world navigation.')
SOURCE_RELIEF = 'DCS World terrain elevation read in the game, 100 m grid'
SOURCE_TERRAINS = 'downloads/parkings/{cle}.json (pydcs 84f6335, LGPL-3.0)'


class _Usage(Exception):
    pass


class _Refus(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def parse_args(argv):
    ap = _Parser(prog='build_msa.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre', help='Syria, GermanyColdWar ou Caucasus')
    ap.add_argument('--grille', required=True, metavar='RELIEF_NPY', help='relief_<cle>.npy (le .json est lu a cote)')
    ap.add_argument('--parkings', required=True, metavar='PARKINGS_JSON', help='downloads/parkings/<cle>.json')
    ap.add_argument('--edition', required=True, help='texte d\'edition porte par le fichier')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    ap.add_argument('--annee', type=float, default=1985.0, help='annee de la declinaison (defaut 1985)')
    a = ap.parse_args(argv)
    if relief.nom_dcs(a.theatre) is None:
        raise _Usage(f'theatre inconnu : {a.theatre} (Syria, GermanyColdWar ou Caucasus)')
    for f in (a.grille, a.parkings):
        if not Path(f).is_file():
            raise _Usage(f'entree introuvable : {f}')
    return a


def sha256_grille(alt, bloc=256):
    """sha256 des octets float32 de la grille (ordre C), lue par blocs de lignes."""
    h = hashlib.sha256()
    for i in range(0, alt.shape[0], bloc):
        h.update(np.ascontiguousarray(alt[i:i + bloc], dtype=np.float32).tobytes())
    return h.hexdigest()


def charger_grille(chemin, theatre, cle):
    """(alt mmap, x0, z0) de la grille et de son .json voisin ; _Refus si elle est d'un autre theatre ou incoherente."""
    chemin = Path(chemin)
    j = chemin.with_suffix('.json')
    try:
        doc = json.loads(j.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise _Refus(f'{j.name} : description de la grille illisible ({e})')
    if doc.get('theatre') != theatre or doc.get('cle') != cle:
        raise _Refus(f'la grille {chemin.name} est celle du theatre {doc.get("theatre")} ({doc.get("cle")}), pas de {theatre} ({cle})')
    try:
        alt = np.load(chemin, mmap_mode='r')
    except (OSError, ValueError) as e:
        raise _Refus(f'{chemin.name} : grille illisible ({e})')
    if alt.dtype != np.float32 or alt.ndim != 2 or list(alt.shape) != doc.get('forme'):
        raise _Refus(f'{chemin.name} : la grille ne correspond pas a sa description (float32, forme {doc.get("forme")})')
    if float(doc.get('pas_m', 0)) != msa.PAS_M:
        raise _Refus(f'{j.name} : pas de {doc.get("pas_m")} m (attendu {msa.PAS_M:g} m)')
    return alt, float(doc['x0']), float(doc['z0'])


def charger_terrains(chemin, cle):
    try:
        doc = json.loads(Path(chemin).read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise _Refus(f'{Path(chemin).name} : terrains illisibles ({e})')
    if doc.get('theatre') != cle:
        raise _Refus(f'les terrains {Path(chemin).name} sont ceux du theatre {doc.get("theatre")}, pas de {cle}')
    return doc['airdromes']


def regle(annee):
    return {'radius_nm': msa.RAYON_NM, 'centre': 'airfield reference point (pydcs)',
            'sectors': 'magnetic bearing to the centre: 000-090, 090-180, 180-270, 270-360',
            'variation': f'IGRF-14 at the centre, {annee:g}', 'margin_ft': msa.MARGE_FT,
            'mountain_margin_ft': msa.MARGE_MONTAGNE_FT, 'mountain_threshold_m': int(msa.SEUIL_MONTAGNE_M),
            'rounding': 'up to the next 100 ft',
            'terrain': 'ground only: buildings, masts and other obstacles are not included'}


def texte(cle, edition, sha, annee, lignes):
    """Le fichier : l'en-tete sur une ligne, un terrain par ligne ; relisible par json.load."""
    source = {'relief': SOURCE_RELIEF, 'relief_grid_sha256': sha, 'airfields': SOURCE_TERRAINS.format(cle=cle)}
    out = ['{' + f'"format":{_dumps(FORMAT)},"theatre":{_dumps(cle)},"edition":{_dumps(edition)},"source":{_dumps(source)},'
           f'"rule":{_dumps(regle(annee))},"attribution":{_dumps(ATTRIBUTION)},"airdromes":[']
    for n, ligne in enumerate(lignes):
        out.append(_dumps(ligne) + (',' if n < len(lignes) - 1 else ''))
    out.append(']}')
    return '\n'.join(out) + '\n'


def calculer(theatre, cle, alt, x0, z0, terrains, annee, log=lambda *_: None):
    """Les lignes (dicts) des terrains. _Refus au premier terrain dont le disque est incomplet."""
    proj = geo.Projection.for_theatre(theatre)
    lignes = []
    for a in terrains:
        xa, za = a['x'], a['y']                                    # `y` du fichier des parkings = le `z` de la grille
        lat, lon = proj.to_latlon(xa, za)
        decl = geo.declination(float(lat), float(lon), annee)
        gamma = proj.convergence(xa, za)
        try:
            r = msa.msa_terrain(alt, x0, z0, xa, za, gamma, decl)
        except msa.DisqueIncomplet as e:
            raise _Refus(f'terrain {a["name"]} (airdromeId {a["airdromeId"]}) : disque de {msa.RAYON_NM} NM incomplet : {e}')
        lignes.append({'airdromeId': a['airdromeId'], 'name': a['name'], 'x': xa, 'y': za, 'variation_deg': round(decl, 2),
                       'msa_ft': r['msa_ft'], 'sectors': r['sectors']})
    return lignes


def main(argv=None):
    try:
        a = parse_args(sys.argv[1:] if argv is None else argv)
    except _Usage as e:
        print(f'usage : {e}', file=sys.stderr)
        return 1
    theatre = relief.nom_dcs(a.theatre)
    cle = relief.cle_theatre(a.theatre)
    try:
        alt, x0, z0 = charger_grille(a.grille, theatre, cle)
        terrains = charger_terrains(a.parkings, cle)
        lignes = calculer(theatre, cle, alt, x0, z0, terrains, a.annee)
        sha = sha256_grille(alt)
    except _Refus as e:
        print(f'refus : {e}', file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{cle}.json').write_bytes(texte(cle, a.edition, sha, a.annee, lignes).encode('utf-8'))
    print(f'{cle} : {len(lignes)} terrains, MSA de {min(l["msa_ft"] for l in lignes)} a {max(l["msa_ft"] for l in lignes)} ft '
          f'-> {out / (cle + ".json")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
