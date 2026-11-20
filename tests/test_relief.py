"""Relief en tuiles web (render/relief.py, render/build_relief.py) : la grille et ses refus, l'ombrage « volumes », Terrain-RGB, les
reductions, l'independance du decoupage en blocs et du nombre de processus, le zip, la ligne de commande. Donnees synthetiques
seulement : aucun releve de DCS, aucun telechargement."""
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from render import build_relief, geo, relief
from render import tiles as T

X0, Z0 = -150037.0, 61013.0            # origine synthetique (en Syrie, hors de toute valeur ronde)
NI, NJ = 260, 260                      # 26 x 26 km : quelques tuiles z11
EMPRISE = (X0, Z0, X0 + 100.0 * (NI - 1), Z0 + 100.0 * (NJ - 1))


def terrain(ni=NI, nj=NJ, graine=7):
    rng = np.random.default_rng(graine)
    i, j = np.meshgrid(np.arange(ni), np.arange(nj), indexing='ij')
    h = 900 + 500 * np.sin(i / 23.0) * np.cos(j / 31.0) + 300 * np.sin((i + j) / 11.0) + 40 * rng.standard_normal((ni, nj))
    h[:60, :70] = 0.0                  # une mer plate dans un coin
    return np.round(np.clip(h, 0.0, None), 2)


def csv_bloc(alt, i0, i1, j0, j1, x0=X0, z0=Z0, ligne_en_trop=None):
    """Les octets d'un CSV du releve pour les cases [i0, i1) x [j0, j1) de `alt`."""
    lignes = ['i,j,x,z,alt_m,surface']
    for i in range(i0, i1):
        for j in range(j0, j1):
            lignes.append(f'{i - i0},{j - j0},{x0 + 100 * i:.1f},{z0 + 100 * j:.1f},{alt[i, j]:.2f},')
    if ligne_en_trop:
        lignes.append(ligne_en_trop)
    return ('\n'.join(lignes) + '\n').encode()


def ecrire_zip(chemin, membres):
    with zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED) as z:
        for nom, octets in membres.items():
            z.writestr(nom, octets)
    return chemin


@pytest.fixture(scope='module')
def alt():
    return terrain()


@pytest.fixture(scope='module')
def entrees(alt, tmp_path_factory):
    """Deux moities qui se partagent une colonne de bord, dans un zip avec un journal, et les memes en CSV nus."""
    d = tmp_path_factory.mktemp('entrees')
    gauche, droite = csv_bloc(alt, 0, NI, 0, 131), csv_bloc(alt, 0, NI, 130, NJ)
    z = ecrire_zip(d / 'nuit_1.zip', {'out/relief/Syria/test_r0_c0_100m_export.csv': gauche,
                                      'out/relief/Syria/test_r0_c1_100m_export.csv': droite,
                                      'out/journal.txt': b'journal de la nuit\n'})
    (d / 'a.csv').write_bytes(gauche)
    (d / 'b.csv').write_bytes(droite)
    return {'zip': z, 'csv': [d / 'a.csv', d / 'b.csv'], 'gauche': gauche, 'droite': droite}


def construire(entrees, out, niveaux=relief.NIVEAUX_DEFAUT, processus=1, emprise=EMPRISE, edition='test'):
    """Grille, tuiles et zip d'un theatre synthetique (projection de Syria). Retourne un dict."""
    g = relief.construire_grille([entrees['zip']], emprise=emprise)
    npy, _ = relief.ecrire_grille(g, out, 'Syria', 'Syria')
    res = relief.produire_tuiles(npy, 'Syria', g.x0, g.z0, emprise, out, niveaux=niveaux, processus=processus)
    info = relief.ecrire_zip(out, 'Syria', 'Syria', edition, res['bounds'], g.entrees)
    return {'out': Path(out), 'grille': g, 'res': res, 'zip': Path(out) / info['name'], 'info': info}


@pytest.fixture(scope='module')
def prod(entrees, tmp_path_factory):
    return construire(entrees, tmp_path_factory.mktemp('prod'))


def contenu(dossier):
    """{chemin relatif : octets} de toutes les tuiles sous `dossier/tiles`."""
    d = Path(dossier) / 'tiles'
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob('*')) if p.is_file()}


def lire(p):
    with Image.open(p) as im:
        return np.asarray(im.convert('RGBA')).copy()


# ------------------------------------------------------------------ R1 : la grille

def test_grille_depuis_le_zip_et_depuis_les_csv_nus(alt, entrees):
    gz = relief.construire_grille([entrees['zip']])
    gc = relief.construire_grille(entrees['csv'])
    assert gz.forme == [NI, NJ]
    assert (gz.x0, gz.z0, gz.pas_m) == (X0, Z0, 100.0)
    assert gz.alt.dtype == np.float32
    assert np.array_equal(gz.alt, alt.astype(np.float32))
    assert np.array_equal(gz.alt, gc.alt)
    assert gz.sha256_octets() == hashlib.sha256(alt.astype(np.float32).tobytes()).hexdigest()


def test_origine_plus_petit_x_et_z_quel_que_soit_l_ordre(alt, entrees):
    g = relief.construire_grille([entrees['csv'][1], entrees['csv'][0]])
    assert (g.x0, g.z0) == (X0, Z0)
    assert np.array_equal(g.alt, alt.astype(np.float32))


def test_les_entrees_portent_leur_sha256(entrees):
    g = relief.construire_grille([entrees['zip']])
    assert [e['sha256'] for e in g.entrees] == [hashlib.sha256(entrees['gauche']).hexdigest(),
                                                hashlib.sha256(entrees['droite']).hexdigest()]
    assert all(e['fichier'].startswith('nuit_1.zip::out/relief/Syria/') for e in g.entrees)      # jamais un chemin du disque
    assert not any('journal' in e['fichier'] for e in g.entrees)


def test_ecrire_grille(entrees, tmp_path):
    g = relief.construire_grille([entrees['zip']])
    npy, doc = relief.ecrire_grille(g, tmp_path / 'g', 'Syria', 'Syria')
    assert npy.name == 'relief_Syria.npy'
    a = np.load(npy)
    assert a.dtype == np.float32 and np.array_equal(a, g.alt)
    j = json.loads((tmp_path / 'g' / 'relief_Syria.json').read_text(encoding='utf-8'))
    assert j == doc
    assert (j['theatre'], j['cle'], j['x0'], j['z0'], j['pas_m'], j['forme']) == ('Syria', 'Syria', X0, Z0, 100.0, [NI, NJ])
    assert j['alt_min_m'] == 0.0 and j['alt_max_m'] == round(float(g.alt.max()), 2)
    assert len(j['entrees']) == 2 and all(len(e['sha256']) == 64 for e in j['entrees'])


# ------------------------------------------------------------------ R6 : les refus

def test_refus_point_hors_noeud(alt, tmp_path):
    mauvais = csv_bloc(alt, 0, 5, 0, 5).replace(b'-150037.0,61113.0', b'-150037.0,61113.5')
    p = tmp_path / 'm.csv'
    p.write_bytes(mauvais)
    with pytest.raises(relief.RefusDonnees, match=r'm\.csv ligne 3 .*ne tombe pas sur un noeud'):
        relief.construire_grille([p])


def test_un_point_a_moins_de_1_cm_d_un_noeud_est_accepte(alt, tmp_path):
    ok = csv_bloc(alt, 0, 5, 0, 5).replace(b'-150037.0,61113.0', b'-150037.0,61113.008')
    p = tmp_path / 'ok.csv'
    p.write_bytes(ok)
    g = relief.construire_grille([p])
    assert g.forme == [5, 5]


def test_refus_bord_commun_en_desaccord(alt, tmp_path):
    a = csv_bloc(alt, 0, 10, 0, 6)
    autre = alt.copy()
    autre[:, 5] += 0.5                                   # la colonne commune porte 0,5 m de plus dans le second carre
    b = csv_bloc(autre, 0, 10, 5, 12)
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(b)
    with pytest.raises(relief.RefusDonnees, match=r'b\.csv ligne \d+ .*deja donne avec une autre altitude'):
        relief.construire_grille([tmp_path / 'a.csv', tmp_path / 'b.csv'])


def test_bord_commun_d_accord_a_0_01_m_pres(alt, tmp_path):
    a = csv_bloc(alt, 0, 10, 0, 6)
    autre = alt.copy()
    autre[:, 5] += 0.01
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(csv_bloc(autre, 0, 10, 5, 12))
    g = relief.construire_grille([tmp_path / 'a.csv', tmp_path / 'b.csv'])
    assert g.forme == [10, 12]


def test_refus_deux_altitudes_dans_un_meme_fichier(alt, tmp_path):
    p = tmp_path / 'd.csv'
    p.write_bytes(csv_bloc(alt, 0, 4, 0, 4, ligne_en_trop='0,0,-150037.0,61013.0,5000.00,'))
    with pytest.raises(relief.RefusDonnees, match=r'd\.csv .*deux fois dans ce fichier'):
        relief.construire_grille([p])


def test_refus_noeud_manquant_dans_l_emprise(alt, tmp_path):
    lignes = csv_bloc(alt, 0, 30, 0, 30).split(b'\n')
    manquants = {b'%d,%d,' % (i, j) for i in (10, 11, 12) for j in (7, 8)}          # trou de 3 x 2 noeuds
    gardees = [l for l in lignes if not any(l.startswith(m) for m in manquants)]
    p = tmp_path / 'trou.csv'
    p.write_bytes(b'\n'.join(gardees))
    emprise = (X0, Z0, X0 + 2900, Z0 + 2900)
    with pytest.raises(relief.RefusDonnees) as e:
        relief.construire_grille([p], emprise=emprise)
    m = str(e.value)
    assert m.startswith('6 noeud(s)') and 'aucune entree' in m
    assert f'x de {X0 + 1000:g} a {X0 + 1200:g}' in m and f'z de {Z0 + 700:g} a {Z0 + 800:g}' in m
    relief.construire_grille([p])                                               # regle debrayee : accepte
    relief.construire_grille([p], emprise=(X0, Z0, X0 + 900, Z0 + 2900))        # le trou est hors de l'emprise : accepte


def test_refus_noeud_manquant_quand_l_emprise_deborde_de_la_grille(alt, tmp_path):
    p = tmp_path / 'p.csv'
    p.write_bytes(csv_bloc(alt, 0, 10, 0, 10))
    with pytest.raises(relief.RefusDonnees, match=r'noeud\(s\) .* aucune entree'):
        relief.construire_grille([p], emprise=(X0, Z0, X0 + 900, Z0 + 1500))
    relief.construire_grille([p], emprise=(X0, Z0, X0 + 900, Z0 + 900))


def test_refus_entete_et_valeurs(alt, tmp_path):
    p = tmp_path / 'e.csv'
    p.write_bytes(b'a,b,c\n1,2,3\n')
    with pytest.raises(relief.RefusDonnees, match='en-tete'):
        relief.construire_grille([p])
    p.write_bytes(b'i,j,x,z,alt_m,surface\n0,0,0.0,0.0,nan,\n0,1,0.0,100.0,5.0,\n')
    with pytest.raises(relief.RefusDonnees, match='ligne 2'):
        relief.construire_grille([p])


def test_refus_zip_sans_csv_ou_illisible(tmp_path):
    z = ecrire_zip(tmp_path / 'v.zip', {'journal.txt': b'rien'})
    with pytest.raises(relief.RefusDonnees, match='aucun CSV'):
        relief.construire_grille([z])
    (tmp_path / 'x.zip').write_bytes(b'pas un zip')
    with pytest.raises(relief.RefusDonnees, match='zip illisible'):
        relief.construire_grille([tmp_path / 'x.zip'])


def theatre_factice(monkeypatch, emprise=EMPRISE):
    monkeypatch.setattr(relief, 'emprise_theatre', lambda theatre: emprise)


def test_ligne_de_commande_codes_de_sortie(alt, entrees, tmp_path, monkeypatch, capsys):
    assert build_relief.main([]) == 1
    assert build_relief.main(['Narnia', '--entrees', str(entrees['zip']), '--edition', 'x', '--out', str(tmp_path)]) == 1
    assert build_relief.main(['Syria', '--entrees', str(tmp_path / 'absent.zip'), '--edition', 'x', '--out', str(tmp_path)]) == 1
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--edition', 'x', '--out', str(tmp_path),
                              '--processus', '0']) == 1
    capsys.readouterr()
    # un refus sur les donnees : code 2, la cause et le fichier nommes, rien d'ecrit
    p = tmp_path / 'mauvais.csv'
    p.write_bytes(csv_bloc(alt, 0, 5, 0, 5).replace(b'-150037.0,61113.0', b'-150037.0,61113.5'))
    theatre_factice(monkeypatch)
    assert build_relief.main(['Syria', '--entrees', str(p), '--edition', 'x', '--out', str(tmp_path / 'sortie')]) == 2
    err = capsys.readouterr().err
    assert 'mauvais.csv ligne 3' in err and 'noeud' in err
    assert not (tmp_path / 'sortie').exists()
    # le releve ne couvre pas l'emprise du theatre : code 2
    p.write_bytes(csv_bloc(alt, 0, 5, 0, 5))
    assert build_relief.main(['Syria', '--entrees', str(p), '--edition', 'x', '--out', str(tmp_path / 'sortie')]) == 2
    assert 'aucune entree' in capsys.readouterr().err


def test_ligne_de_commande_de_bout_en_bout(entrees, tmp_path, monkeypatch, capsys):
    theatre_factice(monkeypatch)
    out = tmp_path / 'sortie'
    stale = out / 'tiles' / 'hillshade' / '11' / '1' / '1.webp'
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'ancien')
    code = build_relief.main(['syria', '--entrees', str(entrees['zip']), '--edition', 'AC-V9.9.9', '--out', str(out),
                              '--processus', '2'])
    assert code == 0
    log = capsys.readouterr().out
    assert 'phase lecture et grille' in log and 'phase tuiles' in log and 'phase zip' in log and 'tuiles hillshade : z6' in log
    assert not stale.exists()                                   # les tuiles d'une passe precedente ne restent pas
    assert (out / 'relief_Syria.npy').is_file() and (out / 'DCS_Airfield_Charts_Syria_relief.zip').is_file()
    with zipfile.ZipFile(out / 'DCS_Airfield_Charts_Syria_relief.zip') as z:
        assert json.loads(z.read('relief.json'))['edition'] == 'AC-V9.9.9'


# ------------------------------------------------------------------ R5 : Terrain-RGB

def test_terrain_rgb_aller_retour():
    h = np.concatenate([np.linspace(-430.0, 5650.0, 70000), np.random.default_rng(1).uniform(-430, 5650, 20000), [0.0, -430.0, 5650.0]])
    h = h[:90000 - 90000 % 256].reshape(-1, 256)
    ok = np.ones(h.shape, bool)
    rgba = relief.encoder_terrain_rgb(h, ok)
    assert rgba.dtype == np.uint8 and (rgba[..., 3] == 255).all()
    assert np.abs(relief.decoder_terrain_rgb(rgba) - h).max() <= 0.05 + 1e-9
    assert relief.encoder_terrain_rgb(np.array([[0.0]]), np.array([[True]]))[0, 0].tolist() == [1, 134, 160, 255]    # 0 m : v = 100 000


def test_terrain_rgb_png_rgba_sans_palette_et_sans_perte():
    h = np.random.default_rng(2).uniform(-430, 5650, (256, 256))
    ok = np.ones(h.shape, bool)
    ok[:, :40] = False                                           # hors-carte : transparent
    rgba = relief.encoder_terrain_rgb(h, ok)
    assert (rgba[:, :40] == 0).all()
    data = relief.encoder(rgba, 'elevation')
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == 'PNG' and im.mode == 'RGBA' and im.size == (256, 256)
        assert im.getpalette() is None
        assert np.array_equal(np.asarray(im), rgba)


def test_hillshade_webp_sans_perte():
    rng = np.random.default_rng(3)
    rgba = rng.integers(0, 256, (256, 256, 4), dtype=np.uint8)
    rgba[rgba[..., 3] < 60] = 0
    with Image.open(io.BytesIO(relief.encoder(rgba, 'hillshade'))) as im:
        assert im.format == 'WEBP'
        assert np.array_equal(np.asarray(im.convert('RGBA')), rgba)


# ------------------------------------------------------------------ l'ombrage

def test_ombrage_le_plat_est_transparent():
    s = relief.ombrage_volumes(np.zeros((90, 90)))
    assert np.allclose(s, relief.COS_ZETA)
    t = relief.couleur_ombrage(s, np.ones(s.shape, bool))
    assert not t.any()                                           # alpha 0 -> (0, 0, 0, 0) : la mer est transparente


def test_couleur_ombrage_formules():
    v = np.ones((1, 3), bool)
    t = relief.couleur_ombrage(np.array([[relief.COS_ZETA - 0.5, relief.COS_ZETA + 0.3, relief.COS_ZETA - 0.9]]), v)
    assert t[0, 0].tolist() == [40, 32, 24, round(min(1, 1.6 * 0.5) * 0.70 * 255)]
    assert t[0, 1].tolist() == [255, 252, 240, round(min(1, 2.2 * 0.3) * 0.55 * 255)]
    assert t[0, 2].tolist() == [40, 32, 24, round(1.0 * 0.70 * 255)]                 # saturee : alpha 179
    hors = relief.couleur_ombrage(np.array([[relief.COS_ZETA - 0.5]]), np.zeros((1, 1), bool))
    assert hors[0, 0].tolist() == [0, 0, 0, 0]


def test_ombrage_orientation_nord_haut_soleil_au_nord_ouest():
    i, j = np.meshgrid(np.arange(60.0), np.arange(60.0), indexing='ij')
    monte_vers_l_est = 0.05 * 100 * j                              # pente de 5 % : la face regarde l'ouest
    monte_vers_l_ouest = -monte_vers_l_est
    monte_vers_le_nord = 0.05 * 100 * i                            # i : x vers le Nord ; la face regarde le sud
    monte_vers_le_sud = -monte_vers_le_nord
    s = {k: relief.ombrage(v, 3.0)[30, 30] for k, v in [('ouest', monte_vers_l_est), ('est', monte_vers_l_ouest),
                                                         ('sud', monte_vers_le_nord), ('nord', monte_vers_le_sud)]}
    assert s['ouest'] > relief.COS_ZETA > s['est']
    assert s['nord'] > relief.COS_ZETA > s['sud']
    assert s['ouest'] > s['nord'] > s['est'] and s['nord'] > s['sud']
    # valeur de la face ouest, a la main : omega = 270 degres, sigma = atan(3 x 0,05)
    sig = math.atan(0.15)
    attendu = sum(w * (math.cos(math.radians(45)) * math.cos(sig) + math.sin(math.radians(45)) * math.sin(sig)
                       * math.cos(math.radians(a) - math.radians(270))) for a, w in relief.AZIMUTS)
    assert s['ouest'] == pytest.approx(attendu, abs=1e-12)


def test_la_grille_lissee_est_un_gaussien_de_8_cases_tronque_a_4_sigma():
    e = np.zeros((101, 101))
    e[50, 50] = 1.0
    lisse = relief.ndimage.gaussian_filter(e, relief.SIGMA_CASES, truncate=relief.TRONC_SIGMA)
    assert lisse[50 + 32, 50] > 0 and lisse[50 + 33, 50] == 0 and lisse[50, 50 - 32] > 0 and lisse[50, 50 - 33] == 0
    assert relief.MARGE_CASES >= 32 + 1 + 1


# ------------------------------------------------------------------ l'echantillonnage et les tuiles z11

@pytest.fixture(scope='module')
def projection():
    return geo.Projection.for_theatre('Syria')


def tuiles_de_test(projection, emprise=EMPRISE):
    return [tuple(t) for t in T.tuiles_de_emprise(relief.contour_emprise(emprise, projection), relief.ZOOM_MAX).tolist()]


def test_un_plan_est_reproduit_exactement_par_l_interpolation(projection):
    """h(i, j) = 100 + 2 i + 3 j : l'interpolation bilineaire est exacte ; le pixel decode vaut h au point du sol, a 0,05 m pres."""
    i, j = np.meshgrid(np.arange(NI, dtype=np.float64), np.arange(NJ, dtype=np.float64), indexing='ij')
    plan = (100 + 2 * i + 3 * j).astype(np.float32)
    res = relief.rendre_tuiles(plan, X0, Z0, EMPRISE, projection, tuiles_de_test(projection))
    assert res['elevation']
    verifie = 0
    for (tx, ty), t in res['elevation'].items():
        X, Z = T.cartes_xz(relief.ZOOM_MAX, tx, ty, projection)
        fi, fj = (X - X0) / 100.0, (Z - Z0) / 100.0
        dedans = (fi >= 0) & (fi <= NI - 1) & (fj >= 0) & (fj <= NJ - 1)
        assert np.array_equal(t[..., 3] > 0, dedans)
        h = relief.decoder_terrain_rgb(t)
        assert np.abs(h[dedans] - (100 + 2 * fi[dedans] + 3 * fj[dedans])).max() <= 0.05 + 1e-3
        verifie += int(dedans.sum())
    assert verifie > 100000


def test_hors_emprise_et_hors_grille_est_transparent(alt, projection):
    emprise = (X0 + 3000, Z0 + 4000, X0 + 20000, Z0 + 15000)           # plus petite que la grille
    tuiles = tuiles_de_test(projection, EMPRISE)
    res = relief.rendre_tuiles(alt, X0, Z0, emprise, projection, tuiles)
    for couche in relief.COUCHES:
        for (tx, ty), t in res[couche].items():
            X, Z = T.cartes_xz(relief.ZOOM_MAX, tx, ty, projection)
            dehors = ~((X >= emprise[0]) & (X <= emprise[2]) & (Z >= emprise[1]) & (Z <= emprise[3]))
            assert not t[dehors].any()
    el = res['elevation']
    assert el and all((t[..., 3] > 0).any() for t in el.values())
    # des tuiles qui coupent la grille mais pas l'emprise sont absentes
    assert len(el) < len(tuiles)
    # au-dela de la grille : rien
    loin = relief.rendre_tuiles(alt, X0, Z0, (X0 - 5e5, Z0 - 5e5, X0 + 5e5, Z0 + 5e5), projection, [(1100, 800)])
    assert loin == {'hillshade': {}, 'elevation': {}}


def test_la_mer_a_0_m_est_opaque_en_elevation_et_transparente_en_ombrage(projection):
    plat = np.zeros((NI, NJ), np.float32)
    res = relief.rendre_tuiles(plat, X0, Z0, EMPRISE, projection, tuiles_de_test(projection))
    assert res['elevation'] and not res['hillshade']
    t = next(iter(res['elevation'].values()))
    assert (relief.decoder_terrain_rgb(t[t[..., 3] > 0]) == 0).all()


def test_une_tuile_ne_depend_pas_des_autres_ni_de_la_fenetre(alt, projection):
    tuiles = tuiles_de_test(projection)
    assert len(tuiles) >= 4
    ensemble = relief.rendre_tuiles(alt, X0, Z0, EMPRISE, projection, tuiles)
    for t in tuiles[::2]:
        seule = relief.rendre_tuiles(alt, X0, Z0, EMPRISE, projection, [t])
        for couche in relief.COUCHES:
            assert (t in seule[couche]) == (t in ensemble[couche])
            if t in seule[couche]:
                assert np.array_equal(seule[couche][t], ensemble[couche][t])


def test_contour_de_l_emprise_echantillonne_tous_les_1000_m(projection):
    poly = relief.contour_emprise((0.0, 0.0, 2500.0, 700.0), projection)
    assert len(poly.exterior.coords) - 1 == 3 + 1 + 3 + 1          # 2 500 m : 3 pas ; 700 m : 1 pas ; chaque cote deux fois
    lat, lon = projection.to_latlon(*[np.array(v) for v in ([0.0, 2500.0], [0.0, 700.0])])
    lon_o, lat_s, lon_e, lat_n = poly.bounds
    assert lat_s <= min(lat) + 1e-9 and lat_n >= max(lat) - 1e-9
    b = relief.bounds_leaflet(poly)
    assert b == [[round(lat_s, 6), round(lon_o, 6)], [round(lat_n, 6), round(lon_e, 6)]]


def test_emprise_des_theatres_est_celle_de_pydcs_dans_l_ordre_min_max():
    assert relief.emprise_theatre('Syria') == (-320000.0, -579986.0, 300000.0, 579998.0)
    assert relief.emprise_theatre('Caucasus') == (-600000.0, -560000.0, 380000.0, 1130000.0)
    x0, z0, x1, z1 = relief.emprise_theatre('GermanyColdWar')
    assert x0 < x1 and z0 < z1


def test_noms_des_theatres():
    assert relief.nom_dcs('germanycoldwar') == 'GermanyColdWar' and relief.cle_theatre('GermanyColdWar') == 'GermanyCW'
    assert relief.nom_dcs('SYRIA') == 'Syria' and relief.cle_theatre('Caucasus') == 'Caucasus'
    assert relief.nom_dcs('Persian Gulf') is None


# ------------------------------------------------------------------ R3 : le decoupage et les processus

def test_les_tuiles_ne_dependent_ni_des_blocs_ni_des_processus(entrees, prod, tmp_path, projection):
    z11 = tuiles_de_test(projection)
    assert len(relief.plan_blocs(z11, 1)) > 1 and len(relief.plan_blocs(z11, 2)) > 1
    de_reference = contenu(prod['out'])
    assert any(k.startswith('hillshade/11/') for k in de_reference) and any(k.startswith('elevation/6/') for k in de_reference)
    for niveaux, processus in ((2, 1), (3, 2), (1, 2)):
        b = construire(entrees, tmp_path / f'n{niveaux}p{processus}', niveaux=niveaux, processus=processus)
        assert contenu(b['out']) == de_reference, (niveaux, processus)
        assert b['zip'].read_bytes() == prod['zip'].read_bytes()


# ------------------------------------------------------------------ R4 : les reductions

def tuiles_ecrites(dossier, couche):
    ext = relief.EXTENSIONS[couche]
    out = {}
    for p in (Path(dossier) / 'tiles' / couche).glob(f'*/*/*.{ext}'):
        out[(int(p.parent.parent.name), int(p.parent.name), int(p.stem))] = lire(p)
    return out


def test_pyramide_hillshade_chaque_parent_est_la_reduction_de_ses_filles(prod):
    t = tuiles_ecrites(prod['out'], 'hillshade')
    assert {z for z, _, _ in t} == set(range(6, 12))
    for (z, x, y), parent in t.items():
        if z == relief.ZOOM_MAX:
            continue
        filles = {(dx, dy): t.get((z + 1, 2 * x + dx, 2 * y + dy)) for dx in (0, 1) for dy in (0, 1)}
        assert any(f is not None for f in filles.values())
        assert np.array_equal(parent, T.reduire(filles)), (z, x, y)
    for (z, x, y) in t:                                            # et chaque fille d'un niveau a son parent
        if z > relief.ZOOM_MIN:
            assert (z - 1, x >> 1, y >> 1) in t


def test_pyramide_elevation_moyenne_des_altitudes_decodees(prod):
    t = tuiles_ecrites(prod['out'], 'elevation')
    assert {z for z, _, _ in t} == set(range(6, 12))
    verifie = 0
    for (z, x, y), parent in t.items():
        if z == relief.ZOOM_MAX:
            continue
        hp = relief.decoder_terrain_rgb(parent)
        somme, n = np.zeros((256, 256)), np.zeros((256, 256))
        for dx in (0, 1):
            for dy in (0, 1):
                f = t.get((z + 1, 2 * x + dx, 2 * y + dy))
                if f is None:
                    continue
                h = relief.decoder_terrain_rgb(f)
                sub = np.zeros((512, 512))
                sub[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = np.nan_to_num(h)
                op = np.zeros((512, 512))
                op[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = (f[..., 3] > 0)
                somme += sub.reshape(256, 2, 256, 2).sum(axis=(1, 3))
                n += op.reshape(256, 2, 256, 2).sum(axis=(1, 3))
        attendu_opaque = n > 0
        assert np.array_equal(parent[..., 3] > 0, attendu_opaque)
        assert (parent[~attendu_opaque] == 0).all()
        assert np.abs(hp[attendu_opaque] - somme[attendu_opaque] / n[attendu_opaque]).max() <= 0.05 + 1e-6
        verifie += int(attendu_opaque.sum())
    assert verifie > 0


def test_aucune_tuile_ecrite_n_est_toute_transparente(prod):
    for couche in relief.COUCHES:
        for cle, t in tuiles_ecrites(prod['out'], couche).items():
            assert t[..., 3].any(), (couche, cle)


def test_reduire_elevation_cas_limites():
    def tuile(v, alpha=255):
        t = np.zeros((256, 256, 4), np.uint8)
        t[..., 0], t[..., 1], t[..., 2], t[..., 3] = (v >> 16) & 255, (v >> 8) & 255, v & 255, alpha
        return t
    assert relief.reduire_elevation({}) is None
    assert relief.reduire_elevation({(0, 0): None, (1, 1): np.zeros((256, 256, 4), np.uint8)}) is None
    r = relief.reduire_elevation({(0, 0): tuile(100000)})                       # une seule fille : le parent la reprend (a 4 pixels pres)
    assert (r[:128, :128, 3] == 255).all() and (r[128:, :, 3] == 0).all() and (r[:, 128:, 3] == 0).all()
    assert (relief.valeurs_terrain_rgb(r)[:128, :128] == 100000).all()
    r = relief.reduire_elevation({(0, 0): tuile(100000), (1, 0): tuile(100010)})
    assert (relief.valeurs_terrain_rgb(r)[:128, 128:] == 100010).all() and (relief.valeurs_terrain_rgb(r)[:128, :128] == 100000).all()
    # un pixel parent dont deux fils sur quatre sont opaques : la moyenne de ces deux-la seulement
    m = tuile(100000)
    m[0, 1, 3] = 0
    m[1, 0, 3] = 0
    m[1, 1, :3] = [1, 134, 190]
    r = relief.reduire_elevation({(0, 0): m})
    assert relief.valeurs_terrain_rgb(r)[0, 0] == round((100000 + 100030) / 2)


# ------------------------------------------------------------------ R7 : le zip

def test_zip_contenu_et_description(prod, entrees):
    z = zipfile.ZipFile(prod['zip'])
    noms = [i.filename for i in z.infolist()]
    assert noms == sorted(noms)
    assert all(i.compress_type == zipfile.ZIP_STORED and i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())
    assert 'relief.json' in noms and 'LICENSE-RELIEF.txt' in noms
    tuiles = [n for n in noms if n not in ('relief.json', 'LICENSE-RELIEF.txt')]
    assert all(n.split('/')[0] in ('hillshade', 'elevation') for n in tuiles)
    assert all(n.endswith('.webp') for n in tuiles if n.startswith('hillshade/')) and all(n.endswith('.png') for n in tuiles if n.startswith('elevation/'))
    sur_disque = contenu(prod['out'])
    assert sorted(tuiles) == sorted(sur_disque)
    for n in tuiles[::7]:
        assert z.read(n) == sur_disque[n]
    d = json.loads(z.read('relief.json'))
    assert d['format'] == 'dcs-relief-tiles-v1' and d['theatre'] == 'Syria' and d['edition'] == 'test'
    assert (d['minZoom'], d['maxNativeZoom'], d['tileSize']) == (6, 11, 256)
    assert d['attribution'] == 'Relief: DCS World, Eagle Dynamics SA — terrain elevation read in the game. Non-commercial use only.'
    (s, o), (n, e) = d['bounds']
    assert s < n and o < e and 30 < s < 40 and 30 < o < 40
    assert d['bounds'] == relief.bounds_leaflet(relief.contour_emprise(EMPRISE, geo.Projection.for_theatre('Syria')))
    assert d['layers'] == {'hillshade': {'path': 'hillshade/{z}/{x}/{y}.webp'},
                           'elevation': {'path': 'elevation/{z}/{x}/{y}.png', 'encoding': 'terrain-rgb', 'base_m': -10000, 'step_m': 0.1}}
    assert d['source'] == [{'file': e['fichier'], 'sha256': e['sha256']} for e in prod['grille'].entrees]
    comptes = {c: {} for c in relief.COUCHES}
    for n_ in tuiles:
        c, zz = n_.split('/')[0], n_.split('/')[1]
        comptes[c][zz] = comptes[c].get(zz, 0) + 1
    assert d['tiles_per_zoom'] == comptes
    assert list(d['tiles_per_zoom']['hillshade']) == ['6', '7', '8', '9', '10', '11']
    lic = z.read('LICENSE-RELIEF.txt').decode('utf-8')
    assert lic.startswith(d['attribution'] + '\n') and 'not covered by the MIT licence' in lic


def test_zip_sidecar(prod):
    j = json.loads(Path(str(prod['zip']) + '.json').read_text(encoding='utf-8'))
    assert j['name'] == 'DCS_Airfield_Charts_Syria_relief.zip'
    assert j['bytes'] == prod['zip'].stat().st_size
    assert j['sha256'] == hashlib.sha256(prod['zip'].read_bytes()).hexdigest()
    assert j['tiles_per_zoom'] == json.loads(zipfile.ZipFile(prod['zip']).read('relief.json'))['tiles_per_zoom']


def test_deux_passes_identiques_donnent_le_meme_zip(entrees, prod, tmp_path):
    b = construire(entrees, tmp_path / 'deuxieme')
    assert b['zip'].read_bytes() == prod['zip'].read_bytes()
    assert Path(str(b['zip']) + '.json').read_bytes() == Path(str(prod['zip']) + '.json').read_bytes()
