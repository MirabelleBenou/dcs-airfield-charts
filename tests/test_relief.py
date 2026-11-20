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
from scipy import ndimage

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
    g = relief.construire_grille([entrees['zip']], 'Syria', emprise=emprise)
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
    gz = relief.construire_grille([entrees['zip']], 'Syria')
    gc = relief.construire_grille(entrees['csv'], 'Syria')
    assert gz.forme == [NI, NJ]
    assert (gz.x0, gz.z0, gz.pas_m) == (X0, Z0, 100.0)
    assert gz.alt.dtype == np.float32
    assert np.array_equal(gz.alt, alt.astype(np.float32))
    assert np.array_equal(gz.alt, gc.alt)
    assert gz.sha256_octets() == hashlib.sha256(alt.astype(np.float32).tobytes()).hexdigest()


def test_origine_plus_petit_x_et_z_quel_que_soit_l_ordre(alt, entrees):
    g = relief.construire_grille([entrees['csv'][1], entrees['csv'][0]], 'Syria')
    assert (g.x0, g.z0) == (X0, Z0)
    assert np.array_equal(g.alt, alt.astype(np.float32))


def test_les_entrees_portent_leur_sha256(entrees):
    g = relief.construire_grille([entrees['zip']], 'Syria')
    assert [e['sha256'] for e in g.entrees] == [hashlib.sha256(entrees['gauche']).hexdigest(),
                                                hashlib.sha256(entrees['droite']).hexdigest()]
    assert all(e['fichier'].startswith('nuit_1.zip::out/relief/Syria/') for e in g.entrees)      # jamais un chemin du disque
    assert not any('journal' in e['fichier'] for e in g.entrees)


def test_ecrire_grille(entrees, tmp_path):
    g = relief.construire_grille([entrees['zip']], 'Syria')
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
        relief.construire_grille([p], 'Syria')


def test_un_point_a_moins_de_1_cm_d_un_noeud_est_accepte(alt, tmp_path):
    ok = csv_bloc(alt, 0, 5, 0, 5).replace(b'-150037.0,61113.0', b'-150037.0,61113.008')
    p = tmp_path / 'ok.csv'
    p.write_bytes(ok)
    g = relief.construire_grille([p], 'Syria')
    assert g.forme == [5, 5]


def test_refus_bord_commun_en_desaccord(alt, tmp_path):
    a = csv_bloc(alt, 0, 10, 0, 6)
    autre = alt.copy()
    autre[:, 5] += 0.5                                   # la colonne commune porte 0,5 m de plus dans le second carre
    b = csv_bloc(autre, 0, 10, 5, 12)
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(b)
    with pytest.raises(relief.RefusDonnees, match=r'b\.csv ligne \d+ .*deja donne avec une autre altitude'):
        relief.construire_grille([tmp_path / 'a.csv', tmp_path / 'b.csv'], 'Syria')


def test_bord_commun_d_accord_a_0_01_m_pres(alt, tmp_path):
    a = csv_bloc(alt, 0, 10, 0, 6)
    autre = alt.copy()
    autre[:, 5] += 0.01
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(csv_bloc(autre, 0, 10, 5, 12))
    g = relief.construire_grille([tmp_path / 'a.csv', tmp_path / 'b.csv'], 'Syria')
    assert g.forme == [10, 12]


def test_refus_deux_altitudes_dans_un_meme_fichier(alt, tmp_path):
    p = tmp_path / 'd.csv'
    p.write_bytes(csv_bloc(alt, 0, 4, 0, 4, ligne_en_trop='0,0,-150037.0,61013.0,5000.00,'))
    with pytest.raises(relief.RefusDonnees, match=r'd\.csv .*deux fois dans ce fichier'):
        relief.construire_grille([p], 'Syria')


def test_refus_noeud_manquant_dans_l_emprise(alt, tmp_path):
    lignes = csv_bloc(alt, 0, 30, 0, 30).split(b'\n')
    manquants = {b'%d,%d,' % (i, j) for i in (10, 11, 12) for j in (7, 8)}          # trou de 3 x 2 noeuds
    gardees = [l for l in lignes if not any(l.startswith(m) for m in manquants)]
    p = tmp_path / 'trou.csv'
    p.write_bytes(b'\n'.join(gardees))
    emprise = (X0, Z0, X0 + 2900, Z0 + 2900)
    with pytest.raises(relief.RefusDonnees) as e:
        relief.construire_grille([p], 'Syria', emprise=emprise)
    m = str(e.value)
    assert m.startswith('6 noeud(s)') and 'aucune entree' in m
    assert f'x de {X0 + 1000:g} a {X0 + 1200:g}' in m and f'z de {Z0 + 700:g} a {Z0 + 800:g}' in m
    relief.construire_grille([p], 'Syria')                                               # regle debrayee : accepte
    relief.construire_grille([p], 'Syria', emprise=(X0, Z0, X0 + 900, Z0 + 2900))        # le trou est hors de l'emprise : accepte


def test_refus_noeud_manquant_quand_l_emprise_deborde_de_la_grille(alt, tmp_path):
    p = tmp_path / 'p.csv'
    p.write_bytes(csv_bloc(alt, 0, 10, 0, 10))
    with pytest.raises(relief.RefusDonnees, match=r'noeud\(s\) .* aucune entree'):
        relief.construire_grille([p], 'Syria', emprise=(X0, Z0, X0 + 900, Z0 + 1500))
    relief.construire_grille([p], 'Syria', emprise=(X0, Z0, X0 + 900, Z0 + 900))


def test_refus_entete_et_valeurs(alt, tmp_path):
    p = tmp_path / 'e.csv'
    p.write_bytes(b'a,b,c\n1,2,3\n')
    with pytest.raises(relief.RefusDonnees, match='en-tete'):
        relief.construire_grille([p], 'Syria')
    p.write_bytes(b'i,j,x,z,alt_m,surface\n0,0,0.0,0.0,nan,\n0,1,0.0,100.0,5.0,\n')
    with pytest.raises(relief.RefusDonnees, match='ligne 2'):
        relief.construire_grille([p], 'Syria')


def test_refus_zip_sans_csv_ou_illisible(tmp_path):
    z = ecrire_zip(tmp_path / 'v.zip', {'journal.txt': b'rien'})
    with pytest.raises(relief.RefusDonnees, match='aucun CSV'):
        relief.construire_grille([z], 'Syria')
    (tmp_path / 'x.zip').write_bytes(b'pas un zip')
    with pytest.raises(relief.RefusDonnees, match='zip illisible'):
        relief.construire_grille([tmp_path / 'x.zip'], 'Syria')


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


# ================================================================== lot ac-msa-v1 : correctifs, zones non relevees, --grille-seule

def retirer_lignes(octets, i_noeuds, j_noeuds):
    """Les octets d'un CSV sans les lignes des noeuds (i, j) du CSV (colonnes i et j du fichier)."""
    ex = {f'{i},{j},' for i in i_noeuds for j in j_noeuds}
    return b'\n'.join(l for l in octets.split(b'\n') if not any(l.startswith(e.encode()) for e in ex))


# ------------------------------------------------------------------ correctif 1 : les membres d'un zip

def test_membres_retenus_seuls_les_csv_du_theatre():
    ok = 'out/relief/Syria/syria_r0_c0_100m_export.csv'
    assert relief.membre_retenu(ok, 'Syria') and relief.membre_retenu(ok, 'syria')
    for autre in ('out/relief/GermanyColdWar/syria_r0_c0_100m_export.csv',        # un autre theatre
                  'out/relief/Syria/syria_r0_c0_100m_export.json',                # pas un CSV
                  'out/relief/Syria/syria_r0_c0.csv',                             # pas un export de 100 m
                  'out/relief/Syria/sous/syria_r0_c0_100m_export.csv',            # pas directement sous le dossier
                  'out/relief/syria/syria_r0_c0_100m_export.csv',                 # le nom DCS exact
                  'out/syria_r0_c0_100m_export.csv', 'autonome/out/relief/Syria/a_100m_export.csv',
                  'out/relief/Syria/_100m_export.csv', 'out/relief/Syria/'):
        assert not relief.membre_retenu(autre, 'Syria'), autre
    assert relief.membre_retenu('out/relief/GermanyColdWar/g_100m_export.csv', 'GermanyColdWar')
    assert not relief.membre_retenu(ok, 'GermanyColdWar')


def test_un_membre_etranger_d_un_zip_est_ignore(alt, entrees, tmp_path):
    """Des CSV d'un autre theatre, d'un autre dossier ou d'un autre nom, qui contrediraient la grille, ne sont pas lus."""
    faux = csv_bloc(alt + 1000.0, 0, NI, 0, NJ)
    z = ecrire_zip(tmp_path / 'etranger.zip', {
        'out/relief/Syria/test_r0_c0_100m_export.csv': entrees['gauche'],
        'out/relief/Syria/test_r0_c1_100m_export.csv': entrees['droite'],
        'out/relief/GermanyColdWar/faux_100m_export.csv': faux,
        'autonome/relief/Syria/faux_100m_export.csv': faux,
        'out/relief/Syria/sous/faux_100m_export.csv': faux,
        'out/relief/Syria/faux.csv': faux,
        'out/relief/Syria/test_r0_c0_100m_export.json': b'{}',
        'out/journal.txt': b'journal'})
    g = relief.construire_grille([z], 'Syria')
    assert np.array_equal(g.alt, alt.astype(np.float32))
    assert [e['fichier'].split('::')[1] for e in g.entrees] == ['out/relief/Syria/test_r0_c0_100m_export.csv',
                                                                 'out/relief/Syria/test_r0_c1_100m_export.csv']
    # le meme zip, lu pour un autre theatre : ses CSV ne sont pas ceux-la
    g2 = relief.construire_grille([z], 'GermanyColdWar')
    assert [e['fichier'].split('::')[1] for e in g2.entrees] == ['out/relief/GermanyColdWar/faux_100m_export.csv']


def test_zip_sans_csv_retenu_est_refuse(alt, entrees, tmp_path, monkeypatch, capsys):
    z = ecrire_zip(tmp_path / 'autre.zip', {'out/relief/GermanyColdWar/g_100m_export.csv': entrees['gauche'],
                                            'out/relief/Syria/syria_r0_c0_100m_export.json': b'{}',
                                            'journal.txt': b'rien'})
    with pytest.raises(relief.RefusDonnees, match='aucun CSV retenu'):
        relief.construire_grille([z], 'Syria')
    theatre_factice(monkeypatch)
    assert build_relief.main(['Syria', '--entrees', str(z), '--edition', 'x', '--out', str(tmp_path / 'sortie')]) == 2
    assert 'aucun CSV retenu' in capsys.readouterr().err
    assert not (tmp_path / 'sortie').exists()
    # un .csv nu reste lu tel quel, quel que soit son nom
    nu = tmp_path / 'nimporte_quoi.csv'
    nu.write_bytes(entrees['gauche'])
    assert relief.construire_grille([nu], 'Syria').forme == [NI, 131]


# ------------------------------------------------------------------ correctif 2 : le numero de ligne du fichier

def avec_lignes_vides(octets, apres):
    """`octets` avec une ligne vide apres chacune des lignes du fichier (numeros a partir de 1) de `apres`."""
    lignes = octets.split(b'\n')
    out = []
    for k, l in enumerate(lignes, start=1):
        out.append(l)
        if k in apres:
            out.append(b'')
    return b'\n'.join(out)


def test_numero_de_ligne_du_fichier_lignes_vides_comprises(alt, tmp_path):
    base = csv_bloc(alt, 0, 6, 0, 6)
    avec = avec_lignes_vides(base, {2, 3, 4, 20, 21})        # des lignes vides entre les lignes de donnees
    lignes = avec.split(b'\n')
    attendu = [k for k, l in enumerate(lignes, start=1) if k > 1 and l]        # les numeros du fichier des lignes de donnees
    assert len(attendu) == 36 and attendu[:4] == [2, 4, 6, 8]
    assert [relief.numero_de_ligne(avec, n) for n in range(36)] == attendu
    assert relief.numero_de_ligne(avec, 36) is None
    # un point hors noeud : le refus donne la ligne du fichier, pas le rang parmi les lignes non vides
    cible = next(k for k, l in enumerate(lignes, start=1) if l.startswith(b'1,2,'))          # la ligne du noeud (1, 2)
    lignes[cible - 1] = lignes[cible - 1].replace(b',61213.0,', b',61213.5,')
    p = tmp_path / 'v.csv'
    p.write_bytes(b'\n'.join(lignes))
    with pytest.raises(relief.RefusDonnees, match=rf'v\.csv ligne {cible} : .*ne tombe pas sur un noeud'):
        relief.construire_grille([p], 'Syria')
    assert cible == attendu[1 * 6 + 2]                        # = 3 lignes vides de plus que le rang de numpy (n + 2)
    assert cible > (1 * 6 + 2) + 2


def test_numero_de_ligne_dans_chaque_refus(alt, tmp_path):
    base = csv_bloc(alt, 0, 8, 0, 8)
    lignes0 = base.split(b'\n')
    # 1. une valeur non numerique ; 2. une valeur non finie ; 3. un noeud donne deux fois avec deux altitudes (meme fichier)
    for nom, modif, motif in (('texte', lambda l: l.replace(b',61313.0,', b',abc,'), 'ligne illisible'),
                              ('nan', lambda l: l.rsplit(b',', 2)[0] + b',nan,', 'valeur absente ou non numerique')):
        lignes = list(lignes0)
        k = next(n for n, l in enumerate(lignes) if l.startswith(b'3,3,'))
        lignes[k] = modif(lignes[k])
        contenu = avec_lignes_vides(b'\n'.join(lignes), {1, 2, 5, k})        # des lignes vides avant et apres la ligne en cause
        numero = next(n for n, l in enumerate(contenu.split(b'\n'), start=1) if l.startswith((b'3,3,')))
        p = tmp_path / f'{nom}.csv'
        p.write_bytes(contenu)
        with pytest.raises(relief.RefusDonnees, match=rf'{nom}\.csv ligne {numero} .*{motif}'):
            relief.construire_grille([p], 'Syria')
        assert numero > k + 1                                                  # le rang de numpy aurait donne un numero plus petit
    # deux altitudes pour un noeud, dans un meme fichier : c'est la ligne en trop qui est nommee
    contenu = avec_lignes_vides(base.rstrip(b'\n') + b'\n3,3,-149737.0,61313.0,9999.00,\n', {1, 2, 3, 4, 5})
    numero = len(contenu.rstrip(b'\n').split(b'\n'))
    p = tmp_path / 'double.csv'
    p.write_bytes(contenu)
    with pytest.raises(relief.RefusDonnees, match=rf'double\.csv ligne {numero} .*deux fois dans ce fichier'):
        relief.construire_grille([p], 'Syria')
    # un bord commun en desaccord avec un fichier precedent
    a = csv_bloc(alt, 0, 10, 0, 6)
    autre = alt.copy()
    autre[4, 5] += 0.5
    b = avec_lignes_vides(csv_bloc(autre, 0, 10, 5, 12), {1, 2, 3, 50, 51})
    numero = next(n for n, l in enumerate(b.split(b'\n'), start=1) if l.startswith(b'4,0,'))
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(b)
    with pytest.raises(relief.RefusDonnees, match=rf'b\.csv ligne {numero} .*deja donne avec une autre altitude'):
        relief.construire_grille([tmp_path / 'a.csv', tmp_path / 'b.csv'], 'Syria')


def test_lignes_vides_sans_erreur_ne_changent_pas_la_grille(alt, tmp_path):
    base = csv_bloc(alt, 0, 12, 0, 12)
    p = tmp_path / 'ok.csv'
    p.write_bytes(avec_lignes_vides(base, {2, 3, 40, 100}) + b'\n\n')
    q = tmp_path / 'sans.csv'
    q.write_bytes(base)
    assert np.array_equal(relief.construire_grille([p], 'Syria').alt, relief.construire_grille([q], 'Syria').alt)


# ------------------------------------------------------------------ correctif 3 : la premiere valeur lue est gardee

def test_un_noeud_lu_deux_fois_garde_la_premiere_valeur_lue(tmp_path):
    def csv(lignes):
        return ('i,j,x,z,alt_m,surface\n' + '\n'.join(lignes) + '\n').encode()

    def pts(j0, valeurs):
        """Deux lignes de noeuds (x = X0 et X0 + 100), colonnes j0, j0 + 1... ; valeurs[(ligne, col)]."""
        return [f'0,0,{X0 + 100 * r},{Z0 + 100 * (j0 + c)},{valeurs[(r, c)]},' for r in (0, 1) for c in range(len(valeurs) // 2)]
    a = csv(pts(0, {(0, 0): '100.00', (0, 1): '200.00', (1, 0): '101.00', (1, 1): '201.00'}))
    b = csv(pts(1, {(0, 0): '200.008', (0, 1): '300.00', (1, 0): '201.008', (1, 1): '301.00'}))        # bord commun : colonne 1
    (tmp_path / 'a.csv').write_bytes(a)
    (tmp_path / 'b.csv').write_bytes(b)
    # ordre des libelles tries : a.csv puis b.csv, quel que soit l'ordre des entrees
    for ordre in (['a.csv', 'b.csv'], ['b.csv', 'a.csv']):
        g = relief.construire_grille([tmp_path / n for n in ordre], 'Syria')
        assert g.forme == [2, 3]
        assert g.alt[0, 1] == np.float32(200.00) and g.alt[1, 1] == np.float32(201.00)
        assert g.alt[0, 2] == np.float32(300.00)
    # l'ordre est celui des libelles, pas celui des entrees : renomme, a passe apres b
    (tmp_path / 'a.csv').rename(tmp_path / 'z.csv')
    g = relief.construire_grille([tmp_path / 'z.csv', tmp_path / 'b.csv'], 'Syria')
    assert g.alt[0, 1] == np.float32(200.008) and g.alt[1, 1] == np.float32(201.008)
    # dans un meme fichier, la premiere ligne du noeud (a 0,01 m pres) donne sa valeur
    c = csv([f'0,0,{X0},{Z0},100.00,', f'0,1,{X0},{Z0 + 100},50.00,', f'0,1,{X0},{Z0 + 100},50.008,',
             f'0,1,{X0},{Z0 + 100},50.003,', f'1,0,{X0 + 100},{Z0},70.00,', f'1,1,{X0 + 100},{Z0 + 100},71.00,'])
    (tmp_path / 'c.csv').write_bytes(c)
    g = relief.construire_grille([tmp_path / 'c.csv'], 'Syria')
    assert g.forme == [2, 2] and g.alt[0, 1] == np.float32(50.00)
    # et un noeud deja donne par un fichier precedent ne bouge pas, meme si le suivant le donne plusieurs fois
    d = csv([f'0,0,{X0},{Z0},100.00,', f'0,1,{X0},{Z0 + 100},50.005,', f'1,0,{X0 + 100},{Z0},70.00,', f'1,1,{X0 + 100},{Z0 + 100},71.00,'])
    (tmp_path / 'a0.csv').write_bytes(d)
    g = relief.construire_grille([tmp_path / 'a0.csv', tmp_path / 'c.csv'], 'Syria')
    assert g.alt[0, 1] == np.float32(50.005)


# ------------------------------------------------------------------ correctif 4 : l'effacement des tuiles n'avale plus d'erreur

def test_un_dossier_de_tuiles_absent_passe(entrees, tmp_path):
    b = construire(entrees, tmp_path / 'neuf')                 # ni tiles/, ni sortie : la passe se fait
    assert b['res']['compte']['hillshade'][relief.ZOOM_MAX] > 0
    relief.effacer_tuiles(tmp_path / 'rien' / 'du' / 'tout')   # dossier de sortie absent : passe
    (tmp_path / 'demi' / 'tiles' / 'elevation').mkdir(parents=True)
    relief.effacer_tuiles(tmp_path / 'demi')                   # une seule couche presente
    assert not (tmp_path / 'demi' / 'tiles' / 'elevation').exists()


def test_un_dossier_qu_on_ne_peut_pas_effacer_arrete_la_passe(entrees, alt, tmp_path, monkeypatch, capsys):
    # un fichier a la place du dossier d'une couche : l'effacement echoue, quel que soit l'utilisateur
    out = tmp_path / 'sortie'
    (out / 'tiles').mkdir(parents=True)
    (out / 'tiles' / 'hillshade').write_bytes(b'je ne suis pas un dossier')
    with pytest.raises(relief.ErreurSortie, match=r'hillshade .*arretee'):
        relief.effacer_tuiles(out)
    # un refus du systeme : l'erreur n'est pas avalee (pas d'`ignore_errors`) et nomme le dossier
    vus = []

    def boum(d, *a, **k):
        vus.append((Path(d), k))
        raise PermissionError(13, 'fichier verrouille', str(d))
    monkeypatch.setattr(relief.shutil, 'rmtree', boum)
    with pytest.raises(relief.ErreurSortie, match=r'verrouille'):
        relief.effacer_tuiles(tmp_path / 'autre')
    assert vus and vus[0][0].name == 'hillshade' and all('ignore_errors' not in k for _, k in vus)
    # la ligne de commande : code 2, un message, rien d'ecrit apres l'arret (ni tuile, ni zip)
    monkeypatch.undo()
    theatre_factice(monkeypatch)
    code = build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--edition', 'x', '--out', str(out)])
    assert code == 2
    err = capsys.readouterr().err
    assert 'erreur' in err and 'hillshade' in err and 'arretee' in err
    assert not list(out.glob('*.zip')) and not (out / 'tiles' / 'elevation').exists()


# ------------------------------------------------------------------ zones non relevees et grille au-dela de l'emprise de pydcs

TROU = (126, 131, 126, 131)            # un trou de NaN : noeuds i de 126 a 130, j de 126 a 130 de la grille synthetique


def avec_trou(alt, trou=TROU):
    g = alt.astype(np.float32).copy()
    i0, i1, j0, j1 = trou
    g[i0:i1, j0:j1] = np.nan
    return g


def poids_pixels(tab_shape, X, Z):
    """Pour une tuile : (ok, i0, j0, ti, tj) des pixels, par la meme fonction que le rendu (la geometrie n'est pas ce qu'on teste)."""
    return relief._coins((X - X0) / 100.0, (Z - Z0) / 100.0, *tab_shape)


def test_zones_non_relevees_hillshade_a_34_cases_elevation_sur_les_noeuds_nan(alt, projection):
    avec, plein = avec_trou(alt), alt.astype(np.float32)
    tuiles = tuiles_de_test(projection)
    r_trou = relief.rendre_tuiles(avec, X0, Z0, EMPRISE, projection, tuiles)
    r_plein = relief.rendre_tuiles(plein, X0, Z0, EMPRISE, projection, tuiles)
    n_proche = n_hors = n_elev_trou = n_vide_ailleurs = 0
    for t in tuiles:
        X, Z = T.cartes_xz(relief.ZOOM_MAX, *t, projection)
        nan = np.isnan(avec)
        ok, i0, j0, ti, tj = poids_pixels(avec.shape, X, Z)
        # elevation : transparente seulement sur un pixel dont un noeud de poids non nul est NaN
        sur_nan = np.zeros(ok.shape, bool)
        for di, dj, w in ((0, 0, (1 - ti) * (1 - tj)), (0, 1, (1 - ti) * tj), (1, 0, ti * (1 - tj)), (1, 1, ti * tj)):
            sur_nan |= nan[i0 + di, j0 + dj] & (w > 0)
        el_t = r_trou['elevation'].get(t)
        el_p = r_plein['elevation'].get(t)
        a_el = np.zeros(ok.shape, bool) if el_t is None else el_t[..., 3] > 0
        assert np.array_equal(a_el, ok & ~sur_nan)
        if el_t is not None:
            assert np.array_equal(el_t[a_el], el_p[a_el])               # ailleurs, la meme altitude qu'avec la grille pleine
        n_elev_trou += int((ok & sur_nan).sum())
        # hillshade : transparent a 34 cases au plus d'un noeud NaN (les quatre noeuds), identique a la grille pleine au-dela
        dist = ndimage.distance_transform_cdt(~nan, metric='chessboard')          # distance au NaN le plus proche
        proche = np.zeros(ok.shape, bool)
        for di in (0, 1):
            for dj in (0, 1):
                proche |= dist[i0 + di, j0 + dj] <= 34
        sh_t, sh_p = r_trou['hillshade'].get(t), r_plein['hillshade'].get(t)
        a_t = np.zeros(ok.shape + (4,), np.uint8) if sh_t is None else sh_t
        a_p = np.zeros(ok.shape + (4,), np.uint8) if sh_p is None else sh_p
        zone = ok & proche
        assert not a_t[zone | ~ok].any()                                  # transparent dans la zone et hors de la grille
        loin = ok & ~proche & ~sur_nan
        assert np.array_equal(a_t[loin], a_p[loin])                       # au-dela : exactement l'ombrage de la grille pleine
        n_proche += int((zone & (a_p[..., 3] > 0)).sum())                 # des pixels que la regle a bien retires
        n_hors += int((loin & (a_t[..., 3] > 0)).sum())                   # et des pixels opaques au-dela
        n_vide_ailleurs += int((loin & (a_t[..., 3] == 0)).sum())
    assert n_proche > 5000 and n_hors > 20000 and n_elev_trou > 0


def test_la_zone_du_hillshade_est_a_34_cases_pas_33_ni_35(projection):
    """Un seul noeud NaN au centre d'un relief a forte pente : les noeuds a 34 cases sont dans la zone, ceux a 35 n'y sont pas."""
    i, j = np.meshgrid(np.arange(NI, dtype=np.float64), np.arange(NJ, dtype=np.float64), indexing='ij')
    pente = (500 + 40 * np.sin(i / 7.0) + 35 * np.cos(j / 5.0)).astype(np.float32)
    pente[130, 130] = np.nan
    proche = relief.noeuds_proches_non_releves(pente.astype(np.float64))
    assert proche[130 - 34, 130 + 34] and proche[130 + 34, 130 - 34] and proche[130, 130 + 34]
    assert not proche[130 - 35, 130] and not proche[130, 130 + 35] and not proche[130 - 35, 130 + 35]
    assert int(proche.sum()) == 69 * 69
    assert relief.noeuds_proches_non_releves(np.zeros((10, 10))) is None
    # le bord de la fenetre n'est pas un noeud non releve
    assert relief.noeuds_proches_non_releves(np.where(np.arange(100)[:, None] < 0, np.nan, 1.0) + np.zeros((100, 100))) is None


def test_un_pixel_dont_un_noeud_a_poids_nul_est_nan_garde_son_elevation():
    """`elevation` : un noeud NaN de poids nul (pixel exactement sur le noeud voisin) ne rend pas le pixel transparent ; de poids
    non nul, si."""
    plan = np.full((NI, NJ), 700.0, np.float32)
    plan[100, 100] = np.nan
    for fi, fj, fini in ((99.0, 100.0, True), (99.0, 99.0, True), (99.5, 100.0, False), (100.0, 100.0, False), (99.0, 99.9, True)):
        ok, i0, j0, ti, tj = relief._coins(np.array([fi]), np.array([fj]), NI, NJ)
        assert np.isfinite(relief._bilineaire(plan, i0, j0, ti, tj)).all() == fini, (fi, fj)


def test_sans_nan_le_hillshade_ne_change_pas(alt, projection, monkeypatch):
    """Sans aucun NaN dans la grille, la regle des zones non relevees ne s'applique pas (les tuiles sont celles d'avant)."""
    tuiles = tuiles_de_test(projection)
    r = relief.rendre_tuiles(alt.astype(np.float32), X0, Z0, EMPRISE, projection, tuiles)
    appels = []
    vrai = relief.noeuds_proches_non_releves
    monkeypatch.setattr(relief, 'noeuds_proches_non_releves', lambda a, rayon=34: appels.append(1) or vrai(a, rayon))
    r2 = relief.rendre_tuiles(alt.astype(np.float32), X0, Z0, EMPRISE, projection, tuiles)
    assert appels and all(np.array_equal(r[c][t], r2[c][t]) for c in relief.COUCHES for t in r[c]) and r['hillshade']
    monkeypatch.setattr(relief, 'noeuds_proches_non_releves', lambda a, rayon=34: None)       # la regle debrayee
    r3 = relief.rendre_tuiles(alt.astype(np.float32), X0, Z0, EMPRISE, projection, tuiles)
    assert r3.keys() == r.keys() and all(set(r3[c]) == set(r[c]) and all(np.array_equal(r3[c][t], r[c][t]) for t in r[c])
                                          for c in relief.COUCHES)


@pytest.fixture(scope='module')
def entrees_trou(alt, tmp_path_factory):
    """Le zip des deux moities de `entrees`, sans les noeuds du trou : une grille avec une zone non relevee."""
    d = tmp_path_factory.mktemp('entrees_trou')
    i0, i1, j0, j1 = TROU
    gauche = retirer_lignes(csv_bloc(alt, 0, NI, 0, 131), range(i0, i1), [j for j in range(j0, j1) if j < 131])
    droite = retirer_lignes(csv_bloc(alt, 0, NI, 130, NJ), range(i0, i1), [j - 130 for j in range(j0, j1) if j >= 130])
    z = ecrire_zip(d / 'trou.zip', {'out/relief/Syria/test_r0_c0_100m_export.csv': gauche,
                                    'out/relief/Syria/test_r0_c1_100m_export.csv': droite})
    return {'zip': z}


def construire_sans_regle(entrees, out, niveaux=relief.NIVEAUX_DEFAUT, processus=1):
    """Comme `construire`, mais la regle « noeud manquant dans l'emprise » est debrayee (la grille a un trou) et l'emprise des
    tuiles est le rectangle de la grille."""
    g = relief.construire_grille([entrees['zip']], 'Syria')
    npy, _ = relief.ecrire_grille(g, out, 'Syria', 'Syria')
    emprise = relief.emprise_grille(g)
    res = relief.produire_tuiles(npy, 'Syria', g.x0, g.z0, emprise, out, niveaux=niveaux, processus=processus)
    info = relief.ecrire_zip(out, 'Syria', 'Syria', 'test', res['bounds'], g.entrees)
    return {'out': Path(out), 'grille': g, 'res': res, 'zip': Path(out) / info['name']}


def test_la_grille_a_trou_est_lue_et_l_emprise_est_son_rectangle(alt, entrees_trou):
    g = relief.construire_grille([entrees_trou['zip']], 'Syria')
    assert g.forme == [NI, NJ] and int(np.isnan(g.alt).sum()) == 25
    assert relief.emprise_grille(g) == EMPRISE
    with pytest.raises(relief.RefusDonnees, match=r'25 noeud\(s\)'):
        relief.construire_grille([entrees_trou['zip']], 'Syria', emprise=EMPRISE)             # la regle 4 vaut toujours


def test_les_tuiles_d_une_grille_a_trou_ne_dependent_ni_des_blocs_ni_des_processus(entrees_trou, tmp_path, projection):
    ref = construire_sans_regle(entrees_trou, tmp_path / 'ref')
    de_reference = contenu(ref['out'])
    assert any(k.startswith('hillshade/11/') for k in de_reference) and any(k.startswith('elevation/6/') for k in de_reference)
    for niveaux, processus in ((2, 1), (3, 2), (1, 2)):
        b = construire_sans_regle(entrees_trou, tmp_path / f'n{niveaux}p{processus}', niveaux=niveaux, processus=processus)
        assert contenu(b['out']) == de_reference, (niveaux, processus)
        assert b['zip'].read_bytes() == ref['zip'].read_bytes()
    # et le trou y est : une tuile z11 de l'ombrage a des pixels transparents que la grille pleine n'a pas
    tuiles = tuiles_de_test(projection)
    plein = relief.rendre_tuiles(np.nan_to_num(ref['grille'].alt, nan=800.0), X0, Z0, EMPRISE, projection, tuiles)
    n_plein = sum(int((t[..., 3] > 0).sum()) for t in plein['hillshade'].values())
    n_trou = sum(int((lire(ref['out'] / 'tiles' / 'hillshade' / '11' / str(x) / f'{y}.webp')[..., 3] > 0).sum()) for x, y in tuiles
                 if (ref['out'] / 'tiles' / 'hillshade' / '11' / str(x) / f'{y}.webp').exists())
    assert n_trou < n_plein


def test_une_grille_qui_depasse_l_emprise_de_pydcs_a_des_tuiles_au_dela(entrees, tmp_path, monkeypatch, capsys, projection):
    """`terrain.bounds` (ici plus petit que la grille) ne commande que la regle du noeud manquant : les tuiles couvrent la grille."""
    bounds = (X0, Z0, X0 + 12000.0, Z0 + 12000.0)
    theatre_factice(monkeypatch, bounds)
    out = tmp_path / 'sortie'
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--edition', 'x', '--out', str(out)]) == 0
    assert 'emprise des tuiles = rectangle de la grille' in capsys.readouterr().out
    with zipfile.ZipFile(out / 'DCS_Airfield_Charts_Syria_relief.zip') as z:
        desc = json.loads(z.read('relief.json'))
    assert desc['bounds'] == relief.bounds_leaflet(relief.contour_emprise(EMPRISE, projection))           # = la grille, pas `bounds`
    assert desc['bounds'] != relief.bounds_leaflet(relief.contour_emprise(bounds, projection))
    au_dela = 0
    for p in (out / 'tiles' / 'elevation' / '11').glob('*/*.png'):
        tx, ty = int(p.parent.name), int(p.stem)
        X, Z = T.cartes_xz(relief.ZOOM_MAX, tx, ty, projection)
        dehors = ~((X >= bounds[0]) & (X <= bounds[2]) & (Z >= bounds[1]) & (Z <= bounds[3]))
        au_dela += int((lire(p)[..., 3][dehors] > 0).sum())
    assert au_dela > 100000
    # la meme grille avec l'emprise de `terrain.bounds` : rien au-dela (le comportement d'avant)
    petit = tmp_path / 'petit'
    g = relief.construire_grille([entrees['zip']], 'Syria')
    npy, _ = relief.ecrire_grille(g, petit, 'Syria', 'Syria')
    relief.produire_tuiles(npy, 'Syria', g.x0, g.z0, bounds, petit)
    grand = {k for k in contenu(out) if k.startswith('elevation/11/')}
    assert len({k for k in contenu(petit) if k.startswith('elevation/11/')}) < len(grand)


# ------------------------------------------------------------------ --grille-seule

def test_grille_seule_n_ecrit_que_la_grille(entrees, tmp_path, monkeypatch, capsys):
    theatre_factice(monkeypatch)
    out = tmp_path / 'sortie'
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--out', str(out), '--grille-seule']) == 0         # sans --edition
    log = capsys.readouterr().out
    assert 'sha256 des octets float32' in log and 'pointe de memoire' in log and 'phase tuiles' not in log
    assert sorted(p.name for p in out.iterdir()) == ['relief_Syria.json', 'relief_Syria.npy']
    g = relief.construire_grille([entrees['zip']], 'Syria')
    a = np.load(out / 'relief_Syria.npy')
    assert np.array_equal(a, g.alt) and a.dtype == np.float32
    assert hashlib.sha256(a.tobytes()).hexdigest() in log
    j = json.loads((out / 'relief_Syria.json').read_text(encoding='utf-8'))
    assert (j['x0'], j['z0'], j['forme']) == (X0, Z0, [NI, NJ])
    # des anciennes tuiles ne sont pas touchees
    vieux = out / 'tiles' / 'hillshade' / '11' / '1' / '1.webp'
    vieux.parent.mkdir(parents=True)
    vieux.write_bytes(b'ancien')
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--out', str(out), '--grille-seule']) == 0
    assert vieux.read_bytes() == b'ancien'
    # sans --grille-seule, --edition reste exigee ; les refus sur les donnees valent aussi pour la grille seule
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--out', str(out)]) == 1
    capsys.readouterr()
    theatre_factice(monkeypatch, (X0, Z0, X0 + 1e6, Z0 + 1e6))
    assert build_relief.main(['Syria', '--entrees', str(entrees['zip']), '--out', str(tmp_path / 'autre'), '--grille-seule']) == 2
    assert 'aucune entree' in capsys.readouterr().err and not (tmp_path / 'autre').exists()
