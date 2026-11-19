"""Aides des tests des tuiles (render/tiles.py, capture/f10tiles.py) : un monde F10 synthetique, un faux DCS (crochet et ecran
simules) et des passes de captures construites. Aucune image de DCS ; les tests de ce fichier verifient les aides elles-memes."""
import json
import math
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / 'capture'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from render import f10import                                    # noqa: E402  (f10capture sous Linux)

f10capture = f10import.load()
import f10tiles as ft                                          # noqa: E402
from render import geo                                          # noqa: E402
from render import tiles as T                                   # noqa: E402

ECRAN_PETIT = (370, 300)           # utile (280, 264) apres les marges de f10capture : des images de quelques Ko
ECRAN_MOYEN = (600, 400)           # utile (510, 364) : assez grand pour que l'etalonnage mesure l'echelle a 0,3 % pres
ECRAN_REEL = (3440, 1440)


# ------------------------------------------------------------------ le monde

def _hash(ix, iz, graine):
    h = (ix.astype(np.int64) * 73856093) ^ (iz.astype(np.int64) * 19349663) ^ (graine * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    return (h ^ (h >> 16)) & 0x7FFFFFFF


_PALETTE_ALT = np.array([[220, 40, 40], [40, 160, 60], [50, 80, 220], [230, 190, 30], [150, 40, 190], [30, 190, 200],
                         [240, 120, 30], [90, 200, 40], [200, 60, 150], [60, 110, 160]], np.uint8)       # saturees
_PALETTE_MAP = np.array([[200, 196, 180], [170, 168, 160], [215, 210, 195], [150, 150, 145], [190, 186, 170]], np.uint8)


class Monde:
    """Couleur du sol en tout point (x, z) (metres, repere DCS). Trois formes :
    'alt'  : cases de couleurs franches (tres peu de contours : aplat eleve, saturation elevee) ;
    'map'  : les memes cases, desaturees ;
    'lisse': une fonction lisse de longueur d'onde ~80 m, pour mesurer une geometrie."""

    def __init__(self, forme='alt', graine=1):
        self.forme, self.graine = forme, graine

    def couleur(self, x, z):
        x, z = np.asarray(x, float), np.asarray(z, float)
        if self.forme == 'lisse':
            r = 127 + 100 * np.sin(2 * np.pi * x / 83.0 + 0.3) * np.cos(2 * np.pi * z / 97.0)
            g = 127 + 100 * np.sin(2 * np.pi * (x + 2 * z) / 131.0)
            b = 127 + 100 * np.cos(2 * np.pi * (3 * x - z) / 151.0 + 1.0)
            return np.clip(np.stack(np.broadcast_arrays(r, g, b), axis=-1), 0, 255).astype(np.uint8)
        if self.forme == 'bruit':
            return self._bruit(x, z)
        pal = _PALETTE_ALT if self.forme == 'alt' else _PALETTE_MAP
        out = None
        for echelle, part in ((2400.0, 0), (350.0, 1), (60.0, 2)):
            h = _hash(np.floor(x / echelle), np.floor(z / echelle), self.graine + part)
            c = pal[h % len(pal)]
            if out is None:
                out = c
            else:
                garde = (_hash(np.floor(x / echelle), np.floor(z / echelle), 977 + part) % 100) < 35
                out = np.where(garde[..., None], c, out)
        return out

    def _bruit(self, x, z):
        """Bruit de valeurs lisse (interpolation lissee de reseaux aleatoires de 40 a 900 m) : du relief a toutes les echelles,
        sans periodicite, comme une carte antialiasee (l'etalonnage mesure des decalages par correlation de phase)."""
        x, z = np.broadcast_arrays(x, z)
        out = np.zeros(x.shape + (3,), float)
        for canal in range(3):
            tot = 0.0
            for k, (echelle, poids) in enumerate(((40.0, 0.25), (110.0, 0.3), (300.0, 0.3), (900.0, 0.4))):
                u, v = x / echelle, z / echelle
                iu, iv = np.floor(u), np.floor(v)
                fu, fv = u - iu, v - iv
                fu, fv = fu * fu * (3 - 2 * fu), fv * fv * (3 - 2 * fv)
                g = self.graine * 7 + canal * 31 + k * 101

                def val(a, b):
                    return (_hash(iu + a, iv + b, g) % 10007) / 10006.0
                tot = tot + poids * ((1 - fu) * ((1 - fv) * val(0, 0) + fv * val(0, 1)) + fu * ((1 - fv) * val(1, 0) + fv * val(1, 1)))
            out[..., canal] = tot / 1.25
        return np.clip(out * 255, 0, 255).astype(np.uint8)

    def image(self, xc, zc, mpp, w, h):
        """Image (h, w, 3) uint8 centree en (xc, zc) a l'echelle mpp."""
        xs = xc - (np.arange(h) + 0.5 - h / 2.0) * mpp
        zs = zc + (np.arange(w) + 0.5 - w / 2.0) * mpp
        return self.couleur(xs[:, None], zs[None, :])


# ------------------------------------------------------------------ la configuration

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """f10capture initialise sur un work_dir neuf, avec un ecran de `ECRAN_PETIT` ; retourne (Config, work_dir)."""
    def make(ecran=ECRAN_PETIT):
        dcs = tmp_path / 'dcs' / 'Mods' / 'terrains'
        dcs.mkdir(parents=True, exist_ok=True)
        w = tmp_path / 'work'
        path = tmp_path / 'config.json'
        path.write_text(json.dumps({'work_dir': str(w), 'dcs_install': str(tmp_path / 'dcs'), 'screen': list(ecran)}),
                        encoding='utf-8')
        monkeypatch.setattr(f10capture, '_screen', None)
        f10capture.init(['x', '--config', str(path)])
        return f10capture.CFG, w
    return make


def args(commande, theatre, couche='alt', zoom=15, rectangle=None, emprise=None, nom=None, **plus):
    """Les arguments de la ligne de commande, comme l'analyseur de f10tiles les rend."""
    argv = ['f10tiles.py', commande, theatre, '--couche', couche, '--zoom', str(zoom)]
    argv += ['--rectangle', rectangle] if rectangle else ['--emprise', str(emprise)]
    if nom:
        argv += ['--nom', nom]
    for k, v in plus.items():
        argv += ['--' + k.replace('_', '-'), str(v)]
    return argv


# ------------------------------------------------------------------ une passe construite

def ecrire_passe(argv, monde, facteur_echelle=1.0, ecart_m=(0.0, 0.0)):
    """Plan, puis une image par case tiree du monde, comme si DCS l'avait capturee (a l'echelle vraie
    planifiee * facteur_echelle, mesuree sans erreur). Retourne (Zone, grille)."""
    a = ft._parser().parse_args(ft._recoller_rectangle(argv[1:]))
    z = ft.Zone(a)
    g = ft.assurer_grille(z)
    mpp = g['mpp_planned'] * facteur_echelle
    uw, uh = g['usable_px']
    dxp, dzp = g['crop_offset_px']
    lignes = []
    for c in g['cases']:
        xc, zc = c['x'] - dxp * mpp + ecart_m[0], c['z'] - dzp * mpp + ecart_m[1]
        img = monde.image(xc, zc, mpp, uw, uh)
        ft._ecrire_image(img, ft._nom_image(z, c), z.couche)
        lignes.append(json.dumps({'row': c['row'], 'col': c['col'], 'mpp': mpp}))
    (z.dossier / 'captures.jsonl').write_text('\n'.join(lignes) + '\n', encoding='utf-8')
    g['calibrations'].append({'mpp': mpp})
    ft._ecrire_json(z.dossier / 'grille.json', g)
    return z, g


# ------------------------------------------------------------------ le faux DCS

class FauxDcs:
    """Crochet et ecran simules : la camera F10 plate au-dessus du monde, a l'echelle de la loi mesuree (facteur pres). Le
    crochet rend la position commandee ; `grab` rend l'ecran complet de la camera. Compte les prises de vue."""

    def __init__(self, monde, ecran, facteur_echelle=1.0, bruit_cols=0):
        """bruit_cols : nombre de colonnes (a gauche) ou un bruit par pixel est ajoute (la texture du Sat)."""
        self.monde, self.ecran, self.facteur, self.bruit_cols = monde, ecran, facteur_echelle, bruit_cols
        self.cam = (0.0, 0.0, 1500.0)
        self.prises = []                           # positions de camera commandees, dans l'ordre
        self.liberations = 0
        self.panne = None                          # fonction(n_prises) -> exception a lever, ou None

    def hook_xz(self, x, z, alt):
        n = len(self.prises) + 1
        if self.panne is not None:
            e = self.panne(n)
            if e is not None:
                raise e
        self.cam = (x, z, alt)
        self.prises.append((round(x, 3), round(z, 3), round(alt, 1)))
        f10capture._last_send = f10capture.time.perf_counter()
        return x, z, alt

    def hook_release(self):
        self.liberations += 1

    def grab(self):
        w, h = self.ecran
        x, z, alt = self.cam
        mpp = T.mpp_camera(alt) * self.facteur
        img = self.monde.image(x, z, mpp, w, h)
        if self.bruit_cols:
            rng = np.random.default_rng(5)
            n = min(self.bruit_cols, w)
            img = img.copy()
            img[:, :n] = np.clip(img[:, :n].astype(np.int16) + rng.integers(-40, 40, img[:, :n].shape), 0, 255).astype(np.uint8)
        return img

    def installer(self, monkeypatch):
        monkeypatch.setattr(f10capture, 'hook_xz', self.hook_xz)
        monkeypatch.setattr(f10capture, 'hook_release', self.hook_release)
        monkeypatch.setattr(f10capture, 'grab', self.grab)
        monkeypatch.setattr(f10capture, 'dcs_in_front', lambda: True)
        monkeypatch.setattr(f10capture, 'time',
                            types.SimpleNamespace(perf_counter=f10capture.time.perf_counter, sleep=lambda s: None))
        monkeypatch.setattr(f10capture, '_last', None)
        monkeypatch.setattr(f10capture, '_last_send', 0.0)
        return self


# ------------------------------------------------------------------ les aides se verifient

def test_monde_est_deterministe_et_reconnaissable():
    m = Monde('alt')
    a = m.image(1000.0, 2000.0, 3.0, 400, 300)
    assert (a == m.image(1000.0, 2000.0, 3.0, 400, 300)).all()
    assert (a != m.image(1000.0, 2400.0, 3.0, 400, 300)).any()
    _, sat = T.mesurer_centre(a)
    assert sat > 80
    carte = Monde('map').image(1000.0, 2000.0, 3.0, 400, 300)
    assert T.mesurer_centre(carte)[1] < 75


def test_monde_lisse_et_image_echantillonnent_le_meme_point():
    m = Monde('lisse')
    img = m.image(5000.0, -3000.0, 2.0, 100, 80)
    # pixel (r, c) = x = xc - (r + .5 - h/2) mpp ; z = zc + (c + .5 - w/2) mpp
    assert (img[10, 20] == m.couleur(5000.0 - (10 + 0.5 - 40) * 2.0, -3000.0 + (20 + 0.5 - 50) * 2.0)).all()


def test_faux_dcs_rend_la_camera(cfg, monkeypatch):
    cfg()
    d = FauxDcs(Monde('lisse'), ECRAN_PETIT).installer(monkeypatch)
    d.hook_xz(10000.0, 20000.0, 2000.0)
    assert d.grab().shape == (300, 370, 3)
    img, delai, n, ok, cmd = f10capture.move_and_stabilize(10200.0, 20000.0, 2000.0)
    assert ok and cmd == (10200.0, 20000.0, 2000.0)
