"""Tuiles XYZ de la carte F10 de DCS : capture en grille a l'echelle d'un zoom cible, reprojection, pyramide, archives.

Usage (python -I capture/f10tiles.py ...) :
  plan      <Theatre> --couche alt|sat --zoom <z> (--emprise <geojson> | --rectangle <lat_s>,<lon_o>,<lat_n>,<lon_e>) [--nom <zone>]
                -> <work_dir>/tuiles_brut/<couche>-<theatre>-z<zoom>-<zone>/grille.json (ne touche pas DCS)
  capture   <memes arguments> [--duree-max-s <s>]
                -> les images de la grille (r<ligne>_c<colonne>.png|webp, captures.jsonl) ; reprend ou elle s'est arretee
  tuiler    <memes arguments> [--zoom-min <z>] [--processus <n>]
                -> les tuiles du zoom cible, la pyramide jusqu'a --zoom-min (defaut 8), les archives sous <work_dir>/out/tuiles/
  aerodromes <Theatre> [<Base> ...]
                -> les tuiles des zooms 17 et 18 des captures de charts out/<theatre>/<Base>/, archives de `alt-<theatre>-aerodromes`
Option : --config <chemin> (sinon variable DCS_CHARTS_CONFIG, sinon capture/config.json ; voir config.example.json)

Codes de sortie : 0 fait (ou arret propre `--duree-max-s`), 2 arguments ou grille refuses, 3 arret qui demande une decision
(sync/QUESTION.txt : etalonnage refuse, mode de la carte, disque, premier plan perdu, crochet muet), 4 `tuiler` sur une grille
incomplete.

La camera, l'ecran et l'etalonnage sont ceux de f10capture.py (importe, non modifie) ; la geometrie, la reprojection, la
pyramide et les archives sont dans render/tiles.py. Le mode de la carte (Alt ou Sat) est pose a la main avant la nuit : l'outil
le verifie et ne le change jamais.
"""
import argparse
import json
import math
import multiprocessing
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I n'ajoute pas le dossier du script
sys.path.insert(0, str(ROOT))                              # ... ni la racine du depot (render)
try:
    import f10capture
except AttributeError:                                     # hors Windows : ctypes.windll absent (tests, plan, tuiler, aerodromes)
    from render import f10import
    f10capture = f10import.load()
import numpy as np
from PIL import Image

from render import geo, georef
from render import tiles as T

RECOUVREMENT_PX = 32            # recouvrement de chaque cote d'une case (pas l'OVERLAP de f10capture)
PAS_ETALONNAGE_PX = 300         # pas de l'etalonnage, en pixels a l'echelle prevue (f10capture.CALIB_STEP_M vaut 200 m, fixe)
WEBP_CAPTURE = {'lossless': True, 'quality': 0, 'method': 0}   # image Sat sans perte, la plus rapide (<= 0,5 s pour 3 350 x 1 404 px)
ESTIM_IMAGE_O = {'alt': 3 * 10 ** 6, 'sat': 6 * 10 ** 6}   # taille d'une image avant la premiere
RESERVE_DISQUE_O = 10 * 10 ** 9
CONTROLE_DISQUE_CASES = 200
ZOOM_MIN_DEFAUT = 8
ZOOMS = (8, 18)
ZOOMS_AERODROMES = (17, 18)
TAILLE_PART_O = T.TAILLE_PART_DEFAUT
CODE_ARGUMENTS, CODE_QUESTION, CODE_INCOMPLET = 2, 3, 4

_horloge = time.monotonic       # remplacee par les tests


def _espace_libre(chemin):
    return shutil.disk_usage(chemin).free


def _log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ les arguments

def _parser():
    p = argparse.ArgumentParser(prog='f10tiles.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='commande', required=True)

    def zone(sp):
        sp.add_argument('theatre')
        sp.add_argument('--couche', required=True, choices=('alt', 'sat'))
        sp.add_argument('--zoom', required=True, type=int)
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument('--emprise', help='fichier GeoJSON (Feature ou Polygon, lon/lat), tel quel')
        g.add_argument('--rectangle', help='lat_s,lon_o,lat_n,lon_e')
        sp.add_argument('--nom', help='nom de la zone (defaut : theatre pour --emprise, rect pour --rectangle)')

    zone(sub.add_parser('plan'))
    sp = sub.add_parser('capture')
    zone(sp)
    sp.add_argument('--duree-max-s', type=float, default=None)
    sp = sub.add_parser('tuiler')
    zone(sp)
    sp.add_argument('--zoom-min', type=int, default=None)
    sp.add_argument('--processus', type=int, default=None)
    sp = sub.add_parser('aerodromes')
    sp.add_argument('theatre')
    sp.add_argument('bases', nargs='*')
    return p


def _recoller_rectangle(argv):
    """`--rectangle -3,1,2,4` : argparse prendrait la valeur pour une option ; on la recolle en `--rectangle=-3,1,2,4`."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == '--rectangle' and i + 1 < len(argv):
            out.append('--rectangle=' + argv[i + 1])
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _refus(msg):
    print(f'ARRET : {msg}', file=sys.stderr)
    raise SystemExit(CODE_ARGUMENTS)


def _theatre_cle(theatre):
    return theatre.replace(' ', '').lower()


def _lire_rectangle(texte):
    try:
        v = [float(s) for s in texte.split(',')]
    except ValueError:
        _refus(f'--rectangle {texte!r} : quatre nombres attendus (lat_s,lon_o,lat_n,lon_e)')
    if len(v) != 4 or not all(math.isfinite(x) for x in v):
        _refus(f'--rectangle {texte!r} : quatre nombres attendus (lat_s,lon_o,lat_n,lon_e)')
    return v


class Zone:
    """Les arguments d'une passe, resolus : emprise, theatre, dossier de la passe."""

    def __init__(self, a):
        if not ZOOMS[0] <= a.zoom <= ZOOMS[1]:
            _refus(f'--zoom {a.zoom} hors de {ZOOMS[0]} a {ZOOMS[1]}')
        self.theatre, self.cle = a.theatre, _theatre_cle(a.theatre)
        self.couche, self.zoom = a.couche, a.zoom
        if a.nom is not None and not re.fullmatch(r'[A-Za-z0-9_-]+', a.nom):
            _refus(f'--nom {a.nom!r} : [A-Za-z0-9_-]+ attendu')
        self.nom = a.nom or ('theatre' if a.emprise else 'rect')
        if a.emprise:
            p = Path(a.emprise)
            if not p.is_file():
                _refus(f'emprise introuvable : {p}')
            try:
                self.emprise, sha = T.charger_emprise(p)
            except (ValueError, KeyError, TypeError) as e:
                _refus(f'emprise illisible : {p} ({e})')
            self.id_emprise = {'geojson_sha256': sha}
        else:
            v = _lire_rectangle(a.rectangle)
            try:
                self.emprise = T.emprise_rectangle(*v)
            except ValueError as e:
                _refus(str(e))
            self.id_emprise = {'rectangle': v}
        self.dossier = f10capture.CFG.work_dir / 'tuiles_brut' / f'{self.couche}-{self.cle}-z{self.zoom}-{self.nom}'
        self.nom_sortie = f'{self.couche}-{self.cle}-{self.nom}'

    @property
    def lat_max(self):
        return self.emprise.bounds[3]

    @property
    def mpp(self):
        """Echelle de capture : celle des tuiles du zoom cible a la latitude la plus haute de l'emprise."""
        return T.mpp_tuile(self.zoom, self.lat_max)


def _projection(theatre):
    try:
        return geo.Projection.for_theatre(theatre)
    except ImportError:
        _refus(f'theatre inconnu de pydcs : {theatre}')


def grille_voulue(z):
    """La grille de la passe (dict de grille.json, sans etalonnage)."""
    uw, uh = f10capture.usable_px()
    dxp, dzp = f10capture.crop_offset(1.0)
    mpp = z.mpp
    exz = T.emprise_xz(z.emprise, _projection(z.theatre))
    g = T.plan_grille(exz, mpp, (uw, uh), RECOUVREMENT_PX, (dxp, dzp))
    if not g['cases']:
        _refus('aucune case : l\'emprise est vide')
    return {'version': 1, 'theatre': z.cle, 'layer': z.couche, 'zoom': z.zoom, 'zone': z.nom, 'extent': z.id_emprise,
            'mpp_planned': mpp, 'commanded_alt_m': T.altitude_commandee(mpp), 'usable_px': [uw, uh],
            'overlap_px': RECOUVREMENT_PX, 'crop_offset_px': [dxp, dzp], 'footprint_m': g['empreinte_m'],
            'step_m': g['pas_m'], 'rows': g['lignes'], 'cols': g['colonnes'], 'cases': g['cases'], 'calibrations': []}


ARGS_GRILLE = ('theatre', 'layer', 'zoom', 'zone', 'extent', 'rows', 'cols')
ARGS_PASSE = ('theatre', 'layer', 'zoom', 'zone', 'extent')      # ce que `tuiler` compare : rien qui depende de l'ecran


def _ecrire_json(chemin, doc):
    chemin = Path(chemin)
    tmp = chemin.with_name(chemin.name + '.tmp')
    tmp.write_text(json.dumps(doc, indent=1), encoding='utf-8')
    os.replace(tmp, chemin)


def assurer_grille(z):
    """grille.json de la passe : ecrit s'il manque ; une grille existante qui differe (autres arguments) est refusee (code 2),
    jamais reecrite. Retourne le dict de grille.json."""
    voulue = grille_voulue(z)
    p = z.dossier / 'grille.json'
    if p.is_file():
        try:
            ex = json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            _refus(f'{p} illisible : grille refusee, a ne pas reecrire')
        diff = [k for k in ARGS_GRILLE + ('usable_px', 'overlap_px') if ex.get(k) != voulue[k]]
        if len(ex.get('cases', [])) != len(voulue['cases']):
            diff.append('cases')
        if diff:
            _refus(f'la grille existante {p} differe des arguments ({", ".join(diff)}) : autre passe, ou supprimer le dossier '
                   f'pour recommencer')
        return ex
    z.dossier.mkdir(parents=True, exist_ok=True)
    _ecrire_json(p, voulue)
    return voulue


def grille_existante(z):
    """grille.json d'une passe deja planifiee, pour `tuiler` : refus (code 2) s'il manque ou si ses arguments (theatre, couche,
    zoom, zone, emprise) different de ceux de la commande. Rien n'est recalcule : la taille de l'ecran n'intervient pas."""
    p = z.dossier / 'grille.json'
    if not p.is_file():
        _refus(f'{p} introuvable : lancer d\'abord plan ou capture avec les memes arguments')
    try:
        ex = json.loads(p.read_text(encoding='utf-8'))
    except ValueError:
        _refus(f'{p} illisible : grille refusee')
    voulu = {'theatre': z.cle, 'layer': z.couche, 'zoom': z.zoom, 'zone': z.nom, 'extent': z.id_emprise}
    diff = [k for k in ARGS_PASSE if ex.get(k) != voulu[k]]
    if diff:
        _refus(f'la grille {p} differe des arguments ({", ".join(diff)}) : autre passe')
    return ex


# ------------------------------------------------------------------ plan

def cmd_plan(a):
    z = Zone(a)
    g = assurer_grille(z)
    n_tuiles = len(T.tuiles_de_emprise(z.emprise, z.zoom))
    n = len(g['cases'])
    fh, fw = g['footprint_m']
    sth, stw = g['step_m']
    alt = g['commanded_alt_m']
    est = n * ESTIM_IMAGE_O[z.couche]
    _log(f'passe : {z.dossier}')
    _log(f'theatre {z.cle}, couche {z.couche}, zoom {z.zoom}, zone {z.nom}')
    _log(f'echelle de capture : {z.mpp:.5f} m/px (tuiles du zoom {z.zoom} a la latitude {z.lat_max:.4f}, la plus haute de l\'emprise)')
    _log(f'altitude commandee : {alt:.1f} m (hauteur vraie {T.hauteur_vraie(alt):.1f} m)')
    _log(f'grille : {g["rows"]} lignes x {g["cols"]} colonnes, empreinte {fh:.0f} x {fw:.0f} m, pas {sth:.0f} x {stw:.0f} m')
    _log(f'images : {n}')
    _log(f'tuiles du zoom {z.zoom} : {n_tuiles}')
    _log(f'place disque estimee : {est / 1e9:.1f} Go ({ESTIM_IMAGE_O[z.couche] / 1e6:.0f} Mo par image)')
    return 0


# ------------------------------------------------------------------ capture

def _nom_image(z, c):
    return z.dossier / T.nom_image(z.couche, c['row'], c['col'])


def _ecrire_image(img, chemin, couche):
    """Image enregistree dans un fichier temporaire puis renommee : un fichier present est complet."""
    tmp = chemin.with_name(chemin.name + '.tmp')
    im = Image.fromarray(img)
    if couche == 'alt':
        im.save(tmp, format='PNG')
    else:
        im.save(tmp, format='WEBP', **WEBP_CAPTURE)      # sans perte, le reglage le plus rapide
    os.replace(tmp, chemin)


def _terrain_aerodrome_proche(theatre, x, zc):
    """(nom, x, z) de l'aerodrome du theatre (pydcs) le plus proche de (x, zc)."""
    from render import dcsdata
    terrain = dcsdata.load_terrain(theatre)
    best = None
    for ap in terrain.airports.values():
        d = math.hypot(ap.position.x - x, ap.position.y - zc)
        if best is None or d < best[0]:
            best = (d, ap.name, float(ap.position.x), float(ap.position.y))
    if best is None:
        raise f10capture.AskCdP(f'ARRET : aucun aerodrome pydcs pour {theatre}')
    return best[1], best[2], best[3]


def _verifier_disque(z, restantes):
    """La place libre du disque de work_dir doit couvrir le reste (taille moyenne des images deja faites, a defaut 3 Mo pour
    alt et 6 Mo pour sat) plus 10 Go ; sinon arret (QUESTION)."""
    tailles = [p.stat().st_size for p in z.dossier.glob('r*_c*.*') if p.suffix in ('.png', '.webp')]
    moy = sum(tailles) / len(tailles) if tailles else ESTIM_IMAGE_O[z.couche]
    besoin = restantes * moy + RESERVE_DISQUE_O
    libre = _espace_libre(f10capture.CFG.work_dir)
    if libre < besoin:
        raise f10capture.AskCdP(f'Place disque insuffisante : libre {libre / 1e9:.1f} Go < {besoin / 1e9:.1f} Go '
                                f'({restantes} images a {moy / 1e6:.1f} Mo + {RESERVE_DISQUE_O / 1e9:.0f} Go)')


def _dossier_etalonnage(dossier):
    """Dossier d'etalonnage neuf : le numero suit le plus haut `etalonnage_NNN` present (pas le nombre d'etalonnages reussis), pour
    qu'aucune image d'une execution precedente ne soit lue pour le mode."""
    nums = [int(m.group(1)) for p in dossier.glob('etalonnage_*') if (m := re.fullmatch(r'etalonnage_(\d+)', p.name))]
    odir = dossier / f'etalonnage_{max(nums, default=0) + 1:03d}'
    odir.mkdir(parents=True, exist_ok=False)
    return odir


def _etalonner(z, g):
    """Etalonnage de l'echelle a l'altitude commandee de la passe, sur l'aerodrome le plus proche du centre de la zone ;
    controle du mode de la carte sur la premiere image. Retourne l'enregistrement de l'etalonnage (note au grille.json).
    Le pas de `calibrate` (200 m, fixe) est porte pendant l'etalonnage a PAS_ETALONNAGE_PX pixels a l'echelle prevue, puis remis."""
    alt = g['commanded_alt_m']
    pred = T.mpp_camera(alt)
    xs = [c['x'] for c in g['cases']]
    zs = [c['z'] for c in g['cases']]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    nom, ax, az = _terrain_aerodrome_proche(z.theatre, cx, cz)
    m = f10capture.MARGIN_ZONE_M
    zone = {'x_min': ax - m, 'x_max': ax + m, 'z_min': az - m, 'z_max': az + m}
    odir = _dossier_etalonnage(z.dossier)
    echec = None
    pas_avant = f10capture.CALIB_STEP_M
    f10capture.CALIB_STEP_M = PAS_ETALONNAGE_PX * pred
    try:
        cal = f10capture.calibrate(zone, alt, pred, odir, f'{nom} (tuiles)')
    except f10capture.CalibrationFailed as e:
        echec, cal = e, None
    finally:
        f10capture.CALIB_STEP_M = pas_avant
    img0 = odir / 'calib' / 'nw_c0.png'
    mode = aplat = sat = None
    if img0.is_file():
        mode, aplat, sat = T.mode_de_image(f10capture.load_png(img0))
        if mode != z.couche:
            raise f10capture.AskCdP(
                f'Mode de la carte : {mode} (aplat {aplat:.3f}, saturation {sat:.1f}) au lieu de {z.couche} : '
                f'poser le mode {z.couche.upper()} dans la F10 puis relancer (l\'outil ne change jamais le mode)')
    if echec is not None:
        raise f10capture.AskCdP(str(echec))
    if mode is None:
        raise f10capture.AskCdP(f'Mode de la carte illisible : {img0} absente apres un etalonnage reussi ; '
                                f'verifier le dossier {odir} et relancer')
    return {'time': datetime.now().isoformat(timespec='seconds'), 'airfield': nom, 'alt_m': alt, 'mpp_predicted': pred,
            'mpp': cal['mean'], 'measures': cal['measures'], 'spread_rel': cal['max_spread_rel'],
            'dev_vs_predicted_rel': cal['dev_vs_predicted_rel'], 'align_deg': cal['align_deg_max'],
            'calib_step_px': PAS_ETALONNAGE_PX, 'mode': mode, 'aplat': aplat, 'saturation': sat, 'folder': odir.name}


def cmd_capture(a):
    z = Zone(a)
    g = assurer_grille(z)
    cases = T.ordre_serpentin(g['cases'])
    total = len(cases)
    for tmp in z.dossier.glob('*.tmp'):
        tmp.unlink()
    restantes = [c for c in cases if not _nom_image(z, c).is_file()]
    t0 = _horloge()
    limite = a.duree_max_s
    if not restantes:
        _log(f'COMPLET {total}/{total}')
        return 0
    try:
        _verifier_disque(z, len(restantes))
        cal = _etalonner(z, g)
        g['calibrations'].append(cal)
        _ecrire_json(z.dossier / 'grille.json', g)
        mpp = cal['mpp']
        _log(f'etalonnage : {mpp:.5f} m/px (prevu {cal["mpp_predicted"]:.5f}), mode {cal["mode"]}, aerodrome {cal["airfield"]}')
        alt = g['commanded_alt_m']
        durees, faites_ici, non_stables = [], 0, 0
        for c in restantes:
            if limite is not None:
                moy = sum(durees) / len(durees) if durees else f10capture.EST_PER_CAPTURE_S
                if _horloge() - t0 + moy > limite:
                    break
            if faites_ici and faites_ici % CONTROLE_DISQUE_CASES == 0:
                _verifier_disque(z, len(restantes) - faites_ici)
            tt = _horloge()
            f10capture.set_status(f'CAPTURE tuiles {z.dossier.name} {total - len(restantes) + faites_ici + 1}/{total}')
            img, delai, n, ok, cmd = f10capture.move_and_stabilize(c['x'], c['z'], alt)
            if not ok:
                non_stables += 1
                _log(f'r{c["row"]}_c{c["col"]} NON STABLE apres {delai:.1f} s : image non enregistree (reprise a la prochaine execution)')
                durees.append(_horloge() - tt)          # son temps compte dans l'estimation qui protege --duree-max-s
                continue
            _ecrire_image(f10capture.crop(img), _nom_image(z, c), z.couche)
            with open(z.dossier / 'captures.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps({'row': c['row'], 'col': c['col'], 'x': c['x'], 'z': c['z'], 'rendered_x': cmd[0],
                                    'rendered_z': cmd[1], 'rendered_alt': cmd[2], 'delay_s': round(delai, 3), 'n_images': n,
                                    'mpp': mpp, 'timestamp': datetime.now().isoformat(timespec='milliseconds')}) + '\n')
            durees.append(_horloge() - tt)
            faites_ici += 1
    finally:
        f10capture.hook_release()
    faites = sum(1 for c in cases if _nom_image(z, c).is_file())
    if faites < total:
        _log(f'INCOMPLET {faites}/{total}')
    else:
        _log(f'COMPLET {total}/{total}')
    return 0


# ------------------------------------------------------------------ tuiler

OCTETS_TUILE_O = {'alt': 50000, 'sat': 20000}      # poids d'une tuile pour l'estimation du disque de `tuiler`


def _verifier_disque_tuiler(z, n_tuiles):
    """Avant `tuiler` : la place libre du disque de work_dir doit couvrir (tuiles du zoom cible x 4/3 x poids d'une tuile) x 2
    plus 10 Go ; sinon arret (QUESTION), rien d'efface."""
    besoin = n_tuiles * 4 / 3 * OCTETS_TUILE_O[z.couche] * 2 + RESERVE_DISQUE_O
    libre = _espace_libre(f10capture.CFG.work_dir)
    if libre < besoin:
        raise f10capture.AskCdP(f'Place disque insuffisante pour tuiler : libre {libre / 1e9:.1f} Go < {besoin / 1e9:.1f} Go '
                                f'({n_tuiles} tuiles au zoom {z.zoom}, pyramide, deux fois, plus {RESERVE_DISQUE_O / 1e9:.0f} Go) ; '
                                f'rien n\'a ete efface')


def _supprimer_dossier(d):
    """Dossier de travail : supprime sans taire les erreurs (une erreur arrete la commande, code 3)."""
    if d.exists():
        shutil.rmtree(d)


def processus_defaut():
    """Nombre de processus de `tuiler` par defaut : les coeurs moins deux, au moins un (la machine reste utilisable)."""
    return max(1, (os.cpu_count() or 1) - 2)


def _executer_blocs(taches, ctx_spec, n_proc, local):
    """Execute des blocs ; retourne la liste de leurs resultats. n_proc 1 : dans ce processus (avec `local` = (projection,
    sources, cache))."""
    res = []
    if n_proc <= 1 or len(taches) <= 1:
        for t in taches:
            res.append(T.traiter_bloc(t, *local))
        return res
    ctx = multiprocessing.get_context()
    with ctx.Pool(min(n_proc, len(taches))) as pool:
        for r in pool.imap_unordered(T.travailler, [(ctx_spec, t) for t in taches]):
            res.append(r)
    return res


def cmd_tuiler(a):
    z = Zone(a)
    zoom_min = a.zoom_min if a.zoom_min is not None else min(ZOOM_MIN_DEFAUT, z.zoom)
    if not 0 <= zoom_min <= z.zoom:
        _refus(f'--zoom-min {zoom_min} hors de 0 a {z.zoom}')
    if a.processus is not None and a.processus < 1:
        _refus('--processus : un entier >= 1')
    g = grille_existante(z)
    captures = T.lire_captures(z.dossier)
    sources, absentes = T.sources_de_grille(g, captures, z.dossier)
    total = len(g['cases'])
    if absentes:
        _log(f'INCOMPLET {total - len(absentes)}/{total}')
        return CODE_INCOMPLET
    n_proc = a.processus or processus_defaut()
    t0 = time.perf_counter()
    proj = _projection(z.theatre)
    dossier, tmp = z.dossier / 'tuiles', z.dossier / 'pyramide_tmp'
    out = f10capture.CFG.work_dir / 'out' / 'tuiles'
    raison = T.manifeste_refuse(out, z.nom_sortie, z.zoom)
    if raison:
        _refus(raison)
    uw, uh = g['usable_px']
    T.verifier_taille_source(z.dossier, (uw, uh))
    base = T.tuiles_de_emprise(z.emprise, z.zoom)
    _verifier_disque_tuiler(z, len(base))
    for d in (dossier, tmp):
        _supprimer_dossier(d)
    out.mkdir(parents=True, exist_ok=True)
    T.nettoyer_provisoires(out, z.nom_sortie)
    _log(f'{len(base)} tuiles au zoom {z.zoom}, {total} images, pyramide jusqu\'au zoom {zoom_min}, {n_proc} processus')
    cache = T.CacheImages(T.charger_image_rgb)       # borne : T.CACHE_IMAGES images par processus
    ctx_spec = {'type': 'grille', 'passe': str(z.dossier), 'theatre': z.theatre}
    local = (proj, sources, cache)
    stats = {'ms_rendu': 0.0, 'ms_reduction': 0.0, 'ms_codage': 0.0, 'octets': 0, 'rendues': 0, 'vides': 0}
    tuiles_phase = [(int(x), int(y)) for x, y in base]
    for k, (zb, niveaux) in enumerate(T.plan_phases(z.zoom, zoom_min)):
        blocs = T.blocs_de(tuiles_phase, zb, T.BLOC_NIVEAUX)
        taches = []
        for (bx, by), lst in sorted(blocs.items()):
            taches.append({'base_zoom': zb, 'bx': bx, 'by': by, 'niveaux': niveaux, 'source': 'rendu' if k == 0 else 'spill',
                           'tuiles': lst, 'couche': z.couche, 'dossier': str(dossier), 'tmp': str(tmp), 'ecrire_base': k == 0,
                           'garder_haut': zb - niveaux > zoom_min})
        _log(f'phase {k + 1} : zoom {zb} -> {zb - niveaux}, {len(taches)} blocs')
        for r in _executer_blocs(taches, ctx_spec, n_proc, local):
            for key in stats:
                stats[key] += r[key]
        if zb - niveaux > zoom_min:
            zh = zb - niveaux
            tuiles_phase = sorted((int(p.parent.name), int(p.stem)) for p in (tmp / str(zh)).glob('*/*.npy')) if (tmp / str(zh)).is_dir() else []
    _supprimer_dossier(tmp)
    fichiers = T.lister_tuiles(dossier, z.couche)
    par_zoom = {}
    for zz, _, _, _ in fichiers:
        par_zoom[zz] = par_zoom.get(zz, 0) + 1
    _log('tuiles par zoom : ' + ', '.join(f'z{k} {v}' for k, v in sorted(par_zoom.items())))
    parts = T.empaqueter(fichiers, f'{z.couche}-{z.cle}', out, z.nom_sortie, z.zoom, TAILLE_PART_O, provisoire=True)
    lon_o, lat_s, lon_e, lat_n = z.emprise.bounds
    meta = {'layer': z.couche, 'theatre': z.cle, 'zone': z.nom, 'zoom': z.zoom, 'format': T.EXTENSIONS[z.couche]}
    T.ecrire_manifeste(out / f'{z.nom_sortie}.json.tmp', meta, parts, par_zoom)
    T.ecrire_config(out / f'{z.nom_sortie}.config.json.tmp', z.couche, T.CLES_ROUTE_PLANNER.get(z.cle, z.theatre),
                    min(par_zoom), max(par_zoom), (lat_s, lon_o, lat_n, lon_e))
    T.publier(out, z.nom_sortie, [z.zoom], parts, [f'{z.nom_sortie}.json', f'{z.nom_sortie}.config.json'])   # tout est ecrit : on remplace
    dt = time.perf_counter() - t0
    if stats['rendues']:
        _log(f'temps par tuile et par coeur : reprojection {stats["ms_rendu"] / stats["rendues"]:.1f} ms '
             f'(lecture des images comprise), codage {stats["ms_codage"] / max(1, len(fichiers)):.1f} ms')
    _log(f'{len(fichiers)} tuiles, {len(parts)} part(s) sous {out}, {dt:.0f} s ({stats["octets"] / 1e6:.0f} Mo de tuiles)')
    return 0


# ------------------------------------------------------------------ aerodromes

def _bases_a_traiter(theatre, noms):
    racine = f10capture.out_theatre(theatre)
    if not racine.is_dir():
        _refus(f'dossier introuvable : {racine}')
    if noms:
        for n in noms:
            if not (racine / n / 'base.json').is_file():
                _refus(f'base introuvable : {racine / n}')
        return list(noms)
    return [d.name for d in sorted(racine.iterdir()) if (d / 'base.json').is_file() and (d / 'mosaic.png').is_file()]


def _couverture_lonlat(g, proj):
    """Rectangle couvert par la mosaique (repere DCS) -> polygone lon/lat, bords decoupes."""
    import shapely
    x1, x0 = g.x_top, g.x_top - g.height * g.mpp
    z0, z1 = g.z_left, g.z_left + g.width * g.mpp
    pts = []
    n = 40
    for t in np.linspace(0, 1, n, endpoint=False):
        pts.append((x0 + (x1 - x0) * t, z0))
    for t in np.linspace(0, 1, n, endpoint=False):
        pts.append((x1, z0 + (z1 - z0) * t))
    for t in np.linspace(0, 1, n, endpoint=False):
        pts.append((x1 - (x1 - x0) * t, z1))
    for t in np.linspace(0, 1, n, endpoint=False):
        pts.append((x0, z1 - (z1 - z0) * t))
    xs, zs = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    lat, lon = proj.to_latlon(xs, zs)
    return shapely.Polygon(np.stack([np.asarray(lon), np.asarray(lat)], axis=1))


def fabriquer_aerodromes(theatre, bases, racine_out, dossier, proj, zooms=ZOOMS_AERODROMES, log=_log):
    """Tuiles des zooms `zooms` des mosaiques de charts `bases` (sous racine_out/<Base>/) ecrites sous `dossier`. Retourne
    ({zoom: nombre de tuiles}, bounds lat/lon union (sud, ouest, nord, est), [bases traitees])."""
    import shapely
    xc, zc, mpp, w, h, cles, couv, noms = [], [], [], [], [], [], [], []
    for b in bases:
        d = Path(racine_out) / b
        bj = georef.load_base_json(d)
        g = georef.georef_from_base(bj)
        mos = d / 'mosaic.png'
        if not mos.is_file():
            log(f'{b} : mosaic.png absente, ignoree')
            continue
        with Image.open(mos) as im:
            T.verifier_taille_source(mos, im.size)
            if im.size != (g.width, g.height):
                log(f'{b} : mosaique {im.size[0]}x{im.size[1]} differente de base.json {g.width}x{g.height}, ignoree')
                continue
        xc.append(g.x_top - g.height * g.mpp / 2)
        zc.append(g.z_left + g.width * g.mpp / 2)
        mpp.append(g.mpp)
        w.append(g.width)
        h.append(g.height)
        cles.append(str(mos))
        couv.append(_couverture_lonlat(g, proj))
        noms.append(b)
    if not noms:
        _refus('aucune base a traiter (base.json et mosaic.png)')
    sources = T.Sources(xc, zc, mpp, w, h, cles)
    cache = T.CacheImages(T.charger_image_rgb, n=3)
    _supprimer_dossier(Path(dossier))
    par_zoom = {}
    for zoom in zooms:
        faites = set()
        for emp in couv:                                  # base par base : la mosaique est lue une fois pour ses tuiles
            tl = [(int(x), int(y)) for x, y in T.tuiles_de_emprise(emp, zoom) if (int(x), int(y)) not in faites]
            for x, y in tl:
                faites.add((x, y))
                t = T.rendre_tuile(zoom, x, y, proj, sources, cache)
                if t is not None:
                    T.ecrire_tuile(dossier, zoom, x, y, 'alt', t)
                    par_zoom[zoom] = par_zoom.get(zoom, 0) + 1
        log(f'zoom {zoom} : {par_zoom.get(zoom, 0)} tuiles')
    union = shapely.union_all(couv)
    lon_o, lat_s, lon_e, lat_n = union.bounds
    return par_zoom, (lat_s, lon_o, lat_n, lon_e), noms


def cmd_aerodromes(a):
    cle = _theatre_cle(a.theatre)
    bases = _bases_a_traiter(a.theatre, a.bases)
    proj = _projection(a.theatre)
    nom = f'alt-{cle}-aerodromes'
    dossier = f10capture.CFG.work_dir / 'tuiles_brut' / nom
    out = f10capture.CFG.work_dir / 'out' / 'tuiles'
    raison = T.manifeste_refuse(out, nom, list(ZOOMS_AERODROMES))
    if raison:
        _refus(raison)
    par_zoom, bounds, traitees = fabriquer_aerodromes(a.theatre, bases, f10capture.out_theatre(a.theatre), dossier, proj)
    out.mkdir(parents=True, exist_ok=True)
    T.nettoyer_provisoires(out, nom)
    fichiers = T.lister_tuiles(dossier, 'alt')
    parts = []
    for zoom in ZOOMS_AERODROMES:
        parts += T.empaqueter([f for f in fichiers if f[0] == zoom], f'alt-{cle}', out, nom, zoom, TAILLE_PART_O, provisoire=True)
    meta = {'layer': 'alt', 'theatre': cle, 'zone': 'aerodromes', 'zoom': list(ZOOMS_AERODROMES), 'format': 'png',
            'bases': traitees}
    T.ecrire_manifeste(out / f'{nom}.json.tmp', meta, parts, par_zoom)
    T.ecrire_config(out / f'{nom}.config.json.tmp', 'alt', T.CLES_ROUTE_PLANNER.get(cle, a.theatre), min(ZOOMS_AERODROMES),
                    max(ZOOMS_AERODROMES), bounds)
    T.publier(out, nom, list(ZOOMS_AERODROMES), parts, [f'{nom}.json', f'{nom}.config.json'])
    _log(f'{len(traitees)} bases, {sum(par_zoom.values())} tuiles, {len(parts)} part(s) sous {out}')
    return 0


# ------------------------------------------------------------------ main

def main(argv):
    a = _parser().parse_args(_recoller_rectangle(argv[1:]))
    return {'plan': cmd_plan, 'capture': cmd_capture, 'tuiler': cmd_tuiler, 'aerodromes': cmd_aerodromes}[a.commande](a)


def executer(argv):
    """main + les arrets qui demandent une decision : sync/QUESTION.txt et code 3 (comme f10capture)."""
    try:
        return main(argv)
    except (f10capture.AskCdP, f10capture.ForegroundFailure, RuntimeError, OSError) as e:
        f10capture.hook_release()
        f10capture.ask(f'{type(e).__name__}: {e}')
        print('QUESTION ecrite :', e)
        return CODE_QUESTION


if __name__ == '__main__':
    _argv = f10capture.init(sys.argv)
    sys.exit(executer(_argv))
