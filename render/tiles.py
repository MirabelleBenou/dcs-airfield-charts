"""Tuiles XYZ de la carte F10 : loi de la camera, Web Mercator, grille de capture, mode de la carte, reprojection, pyramide,
codage et archives (lot ac-tuiles-v1).

Module pur : aucun import de capture/f10capture.py (il se teste sous Linux, sans DCS ni ecran). L'outil capture/f10tiles.py
lui passe les constantes de l'ecran (taille utile, decalage du recadrage) et l'echelle mesuree.

Reperes. Le repere DCS d'un theatre : x vers le Nord, z vers l'Est (metres). Une image de capture est la carte F10 recadree
(marges haut et droite retirees) : son centre est le point (xc, zc) du sol ; le pixel (r, c) est en
    x = xc - (r + 0,5 - h/2) * mpp ,   z = zc + (c + 0,5 - w/2) * mpp .
Une tuile (z, tx, ty) de 256 px est en Web Mercator (EPSG:3857) ; son pixel (i, j) est au pixel monde
(tx * 256 + j + 0,5 ; ty * 256 + i + 0,5).
"""
import hashlib
import io
import json
import math
import tarfile
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
import shapely
from PIL import Image
from shapely.geometry import shape as _shape

TILE = 256
M_PAR_PX_Z0 = 156543.033928            # metres par pixel a l'equateur, zoom 0
MPP_PAR_ALT_M = 0.00033555             # m/px par metre de hauteur vraie de la camera F10 (meme valeur que f10capture)
K_HAUTEUR_M = 5232.6                   # au-dela, la hauteur vraie est altitude^2 / K
LAT_MAX = 85.0511287798066
PAS_NOEUDS_PX = 16                     # treillis de la reprojection : x, z calcules exactement tous les 16 px, interpoles entre
BLOC_NIVEAUX = 4                       # un bloc de travail = 2^4 x 2^4 tuiles du zoom de depart, 4 niveaux de pyramide
PNG_COULEURS = 256
WEBP_QUALITE = 85
WEBP_METHODE = 6
ALPHA_OPAQUE_MIN = 128                 # PNG a palette : transparence binaire (alpha >= 128 : opaque)
TAILLE_PART_DEFAUT = 300000000
LICENCE_NOM = 'LICENSE-TILES.txt'
EXTENSIONS = {'alt': 'png', 'sat': 'webp'}

MENTION = ('Map imagery © Eagle Dynamics SA — captured from the DCS World F10 map. Non-commercial use only. '
           'Not affiliated with or endorsed by Eagle Dynamics.')
LICENCE_TUILES = (MENTION + '\n\nThese map tiles are not covered by the MIT licence of the code in this repository. '
                  'They are images of the DCS World F10 map and remain subject to the terms of Eagle Dynamics SA.\n')

# Cle d'un theatre dans le Route Planner (nom du jeu en minuscules -> cle).
CLES_ROUTE_PLANNER = {'syria': 'Syria', 'germanycoldwar': 'GermanyCW', 'caucasus': 'Caucasus'}

# Mode de la carte F10 (§2.5) : centre de l'image recadree et seuils.
MODE_X0, MODE_Y0, MODE_W, MODE_H = 800, 300, 1800, 800
APLAT_SAT_MAX = 0.15                   # sat : aplat <= 0,15
APLAT_PLAT_MIN = 0.30                  # alt et map : aplat >= 0,30
SATURATION_ALT_MIN = 80.0              # alt : saturation >= 80
SATURATION_MAP_MAX = 75.0              # map : saturation < 75


# ------------------------------------------------------------------ la camera F10

def mpp_tuile(zoom, lat):
    """Metres par pixel d'une tuile du zoom `zoom` a la latitude `lat` (degres)."""
    return M_PAR_PX_Z0 * math.cos(math.radians(lat)) / 2.0 ** zoom


def hauteur_vraie(alt):
    """Hauteur vraie de la camera pour l'altitude commandee `alt` : l'altitude jusqu'a K, puis alt^2 / K."""
    return alt if alt <= K_HAUTEUR_M else alt * alt / K_HAUTEUR_M


def mpp_camera(alt):
    """Echelle (m/px) de la carte F10 a l'altitude commandee `alt`."""
    return MPP_PAR_ALT_M * hauteur_vraie(alt)


def altitude_commandee(mpp):
    """Altitude a commander pour obtenir l'echelle `mpp` (inverse de mpp_camera)."""
    h = mpp / MPP_PAR_ALT_M
    return h if h <= K_HAUTEUR_M else math.sqrt(K_HAUTEUR_M * h)


# ------------------------------------------------------------------ Web Mercator

def monde_px(lat, lon, zoom):
    """Pixel monde (x, y) (origine en haut a gauche, y vers le sud) d'un point lat/lon au zoom `zoom`."""
    n = TILE * 2.0 ** zoom
    lat = np.clip(np.asarray(lat, float), -LAT_MAX, LAT_MAX)
    s = np.sin(np.radians(lat))
    x = (np.asarray(lon, float) + 180.0) / 360.0 * n
    y = (0.5 - np.log((1.0 + s) / (1.0 - s)) / (4.0 * np.pi)) * n
    return x, y


def latlon_de_monde_px(x, y, zoom):
    """Inverse de monde_px : (lat, lon) en degres."""
    n = TILE * 2.0 ** zoom
    lon = np.asarray(x, float) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * np.asarray(y, float) / n))))
    return lat, lon


def boite_tuile(zoom, tx, ty):
    """(ouest, sud, est, nord) en degres de la tuile (zoom, tx, ty)."""
    lat_n, lon_o = latlon_de_monde_px(tx * TILE, ty * TILE, zoom)
    lat_s, lon_e = latlon_de_monde_px((tx + 1) * TILE, (ty + 1) * TILE, zoom)
    return float(lon_o), float(lat_s), float(lon_e), float(lat_n)


# ------------------------------------------------------------------ l'emprise

def charger_emprise(chemin):
    """Emprise d'un fichier GeoJSON (un Feature, un Polygon ou un MultiPolygon, lon/lat), telle quelle.
    Retourne (geometrie shapely, sha256 hexa du fichier)."""
    raw = Path(chemin).read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    if data.get('type') == 'Feature':
        data = data['geometry']
    elif data.get('type') == 'FeatureCollection':
        geoms = [_shape(f['geometry']) for f in data['features']]
        g = shapely.union_all(geoms)
        return _polygonale(g), hashlib.sha256(raw).hexdigest()
    return _polygonale(_shape(data)), hashlib.sha256(raw).hexdigest()


def _polygonale(g):
    if g.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError(f'emprise : un polygone est attendu (recu : {g.geom_type})')
    if not g.is_valid:
        g = shapely.make_valid(g)
    return g


def emprise_rectangle(lat_s, lon_o, lat_n, lon_e):
    """Rectangle lat/lon (degres) comme emprise. ValueError s'il est vide ou hors du monde."""
    if not (-90.0 <= lat_s < lat_n <= 90.0 and -180.0 <= lon_o < lon_e <= 180.0):
        raise ValueError('rectangle : attendu lat_s < lat_n dans [-90, 90] et lon_o < lon_e dans [-180, 180]')
    return shapely.box(lon_o, lat_s, lon_e, lat_n)


def _boites_tuiles(gx, gy, zoom):
    lat_h, lon_g = latlon_de_monde_px(gx * TILE, gy * TILE, zoom)
    lat_b, lon_d = latlon_de_monde_px((gx + 1) * TILE, (gy + 1) * TILE, zoom)
    return shapely.box(lon_g, lat_b, lon_d, lat_h)


def _grille_tuiles(tx0, tx1, ty0, ty1):
    gx, gy = np.meshgrid(np.arange(tx0, tx1 + 1), np.arange(ty0, ty1 + 1))
    return gx.ravel(), gy.ravel()


def tuiles_de_emprise(emprise, zoom, brut=False):
    """Tuiles du zoom `zoom` dont la boite (lon/lat) coupe l'emprise : tableau (n, 2) d'entiers (tx, ty), trie par (ty, tx).
    Par blocs de 16 x 16 tuiles : un bloc tout entier dans l'emprise l'est pour ses 256 tuiles, un bloc qui ne la coupe pas
    n'en donne aucune, les blocs de bord sont testes tuile par tuile (`brut` : toutes les tuiles, tuile par tuile)."""
    lon_o, lat_s, lon_e, lat_n = emprise.bounds
    n = 2 ** zoom
    x0, y0 = monde_px(lat_n, lon_o, zoom)
    x1, y1 = monde_px(lat_s, lon_e, zoom)
    tx0, tx1 = max(0, int(x0 // TILE)), min(n - 1, int(x1 // TILE))
    ty0, ty1 = max(0, int(y0 // TILE)), min(n - 1, int(y1 // TILE))
    if tx1 < tx0 or ty1 < ty0:
        return np.empty((0, 2), np.int64)
    shapely.prepare(emprise)
    if brut:
        gx, gy = _grille_tuiles(tx0, tx1, ty0, ty1)
        sel = shapely.intersects(_boites_tuiles(gx, gy, zoom), emprise)
        return np.stack([gx[sel], gy[sel]], axis=1).astype(np.int64)
    B = 16
    bx, by = _grille_tuiles(tx0 // B, tx1 // B, ty0 // B, ty1 // B)
    ax, ay = bx * B, by * B                                     # premiere tuile du bloc
    ex, ey = np.minimum(ax + B - 1, n - 1), np.minimum(ay + B - 1, n - 1)       # derniere tuile du bloc
    lat_h, lon_g = latlon_de_monde_px(ax * TILE, ay * TILE, zoom)
    lat_b, lon_d = latlon_de_monde_px((ex + 1) * TILE, (ey + 1) * TILE, zoom)
    boites = shapely.box(lon_g, lat_b, lon_d, lat_h)
    coupe = shapely.intersects(boites, emprise)
    dedans = coupe & shapely.contains(emprise, boites)
    out = []
    for i in np.nonzero(coupe)[0]:
        gx, gy = _grille_tuiles(max(ax[i], tx0), min(ex[i], tx1), max(ay[i], ty0), min(ey[i], ty1))
        if not dedans[i]:
            sel = shapely.intersects(_boites_tuiles(gx, gy, zoom), emprise)
            gx, gy = gx[sel], gy[sel]
        out.append(np.stack([gx, gy], axis=1))
    if not out:
        return np.empty((0, 2), np.int64)
    t = np.concatenate(out).astype(np.int64)
    return t[np.lexsort((t[:, 0], t[:, 1]))]


def emprise_xz(emprise, projection, pas_deg=0.01):
    """L'emprise (lon/lat) dans le repere DCS : les bords sont decoupes tous les `pas_deg` degres puis chaque sommet est
    projete. Les coordonnees shapely du resultat sont (x, z) : x Nord, z Est."""
    dense = shapely.segmentize(emprise, pas_deg)

    def f(coords):
        lat, lon = coords[:, 1], coords[:, 0]
        x, z = projection.to_xz(lat, lon)
        return np.stack([np.asarray(x), np.asarray(z)], axis=1)
    return shapely.transform(dense, f)


# ------------------------------------------------------------------ la grille de capture

def plan_grille(emp_xz, mpp, utile_px, recouvrement_px, decalage_px):
    """Grille de cases couvrant l'emprise (dans le repere DCS).

    utile_px : (largeur, hauteur) de l'image recadree ; recouvrement_px : px de chaque cote ; decalage_px : (dx, dz) du
    recadrage en pixels (le contenu garde est decale de dx px vers le sud et dz px vers l'ouest de la camera : on commande
    la camera en x + dx * mpp, z + dz * mpp). Retourne un dict : empreinte_m [fh (x), fw (z)], pas_m [sth, stw], lignes,
    colonnes, cases [{row, col, x, z}] (positions commandees ; ligne 0 = nord ; seules les cases dont l'empreinte coupe
    l'emprise), centre_x0, centre_z0 (centre du contenu de la case (0, 0))."""
    uw, uh = utile_px
    fw, fh = uw * mpp, uh * mpp
    stw, sth = (uw - 2 * recouvrement_px) * mpp, (uh - 2 * recouvrement_px) * mpp
    x_min, z_min, x_max, z_max = emp_xz.bounds        # shapely : (minx = x, miny = z, maxx, maxy)
    nrow = max(1, math.ceil((x_max - x_min) / sth - 1e-9))
    ncol = max(1, math.ceil((z_max - z_min) / stw - 1e-9))
    cx, cz = (x_min + x_max) / 2, (z_min + z_max) / 2
    dxm, dzm = decalage_px[0] * mpp, decalage_px[1] * mpp
    xs = cx + ((nrow - 1) / 2 - np.arange(nrow)) * sth
    zs = cz + (np.arange(ncol) - (ncol - 1) / 2) * stw
    gi, gj = np.meshgrid(np.arange(nrow), np.arange(ncol), indexing='ij')
    gi, gj = gi.ravel(), gj.ravel()
    boites = shapely.box(xs[gi] - fh / 2, zs[gj] - fw / 2, xs[gi] + fh / 2, zs[gj] + fw / 2)
    shapely.prepare(emp_xz)
    sel = shapely.intersects(boites, emp_xz)
    cases = [{'row': int(i), 'col': int(j), 'x': float(xs[i] + dxm), 'z': float(zs[j] + dzm)}
             for i, j in zip(gi[sel], gj[sel])]
    return {'empreinte_m': [fh, fw], 'pas_m': [sth, stw], 'lignes': nrow, 'colonnes': ncol, 'cases': cases,
            'centre_x0': float(xs[0]), 'centre_z0': float(zs[0])}


def ordre_serpentin(cases):
    """Ordre de visite : ligne par ligne du nord au sud, en serpentin (ligne 0 d'ouest en est, ligne 1 d'est en ouest...)."""
    out = []
    for r in sorted({c['row'] for c in cases}):
        ligne = sorted((c for c in cases if c['row'] == r), key=lambda c: c['col'])
        out.extend(ligne if r % 2 == 0 else ligne[::-1])
    return out


# ------------------------------------------------------------------ le mode de la carte

def mesurer_centre(centre):
    """(aplat, saturation) du centre d'une image (tableau RGB uint8) : part des pixels ou le laplacien 3 x 3 des niveaux de
    gris vaut 0, et moyenne du canal S (HSV, 0-255)."""
    gris = cv2.cvtColor(centre, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gris, cv2.CV_16S, ksize=1)
    aplat = float(np.mean(lap == 0))
    sat = float(cv2.cvtColor(centre, cv2.COLOR_RGB2HSV)[:, :, 1].mean())
    return aplat, sat


def classer_mode(aplat, saturation):
    """'sat', 'alt', 'map' ou 'inconnu'."""
    if aplat <= APLAT_SAT_MAX:
        return 'sat'
    if aplat >= APLAT_PLAT_MIN:
        if saturation >= SATURATION_ALT_MIN:
            return 'alt'
        if saturation < SATURATION_MAP_MAX:
            return 'map'
    return 'inconnu'


def mode_de_image(img):
    """(mode, aplat, saturation) d'une image recadree (RGB uint8) : mesure sur son centre de MODE_W x MODE_H px a partir de
    (MODE_X0, MODE_Y0). Une image trop petite pour ce centre : 'inconnu'."""
    centre = img[MODE_Y0:MODE_Y0 + MODE_H, MODE_X0:MODE_X0 + MODE_W]
    if centre.shape[0] < 3 or centre.shape[1] < 3:
        return 'inconnu', float('nan'), float('nan')
    a, s = mesurer_centre(np.ascontiguousarray(centre))
    return classer_mode(a, s), a, s


# ------------------------------------------------------------------ les sources d'images et la reprojection

class Sources:
    """Images georeferencees (cases d'une grille, mosaiques de charts). Chacune : centre du contenu (xc, zc) en metres,
    echelle mpp, taille w x h en pixels, cle (pour le chargeur)."""

    def __init__(self, xc, zc, mpp, w, h, cles):
        self.xc, self.zc = np.asarray(xc, float), np.asarray(zc, float)
        self.mpp = np.asarray(mpp, float)
        self.w, self.h = np.asarray(w, np.int64), np.asarray(h, np.int64)
        self.cles = list(cles)
        self.demi_x = self.h / 2.0 * self.mpp          # demi-etendue selon x (lignes)
        self.demi_z = self.w / 2.0 * self.mpp          # demi-etendue selon z (colonnes)

    def __len__(self):
        return len(self.cles)

    def candidates(self, x_lo, x_hi, z_lo, z_hi):
        """Indices des sources dont l'image coupe la boite [x_lo, x_hi] x [z_lo, z_hi]."""
        m = ((self.xc + self.demi_x >= x_lo) & (self.xc - self.demi_x <= x_hi)
             & (self.zc + self.demi_z >= z_lo) & (self.zc - self.demi_z <= z_hi))
        return np.nonzero(m)[0]


class CacheImages:
    """Cache d'images decodees (les plus recentes d'abord), borne en nombre d'images."""

    def __init__(self, charger, n=16):
        self._charger, self._n = charger, n
        self._d = OrderedDict()

    def __call__(self, cle):
        if cle in self._d:
            self._d.move_to_end(cle)
            return self._d[cle]
        img = self._charger(cle)
        self._d[cle] = img
        while len(self._d) > self._n:
            self._d.popitem(last=False)
        return img


def charger_image_rgb(chemin):
    """Image (PNG ou WebP) en tableau RGB uint8."""
    with Image.open(chemin) as im:
        return np.ascontiguousarray(np.asarray(im.convert('RGB')))


def _interpoler_noeuds(noeuds):
    """Treillis (K, K) tous les PAS_NOEUDS_PX px -> carte (256, 256) aux centres de pixels, interpolation bilineaire."""
    f = (np.arange(TILE) + 0.5) / PAS_NOEUDS_PX
    k = np.floor(f).astype(int)
    t = f - k
    lignes = noeuds[k] * (1 - t)[:, None] + noeuds[k + 1] * t[:, None]
    return lignes[:, k] * (1 - t)[None, :] + lignes[:, k + 1] * t[None, :]


def cartes_xz(zoom, tx, ty, projection):
    """(X, Z) : le point du sol (repere DCS) de chaque pixel d'une tuile, deux tableaux (256, 256). Coordonnees de tuile ->
    lat/lon -> x, z : calcule exactement sur un treillis de pas PAS_NOEUDS_PX px, interpole entre ses noeuds."""
    u = np.arange(0, TILE + 1, PAS_NOEUDS_PX, dtype=float)
    k = len(u)
    px = np.broadcast_to(tx * TILE + u[None, :], (k, k))
    py = np.broadcast_to(ty * TILE + u[:, None], (k, k))
    lat, lon = latlon_de_monde_px(px.ravel(), py.ravel(), zoom)
    x, z = projection.to_xz(lat, lon)
    x, z = np.asarray(x).reshape(k, k), np.asarray(z).reshape(k, k)
    return _interpoler_noeuds(x), _interpoler_noeuds(z)


def rendre_tuile(zoom, tx, ty, projection, sources, cache, maps=None):
    """Tuile RGBA (256, 256, 4) uint8 du zoom `zoom`, ou None si aucune image ne couvre aucun de ses pixels.

    Chaque pixel va chercher son point : tuile -> lat/lon -> (x, z) -> la source dont le centre est le plus proche parmi
    celles dont l'image couvre ce point -> pixel de l'image, interpole bilineaire (un seul reechantillonnage). Un pixel que
    aucune image ne couvre est transparent."""
    X, Z = maps if maps is not None else cartes_xz(zoom, tx, ty, projection)
    cand = sources.candidates(X.min(), X.max(), Z.min(), Z.max())
    if len(cand) == 0:
        return None
    rr, cc, d2 = [], [], []
    for i in cand:
        mpp, w, h = sources.mpp[i], int(sources.w[i]), int(sources.h[i])
        r = (sources.xc[i] - X) / mpp + h / 2.0 - 0.5
        c = (Z - sources.zc[i]) / mpp + w / 2.0 - 0.5
        couvre = (r >= 0) & (r <= h - 1) & (c >= 0) & (c <= w - 1)
        d = (X - sources.xc[i]) ** 2 + (Z - sources.zc[i]) ** 2
        d[~couvre] = np.inf
        rr.append(r)
        cc.append(c)
        d2.append(d)
    if len(cand) == 1:
        etiq = np.where(np.isfinite(d2[0]), 0, -1)
    else:
        pile = np.stack(d2)
        etiq = pile.argmin(axis=0)
        etiq[~np.isfinite(pile.min(axis=0))] = -1
    if not (etiq >= 0).any():
        return None
    out = np.zeros((TILE, TILE, 4), np.uint8)
    for n, i in enumerate(cand):
        sel = etiq == n
        if not sel.any():
            continue
        img = cache(sources.cles[i])
        warp = cv2.remap(img, cc[n].astype(np.float32), rr[n].astype(np.float32), cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
        out[sel, :3] = warp[sel]
        out[sel, 3] = 255
    return out


# ------------------------------------------------------------------ la pyramide et le codage

def reduire(filles):
    """Tuile du zoom z - 1 : la reduction 2 x 2 (moyenne, alpha compris, arrondie) de ses quatre filles. `filles` : dict
    {(dx, dy): tuile RGBA ou None}, dx dy dans {0, 1} (colonne, ligne) ; une fille absente est transparente. None si
    toutes sont absentes ou transparentes."""
    toile = np.zeros((2 * TILE, 2 * TILE, 4), np.uint8)
    present = False
    for (dx, dy), t in filles.items():
        if t is None:
            continue
        toile[dy * TILE:(dy + 1) * TILE, dx * TILE:(dx + 1) * TILE] = t
        present = True
    if not present:
        return None
    s = toile.reshape(TILE, 2, TILE, 2, 4).astype(np.uint16).sum(axis=(1, 3))
    out = ((s + 2) // 4).astype(np.uint8)
    return out if out[:, :, 3].any() else None


def encoder_png_palette(rgba):
    """PNG a palette de 256 couleurs au plus (MEDIANCUT, sans tramage) ; transparence par index (alpha < 128 : transparent)."""
    rgb = np.ascontiguousarray(rgba[:, :, :3])
    transp = rgba[:, :, 3] < ALPHA_OPAQUE_MIN
    n_transp = int(transp.sum())
    if n_transp == transp.size:
        raise ValueError('tuile entierement transparente')
    couleurs = PNG_COULEURS
    if n_transp:
        rgb = rgb.copy()
        rgb[transp] = np.round(rgb[~transp].mean(axis=0)).astype(np.uint8)   # le fond transparent ne prend pas de couleur propre
        couleurs = PNG_COULEURS - 1
    im = Image.fromarray(rgb).quantize(colors=couleurs, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    if n_transp:
        idx = np.array(im)
        pal = im.getpalette() or []
        n_pal = len(pal) // 3
        t_idx = min(n_pal, PNG_COULEURS - 1)
        idx[transp] = t_idx
        im = Image.fromarray(idx, 'P')
        pal = (pal + [0] * (3 * (t_idx + 1)))[:3 * (t_idx + 1)]
        im.putpalette(pal)
        im.save(buf, format='PNG', transparency=t_idx, optimize=True)
    else:
        im.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def encoder_webp(rgba):
    """WebP q85, methode 6, alpha conserve."""
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, format='WEBP', quality=WEBP_QUALITE, method=WEBP_METHODE)
    return buf.getvalue()


def encoder(rgba, couche):
    return encoder_png_palette(rgba) if couche == 'alt' else encoder_webp(rgba)


def chemin_tuile(dossier, z, x, y, couche):
    return Path(dossier) / str(z) / str(x) / f'{y}.{EXTENSIONS[couche]}'


def ecrire_tuile(dossier, z, x, y, couche, rgba):
    """Ecrit la tuile ; retourne le nombre d'octets."""
    p = chemin_tuile(dossier, z, x, y, couche)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = encoder(rgba, couche)
    p.write_bytes(data)
    return len(data)


def _spill(dossier, z, x, y):
    return Path(dossier) / str(z) / str(x) / f'{y}.npy'


def traiter_bloc(spec, projection, sources, cache):
    """Un bloc de travail. spec : dict
         base_zoom, bx, by (indices du bloc : tuiles x >> 4, y >> 4), niveaux (nombre de zooms a produire en dessous),
         source : 'rendu' (les tuiles `tuiles` du zoom de base sont rendues) ou 'spill' (elles sont lues de `tmp`),
         tuiles : [(tx, ty)], couche, dossier, tmp, ecrire_base (vrai : ecrire aussi les tuiles du zoom de base),
         garder_haut (vrai : garder en tmp la tuile du niveau le plus bas pour la phase suivante).
    Retourne {'ecrites': {zoom: n}, 'octets': n, 'ms_rendu' (reprojection du zoom de base, lecture des images comprise),
    'ms_reduction', 'ms_codage', 'rendues' (tuiles non vides rendues par reprojection ; 0 pour un bloc lu du spill), 'vides'}."""
    import time
    z0, couche = spec['base_zoom'], spec['couche']
    niveaux = spec['niveaux']
    t_rendu = t_red = t_codage = 0.0
    octets, vides, rendues = 0, 0, 0
    ecrites = {}
    courant = {}
    for tx, ty in spec['tuiles']:
        t0 = time.perf_counter()
        if spec['source'] == 'rendu':
            t = rendre_tuile(z0, tx, ty, projection, sources, cache)
        else:
            p = _spill(spec['tmp'], z0, tx, ty)
            t = np.load(p) if p.is_file() else None
        if spec['source'] == 'rendu':
            t_rendu += time.perf_counter() - t0
        else:
            t_red += time.perf_counter() - t0
        if t is None:
            vides += 1
            continue
        if spec['source'] == 'rendu':
            rendues += 1
        courant[(tx, ty)] = t
        if spec['ecrire_base']:
            t0 = time.perf_counter()
            octets += ecrire_tuile(spec['dossier'], z0, tx, ty, couche, t)
            t_codage += time.perf_counter() - t0
            ecrites[z0] = ecrites.get(z0, 0) + 1
    for k in range(1, niveaux + 1):
        z = z0 - k
        parents = {}
        for (x, y), t in courant.items():
            parents.setdefault((x >> 1, y >> 1), {})[(x & 1, y & 1)] = t
        suivant = {}
        t0 = time.perf_counter()
        for (px, py), filles in parents.items():
            r = reduire(filles)
            if r is not None:
                suivant[(px, py)] = r
        t_red += time.perf_counter() - t0
        t0 = time.perf_counter()
        for (px, py), r in suivant.items():
            octets += ecrire_tuile(spec['dossier'], z, px, py, couche, r)
            ecrites[z] = ecrites.get(z, 0) + 1
        t_codage += time.perf_counter() - t0
        courant = suivant
    if spec['garder_haut']:
        z = z0 - niveaux
        for (x, y), t in courant.items():
            p = _spill(spec['tmp'], z, x, y)
            p.parent.mkdir(parents=True, exist_ok=True)
            np.save(p, t)
    return {'ecrites': ecrites, 'octets': octets, 'ms_rendu': t_rendu * 1000, 'ms_reduction': t_red * 1000,
            'ms_codage': t_codage * 1000, 'rendues': rendues, 'vides': vides}


def blocs_de(tuiles, base_zoom, niveaux):
    """Regroupe des tuiles (liste de (tx, ty)) en blocs alignes de 2^niveaux x 2^niveaux : {(bx, by): [(tx, ty)]}."""
    blocs = {}
    for tx, ty in tuiles:
        blocs.setdefault((tx >> niveaux, ty >> niveaux), []).append((int(tx), int(ty)))
    return blocs


def plan_phases(zoom, zoom_min):
    """Phases de la pyramide : liste de (zoom de base, niveaux). La premiere rend le zoom cible ; les suivantes lisent le
    niveau le plus bas de la precedente."""
    phases, z = [], zoom
    while True:
        n = min(BLOC_NIVEAUX, z - zoom_min)
        phases.append((z, n))
        if z - n <= zoom_min:
            break
        z -= n
    return phases


# ------------------------------------------------------------------ les archives

def _cout(taille):
    return 512 + ((taille + 511) // 512) * 512


def _fin(offset):
    """Taille d'une archive qui contient `offset` octets de membres : deux blocs nuls, puis arrondi a 10 240 octets."""
    return -(-(offset + 1024) // tarfile.RECORDSIZE) * tarfile.RECORDSIZE


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def empaqueter(tuiles, prefixe, dossier_sortie, nom, zoom_label, limite=TAILLE_PART_DEFAUT, licence=LICENCE_TUILES):
    """Parts tar `<nom>_z<zoom_label>_<nnn>.tar` de `limite` octets au plus (une tuile n'est jamais coupee), chemins internes
    `<prefixe>/{z}/{x}/{y}.<ext>`, `LICENSE-TILES.txt` dans chaque part. tuiles : liste triee de (z, x, y, chemin).
    Retourne [{'name', 'tiles', 'bytes', 'sha256'}]."""
    dossier_sortie = Path(dossier_sortie)
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    lic = licence.encode('utf-8')
    parts = []
    tf, nom_part, n_tuiles, chemin_part = None, None, 0, None

    def ouvrir():
        nonlocal tf, nom_part, n_tuiles, chemin_part
        nom_part = f'{nom}_z{zoom_label}_{len(parts) + 1:03d}.tar'
        chemin_part = dossier_sortie / nom_part
        tf = tarfile.open(chemin_part, 'w', format=tarfile.USTAR_FORMAT)
        info = tarfile.TarInfo(LICENCE_NOM)
        info.size, info.mode, info.mtime = len(lic), 0o644, 0
        tf.addfile(info, io.BytesIO(lic))
        n_tuiles = 0
        if _fin(tf.offset) > limite:
            raise ValueError(f'limite de {limite} octets trop basse pour une part')

    def fermer():
        nonlocal tf
        tf.close()
        taille = chemin_part.stat().st_size
        if taille > limite:
            raise AssertionError(f'{nom_part} : {taille} octets > {limite}')
        parts.append({'name': nom_part, 'tiles': n_tuiles, 'bytes': taille, 'sha256': sha256_fichier(chemin_part)})
        tf = None

    for z, x, y, chemin in tuiles:
        taille = Path(chemin).stat().st_size
        if tf is not None and _fin(tf.offset + _cout(taille)) > limite:
            fermer()
        if tf is None:
            ouvrir()
            if _fin(tf.offset + _cout(taille)) > limite:
                raise ValueError(f'une tuile de {taille} octets ne tient pas dans {limite} octets')
        info = tarfile.TarInfo(f'{prefixe}/{z}/{x}/{y}{Path(chemin).suffix}')
        info.size, info.mode, info.mtime = taille, 0o644, 0
        with open(chemin, 'rb') as f:
            tf.addfile(info, f)
        n_tuiles += 1
    if tf is not None:
        fermer()
    return parts


def lister_tuiles(dossier, couche):
    """Les tuiles ecrites sous `dossier` : liste triee de (z, x, y, chemin)."""
    ext = '.' + EXTENSIONS[couche]
    out = []
    for p in Path(dossier).glob('*/*/*' + ext):
        try:
            out.append((int(p.parent.parent.name), int(p.parent.name), int(p.stem), p))
        except ValueError:
            continue
    out.sort(key=lambda t: t[:3])
    return out


def ecrire_manifeste(chemin, meta, parts, tuiles_par_zoom):
    """`<nom>.json` : pour chaque part son nom, son nombre de tuiles, ses octets et son sha256 ; par zoom, le nombre de tuiles."""
    doc = dict(meta)
    doc['parts'] = parts
    doc['tiles_per_zoom'] = {str(z): n for z, n in sorted(tuiles_par_zoom.items())}
    doc['tiles_total'] = sum(tuiles_par_zoom.values())
    Path(chemin).write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
    return doc


def ecrire_config(chemin, couche, theatre_cle, min_zoom, max_native_zoom, bounds):
    """`<nom>.config.json` : la description de la couche pour le Route Planner. bounds : (sud, ouest, nord, est)."""
    doc = {'layer': couche, 'theatre': theatre_cle, 'format': EXTENSIONS[couche], 'minZoom': int(min_zoom),
           'maxNativeZoom': int(max_native_zoom), 'bounds': [float(b) for b in bounds], 'tileSize': TILE,
           'attribution': MENTION}
    Path(chemin).write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding='utf-8')
    return doc


# ------------------------------------------------------------------ les sources d'une grille (grille.json + captures.jsonl)

def nom_image(couche, row, col):
    return f'r{row}_c{col}.{EXTENSIONS[couche]}'


def sources_de_grille(grille, captures, passe):
    """Sources d'une passe : une par case dont l'image existe. L'echelle de chaque image est celle de sa ligne de
    captures.jsonl (la mesure de l'execution qui l'a prise), a defaut celle du dernier etalonnage. Retourne (Sources, cles
    absentes) ; la cle d'une source est le chemin de son image."""
    mesure = {(c['row'], c['col']): c.get('mpp') for c in captures}
    etal = [e['mpp'] for e in grille.get('calibrations', []) if e.get('mpp')]
    defaut = etal[-1] if etal else grille['mpp_planned']
    uw, uh = grille['usable_px']
    dxp, dzp = grille['crop_offset_px']
    xc, zc, mpp, cles, absentes = [], [], [], [], []
    for c in grille['cases']:
        p = Path(passe) / nom_image(grille['layer'], c['row'], c['col'])
        if not p.is_file():
            absentes.append((c['row'], c['col']))
            continue
        m = mesure.get((c['row'], c['col'])) or defaut
        xc.append(c['x'] - dxp * m)
        zc.append(c['z'] - dzp * m)
        mpp.append(m)
        cles.append(str(p))
    return Sources(xc, zc, mpp, [uw] * len(cles), [uh] * len(cles), cles), absentes


def lire_captures(passe):
    p = Path(passe) / 'captures.jsonl'
    out = []
    if p.is_file():
        for ligne in p.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(ligne))
            except ValueError:
                continue                       # ligne tronquee par un arret brutal : l'image, elle, est complete
    return out


# ------------------------------------------------------------------ le travail d'un processus de la reprojection

_CTX = {}


def travailler(arg):
    """Point d'entree d'un processus du pool : (ctx_spec, bloc) -> resultat de traiter_bloc. Le contexte (projection, sources
    de la grille, cache d'images) est construit une fois par processus."""
    ctx_spec, bloc = arg
    cle = (ctx_spec['passe'], ctx_spec['theatre'])
    if cle not in _CTX:
        from . import geo
        passe = Path(ctx_spec['passe'])
        grille = json.loads((passe / 'grille.json').read_text(encoding='utf-8'))
        sources, _ = sources_de_grille(grille, lire_captures(passe), passe)
        _CTX.clear()
        _CTX[cle] = (geo.Projection.for_theatre(ctx_spec['theatre']), sources, CacheImages(charger_image_rgb))
    return traiter_bloc(bloc, *_CTX[cle])
