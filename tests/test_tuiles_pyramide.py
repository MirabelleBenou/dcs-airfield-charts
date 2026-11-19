"""Tuiles XYZ : la reprojection (geometrie mesuree sur un monde synthetique), la pyramide (TU5), le codage et les archives (TU6),
`tuiler` de bout en bout sur une petite passe construite. Aucun DCS, aucune image de DCS."""
import io
import json
import math
import tarfile
from pathlib import Path

import numpy as np
import pytest
import shapely
from PIL import Image

from test_tuiles_monde import FauxDcs, Monde, args, cfg, ecrire_passe, ft, T, geo   # noqa: F401  (cfg : fixture)

RECT = '35.000,36.000,35.045,36.060'          # ~ 5 x 5,4 km : 49 cases au zoom 15 sur l'ecran de test


@pytest.fixture
def passe_lisse(cfg):
    """Une passe Syria, zoom 15, alt, rectangle RECT, images tirees du monde lisse. Retourne (Zone, grille, monde, proj)."""
    cfg()
    monde = Monde('lisse')
    z, g = ecrire_passe(args('capture', 'Syria', zoom=15, rectangle=RECT), monde)
    return z, g, monde, geo.Projection.for_theatre('Syria')


@pytest.fixture
def passe(cfg):
    """Comme passe_lisse, avec des cases de couleurs franches (comme l'Alt : le codage a palette est alors rapide)."""
    cfg()
    z, g = ecrire_passe(args('capture', 'Syria', zoom=15, rectangle=RECT), Monde('alt'))
    return z, g, Monde('alt'), geo.Projection.for_theatre('Syria')


def _contexte(z, g, proj):
    captures = T.lire_captures(z.dossier)
    sources, absentes = T.sources_de_grille(g, captures, z.dossier)
    assert not absentes
    return sources, T.CacheImages(T.charger_image_rgb)


# ------------------------------------------------------------------ la reprojection

def test_la_passe_de_test_a_au_moins_30_cases(passe_lisse):
    z, g, monde, proj = passe_lisse
    assert len(g['cases']) >= 30


@pytest.mark.parametrize('facteur', [1.0, 1.004])
def test_reprojection_retrouve_le_monde(passe_lisse, cfg, facteur):
    """Chaque pixel de tuile reprend la couleur du monde au point qu'il designe (a l'erreur d'echantillonnage pres), y compris
    quand l'echelle mesuree s'ecarte de la prevue."""
    cfg()
    monde = Monde('lisse')
    z, g = ecrire_passe(args('capture', 'Syria', zoom=15, rectangle=RECT, nom=f'f{int(facteur * 1000)}'), monde,
                        facteur_echelle=facteur)
    proj = geo.Projection.for_theatre('Syria')
    sources, cache = _contexte(z, g, proj)
    tuiles = T.tuiles_de_emprise(z.emprise, 15)
    assert len(tuiles) >= 20
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    verifie = 0
    for tx, ty in tuiles[::7]:
        tx, ty = int(tx), int(ty)
        t = T.rendre_tuile(15, tx, ty, proj, sources, cache)
        assert t is not None
        la, lo = T.latlon_de_monde_px(tx * 256 + j + 0.5, ty * 256 + i + 0.5, 15)
        X, Z = proj.to_xz(la, lo)
        ref = monde.couleur(X, Z).astype(int)
        opaque = t[:, :, 3] == 255
        assert opaque.mean() > 0.2
        ecart = np.abs(t[:, :, :3].astype(int) - ref)[opaque]
        assert ecart.max() <= 5, f'tuile {tx}/{ty} : ecart max {ecart.max()}'
        verifie += 1
    assert verifie >= 3


def test_une_erreur_d_echelle_non_comptee_se_voit(passe_lisse, cfg):
    """Contre-epreuve : si la reprojection ignorait l'echelle mesuree (images a +0,4 %), le monde ne serait plus retrouve."""
    cfg()
    monde = Monde('lisse')
    z, g = ecrire_passe(args('capture', 'Syria', zoom=15, rectangle=RECT, nom='ce'), monde, facteur_echelle=1.004)
    (z.dossier / 'captures.jsonl').write_text('', encoding='utf-8')
    g['calibrations'] = []
    proj = geo.Projection.for_theatre('Syria')
    sources, cache = _contexte(z, g, proj)
    tx, ty = (int(v) for v in T.tuiles_de_emprise(z.emprise, 15)[len(T.tuiles_de_emprise(z.emprise, 15)) // 2])
    t = T.rendre_tuile(15, tx, ty, proj, sources, cache)
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    X, Z = proj.to_xz(*T.latlon_de_monde_px(tx * 256 + j + 0.5, ty * 256 + i + 0.5, 15))
    ecart = np.abs(t[:, :, :3].astype(int) - monde.couleur(X, Z).astype(int))[t[:, :, 3] == 255]
    assert ecart.max() > 20


class _ProjPlane:
    """Projection de test : x = 1000 * lat, z = 1000 * lon (metres)."""

    def to_xz(self, lat, lon):
        return np.asarray(lat) * 1000.0, np.asarray(lon) * 1000.0


def test_pixel_sans_image_transparent_et_plus_proche_centre():
    zoom, tx, ty = 18, 140000, 131072                   # a l'equateur : memes echelles en x et en z
    proj = _ProjPlane()
    X, Z = T.cartes_xz(zoom, tx, ty, proj)
    pas = float(Z[0, 1] - Z[0, 0])                       # metres (de ce plan) par pixel de tuile
    assert float(X[0, 0] - X[1, 0]) == pytest.approx(pas, rel=1e-3)
    xc, zc = float(X.mean()), float(Z.mean())
    zpx, xpx = (Z - zc) / pas, (X - xc) / pas            # pixels depuis le centre de la tuile (xpx : vers le nord)
    imgs = {'a': np.full((200, 200, 3), (255, 0, 0), np.uint8), 'b': np.full((200, 200, 3), (0, 0, 255), np.uint8)}
    cache = T.CacheImages(lambda k: imgs[k])
    # a : centre a -60 px, couvre de -160 a +40 ; b : centre a +60 px, couvre de -40 a +160 ; x : +-100 px
    deux = T.Sources([xc, xc], [zc - 60 * pas, zc + 60 * pas], [pas, pas], [200, 200], [200, 200], ['a', 'b'])
    t = T.rendre_tuile(zoom, tx, ty, proj, deux, cache)
    dedans = np.abs(xpx) < 98
    hors = np.abs(xpx) > 102
    assert (t[dedans, 3] == 255).all() and (t[hors, 3] == 0).all()                  # hors des images : transparent
    assert (t[dedans & (zpx < -1), 0] == 255).all() and (t[dedans & (zpx < -1), 2] == 0).all()     # plus proche centre : a
    assert (t[dedans & (zpx > 1), 2] == 255).all() and (t[dedans & (zpx > 1), 0] == 0).all()       # puis b
    seul = T.Sources([xc], [zc - 60 * pas], [pas], [200], [200], ['a'])
    t = T.rendre_tuile(zoom, tx, ty, proj, seul, cache)
    assert (t[dedans & (zpx < 39), 3] == 255).all() and (t[dedans & (zpx > 41), 3] == 0).all()    # un pixel qu'aucune image ne couvre
    loin = T.Sources([xc + 5000 * pas], [zc], [pas], [200], [200], ['a'])
    assert T.rendre_tuile(zoom, tx, ty, proj, loin, cache) is None                   # aucune image : pas de tuile


# ------------------------------------------------------------------ TU5 : la pyramide

def _toile(f):
    toile = np.zeros((512, 512, 4), np.uint8)
    for (dx, dy), t in f.items():
        toile[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = t
    return toile


def test_reduire_est_la_moyenne_2x2_sur_une_zone_opaque():
    rng = np.random.default_rng(1)
    f = {(dx, dy): rng.integers(0, 256, (256, 256, 4), dtype=np.uint8) for dx in (0, 1) for dy in (0, 1)}
    for t in f.values():
        t[:, :, 3] = 255
    r = T.reduire(f)
    att = _toile(f).reshape(256, 2, 256, 2, 4).astype(float).mean(axis=(1, 3))
    assert np.abs(r.astype(float) - att).max() <= 1.0 and (r[:, :, 3] == 255).all()       # opaque : rien ne change
    f[(1, 1)] = None
    f[(0, 1)] = None
    r2 = T.reduire(f)
    assert (r2[128:] == 0).all() and r2[:128].any()                    # une fille absente est transparente
    assert T.reduire({(0, 0): None}) is None
    assert T.reduire({(0, 0): np.zeros((256, 256, 4), np.uint8)}) is None


def test_reduire_pondere_par_l_alpha_contre_le_calcul_en_flottants():
    rng = np.random.default_rng(1)
    f = {(dx, dy): rng.integers(0, 256, (256, 256, 4), dtype=np.uint8) for dx in (0, 1) for dy in (0, 1)}
    r = T.reduire(f)
    toile = _toile(f).astype(float)
    a = toile[:, :, 3].reshape(256, 2, 256, 2)
    rgb = toile[:, :, :3].reshape(256, 2, 256, 2, 3)
    att_rgb = (rgb * a[..., None]).sum(axis=(1, 3)) / np.maximum(a.sum(axis=(1, 3)), 1)[..., None]
    assert np.abs(r[:, :, :3].astype(float) - att_rgb).max() <= 1.0
    assert np.abs(r[:, :, 3].astype(float) - a.mean(axis=(1, 3))).max() <= 1.0


def test_tu5_bord_transparent_parent_attendu_ecrit_en_dur():
    """Le parent attendu est ecrit a la main, selon la regle : alpha = moyenne des alphas ; rgb = somme(rgb x alpha) / somme(alpha)."""
    # on construit les cas dans la toile 512 x 512 des quatre filles : chaque bloc 2 x 2 de pixels donne un pixel de la mere ;
    # deux pixels opaques gris 200 et deux transparents : le gris reste 200 (la moyenne simple donnait 100), alpha 128
    toile = np.zeros((512, 512, 4), np.uint8)
    toile[0:2, 0:2] = [[(200, 200, 200, 255), (200, 200, 200, 255)], [(0, 0, 0, 0), (0, 0, 0, 0)]]              # mere (0, 0)
    toile[0:2, 2:4] = [[(100, 0, 0, 255), (0, 200, 0, 128)], [(50, 50, 50, 0), (0, 0, 240, 64)]]               # mere (0, 1)
    toile[0:2, 4:6] = 0                                                                                          # mere (0, 2)
    toile[0:2, 6:8] = [[(10, 20, 30, 255)] * 2] * 2                                                              # mere (0, 3) : opaque
    filles = {(dx, dy): np.ascontiguousarray(toile[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256]) for dx in (0, 1) for dy in (0, 1)}
    r = T.reduire(filles)
    assert tuple(r[0, 0]) == (200, 200, 200, 128)
    assert tuple(r[0, 1]) == (57, 57, 34, 112)         # somme alpha 447 ; r 25500/447, g 25600/447, b 15360/447 ; alpha 447/4
    assert tuple(r[0, 2]) == (0, 0, 0, 0)               # quatre transparents : rgb 0, alpha 0
    assert tuple(r[0, 3]) == (10, 20, 30, 255)          # opaque : moyenne simple


def test_tu5_le_bord_ne_s_assombrit_pas_de_niveau_en_niveau():
    """Une zone opaque gris 200 dont la moitie droite est transparente : sur quatre niveaux, tout pixel non transparent reste
    (200, 200, 200) (la moyenne simple l'assombrissait a chaque niveau)."""
    t = np.zeros((256, 256, 4), np.uint8)
    t[:, :101] = (200, 200, 200, 255)                  # frontiere au milieu d'un bloc 2 x 2 a chaque niveau
    for niveau in range(4):
        r = T.reduire({(dx, dy): t for dx in (0, 1) for dy in (0, 1)})
        vus = r[r[:, :, 3] > 0]
        assert len(vus) and (vus[:, :3] == 200).all(), niveau
        t = r


def test_plan_des_phases():
    assert T.plan_phases(15, 8) == [(15, 4), (11, 3)]
    assert sum(n for _, n in T.plan_phases(15, 8)) == 7
    assert T.plan_phases(15, 15) == [(15, 0)]
    assert T.plan_phases(18, 8)[0] == (18, 4) and sum(n for _, n in T.plan_phases(18, 8)) == 10
    assert T.plan_phases(10, 9) == [(10, 1)]


def test_tu5_chaque_tuile_est_la_reduction_de_ses_quatre_filles(passe_lisse, monkeypatch):
    """RGBA avant codage : on intercepte l'ecriture et on verifie tous les niveaux d'un bloc, en deux phases."""
    z, g, monde, proj = passe_lisse
    sources, cache = _contexte(z, g, proj)
    vues = {}
    monkeypatch.setattr(T, 'ecrire_tuile', lambda d, zz, x, y, couche, rgba: vues.__setitem__((zz, x, y), rgba.copy()) or 1)
    base = [(int(x), int(y)) for x, y in T.tuiles_de_emprise(z.emprise, 15)]
    tmp = z.dossier / 'pyr'
    zoom_min = 8
    tuiles = base
    for k, (zb, niv) in enumerate(T.plan_phases(15, zoom_min)):
        for (bx, by), lst in T.blocs_de(tuiles, zb, T.BLOC_NIVEAUX).items():
            T.traiter_bloc({'base_zoom': zb, 'bx': bx, 'by': by, 'niveaux': niv, 'source': 'rendu' if k == 0 else 'spill',
                            'tuiles': lst, 'couche': 'alt', 'dossier': str(z.dossier / 'tp'), 'tmp': str(tmp),
                            'ecrire_base': k == 0, 'garder_haut': zb - niv > zoom_min}, proj, sources, cache)
        if zb - niv > zoom_min:
            zh = zb - niv
            tuiles = sorted((int(p.parent.name), int(p.stem)) for p in (tmp / str(zh)).glob('*/*.npy'))
            for p in (tmp / str(zh)).glob('*/*.npy'):                            # la tuile gardee = celle du niveau precedent
                np.testing.assert_array_equal(np.load(p), vues[(zh, int(p.parent.name), int(p.stem))])
    zooms = sorted({k[0] for k in vues})
    assert zooms == list(range(8, 16))
    n_verifiees = 0
    for (zz, x, y), t in vues.items():
        if zz == 15:
            continue
        filles = {(dx, dy): vues.get((zz + 1, 2 * x + dx, 2 * y + dy)) for dx in (0, 1) for dy in (0, 1)}
        att = T.reduire(filles)
        assert att is not None and np.abs(t.astype(int) - att.astype(int)).max() <= 1
        n_verifiees += 1
    assert n_verifiees >= 7


def _tuiler(argv, **kw):
    return ft.main(argv + [f'--{k.replace("_", "-")}={v}' for k, v in kw.items()])


def _lire_manifeste(work, nom):
    return json.loads((work / 'out' / 'tuiles' / f'{nom}.json').read_text(encoding='utf-8'))


def test_tu5_nombre_de_tuiles_par_zoom_et_aucune_hors_emprise(passe, cfg):
    z, g, monde, proj = passe
    work = ft.f10capture.CFG.work_dir
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    man = _lire_manifeste(work, 'alt-syria-rect')
    for zoom in range(8, 16):
        attendu = len(T.tuiles_de_emprise(z.emprise, zoom))
        assert man['tiles_per_zoom'][str(zoom)] == attendu, f'zoom {zoom}'
        ecrites = T.lister_tuiles(z.dossier / 'tuiles', 'alt')
        ens = {(t[1], t[2]) for t in ecrites if t[0] == zoom}
        assert ens == {(int(a), int(b)) for a, b in T.tuiles_de_emprise(z.emprise, zoom)}
    assert man['tiles_total'] == sum(man['tiles_per_zoom'].values())
    assert 'tiles_per_zoom' in man and min(map(int, man['tiles_per_zoom'])) == 8


def test_tu5_zone_sans_image_tuile_absente(passe_lisse, monkeypatch):
    z, g, monde, proj = passe_lisse
    sources, cache = _contexte(z, g, proj)
    ecrites = {}
    monkeypatch.setattr(T, 'ecrire_tuile', lambda d, zz, x, y, couche, rgba: ecrites.__setitem__((zz, x, y), 1) or 1)
    tx, ty = (int(v) for v in T.tuiles_de_emprise(z.emprise, 15)[0])
    loin = (tx + 400, ty + 400)
    r = T.traiter_bloc({'base_zoom': 15, 'bx': 0, 'by': 0, 'niveaux': 2, 'source': 'rendu', 'tuiles': [loin, (tx, ty)],
                        'couche': 'alt', 'dossier': 'x', 'tmp': 'y', 'ecrire_base': True, 'garder_haut': False},
                       proj, sources, cache)
    assert r['vides'] == 1 and (15, *loin) not in ecrites and (15, tx, ty) in ecrites
    assert not any(k[0] == 14 and k[1:] == (loin[0] // 2, loin[1] // 2) for k in ecrites) or (tx // 2, ty // 2) == (loin[0] // 2, loin[1] // 2)


def test_tuiler_sur_grille_incomplete_n_ecrit_rien(passe, capsys):
    z, g, monde, proj = passe
    work = ft.f10capture.CFG.work_dir
    ft._nom_image(z, g['cases'][3]).unlink()
    capsys.readouterr()
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 4
    n = len(g['cases'])
    assert f'INCOMPLET {n - 1}/{n}' in capsys.readouterr().out
    assert not (z.dossier / 'tuiles').exists() and not (work / 'out' / 'tuiles').exists()


def test_tuiler_sans_plan_ou_avec_autres_arguments_est_refuse(passe, cfg):
    z, g, monde, proj = passe
    with pytest.raises(SystemExit) as e:
        _tuiler(args('tuiler', 'Syria', zoom=15, rectangle='35.0,36.0,35.05,36.06'), processus=1)       # autre emprise, autre passe
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), zoom_min=16)
    assert e.value.code == 2


def test_plusieurs_processus_donnent_les_memes_tuiles(passe):
    z, g, monde, proj = passe
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    un = {t[:3]: t[3].read_bytes() for t in T.lister_tuiles(z.dossier / 'tuiles', 'alt')}
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=2) == 0           # reprise : tout est reecrit
    deux = {t[:3]: t[3].read_bytes() for t in T.lister_tuiles(z.dossier / 'tuiles', 'alt')}
    assert un.keys() == deux.keys() and all(un[k] == deux[k] for k in un)


# ------------------------------------------------------------------ TU6 : le codage et les archives

def test_tu6_codage_alt_png_palette_et_sat_webp():
    rng = np.random.default_rng(2)
    t = rng.integers(0, 256, (256, 256, 4), dtype=np.uint8)
    t[:, :, 3] = 255
    png = T.encoder(t, 'alt')
    im = Image.open(io.BytesIO(png))
    assert im.format == 'PNG' and im.mode == 'P' and len(im.getcolors(256)) <= 256
    assert 'transparency' not in im.info
    t[:100, :, 3] = 0
    im = Image.open(io.BytesIO(T.encoder(t, 'alt')))
    assert im.mode == 'P' and im.format == 'PNG' and isinstance(im.info['transparency'], int)
    rgba = im.convert('RGBA')
    assert (np.asarray(rgba)[:100, :, 3] == 0).all() and (np.asarray(rgba)[100:, :, 3] == 255).all()
    assert len(im.getcolors(256)) <= 256
    w = Image.open(io.BytesIO(T.encoder(t, 'sat')))
    assert w.format == 'WEBP' and w.size == (256, 256)
    assert np.asarray(w.convert('RGBA'))[:100, :, 3].max() == 0           # alpha conserve
    with pytest.raises(ValueError):
        T.encoder(np.zeros((256, 256, 4), np.uint8), 'alt')


def test_tu6_alt_et_sat_ecrits_par_tuiler(passe, cfg):
    z, g, monde, proj = passe
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    for _, _, _, p in T.lister_tuiles(z.dossier / 'tuiles', 'alt')[:60]:
        assert p.suffix == '.png'
        with Image.open(p) as im:
            assert im.mode == 'P' and len(im.getcolors(256)) <= 256 and im.size == (256, 256)
    cfg()
    zs, gs = ecrire_passe(args('capture', 'Syria', couche='sat', zoom=15, rectangle=RECT), Monde('alt'))
    assert _tuiler(args('tuiler', 'Syria', couche='sat', zoom=15, rectangle=RECT), processus=1, zoom_min=13) == 0
    fich = T.lister_tuiles(zs.dossier / 'tuiles', 'sat')
    assert fich and all(p.suffix == '.webp' for *_, p in fich) and {f[0] for f in fich} == {13, 14, 15}
    with Image.open(fich[0][3]) as im:
        assert im.format == 'WEBP' and im.size == (256, 256)
    assert (ft.f10capture.CFG.work_dir / 'out' / 'tuiles' / 'sat-syria-rect.config.json').is_file()


def _fichiers_de_test(tmp_path, n=40, ext='png'):
    out = []
    for k in range(n):
        p = tmp_path / 'src' / '12' / str(k // 7) / f'{k}.{ext}'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(range(256)) * (3 + (k * 37) % 41))
        out.append((12, k // 7, k, p))
    return out


def test_tu6_parts_sous_la_limite_manifeste_et_sha256(tmp_path):
    fichiers = _fichiers_de_test(tmp_path)
    limite = 40000
    parts = T.empaqueter(fichiers, 'alt-syria', tmp_path / 'out', 'alt-syria-rect', 12, limite=limite)
    assert len(parts) >= 4 and [p['name'] for p in parts] == [f'alt-syria-rect_z12_{i:03d}.tar' for i in range(1, len(parts) + 1)]
    vus = []
    for p in parts:
        chemin = tmp_path / 'out' / p['name']
        assert chemin.stat().st_size <= limite and chemin.stat().st_size == p['bytes']
        assert T.sha256_fichier(chemin) == p['sha256']
        with tarfile.open(chemin) as tf:
            noms = tf.getnames()
            assert 'LICENSE-TILES.txt' in noms and len(noms) - 1 == p['tiles']
            lic = tf.extractfile('LICENSE-TILES.txt').read().decode('utf-8')
            assert lic.startswith('Map imagery © Eagle Dynamics SA — captured from the DCS World F10 map. '
                                  'Non-commercial use only. Not affiliated with or endorsed by Eagle Dynamics.')
            for m in tf.getmembers():
                if m.name != 'LICENSE-TILES.txt':
                    assert m.name.startswith('alt-syria/12/')
                    vus.append(m.name)
                    src = tmp_path / 'src' / m.name.split('/', 1)[1]
                    assert tf.extractfile(m).read() == src.read_bytes()      # une tuile n'est jamais coupee
    assert len(vus) == len(set(vus)) == len(fichiers)
    assert vus == [f'alt-syria/12/{x}/{y}.png' for _, x, y, _ in fichiers]      # ordre conserve


def test_tu6_limite_trop_basse_et_tuile_trop_grosse(tmp_path):
    fichiers = _fichiers_de_test(tmp_path, 3)
    with pytest.raises(ValueError):
        T.empaqueter(fichiers, 'a', tmp_path / 'o', 'n', 1, limite=5000)
    gros = tmp_path / 'g.png'
    gros.write_bytes(b'x' * 50000)
    with pytest.raises(ValueError):
        T.empaqueter([(1, 0, 0, gros)], 'a', tmp_path / 'o2', 'n', 1, limite=30000)
    assert T.empaqueter([], 'a', tmp_path / 'o3', 'n', 1) == []


def test_tu6_tuiler_ecrit_parts_manifeste_config_et_licence(passe, monkeypatch):
    z, g, monde, proj = passe
    work = ft.f10capture.CFG.work_dir
    monkeypatch.setattr(ft, 'TAILLE_PART_O', 120000)
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    out = work / 'out' / 'tuiles'
    man = _lire_manifeste(work, 'alt-syria-rect')
    assert len(man['parts']) >= 2
    total = 0
    for p in man['parts']:
        ch = out / p['name']
        assert ch.stat().st_size <= 120000 and T.sha256_fichier(ch) == p['sha256']
        with tarfile.open(ch) as tf:
            assert 'LICENSE-TILES.txt' in tf.getnames()
            lic = tf.extractfile('LICENSE-TILES.txt').read().decode('utf-8')
            assert lic == T.LICENCE_TUILES
            assert 'These map tiles are not covered by the MIT licence of the code in this repository. They are images of ' \
                   'the DCS World F10 map and remain subject to the terms of Eagle Dynamics SA.' in lic
            total += len(tf.getnames()) - 1
    assert total == man['tiles_total']
    cfg_json = json.loads((out / 'alt-syria-rect.config.json').read_text(encoding='utf-8'))
    assert cfg_json['layer'] == 'alt' and cfg_json['theatre'] == 'Syria' and cfg_json['format'] == 'png'
    assert cfg_json['minZoom'] == 8 and cfg_json['maxNativeZoom'] == 15 and cfg_json['tileSize'] == 256
    s, o, n, e = cfg_json['bounds']
    assert (s, o, n, e) == pytest.approx((35.0, 36.0, 35.045, 36.06))
    assert cfg_json['attribution'] == T.MENTION
    assert man['layer'] == 'alt' and man['zoom'] == 15
    # une reprise (tuiler relance) remplace les parts : pas de reste d'une execution precedente
    monkeypatch.setattr(ft, 'TAILLE_PART_O', 400000)
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    assert sorted(p.name for p in out.glob('*.tar')) == [p['name'] for p in _lire_manifeste(work, 'alt-syria-rect')['parts']]


def test_theatre_du_route_planner():
    assert T.CLES_ROUTE_PLANNER == {'syria': 'Syria', 'germanycoldwar': 'GermanyCW', 'caucasus': 'Caucasus'}
