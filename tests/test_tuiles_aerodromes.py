"""`f10tiles.py aerodromes` : les tuiles des zooms 17 et 18 depuis les mosaiques des charts. Mosaiques synthetiques (geometrie,
recouvrement de deux bases) puis, avec les echantillons (DCS_CHARTS_SAMPLES), le calage sur Adana et Konya (TU2)."""
import io
import json
import os
import tarfile
from pathlib import Path

import numpy as np
import pytest
import shapely
from PIL import Image

from test_tuiles_monde import FauxDcs, Monde, args, cfg, f10capture, ft, T, geo   # noqa: F401  (cfg : fixture)


def _png_sans_perte(rgba, couche):
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, format='PNG')
    return buf.getvalue()


def _ecrire_base(racine, nom, xc, zc, mpp, w, h, monde, inverse=False, version=2):
    """out/<theatre>/<nom>/{base.json, mosaic.png} : une mosaique tiree du monde (pixel (r, c) en x = x_top - (r + .5) mpp,
    z = z_left + (c + .5) mpp), georeferencement `version`."""
    d = Path(racine) / nom
    d.mkdir(parents=True, exist_ok=True)
    x_top, z_left = xc + h * mpp / 2, zc - w * mpp / 2
    img = monde.image(xc, zc, mpp, w, h)
    if inverse:
        img = 255 - img
    Image.fromarray(img).save(d / 'mosaic.png')
    cover = {'x_min': x_top - h * mpp, 'x_max': x_top, 'z_min': z_left, 'z_max': z_left + w * mpp}
    bj = {'base': nom, 'theatre': 'Syria', 'm_per_px': mpp, 'mosaic_px': [w, h], 'mosaic_coverage_m': cover,
          'margins_px_lthr_b': [0, 36, 90, 0]}
    if version == 2:
        bj['georef_version'] = 2
    else:                                      # v1 : la couverture n'est pas corrigee du recadrage de l'ecran
        bj['mosaic_coverage_m'] = {'x_min': cover['x_min'] + 36 / 2 * mpp, 'x_max': cover['x_max'] + 36 / 2 * mpp,
                                   'z_min': cover['z_min'] + 90 / 2 * mpp, 'z_max': cover['z_max'] + 90 / 2 * mpp}
    (d / 'base.json').write_text(json.dumps(bj), encoding='utf-8')
    return x_top, z_left


def _lire_tuile(dossier, z, x, y):
    return np.asarray(Image.open(Path(dossier) / str(z) / str(x) / f'{y}.png').convert('RGBA'))


@pytest.fixture
def deux_bases(cfg, monkeypatch):
    cfg()
    monkeypatch.setattr(T, 'encoder', _png_sans_perte)                    # comparaisons exactes : pas de palette
    work = f10capture.CFG.work_dir
    racine = work / 'out' / 'syria'
    monde = Monde('lisse')
    xa, za = 219500.0, -48100.0
    # deux bases de 900 x 700 m qui se recouvrent sur 300 m en z ; la seconde est inversee pour voir laquelle fournit un pixel
    _ecrire_base(racine, 'BaseA', xa, za, 0.45, 2000, 1560, monde)
    _ecrire_base(racine, 'BaseB', xa + 50.0, za + 600.0, 0.45, 2000, 1560, monde, inverse=True)
    return work, racine, monde


def test_aerodromes_geometrie_et_recouvrement(deux_bases):
    work, racine, monde = deux_bases
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    brut = work / 'tuiles_brut' / 'alt-syria-aerodromes'
    proj = geo.Projection.for_theatre('Syria')
    centres = {'A': (219500.0, -48100.0), 'B': (219550.0, -47500.0)}
    n_a = n_b = 0
    tuiles = {z: [(int(p.parent.name), int(p.stem)) for p in (brut / str(z)).glob('*/*.png')] for z in (17, 18)}
    assert len(tuiles[17]) > 0 and len(tuiles[18]) > len(tuiles[17])
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    for z in (17, 18):
        for tx, ty in tuiles[z][::3]:
            t = _lire_tuile(brut, z, tx, ty)
            X, Z = proj.to_xz(*T.latlon_de_monde_px(tx * 256 + j + 0.5, ty * 256 + i + 0.5, z))
            ref = monde.couleur(X, Z).astype(int)
            opaque = t[:, :, 3] == 255
            assert opaque.any()
            # la base la plus proche du centre fournit le pixel : A directement, B en negatif
            dA = (X - centres['A'][0]) ** 2 + (Z - centres['A'][1]) ** 2
            dB = (X - centres['B'][0]) ** 2 + (Z - centres['B'][1]) ** 2
            def cov(c, marge):               # couverture d'une mosaique de 2000 x 1560 px a 0,45 m/px, elargie de `marge` m
                return (np.abs(X - c[0]) < 1560 * 0.45 / 2 + marge) & (np.abs(Z - c[1]) < 2000 * 0.45 / 2 + marge)
            net = np.abs(np.sqrt(dA) - np.sqrt(dB)) > 1.5            # a plus d'un metre de la mediatrice (arrondi du treillis)
            # une base fournit le pixel s'il est franchement chez elle et (franchement plus pres de son centre, ou hors de l'autre)
            deA = opaque & cov(centres['A'], -2) & (((dA < dB) & net) | ~cov(centres['B'], 2))
            deB = opaque & cov(centres['B'], -2) & (((dB < dA) & net) | ~cov(centres['A'], 2))
            if deA.any():
                assert np.abs(t[:, :, :3].astype(int) - ref)[deA].max() <= 4
                n_a += 1
            if deB.any():
                assert np.abs(t[:, :, :3].astype(int) - (255 - ref))[deB].max() <= 4
                n_b += 1
    assert n_a > 3 and n_b > 3


def test_aerodromes_tuiles_sont_celles_qui_coupent_la_couverture(deux_bases):
    work, racine, monde = deux_bases
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    proj = geo.Projection.for_theatre('Syria')
    couvs = []
    for nom in ('BaseA', 'BaseB'):
        bj = json.loads((racine / nom / 'base.json').read_text(encoding='utf-8'))
        from render import georef
        g = georef.georef_from_base(bj)
        couvs.append(ft._couverture_lonlat(g, proj))
    union = shapely.union_all(couvs)
    for z in (17, 18):
        attendu = {(int(a), int(b)) for a, b in T.tuiles_de_emprise(union, z)}
        vu = {(int(p.parent.name), int(p.stem)) for p in (work / 'tuiles_brut' / 'alt-syria-aerodromes' / str(z)).glob('*/*.png')}
        assert vu == attendu, f'zoom {z}'


def test_aerodromes_archives_manifeste_et_config(deux_bases):
    work, racine, monde = deux_bases
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    out = work / 'out' / 'tuiles'
    assert sorted(p.name for p in out.glob('*.tar')) == ['alt-syria-aerodromes_z17_001.tar', 'alt-syria-aerodromes_z18_001.tar']
    man = json.loads((out / 'alt-syria-aerodromes.json').read_text(encoding='utf-8'))
    assert [p['name'] for p in man['parts']] == ['alt-syria-aerodromes_z17_001.tar', 'alt-syria-aerodromes_z18_001.tar']
    assert set(man['tiles_per_zoom']) == {'17', '18'} and man['bases'] == ['BaseA', 'BaseB']
    for p in man['parts']:
        assert T.sha256_fichier(out / p['name']) == p['sha256']
        with tarfile.open(out / p['name']) as tf:
            noms = tf.getnames()
            assert 'LICENSE-TILES.txt' in noms and len(noms) - 1 == p['tiles']
            z = p['name'].split('_z')[1][:2]
            assert all(n.startswith(f'alt-syria/{z}/') for n in noms if n != 'LICENSE-TILES.txt')
            assert tf.extractfile('LICENSE-TILES.txt').read().decode('utf-8') == T.LICENCE_TUILES
    cfg_json = json.loads((out / 'alt-syria-aerodromes.config.json').read_text(encoding='utf-8'))
    assert cfg_json['layer'] == 'alt' and cfg_json['theatre'] == 'Syria' and cfg_json['format'] == 'png'
    assert cfg_json['minZoom'] == 17 and cfg_json['maxNativeZoom'] == 18 and cfg_json['tileSize'] == 256
    s, o, n, e = cfg_json['bounds']
    assert s < n and o < e and 36.9 < s < 37.1 and 35.2 < o < 35.4
    assert cfg_json['attribution'] == T.MENTION


def test_aerodromes_bases_nommees_et_georeferencement_v1(cfg, monkeypatch):
    cfg()
    monkeypatch.setattr(T, 'encoder', _png_sans_perte)
    work = f10capture.CFG.work_dir
    racine = work / 'out' / 'syria'
    monde = Monde('lisse')
    _ecrire_base(racine, 'V1', 219500.0, -48100.0, 0.45, 1200, 900, monde, version=1)
    _ecrire_base(racine, 'V2', 225500.0, -48100.0, 0.45, 1200, 900, monde, version=2)
    (racine / 'osm').mkdir()
    (racine / 'contexte' / 'Autre').mkdir(parents=True)                       # ni base.json, ni mosaique : ignores
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria', 'V1']) == 0
    man = json.loads((work / 'out' / 'tuiles' / 'alt-syria-aerodromes.json').read_text(encoding='utf-8'))
    assert man['bases'] == ['V1']
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    man = json.loads((work / 'out' / 'tuiles' / 'alt-syria-aerodromes.json').read_text(encoding='utf-8'))
    assert man['bases'] == ['V1', 'V2']
    # v1 : le recadrage de l'ecran est corrige par georef_from_base, comme pour les charts
    brut = work / 'tuiles_brut' / 'alt-syria-aerodromes'
    proj = geo.Projection.for_theatre('Syria')
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    for tx, ty in [(int(p.parent.name), int(p.stem)) for p in (brut / '18').glob('*/*.png')][::9]:
        t = _lire_tuile(brut, 18, tx, ty)
        X, Z = proj.to_xz(*T.latlon_de_monde_px(tx * 256 + j + 0.5, ty * 256 + i + 0.5, 18))
        ok = (t[:, :, 3] == 255)
        assert np.abs(t[:, :, :3].astype(int) - monde.couleur(X, Z).astype(int))[ok].max() <= 4
    with pytest.raises(SystemExit) as e:
        ft.main(['f10tiles.py', 'aerodromes', 'Syria', 'Inconnue'])
    assert e.value.code == 2


def test_aerodromes_sans_base_est_refuse(cfg):
    cfg()
    (f10capture.CFG.work_dir / 'out' / 'syria').mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        ft.main(['f10tiles.py', 'aerodromes', 'Syria'])
    assert e.value.code == 2


def test_aerodromes_mosaique_de_taille_differente_est_ignoree(cfg, capsys):
    cfg()
    racine = f10capture.CFG.work_dir / 'out' / 'syria'
    _ecrire_base(racine, 'Bonne', 219500.0, -48100.0, 0.45, 800, 600, Monde('lisse'))
    _ecrire_base(racine, 'Mauvaise', 225500.0, -48100.0, 0.45, 800, 600, Monde('lisse'))
    bj = json.loads((racine / 'Mauvaise' / 'base.json').read_text(encoding='utf-8'))
    bj['mosaic_px'] = [900, 600]
    (racine / 'Mauvaise' / 'base.json').write_text(json.dumps(bj), encoding='utf-8')
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    assert 'Mauvaise : mosaique 800x600 differente de base.json 900x600, ignoree' in capsys.readouterr().out
    assert json.loads((f10capture.CFG.work_dir / 'out' / 'tuiles' / 'alt-syria-aerodromes.json').read_text())['bases'] == ['Bonne']


# ------------------------------------------------------------------ TU2 : le calage sur Adana et Konya (echantillons)

def mesure_calage(samples, work, base, zooms=(17, 18), seuil_m=5.0):
    """Part des points .rn4 (revetement) a moins de `seuil_m` d'un pixel de revetement : dans la mosaique, puis dans les tuiles
    des zooms `zooms` (assemblees, meme detection du revetement, meme distance). Retourne {'mosaique': %, 'z17': %, ...}."""
    from render import georef, vector
    brut = Path(work) / 'tuiles_brut' / 'alt-syria-aerodromes'
    dossier = Path(samples) / 'syria' / base
    pts = np.array([[q[1], q[3]] for q in json.loads((dossier / 'rn4_points.json').read_text(encoding='utf-8'))['points']])
    g = georef.georef_from_base(georef.load_base_json(dossier))
    img = np.asarray(Image.open(dossier / 'mosaic.png').convert('RGB'))
    dist = vector.distance_to_mask_m(vector.pavement_mask(img, g.mpp), g.mpp)
    rc = np.array([g.xz_to_rc(x, z) for x, z in pts])
    r, c = np.rint(rc[:, 0]).astype(int), np.rint(rc[:, 1]).astype(int)
    dedans = (r >= 0) & (r < g.height) & (c >= 0) & (c < g.width)
    touche = np.zeros(len(pts), bool)
    touche[dedans] = dist[r[dedans], c[dedans]] <= seuil_m
    res = {'points': len(pts), 'dans_la_mosaique': int(dedans.sum()), 'mosaique': 100 * float(touche.mean())}
    proj = geo.Projection.for_theatre('Syria')
    la, lo = proj.to_latlon(pts[:, 0], pts[:, 1])
    for z in zooms:
        px, py = T.monde_px(la, lo, z)
        tx0, tx1 = int(px.min() // 256) - 1, int(px.max() // 256) + 1
        ty0, ty1 = int(py.min() // 256) - 1, int(py.max() // 256) + 1
        toile = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, 4), np.uint8)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                p = brut / str(z) / str(tx) / f'{ty}.png'
                if p.is_file():
                    toile[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = np.asarray(Image.open(p).convert('RGBA'))
        mpp = T.mpp_tuile(z, float(la.mean()))
        d2 = vector.distance_to_mask_m(vector.pavement_mask(np.ascontiguousarray(toile[:, :, :3]), mpp), mpp)
        cc, rr = np.rint(px - tx0 * 256 - 0.5).astype(int), np.rint(py - ty0 * 256 - 0.5).astype(int)
        res[f'z{z}'] = 100 * float((d2[rr, cc] <= seuil_m).mean())
        res[f'z{z}_mpp'] = mpp
    return res


@pytest.fixture(scope='module')
def aerodromes_samples(samples, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('calage')
    (tmp / 'dcs' / 'Mods' / 'terrains').mkdir(parents=True)
    work = tmp / 'work'
    (work / 'out' / 'syria').mkdir(parents=True)
    for b in ('Adana', 'Konya'):
        os.symlink(samples / 'syria' / b, work / 'out' / 'syria' / b, target_is_directory=True)
    cfgp = tmp / 'config.json'
    cfgp.write_text(json.dumps({'work_dir': str(work), 'dcs_install': str(tmp / 'dcs'), 'screen': [3440, 1440]}), encoding='utf-8')
    f10capture._screen = None
    f10capture.init(['x', '--config', str(cfgp)])
    assert ft.main(['f10tiles.py', 'aerodromes', 'Syria']) == 0
    return samples, work


@pytest.mark.parametrize('base', ['Adana', 'Konya'])
def test_tu2_calage_des_tuiles_egal_a_celui_de_la_mosaique(aerodromes_samples, base):
    samples, work = aerodromes_samples
    m = mesure_calage(samples, work, base)
    assert m['dans_la_mosaique'] == m['points']
    for z in (17, 18):
        assert abs(m[f'z{z}'] - m['mosaique']) <= 1.0, m
    assert m['mosaique'] > 90.0                                    # la mesure a un sens : la mosaique cale elle-meme


def test_tu2_archives_des_echantillons(aerodromes_samples):
    samples, work = aerodromes_samples
    man = json.loads((work / 'out' / 'tuiles' / 'alt-syria-aerodromes.json').read_text(encoding='utf-8'))
    assert man['bases'] == ['Adana', 'Konya'] and man['tiles_per_zoom']['18'] > man['tiles_per_zoom']['17'] > 100
    for p in man['parts']:
        assert (work / 'out' / 'tuiles' / p['name']).stat().st_size <= 300000000
        assert T.sha256_fichier(work / 'out' / 'tuiles' / p['name']) == p['sha256']
