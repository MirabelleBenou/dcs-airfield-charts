"""Pilote de la file de nuit : l'etape `tuiles` (capture/f10tiles.py capture puis tuiler) : validation, commandes depliees,
dependance du tuilage, rassemblement (out/tuiles/ oui, tuiles_brut/ jamais). Aucun DCS, aucun Windows."""
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'capture' / 'autonome'))
import nuit  # noqa: E402

from test_autonome import Monde, ecrire_file, file_exemple, membres, monde, statuts   # noqa: E402,F401  (monde : fixture)


def tuiles(**kw):
    e = {'type': 'tuiles', 'theatre': 'Syria', 'couche': 'alt', 'zoom': 15, 'emprise': 'zones/syria.geojson'}
    e.update(kw)
    return e


def file_tuiles(*etapes, **plus):
    d = file_exemple(**plus)
    d['etapes'] = list(etapes)
    return d


def sortie_verifier(tmp_path, capsys, data):
    code = nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, data))])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ------------------------------------------------------------------ TU7 : verifier accepte

def test_types_et_cles():
    assert nuit.TYPES == ('charts', 'contexte', 'panneaux', 'tuiles')
    assert nuit.valider_file(file_tuiles(tuiles()))['etapes'] == [
        {'type': 'tuiles', 'theatre': 'Syria', 'couche': 'alt', 'zoom': 15, 'emprise': 'zones/syria.geojson'}]


def test_verifier_etape_tuiles_avec_emprise(tmp_path, capsys):
    code, out, err = sortie_verifier(tmp_path, capsys, file_tuiles(tuiles()))
    assert code == 0 and err == ''
    lignes = out.splitlines()
    assert lignes[0] == '2 commande(s) :'
    assert lignes[1] == '    -> vue f10 demandee'
    assert lignes[2] == '01  [f10]  f10tiles.py capture Syria --couche alt --zoom 15 --emprise zones/syria.geojson --duree-max-s 3480'
    assert lignes[3] == '02  [f10]  f10tiles.py tuiler Syria --couche alt --zoom 15 --emprise zones/syria.geojson'
    assert lignes[4].startswith('budget_s du crochet conseille')


def test_verifier_etape_tuiles_avec_rectangle_nom_et_zoom_min(tmp_path, capsys):
    e = tuiles(couche='sat', zoom=13, nom='essai-1', zoom_min=10)
    del e['emprise']
    e['rectangle'] = [34.5, 35.5, 35.0, 36.25]
    code, out, _ = sortie_verifier(tmp_path, capsys, file_tuiles(e, delai_commande_s=5400, config='C:\\c.json'))
    assert code == 0
    cmds = [l for l in out.splitlines() if l[:2].isdigit()]
    assert cmds == ['01  [f10]  f10tiles.py capture Syria --couche sat --zoom 13 --rectangle 34.5,35.5,35,36.25 --nom essai-1 '
                    '--duree-max-s 5280 --config C:\\c.json',
                    '02  [f10]  f10tiles.py tuiler Syria --couche sat --zoom 13 --rectangle 34.5,35.5,35,36.25 --nom essai-1 '
                    '--zoom-min 10 --config C:\\c.json']


def test_verifier_rectangle_en_entiers_et_negatifs(tmp_path, capsys):
    e = tuiles()
    del e['emprise']
    e['rectangle'] = [-10, -20, 5, 0.5]
    code, out, _ = sortie_verifier(tmp_path, capsys, file_tuiles(e))
    assert code == 0 and '--rectangle -10,-20,5,0.5 ' in out


def test_verifier_tuiles_avec_les_autres_etapes(tmp_path, capsys):
    d = file_exemple()
    d['etapes'] = d['etapes'] + [tuiles(), tuiles(couche='sat', zoom=16, nom='b')]
    code, out, _ = sortie_verifier(tmp_path, capsys, d)
    assert code == 0
    cmds = [l for l in out.splitlines() if l[:2].isdigit()]
    assert [l.split()[0] for l in cmds] == ['01', '02', '03', '04', '05', '06', '07', '08', '09']
    assert 'f10tiles.py capture Syria --couche alt' in cmds[5] and 'f10tiles.py tuiler Syria --couche alt' in cmds[6]
    assert '-> vue f11 demandee' in out and out.count('-> vue') == 3        # f10, f11, f10 : l'etape tuiles est en vue f10
    assert out.splitlines()[out.splitlines().index(cmds[5]) - 1] == '    -> vue f10 demandee'


def test_deplier_depend_de_la_capture():
    cmds = nuit.deplier(nuit.valider_file(file_tuiles(tuiles(), tuiles(couche='sat'))))
    assert [(c['outil'], c['commande'], c['vue'], c['depend_de']) for c in cmds] == [
        ('f10tiles.py', 'capture', 'f10', None), ('f10tiles.py', 'tuiler', 'f10', 1),
        ('f10tiles.py', 'capture', 'f10', None), ('f10tiles.py', 'tuiler', 'f10', 3)]
    assert cmds[0]['args'][-2:] == ['--duree-max-s', '3480']


# ------------------------------------------------------------------ TU7 : verifier refuse

def _sans(**cles):
    e = tuiles()
    for k in cles:
        e.pop(k, None)
    return e


def _rect(r, **kw):
    e = tuiles(**kw)
    del e['emprise']
    e['rectangle'] = r
    return e


REFUS_TUILES = [
    (tuiles(rectangle=[1, 2, 3, 4]), 'emprise'),                              # emprise et rectangle ensemble
    (_sans(emprise=1), 'emprise'),                                            # aucun des deux
    (tuiles(couche='map'), 'couche'),
    (_sans(couche=1), 'couche'),
    (tuiles(couche=1), 'couche'),
    (tuiles(zoom=7), 'zoom'),
    (tuiles(zoom=19), 'zoom'),
    (tuiles(zoom=15.5), 'zoom'),
    (tuiles(zoom='15'), 'zoom'),
    (tuiles(zoom=True), 'zoom'),
    (_sans(zoom=1), 'zoom'),
    (tuiles(bases=['Adana']), 'bases'),
    (tuiles(marge=2000), 'marge'),
    (tuiles(zones=[]), 'zones'),                                              # une cle du relief
    (tuiles(pas_m=100), 'pas_m'),
    (tuiles(emprise=''), 'emprise'),
    (tuiles(emprise=3), 'emprise'),
    (_rect([1, 2, 3]), 'rectangle'),
    (_rect('1,2,3,4'), 'rectangle'),
    (_rect([1, 2, 3, 'a']), 'rectangle'),
    (_rect([1, 2, 3, True]), 'rectangle'),
    (_rect([1, 2, 3, float('inf')]), 'rectangle'),
    (_rect([10, 2, 5, 4]), 'rectangle'),                                      # lat_s > lat_n
    (_rect([1, 20, 3, 4]), 'rectangle'),                                      # lon_o > lon_e
    (_rect([-91, 2, 3, 4]), 'rectangle'),
    (tuiles(nom='a b'), 'nom'),
    (tuiles(nom=''), 'nom'),
    (tuiles(zoom_min=16), 'zoom_min'),
    (tuiles(zoom_min=-1), 'zoom_min'),
    (tuiles(zoom_min='8'), 'zoom_min'),
    (tuiles(truc=1), 'truc'),
    (_sans(theatre=1), 'theatre'),
    ({'type': 'charts', 'theatre': 'T', 'bases': ['B'], 'zoom': 15}, 'zoom'),     # les cles des tuiles hors de l'etape tuiles
    ({'type': 'charts', 'theatre': 'T', 'bases': ['B'], 'couche': 'alt'}, 'couche'),
    ({'type': 'contexte', 'theatre': 'T', 'bases': ['B'], 'marge': 100, 'emprise': 'x'}, 'emprise'),
    ({'type': 'panneaux', 'theatre': 'T', 'bases': ['B'], 'rectangle': [1, 2, 3, 4]}, 'rectangle'),
    ({'type': 'relief', 'theatre': 'T', 'zones': [{'nom': 'a', 'lat': 1, 'lon': 1, 'cote_m': 1000}], 'pas_m': 100, 'nom': 'x'}, 'nom'),
]


@pytest.mark.parametrize('etape,mot', REFUS_TUILES)
def test_verifier_refuse_une_etape_tuiles_invalide(tmp_path, capsys, etape, mot):
    d = file_exemple()
    d['etapes'] = [etape]
    code, out, err = sortie_verifier(tmp_path, capsys, d)
    assert code == 2 and out == ''
    assert err.startswith('Erreur') and 'etapes[0]' in err and mot in err


def test_verifier_refuse_un_delai_trop_court_avec_tuiles(tmp_path, capsys):
    code, _, err = sortie_verifier(tmp_path, capsys, file_tuiles(tuiles(), delai_commande_s=120))
    assert code == 2 and 'delai_commande_s' in err and '120' in err
    assert sortie_verifier(tmp_path, capsys, file_tuiles(tuiles(), delai_commande_s=121))[0] == 0
    d = file_exemple(delai_commande_s=60)                       # sans etape tuiles, le delai reste libre
    assert sortie_verifier(tmp_path, capsys, d)[0] == 0


def test_zoom_aux_bornes(tmp_path, capsys):
    for z in (8, 18):
        assert sortie_verifier(tmp_path, capsys, file_tuiles(tuiles(zoom=z)))[0] == 0
    assert sortie_verifier(tmp_path, capsys, file_tuiles(tuiles(zoom=12, zoom_min=12)))[0] == 0
    assert sortie_verifier(tmp_path, capsys, file_tuiles(tuiles(zoom=12, zoom_min=0)))[0] == 0


def test_verifier_en_sous_processus_isole_avec_tuiles(tmp_path):
    import subprocess
    p = ecrire_file(tmp_path, file_tuiles(tuiles()))
    r = subprocess.run([sys.executable, '-I', str(ROOT / 'capture' / 'autonome' / 'nuit.py'), 'verifier', str(p)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and 'f10tiles.py tuiler Syria' in r.stdout


# ------------------------------------------------------------------ TU7 : la nuit jouee

def test_nuit_joue_capture_puis_tuilage(monde):
    assert monde.jouer(file_tuiles(tuiles(nom='z1'))) == 0
    a = monde.appels
    cap = str(ROOT / 'capture')
    cfg = str(monde.cfg)
    assert [c['argv'] for c in a] == [
        ['PYTHON', '-I', cap + '/f10tiles.py', 'capture', 'Syria', '--couche', 'alt', '--zoom', '15', '--emprise',
         'zones/syria.geojson', '--nom', 'z1', '--duree-max-s', '3480', '--config', cfg],
        ['PYTHON', '-I', cap + '/f10tiles.py', 'tuiler', 'Syria', '--couche', 'alt', '--zoom', '15', '--emprise',
         'zones/syria.geojson', '--nom', 'z1', '--config', cfg]]
    assert [c['timeout'] for c in a] == [3600, 3600]
    assert [c['vue'] for c in a] == ['f10', 'f10']
    b = monde.bilan()
    assert statuts(b) == ['ok', 'ok']
    assert sorted(p.name for p in monde.nuit_dir.glob('*.log')) == ['01_f10tiles_capture_z1.log', '02_f10tiles_tuiler_z1.log', 'nuit.log']


@pytest.mark.parametrize('capture,attendu', [({'code': 1}, ['echec', 'saute_visee']),
                                             ({'code': 3, 'question': 'Mode de la carte : map'}, ['question', 'saute_visee']),
                                             ({'delai': True, 'code': None, 'duree': 3600}, ['delai', 'saute_visee']),
                                             ({'code': 0}, ['ok', 'ok'])])
def test_tuiler_saute_si_capture_a_echoue(monde, capture, attendu):
    monde.scripts = {1: capture}
    assert monde.jouer(file_tuiles(tuiles())) == 0
    assert statuts(monde.bilan()) == attendu
    assert [c['n'] for c in monde.appels] == ([1] if attendu[1] == 'saute_visee' else [1, 2])


def test_message_neutre_du_tuilage_saute(monde):
    monde.scripts = {1: {'code': 1}}
    monde.jouer(file_tuiles(tuiles()))
    journal = (monde.nuit_dir / 'nuit.log').read_text(encoding='utf-8')
    ligne = next(l for l in journal.splitlines() if '02 non lancee' in l)
    assert ligne.endswith('02 non lancee : la commande dont elle depend a echoue') and 'visee' not in ligne


def test_message_des_visees_inchange(monde):
    monde.scripts = {4: {'code': 7}}                              # etape de charts : la visee echoue, la capture est sautee
    monde.jouer(file_exemple())
    journal = (monde.nuit_dir / 'nuit.log').read_text(encoding='utf-8')
    assert '05 non lancee : la visee a echoue' in journal


def test_une_capture_en_echec_ne_saute_que_son_tuilage(monde):
    monde.scripts = {1: {'code': 1}}
    monde.jouer(file_tuiles(tuiles(nom='a'), tuiles(nom='b')))
    assert statuts(monde.bilan()) == ['echec', 'saute_visee', 'ok', 'ok']


def test_tuilage_en_echec_code_4(monde):
    monde.scripts = {2: {'code': 4}}
    monde.jouer(file_tuiles(tuiles()))
    assert statuts(monde.bilan()) == ['ok', 'echec']


# ------------------------------------------------------------------ TU7 : le rassemblement

def test_le_rassemblement_ne_prend_rien_de_tuiles_brut_et_prend_out_tuiles(monde):
    png = b'\x89PNG\r\n\x1a\n' + bytes(300)
    monde.scripts = {
        1: {'fichiers': [('tuiles_brut/alt-syria-z15-theatre/r0_c0.png', png), ('tuiles_brut/alt-syria-z15-theatre/r0_c1.png', png),
                         ('tuiles_brut/alt-syria-z15-theatre/grille.json', b'{}'),
                         ('tuiles_brut/alt-syria-z15-theatre/captures.jsonl', b'{}\n'),
                         ('tuiles_brut/sat-syria-z15-theatre/r0_c0.webp', b'RIFF....WEBP')]},
        2: {'fichiers': [('out/tuiles/alt-syria-theatre_z15_001.tar', b'tar' * 100), ('out/tuiles/alt-syria-theatre_z15_002.tar', b'tar'),
                         ('out/tuiles/alt-syria-theatre.json', b'{}'), ('out/tuiles/alt-syria-theatre.config.json', b'{}'),
                         ('tuiles_brut/alt-syria-z15-theatre/tuiles/15/1/2.png', png)]}}
    monde.jouer(file_tuiles(tuiles()))
    z = membres(monde)
    assert len(z) == 1
    (nom, info), = z.items()
    fichiers = sorted(n for n in info if not n.startswith('autonome/'))
    assert fichiers == ['out/tuiles/alt-syria-theatre.config.json', 'out/tuiles/alt-syria-theatre.json',
                        'out/tuiles/alt-syria-theatre_z15_001.tar', 'out/tuiles/alt-syria-theatre_z15_002.tar']
    assert not any('tuiles_brut' in n for n in info)
    with zipfile.ZipFile(monde.nuit_dir / nom) as zf:
        assert zf.read('out/tuiles/alt-syria-theatre_z15_001.tar') == b'tar' * 100 and zf.testzip() is None
