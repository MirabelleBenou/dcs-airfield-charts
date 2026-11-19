"""capture/f10tiles.py : la capture en grille sur un crochet et un ecran simules (reprise, duree maximale, disque, etalonnage,
mode de la carte), `plan` et les refus d'arguments. Aucun DCS."""
import json
import random
from pathlib import Path

import numpy as np
import pytest

from test_tuiles_monde import (ECRAN_MOYEN, ECRAN_REEL, FauxDcs, Monde, args, cfg, ecrire_passe, f10capture, ft, T,   # noqa: F401
                               geo)

RECT = '35.000,36.000,35.030,36.060'           # ~ 3,3 x 5,5 km : une douzaine de cases au zoom 15 sur ECRAN_MOYEN


class Horloge:
    """Horloge simulee : avance de `pas` secondes a chaque prise de vue du faux crochet."""

    def __init__(self, pas=10.0):
        self.t, self.pas = 0.0, pas

    def __call__(self):
        return self.t


@pytest.fixture
def monde_capture(cfg, monkeypatch):
    """Faux DCS (ecran moyen, monde bruite) installe, mode de la carte reconnu comme Alt, horloge simulee."""
    cfg(ecran=ECRAN_MOYEN)
    dcs = FauxDcs(Monde('bruit'), ECRAN_MOYEN).installer(monkeypatch)
    horloge = Horloge()
    monkeypatch.setattr(ft, '_horloge', horloge)
    monkeypatch.setattr(T, 'mode_de_image', lambda img: ('alt', 0.5, 100.0))
    hook = dcs.hook_xz

    def hook_horloge(x, z, alt):
        horloge.t += horloge.pas
        return hook(x, z, alt)
    monkeypatch.setattr(f10capture, 'hook_xz', hook_horloge)
    return dcs, horloge


def _zone(argv):
    return ft.Zone(ft._parser().parse_args(ft._recoller_rectangle(argv[1:])))


def _cases_prises(dcs, g):
    pos = {(round(c['x'], 3), round(c['z'], 3)) for c in g['cases']}
    return [p for p in dcs.prises if (p[0], p[1]) in pos]


# ------------------------------------------------------------------ TU4 : la reprise

def test_tu4_reprise_chaque_case_une_fois_et_une_seule(monde_capture, monkeypatch, capsys):
    dcs, horloge = monde_capture
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    z = _zone(argv)
    n_calib = 6                                            # prises de vue de l'etalonnage a chaque execution
    ecrites = []
    ecrire = ft._ecrire_image
    monkeypatch.setattr(ft, '_ecrire_image', lambda img, chemin, couche: (ecrites.append(chemin.name), ecrire(img, chemin, couche))[1])
    rng = random.Random(4)
    assert ft.main(args('plan', 'Syria', zoom=15, rectangle=RECT)) == 0
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    total = len(g['cases'])
    assert total >= 8

    def execution(duree=None, panne_a=None):
        dcs.panne = (lambda n, base=len(dcs.prises): RuntimeError('crochet muet') if n == base + 1 + n_calib + panne_a else None) \
            if panne_a is not None else None
        horloge.t = 0.0
        extra = [f'--duree-max-s={duree}'] if duree else []
        return ft.executer(argv + extra)

    # 1. exception levee a une case tiree au sort
    k1 = rng.randint(2, total - 4)
    assert execution(panne_a=k1) == 3
    assert (f10capture.CFG.work_dir / 'sync' / 'QUESTION.txt').read_text(encoding='utf-8').startswith('RuntimeError: crochet muet')
    assert sum(1 for p in z.dossier.glob('r*_c*.png')) == k1
    # 2. duree maximale depassee : elle s'arrete d'elle-meme, proprement (code 0, INCOMPLET, camera relachee)
    liberees = dcs.liberations
    capsys.readouterr()
    m = rng.randint(1, 2)
    assert execution(duree=n_calib * 10 + 10 * m + 3) == 0
    sortie = capsys.readouterr().out
    assert f'INCOMPLET {k1 + m}/{total}' in sortie and dcs.liberations == liberees + 1
    # 3. deuxieme exception, 4. deuxieme duree depassee, 5. fin
    k2 = rng.randint(1, max(1, total - k1 - m - 3))
    assert execution(panne_a=k2) == 3
    assert execution(duree=n_calib * 10 + 10 + 3) == 0
    assert execution() == 0
    assert 'COMPLET' in capsys.readouterr().out
    # une reprise sur une grille complete ne capture rien
    avant = len(dcs.prises)
    assert execution() == 0 and len(dcs.prises) == avant
    # chaque case : une image ecrite, une prise de vue reussie, une ligne de captures.jsonl
    assert sorted(ecrites) == sorted(T.nom_image('alt', c['row'], c['col']) for c in g['cases'])
    assert len(set(ecrites)) == total
    prises = _cases_prises(dcs, g)
    assert len(prises) == total and len(set(prises)) == total
    lignes = [json.loads(l) for l in (z.dossier / 'captures.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(lignes) == total and len({(l['row'], l['col']) for l in lignes}) == total
    assert all(set(l) >= {'row', 'col', 'x', 'z', 'rendered_x', 'rendered_z', 'rendered_alt', 'delay_s', 'mpp'} for l in lignes)
    assert not list(z.dossier.glob('*.tmp'))
    # chaque execution a note son etalonnage au grille.json ; l'echelle mesuree est celle de la ligne de captures
    g2 = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    assert len(g2['calibrations']) == 5 and g2['cases'] == g['cases']
    assert all(c['mpp'] == pytest.approx(T.mpp_camera(g['commanded_alt_m']), rel=0.01) for c in g2['calibrations'])
    assert {l['mpp'] for l in lignes} <= {c['mpp'] for c in g2['calibrations']}
    assert all(c['mode'] == 'alt' for c in g2['calibrations'])


def test_tu4_ordre_serpentin_et_altitude_commandee(monde_capture):
    dcs, _ = monde_capture
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv) == 0
    z = _zone(argv)
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    prises = _cases_prises(dcs, g)
    ordre = [(c['row'], c['col']) for c in T.ordre_serpentin(g['cases'])]
    par_pos = {(round(c['x'], 3), round(c['z'], 3)): (c['row'], c['col']) for c in g['cases']}
    assert [par_pos[(p[0], p[1])] for p in prises] == ordre
    assert all(p[2] == round(g['commanded_alt_m'], 1) for p in prises)


def test_tu4_images_alt_png_et_sat_sans_perte(cfg, monkeypatch):
    for couche, ext in (('alt', 'png'), ('sat', 'webp')):
        cfg(ecran=ECRAN_MOYEN)
        dcs = FauxDcs(Monde('bruit'), ECRAN_MOYEN).installer(monkeypatch)
        monkeypatch.setattr(T, 'mode_de_image', lambda img, c=couche: (c, 0.1, 50.0))
        argv = args('capture', 'Syria', couche=couche, zoom=15, rectangle='35.0,36.0,35.01,36.01', nom='un')
        assert ft.executer(argv) == 0
        z = _zone(argv)
        fichiers = sorted(z.dossier.glob(f'r*_c*.{ext}'))
        assert fichiers and not list(z.dossier.glob('r*_c*.*.tmp'))
        g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
        c = g['cases'][0]
        alt = g['commanded_alt_m']
        dcs.hook_xz(c['x'], c['z'], alt)
        attendu = f10capture.crop(dcs.grab())
        lu = T.charger_image_rgb(z.dossier / T.nom_image(couche, c['row'], c['col']))
        assert lu.shape == attendu.shape and (lu == attendu).all()                  # sans perte, Sat compris
        assert lu.shape[1] == ECRAN_MOYEN[0] - 90 and lu.shape[0] == ECRAN_MOYEN[1] - 36


def test_tu4_grille_aux_arguments_differents_refusee(monde_capture):
    argv = args('capture', 'Syria', zoom=15, rectangle='35.0,36.0,35.02,36.04', nom='z')
    assert ft.executer(argv) == 0
    z = _zone(argv)
    avant = (z.dossier / 'grille.json').read_text(encoding='utf-8')
    for autre in (args('capture', 'Syria', zoom=15, rectangle='35.0,36.0,35.03,36.04', nom='z'),
                  args('plan', 'Syria', zoom=15, rectangle='35.0,36.0,35.02,36.05', nom='z')):
        with pytest.raises(SystemExit) as e:
            ft.executer(autre)
        assert e.value.code == 2
    assert (z.dossier / 'grille.json').read_text(encoding='utf-8') == avant                # jamais reecrite
    # la meme grille, autrement dite (autre nom de fichier) : la meme passe, acceptee
    assert ft.executer(argv) == 0


def test_tu4_une_image_non_stable_n_est_pas_enregistree(monde_capture, monkeypatch, capsys):
    dcs, _ = monde_capture
    reel = f10capture.move_and_stabilize
    appels = []

    def instable(x, z, alt, expect_change=True):
        img, delai, n, ok, cmd = reel(x, z, alt, expect_change)
        appels.append((x, z))
        if len(appels) == 9:                                  # une case (l'etalonnage en prend 6)
            return img, 10.0, n, False, cmd
        return img, delai, n, ok, cmd
    monkeypatch.setattr(f10capture, 'move_and_stabilize', instable)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv) == 0
    z = _zone(argv)
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    n = len(g['cases'])
    sortie = capsys.readouterr().out
    assert 'NON STABLE' in sortie and f'INCOMPLET {n - 1}/{n}' in sortie
    assert len(list(z.dossier.glob('r*_c*.png'))) == n - 1
    # la reprise reprend ce qui manque
    monkeypatch.setattr(f10capture, 'move_and_stabilize', reel)
    assert ft.executer(argv) == 0 and len(list(z.dossier.glob('r*_c*.png'))) == n


# ------------------------------------------------------------------ le disque

def test_disque_insuffisant_avant_la_premiere_case(monde_capture, monkeypatch):
    dcs, _ = monde_capture
    vu = []
    monkeypatch.setattr(ft, '_espace_libre', lambda p: vu.append(p) or 15 * 10 ** 9)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    # 3 Mo (alt) par case avant la premiere image : n cases + 10 Go
    z = _zone(argv)
    ft.main(args('plan', 'Syria', zoom=15, rectangle=RECT))
    n = len(json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))['cases'])
    assert n * 3 * 10 ** 6 < 5 * 10 ** 9
    monkeypatch.setattr(ft, '_espace_libre', lambda p: 10 * 10 ** 9 + n * 3 * 10 ** 6 - 1)
    assert ft.executer(argv) == 3
    q = (f10capture.CFG.work_dir / 'sync' / 'QUESTION.txt').read_text(encoding='utf-8')
    assert 'Place disque insuffisante' in q and dcs.prises == []                 # rien n'a ete capture
    monkeypatch.setattr(ft, '_espace_libre', lambda p: 10 * 10 ** 9 + n * 3 * 10 ** 6)
    assert ft.executer(argv) == 0


def test_disque_controle_toutes_les_200_cases(monde_capture, monkeypatch):
    dcs, _ = monde_capture
    monkeypatch.setattr(ft, 'CONTROLE_DISQUE_CASES', 3)
    appels = []

    def libre(p):
        appels.append(1)
        return 10 ** 15 if len(appels) < 3 else 0                  # le disque se remplit au troisieme controle
    monkeypatch.setattr(ft, '_espace_libre', libre)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv) == 3
    z = _zone(argv)
    # controles : avant la premiere case (1), apres 3 cases (2), apres 6 (3 : arret)
    assert len(appels) == 3 and len(list(z.dossier.glob('r*_c*.png'))) == 6
    assert dcs.liberations >= 1


def test_disque_avec_la_taille_moyenne_des_images_deja_faites(monde_capture, monkeypatch):
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv + ['--duree-max-s=85']) == 0
    z = _zone(argv)
    images = sorted(z.dossier.glob('r*_c*.png'))
    assert images
    moy = sum(p.stat().st_size for p in images) / len(images)
    n = len(json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))['cases'])
    reste = n - len(images)
    monkeypatch.setattr(ft, '_espace_libre', lambda p: 10 * 10 ** 9 + reste * moy - 1000)
    assert ft.executer(argv) == 3
    monkeypatch.setattr(ft, '_espace_libre', lambda p: 10 * 10 ** 9 + reste * moy + 1000)
    assert ft.executer(argv) == 0


# ------------------------------------------------------------------ l'etalonnage et le mode (TU10)

def _calibrate_faux(dcs, ecrire_image=True):
    """Remplace `calibrate` : une prise de vue au coin Nord-Ouest (crochet et ecran simules), enregistree comme le fait
    l'etalonnage de f10capture, puis une mesure canned."""
    def calibrate(zone, alt, mpp_pred, odir, label, prog=None):
        img, delai, n, ok, cmd = f10capture.move_and_stabilize(zone['x_max'], zone['z_min'], alt)
        f10capture.save_png(f10capture.crop(img), odir / 'calib' / 'nw_c0.png')
        return {'measures': [mpp_pred] * 4, 'mean': mpp_pred, 'max_spread_rel': 0.0, 'predicted': mpp_pred,
                'dev_vs_predicted_rel': 0.0, 'align_deg_max': 0.0, 'peaks': [1.0] * 4, 'detail': {}}
    return calibrate


def _trouver_bruit_inconnu():
    """Nombre de colonnes bruitees qui donne a l'image Alt un aplat entre 0,15 et 0,30 (mode inconnu)."""
    monde = Monde('alt')
    for cols in range(900, 3440, 50):
        d = FauxDcs(monde, ECRAN_REEL, bruit_cols=cols)
        d.cam = (219000.0, -48000.0, 4400.0)
        a, s = T.mesurer_centre(np.ascontiguousarray(f10capture.crop(d.grab())[300:1100, 800:2600]))
        if 0.15 < a < 0.30:
            return cols
    raise AssertionError('aucun bruit ne donne un mode inconnu')


@pytest.mark.parametrize('vu,monde,bruit_cols', [('map', 'map', 0), ('sat', 'alt', 3440), ('inconnu', 'alt', None)])
def test_tu10_la_passe_s_arrete_sur_un_mode_autre_que_la_couche(cfg, monkeypatch, vu, monde, bruit_cols):
    cfg(ecran=ECRAN_REEL)
    if bruit_cols is None:
        bruit_cols = _trouver_bruit_inconnu()
    dcs = FauxDcs(Monde(monde), ECRAN_REEL, bruit_cols=bruit_cols).installer(monkeypatch)
    monkeypatch.setattr(f10capture, 'calibrate', _calibrate_faux(dcs))
    argv = args('capture', 'Syria', zoom=15, rectangle='35.0,36.0,35.02,36.04')
    assert ft.executer(argv) == 3
    q = (f10capture.CFG.work_dir / 'sync' / 'QUESTION.txt').read_text(encoding='utf-8')
    assert f'Mode de la carte : {vu} (aplat ' in q and 'saturation ' in q and 'au lieu de alt' in q
    z = _zone(argv)
    assert not list(z.dossier.glob('r*_c*.png'))                                  # aucune case capturee
    assert len(dcs.prises) == 1 and dcs.liberations >= 1                          # la camera est relachee
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    assert g['calibrations'] == []                                                # l'outil ne change jamais le mode ni n'avance


def test_tu10_un_mode_alt_est_accepte_en_couche_alt(cfg, monkeypatch):
    cfg(ecran=ECRAN_REEL)
    dcs = FauxDcs(Monde('alt'), ECRAN_REEL).installer(monkeypatch)
    monkeypatch.setattr(f10capture, 'calibrate', _calibrate_faux(dcs))
    argv = args('capture', 'Syria', zoom=15, rectangle='35.0,36.0,35.02,36.04')
    assert ft.executer(argv + ['--duree-max-s=3']) == 0
    z = _zone(argv)
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    assert g['calibrations'][0]['mode'] == 'alt' and g['calibrations'][0]['aplat'] >= 0.30


def test_etalonnage_refuse_arrete_la_passe(monde_capture, monkeypatch):
    dcs, _ = monde_capture

    def calibrate(*a, **k):
        raise f10capture.CalibrationFailed('moyenne trop loin de la prevision')
    monkeypatch.setattr(f10capture, 'calibrate', calibrate)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv) == 3
    q = (f10capture.CFG.work_dir / 'sync' / 'QUESTION.txt').read_text(encoding='utf-8')
    assert 'moyenne trop loin de la prevision' in q
    assert not list(_zone(argv).dossier.glob('r*_c*.png')) and dcs.liberations >= 1


def test_etalonnage_a_l_aerodrome_le_plus_proche_du_centre(monde_capture, monkeypatch):
    dcs, _ = monde_capture
    vus = []
    reel = f10capture.calibrate

    def calibrate(zone, alt, mpp_pred, odir, label, prog=None):
        vus.append((zone, alt, mpp_pred, label))
        return reel(zone, alt, mpp_pred, odir, label, prog)
    monkeypatch.setattr(f10capture, 'calibrate', calibrate)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    assert ft.executer(argv + ['--duree-max-s=3']) == 0
    (zone, alt, pred, label), = vus
    from render import dcsdata
    z = _zone(argv)
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    xs, zs = [c['x'] for c in g['cases']], [c['z'] for c in g['cases']]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    terrain = dcsdata.load_terrain('Syria')
    proche = min(terrain.airports.values(), key=lambda a: (a.position.x - cx) ** 2 + (a.position.y - cz) ** 2)
    assert (zone['x_min'] + zone['x_max']) / 2 == pytest.approx(proche.position.x)
    assert (zone['z_min'] + zone['z_max']) / 2 == pytest.approx(proche.position.y)
    assert alt == pytest.approx(g['commanded_alt_m']) and pred == pytest.approx(T.mpp_camera(alt))   # l'echelle prevue : la loi
    assert proche.name in label


# ------------------------------------------------------------------ les arguments

def test_arguments_refuses(cfg, capsys):
    cfg()
    base = ['f10tiles.py', 'plan', 'Syria', '--couche', 'alt', '--zoom', '15']
    for argv, code in [(base + ['--rectangle', '35,36,35.1,36.1', '--emprise', 'x.geojson'], 2),     # les deux
                       (base, 2),                                                                  # aucun des deux
                       (['f10tiles.py', 'plan', 'Syria', '--couche', 'map', '--zoom', '15', '--rectangle', '35,36,35.1,36.1'], 2),
                       (['f10tiles.py', 'plan', 'Syria', '--couche', 'alt', '--zoom', '19', '--rectangle', '35,36,35.1,36.1'], 2),
                       (['f10tiles.py', 'plan', 'Syria', '--couche', 'alt', '--zoom', '7', '--rectangle', '35,36,35.1,36.1'], 2),
                       (base + ['--rectangle', '35,36,34,36.1'], 2),                              # lat_s > lat_n
                       (base + ['--rectangle', '35,36,35.1'], 2),
                       (base + ['--rectangle', 'a,b,c,d'], 2),
                       (base + ['--rectangle', '35,36,35.1,36.1', '--nom', 'a b'], 2),
                       (base + ['--emprise', '/introuvable.geojson'], 2),
                       (['f10tiles.py', 'plan', 'Atlantide', '--couche', 'alt', '--zoom', '15', '--rectangle', '35,36,35.1,36.1'], 2),
                       (['f10tiles.py', 'tuiler', 'Syria', '--couche', 'alt', '--zoom', '15', '--rectangle', '35,36,35.1,36.1',
                         '--processus', '0'], 2),
                       (['f10tiles.py', 'aerodromes', 'Syria', 'Inconnue'], 2),
                       (['f10tiles.py', 'inconnu'], 2)]:
        with pytest.raises(SystemExit) as e:
            ft.main(argv)
        assert e.value.code == code, argv


def test_rectangle_negatif_et_nom_de_la_zone(cfg):
    cfg()
    a = ft._parser().parse_args(ft._recoller_rectangle(['plan', 'Syria', '--couche', 'alt', '--zoom', '15',
                                                         '--rectangle', '-3.5,-10,-3.0,-9.5']))
    z = ft.Zone(a)
    assert z.nom == 'rect' and z.id_emprise['rectangle'] == [-3.5, -10.0, -3.0, -9.5]
    z = ft.Zone(ft._parser().parse_args(['plan', 'Syria', '--couche', 'sat', '--zoom', '12', '--rectangle=35,36,35.1,36.1',
                                         '--nom', 'ma-zone']))
    assert z.dossier.name == 'sat-syria-z12-ma-zone' and z.nom_sortie == 'sat-syria-ma-zone'
    assert z.dossier.parent.name == 'tuiles_brut' and z.dossier.parent.parent == f10capture.CFG.work_dir
    z = ft.Zone(ft._parser().parse_args(['plan', 'Germany Cold War', '--couche', 'alt', '--zoom', '12', '--rectangle=51,10,51.1,10.1']))
    assert z.cle == 'germanycoldwar' and z.dossier.name == 'alt-germanycoldwar-z12-rect'


def test_plan_ne_touche_pas_dcs_et_ecrit_la_grille(cfg, monkeypatch, capsys):
    cfg(ecran=ECRAN_REEL)

    def interdit(*a, **k):
        raise AssertionError('plan ne touche pas DCS')
    for nom in ('hook_xz', 'grab', 'move_and_stabilize', 'calibrate', 'hook_release'):
        monkeypatch.setattr(f10capture, nom, interdit)
    assert ft.main(args('plan', 'Syria', zoom=17, rectangle='35.0,36.0,35.1,36.1', nom='essai')) == 0
    sortie = capsys.readouterr().out
    for mot in ('altitude commandee :', 'images :', 'tuiles du zoom 17 :', 'place disque estimee :'):
        assert mot in sortie
    z = ft.Zone(ft._parser().parse_args(['plan', 'Syria', '--couche', 'alt', '--zoom', '17', '--rectangle=35.0,36.0,35.1,36.1',
                                         '--nom', 'essai']))
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    assert g['theatre'] == 'syria' and g['layer'] == 'alt' and g['zoom'] == 17 and g['zone'] == 'essai'
    assert g['extent'] == {'rectangle': [35.0, 36.0, 35.1, 36.1]}
    assert g['usable_px'] == [3350, 1404] and g['overlap_px'] == 32 and g['crop_offset_px'] == [18.0, 45.0]
    assert g['mpp_planned'] == pytest.approx(T.mpp_tuile(17, 35.1))
    assert g['commanded_alt_m'] == pytest.approx(T.altitude_commandee(g['mpp_planned']))
    assert len(g['cases']) >= 1 and g['calibrations'] == [] and not list(z.dossier.glob('r*.png'))
    # un deuxieme plan, memes arguments : la grille est conservee telle quelle
    avant = (z.dossier / 'grille.json').read_text(encoding='utf-8')
    assert ft.main(args('plan', 'Syria', zoom=17, rectangle='35.0,36.0,35.1,36.1', nom='essai')) == 0
    assert (z.dossier / 'grille.json').read_text(encoding='utf-8') == avant


def test_les_constantes_de_f10capture_sont_celles_de_l_outil():
    assert T.MPP_PAR_ALT_M == f10capture.MPP_PER_ALT_M
    assert f10capture.crop_offset(1.0) == (18.0, 45.0)
    assert ft.RECOUVREMENT_PX == 32 and ft.RECOUVREMENT_PX != f10capture.OVERLAP
    assert (T.MODE_X0, T.MODE_Y0, T.MODE_W, T.MODE_H) == (800, 300, 1800, 800)
