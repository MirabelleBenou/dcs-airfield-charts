"""Panneaux de taxiway : la lecture des panneaux de position (lettre jaune encadree sur fond noir) sur les images de la prise
de vues, par gabarits des glyphes du jeu.

Usage : python -I capture/signcapture.py lire <Theatre> <Base> [...]   (voir signcapture.py)
  lit <work_dir>/panneaux/<Theatre>/<Base>/index.json et les images img/<n>.png des vues `faite` ou `instable` ;
  ecrit lecture.json et lecture.tsv dans le meme dossier, rien d'autre.

Les gabarits se tirent a l'execution de l'atlas du jeu installe (Mods/terrains/<Theatre>/Models/AirfieldsTabs/
AirfieldsTabs.texture.zip : textures abc, abc_12345, abc_6789, et vxy en Syria) ; aucun glyphe, aucune texture du jeu n'est
dans ce depot : tout reste en memoire. Bibliotheques : numpy, OpenCV, Pillow (lecture des DDS).

La lecture d'une image est une fonction pure (read_image) : elle ne depend ni de la taille de l'image ni de la position du
point vise. Methode :
  1. une carte de jaune relatif (teinte jaune, jugee contre la luminance locale : le panneau a contre-jour est olive, pas
     seulement le jaune franc) ; des cadres candidats par correlation d'un gabarit de cadre (anneau clair, bande sombre)
     sur cette carte, a plusieurs echelles ;
  2. pour chaque candidat : correlation (x et y a part, sous-pixel) du coeur de chacun des 35 glyphes avec la carte
     min(R, G) de l'image, puis du glyphe entier (cadre compris) : une lettre de position est jaune sur noir, une lettre de
     direction noire sur jaune, la polarite les separe (une lettre de direction n'est jamais rendue) ;
  3. un caractere n'est rendu que si le score, le departage sur les seuls pixels ou les gabarits different, et l'ecart au
     rival passent des seuils qui durcissent avec la petitesse de la tuile ; sinon `?`, jamais devine ;
  4. une tuile coupee par le bord de l'image est signalee `coupee`, jamais lue ; la fenetre parasite de l'application
     (bloc blanc colle au bord droit) est detectee, son rectangle exclu, la vue signalee.
"""
import io
import json
import math
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

cv2.setNumThreads(2)

# ---------- atlas ----------
ATLAS_REL = ('Models', 'AirfieldsTabs', 'AirfieldsTabs.texture.zip')
TEXTURE_SUFFIX = '.tif.dds'
# (texture, disposition des cadres de la moitie basse, ligne de depart de la recherche des cadres) ; vxy refait V-Z (Syria)
ATLAS_SPEC = (
    ('abc', ('ABCDE', 'FGHIJKL', 'MNOPQRS', 'TUVWXYZ'), 470),
    ('abc_12345', ('ABCDE', 'FGHIJKL', 'MNOPQRS', 'TU12345'), 470),
    ('abc_6789', ('6789',), 0),
    ('vxy', ('VWX', 'YZ'), 0),
)
DIGIT_TEXTURES = ('abc_12345', 'abc_6789')
FRAME_YELLOW_MIN = 100            # jaunisse min(R, G) - B d'un cadre de l'atlas
FRAME_SIDE = (105, 140)           # cote d'un cadre de l'atlas (px) : 124 x 122
GLYPH_LOW, GLYPH_HIGH = 85.0, 215.0   # niveaux min(R, G) du fond et du jaune dans l'atlas
CORE_ROWS, CORE_COLS = (17, 105), (17, 107)   # coeur de la tuile 122 x 124 : 14 a 86 % du cote

# ---------- detection ----------
HUE_LO, HUE_HI = 0.80, 1.12       # G / R d'un jaune de panneau (teinte 49 a 65 degres) ; l'herbe est plus verte
CHROMA_MIN = 22                   # min(R, G) - B
CHROMA_REL = 0.50                 # ... et au moins la moitie de max(R, G) : jaune relatif, pas absolu
LUMA_MIN = 30
CHROMA_FULL = 160.0
RING_SIZES = (8, 10, 12, 14, 17)
RING_SIZES_L0 = (8, 10, 12, 14)
RING_LEVELS = 4
RING_THRESHOLD = 0.30
RING_GUARD = 0.02                 # fenetre sans energie : correlation sans objet
RING_PER_SIZE = 40
MAX_PROPOSALS = 48
FAIR_RIVAL = False
SECOND_GAP = 0.02                 # ecart minimal entre le meilleur glyphe et le suivant
COARSE_MIN = 0.65                 # correlation entiere minimale d'un glyphe pour l'affiner
DOUBT_MIN = 0.85                 # score au-dela duquel une tuile non decidee est quand meme signalee (`?`)
MIN_TILE = 8                      # tuile la plus petite lue (px de haut)
PIX_THR = 8                       # pixel qui differe : ecart de plus de 8/255 sur un canal
FROZEN_FRAC = 1e-4                # moins de 0,01 % des pixels differents : image figee
WINDOW_PROBE = 450                # largeur examinee au bord droit pour la fenetre parasite

# ---------- decision ----------
STATUS_READ, STATUS_FROZEN, STATUS_MISSING = 'lue', 'figee', 'absente'


class ReadError(Exception):
    """Atlas absent ou illisible (le message nomme le chemin)."""


# ---------- les gabarits ----------
def _yellowness(bgr):
    b, g, r = [c.astype(np.int16) for c in cv2.split(bgr)]
    return np.clip(np.minimum(r, g) - b, 0, 255).astype(np.uint8)


def _frame_rows(bgr, top):
    """Les cadres jaunes de l'atlas (composantes connexes ~124 x 122, anneaux), ranges par lignes puis par colonnes."""
    mask = (_yellowness(bgr) > FRAME_YELLOW_MIN).astype(np.uint8)
    mask[:top] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if FRAME_SIDE[0] <= w <= FRAME_SIDE[1] and FRAME_SIDE[0] <= h <= FRAME_SIDE[1] and a < 0.6 * w * h:
            boxes.append((x, y, w, h))
    rows = []
    for t in sorted(boxes, key=lambda t: (t[1], t[0])):
        if rows and abs(rows[-1][0][1] - t[1]) < 30:
            rows[-1].append(t)
        else:
            rows.append([t])
    return [sorted(r) for r in rows]


class Templates:
    """Les 35 glyphes de position (au plus) du theatre : tuile entiere et coeur, normalises (fond 0, jaune 1)."""

    def __init__(self, tiles):
        self.chars = ''.join(sorted(tiles))
        self.tile = {}
        self.core = {}
        for ch, bgr in tiles.items():
            b, g, r = [c.astype(np.float32) for c in cv2.split(bgr)]
            full = np.clip((np.minimum(r, g) - GLYPH_LOW) / (GLYPH_HIGH - GLYPH_LOW), 0, 1).astype(np.float32)
            self.tile[ch] = full
            self.core[ch] = np.ascontiguousarray(full[CORE_ROWS[0]:CORE_ROWS[1], CORE_COLS[0]:CORE_COLS[1]])
        self._cache = {}

    def resized(self, kind, ch, w, h):
        key = (kind, ch, w, h)
        t = self._cache.get(key)
        if t is None:
            src = self.core[ch] if kind == 'core' else self.tile[ch]
            t = cv2.resize(src, (w, h), interpolation=cv2.INTER_AREA)
            if len(self._cache) > 20000:
                self._cache.clear()
            self._cache[key] = t
        return t


def atlas_path(dcs_install, theatre):
    return Path(dcs_install) / 'Mods' / 'terrains' / theatre / Path(*ATLAS_REL)


def load_templates(dcs_install, theatre):
    """Tire les gabarits de l'atlas du jeu installe, en memoire. Un atlas absent ou illisible : ReadError nommant le chemin."""
    path = atlas_path(dcs_install, theatre)
    if not path.is_file():
        raise ReadError(f'atlas introuvable : {path}')
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ReadError(f'atlas illisible : {path} ({e})')
    tiles = {}
    with zf:
        names = set(zf.namelist())
        if 'abc' + TEXTURE_SUFFIX not in names:
            raise ReadError(f'atlas illisible : {path} (texture abc absente)')
        for tex, layout, top in ATLAS_SPEC:
            name = tex + TEXTURE_SUFFIX
            if name not in names:
                continue                  # abc_12345, abc_6789, vxy : selon le theatre
            try:
                rgb = np.array(Image.open(io.BytesIO(zf.read(name))).convert('RGB'))
            except Exception as e:        # Pillow : DDS corrompu, format non pris en charge
                raise ReadError(f'atlas illisible : {path} (texture {tex} : {e})')
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            rows = _frame_rows(bgr, top)
            for ri, chars in enumerate(layout):
                for ci, ch in enumerate(chars):
                    if tex == 'abc_12345' and ch.isalpha():
                        continue          # les lettres de abc_12345 sont celles de abc
                    if ri >= len(rows) or ci >= len(rows[ri]):
                        raise ReadError(f'atlas illisible : {path} (texture {tex} : disposition inattendue, '
                                        f'glyphe {ch} introuvable)')
                    x, y, w, h = rows[ri][ci]
                    tiles[ch] = bgr[y:y + h, x:x + w].copy()
    missing = [c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if c not in tiles]
    if missing:
        raise ReadError(f'atlas illisible : {path} (lettres absentes : {"".join(missing)})')
    return Templates(tiles)


# ---------- cartes ----------
def yellow_map(bgr):
    """Jaune relatif : teinte jaune (G/R proche de 1), chroma min(R,G) - B d'au moins la moitie de max(R,G), pas seuil absolu.
    Retourne (carte douce 0..1, min(R,G) en float32)."""
    b, g, r = [c.astype(np.float32) for c in cv2.split(bgr)]
    mx = np.maximum(r, g)
    mn = np.minimum(r, g)
    chroma = mn - b
    ratio = g / np.maximum(r, 1.0)
    m = (ratio >= HUE_LO) & (ratio <= HUE_HI) & (chroma >= CHROMA_MIN) & (chroma >= CHROMA_REL * mx) & (mx >= LUMA_MIN)
    soft = (m * np.clip(chroma, 0, CHROMA_FULL) / CHROMA_FULL).astype(np.float32)
    return soft, mn


def _ring_template(s):
    """Cadre s x s : anneau clair, bande interieure sombre, centre moyen, marge exterieure sombre de 2 px."""
    t = np.zeros((s + 4, s + 4), np.float32)
    o = 2
    th = max(1, int(round(0.09 * s)))
    t[o:o + s, o:o + s] = 0.35
    t[o:o + th, o:o + s] = 1
    t[o + s - th:o + s, o:o + s] = 1
    t[o:o + s, o:o + th] = 1
    t[o:o + s, o + s - th:o + s] = 1
    b = max(1, int(round(0.12 * s)))
    i0, i1 = o + th, o + s - th
    t[i0:i1, i0:i0 + b] = 0
    t[i0:i1, i1 - b:i1] = 0
    t[i0:i0 + b, i0:i1] = 0
    t[i1 - b:i1, i0:i1] = 0
    return t


_RING = {s: _ring_template(s) for s in RING_SIZES}


def ring_proposals(soft):
    """Cadres candidats : [(score, x, y, cote)] triees, apres fusion des quasi-doublons. Pyramide : les memes gabarits a pleine
    resolution, a la moitie, au quart, au huitieme (cotes de 8 a ~136 px)."""
    props = []
    cur = soft
    for level in range(RING_LEVELS):
        f = 2 ** level
        if min(cur.shape) < 24:
            break
        ii = cv2.integral(cur * cur, sdepth=cv2.CV_64F)       # energie d'une fenetre : somme par image integrale
        for s in (RING_SIZES_L0 if level == 0 else RING_SIZES):
            res = cv2.matchTemplate(cur, _RING[s], cv2.TM_CCOEFF_NORMED)
            w = s + 4
            energy = ii[w:, w:] - ii[:-w, w:] - ii[w:, :-w] + ii[:-w, :-w]
            ys, xs = np.where((res > RING_THRESHOLD) & (energy > RING_GUARD * (s + 4) * (s + 4)))
            if len(ys) == 0:
                continue
            sc = res[ys, xs]
            order = np.argsort(-sc)[:RING_PER_SIZE * 6]
            kept = []
            for i in order:
                y, x = int(ys[i]), int(xs[i])
                if any(abs(y - yy) < s * 0.5 and abs(x - xx) < s * 0.5 for yy, xx in kept):
                    continue
                kept.append((y, x))
                props.append((float(sc[i]), (x + 2) * f, (y + 2) * f, s * f))
                if len(kept) >= RING_PER_SIZE:
                    break
        cur = cv2.resize(cur, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    props.sort(reverse=True)
    out = []
    for p in props:
        cx, cy = p[1] + p[3] / 2, p[2] + p[3] / 2
        if any(abs(cx - (q[1] + q[3] / 2)) < 0.35 * max(p[3], q[3]) and abs(cy - (q[2] + q[3] / 2)) < 0.35 * max(p[3], q[3])
               and 0.7 < p[3] / q[3] < 1.43 for q in out):
            continue
        out.append(p)
    return out


# ---------- reconnaissance ----------
def _ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-6 else 0.0


def _render(src, x0, y0, tw, th, X0, Y0, X1, Y1, blur=0.45):
    """Rend le glyphe src (flou a l'echelle) dans la fenetre [X0:X1, Y0:Y1] de l'image, coin haut-gauche flottant (x0, y0),
    taille flottante (tw, th) : le sous-pixel."""
    h, w = src.shape
    sx, sy = tw / w, th / h
    sig = max(0.0, blur / min(sx, sy))
    s = cv2.GaussianBlur(src, (0, 0), sig) if sig > 0.3 else src
    mat = np.array([[sx, 0, x0 - X0 + 0.5 * sx - 0.5], [0, sy, y0 - Y0 + 0.5 * sy - 0.5]], np.float32)
    return cv2.warpAffine(s, mat, (X1 - X0, Y1 - Y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


COARSE_FACTORS = (0.8, 0.9, 1.0, 1.1, 1.25)


def _coarse(m, tpl, cx, cy, s):
    """Pour chaque glyphe, la meilleure correlation entiere de son coeur, a plusieurs echelles autour d'une tuile de cote s
    centree en (cx, cy) : {car: (score, x, y, w, h)}."""
    H, W = m.shape
    core0 = 0.72 * s
    pad = max(2, int(round(0.2 * s)))
    best = {}
    for f in COARSE_FACTORS:
        cw = max(5, int(round(core0 * f)))
        x0, y0 = int(round(cx - cw / 2 - pad)), int(round(cy - cw / 2 - pad))
        xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + cw + 2 * pad), min(H, y0 + cw + 2 * pad)
        roi = m[ya:yb, xa:xb]
        if roi.shape[0] < cw or roi.shape[1] < cw:
            continue
        for ch in tpl.chars:
            r = cv2.matchTemplate(roi, tpl.resized('core', ch, cw, cw), cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            if ch not in best or mx > best[ch][0]:
                best[ch] = (float(mx), xa + loc[0], ya + loc[1], cw, cw)
    return best


def _refine(m, src, box, scales=(0.92, 1.0, 1.08), step=0.5, rng=0.5, max_diff=0.09):
    """Raffine (score, boite flottante, rendu, fenetre) du glyphe src autour d'une boite entiere (x, y, w, h)."""
    H, W = m.shape
    x, y, w, h = box
    best = (-2.0, None, None, None)
    offs = np.arange(-rng, rng + 1e-6, step)
    for sx in scales:
        for sy in scales:
            if abs(sx - sy) > max_diff:
                continue
            tw, th = w * sx, h * sy
            for dx in offs:
                for dy in offs:
                    x0, y0 = x + dx - (tw - w) / 2, y + dy - (th - h) / 2
                    X0, Y0, X1, Y1 = int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x0 + tw)), int(math.ceil(y0 + th))
                    if X0 < 0 or Y0 < 0 or X1 > W or Y1 > H or X1 - X0 < 3 or Y1 - Y0 < 3:
                        continue
                    t = _render(src, x0, y0, tw, th, X0, Y0, X1, Y1)
                    sc = _ncc(m[Y0:Y1, X0:X1], t)
                    if sc > best[0]:
                        best = (sc, (x0, y0, tw, th), t, (X0, Y0, X1, Y1))
    return best


def _refine_full(m, src, tile, scales=(0.88, 1.0, 1.12), offs=(-1.0, 0.0, 1.0)):
    """Correlation du glyphe entier (cadre compris) autour de la tuile deduite du coeur : (score, boite)."""
    H, W = m.shape
    x, y, w, h = tile
    best = (-2.0, None)
    for sx in scales:
        for sy in scales:
            if abs(sx - sy) > 0.13:
                continue
            tw, th = w * sx, h * sy
            for dx in offs:
                for dy in offs:
                    x0, y0 = x + dx - (tw - w) / 2, y + dy - (th - h) / 2
                    X0, Y0 = int(math.floor(x0)) - 1, int(math.floor(y0)) - 1
                    X1, Y1 = int(math.ceil(x0 + tw)) + 1, int(math.ceil(y0 + th)) + 1
                    if X0 < 0 or Y0 < 0 or X1 > W or Y1 > H or X1 - X0 < 5 or Y1 - Y0 < 5:
                        continue
                    t = _render(src, x0, y0, tw, th, X0, Y0, X1, Y1)
                    sc = _ncc(m[Y0:Y1, X0:X1], t)
                    if sc > best[0]:
                        best = (sc, (x0, y0, tw, th))
    return best


def _tile_of_core(box):
    """La tuile entiere deduite d'un coeur aligne (le coeur fait 72 % du cote, au centre)."""
    x, y, w, h = box
    return (x + w / 2 - w / 0.72 / 2, y + h / 2 - h / 0.72 / 2, w / 0.72, h / 0.72)


def _discriminate(m, tpl, win, box, first, rival, box_rival=None):
    """Le departage : correlation de Pearson, sur les seuls pixels ou les deux gabarits different, de l'image avec chacun,
    au meme alignement. Retourne (p_premier, p_rival) ou None si les gabarits ne different presque nulle part."""
    X0, Y0, X1, Y1 = win
    x0, y0, tw, th = box
    t1 = _render(tpl.core[first], x0, y0, tw, th, X0, Y0, X1, Y1)
    t2 = _render(tpl.core[rival], x0, y0, tw, th, X0, Y0, X1, Y1)
    diff = np.abs(t1 - t2) > 0.30
    if int(diff.sum()) < 4:
        return None
    obs = m[Y0:Y1, X0:X1]
    pr = _ncc(obs[diff], t2[diff])
    if box_rival is not None:
        # le rival est aussi essaye a son propre alignement : l'equite entre deux glyphes proches
        t3 = _render(tpl.core[rival], *box_rival, X0, Y0, X1, Y1)
        pr = max(pr, _ncc(obs[diff], t3[diff]))
    return _ncc(obs[diff], t1[diff]), pr


def recognize(m, tpl, cx, cy, s, rivals=3):
    """Lit une tuile de cote ~s centree en (cx, cy) : dict (caractere, score, departage, ecart, boite du coeur, score du glyphe
    entier) ou None. Le caractere n'est pas encore decide (voir decide)."""
    co = _coarse(m, tpl, cx, cy, s)
    if not co:
        return None
    rank = sorted(co.items(), key=lambda kv: -kv[1][0])[:rivals]
    if rank[0][1][0] < COARSE_MIN:
        return None                       # rien qui ressemble a un glyphe : on n'affine pas
    refined = []
    for ch, (sc0, x, y, w, h) in rank:
        sc, box, t, win = _refine(m, tpl.core[ch], (x, y, w, h))
        if box is not None:
            refined.append((sc, ch, box, win))
    if not refined:
        return None
    refined.sort(key=lambda t: -t[0])
    s1, ch1, box1, win1 = refined[0]
    full, tile = _refine_full(m, tpl.tile[ch1], _tile_of_core(box1))
    p1, pr = 1.0, -1.0
    for s2, ch2, box2, _ in refined[1:]:
        d = _discriminate(m, tpl, win1, box1, ch1, ch2, box2 if FAIR_RIVAL else None)
        if d is None:
            continue
        p1, pr = min(p1, d[0]), max(pr, d[1])
    if pr < -0.99:
        p1, pr = (_ncc(m[win1[1]:win1[3], win1[0]:win1[2]], _render(tpl.core[ch1], *box1, *win1)), 0.0)
    second = refined[1][0] if len(refined) > 1 else 0.0
    return {'car': ch1, 'score': s1, 'second': second, 'depart': p1, 'ecart': p1 - pr, 'core': box1,
            'full': full, 'tile': tile if tile is not None else _tile_of_core(box1)}


def decide(r):
    """Le caractere rendu, ou '?' : jamais devine. Les seuils durcissent avec la petitesse de la tuile."""
    if r is None:
        return '?'
    h = r['tile'][3]
    if h >= 20:
        smin, fmin, dmin, gmin = 0.80, 0.60, 0.40, 0.30
    elif h >= 12:
        smin, fmin, dmin, gmin = 0.84, 0.64, 0.45, 0.35
    else:
        smin, fmin, dmin, gmin = 0.88, 0.68, 0.50, 0.40
    ok = r['score'] >= smin and r['full'] >= fmin and r['depart'] >= dmin and r['ecart'] >= gmin
    ok = ok and r['score'] - r['second'] >= SECOND_GAP      # deux glyphes a egalite (B/8) : on ne tranche pas
    return r['car'] if ok else '?'


# ---------- fenetre parasite ----------
def find_window(bgr):
    """Fenetre d'application posee devant le jeu : bloc de blanc pur (255) colle au bord droit, bord gauche rectiligne.
    Retourne [x, y, w, h] ou None."""
    H, W = bgr.shape[:2]
    probe = min(WINDOW_PROBE, W)
    m = bgr.min(axis=2) >= 250
    zone = m[:, W - probe:]
    runs = np.zeros(zone.shape[1], int)
    start = np.zeros(zone.shape[1], int)
    for j in range(zone.shape[1]):
        col = np.concatenate([[0], zone[:, j].astype(np.int8), [0]])
        d = np.diff(col)
        st, en = np.where(d == 1)[0], np.where(d == -1)[0]
        if len(st):
            k = int(np.argmax(en - st))
            runs[j] = en[k] - st[k]
            start[j] = st[k]
    cols = np.where(runs >= 80)[0]
    if len(cols) < 15:
        return None
    j0 = int(cols.min())
    x0 = W - probe + j0
    y0, y1 = int(start[j0]), int(start[j0] + runs[j0])
    if x0 < 3:
        return None
    left = m[y0:y1, x0 - 3].mean()
    if left > 0.3 or (W - x0) - len(cols) > 0.5 * (W - x0):
        return None
    return [int(x0), int(y0), int(W - x0), int(max(runs[j] + start[j] for j in cols) - y0)]


# ---------- panneaux d'attente (rouges) ----------
def red_panels(bgr, hmin=4):
    """Les panneaux d'attente (blanc sur rouge) : boites [x, y, w, h] des blocs rouges allonges. Deux fermetures : fine (petits
    panneaux) et large (grands panneaux, dont les inscriptions blanches coupent le rouge)."""
    b, g, r = [c.astype(np.int16) for c in cv2.split(bgr)]
    gb = np.maximum(g, b)
    raw = ((r - gb >= 60) & (r >= 90) & (r * 100 >= 180 * np.maximum(gb, 1))).astype(np.uint8)
    out = []
    for k, hlow in ((3, hmin), (9, 12)):
        mask = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        mask = np.zeros_like(mask)
        cv2.drawContours(mask, cs, -1, 1, cv2.FILLED)      # le blanc des inscriptions ne troue pas le panneau
        n, lab, st, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in st[i])
            if h < hlow or w < 1.4 * h or w > 800 or a < 0.45 * w * h:
                continue
            if any(x >= ox - 2 and y >= oy - 2 and x + w <= ox + ow + 2 and y + h <= oy + oh + 2 for ox, oy, ow, oh in out):
                continue
            out = [o for o in out if not (o[0] >= x - 2 and o[1] >= y - 2 and o[0] + o[2] <= x + w + 2 and o[1] + o[3] <= y + h + 2)]
            out.append([x, y, w, h])
    return out


def _attached(tile, panels):
    """Un panneau d'attente accole : meme rangee, bord a bord (a 0,6 de la hauteur au plus)."""
    x, y, w, h = tile
    for px, py, pw, ph in panels:
        if abs((y + h / 2) - (py + ph / 2)) < 0.6 * max(h, ph) and min(abs(x - (px + pw)), abs(px - (x + w))) <= 0.6 * max(h, ph):
            return True
    return False


def _cut_beside_panels(soft, panels, tiles, W):
    """Une tuile coupee par le bord gauche ou droit de l'image, collee a un panneau d'attente : le cadre jaune commence contre
    le panneau et sort de l'image. Signalee `coupee`, jamais lue."""
    out = []
    for px, py, pw, ph in panels:
        if ph < 8:
            continue
        for side in ('d', 'g'):
            if side == 'd':
                x0, x1 = px + pw, W
                near = W - x0 <= 1.2 * ph and px + pw < W - 1
            else:
                x0, x1 = 0, px
                near = px <= 1.2 * ph and px > 1
            if not near or x1 - x0 < 3:
                continue
            strip = soft[py:py + ph, x0:x1]
            if float((strip > 0.5).mean()) < 0.10:
                continue
            if any(abs(t['y'] + t['h'] / 2 - (py + ph / 2)) < ph and t['x'] < x1 and t['x'] + t['w'] > x0 for t in tiles):
                continue
            out.append({'x': int(x0), 'y': int(py), 'w': int(x1 - x0), 'h': int(ph), 'car': '?', 'score': 0.0, 'depart': 0.0,
                        'ecart': 0.0, 'attente': True, 'coupee': True})
    return out


# ---------- lecture d'une image ----------
def read_image(bgr, tpl, timings=None, debug=None):
    """Lit une image (BGR, n'importe quelle taille). Retourne {'fenetre': [x, y, w, h] | None, 'tuiles': [...]} ; par tuile :
    x, y, w, h (boite de la tuile entiere dans l'image), car (lettre ou `?`), score, depart, ecart, attente (panneau d'attente
    accole), coupee (tuile coupee par le bord de l'image : jamais lue)."""
    H, W = bgr.shape[:2]
    t0 = time.perf_counter()
    window = find_window(bgr)
    soft, m = yellow_map(bgr)
    if window:
        soft[:, window[0]:] = 0           # ce qui tombe dans la fenetre parasite est ecarte
    t1 = time.perf_counter()
    props = ring_proposals(soft)[:MAX_PROPOSALS]
    t2 = time.perf_counter()
    cands = []
    for score, x, y, s in props:
        if s < MIN_TILE:
            continue
        r = recognize(m, tpl, x + s / 2, y + s / 2, s)
        if r is not None:
            r['ring'] = score
            cands.append(r)
    t3 = time.perf_counter()
    cands.sort(key=lambda r: -(r['full'] + r['score']))
    kept = []
    for r in cands:
        tx, ty, tw, th = r['tile']
        if any(abs((tx + tw / 2) - (k['tile'][0] + k['tile'][2] / 2)) < 0.5 * max(tw, k['tile'][2])
               and abs((ty + th / 2) - (k['tile'][1] + k['tile'][3] / 2)) < 0.5 * max(th, k['tile'][3]) for k in kept):
            continue
        kept.append(r)
    if debug is not None:
        debug.extend(kept)
    panels = red_panels(bgr)
    tiles = []
    for r in kept:
        tx, ty, tw, th = r['tile']
        box = [int(round(tx)), int(round(ty)), int(round(tw)), int(round(th))]
        car = decide(r)
        cut = box[0] <= 1 or box[1] <= 1 or box[0] + box[2] >= W - 1 or box[1] + box[3] >= H - 1
        if cut and r['score'] < DOUBT_MIN:
            continue
        if car == '?' and r['score'] < DOUBT_MIN:
            continue                      # rien qui ressemble a une lettre : pas de tuile
        tiles.append({'x': box[0], 'y': box[1], 'w': box[2], 'h': box[3], 'car': '?' if cut else car,
                      'score': round(r['score'], 3), 'depart': round(r['depart'], 2), 'ecart': round(r['ecart'], 2),
                      'attente': _attached(box, panels), 'coupee': bool(cut)})
    tiles.extend(_cut_beside_panels(soft, panels, tiles, W))
    if timings is not None:
        timings.update({'cartes': t1 - t0, 'cadres': t2 - t1, 'lecture': t3 - t2, 'total': time.perf_counter() - t0})
    return {'fenetre': window, 'tuiles': tiles}


# ---------- image figee ----------
def frozen(prev, cur):
    """Vrai si moins de FROZEN_FRAC des pixels different (plus de PIX_THR/255 sur un canal) de l'image precedente."""
    if prev is None or prev.shape != cur.shape:
        return False
    d = cv2.absdiff(prev, cur).max(axis=2)
    return float((d > PIX_THR).mean()) < FROZEN_FRAC


# ---------- commande lire ----------
TSV_HEAD = ('base', 'vue', 'x', 'y', 'w', 'h', 'car', 'score', 'depart', 'ecart', 'attente', 'coupee')


def read_base(images_dir, index, tpl, base='', log=print):
    """Lit les vues `faite` ou `instable` d'un index : liste de dicts par vue (numero, image, fenetre, tuiles)."""
    views, prev = [], None
    for e in index['vues']:
        if e.get('statut') not in ('faite', 'instable'):
            continue
        n = e['numero']
        path = Path(images_dir) / (e.get('img') or f'img/{n:03d}.png')
        img = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.is_file() else None
        if img is None:
            views.append({'vue': n, 'image': 'absente', 'fenetre': None, 'tuiles': []})
            log(f'{base} vue {n:03d} : absente')
            continue
        t0 = time.perf_counter()
        if frozen(prev, img):
            views.append({'vue': n, 'image': 'figee', 'fenetre': None, 'tuiles': []})
            log(f'{base} vue {n:03d} : figee')
        else:
            r = read_image(img, tpl)
            views.append({'vue': n, 'image': 'lue', 'fenetre': r['fenetre'], 'tuiles': r['tuiles']})
            lettres = ''.join(t['car'] for t in r['tuiles'])
            log(f'{base} vue {n:03d} : {len(r["tuiles"])} tuile(s) {lettres}'
                f'{" fenetre parasite" if r["fenetre"] else ""} ({time.perf_counter() - t0:.2f} s)')
        prev = img
    return views


def write_lecture(out_dir, base, theatre, views):
    """Ecrit lecture.json et lecture.tsv dans out_dir (rien d'autre)."""
    out = Path(out_dir)
    doc = {'theatre': theatre, 'base': base,
           'vues': [{'vue': v['vue'], 'image': v['image'], 'fenetre_parasite': bool(v['fenetre']), 'fenetre': v['fenetre'],
                     'tuiles': v['tuiles']} for v in views]}
    (out / 'lecture.json').write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    lines = ['\t'.join(TSV_HEAD)]
    for v in views:
        for t in v['tuiles']:
            lines.append('\t'.join(str(x) for x in (base, v['vue'], t['x'], t['y'], t['w'], t['h'], t['car'], t['score'],
                                                     t['depart'], t['ecart'], 'oui' if t['attente'] else 'non',
                                                     'oui' if t['coupee'] else 'non')))
    (out / 'lecture.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def cmd_lire(cfg, theatre, bases, panneaux_dir, find_base, log=print):
    """lire <Theatre> <Base> [...] : lecture des images d'une prise de vues (index.json) ; ecrit lecture.json et lecture.tsv."""
    tpl = load_templates(cfg.dcs_install, theatre)
    for b in bases:
        name, _ = find_base(cfg, theatre, b)
        d = panneaux_dir(cfg, theatre) / name
        ipath = d / 'index.json'
        if not ipath.is_file():
            raise ReadError(f'index.json introuvable : {ipath}')
        index = json.loads(ipath.read_text(encoding='utf-8'))
        t0 = time.perf_counter()
        views = read_base(d, index, tpl, name, log)
        write_lecture(d, name, theatre, views)
        nt = sum(len(v['tuiles']) for v in views)
        log(f'lire {theatre}/{name} : {len(views)} vue(s), {nt} tuile(s) en {time.perf_counter() - t0:.1f} s')
