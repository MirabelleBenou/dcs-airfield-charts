"""Etape `relief` de la file de nuit, cote pilote (capture/autonome/nuit.py) : la file (verifier), le depliage, et une nuit jouee
avec un faux crochet qui sert releve_demande.lua. Aucun DCS, aucun Windows."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'capture' / 'autonome'))
import nuit  # noqa: E402
from test_autonome import Monde, T0, ecrire_file, statuts  # noqa: E402

ZONE_ALEPPO = {'nom': 'aleppo', 'lat': 36.2, 'lon': 37.25, 'cote_m': 10000}
ZONE_RAYAK = {'nom': 'rayak', 'lat': 33.85, 'lon': 36.0, 'cote_m': 10000}

FILE_EXEMPLE = {
    'etapes': [
        {'type': 'charts', 'theatre': 'Syria', 'bases': ['Aleppo']},
        {'type': 'relief', 'theatre': 'Syria', 'pas_m': 100, 'zones': [ZONE_ALEPPO, ZONE_RAYAK]},
        {'type': 'relief', 'theatre': 'Syria', 'pas_m': 30, 'methode': 'mission', 'surface': True, 'budget_image_ms': 60,
         'zones': [ZONE_RAYAK]},
    ]
}

SORTIE_EXEMPLE = """4 commande(s) :
    -> vue f10 demandee
01  [f10]  f10capture.py capture Syria Aleppo
02  [--]  relief Syria aleppo : 101 x 101 points au pas de 100 m, methode export
03  [--]  relief Syria rayak : 101 x 101 points au pas de 100 m, methode export
04  [--]  relief Syria rayak : 334 x 334 points au pas de 30 m, methode mission, surface
budget_s du crochet conseille : 18000 s au moins
"""


def relief(**plus):
    e = {'type': 'relief', 'theatre': 'Syria', 'pas_m': 100, 'zones': [dict(ZONE_ALEPPO)]}
    e.update(plus)
    return e


def file_de(*etapes, **plus):
    return dict({'etapes': list(etapes)}, **plus)


# ------------------------------------------------------------------ R1 : verifier

def test_r1_la_file_d_exemple_sortie_exacte(tmp_path, capsys):
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, FILE_EXEMPLE))]) == 0
    assert capsys.readouterr().out == SORTIE_EXEMPLE


def test_r1_en_sous_processus_isole(tmp_path):
    import subprocess
    p = ecrire_file(tmp_path, FILE_EXEMPLE)
    r = subprocess.run([sys.executable, '-I', str(ROOT / 'capture' / 'autonome' / 'nuit.py'), 'verifier', str(p)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout == SORTIE_EXEMPLE


def test_r1_types_inchanges_et_defauts():
    assert nuit.TYPES == ('charts', 'contexte', 'panneaux', 'tuiles')
    e = nuit.valider_file(file_de(relief()))['etapes'][0]
    assert (e['methode'], e['surface'], e['budget_image_ms']) == ('export', False, 40)
    assert e['zones'] == [ZONE_ALEPPO] and e['pas_m'] == 100 and 'bases' not in e


def test_r1_budget_conseille_inchange(tmp_path, capsys):
    nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, file_de(relief())))])
    assert capsys.readouterr().out.splitlines()[-1] == 'budget_s du crochet conseille : 18000 s au moins'


def test_r1_pas_decimal_dans_la_ligne_et_la_sortie(tmp_path, capsys):
    f = file_de(relief(pas_m=12.5, zones=[dict(ZONE_ALEPPO, cote_m=100)]))
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, f))]) == 0
    assert '9 x 9 points au pas de 12.5 m, methode export' in capsys.readouterr().out
    c = nuit.deplier(nuit.valider_file(f))[0]
    assert c['releve']['sortie'] == ('out', 'relief', 'Syria', 'aleppo_12.5m_export.csv')


def _z(**plus):
    d = dict(ZONE_ALEPPO)
    d.update(plus)
    return d


REFUS = [
    (file_de(relief(surface=True)), ['etapes[0]', 'surface', 'export']),
    (file_de(relief(bases=['A'])), ['etapes[0]', 'bases']),
    (file_de(relief(marge=100)), ['etapes[0]', 'marge']),
    (file_de({'type': 'charts', 'theatre': 'T', 'bases': ['B'], 'pas_m': 100}), ['etapes[0]', 'pas_m', 'charts']),
    (file_de({'type': 'contexte', 'theatre': 'T', 'bases': ['B'], 'marge': 100, 'zones': []}), ['zones', 'contexte']),
    (file_de({'type': 'panneaux', 'theatre': 'T', 'bases': ['B'], 'methode': 'export'}), ['methode', 'panneaux']),
    (file_de({'type': 'charts', 'theatre': 'T', 'bases': ['B'], 'surface': False}), ['surface', 'charts']),
    (file_de({'type': 'charts', 'theatre': 'T', 'bases': ['B'], 'budget_image_ms': 40}), ['budget_image_ms', 'charts']),
    (file_de(relief(zones=[{k: v for k, v in ZONE_ALEPPO.items() if k != 'cote_m'}])), ['zones[0]', 'cote_m']),
    (file_de(relief(zones=[_z(nom='a b')])), ['zones[0]', 'nom']),
    (file_de(relief(zones=[_z(nom='')])), ['zones[0]', 'nom']),
    (file_de(relief(zones=[_z(nom='a/b')])), ['zones[0]', 'nom']),
    (file_de(relief(zones=[_z(nom='aleppo\n')])), ['zones[0]', 'nom']),
    (file_de(relief(pas_m=1, zones=[_z(cote_m=2000)])), ['zones[0]', '2001', '4000000']),
    (file_de(relief(pas_m=1, zones=[_z(cote_m=100000)])), ['zones[0]', '4000000']),
    (file_de(relief(zones=[_z(cote_m=50)])), ['zones[0]', 'n = 1']),
    (file_de(relief(zones=[ZONE_ALEPPO, ZONE_RAYAK, ZONE_ALEPPO])), ['zones[2]', 'meme sortie']),
    (file_de(relief(), relief()), ['etapes[1]', 'meme sortie']),
    (file_de(relief(zones=[_z(nom='Aleppo'), ZONE_ALEPPO])), ['zones[1]', 'meme sortie']),
    (file_de(relief(methode='autre')), ['etapes[0]', 'methode']),
    (file_de(relief(methode=1)), ['etapes[0]', 'methode']),
    (file_de(relief(budget_image_ms=0)), ['etapes[0]', 'budget_image_ms']),
    (file_de(relief(budget_image_ms=1001)), ['budget_image_ms']),
    (file_de(relief(budget_image_ms=-5)), ['budget_image_ms']),
    (file_de(relief(budget_image_ms='40')), ['budget_image_ms']),
    (file_de(relief(budget_image_ms=True)), ['budget_image_ms']),
    (file_de(relief(pas_m=0)), ['etapes[0]', 'pas_m']),
    (file_de(relief(pas_m='100')), ['pas_m']),
    (file_de(relief(pas_m=True)), ['pas_m']),
    (file_de({k: v for k, v in relief().items() if k != 'pas_m'}), ['etapes[0]', 'pas_m']),
    (file_de({k: v for k, v in relief().items() if k != 'zones'}), ['etapes[0]', 'zones']),
    (file_de(relief(zones=[])), ['zones']),
    (file_de(relief(zones='aleppo')), ['zones']),
    (file_de(relief(zones=['aleppo'])), ['zones[0]']),
    (file_de(relief(zones=[_z(truc=1)])), ['zones[0]', 'truc']),
    (file_de(relief(zones=[_z(lat=91)])), ['zones[0]', 'lat']),
    (file_de(relief(zones=[_z(lat=-90.5)])), ['zones[0]', 'lat']),
    (file_de(relief(zones=[_z(lon=181)])), ['zones[0]', 'lon']),
    (file_de(relief(zones=[_z(lon='37')])), ['zones[0]', 'lon']),
    (file_de(relief(zones=[_z(lat=True)])), ['zones[0]', 'lat']),
    (file_de(relief(zones=[_z(cote_m=0)])), ['zones[0]', 'cote_m']),
    (file_de(relief(zones=[_z(cote_m='10000')])), ['zones[0]', 'cote_m']),
    (file_de(relief(surface='oui', methode='mission')), ['surface']),
    (file_de(relief(truc=1)), ['etapes[0]', 'truc']),
    (file_de(relief(theatre='')), ['etapes[0]', 'theatre']),
]


@pytest.mark.parametrize('data,mots', REFUS)
def test_r1_verifier_refuse(tmp_path, capsys, data, mots):
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, data))]) == 2
    err = capsys.readouterr().err
    assert err.startswith('Erreur')
    for m in mots:
        assert m in err, (m, err)


def test_r1_valeurs_non_finies_refusees(tmp_path, capsys):
    for texte in ['{"etapes": [{"type": "relief", "theatre": "T", "pas_m": Infinity, "zones": [{"nom": "a", "lat": 1, "lon": 1, "cote_m": 100}]}]}',
                  '{"etapes": [{"type": "relief", "theatre": "T", "pas_m": 10, "zones": [{"nom": "a", "lat": 1, "lon": 1, "cote_m": Infinity}]}]}',
                  '{"etapes": [{"type": "relief", "theatre": "T", "pas_m": 10, "zones": [{"nom": "a", "lat": NaN, "lon": 1, "cote_m": 100}]}]}',
                  '{"etapes": [{"type": "relief", "theatre": "T", "pas_m": NaN, "zones": [{"nom": "a", "lat": 1, "lon": 1, "cote_m": 100}]}]}']:
        assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, texte))]) == 2
        assert capsys.readouterr().err.startswith('Erreur')


def test_r1_bornes_acceptees():
    # n x n = 4 000 000 exactement ; n = 2 exactement
    f = nuit.valider_file(file_de(relief(pas_m=1, zones=[_z(cote_m=1999)])))
    assert nuit.deplier(f)[0]['releve']['n'] == 2000
    f = nuit.valider_file(file_de(relief(pas_m=100, zones=[_z(cote_m=100)])))
    assert nuit.deplier(f)[0]['releve']['n'] == 2
    nuit.valider_file(file_de(relief(budget_image_ms=1000), relief(pas_m=50, methode='mission', surface=True)))
    nuit.valider_file(file_de(relief(), relief(methode='mission')))          # meme zone et pas, autre methode : autre sortie
    nuit.valider_file(file_de(relief(), relief(pas_m=50)))                    # autre pas : autre sortie
    nuit.valider_file(file_de(relief(), relief(theatre='Caucasus')))          # autre theatre : autre sortie


def test_r1_le_nom_de_theatre_ne_sort_pas_du_dossier():
    c = nuit.deplier(nuit.valider_file(file_de(relief(theatre='../x y'))))[0]
    assert c['releve']['sortie'][:2] == ('out', 'relief') and '/' not in c['releve']['sortie'][2] and c['releve']['sortie'][2] != '..'


def test_r1_depliage():
    cmds = nuit.deplier(nuit.valider_file(FILE_EXEMPLE))
    assert [c['n'] for c in cmds] == [1, 2, 3, 4]
    assert [c['vue'] for c in cmds] == ['f10', None, None, None]
    assert [(c['outil'], c['commande'], c['base']) for c in cmds[1:]] == [('crochet', 'relief', 'aleppo'), ('crochet', 'relief', 'rayak'),
                                                                          ('crochet', 'relief', 'rayak')]
    assert cmds[1]['args'] == ['relief', 'Syria', 'aleppo', '--pas', '100', '--methode', 'export']
    assert cmds[3]['args'] == ['relief', 'Syria', 'rayak', '--pas', '30', '--methode', 'mission', '--surface']


def test_r1_une_etape_relief_entre_deux_vues_ne_change_pas_la_ligne_de_vue(tmp_path, capsys):
    f = file_de({'type': 'charts', 'theatre': 'S', 'bases': ['A']}, relief(), {'type': 'charts', 'theatre': 'S', 'bases': ['B']})
    nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, f))])
    sortie = capsys.readouterr().out.splitlines()
    assert [l for l in sortie if 'vue' in l and '->' in l] == ['    -> vue f10 demandee']
    assert sortie[3].startswith('02  [--]  relief ')


def test_r1_file_de_releves_seulement_sans_ligne_de_vue(tmp_path, capsys):
    nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, file_de(relief())))])
    assert '->' not in capsys.readouterr().out


# ------------------------------------------------------------------ R2 : une nuit jouee, crochet simule en Python

class MondeRelief(Monde):
    """Le faux crochet sert releve_demande.lua : il lit la table, la supprime, puis (apres `duree` secondes de temps simule)
    ecrit le CSV et le .json de sortie et releve_fait.txt. Scenario par numero de commande : reponse, duree, mort, muet."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.scenarios = {}
        self.tables = []                 # (t, texte)
        self.en_cours = None
        self.tmp_vu = []                 # le .tmp etait-il la quand le crochet a lu ?
        self.annules = []
        self.tard = None                 # contenu de releve_fait.txt a ecrire a la prochaine seconde (ecriture lente)

    def sleep(self, s):
        super().sleep(s)
        self.servir()

    def servir(self):
        if self.tard is not None:
            (self.auto / 'releve_fait.txt').write_text(self.tard, encoding='utf-8')
            self.tard = None
        d = self.auto / 'releve_demande.lua'
        if self.vivant and d.exists():
            texte = d.read_text(encoding='utf-8')
            d.unlink()
            self.tables.append((self.t, texte))
            n = int(re.search(r"id = '(\d\d)_", texte).group(1))
            sortie = Path(re.search(r'sortie = \[\[(.*?)\]\]', texte).group(1))
            self.en_cours = {'n': n, 't0': self.t, 'sc': self.scenarios.get(n, {}), 'sortie': sortie}
            if self.en_cours['sc'].get('mort'):
                self.vivant = False
                self.en_cours = None
                return
        e = self.en_cours
        if not e or not self.vivant:
            return
        sc = e['sc']
        if (self.auto / 'releve_annule.txt').exists() and not sc.get('sourd'):
            self.annules.append(self.t)
            self.repondre(e, 'annule', fin='annule')
        elif not sc.get('muet') and self.t - e['t0'] >= sc.get('duree', 3):
            self.repondre(e, sc.get('reponse', 'ok'), fin='complet')

    def repondre(self, e, mot, fin):
        e['sortie'].parent.mkdir(parents=True, exist_ok=True)
        if e['sc'].get('json', True):
            e['sortie'].write_text('i,j,x,z,alt_m,surface\n', encoding='utf-8')
            e['sortie'].with_suffix('.json').write_text(json.dumps({'id': f'{e["n"]:02d}', 'points': 10201, 'fin': fin}), encoding='utf-8')
        if e['sc'].get('lent'):
            (self.auto / 'releve_fait.txt').write_text('', encoding='utf-8')
            self.tard = mot
        else:
            (self.auto / 'releve_fait.txt').write_text(mot, encoding='utf-8')
        self.en_cours = None


@pytest.fixture
def mr(tmp_path):
    return MondeRelief(tmp_path)


def sortie_de(m, rel):
    return m.work.joinpath(*rel)


def test_r2_table_exacte_et_bilan(mr):
    assert mr.jouer(FILE_EXEMPLE) == 0
    b = mr.bilan()
    assert statuts(b) == ['ok', 'ok', 'ok', 'ok']
    attendu = [
        "return { id = '02_aleppo', lat = 36.2, lon = 37.25, n = 101, pas_m = 100, methode = 'export', surface = false, "
        f"budget_image_ms = 40, sortie = [[{sortie_de(mr, ('out', 'relief', 'Syria', 'aleppo_100m_export.csv'))}]] }}\n",
        "return { id = '03_rayak', lat = 33.85, lon = 36.0, n = 101, pas_m = 100, methode = 'export', surface = false, "
        f"budget_image_ms = 40, sortie = [[{sortie_de(mr, ('out', 'relief', 'Syria', 'rayak_100m_export.csv'))}]] }}\n",
        "return { id = '04_rayak', lat = 33.85, lon = 36.0, n = 334, pas_m = 30, methode = 'mission', surface = true, "
        f"budget_image_ms = 60, sortie = [[{sortie_de(mr, ('out', 'relief', 'Syria', 'rayak_30m_mission.csv'))}]] }}\n",
    ]
    assert [t for _, t in mr.tables] == attendu
    # le releve du bilan est le .json ; la commande 1 (charts) n'a pas de cle `releve`
    assert 'releve' not in b['commandes'][0]
    assert b['commandes'][1]['releve'] == json.loads(sortie_de(mr, ('out', 'relief', 'Syria', 'aleppo_100m_export.csv')).with_suffix('.json').read_text())
    assert b['commandes'][3]['releve']['fin'] == 'complet'
    assert [c['outil'] for c in b['commandes']] == ['f10capture.py', 'crochet', 'crochet', 'crochet']
    assert [c['vue'] for c in b['commandes']] == ['f10', None, None, None]
    assert [c['arguments'] for c in b['commandes'][1:]] == [
        ['relief', 'Syria', 'aleppo', '--pas', '100', '--methode', 'export'],
        ['relief', 'Syria', 'rayak', '--pas', '100', '--methode', 'export'],
        ['relief', 'Syria', 'rayak', '--pas', '30', '--methode', 'mission', '--surface']]
    assert all(c['code'] is None and c['debut'] and c['duree_s'] >= 3 for c in b['commandes'][1:])
    assert b['commandes'][1]['journal'] == '02_crochet_relief_aleppo.log'


def test_r2_demande_ecrite_par_renommage(mr, monkeypatch):
    appels = []
    vrai = nuit.os.replace

    def spy(src, dst):
        appels.append((Path(src).name, Path(dst).name, Path(src).read_text(encoding='utf-8').startswith('return { id =')))
        return vrai(src, dst)
    monkeypatch.setattr(nuit.os, 'replace', spy)
    mr.jouer(file_de(relief()))
    assert appels == [('releve_demande.lua.tmp', 'releve_demande.lua', True)]
    assert not (mr.auto / 'releve_demande.lua.tmp').exists() and not (mr.auto / 'releve_demande.lua').exists()


def test_r2_aucune_vue_pour_une_file_de_releves(mr):
    mr.jouer(file_de(relief(), relief(methode='mission', surface=True)))
    assert mr.demandes == [] and mr.appels == []
    b = mr.bilan()
    assert statuts(b) == ['ok', 'ok'] and [c['vue'] for c in b['commandes']] == [None, None]
    assert not (mr.auto / 'vue_demande.txt').exists()


def test_r2_une_etape_charts_apres_un_releve_demande_la_vue_une_fois(mr):
    mr.jouer(file_de(relief(), {'type': 'charts', 'theatre': 'S', 'bases': ['A']}, relief(methode='mission'),
                     {'type': 'charts', 'theatre': 'S', 'bases': ['B']}))
    assert [m for _, m in mr.demandes] == ['f10']
    assert statuts(mr.bilan()) == ['ok'] * 4


def test_r2_reponses_ok_annule_erreur(mr):
    mr.scenarios = {1: {'reponse': 'ok'}, 2: {'reponse': 'annule'}, 3: {'reponse': 'erreur geo'},
                    4: {'reponse': 'erreur sortie\nautre ligne'}, 5: {'reponse': 'ok\n'}}
    f = file_de(relief(zones=[ZONE_ALEPPO, ZONE_RAYAK, _z(nom='a3'), _z(nom='a4'), _z(nom='a5')]))
    assert mr.jouer(f) == 0
    b = mr.bilan()
    assert statuts(b) == ['ok', 'delai', 'echec', 'echec', 'ok']
    assert b['commandes'][2]['erreur'] == 'geo' and b['commandes'][3]['erreur'] == 'sortie'
    assert 'erreur' not in b['commandes'][0]
    log = (mr.nuit_dir / '03_crochet_relief_a3.log').read_text(encoding='utf-8')
    assert log.splitlines()[0] == '# releve_demande.lua' and "id = '03_a3'" in log and 'reponse : erreur geo' in log
    assert b['commandes'][2]['fin_journal'][-1] == 'reponse : erreur geo'
    assert not (mr.auto / 'releve_fait.txt').exists()                         # supprime apres lecture


def test_r2_reponse_inconnue_est_un_echec(mr):
    mr.scenarios = {1: {'reponse': 'peut-etre'}}
    mr.jouer(file_de(relief()))
    c = mr.bilan()['commandes'][0]
    assert c['statut'] == 'echec' and 'peut-etre' in c['erreur']


def test_r2_releve_sans_json_donne_none(mr):
    mr.scenarios = {1: {'reponse': 'erreur geo', 'json': False}}
    mr.jouer(file_de(relief()))
    assert mr.bilan()['commandes'][0]['releve'] is None


def test_r2_reponse_vide_pas_encore_ecrite_est_attendue(mr):
    mr.scenarios = {1: {'reponse': 'erreur geo', 'lent': True}}
    mr.jouer(file_de(relief()))
    c = mr.bilan()['commandes'][0]
    assert c['statut'] == 'echec' and c['erreur'] == 'geo'            # le fichier vide n'a pas ete pris pour une reponse


def test_r2_restes_d_une_autre_nuit_effaces(mr):
    (mr.auto / 'releve_fait.txt').write_text('ok')
    (mr.auto / 'releve_annule.txt').write_text('annule')
    (mr.auto / 'releve_demande.lua').write_text('return {}')
    sortie = sortie_de(mr, ('out', 'relief', 'Syria', 'aleppo_100m_export.csv'))
    sortie.parent.mkdir(parents=True)
    sortie.with_suffix('.json').write_text('{"vieux": true}')
    mr.scenarios = {1: {'reponse': 'erreur geo', 'json': False, 'duree': 10}}
    mr.jouer(file_de(relief()))
    c = mr.bilan()['commandes'][0]
    assert c['statut'] == 'echec' and c['releve'] is None                       # ni le vieux `ok`, ni le vieux .json
    assert len(mr.tables) == 1 and mr.annules == []


def test_r2_delai_depasse_ecrit_releve_annule_puis_delai(mr):
    mr.scenarios = {1: {'muet': True}}
    mr.jouer(file_de(relief(), relief(pas_m=50), delai_commande_s=100))
    b = mr.bilan()
    assert statuts(b) == ['delai', 'ok']
    assert len(mr.annules) == 1 and 100 < mr.annules[0] - T0 <= 110
    assert b['commandes'][0]['releve']['fin'] == 'annule'                      # le crochet a ferme le releve et ecrit son .json
    assert 'releve_annule.txt' in b['commandes'][0]['fin_journal'][-1]
    assert (mr.auto / 'releve_annule.txt').exists() is False                    # la commande suivante l'a effacee avant sa demande


def test_r2_delai_crochet_sourd_apres_l_annulation_attente_30_s(mr):
    mr.scenarios = {1: {'muet': True, 'sourd': True}}
    mr.jouer(file_de(relief(), delai_commande_s=100))
    b = mr.bilan()
    assert statuts(b) == ['delai'] and b['commandes'][0]['releve'] is None
    assert 130 <= b['commandes'][0]['duree_s'] <= 135
    assert (mr.auto / 'releve_annule.txt').exists()


def test_r2_pouls_perdu_pendant_un_releve(mr):
    mr.scenarios = {2: {'mort': True}}
    mr.jouer(file_de(relief(zones=[ZONE_ALEPPO, ZONE_RAYAK, _z(nom='a3')]), eteindre=True))
    b = mr.bilan()
    assert statuts(b) == ['ok', 'dcs_absent', 'dcs_absent'] and b['cause'] == 'dcs_absent'
    assert 300 < b['commandes'][1]['duree_s'] < 320
    assert mr.arrets == 1 and (mr.auto / 'fin.txt').read_text() == 'eteindre'
    assert (mr.auto / 'file.lua').read_text().strip() == 'return false'
    assert not (mr.auto / 'releve_demande.lua').exists() and len(mr.tables) == 2


def test_r2_pouls_perdu_la_file_s_arrete_aussi_pour_les_autres_types(mr):
    mr.scenarios = {1: {'mort': True}}
    mr.jouer(file_de(relief(), {'type': 'charts', 'theatre': 'S', 'bases': ['A']}))
    b = mr.bilan()
    assert statuts(b) == ['dcs_absent', 'dcs_absent'] and mr.appels == [] and mr.arrets == 0
    assert (mr.auto / 'fin.txt').read_text() == 'rester'


def test_r2_budget_de_la_file_depasse_apres_un_releve(mr):
    mr.scenarios = {1: {'duree': 700}}
    mr.jouer(file_de(relief(), relief(pas_m=50), budget_captures_s=600))
    b = mr.bilan()
    assert statuts(b) == ['ok', 'saute_budget'] and b['cause'] == 'budget' and len(mr.tables) == 1


def test_r2_les_csv_de_out_partent_dans_les_zips(mr):
    mr.jouer(file_de(relief()))
    sortie = sortie_de(mr, ('out', 'relief', 'Syria', 'aleppo_100m_export.csv'))
    os.utime(sortie, (T0 + 5, T0 + 5))
    os.utime(sortie.with_suffix('.json'), (T0 + 5, T0 + 5))
    fichiers, _ = nuit.rassembler(mr.work, mr.nuit_dir, T0)
    noms = [a for _, a in fichiers]
    assert 'out/relief/Syria/aleppo_100m_export.csv' in noms and 'out/relief/Syria/aleppo_100m_export.json' in noms


def test_r2_dossier_de_sortie_cree(mr):
    mr.scenarios = {1: {'reponse': 'erreur geo', 'json': False}}
    mr.jouer(file_de(relief(theatre='Syria')))
    assert (mr.work / 'out' / 'relief' / 'Syria').is_dir()


def test_r2_pas_de_config_dans_la_ligne_ni_les_arguments(mr):
    mr.jouer(file_de(relief()))
    c = mr.bilan()['commandes'][0]
    assert '--config' not in c['arguments']
    assert 'relief Syria aleppo : 101 x 101 points au pas de 100 m, methode export' in mr.sortie
    assert '--config' not in mr.sortie.split('01 lance : ')[1].splitlines()[0]
