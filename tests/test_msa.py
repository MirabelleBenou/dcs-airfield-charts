"""MSA de chaque terrain (render/msa.py, render/build_msa.py) : le secteur d'un sommet (relevement du noeud vers le centre), la
declinaison, le rayon de 25 NM a 0,1 m pres, l'arrondi au pied, le seuil de montagne, le premier sommet a egalite, le refus d'un
disque incomplet, le fichier `dcs-msa-v1` et la ligne de commande. Donnees synthetiques seulement : aucun releve de DCS."""
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from render import build_msa, geo, msa, relief

N = 1001
C = 500                                    # le centre est le noeud (500, 500) : le disque (463 cases de rayon) tient dans la grille
X0 = Z0 = 0.0
XA = ZA = 50000.0


def plat(valeur=0.0, n=N):
    return np.full((n, n), valeur, np.float32)


def mesure(alt, xa=XA, za=ZA, gamma=0.0, decl=0.0, x0=X0, z0=Z0):
    return msa.msa_terrain(alt, x0, z0, xa, za, gamma, decl)


def secteur_du_sommet(r):
    """Les secteurs (0 a 3) dont la hauteur n'est pas celle du sol plat."""
    return [n for n, s in enumerate(r['sectors']) if s['terrain_max_m'] != 0.0]


# ------------------------------------------------------------------ le secteur d'un sommet : relevement du noeud vers le centre

@pytest.mark.parametrize('di, dj, k', [
    (+100, 0, 2),       # au Nord du centre : le relevement vers le centre est 180 -> secteur 180-270
    (0, +100, 3),       # a l'Est : 270 -> 270-360
    (-100, 0, 0),       # au Sud : 360 = 000 -> 000-090
    (0, -100, 1),       # a l'Ouest : 090 -> 090-180
    (+100, +100, 2),    # NE : 225
    (-100, +100, 3),    # SE : 315
    (-100, -100, 0),    # SO : 045
    (+100, -100, 1),    # NO : 135
])
def test_le_secteur_d_un_sommet_est_le_relevement_vers_le_centre(di, dj, k):
    alt = plat()
    alt[C + di, C + dj] = 800.0
    r = mesure(alt)
    assert secteur_du_sommet(r) == [k]
    s = r['sectors'][k]
    assert s['terrain_max_m'] == 800.0 and (s['from'], s['to']) == (90 * k, 90 * k + 90)
    assert (s['peak_x'], s['peak_y']) == (X0 + 100.0 * (C + di), Z0 + 100.0 * (C + dj))


def test_la_declinaison_deplace_un_sommet_d_un_secteur_a_l_autre():
    alt = plat()
    alt[C + 100, C + 100] = 800.0                      # relevement vers le centre : 225 pour gamma = D = 0
    attendu = {0.0: 2, -50.0: 3, -140.0: 0, 50.0: 1, 135.0: 1, 140.0: 0, 45.0: 2, 46.0: 1, -135.0: 0}
    for decl, k in attendu.items():                    # q = 225 - D
        assert secteur_du_sommet(mesure(alt, decl=decl)) == [k], decl


def test_la_convergence_joue_en_sens_inverse_de_la_declinaison():
    alt = plat()
    alt[C + 100, C + 100] = 800.0                      # q = 225 + gamma - D
    for gamma, decl in ((10.0, 10.0), (-7.5, -7.5), (33.0, 33.0)):
        assert mesure(alt, gamma=gamma, decl=decl) == mesure(alt)
    assert secteur_du_sommet(mesure(alt, gamma=50.0)) == [3]
    assert secteur_du_sommet(mesure(alt, gamma=-50.0)) == [1]
    assert secteur_du_sommet(mesure(alt, gamma=50.0, decl=100.0)) == [1]          # 225 + 50 - 100 = 175


def test_k_egal_4_par_l_arrondi_vaut_0():
    dx, dz = np.array([[0.0]]), np.array([[-100.0]])             # a l'Ouest : atan2 = -90 exactement
    q = (np.degrees(np.arctan2(dz, dx)) - 90.0 - 3e-14 + 180.0) % 360.0         # -2,8e-14 modulo 360 donne 360,0 en flottant
    assert q.tolist() == [[360.0]]
    assert msa.numero_secteur(dx, dz, -90.0, 3e-14).tolist() == [[0]]


def test_les_quatre_secteurs_sont_independants():
    alt = plat()
    hauteurs = {(+100, +100): 400.0, (-100, +100): 300.0, (-100, -100): 200.0, (+100, -100): 100.0}
    for (di, dj), h in hauteurs.items():
        alt[C + di, C + dj] = h
    r = mesure(alt)
    assert [s['terrain_max_m'] for s in r['sectors']] == [200.0, 100.0, 400.0, 300.0]
    assert r['msa_ft'] == max(s['msa_ft'] for s in r['sectors']) == msa.arrondir_msa_ft(400.0, 1000)
    assert [s['msa_ft'] for s in r['sectors']] == [msa.arrondir_msa_ft(h, 1000) for h in (200.0, 100.0, 400.0, 300.0)]


# ------------------------------------------------------------------ le rayon : 25 NM = 46 300,0 m, a 0,1 m pres

def test_un_noeud_a_46300_0_m_est_compte_a_46300_1_m_non():
    alt = plat()
    alt[C + 463, C] = 900.0                            # au Nord, dx = 46 300,0 m exactement : dans le disque
    r = mesure(alt)
    assert r['sectors'][2]['terrain_max_m'] == 900.0
    r = mesure(alt, xa=XA - 0.1)                       # dx = 46 300,1 m : hors du disque
    assert r['sectors'][2]['terrain_max_m'] == 0.0 and r['msa_ft'] == 1000
    r = mesure(alt, xa=XA + 0.1)                       # dx = 46 299,9 m : dans le disque
    assert r['sectors'][2]['terrain_max_m'] == 900.0
    assert msa.RAYON_M == 46300.0 == 25 * 1852.0
    # a l'Est comme au Sud et a l'Ouest : le rayon vaut dans les quatre directions
    for di, dj, k in ((0, 463, 3), (-463, 0, 0), (0, -463, 1)):
        a = plat()
        a[C + di, C + dj] = 900.0
        assert mesure(a)['sectors'][k]['terrain_max_m'] == 900.0
        b = plat()
        b[C + di, C + dj] = 900.0
        decalage = {(0, 463): (0.0, -0.1), (-463, 0): (0.1, 0.0), (0, -463): (0.0, 0.1)}[(di, dj)]       # le centre s'eloigne de 0,1 m
        assert mesure(b, xa=XA + decalage[0], za=ZA + decalage[1])['sectors'][k]['terrain_max_m'] == 0.0


def test_la_distance_est_hypot_pas_une_boite():
    alt = plat()
    alt[C + 330, C + 330] = 700.0                      # hypot(33 000, 33 000) = 46 669 m > 46 300 : hors du disque
    alt[C + 327, C + 327] = 650.0                      # hypot(32 700, 32 700) = 46 244 m : dans le disque
    r = mesure(alt)
    assert r['sectors'][2]['terrain_max_m'] == 650.0


def test_un_noeud_nan_hors_du_disque_ne_compte_pas_dans_le_disque_oui():
    alt = plat()
    alt[C + 463, C + 1] = np.nan                       # dx = 46 300, dz = 100 : hypot > 46 300 -> hors du disque
    mesure(alt)
    alt[C + 463, C] = np.nan                           # a 46 300,0 m : dans le disque
    with pytest.raises(msa.DisqueIncomplet) as e:
        mesure(alt)
    assert e.value.manquants == 1
    # un coin du carre englobant est NaN : hors du disque, pas de refus
    alt = plat()
    alt[C - 463, C - 463] = np.nan
    alt[C + 463, C + 463] = np.nan
    mesure(alt)


# ------------------------------------------------------------------ l'arrondi, le seuil de montagne

def test_arrondi_au_pied_une_valeur_juste_sur_une_centaine_reste():
    h = float(np.float32(304.8))                       # 304,79998... m : (h / 0,3048 + 1000) = 1999,99996 ft
    assert h < 304.8 and msa.arrondir_msa_ft(h, 1000) == 2000
    assert msa.arrondir_msa_ft(304.7, 1000) == 2000
    assert msa.arrondir_msa_ft(304.9, 1000) == 2100
    assert msa.arrondir_msa_ft(0.0, 1000) == 1000
    assert msa.arrondir_msa_ft(-5.0, 1000) == 1000        # sous le niveau de la mer : 1 000 - 16,4 = 983,6 -> 1 000
    alt = plat()
    alt[C + 50, C + 50] = np.float32(304.8)
    r = mesure(alt)
    assert r['sectors'][2]['terrain_max_m'] == 304.8 and r['sectors'][2]['msa_ft'] == 2000 and r['msa_ft'] == 2000
    alt[C + 50, C + 50] = np.float32(304.9)
    assert mesure(alt)['msa_ft'] == 2100


def test_seuil_de_montagne_a_1500_0_m_compris():
    alt = plat()
    alt[C + 50, C + 50] = np.float32(1500.0)
    s = mesure(alt)['sectors'][2]
    assert s['mountain'] is True and s['msa_ft'] == msa.arrondir_msa_ft(1500.0, 2000) == 7000
    alt[C + 50, C + 50] = np.float32(1499.9)
    s = mesure(alt)['sectors'][2]
    assert s['mountain'] is False and s['msa_ft'] == msa.arrondir_msa_ft(float(np.float32(1499.9)), 1000) == 6000
    alt[C + 50, C + 50] = np.float32(2500.0)
    assert mesure(alt)['sectors'][2]['mountain'] is True
    # les autres secteurs ne sont pas des montagnes
    assert [s['mountain'] for s in mesure(alt)['sectors']] == [False, False, True, False]


def test_terrain_max_est_la_valeur_float32_arrondie_au_dixieme():
    alt = plat()
    alt[C + 50, C + 50] = np.float32(123.456)
    s = mesure(alt)['sectors'][2]
    assert s['terrain_max_m'] == 123.5 and isinstance(s['terrain_max_m'], float)
    assert s['msa_ft'] == msa.arrondir_msa_ft(float(np.float32(123.456)), 1000)       # le calcul part de la valeur lue, non arrondie


# ------------------------------------------------------------------ le sommet : premier en (i, j)

def test_sommet_a_egalite_le_premier_en_i_puis_j():
    alt = plat()
    alt[C + 20, C + 60] = 500.0
    alt[C + 10, C + 70] = 500.0                         # i plus petit : c'est lui, meme avec un j plus grand
    s = mesure(alt)['sectors'][2]
    assert (s['peak_x'], s['peak_y']) == (X0 + 100.0 * (C + 10), Z0 + 100.0 * (C + 70))
    alt = plat()
    alt[C + 30, C + 100] = 500.0
    alt[C + 30, C + 80] = 500.0
    alt[C + 30, C + 90] = 500.0
    s = mesure(alt)['sectors'][2]
    assert (s['peak_x'], s['peak_y']) == (X0 + 100.0 * (C + 30), Z0 + 100.0 * (C + 80))
    # un maximum a egalite dans le secteur voisin n'y est pas compte
    alt[C - 30, C - 80] = 500.0
    r = mesure(alt)
    assert r['sectors'][2]['peak_y'] == Z0 + 100.0 * (C + 80) and (r['sectors'][0]['peak_x'], r['sectors'][0]['peak_y']) == \
        (X0 + 100.0 * (C - 30), Z0 + 100.0 * (C - 80))


def test_le_sommet_se_lit_avec_x0_z0_et_la_position_du_centre():
    alt = plat(n=1200)
    x0, z0 = -20000.0, 7000.0                           # grille decalee ; le centre au noeud (600, 600)
    xa, za = x0 + 60000.0, z0 + 60000.0
    alt[610, 640] = 321.0
    s = msa.msa_terrain(alt, x0, z0, xa, za, 0.0, 0.0)['sectors'][2]
    assert (s['peak_x'], s['peak_y']) == (x0 + 61000.0, z0 + 64000.0) and s['terrain_max_m'] == 321.0
    # un centre entre deux noeuds
    s = msa.msa_terrain(alt, x0, z0, xa + 37.5, za - 12.25, 0.0, 0.0)['sectors'][2]
    assert (s['peak_x'], s['peak_y']) == (x0 + 61000.0, z0 + 64000.0)


# ------------------------------------------------------------------ le disque incomplet est refuse

def disque_brut(x0, z0, ni, nj, xa, za, alt):
    """Nombre de noeuds du disque hors du tableau ou NaN, par une boucle sans numpy (reference)."""
    n = 0
    for i in range(math.floor((xa - 46300 - x0) / 100) - 2, math.ceil((xa + 46300 - x0) / 100) + 3):
        for j in range(math.floor((za - 46300 - z0) / 100) - 2, math.ceil((za + 46300 - z0) / 100) + 3):
            if math.hypot((x0 + 100 * i) - xa, (z0 + 100 * j) - za) <= 46300.0:
                if not (0 <= i < ni and 0 <= j < nj) or math.isnan(float(alt[i, j])):
                    n += 1
    return n


def test_refus_d_un_disque_hors_de_la_grille_ou_avec_des_trous():
    alt = plat(n=700)                                  # 70 km de cote : le disque (92,6 km) deborde de partout
    with pytest.raises(msa.DisqueIncomplet) as e:
        msa.msa_terrain(alt, 0.0, 0.0, 35000.0, 35000.0, 0.0, 0.0)
    assert e.value.manquants == disque_brut(0.0, 0.0, 700, 700, 35000.0, 35000.0, alt) > 0
    # un centre au bord de la grille : le demi-disque hors tableau est compte
    alt = plat()
    with pytest.raises(msa.DisqueIncomplet) as e:
        mesure(alt, xa=30000.0, za=50000.0)
    assert e.value.manquants == disque_brut(0.0, 0.0, N, N, 30000.0, 50000.0, alt)
    # un centre a l'exterieur de la grille
    with pytest.raises(msa.DisqueIncomplet):
        mesure(alt, xa=-90000.0, za=50000.0)
    with pytest.raises(msa.DisqueIncomplet):
        mesure(alt, xa=1e7, za=1e7)
    # des NaN dans le disque
    alt = plat()
    alt[C + 5, C + 5] = alt[C - 200, C + 300] = alt[C, C] = np.nan
    with pytest.raises(msa.DisqueIncomplet) as e:
        mesure(alt)
    assert e.value.manquants == 3 and '3 noeud(s)' in str(e.value)


def test_un_secteur_sans_noeud_est_refuse(monkeypatch):
    monkeypatch.setattr(msa, 'RAYON_M', 0.0)           # le disque ne contient plus que le centre : trois secteurs sont vides
    with pytest.raises(msa.DisqueIncomplet) as e:
        mesure(plat())
    assert e.value.manquants == 0 and len(e.value.secteurs_vides) == 3


# ------------------------------------------------------------------ une reference sans numpy

def reference(alt, x0, z0, xa, za, gamma, decl):
    """La regle du cadrage, noeud par noeud, en Python pur."""
    ni, nj = alt.shape
    h = [None] * 4
    for i in range(math.floor((xa - 46300 - x0) / 100) - 2, math.ceil((xa + 46300 - x0) / 100) + 3):
        for j in range(math.floor((za - 46300 - z0) / 100) - 2, math.ceil((za + 46300 - z0) / 100) + 3):
            dx, dz = (x0 + 100 * i) - xa, (z0 + 100 * j) - za
            if math.hypot(dx, dz) > 46300.0:
                continue
            assert 0 <= i < ni and 0 <= j < nj
            q = (math.degrees(math.atan2(dz, dx)) + gamma - decl + 180) % 360
            k = int(math.floor(q / 90)) % 4
            v = float(alt[i, j])
            if h[k] is None or v > h[k][0] or (v == h[k][0] and (i, j) < h[k][1]):
                h[k] = (v, (i, j))
    secteurs = []
    for k, (v, (i, j)) in enumerate(h):
        mont = v >= 1500.0
        secteurs.append({'from': 90 * k, 'to': 90 * k + 90, 'terrain_max_m': round(v, 1), 'peak_x': x0 + 100.0 * i,
                         'peak_y': z0 + 100.0 * j, 'mountain': mont,
                         'msa_ft': 100 * math.ceil(round((v / 0.3048 + (2000 if mont else 1000)) / 100, 6))})
    return {'msa_ft': max(s['msa_ft'] for s in secteurs), 'sectors': secteurs}


def test_le_calcul_vectoriel_egale_la_reference_noeud_par_noeud():
    rng = np.random.default_rng(11)
    i, j = np.meshgrid(np.arange(N), np.arange(N), indexing='ij')
    alt = (900 + 700 * np.sin(i / 90.0) * np.cos(j / 120.0) + 300 * rng.standard_normal((N, N))).astype(np.float32)
    alt = np.round(alt, 0).astype(np.float32)           # beaucoup d'egalites : le premier en (i, j) compte
    for xa, za, gamma, decl in ((50000.0, 50000.0, 0.0, 0.0), (49937.3, 50061.9, 1.7, -4.25), (50010.0, 49990.0, -2.3, 8.0)):
        assert msa.msa_terrain(alt, 0.0, 0.0, xa, za, gamma, decl) == reference(alt, 0.0, 0.0, xa, za, gamma, decl)


def test_deux_calculs_donnent_le_meme_resultat_avec_un_tableau_mmap(tmp_path):
    rng = np.random.default_rng(3)
    alt = (rng.random((N, N)) * 2000).astype(np.float32)
    np.save(tmp_path / 'g.npy', alt)
    m = np.load(tmp_path / 'g.npy', mmap_mode='r')
    assert msa.msa_terrain(m, 0.0, 0.0, XA, ZA, 3.0, 1.0) == msa.msa_terrain(alt, 0.0, 0.0, XA, ZA, 3.0, 1.0)


# ------------------------------------------------------------------ le fichier et la ligne de commande

@pytest.fixture(scope='module')
def projection():
    return geo.Projection.for_theatre('Syria')


GX0, GZ0 = -100000.0, 200000.0           # une grille de 100 km en Syrie, autour du terrain (xa, za) = (-50000, 250000)
TERRAINS = [{'airdromeId': 7, 'name': 'Alpha — é', 'x': -50000.0, 'y': 250000.0, 'parkings': [{'parking': 1}]},
            {'airdromeId': 3, 'name': 'Bravo', 'x': -49950.25, 'y': 250080.5, 'parkings': []}]


def fabriquer(tmp_path, terrains=TERRAINS, theatre='Syria', cle='Syria', graine=5):
    rng = np.random.default_rng(graine)
    i, j = np.meshgrid(np.arange(N), np.arange(N), indexing='ij')
    alt = np.clip(700 + 600 * np.sin(i / 130.0) * np.cos(j / 170.0) + 250 * np.sin((i + 2 * j) / 60.0) + 40 * rng.standard_normal((N, N)),
                  0.0, None)
    alt = np.round(alt, 1).astype(np.float32)
    g = relief.Grille(alt, GX0, GZ0)
    npy, _ = relief.ecrire_grille(g, tmp_path / 'grille', theatre, cle)
    pk = tmp_path / f'{cle}.json'
    pk.write_text(json.dumps({'format': 'dcs-parkings-v1', 'theatre': cle, 'source': 'x',
                              'airdromes': terrains}), encoding='utf-8')
    return g, npy, pk


def lancer(npy, pk, out, edition='test-ed', theatre='Syria', extra=()):
    return build_msa.main([theatre, '--grille', str(npy), '--parkings', str(pk), '--edition', edition, '--out', str(out), *extra])


def test_le_fichier_dcs_msa_v1(tmp_path, projection, capsys):
    g, npy, pk = fabriquer(tmp_path)
    out = tmp_path / 'msa'
    assert lancer(npy, pk, out) == 0
    brut = (out / 'Syria.json').read_bytes()
    texte = brut.decode('utf-8')
    lignes = texte.split('\n')
    assert lignes[-1] == '' and len(lignes) == 1 + 2 + 1 + 1                 # en-tete, deux terrains, fermeture, fin de ligne
    assert lignes[0].startswith('{"format":"dcs-msa-v1","theatre":"Syria","edition":"test-ed","source":{') and lignes[0].endswith('"airdromes":[')
    assert lignes[3] == ']}' and lignes[1].endswith(']},') and lignes[2].endswith(']}')
    doc = json.loads(texte)                                                   # json.load relit le fichier
    assert doc['format'] == 'dcs-msa-v1' and doc['theatre'] == 'Syria' and doc['edition'] == 'test-ed'
    assert doc['source'] == {'relief': 'DCS World terrain elevation read in the game, 100 m grid',
                             'relief_grid_sha256': hashlib.sha256(g.alt.tobytes()).hexdigest(),
                             'airfields': 'downloads/parkings/Syria.json (DCS mission editor)'}
    assert doc['rule'] == {'radius_nm': 25, 'centre': 'airfield reference point (DCS mission editor)',
                           'sectors': 'magnetic bearing to the centre: 000-090, 090-180, 180-270, 270-360',
                           'variation': 'IGRF-14 at the centre, 1985', 'margin_ft': 1000, 'mountain_margin_ft': 2000,
                           'mountain_threshold_m': 1500, 'rounding': 'up to the next 100 ft',
                           'terrain': 'ground only: buildings, masts and other obstacles are not included'}
    assert doc['attribution'] == ('Terrain: DCS World, Eagle Dynamics SA — terrain elevation read in the game. Non-commercial use only. '
                                  'Not for real-world navigation.')
    assert [(a['airdromeId'], a['name']) for a in doc['airdromes']] == [(7, 'Alpha — é'), (3, 'Bravo')]      # l'ordre du fichier des parkings
    assert 'Alpha — é' in texte                                                # ensure_ascii=False
    for t, a in zip(TERRAINS, doc['airdromes']):
        assert list(a) == ['airdromeId', 'name', 'x', 'y', 'variation_deg', 'msa_ft', 'sectors'] and (a['x'], a['y']) == (t['x'], t['y'])
        lat, lon = projection.to_latlon(t['x'], t['y'])
        d = geo.declination(float(lat), float(lon), 1985.0)
        assert a['variation_deg'] == round(d, 2)
        attendu = reference(g.alt, GX0, GZ0, t['x'], t['y'], projection.convergence(t['x'], t['y']), d)
        assert a['sectors'] == attendu['sectors'] and a['msa_ft'] == attendu['msa_ft'] == max(s['msa_ft'] for s in a['sectors'])
        assert len({s['msa_ft'] for s in a['sectors']}) > 1                      # des secteurs differents
        for s in a['sectors']:
            assert list(s) == ['from', 'to', 'terrain_max_m', 'peak_x', 'peak_y', 'mountain', 'msa_ft']
            assert isinstance(s['from'], int) and isinstance(s['msa_ft'], int) and isinstance(s['peak_x'], float)
    assert {s['mountain'] for a in doc['airdromes'] for s in a['sectors']} == {True, False}
    assert capsys.readouterr().out.startswith('Syria : 2 terrains')
    # deux passes : le meme fichier a l'octet
    out2 = tmp_path / 'msa2'
    assert lancer(npy, pk, out2) == 0
    assert (out2 / 'Syria.json').read_bytes() == brut
    # l'annee de la declinaison change le fichier et la regle
    out3 = tmp_path / 'msa3'
    assert lancer(npy, pk, out3, extra=['--annee', '2000']) == 0
    d3 = json.loads((out3 / 'Syria.json').read_text(encoding='utf-8'))
    assert d3['rule']['variation'] == 'IGRF-14 at the centre, 2000' and d3['airdromes'][0]['variation_deg'] != doc['airdromes'][0]['variation_deg']


def test_ligne_de_commande_codes_de_sortie(tmp_path, capsys):
    g, npy, pk = fabriquer(tmp_path)
    out = tmp_path / 'msa'
    assert build_msa.main([]) == 1
    assert lancer(npy, pk, out, theatre='Narnia') == 1
    assert lancer(tmp_path / 'absent.npy', pk, out) == 1
    assert build_msa.main(['Syria', '--grille', str(npy), '--parkings', str(pk), '--out', str(out)]) == 1            # --edition manque
    assert not out.exists()
    capsys.readouterr()
    # un terrain a moins de 25 NM du bord de la grille : refus, le terrain nomme, rien d'ecrit
    proche = TERRAINS + [{'airdromeId': 9, 'name': 'Bord', 'x': -99000.0, 'y': 250000.0, 'parkings': []}]
    g2, npy2, pk2 = fabriquer(tmp_path / 'b', terrains=proche)
    assert lancer(npy2, pk2, out) == 2
    err = capsys.readouterr().err
    assert 'Bord' in err and 'noeud(s)' in err and 'refus' in err
    assert not out.exists()
    # une grille ou des terrains d'un autre theatre
    assert lancer(npy, pk, out, theatre='GermanyColdWar') == 2
    assert 'pas de GermanyColdWar' in capsys.readouterr().err
    g3, npy3, pk3 = fabriquer(tmp_path / 'c', theatre='Caucasus', cle='Caucasus')
    assert lancer(npy3, pk, out) == 2 and 'Caucasus' in capsys.readouterr().err                   # la grille de Caucasus, pour Syria
    assert lancer(npy, pk3, out) == 2 and 'pas de Syria' in capsys.readouterr().err                # les terrains de Caucasus
    assert not out.exists()
    # une grille sans son .json voisin
    (tmp_path / 'grille' / 'relief_Syria.json').unlink()
    assert lancer(npy, pk, out) == 2 and 'description de la grille' in capsys.readouterr().err


def _refus_terrains(tmp_path, capsys, doc_terrains, nom='terrains.json'):
    """Lance `build_msa` avec un fichier de terrains donne (objet JSON ou texte) ; rend (code, stderr, sortie existe)."""
    g, npy, pk = fabriquer(tmp_path / 'g')
    pk2 = tmp_path / nom
    pk2.write_text(doc_terrains if isinstance(doc_terrains, str) else json.dumps(doc_terrains), encoding='utf-8')
    out = tmp_path / 'msa'
    capsys.readouterr()
    code = lancer(npy, pk2, out)
    return code, capsys.readouterr().err, out.exists()


def _bon(**changes):
    t = {'airdromeId': 7, 'name': 'Alpha', 'x': -50000.0, 'y': 250000.0, 'parkings': []}
    t.update(changes)
    return {k: v for k, v in t.items() if v is not ABSENT}


ABSENT = object()


@pytest.mark.parametrize('cas, doc, attendu', [
    ('cle absente', {'format': 'dcs-parkings-v1', 'theatre': 'Syria'}, 'airdromes'),
    ('pas une liste', {'theatre': 'Syria', 'airdromes': {'a': 1}}, 'airdromes'),
    ('liste nulle', {'theatre': 'Syria', 'airdromes': None}, 'airdromes'),
    ('pas un objet', [1, 2], 'objet JSON'),
    ('entree pas un objet', {'theatre': 'Syria', 'airdromes': [_bon(), 5]}, 'entree 2'),
    ('sans airdromeId', {'theatre': 'Syria', 'airdromes': [_bon(), _bon(airdromeId=ABSENT, name='Sans id')]}, r'entree 2 .*Sans id.*"airdromeId" absent'),
    ('sans name', {'theatre': 'Syria', 'airdromes': [_bon(name=ABSENT)]}, r'entree 1 .*airdromeId 7.*"name" absent'),
    ('sans x', {'theatre': 'Syria', 'airdromes': [_bon(x=ABSENT)]}, r'entree 1 .*"x" absent'),
    ('sans y', {'theatre': 'Syria', 'airdromes': [_bon(y=ABSENT)]}, r'entree 1 .*"y" absent'),
    ('x texte', {'theatre': 'Syria', 'airdromes': [_bon(x='12')]}, r'entree 1 .*"x" n\'est pas un nombre fini'),
    ('y nul', {'theatre': 'Syria', 'airdromes': [_bon(y=None)]}, r'entree 1 .*"y" n\'est pas un nombre fini'),
    ('x booleen', {'theatre': 'Syria', 'airdromes': [_bon(x=True)]}, r'"x" n\'est pas un nombre fini'),
    ('x non fini', '{"theatre": "Syria", "airdromes": [{"airdromeId": 1, "name": "N", "x": NaN, "y": 0}]}', r'entree 1 .*"x" n\'est pas un nombre fini'),
    ('y infini', '{"theatre": "Syria", "airdromes": [{"airdromeId": 1, "name": "N", "x": 0, "y": Infinity}]}', r'entree 1 .*"y" n\'est pas un nombre fini'),
])
def test_un_fichier_de_terrains_mal_forme_est_refuse_code_2(tmp_path, capsys, cas, doc, attendu):
    code, err, sortie = _refus_terrains(tmp_path, capsys, doc, nom='mes_terrains.json')
    assert code == 2, cas
    assert err.startswith('refus : ') and 'mes_terrains.json' in err and re.search(attendu, err), (cas, err)
    assert 'Traceback' not in err and err.count('\n') == 1 and not sortie


def test_un_fichier_de_terrains_bien_forme_passe_toujours(tmp_path, capsys):
    code, err, sortie = _refus_terrains(tmp_path, capsys, {'theatre': 'Syria', 'airdromes': [_bon(x=-50000, y=250000)]})
    assert code == 0 and err == '' and sortie                  # des entiers JSON sont des nombres
