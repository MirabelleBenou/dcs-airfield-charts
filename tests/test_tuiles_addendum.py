"""Tuiles XYZ, ecarts de la reception (addendum 1) : etalonnage a l'echelle des tuiles (TU13), archives et purge (TU14), petites
regles (TU15 : dossier d'etalonnage neuf, mode illisible, disque, rmtree, WebP, case non stable, `tuiler` sans ecran, processus
par defaut, source trop grande). Aucun DCS, aucune image de DCS."""
import json
import os
import time

import numpy as np
import pytest
from PIL import Image

from test_tuiles_monde import (ECRAN_MOYEN, ECRAN_REEL, FauxDcs, Monde, args, cfg, ecrire_passe, f10capture, ft, T,   # noqa: F401
                               geo)
from test_tuiles_pyramide import RECT, _lire_manifeste, _tuiler, passe   # noqa: F401  (passe : fixture)

FACTEUR = 1.0137                                       # une echelle vraie qui n'est pas la prevue : le decalage mesure n'est pas entier
RECT_PETIT = '35.00,36.00,35.02,36.02'


def _zone(argv):
    return ft.Zone(ft._parser().parse_args(ft._recoller_rectangle(argv[1:])))


def _question():
    return (f10capture.CFG.work_dir / 'sync' / 'QUESTION.txt').read_text(encoding='utf-8')


# ------------------------------------------------------------------ TU13 : l'etalonnage a l'echelle des tuiles

@pytest.mark.parametrize('zoom', [8, 11, 13, 15, 17])
def test_tu13_etalonnage_accepte_et_juste_a_chaque_zoom(cfg, monkeypatch, zoom):
    """Monde synthetique, vrai `calibrate`, ecran 3 440 x 1 440 : l'etalonnage est accepte et l'echelle mesuree est a 0,05 % de la
    vraie ; le pas de calibrate vaut PAS_ETALONNAGE_PX pixels a l'echelle prevue pendant l'etalonnage, puis est remis."""
    cfg(ecran=ECRAN_REEL)
    FauxDcs(Monde('bruit', graine=3), ECRAN_REEL, facteur_echelle=FACTEUR).installer(monkeypatch)
    monkeypatch.setattr(T, 'mode_de_image', lambda img: ('alt', 0.5, 100.0))
    z = _zone(args('plan', 'Syria', zoom=zoom, rectangle=RECT_PETIT))
    g = ft.assurer_grille(z)
    pas = []
    vrai_calibrate = f10capture.calibrate
    monkeypatch.setattr(f10capture, 'calibrate',
                        lambda *a, **k: (pas.append(f10capture.CALIB_STEP_M), vrai_calibrate(*a, **k))[1])
    avant = f10capture.CALIB_STEP_M
    cal = ft._etalonner(z, g)
    alt = g['commanded_alt_m']
    vrai = T.mpp_camera(alt) * FACTEUR
    ecart = abs(cal['mpp'] / vrai - 1)
    print(f'zoom {zoom} : alt {alt:.1f} m, mpp mesure {cal["mpp"]:.6f}, vrai {vrai:.6f}, ecart {ecart * 100:.4f} %')
    assert ecart <= 0.0005, f'{ecart * 100:.4f} %'
    assert pas == [pytest.approx(ft.PAS_ETALONNAGE_PX * T.mpp_camera(alt))]
    assert f10capture.CALIB_STEP_M == avant == 200.0


def _ecart_moyen_au_monde(z, monde, proj, zoom):
    """Ecart moyen absolu (niveaux, canaux RVB) entre les tuiles ecrites du zoom `zoom` et le monde, sur les pixels opaques."""
    somme, n = 0.0, 0
    for zz, tx, ty, chemin in T.lister_tuiles(z.dossier / 'tuiles', 'alt'):
        if zz != zoom:
            continue
        t = np.asarray(Image.open(chemin).convert('RGBA'))
        X, Zc = T.cartes_xz(zoom, tx, ty, proj)
        attendu = monde.couleur(X, Zc).astype(float)
        opaque = t[:, :, 3] == 255
        somme += np.abs(t[:, :, :3].astype(float) - attendu)[opaque].sum()
        n += int(opaque.sum()) * 3
    assert n > 0
    return somme / n


def test_tu13_de_bout_en_bout_au_z15_l_echelle_mesuree_vaut_presque_la_vraie(cfg, monkeypatch):
    """Capture sur FauxDcs avec le vrai `calibrate`, puis `tuiler` : l'ecart moyen de couleur des tuiles au monde est au plus le
    double de celui obtenu avec l'echelle vraie (meme passe, echelle remplacee par la vraie)."""
    cfg(ecran=ECRAN_REEL)
    monde = Monde('bruit', graine=3)
    FauxDcs(monde, ECRAN_REEL, facteur_echelle=FACTEUR).installer(monkeypatch)
    monkeypatch.setattr(T, 'mode_de_image', lambda img: ('alt', 0.5, 100.0))
    argv = args('capture', 'Syria', zoom=15, rectangle='35.00,36.00,35.12,36.15')
    assert ft.executer(argv) == 0
    z = _zone(argv)
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    assert len(g['cases']) >= 4 and g['rows'] >= 2 and g['cols'] >= 2             # des raccords, dans les deux sens
    proj = geo.Projection.for_theatre('Syria')
    tuiler = args('tuiler', 'Syria', zoom=15, rectangle='35.00,36.00,35.12,36.15')
    assert ft.main(tuiler + ['--zoom-min=15', '--processus=1']) == 0
    ecart_mesure = _ecart_moyen_au_monde(z, monde, proj, 15)
    # la meme passe avec l'echelle vraie
    g = json.loads((z.dossier / 'grille.json').read_text(encoding='utf-8'))
    vrai = g['mpp_planned'] * FACTEUR
    mesuree = g['calibrations'][-1]['mpp']
    g['calibrations'][-1]['mpp'] = vrai
    ft._ecrire_json(z.dossier / 'grille.json', g)
    lignes = [json.loads(l) for l in (z.dossier / 'captures.jsonl').read_text(encoding='utf-8').splitlines()]
    for l in lignes:
        l['mpp'] = vrai
    (z.dossier / 'captures.jsonl').write_text('\n'.join(json.dumps(l) for l in lignes) + '\n', encoding='utf-8')
    assert ft.main(tuiler + ['--zoom-min=15', '--processus=1']) == 0
    ecart_vrai = _ecart_moyen_au_monde(z, monde, proj, 15)
    print(f'z15 de bout en bout : echelle mesuree {mesuree:.6f} (vraie {vrai:.6f}, {abs(mesuree / vrai - 1) * 100:.4f} %), '
          f'ecart moyen de couleur {ecart_mesure:.3f} niveaux avec l\'echelle mesuree, {ecart_vrai:.3f} avec la vraie '
          f'(rapport {ecart_mesure / ecart_vrai:.3f})')
    assert ecart_vrai > 0
    assert ecart_mesure <= 2 * ecart_vrai


# ------------------------------------------------------------------ TU15 : l'etalonnage (dossier neuf, mode illisible)

def _faux_calibrate(odirs, ecrire_image=True, leve=None, pas=None):
    def calibrate(zone, alt, mpp_pred, odir, label, prog=None):
        odirs.append(odir)
        if pas is not None:
            pas.append(f10capture.CALIB_STEP_M)
        if leve is not None:
            raise leve
        if ecrire_image:
            f10capture.save_png(_image_alt(), odir / 'calib' / 'nw_c0.png')
        return {'measures': [mpp_pred] * 4, 'mean': mpp_pred, 'max_spread_rel': 0.0, 'predicted': mpp_pred,
                'dev_vs_predicted_rel': 0.0, 'align_deg_max': 0.0, 'peaks': [1.0] * 4, 'detail': {}}
    return calibrate


def _image_alt():
    """Une image recadree de la taille du mode (assez grande pour la mesure), de couleurs franches : le mode Alt."""
    return Monde('alt').image(219000.0, -48000.0, 3.0, 2600, 1100)


@pytest.fixture
def passe_planifiee(cfg, monkeypatch):
    cfg(ecran=ECRAN_MOYEN)
    z = _zone(args('plan', 'Syria', zoom=15, rectangle=RECT_PETIT))
    g = ft.assurer_grille(z)
    return z, g


def test_tu15_le_dossier_d_etalonnage_est_neuf_a_chaque_execution(passe_planifiee, monkeypatch):
    z, g = passe_planifiee
    (z.dossier / 'etalonnage_001' / 'calib').mkdir(parents=True)
    (z.dossier / 'etalonnage_005' / 'calib').mkdir(parents=True)
    f10capture.save_png(_image_alt(), z.dossier / 'etalonnage_005' / 'calib' / 'nw_c0.png')      # une image d'une execution precedente
    odirs = []
    monkeypatch.setattr(f10capture, 'calibrate', _faux_calibrate(odirs))
    cal = ft._etalonner(z, g)
    assert [o.name for o in odirs] == ['etalonnage_006'] and cal['folder'] == 'etalonnage_006'     # le plus haut + 1, pas le nombre de reussis
    assert g['calibrations'] == []                                                                  # aucun etalonnage reussi note
    cal = ft._etalonner(z, g)
    assert cal['folder'] == 'etalonnage_007'


def test_tu15_mode_illisible_arrete_la_passe_meme_avec_une_ancienne_image(passe_planifiee, monkeypatch):
    z, g = passe_planifiee
    (z.dossier / 'etalonnage_001' / 'calib').mkdir(parents=True)
    f10capture.save_png(_image_alt(), z.dossier / 'etalonnage_001' / 'calib' / 'nw_c0.png')         # un mode lisible, mais ancien
    monkeypatch.setattr(f10capture, 'calibrate', _faux_calibrate([], ecrire_image=False))
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT_PETIT)
    dcs = FauxDcs(Monde('alt'), ECRAN_MOYEN).installer(monkeypatch)
    assert ft.executer(argv) == 3
    q = _question()
    assert 'illisible' in q and 'nw_c0.png' in q and dcs.prises == []
    # un mode lisible et juste : la passe va au bout de l'etalonnage
    monkeypatch.setattr(T, 'mode_de_image', lambda img: ('alt', 0.5, 100.0))
    monkeypatch.setattr(f10capture, 'calibrate', _faux_calibrate([]))
    assert ft.executer(argv) == 0


def test_tu15_le_pas_d_etalonnage_est_remis_meme_sur_exception(passe_planifiee, monkeypatch):
    z, g = passe_planifiee
    monkeypatch.setattr(ft, 'PAS_ETALONNAGE_PX', 300)                 # la valeur de l'outil (le petit ecran de test en pose une autre)
    avant = f10capture.CALIB_STEP_M
    pas = []
    monkeypatch.setattr(f10capture, 'calibrate', _faux_calibrate([], leve=RuntimeError('boum'), pas=pas))
    with pytest.raises(RuntimeError):
        ft._etalonner(z, g)
    assert pas == [pytest.approx(300 * T.mpp_camera(g['commanded_alt_m']))] and f10capture.CALIB_STEP_M == avant
    monkeypatch.setattr(f10capture, 'calibrate', _faux_calibrate([], leve=f10capture.CalibrationFailed('refuse')))
    with pytest.raises(f10capture.AskCdP):
        ft._etalonner(z, g)
    assert f10capture.CALIB_STEP_M == avant


# ------------------------------------------------------------------ TU14 : les archives et leur purge

def _etat_sorties(work):
    out = work / 'out' / 'tuiles'
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())} if out.is_dir() else {}


def test_tu14_une_autre_zone_et_un_autre_zoom_ne_touchent_pas_la_serie(passe, cfg):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    z15 = _etat_sorties(work)
    assert 'alt-syria-rect.json' in z15 and any(n.endswith('_z15_001.tar') for n in z15)
    # une deuxieme passe, d'une autre zone : les deux series coexistent, la premiere intacte
    ecrire_passe(args('capture', 'Syria', zoom=15, rectangle='35.000,36.000,35.020,36.030', nom='autre'), Monde('alt'))
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle='35.000,36.000,35.020,36.030', nom='autre'), processus=1) == 0
    apres = _etat_sorties(work)
    assert all(apres[n] == b for n, b in z15.items())
    assert 'alt-syria-autre.json' in apres and any(n.startswith('alt-syria-autre_z15_') for n in apres)
    # un tuilage au z16 de la meme zone : refus (code 2), rien d'ecrit, rien d'efface
    ecrire_passe(args('capture', 'Syria', zoom=16, rectangle=RECT), Monde('alt'))
    with pytest.raises(SystemExit) as e:
        _tuiler(args('tuiler', 'Syria', zoom=16, rectangle=RECT), processus=1)
    assert e.value.code == 2
    assert _etat_sorties(work) == apres
    assert _lire_manifeste(work, 'alt-syria-rect')['zoom'] == 15
    # et par la commande complete (executer) : code 2, pas de question
    with pytest.raises(SystemExit):
        ft.executer(args('tuiler', 'Syria', zoom=16, rectangle=RECT))


def test_tu14_seule_la_serie_du_zoom_de_la_commande_est_remplacee(passe, monkeypatch):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    out = work / 'out' / 'tuiles'
    out.mkdir(parents=True)
    voisine = out / 'alt-syria-rect_z14_001.tar'                   # une autre serie : intouchee
    orpheline = out / 'alt-syria-rect_z15_009.tar'                 # une part en trop de la serie du zoom 15 : effacee
    autre_zone = out / 'alt-syria-rect_z15_z15_001.tar'            # nom de zone voisin : intouche
    for p in (voisine, orpheline, autre_zone):
        p.write_bytes(b'x')
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    assert voisine.exists() and autre_zone.exists() and not orpheline.exists()
    assert (out / 'alt-syria-rect_z15_001.tar').exists()


def test_tu14_un_tuiler_arrete_en_route_laisse_les_archives_intactes(passe, monkeypatch):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    argv = args('tuiler', 'Syria', zoom=15, rectangle=RECT)
    assert _tuiler(argv, processus=1) == 0
    avant = _etat_sorties(work)
    # 1. une exception pendant la reprojection
    vrai = T.traiter_bloc
    appels = []

    def casse(spec, *a):
        appels.append(1)
        if len(appels) == 3:
            raise RuntimeError('arret en route')
        return vrai(spec, *a)
    monkeypatch.setattr(T, 'traiter_bloc', casse)
    assert ft.executer(argv + ['--processus=1']) == 3
    assert _etat_sorties(work) == avant
    monkeypatch.setattr(T, 'traiter_bloc', vrai)
    # 2. une exception pendant l'ecriture des parts : les parts provisoires restent, les archives en place aussi
    vrai_emp = T.empaqueter

    def emp_puis_casse(*a, **k):
        vrai_emp(*a, **k)
        raise RuntimeError('arret pendant les archives')
    monkeypatch.setattr(T, 'empaqueter', emp_puis_casse)
    assert ft.executer(argv + ['--processus=1']) == 3
    out = work / 'out' / 'tuiles'
    assert list(out.glob('*.tmp'))
    assert {n: b for n, b in _etat_sorties(work).items() if not n.endswith('.tmp')} == avant
    # 3. une reprise reussie nettoie les provisoires
    monkeypatch.setattr(T, 'empaqueter', vrai_emp)
    assert _tuiler(argv, processus=1) == 0
    assert not list(out.glob('*.tmp')) and _etat_sorties(work) == avant


def test_tu14_un_aerodromes_refuse_n_efface_rien(cfg, monkeypatch):
    cfg()
    work = f10capture.CFG.work_dir
    out = work / 'out' / 'tuiles'
    out.mkdir(parents=True)
    f10capture.out_theatre('Syria').mkdir(parents=True)             # un theatre sans aucune base
    for n in ('alt-syria-aerodromes_z17_001.tar', 'alt-syria-aerodromes_z18_001.tar', 'alt-syria-aerodromes.json',
              'alt-syria-aerodromes.config.json'):
        (out / n).write_bytes(b'ancien ' + n.encode())
    brut = work / 'tuiles_brut' / 'alt-syria-aerodromes'
    (brut / '17').mkdir(parents=True)
    (brut / '17' / 'x').write_bytes(b'x')
    avant = _etat_sorties(work)
    with pytest.raises(SystemExit) as e:
        ft.main(['f10tiles.py', 'aerodromes', 'Syria'])
    assert e.value.code == 2
    assert _etat_sorties(work) == avant and (brut / '17' / 'x').exists()
    # un manifeste d'un autre zoom cible : refus aussi
    (out / 'alt-syria-aerodromes.json').write_text(json.dumps({'zoom': 16}), encoding='utf-8')
    with pytest.raises(SystemExit) as e:
        ft.main(['f10tiles.py', 'aerodromes', 'Syria'])
    assert e.value.code == 2


# ------------------------------------------------------------------ TU15 : §3 le disque et rmtree

def test_tu15_disque_avant_tuiler(passe, monkeypatch):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    argv = args('tuiler', 'Syria', zoom=15, rectangle=RECT)
    assert _tuiler(argv, processus=1) == 0
    avant = _etat_sorties(work)
    n = len(T.tuiles_de_emprise(z.emprise, 15))
    besoin = n * 4 / 3 * 50000 * 2 + 10 * 10 ** 9                  # alt : 50 000 octets par tuile
    monkeypatch.setattr(ft, '_espace_libre', lambda p: besoin - 1)
    assert ft.executer(argv + ['--processus=1']) == 3
    assert 'Place disque insuffisante' in _question() and _etat_sorties(work) == avant and (z.dossier / 'tuiles').is_dir()
    monkeypatch.setattr(ft, '_espace_libre', lambda p: besoin + 1)
    assert ft.executer(argv + ['--processus=1']) == 0


def test_tu15_disque_avant_tuiler_sat_a_20000_octets(cfg, monkeypatch):
    cfg()
    z, g = ecrire_passe(args('capture', 'Syria', couche='sat', zoom=15, rectangle=RECT_PETIT), Monde('alt'))
    argv = args('tuiler', 'Syria', couche='sat', zoom=15, rectangle=RECT_PETIT)
    n = len(T.tuiles_de_emprise(z.emprise, 15))
    besoin = n * 4 / 3 * 20000 * 2 + 10 * 10 ** 9
    monkeypatch.setattr(ft, '_espace_libre', lambda p: besoin - 1)
    assert ft.executer(argv + ['--processus=1']) == 3
    monkeypatch.setattr(ft, '_espace_libre', lambda p: besoin + 1)
    assert ft.executer(argv + ['--processus=1']) == 0


def test_tu15_rmtree_sans_ignore_errors_une_erreur_arrete_en_code_3(passe, monkeypatch):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    argv = args('tuiler', 'Syria', zoom=15, rectangle=RECT)
    assert _tuiler(argv, processus=1) == 0
    avant = _etat_sorties(work)
    vus = []

    def boum(d, *a, **k):
        vus.append((d, k))
        raise PermissionError(13, 'fichier verrouille', str(d))
    monkeypatch.setattr(ft.shutil, 'rmtree', boum)
    assert ft.executer(argv + ['--processus=1']) == 3
    assert vus and 'verrouille' in _question() and all('ignore_errors' not in k for _, k in vus)
    assert _etat_sorties(work) == avant


# ------------------------------------------------------------------ TU15 : §4 WebP, case non stable, tuiler sans ecran

def _image_type_sat(w=3350, h=1404):
    """Une image de type Sat : du relief a toutes les echelles et du grain par pixel (peu compressible)."""
    img = Monde('bruit', graine=3).image(219000.0, -48000.0, 1.0, w, h)
    rng = np.random.default_rng(1)
    return np.clip(img.astype(np.int16) + rng.normal(0, 5, img.shape).astype(np.int16), 0, 255).astype(np.uint8)


def test_tu15_webp_sans_perte_pixel_a_pixel_et_en_moins_de_0_5_s(tmp_path):
    img = _image_type_sat()
    chemin = tmp_path / 'r0_c0.webp'
    meilleur = min(_dure(lambda: ft._ecrire_image(img, chemin, 'sat')) for _ in range(3))
    lu = np.asarray(Image.open(chemin).convert('RGB'))
    assert lu.shape == img.shape and (lu == img).all()                          # sans perte, pixel a pixel
    print(f'webp sans perte {ft.WEBP_CAPTURE} : {meilleur:.3f} s, {chemin.stat().st_size} octets pour {img.shape[1]} x {img.shape[0]}')
    assert ft.WEBP_CAPTURE['lossless'] is True and ft.WEBP_CAPTURE['method'] in (0, 1)
    assert meilleur <= 0.5
    assert not list(tmp_path.glob('*.tmp'))


def _dure(f):
    t = time.perf_counter()
    f()
    return time.perf_counter() - t


class _Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_tu15_le_temps_d_une_case_non_stable_entre_dans_l_estimation(cfg, monkeypatch, capsys):
    cfg(ecran=ECRAN_MOYEN)
    FauxDcs(Monde('bruit'), ECRAN_MOYEN).installer(monkeypatch)
    monkeypatch.setattr(T, 'mode_de_image', lambda img: ('alt', 0.5, 100.0))
    horloge = _Horloge()
    monkeypatch.setattr(ft, '_horloge', horloge)
    reel = f10capture.move_and_stabilize
    appels = []

    def mesure(x, z, alt, expect_change=True, **kw):
        appels.append((x, z))
        img, delai, n, ok, cmd = reel(x, z, alt, expect_change, **kw)
        if len(appels) == 7:                                       # la premiere case (l'etalonnage en prend 6) : instable et longue
            horloge.t += 100.0
            return img, 100.0, n, False, cmd
        horloge.t += 10.0
        return img, delai, n, ok, cmd
    monkeypatch.setattr(f10capture, 'move_and_stabilize', mesure)
    argv = args('capture', 'Syria', zoom=15, rectangle=RECT)
    # etalonnage : 60 s ; case instable : 100 s -> 160 s ; la moyenne des cases (100 s) ne tient plus dans 200 s : arret propre
    assert ft.executer(argv + ['--duree-max-s=200']) == 0
    sortie = capsys.readouterr().out
    assert 'NON STABLE' in sortie and 'INCOMPLET 0/' in sortie
    assert len(appels) == 7                                         # aucune autre case tentee apres l'instable
    # sans ce compte, l'estimation serait celle d'une case par defaut et la passe tenterait la suivante
    assert f10capture.EST_PER_CAPTURE_S + 160.0 < 200.0


def test_tu15_tuiler_ne_depend_pas_de_l_ecran(passe, monkeypatch):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir

    def sans_ecran(*a, **k):
        raise AssertionError('l\'ecran configure ne doit pas servir a tuiler')
    monkeypatch.setattr(f10capture, 'usable_px', sans_ecran)
    monkeypatch.setattr(f10capture, 'screen_size', sans_ecran)
    monkeypatch.setattr(f10capture, 'crop_offset', sans_ecran)
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=1) == 0
    assert (work / 'out' / 'tuiles' / 'alt-syria-rect.json').is_file()
    # et il refuse toujours une passe d'autres arguments (theatre, couche, zoom, zone, emprise)
    for autre in (args('tuiler', 'Syria', zoom=15, rectangle='35.0,36.0,35.05,36.06'),):
        with pytest.raises(SystemExit) as e:
            _tuiler(autre, processus=1)
        assert e.value.code == 2


# ------------------------------------------------------------------ TU15 : §5 processus, source trop grande

@pytest.mark.parametrize('coeurs,attendu', [(16, 14), (8, 6), (4, 2), (3, 1), (2, 1), (1, 1), (None, 1)])
def test_tu15_processus_par_defaut(monkeypatch, coeurs, attendu):
    monkeypatch.setattr(os, 'cpu_count', lambda: coeurs)
    assert ft.processus_defaut() == attendu == max(1, (coeurs or 1) - 2)


def test_tu15_tuiler_prend_le_defaut_sans_l_option(passe, monkeypatch):
    z, g, monde, proj = passe
    monkeypatch.setattr(os, 'cpu_count', lambda: 5)
    vus = []
    vrai = ft._executer_blocs
    monkeypatch.setattr(ft, '_executer_blocs', lambda taches, spec, n, local: (vus.append(n), vrai(taches, spec, 1, local))[1])
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT)) == 0
    assert set(vus) == {3}
    vus.clear()
    assert _tuiler(args('tuiler', 'Syria', zoom=15, rectangle=RECT), processus=2) == 0
    assert set(vus) == {2}


def test_tu15_le_cache_d_images_est_borne():
    ouvertures = []
    cache = T.CacheImages(lambda cle: ouvertures.append(cle) or np.zeros(1), n=T.CACHE_IMAGES)
    for i in range(T.CACHE_IMAGES * 3):
        cache(i)
    assert len(cache._d) == T.CACHE_IMAGES == 16


def test_tu15_la_limite_de_pillow_est_levee_et_une_source_trop_grande_est_refusee(tmp_path):
    assert Image.MAX_IMAGE_PIXELS is None                                        # comme render/analysis.py
    large = tmp_path / 'large.png'
    Image.new('1', (T.COTE_MAX_SOURCE_PX + 1, 8000), 0).save(large)               # 262 millions de pixels : plus de deux fois la limite de Pillow
    with Image.open(large) as im:                                                # s'ouvre : plus de DecompressionBombError
        assert im.size == (T.COTE_MAX_SOURCE_PX + 1, 8000)
    with pytest.raises(T.SourceTropGrande) as e:
        T.charger_image_rgb(large)
    assert str(large) in str(e.value) and '32768' in str(e.value)
    juste = tmp_path / 'juste.png'
    Image.new('RGB', (T.COTE_MAX_SOURCE_PX, 2), (1, 2, 3)).save(juste)
    assert T.charger_image_rgb(juste).shape == (2, T.COTE_MAX_SOURCE_PX, 3)


@pytest.mark.parametrize('processus', [1, 2])
def test_tu15_tuiler_arrete_en_code_3_sur_une_source_trop_grande(passe, processus):
    z, g, monde, proj = passe
    work = f10capture.CFG.work_dir
    c = g['cases'][4]
    cible = ft._nom_image(z, c)
    Image.new('RGB', (T.COTE_MAX_SOURCE_PX + 1, 2), (1, 2, 3)).save(cible)
    assert ft.executer(args('tuiler', 'Syria', zoom=15, rectangle=RECT) + [f'--processus={processus}']) == 3
    q = _question()
    assert cible.name in q and 'trop grande' in q
    assert not (work / 'out' / 'tuiles' / 'alt-syria-rect.json').exists()


def test_tu15_aerodromes_refuse_une_mosaique_trop_grande(cfg, monkeypatch, tmp_path):
    cfg()
    racine = f10capture.out_theatre('Syria') / 'Base'
    racine.mkdir(parents=True)
    mos = racine / 'mosaic.png'
    Image.new('RGB', (T.COTE_MAX_SOURCE_PX + 1, 2), (1, 2, 3)).save(mos)
    monkeypatch.setattr(ft.georef, 'load_base_json', lambda d: {})
    monkeypatch.setattr(ft.georef, 'georef_from_base',
                        lambda bj: type('G', (), {'width': T.COTE_MAX_SOURCE_PX + 1, 'height': 2, 'mpp': 0.5, 'x_top': 1.0, 'z_left': 1.0})())
    with pytest.raises(T.SourceTropGrande) as e:
        ft.fabriquer_aerodromes('Syria', ['Base'], f10capture.out_theatre('Syria'), tmp_path / 'brut', geo.Projection.for_theatre('Syria'))
    assert 'mosaic.png' in str(e.value)
