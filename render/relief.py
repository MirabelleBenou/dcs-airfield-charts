"""Relief de DCS en tuiles web : la grille du relief, un ombrage transparent (`hillshade`) et l'altitude au format Terrain-RGB
(`elevation`), en tuiles XYZ Web Mercator de 256 px du zoom 6 au zoom 11 (z11 natif), et le zip qui les porte.

Entree : le relief de DCS releve au pas de 100 m (CSV `i,j,x,z,alt_m,surface`, lus dans des zips sans les decompresser, ou nus).
Repere DCS : x vers le Nord, z vers l'Est (metres). La grille a pour case (i, j) le point x = x0 + 100 i, z = z0 + 100 j.

Module pur : ni DCS ni ecran. La reprojection (pixel de tuile -> point du sol) est celle des tuiles F10 (`render.tiles.cartes_xz`) ;
la reduction de `hillshade` est `render.tiles.reduire`. Le calcul se fait par blocs de tuiles, sur une fenetre de la grille avec
une marge de MARGE_CASES cases : le resultat ne depend ni de la taille des blocs ni du nombre de processus.
"""
import hashlib
import io
import json
import math
import multiprocessing
import os
import shutil
import time
import warnings
import zipfile
from pathlib import Path

import numpy as np
import shapely
from PIL import Image
from scipy import ndimage

from . import dcsdata, geo, tiles as T

PAS_M = 100.0                         # pas de la grille
TOL_NOEUD_M = 0.01                    # un point d'entree tombe sur un noeud a 0,01 m pres
TOL_ALT_M = 0.01                      # un noeud lu deux fois porte la meme altitude a 0,01 m pres
TOL_ALT_FLOAT32_M = 0.0005            # marge pour l'arrondi en float32 (resolution de 0,0005 m vers 5 000 m)
CASES_MAX = 1_000_000_000             # au-dela, la grille est refusee (des points aberrants feraient allouer des teraoctets)
ENTETE = ['i', 'j', 'x', 'z', 'alt_m']
ZOOM_MIN, ZOOM_MAX = 6, 11
NIVEAUX_DEFAUT = 4                    # un bloc = 2^4 x 2^4 tuiles z11 ; ses niveaux z11 a z7 se font dans le bloc
PAS_CONTOUR_M = 1000.0                # le contour de l'emprise est echantillonne tous les 1 000 m au plus

# L'ombrage « volumes » (variante D)
ZETA_RAD = math.radians(45.0)         # soleil a 45 degres de hauteur : 45 degres du zenith
COS_ZETA = math.cos(ZETA_RAD)
SIN_ZETA = math.sin(ZETA_RAD)
AZIMUTS = ((315.0, 0.40), (270.0, 0.25), (0.0, 0.25), (225.0, 0.10))
SIGMA_CASES, TRONC_SIGMA = 8.0, 4.0   # grille lissee : filtre gaussien de 8 cases (800 m), tronque a 4 sigma
F_BRUTE, F_LISSEE = 3.0, 7.0          # exagerations verticales
POIDS_BRUTE, POIDS_LISSEE = 0.55, 0.45
MARGE_CASES = 34                      # 32 (4 sigma) + 1 (gradient) + 1 (interpolation)
RAYON_NON_RELEVE = 34                 # `hillshade` : transparent a 34 cases (Tchebychev) au plus d'un noeud non releve (NaN)
COULEUR_OMBRE, GAIN_OMBRE, OPACITE_OMBRE = (40, 32, 24), 1.6, 0.70
COULEUR_LUMIERE, GAIN_LUMIERE, OPACITE_LUMIERE = (255, 252, 240), 2.2, 0.55

# Terrain-RGB (Mapbox) : h = -10 000 + (R x 65 536 + G x 256 + B) x 0,1
BASE_M, PAS_ALT_M = -10000.0, 0.1
V_MAX = (1 << 24) - 1

COUCHES = ('hillshade', 'elevation')
EXTENSIONS = {'hillshade': 'webp', 'elevation': 'png'}
FORMAT = 'dcs-relief-tiles-v1'
LICENCE_NOM = 'LICENSE-RELIEF.txt'
ATTRIBUTION = 'Relief: DCS World, Eagle Dynamics SA — terrain elevation read in the game. Non-commercial use only.'
LICENCE_RELIEF = (ATTRIBUTION + '\n\nThese map tiles are not covered by the MIT licence of the code in this repository. '
                  'They are derived from the terrain elevation read in DCS World and remain subject to the terms of '
                  'Eagle Dynamics SA.\n')
NOMS_DCS = {'syria': 'Syria', 'germanycoldwar': 'GermanyColdWar', 'caucasus': 'Caucasus'}


class RefusDonnees(Exception):
    """Une entree que l'outil refuse (code 2) : le message nomme la cause."""


class ErreurSortie(Exception):
    """Un dossier de sortie que l'outil ne peut pas preparer (code 2) : le message nomme le dossier et la cause."""


def nom_dcs(theatre):
    """Nom DCS du theatre (Syria, GermanyColdWar, Caucasus) d'apres son nom sans casse ni espaces ; None s'il est inconnu."""
    return NOMS_DCS.get(theatre.replace(' ', '').lower())


def cle_theatre(theatre):
    """Cle du theatre dans le Route Planner (Syria, GermanyCW, Caucasus) ; None s'il est inconnu."""
    return T.CLES_ROUTE_PLANNER.get(theatre.replace(' ', '').lower())


def _rien(*_a, **_k):
    pass


# ------------------------------------------------------------------ la lecture des entrees

def membre_retenu(nom_membre, theatre):
    """Vrai si le membre d'un zip est un CSV du releve du theatre : `out/relief/<nom DCS du theatre>/<nom>_100m_export.csv`
    (directement sous ce dossier). Tout autre membre (journaux, `.json`, CSV d'un autre theatre ou d'un autre dossier) s'ignore."""
    prefixe = f'out/relief/{nom_dcs(theatre) or theatre}/'
    if not nom_membre.startswith(prefixe):
        return False
    reste = nom_membre[len(prefixe):]
    return '/' not in reste and reste.endswith('_100m_export.csv') and len(reste) > len('_100m_export.csv')


def lister_csv(chemins, theatre):
    """Les CSV des entrees : liste triee de (libelle, ouvrir) ou `ouvrir()` rend les octets du CSV. Un zip donne ses membres
    `out/relief/<nom DCS de `theatre`>/*_100m_export.csv` (les autres membres s'ignorent) lus sans rien ecrire sur le disque ;
    un `.csv` nu est lu tel quel. Le libelle est `<zip>::<membre>` ou le nom du fichier : jamais le chemin du disque.
    RefusDonnees : zip illisible, ou aucun CSV retenu."""
    out = []
    for c in chemins:
        c = Path(c)
        if c.suffix.lower() == '.zip':
            try:
                with zipfile.ZipFile(c) as z:
                    membres = [i.filename for i in z.infolist() if not i.is_dir() and membre_retenu(i.filename, theatre)]
            except zipfile.BadZipFile as e:
                raise RefusDonnees(f'{c.name} : zip illisible ({e})')
            for m in membres:
                out.append((f'{c.name}::{m}', _ouvrir_membre(c, m)))
        else:
            out.append((c.name, _ouvrir_fichier(c)))
    if not out:
        raise RefusDonnees(f'aucun CSV retenu dans les entrees (attendu : out/relief/{nom_dcs(theatre) or theatre}/*_100m_export.csv '
                           f'dans un zip, ou un .csv nu)')
    out.sort(key=lambda t: t[0])
    return out


def _ouvrir_membre(zip_chemin, membre):
    def ouvrir():
        with zipfile.ZipFile(zip_chemin) as z:
            return z.read(membre)
    return ouvrir


def _ouvrir_fichier(chemin):
    def ouvrir():
        return Path(chemin).read_bytes()
    return ouvrir


def _est_ligne_ignoree(ligne):
    """Une ligne que `np.loadtxt` saute : vide, ou qui ne porte qu'un commentaire (`#`)."""
    return ligne.rstrip(b'\r').split(b'#', 1)[0] == b''


def numero_de_ligne(octets, n):
    """Numero (dans le fichier, a partir de 1, l'en-tete etant la ligne 1, lignes vides comprises) de la ligne de la n-ieme ligne
    de donnees (n a partir de 0) : `np.loadtxt` saute les lignes vides, son propre rang glisse alors. None s'il y en a moins."""
    vues = -1
    for k, ligne in enumerate(octets.split(b'\n')):
        if k == 0 or _est_ligne_ignoree(ligne):
            continue
        vues += 1
        if vues == n:
            return k + 1
    return None


def _premiere_ligne_illisible(octets):
    """(numero de ligne du fichier, cause) de la premiere ligne de donnees que `lire_csv` ne sait pas lire ; None si aucune."""
    for k, ligne in enumerate(octets.split(b'\n')):
        if k == 0 or _est_ligne_ignoree(ligne):
            continue
        champs = ligne.rstrip(b'\r').split(b'#', 1)[0].split(b',')
        if len(champs) < len(ENTETE):
            return k + 1, f'{len(champs)} colonne(s) au lieu de {len(ENTETE)} au moins'
        for c in (2, 3, 4):
            try:
                float(champs[c])
            except ValueError:
                return k + 1, f'valeur non numerique : {champs[c].decode("utf-8", errors="replace")[:40]!r} (colonne {c + 1})'
    return None


def lire_csv(libelle, octets):
    """Tableau (n, 3) float64 de (x, z, alt_m) d'un CSV. RefusDonnees : en-tete inattendu, ligne illisible, valeur non finie ;
    le numero de ligne d'un refus est celui du fichier, lignes vides comprises."""
    entete = octets.split(b'\n', 1)[0].decode('utf-8-sig', errors='replace').strip().split(',')
    if [c.strip() for c in entete[:len(ENTETE)]] != ENTETE:
        raise RefusDonnees(f'{libelle} : en-tete inattendu (attendu : i,j,x,z,alt_m,surface ; recu : {",".join(entete)[:80]})')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            a = np.loadtxt(io.BytesIO(octets), delimiter=',', skiprows=1, usecols=(2, 3, 4), dtype=np.float64, ndmin=2)
    except ValueError as e:
        mauvaise = _premiere_ligne_illisible(octets)
        if mauvaise is None:
            raise RefusDonnees(f'{libelle} : ligne illisible ({e})')
        raise RefusDonnees(f'{libelle} ligne {mauvaise[0]} : ligne illisible ({mauvaise[1]})')
    if a.shape[0] == 0:
        raise RefusDonnees(f'{libelle} : aucun point')
    mauvais = ~np.isfinite(a).all(axis=1)
    if mauvais.any():
        n = int(np.nonzero(mauvais)[0][0])
        raise RefusDonnees(f'{libelle} ligne {numero_de_ligne(octets, n)} : valeur absente ou non numerique (x, z ou alt_m)')
    return a


class Grille:
    """Le relief d'un theatre : `alt` (float32, absent = NaN), la case (i, j) est au point x = x0 + pas i, z = z0 + pas j."""

    def __init__(self, alt, x0, z0, pas_m=PAS_M, entrees=()):
        self.alt, self.x0, self.z0, self.pas_m = alt, float(x0), float(z0), float(pas_m)
        self.entrees = list(entrees)             # [{'fichier', 'sha256'}]

    @property
    def forme(self):
        return [int(self.alt.shape[0]), int(self.alt.shape[1])]

    def sha256_octets(self):
        """sha256 des octets float32 de la grille (ordre C)."""
        return hashlib.sha256(np.ascontiguousarray(self.alt, dtype=np.float32).tobytes()).hexdigest()


def construire_grille(chemins, theatre, emprise=None, log=_rien):
    """La grille de toutes les entrees (§ regles de la grille) du theatre `theatre` (le filtre des membres d'un zip, `lister_csv`).
    `emprise` : (x_min, z_min, x_max, z_max) en metres ; donnee, elle commande la regle « un noeud de la grille dans l'emprise
    que nulle entree ne donne est refuse » ; None : regle debrayee (essais, tests). Un noeud lu deux fois (bord commun) garde la
    premiere valeur lue (ordre des libelles tries). RefusDonnees pour : un point hors noeud (a 0,01 m pres), un noeud lu deux fois
    avec deux altitudes (a 0,01 m pres), un noeud manquant dans l'emprise, une entree illisible."""
    csv = lister_csv(chemins, theatre)
    # passe 1 : l'origine (plus petit x, plus petit z) et l'etendue de toutes les entrees
    x_lo = z_lo = math.inf
    x_hi = z_hi = -math.inf
    for lib, ouvrir in csv:
        a = lire_csv(lib, ouvrir())
        x_lo, x_hi = min(x_lo, float(a[:, 0].min())), max(x_hi, float(a[:, 0].max()))
        z_lo, z_hi = min(z_lo, float(a[:, 1].min())), max(z_hi, float(a[:, 1].max()))
        del a
    x0, z0 = x_lo, z_lo
    ni, nj = int(round((x_hi - x0) / PAS_M)) + 1, int(round((z_hi - z0) / PAS_M)) + 1
    if ni < 2 or nj < 2:
        raise RefusDonnees(f'grille trop petite ({ni} x {nj} cases) : au moins 2 x 2 noeuds sont attendus')
    if ni * nj > CASES_MAX:
        raise RefusDonnees(f'grille demesuree ({ni} x {nj} cases) : des points sont tres loin les uns des autres')
    log(f'{len(csv)} CSV, origine x0 = {x0:g}, z0 = {z0:g}, grille {ni} x {nj} cases')
    # passe 2 : les points sur leurs noeuds
    alt = np.full((ni, nj), np.nan, np.float32)
    entrees = []
    for lib, ouvrir in csv:
        octets = ouvrir()
        sha = hashlib.sha256(octets).hexdigest()
        a = lire_csv(lib, octets)
        x, z, h = a[:, 0], a[:, 1], a[:, 2]
        i = np.rint((x - x0) / PAS_M)
        j = np.rint((z - z0) / PAS_M)
        hors = (np.abs(x - (x0 + PAS_M * i)) > TOL_NOEUD_M) | (np.abs(z - (z0 + PAS_M * j)) > TOL_NOEUD_M)
        if hors.any():
            n = int(np.nonzero(hors)[0][0])
            raise RefusDonnees(f'{lib} ligne {numero_de_ligne(octets, n)} : le point (x = {x[n]:g}, z = {z[n]:g}) ne tombe pas sur '
                               f'un noeud de la grille de {PAS_M:g} m (origine x0 = {x0:g}, z0 = {z0:g}) a {TOL_NOEUD_M:g} m pres '
                               f'({int(hors.sum())} point(s) dans ce cas)')
        i, j = i.astype(np.int64), j.astype(np.int64)
        h32 = h.astype(np.float32)
        avant = alt[i, j]
        deja = ~np.isnan(avant)
        ecart = np.abs(avant.astype(np.float64) - h32.astype(np.float64))
        diff = deja & (ecart > TOL_ALT_M + TOL_ALT_FLOAT32_M)
        if diff.any():
            n = int(np.nonzero(diff)[0][0])
            raise RefusDonnees(f'{lib} ligne {numero_de_ligne(octets, n)} : le noeud ({int(i[n])}, {int(j[n])}) (x = {x[n]:g}, '
                               f'z = {z[n]:g}) est deja donne avec une autre altitude ({float(avant[n]):.2f} m, ici {float(h32[n]):.2f} m ; '
                               f'ecart {float(ecart[n]):.2f} m > {TOL_ALT_M:g} m)')
        # un noeud deja donne par une entree precedente garde sa valeur ; dans ce fichier, la premiere ligne d'un noeud
        # donne sa valeur
        neuf = np.nonzero(~deja)[0]
        if neuf.size:
            _, premiere = np.unique(i[neuf] * nj + j[neuf], return_index=True)
            choix = neuf[premiere]
            alt[i[choix], j[choix]] = h32[choix]
        relu = ~deja & (np.abs(alt[i, j].astype(np.float64) - h32.astype(np.float64)) > TOL_ALT_M + TOL_ALT_FLOAT32_M)
        if relu.any():
            n = int(np.nonzero(relu)[0][0])
            raise RefusDonnees(f'{lib} ligne {numero_de_ligne(octets, n)} : le noeud ({int(i[n])}, {int(j[n])}) est donne deux fois '
                               f'dans ce fichier avec deux altitudes')
        entrees.append({'fichier': lib, 'sha256': sha})
        log(f'  lu {lib} ({a.shape[0]} points)')
        del octets, a, x, z, h, i, j, h32, avant, deja, ecart, diff, relu, hors, neuf
    g = Grille(alt, x0, z0, PAS_M, entrees)
    if emprise is not None:
        verifier_emprise(g, emprise)
    return g


def emprise_grille(grille):
    """(x_min, z_min, x_max, z_max) : le rectangle de la grille, de `x0` a `x0 + pas (ni - 1)` et de `z0` a `z0 + pas (nj - 1)`.
    C'est l'emprise des tuiles (les noeuds non releves y sont transparents)."""
    ni, nj = grille.alt.shape
    return (grille.x0, grille.z0, grille.x0 + grille.pas_m * (ni - 1), grille.z0 + grille.pas_m * (nj - 1))


def verifier_emprise(grille, emprise):
    """RefusDonnees s'il manque un noeud de la grille (x0 + pas i, z0 + pas j) dans l'emprise (x_min, z_min, x_max, z_max) :
    le message donne leur nombre et la boite (x, z, metres) qui les contient. Les noeuds hors de l'emprise peuvent manquer."""
    x_min, z_min, x_max, z_max = emprise
    ni, nj = grille.alt.shape
    eps = 1e-9
    ia, ib = math.ceil((x_min - grille.x0) / grille.pas_m - eps), math.floor((x_max - grille.x0) / grille.pas_m + eps)
    ja, jb = math.ceil((z_min - grille.z0) / grille.pas_m - eps), math.floor((z_max - grille.z0) / grille.pas_m + eps)
    if ib < ia or jb < ja:
        return
    total = (ib - ia + 1) * (jb - ja + 1)
    ia2, ib2, ja2, jb2 = max(ia, 0), min(ib, ni - 1), max(ja, 0), min(jb, nj - 1)
    present = 0
    lignes = colonnes = None
    if ia2 <= ib2 and ja2 <= jb2:
        manque = np.isnan(grille.alt[ia2:ib2 + 1, ja2:jb2 + 1])
        present = manque.size - int(manque.sum())
        lignes, colonnes = np.nonzero(manque.any(axis=1))[0], np.nonzero(manque.any(axis=0))[0]
    manquants = total - present
    if manquants == 0:
        return
    boites = []                                       # (i_min, i_max, j_min, j_max) des parties qui manquent
    if lignes is not None and lignes.size:
        boites.append((ia2 + int(lignes[0]), ia2 + int(lignes[-1]), ja2 + int(colonnes[0]), ja2 + int(colonnes[-1])))
    for (i_a, i_b, j_a, j_b) in ((ia, min(ib, -1), ja, jb), (max(ia, ni), ib, ja, jb),      # hors du tableau de la grille
                                 (ia, ib, ja, min(jb, -1)), (ia, ib, max(ja, nj), jb)):
        if i_a <= i_b and j_a <= j_b:
            boites.append((i_a, i_b, j_a, j_b))
    xa, xb = grille.x0 + grille.pas_m * min(b[0] for b in boites), grille.x0 + grille.pas_m * max(b[1] for b in boites)
    za, zb = grille.z0 + grille.pas_m * min(b[2] for b in boites), grille.z0 + grille.pas_m * max(b[3] for b in boites)
    raise RefusDonnees(f'{manquants} noeud(s) de la grille dans l\'emprise du theatre ne sont donnes par aucune entree : '
                       f'boite x de {xa:g} a {xb:g}, z de {za:g} a {zb:g} (m)')


def ecrire_grille(grille, dossier, theatre, cle):
    """`relief_<cle>.npy` (float32, absent = NaN) et `relief_<cle>.json`. Retourne (chemin npy, document json)."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    npy = dossier / f'relief_{cle}.npy'
    np.save(npy, grille.alt)
    doc = {'theatre': theatre, 'cle': cle, 'x0': grille.x0, 'z0': grille.z0, 'pas_m': grille.pas_m, 'forme': grille.forme,
           'alt_min_m': round(float(np.nanmin(grille.alt)), 2), 'alt_max_m': round(float(np.nanmax(grille.alt)), 2),
           'entrees': grille.entrees}
    (dossier / f'relief_{cle}.json').write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    return npy, doc


# ------------------------------------------------------------------ l'emprise et les tuiles z11

def emprise_theatre(theatre):
    """(x_min, z_min, x_max, z_max) : le rectangle `terrain.bounds` de pydcs, en x, z (quel que soit l'ordre de ses bornes)."""
    b = dcsdata.load_terrain(theatre).bounds
    return (float(min(b.top, b.bottom)), float(min(b.left, b.right)), float(max(b.top, b.bottom)), float(max(b.left, b.right)))


def contour_emprise(emprise, projection, pas_m=PAS_CONTOUR_M):
    """Polygone lon/lat (shapely) du rectangle `emprise` : son contour echantillonne tous les `pas_m` au plus, puis projete."""
    x_min, z_min, x_max, z_max = emprise

    def cote(a, b):
        n = max(1, math.ceil(abs(b - a) / pas_m - 1e-9))
        return np.linspace(a, b, n + 1)[:-1]
    sud_z = cote(z_min, z_max)
    est_x = cote(x_min, x_max)
    nord_z = cote(z_max, z_min)
    ouest_x = cote(x_max, x_min)
    xs = np.concatenate([np.full(sud_z.size, x_min), est_x, np.full(nord_z.size, x_max), ouest_x])
    zs = np.concatenate([sud_z, np.full(est_x.size, z_max), nord_z, np.full(ouest_x.size, z_min)])
    lat, lon = projection.to_latlon(xs, zs)
    return shapely.Polygon(np.stack([np.asarray(lon), np.asarray(lat)], axis=1))


def bounds_leaflet(contour):
    """[[sud, ouest], [nord, est]] en degres, a 1e-6 pres : la boite lat/lon de l'emprise."""
    lon_o, lat_s, lon_e, lat_n = contour.bounds
    return [[round(lat_s, 6), round(lon_o, 6)], [round(lat_n, 6), round(lon_e, 6)]]


# ------------------------------------------------------------------ l'ombrage « volumes »

def ombrage(E, f):
    """Ombrage multidirectionnel d'une surface E (float64, Nord = +x = axe 0, Est = +z = axe 1, cases de PAS_M) avec une
    exageration `f` : ∂E/∂nord et ∂E/∂est par differences centrees, p = f ∂E/∂est, q = f ∂E/∂nord, pente sigma = atan(|(p, q)|),
    orientation omega = atan2(-p, -q), soleil a 45 degres de hauteur ; moyenne ponderee des azimuts de AZIMUTS."""
    nord = np.gradient(E, PAS_M, axis=0)
    est = np.gradient(E, PAS_M, axis=1)
    p, q = f * est, f * nord
    sigma = np.arctan(np.hypot(p, q))
    omega = np.arctan2(-p, -q)
    cs, ss = COS_ZETA * np.cos(sigma), SIN_ZETA * np.sin(sigma)
    somme = 0.0
    for az, w in AZIMUTS:
        somme = somme + w * (cs + ss * np.cos(math.radians(az) - omega))
    return somme / sum(w for _, w in AZIMUTS)


def ombrage_volumes(E):
    """s = 0,55 x ombrage(E, 3) + 0,45 x ombrage(E lissee, 7), E lissee par un filtre gaussien de 8 cases tronque a 4 sigma.
    E : tableau float64 sans NaN."""
    lisse = ndimage.gaussian_filter(E, SIGMA_CASES, truncate=TRONC_SIGMA)
    return POIDS_BRUTE * ombrage(E, F_BRUTE) + POIDS_LISSEE * ombrage(lisse, F_LISSEE)


def couleur_ombrage(s, valide):
    """Tuile RGBA (uint8) de l'ombrage `s` (valeur du pixel, apres interpolation) : d = s - cos 45 ; ombre (d < 0) : couleur
    (40, 32, 24), opacite min(1, -1,6 d) x 0,70 ; lumiere : (255, 252, 240), min(1, 2,2 d) x 0,55 ; alpha = opacite x 255
    arrondie. Un pixel d'alpha 0, ou hors de `valide`, est (0, 0, 0, 0)."""
    d = s - COS_ZETA
    ombre = d < 0
    opacite = np.where(ombre, np.minimum(1.0, -GAIN_OMBRE * d) * OPACITE_OMBRE, np.minimum(1.0, GAIN_LUMIERE * d) * OPACITE_LUMIERE)
    alpha = np.rint(opacite * 255.0).astype(np.uint8)
    out = np.zeros(s.shape + (4,), np.uint8)
    out[..., :3] = np.where(ombre[..., None], np.array(COULEUR_OMBRE, np.uint8), np.array(COULEUR_LUMIERE, np.uint8))
    out[..., 3] = alpha
    out[(alpha == 0) | ~valide] = 0
    return out


# ------------------------------------------------------------------ Terrain-RGB

def encoder_terrain_rgb(h, valide):
    """Tuile RGBA (uint8) des altitudes `h` (m) : v = arrondi((h + 10 000) x 10), R = v >> 16, G = v >> 8, B = v, alpha 255 ;
    un pixel hors de `valide` est (0, 0, 0, 0)."""
    v = np.rint((np.where(valide, h, 0.0) - BASE_M) * 10.0)
    v = np.clip(v, 0, V_MAX).astype(np.int64)
    out = np.zeros(h.shape + (4,), np.uint8)
    out[..., 0] = (v >> 16) & 255
    out[..., 1] = (v >> 8) & 255
    out[..., 2] = v & 255
    out[..., 3] = 255
    out[~valide] = 0
    return out


def valeurs_terrain_rgb(rgba):
    """Le nombre v = R x 65 536 + G x 256 + B de chaque pixel d'une tuile Terrain-RGB (entiers int64)."""
    t = rgba.astype(np.int64)
    return t[..., 0] * 65536 + t[..., 1] * 256 + t[..., 2]


def decoder_terrain_rgb(rgba):
    """Altitudes (m, float64) d'une tuile Terrain-RGB : h = -10 000 + v x 0,1 ; NaN pour un pixel transparent."""
    h = BASE_M + valeurs_terrain_rgb(rgba) * PAS_ALT_M
    return np.where(rgba[..., 3] > 0, h, np.nan)


def reduire_elevation(filles):
    """Tuile du zoom z - 1 de `elevation` : chaque pixel = la moyenne des altitudes decodees de ses pixels fils opaques,
    reencodee (meme arrondi) ; alpha 255 s'il en a au moins un, sinon (0, 0, 0, 0). `filles` : {(dx, dy): tuile RGBA ou None}
    (une fille absente est transparente). None si aucun pixel n'est opaque."""
    toile = np.zeros((2 * T.TILE, 2 * T.TILE, 4), np.uint8)
    for (dx, dy), t in filles.items():
        if t is not None:
            toile[dy * T.TILE:(dy + 1) * T.TILE, dx * T.TILE:(dx + 1) * T.TILE] = t
    opaque = (toile[..., 3] > 0).reshape(T.TILE, 2, T.TILE, 2)
    if not opaque.any():
        return None
    v = valeurs_terrain_rgb(toile).reshape(T.TILE, 2, T.TILE, 2)
    n = opaque.sum(axis=(1, 3))
    somme = np.where(opaque, v, 0).sum(axis=(1, 3))
    moyenne = np.rint(somme / np.maximum(n, 1)).astype(np.int64)         # la moyenne des h decodes, reencodee, est celle des v
    out = np.zeros((T.TILE, T.TILE, 4), np.uint8)
    out[..., 0] = (moyenne >> 16) & 255
    out[..., 1] = (moyenne >> 8) & 255
    out[..., 2] = moyenne & 255
    out[..., 3] = np.where(n > 0, 255, 0)
    out[n == 0] = 0
    return out


# ------------------------------------------------------------------ l'echantillonnage de la grille

def _coins(fi, fj, ni, nj):
    """Coordonnees fractionnaires (fi, fj) en cases -> (ok, i0, j0, ti, tj) : `ok` si le point est dans la grille ; i0, j0 :
    la case du coin (i0 <= ni - 2) et ti, tj : les poids dans [0, 1]. Hors de la grille, indices et poids sont neutres."""
    ok = (fi >= 0) & (fi <= ni - 1) & (fj >= 0) & (fj <= nj - 1)
    fi, fj = np.where(ok, fi, 0.0), np.where(ok, fj, 0.0)
    i0 = np.clip(np.floor(fi).astype(np.int64), 0, ni - 2)
    j0 = np.clip(np.floor(fj).astype(np.int64), 0, nj - 2)
    return ok, i0, j0, fi - i0, fj - j0


def _bilineaire(tab, i0, j0, ti, tj):
    """Interpolation bilineaire de `tab` aux coins (i0, j0) avec les poids (ti, tj). Un coin de poids nul ne compte pas ; un coin
    NaN de poids non nul donne NaN."""
    out = 0.0
    for di, dj, w in ((0, 0, (1 - ti) * (1 - tj)), (0, 1, (1 - ti) * tj), (1, 0, ti * (1 - tj)), (1, 1, ti * tj)):
        v = tab[i0 + di, j0 + dj].astype(np.float64)
        out = out + np.where(w > 0, w * v, 0.0)
    return out


def noeuds_proches_non_releves(alt_fenetre, rayon=RAYON_NON_RELEVE):
    """Booleens de la forme de `alt_fenetre` : vrai pour un noeud a `rayon` cases au plus (distance de Tchebychev) d'un noeud NaN de
    la fenetre. Le bord de la fenetre n'est pas un noeud non releve. None s'il n'y a aucun NaN."""
    manque = np.isnan(alt_fenetre)
    if not manque.any():
        return None
    return ndimage.maximum_filter(manque.astype(np.uint8), size=2 * rayon + 1, mode='constant', cval=0).astype(bool)


def rendre_tuiles(grille_alt, x0, z0, emprise, projection, tuiles):
    """Les tuiles z11 `tuiles` [(tx, ty)] des deux couches : {'hillshade': {(tx, ty): RGBA}, 'elevation': {...}} ; une tuile
    entierement transparente n'y est pas. Chaque pixel prend le point du sol de `render.tiles.cartes_xz` ; hors de l'emprise,
    hors de la grille, ou sur un noeud manquant, il est transparent. Zones non relevees (NaN dans la grille) : `elevation` est
    transparente sur un pixel dont un noeud d'interpolation de poids non nul est NaN ; `hillshade` l'est en plus sur un pixel dont
    l'un des quatre noeuds d'interpolation est a RAYON_NON_RELEVE cases au plus d'un noeud NaN (l'ombrage remplace NaN par 0 et
    dessinerait une falaise). Le resultat d'une tuile ne depend pas des autres."""
    ni, nj = grille_alt.shape
    x_min, z_min, x_max, z_max = emprise
    cartes = {}
    f_lo = np.array([np.inf, np.inf])
    f_hi = np.array([-np.inf, -np.inf])
    for tx, ty in tuiles:
        X, Z = T.cartes_xz(ZOOM_MAX, tx, ty, projection)
        fi, fj = (X - x0) / PAS_M, (Z - z0) / PAS_M
        cartes[(tx, ty)] = (X, Z, fi, fj)
        f_lo = np.minimum(f_lo, [fi.min(), fj.min()])
        f_hi = np.maximum(f_hi, [fi.max(), fj.max()])
    out = {c: {} for c in COUCHES}
    if not tuiles:
        return out
    # la fenetre de la grille : les noeuds des pixels + MARGE_CASES, rognee a la grille
    wi0, wi1 = max(0, math.floor(f_lo[0]) - MARGE_CASES), min(ni - 1, math.floor(f_hi[0]) + 1 + MARGE_CASES)
    wj0, wj1 = max(0, math.floor(f_lo[1]) - MARGE_CASES), min(nj - 1, math.floor(f_hi[1]) + 1 + MARGE_CASES)
    s = proche = None
    if wi1 - wi0 >= 1 and wj1 - wj0 >= 1:
        brut = np.asarray(grille_alt[wi0:wi1 + 1, wj0:wj1 + 1], dtype=np.float64)
        proche = noeuds_proches_non_releves(brut)
        fenetre = np.nan_to_num(brut, nan=0.0)
        del brut
        s = ombrage_volumes(fenetre)
        del fenetre
    for (tx, ty), (X, Z, fi, fj) in cartes.items():
        ok, i0, j0, ti, tj = _coins(fi, fj, ni, nj)
        ok &= (X >= x_min) & (X <= x_max) & (Z >= z_min) & (Z <= z_max)
        if not ok.any():
            continue
        h = _bilineaire(grille_alt, i0, j0, ti, tj)
        ok &= ~np.isnan(h)
        if not ok.any():
            continue
        out['elevation'][(tx, ty)] = encoder_terrain_rgb(h, ok)
        wsi, wsj = s.shape
        pi, pj = np.clip(i0 - wi0, 0, wsi - 2), np.clip(j0 - wj0, 0, wsj - 2)
        si = _bilineaire(s, pi, pj, ti, tj)
        valide = ok
        if proche is not None:
            valide = ok & ~(proche[pi, pj] | proche[pi + 1, pj] | proche[pi, pj + 1] | proche[pi + 1, pj + 1])
        ombre = couleur_ombrage(si, valide)
        if ombre[..., 3].any():
            out['hillshade'][(tx, ty)] = ombre
    return out


# ------------------------------------------------------------------ le codage et les fichiers

def encoder(rgba, couche):
    """`hillshade` : WebP sans perte (methode 6 ; `exact` : le RGB d'un pixel d'alpha 0 est conserve, la tuile relue est celle qu'on
    a ecrite : `reduire` en laisse sous un alpha arrondi a 0 ; pour les tuiles z11, dont ces pixels sont (0, 0, 0, 0), les octets
    sont ceux du codage sans `exact`) ; `elevation` : PNG RGBA 8 bits sans palette, compression 9."""
    buf = io.BytesIO()
    im = Image.fromarray(rgba, 'RGBA')
    if couche == 'hillshade':
        im.save(buf, format='WEBP', lossless=True, method=6, exact=True)
    else:
        im.save(buf, format='PNG', compress_level=9)
    return buf.getvalue()


def chemin_tuile(dossier, couche, z, x, y):
    return Path(dossier) / 'tiles' / couche / str(z) / str(x) / f'{y}.{EXTENSIONS[couche]}'


def ecrire_tuile(dossier, couche, z, x, y, rgba):
    p = chemin_tuile(dossier, couche, z, x, y)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = encoder(rgba, couche)
    p.write_bytes(data)
    return len(data)


def reduire_niveau(courant, couche):
    """Le niveau du dessus de {(x, y): tuile} : chaque parent = la reduction 2 x 2 de ses filles (hillshade : `render.tiles.reduire` ;
    elevation : `reduire_elevation`). Un parent entierement transparent n'existe pas."""
    parents = {}
    for (x, y), t in courant.items():
        parents.setdefault((x >> 1, y >> 1), {})[(x & 1, y & 1)] = t
    fonction = T.reduire if couche == 'hillshade' else reduire_elevation
    out = {}
    for p, filles in parents.items():
        r = fonction(filles)
        if r is not None:
            out[p] = r
    return out


def monter(courant, couche, z_depart, z_arret, dossier, compte):
    """Reduit {(x, y): tuile} du zoom `z_depart` jusqu'a `z_arret` (inclus), en ecrivant chaque niveau. Retourne (le dernier niveau,
    octets ecrits) ; `compte` : {couche: {zoom: n}} mis a jour."""
    octets = 0
    for z in range(z_depart - 1, z_arret - 1, -1):
        courant = reduire_niveau(courant, couche)
        for (x, y), t in courant.items():
            octets += ecrire_tuile(dossier, couche, z, x, y, t)
        compte[couche][z] = compte[couche].get(z, 0) + len(courant)
    return courant, octets


# ------------------------------------------------------------------ les blocs de travail

_CTX = {}


def _contexte(ctx):
    st = os.stat(ctx['grille'])
    cle = (ctx['grille'], ctx['theatre'], st.st_mtime_ns, st.st_size)
    if cle not in _CTX:
        _CTX.clear()
        _CTX[cle] = (np.load(ctx['grille'], mmap_mode='r'), geo.Projection.for_theatre(ctx['theatre']))
    return _CTX[cle]


def traiter_bloc(arg):
    """Un bloc de travail : (ctx, bloc). ctx : grille (chemin du .npy), theatre, x0, z0, emprise, dossier. bloc : niveaux, tuiles
    [(tx, ty)] z11. Rend les tuiles z11 des deux couches, les ecrit, puis monte de `niveaux` niveaux (z11 -> z11 - niveaux, ecrits).
    Retourne {'compte': {couche: {zoom: n}}, 'octets', 'haut': {couche: {(x, y): tuile}} (le dernier niveau, pour la suite),
    'ms_rendu', 'ms_pyramide', 'ms_codage'}."""
    ctx, bloc = arg
    alt, proj = _contexte(ctx)
    compte = {c: {} for c in COUCHES}
    t0 = time.perf_counter()
    z11 = rendre_tuiles(alt, ctx['x0'], ctx['z0'], tuple(ctx['emprise']), proj, bloc['tuiles'])
    ms_rendu = (time.perf_counter() - t0) * 1000
    octets, ms_codage, ms_pyr = 0, 0.0, 0.0
    haut = {}
    for couche in COUCHES:
        courant = z11[couche]
        t0 = time.perf_counter()
        for (x, y), t in courant.items():
            octets += ecrire_tuile(ctx['dossier'], couche, ZOOM_MAX, x, y, t)
        compte[couche][ZOOM_MAX] = len(courant)
        ms_codage += (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        courant, o = monter(courant, couche, ZOOM_MAX, ZOOM_MAX - bloc['niveaux'], ctx['dossier'], compte)
        ms_pyr += (time.perf_counter() - t0) * 1000
        octets += o
        haut[couche] = courant
    return {'compte': compte, 'octets': octets, 'haut': haut, 'ms_rendu': ms_rendu, 'ms_pyramide': ms_pyr, 'ms_codage': ms_codage}


def plan_blocs(tuiles, niveaux):
    """{(bx, by): [(tx, ty)]} : les tuiles z11 regroupees en blocs alignes de 2^niveaux x 2^niveaux."""
    blocs = {}
    for tx, ty in tuiles:
        blocs.setdefault((int(tx) >> niveaux, int(ty) >> niveaux), []).append((int(tx), int(ty)))
    return blocs


def _executer(taches, processus):
    """Resultats des blocs (dans l'ordre d'arrivee). 1 processus : dans celui-ci."""
    if processus <= 1 or len(taches) <= 1:
        for t in taches:
            yield traiter_bloc(t)
        return
    with multiprocessing.get_context().Pool(processus) as pool:
        yield from pool.imap_unordered(traiter_bloc, taches)


def effacer_tuiles(dossier):
    """Efface les anciennes tuiles des deux couches sous `<dossier>/tiles/` : un dossier absent passe ; une erreur (dossier qu'on ne
    peut pas effacer, fichier a la place d'un dossier...) arrete la passe (ErreurSortie, le dossier et la cause nommes)."""
    for couche in COUCHES:
        d = Path(dossier) / 'tiles' / couche
        try:
            shutil.rmtree(d)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise ErreurSortie(f'les anciennes tuiles de {d.name} ne peuvent pas etre effacees ({d} : {e}) : passe arretee')


def produire_tuiles(chemin_grille, theatre, x0, z0, emprise, dossier, niveaux=NIVEAUX_DEFAUT, processus=1, log=_rien):
    """Toutes les tuiles des deux couches de z11 a z6 sous `<dossier>/tiles/<couche>/{z}/{x}/{y}.<ext>` (les anciennes tuiles
    de ces deux dossiers sont effacees d'abord ; ErreurSortie si l'effacement echoue). Retourne {'compte': {couche: {zoom: n}}, 'octets', 'bounds', 'ms': {...}}."""
    if not 1 <= niveaux <= ZOOM_MAX - ZOOM_MIN:
        raise ValueError(f'niveaux : de 1 a {ZOOM_MAX - ZOOM_MIN}')
    dossier = Path(dossier)
    effacer_tuiles(dossier)
    proj = geo.Projection.for_theatre(theatre)
    contour = contour_emprise(emprise, proj)
    z11 = T.tuiles_de_emprise(contour, ZOOM_MAX)
    blocs = plan_blocs(z11.tolist(), niveaux)
    log(f'{len(z11)} tuiles z11 coupent l\'emprise, {len(blocs)} blocs de {2 ** niveaux} x {2 ** niveaux} tuiles, {processus} processus')
    ctx = {'grille': str(chemin_grille), 'theatre': theatre, 'x0': x0, 'z0': z0, 'emprise': list(emprise), 'dossier': str(dossier)}
    taches = [(ctx, {'niveaux': niveaux, 'tuiles': t}) for _, t in sorted(blocs.items())]
    compte = {c: {} for c in COUCHES}
    haut = {c: {} for c in COUCHES}
    octets, ms = 0, {'rendu': 0.0, 'pyramide': 0.0, 'codage': 0.0}
    fait = 0
    for r in _executer(taches, processus):
        fait += 1
        octets += r['octets']
        ms['rendu'] += r['ms_rendu']
        ms['pyramide'] += r['ms_pyramide']
        ms['codage'] += r['ms_codage']
        for c in COUCHES:
            for z, n in r['compte'][c].items():
                compte[c][z] = compte[c].get(z, 0) + n
            haut[c].update(r['haut'][c])
        if fait % 10 == 0 or fait == len(taches):
            log(f'  {fait} / {len(taches)} blocs')
    _CTX.clear()
    z_haut = ZOOM_MAX - niveaux
    t0 = time.perf_counter()
    for c in COUCHES:
        if z_haut > ZOOM_MIN:
            _, o = monter(haut[c], c, z_haut, ZOOM_MIN, dossier, compte)
            octets += o
    ms['haut'] = (time.perf_counter() - t0) * 1000
    return {'compte': {c: dict(sorted(compte[c].items())) for c in COUCHES}, 'octets': octets, 'bounds': bounds_leaflet(contour),
            'tuiles_z11': int(len(z11)), 'ms': ms}


# ------------------------------------------------------------------ le zip

def lister_tuiles(dossier):
    """Les tuiles ecrites : {couche: [(z, x, y, chemin)]} triees."""
    out = {}
    for couche in COUCHES:
        ext = '.' + EXTENSIONS[couche]
        liste = []
        for p in (Path(dossier) / 'tiles' / couche).glob('*/*/*' + ext):
            liste.append((int(p.parent.parent.name), int(p.parent.name), int(p.stem), p))
        liste.sort(key=lambda t: t[:3])
        out[couche] = liste
    return out


def comptes_par_zoom(liste):
    """{couche: {"z": n}} d'apres `lister_tuiles`."""
    out = {}
    for couche, tuiles in liste.items():
        c = {}
        for z, _, _, _ in tuiles:
            c[str(z)] = c.get(str(z), 0) + 1
        out[couche] = dict(sorted(c.items(), key=lambda kv: int(kv[0])))
    return out


def document_relief(cle, edition, bounds, entrees, comptes):
    """`relief.json` du zip."""
    return {'format': FORMAT, 'theatre': cle, 'edition': edition, 'minZoom': ZOOM_MIN, 'maxNativeZoom': ZOOM_MAX,
            'tileSize': T.TILE, 'bounds': bounds,
            'layers': {'hillshade': {'path': 'hillshade/{z}/{x}/{y}.webp'},
                       'elevation': {'path': 'elevation/{z}/{x}/{y}.png', 'encoding': 'terrain-rgb', 'base_m': int(BASE_M),
                                     'step_m': PAS_ALT_M}},
            'attribution': ATTRIBUTION,
            'source': [{'file': e['fichier'], 'sha256': e['sha256']} for e in entrees],
            'tiles_per_zoom': comptes}


def nom_zip(theatre):
    return f'DCS_Airfield_Charts_{theatre}_relief.zip'


def ecrire_zip(dossier, theatre, cle, edition, bounds, entrees):
    """`<dossier>/DCS_Airfield_Charts_<theatre>_relief.zip` (ZIP_STORED, dates fixes, ordre trie : memes entrees, meme zip a l'octet)
    et `<zip>.json` (nom, octets, sha256, tuiles par zoom et par couche). Retourne le document du `<zip>.json`."""
    dossier = Path(dossier)
    liste = lister_tuiles(dossier)
    comptes = comptes_par_zoom(liste)
    membres = []
    for couche, tuiles in liste.items():
        for z, x, y, p in tuiles:
            membres.append((f'{couche}/{z}/{x}/{y}.{EXTENSIONS[couche]}', p))
    doc = json.dumps(document_relief(cle, edition, bounds, entrees, comptes), indent=1, ensure_ascii=False) + '\n'
    membres.sort(key=lambda m: m[0])
    chemin = dossier / nom_zip(theatre)
    provisoire = chemin.with_name(chemin.name + '.tmp')
    with zipfile.ZipFile(provisoire, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        def ajouter(nom, octets):
            info = zipfile.ZipInfo(nom, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, octets)
        tout = [(n, None, p) for n, p in membres]
        tout += [('relief.json', doc.encode('utf-8'), None), (LICENCE_NOM, LICENCE_RELIEF.encode('utf-8'), None)]
        for nom, octets, p in sorted(tout, key=lambda m: m[0]):
            ajouter(nom, p.read_bytes() if p is not None else octets)
    os.replace(provisoire, chemin)
    info = {'name': chemin.name, 'bytes': chemin.stat().st_size, 'sha256': T.sha256_fichier(chemin), 'tiles_per_zoom': comptes}
    (dossier / (chemin.name + '.json')).write_text(json.dumps(info, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    return info
