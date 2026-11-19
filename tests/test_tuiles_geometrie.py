"""Tuiles XYZ : la loi de la camera, Web Mercator, la projection des aerodromes (TU1), la grille (TU3), la loi et le plan (TU9),
le mode de la carte (TU10, images construites et echantillons). Aucun DCS."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from PIL import Image

from test_tuiles_monde import FauxDcs, Monde, args, cfg, ecrire_passe, ft, T, geo   # noqa: F401  (cfg : fixture)

USABLE = (3350, 1404)
DECALAGE = (18.0, 45.0)


# ------------------------------------------------------------------ TU9 : la loi de la camera

@pytest.mark.parametrize('mpp,alt', [(1.47642, 4400), (2.15723, 5800), (4.96598, 8800), (8.77832, 11700)])
def test_tu9_altitude_commandee_mesures_de_la_sonde(mpp, alt):
    assert T.altitude_commandee(mpp) == pytest.approx(alt, abs=1.0)


def test_tu9_constantes_de_la_loi():
    assert T.K_HAUTEUR_M == 5232.6
    assert T.MPP_PAR_ALT_M == 0.00033555
    f10 = ft.f10capture
    assert T.MPP_PAR_ALT_M == f10.MPP_PER_ALT_M          # la meme valeur que l'outil de capture


def test_tu9_aller_retour_exact():
    for alt in np.concatenate([np.linspace(1100, 5232.6, 40), np.linspace(5232.7, 187000, 200)]):
        assert T.altitude_commandee(T.mpp_camera(alt)) == pytest.approx(alt, rel=1e-12)
    for mpp in np.geomspace(0.3, 6000, 200):
        assert T.mpp_camera(T.altitude_commandee(mpp)) == pytest.approx(mpp, rel=1e-12)
    # continuite a K : en dessous, h = altitude ; au dessus, h = alt^2 / K
    assert T.hauteur_vraie(T.K_HAUTEUR_M) == T.K_HAUTEUR_M
    assert T.hauteur_vraie(2 * T.K_HAUTEUR_M) == pytest.approx(4 * T.K_HAUTEUR_M)


def test_tu9_hauteurs_des_zooms_de_la_sonde():
    """Tableau de la sonde (Syria 35 deg, GermanyCW 52 deg) : altitude commandee aux zooms 15 a 18."""
    for lat, attendu in ((35.0, {15: 7810, 16: 5520, 17: 2920, 18: 1460}), (52.0, {15: 6770, 16: 4380, 17: 2190, 18: 1100})):
        for z, alt in attendu.items():
            assert T.altitude_commandee(T.mpp_tuile(z, lat)) == pytest.approx(alt, rel=0.01)


# ------------------------------------------------------------------ Web Mercator

def test_mercator_aller_retour_et_reperes_connus():
    lat, lon = np.array([48.8566, -33.87, 0.0]), np.array([2.3522, 151.2, 0.0])
    for z in (0, 8, 15, 18):
        x, y = T.monde_px(lat, lon, z)
        la, lo = T.latlon_de_monde_px(x, y, z)
        assert np.abs(la - lat).max() < 1e-9 and np.abs(lo - lon).max() < 1e-9
    x, y = T.monde_px(0.0, 0.0, 1)
    assert (float(x), float(y)) == (256.0, 256.0)
    assert T.mpp_tuile(0, 0.0) == pytest.approx(156543.033928)
    assert T.mpp_tuile(16, 60.0) == pytest.approx(156543.033928 * 0.5 / 65536)
    o, s, e, n = T.boite_tuile(0, 0, 0)
    assert (o, e) == (-180.0, 180.0) and n == pytest.approx(T.LAT_MAX) and s == pytest.approx(-T.LAT_MAX)


# ------------------------------------------------------------------ TU1 : la projection des aerodromes

@pytest.mark.parametrize('theatre', ['Syria', 'GermanyColdWar'])
def test_tu1_projection_des_aerodromes_au_zoom_18(theatre):
    from render import dcsdata
    terrain = dcsdata.load_terrain(theatre)
    proj = geo.Projection.for_theatre(theatre)
    pts = [(float(a.position.x), float(a.position.y)) for a in terrain.airports.values()]
    assert len(pts) > 100
    x, z = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    lat, lon = proj.to_latlon(x, z)
    px, py = T.monde_px(lat, lon, 18)
    # (a) pixel de tuile flottant : retombe sur le point
    la, lo = T.latlon_de_monde_px(px, py, 18)
    x2, z2 = proj.to_xz(la, lo)
    assert np.hypot(np.asarray(x2) - x, np.asarray(z2) - z).max() < 0.01
    # (b) pixel entier (le pixel qui contient le point, pris en son centre)
    pxi, pyi = np.floor(px) + 0.5, np.floor(py) + 0.5
    la, lo = T.latlon_de_monde_px(pxi, pyi, 18)
    x3, z3 = proj.to_xz(la, lo)
    err = np.hypot(np.asarray(x3) - x, np.asarray(z3) - z)
    assert err.max() < 0.5, f'{theatre} : {err.max():.3f} m'
    # (c) la tuile (zoom 18, tx, ty) retrouve son point
    tx, ty = (px // 256).astype(int), (py // 256).astype(int)
    ok = [boite_contient(T.boite_tuile(18, int(a), int(b)), float(lo_), float(la_))
          for a, b, la_, lo_ in zip(tx, ty, lat, lon)]
    assert all(ok)


def boite_contient(boite, lon, lat):
    o, s, e, n = boite
    return o <= lon <= e and s <= lat <= n


# ------------------------------------------------------------------ TU3 : la grille

THEATRES_TU3 = {'Syria': (31.0, 34.5, 37.5, 41.5), 'GermanyColdWar': (47.5, 6.0, 55.0, 16.0)}   # lat_s, lon_o, lat_n, lon_e


def _rectangle_aleatoire(rng, boite):
    lat_s0, lon_o0, lat_n0, lon_e0 = boite
    h_km, w_km = rng.uniform(5, 300, 2)
    lat0 = rng.uniform(lat_s0, lat_n0 - 1.0)
    lon0 = rng.uniform(lon_o0, lon_e0 - 1.0)
    dlat = h_km / 111.2
    dlon = w_km / (111.2 * math.cos(math.radians(lat0 + dlat / 2)))
    return lat0, lon0, lat0 + dlat, lon0 + dlon


def test_tu3_grille_couvre_la_zone_et_rien_de_trop():
    rng = np.random.default_rng(20261119)
    projections = {t: geo.Projection.for_theatre(t) for t in THEATRES_TU3}
    n_cases = 0
    for k in range(200):
        theatre = ('Syria', 'GermanyColdWar')[k % 2]
        zoom = int(rng.integers(13, 18))
        lat_s, lon_o, lat_n, lon_e = _rectangle_aleatoire(rng, THEATRES_TU3[theatre])
        emp = T.emprise_rectangle(lat_s, lon_o, lat_n, lon_e)
        mpp = T.mpp_tuile(zoom, lat_n)
        exz = T.emprise_xz(emp, projections[theatre])
        g = T.plan_grille(exz, mpp, USABLE, 32, DECALAGE)
        fh, fw = g['empreinte_m']
        sth, stw = g['pas_m']
        assert (fh, fw) == (pytest.approx(USABLE[1] * mpp), pytest.approx(USABLE[0] * mpp))
        assert (sth, stw) == (pytest.approx((USABLE[1] - 64) * mpp), pytest.approx((USABLE[0] - 64) * mpp))
        cases = g['cases']
        assert cases
        n_cases += len(cases)
        # centre du contenu de chaque case = position commandee moins le decalage du recadrage
        xc = np.array([c['x'] - DECALAGE[0] * mpp for c in cases])
        zc = np.array([c['z'] - DECALAGE[1] * mpp for c in cases])
        utiles = shapely.box(xc - sth / 2, zc - stw / 2, xc + sth / 2, zc + stw / 2)
        reste = exz.difference(shapely.union_all(utiles))
        # des filets de 1e-9 m (arrondis flottants) sont tolerables ; un manque de 0,2 mm de large ne l'est pas
        assert reste.buffer(-1e-4).is_empty, f'zone {k} ({theatre}, z{zoom}) : {reste.area:.1f} m2 non couverts'
        empreintes = shapely.box(xc - fh / 2, zc - fw / 2, xc + fh / 2, zc + fw / 2)
        assert shapely.intersects(empreintes, exz).all(), f'zone {k} : une case ne coupe pas l\'emprise'
        elargie = exz.buffer(max(fh, fw))
        assert shapely.contains(elargie, shapely.points(xc, zc)).all()
    assert n_cases > 1000


def test_tu3_les_cases_sont_au_pas_et_les_lignes_vont_du_nord_au_sud():
    proj = geo.Projection.for_theatre('Syria')
    emp = T.emprise_rectangle(35.0, 36.0, 35.5, 36.6)
    exz = T.emprise_xz(emp, proj)
    mpp = T.mpp_tuile(15, 35.5)
    g = T.plan_grille(exz, mpp, USABLE, 32, DECALAGE)
    xs = {c['row']: c['x'] for c in g['cases']}
    assert all(xs[r] > xs[r + 1] for r in range(g['lignes'] - 1) if r in xs and r + 1 in xs)      # ligne 0 = nord = x le plus grand
    zs = {c['col']: c['z'] for c in g['cases']}
    assert all(zs[c] < zs[c + 1] for c in range(g['colonnes'] - 1) if c in zs and c + 1 in zs)
    ordre = T.ordre_serpentin(g['cases'])
    assert len(ordre) == len(g['cases']) and {(c['row'], c['col']) for c in ordre} == {(c['row'], c['col']) for c in g['cases']}
    r0 = [c['col'] for c in ordre if c['row'] == 0]
    assert r0 == sorted(r0)
    r1 = [c['col'] for c in ordre if c['row'] == 1]
    assert r1 == sorted(r1, reverse=True)
    assert [c['row'] for c in ordre] == sorted(c['row'] for c in ordre)


def test_emprise_en_forme_de_l_et_case_sans_emprise():
    """Une emprise en L : les cases du coin vide ne sont pas dans la grille."""
    proj = geo.Projection.for_theatre('Syria')
    L = shapely.Polygon([(36.0, 35.0), (36.0, 35.6), (36.1, 35.6), (36.1, 35.1), (36.6, 35.1), (36.6, 35.0)])
    exz = T.emprise_xz(L, proj)
    g = T.plan_grille(exz, T.mpp_tuile(14, 35.6), USABLE, 32, DECALAGE)
    assert len(g['cases']) < g['lignes'] * g['colonnes']
    boite = T.plan_grille(T.emprise_xz(shapely.box(36.0, 35.0, 36.6, 35.6), proj), T.mpp_tuile(14, 35.6), USABLE, 32, DECALAGE)
    assert len(g['cases']) < len(boite['cases'])


# ------------------------------------------------------------------ les tuiles de l'emprise

def test_tuiles_de_emprise_par_blocs_egal_au_calcul_tuile_par_tuile():
    emp = shapely.Polygon([(36.0, 35.0), (36.0, 35.6), (36.1, 35.6), (36.1, 35.1), (36.9, 35.1), (36.6, 35.0)])
    for z in (6, 9, 12, 13):
        a, b = T.tuiles_de_emprise(emp, z), T.tuiles_de_emprise(emp, z, brut=True)
        assert a.shape == b.shape and (a == b).all()
    assert len(T.tuiles_de_emprise(emp, 0)) == 1
    assert (np.diff(T.tuiles_de_emprise(emp, 13)[:, 1]) >= 0).all()
    # une tuile retenue coupe l'emprise ; sa voisine hors du compte, non
    tl = {tuple(t) for t in T.tuiles_de_emprise(emp, 11)}
    for tx, ty in list(tl)[:20]:
        o, s, e, n = T.boite_tuile(11, tx, ty)
        assert emp.intersects(shapely.box(o, s, e, n))
    o, s, e, n = T.boite_tuile(11, *T.tuiles_de_emprise(emp, 11)[0])
    assert (tx, ty) in tl


def test_emprises_invalides():
    with pytest.raises(ValueError):
        T.emprise_rectangle(10, 10, 5, 20)
    with pytest.raises(ValueError):
        T.emprise_rectangle(10, 10, 20, 5)


def test_charger_emprise_feature_polygon_et_sha(tmp_path):
    ring = [[35.0, 30.0], [36.0, 30.0], [36.0, 31.0], [35.0, 31.0], [35.0, 30.0]]
    poly = {'type': 'Polygon', 'coordinates': [ring]}
    for nom, doc in (('f.geojson', {'type': 'Feature', 'properties': {}, 'geometry': poly}), ('p.geojson', poly)):
        p = tmp_path / nom
        p.write_text(json.dumps(doc), encoding='utf-8')
        g, sha = T.charger_emprise(p)
        assert g.bounds == (35.0, 30.0, 36.0, 31.0) and len(sha) == 64
    (tmp_path / 'pt.geojson').write_text(json.dumps({'type': 'Point', 'coordinates': [1, 2]}), encoding='utf-8')
    with pytest.raises(ValueError):
        T.charger_emprise(tmp_path / 'pt.geojson')


# ------------------------------------------------------------------ la reprojection se calcule sur un treillis : sa precision

@pytest.mark.parametrize('theatre,lat,lon,zoom', [('Syria', 36.0, 36.0, 8), ('Syria', 37.5, 41.0, 13), ('Syria', 33.0, 35.5, 18),
                                                 ('GermanyColdWar', 54.0, 12.0, 11), ('GermanyColdWar', 48.0, 8.0, 17)])
def test_cartes_xz_treillis_contre_calcul_exact(theatre, lat, lon, zoom):
    proj = geo.Projection.for_theatre(theatre)
    px, py = T.monde_px(lat, lon, zoom)
    tx, ty = int(px // 256), int(py // 256)
    X, Z = T.cartes_xz(zoom, tx, ty, proj)
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    la, lo = T.latlon_de_monde_px(tx * 256 + j + 0.5, ty * 256 + i + 0.5, zoom)
    Xe, Ze = proj.to_xz(la, lo)
    err_px = max(np.abs(X - Xe).max(), np.abs(Z - Ze).max()) / T.mpp_tuile(zoom, lat)
    assert err_px < 0.005, f'{err_px:.5f} px'                        # l'interpolation du treillis : bien moins d'un pixel


# ------------------------------------------------------------------ TU10 : le mode

MODES_DIR = 'tuiles/modes'


def test_tu10_les_neuf_images_de_la_sonde(samples):
    dossier = samples / MODES_DIR
    fichiers = sorted(dossier.glob('*.png'))
    assert len(fichiers) == 9
    for f in fichiers:
        mode_nom = f.name.split('_')[0]
        im = np.asarray(Image.open(f).convert('RGB'))
        assert im.shape == (800, 1800, 3)
        aplat, sat = T.mesurer_centre(im)
        assert T.classer_mode(aplat, sat) == mode_nom, f'{f.name} : aplat {aplat:.3f}, saturation {sat:.1f}'


def test_tu10_image_a_mi_chemin_est_inconnue():
    """Une image construite entre l'Alt (aplat eleve) et le Sat (aplat nul) : en bruitant une part croissante de l'image, l'aplat
    passe de 0,9 a 0 ; il existe une part pour laquelle il tombe entre 0,15 et 0,30 : le mode est inconnu."""
    rng = np.random.default_rng(3)
    base = Monde('alt').image(1000.0, 1000.0, 3.0, 1800, 800)
    bruit = np.clip(base.astype(np.int16) + rng.integers(-30, 30, base.shape), 0, 255).astype(np.uint8)
    assert T.classer_mode(*T.mesurer_centre(base)) == 'alt' and T.classer_mode(*T.mesurer_centre(bruit)) == 'sat'
    modes = set()
    for part in np.linspace(0.0, 1.0, 41):
        im = base.copy()
        im[:, :int(1800 * part)] = bruit[:, :int(1800 * part)]
        a, s = T.mesurer_centre(im)
        mode = T.classer_mode(a, s)
        modes.add(mode)
        assert (mode == 'inconnu') == (0.15 < a < 0.30)
    assert modes == {'alt', 'inconnu', 'sat'}


def test_tu10_seuils_aux_bornes():
    assert T.classer_mode(0.15, 10) == 'sat' and T.classer_mode(0.1500001, 100) == 'inconnu'
    assert T.classer_mode(0.30, 80) == 'alt' and T.classer_mode(0.30, 79.9) == 'inconnu'
    assert T.classer_mode(0.30, 74.9) == 'map' and T.classer_mode(0.30, 75) == 'inconnu'


def test_mode_d_une_image_recadree_prend_le_centre_de_la_spec():
    img = np.zeros((1404, 3350, 3), np.uint8)
    img[:] = (220, 40, 40)
    assert T.mode_de_image(img)[0] == 'alt'
    assert T.mode_de_image(np.zeros((100, 100, 3), np.uint8))[0] == 'inconnu'          # trop petite pour le centre
    marque = img.copy()
    marque[:300, :] = 0                                  # hors du centre : sans effet
    marque[:, :800] = 255
    assert T.mesurer_centre(np.ascontiguousarray(marque[300:1100, 800:2600])) == T.mesurer_centre(
        np.ascontiguousarray(img[300:1100, 800:2600]))
    assert T.mode_de_image(marque)[1:] == T.mode_de_image(img)[1:]


# ------------------------------------------------------------------ TU9 : le plan, en ligne de commande

def _plan(cfg_, samples, capsys, theatre, nom_emprise, zoom=15):
    cfg_(ecran=(3440, 1440))
    code = ft.main(args('plan', theatre, zoom=zoom, emprise=samples / 'tuiles' / 'emprises' / nom_emprise))
    assert code == 0
    sortie = capsys.readouterr().out
    vals = {}
    for ligne in sortie.splitlines():
        for cle in ('altitude commandee', 'images', f'tuiles du zoom {zoom}'):
            if ligne.startswith(cle + ' :'):
                vals[cle] = ligne.split(':', 1)[1].split()[0]
    return sortie, vals


@pytest.mark.parametrize('theatre,nom,alt,tuiles,images', [('Syria', 'syria.geojson', 7637.6, 889132, 14803),
                                                           ('GermanyColdWar', 'germanycw.geojson', 6425.0, 1032264, 19455)])
def test_tu9_plan_zoom_15_sur_les_emprises(cfg, samples, capsys, theatre, nom, alt, tuiles, images):
    sortie, v = _plan(cfg, samples, capsys, theatre, nom)
    assert float(v['altitude commandee']) == pytest.approx(alt, abs=0.1), sortie
    assert int(v['tuiles du zoom 15']) == tuiles, sortie
    assert int(v['images']) == images, sortie
    _, w = ft.f10capture.CFG.work_dir, None
    grille = json.loads(next((ft.f10capture.CFG.work_dir / 'tuiles_brut').glob('*/grille.json')).read_text())
    assert grille['layer'] == 'alt' and grille['zoom'] == 15 and len(grille['cases']) == images
    assert grille['commanded_alt_m'] == pytest.approx(alt, abs=0.1)
    assert grille['usable_px'] == [3350, 1404] and grille['overlap_px'] == 32
