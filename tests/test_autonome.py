"""Pilote de la file de nuit (capture/autonome/nuit.py) : la file, les commandes depliees, une nuit jouee avec un faux monde
(horloge, crochet, outils, Windows), les arrets, le rassemblement, le depot contre un faux serveur de l'API, la fin.
Aucun DCS, aucun Windows : tout ce qui est propre a Windows et le lancement des outils sont remplaces."""
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'capture' / 'autonome'))
import nuit  # noqa: E402

JETON = 'ghp_JETON_SECRET_0123456789abcdef'


def file_exemple(**plus):
    f = {
        'etapes': [
            {'type': 'charts', 'theatre': 'GermanyColdWar', 'bases': ['Mahlwinkel', 'Frankfurt']},
            {'type': 'contexte', 'theatre': 'GermanyColdWar', 'bases': ['Mahlwinkel'], 'marge': 2000},
            {'type': 'panneaux', 'theatre': 'GermanyColdWar', 'bases': ['Mahlwinkel']},
        ],
        'budget_captures_s': 12600, 'delai_commande_s': 3600, 'eteindre': True, 'ecran_eteint': False,
        'taille_zip_max_o': 1900000000,
    }
    f.update(plus)
    return f


# ------------------------------------------------------------------ P1 : verifier

def ecrire_file(tmp_path, data, nom='nuit.json'):
    p = tmp_path / nom
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')
    return p


def test_verifier_exemple(tmp_path, capsys):
    d = file_exemple(config='C:\\x\\capture\\config.json', tampon={'depot': 'proprio/depot', 'jeton': 'C:\\x\\jeton.txt', 'release': 'nuit'})
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, d))]) == 0
    sortie = capsys.readouterr().out.splitlines()
    cmds = [l for l in sortie if l[:2].isdigit()]
    assert len(cmds) == 5
    assert [l.split()[0] for l in cmds] == ['01', '02', '03', '04', '05']
    assert 'f10capture.py capture GermanyColdWar Mahlwinkel' in cmds[0] and '--marge' not in cmds[0]
    assert 'f10capture.py capture GermanyColdWar Frankfurt' in cmds[1]
    assert 'f10capture.py capture GermanyColdWar Mahlwinkel --marge 2000' in cmds[2]
    assert 'signcapture.py visee GermanyColdWar Mahlwinkel' in cmds[3] and '[f11]' in cmds[3]
    assert 'signcapture.py capture GermanyColdWar Mahlwinkel' in cmds[4]
    assert all('--config C:\\x\\capture\\config.json' in l for l in cmds)
    # la ligne de vue n'apparait qu'aux changements : f10 avant la 1re, f11 avant la 4e
    vues = [(i, l.strip()) for i, l in enumerate(sortie) if l.strip().startswith('-> vue')]
    assert [v for _, v in vues] == ['-> vue f10 demandee', '-> vue f11 demandee']
    idx = [i for i, l in enumerate(sortie) if l[:2].isdigit()]
    assert vues[0][0] < idx[0] and idx[2] < vues[1][0] < idx[3]


def test_verifier_defauts_et_marge_decimale(tmp_path, capsys):
    d = {'etapes': [{'type': 'contexte', 'theatre': 'T', 'bases': ['B'], 'marge': 1500.5}]}
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, d))]) == 0
    assert '--marge 1500.5' in capsys.readouterr().out
    f = nuit.valider_file(d)
    assert (f['budget_captures_s'], f['delai_commande_s'], f['eteindre'], f['ecran_eteint'], f['taille_zip_max_o'], f['config'], f['tampon']) \
        == (12600, 3600, False, False, 1900000000, None, None)
    t = nuit.valider_file({**d, 'tampon': {'depot': 'a/b', 'jeton': 'j'}})['tampon']
    assert t['release'] == 'nuit'


def _avec(**kw):
    d = file_exemple()
    d.update(kw)
    return d


def _etape(**kw):
    d = file_exemple()
    d['etapes'] = [kw]
    return d


REFUS = [
    (_avec(inconnue=1), 'inconnue'),
    (_avec(etapes=[]), 'etapes'),
    ({k: v for k, v in file_exemple().items() if k != 'etapes'}, 'etapes'),
    (_etape(type='contexte', theatre='T', bases=['B']), 'marge'),
    (_etape(type='charts', theatre='T', bases=['B'], marge=2000), 'marge'),
    (_etape(type='panneaux', theatre='T', bases=['B'], marge=2000), 'marge'),
    (_etape(type='charts', theatre='T', bases=[]), 'bases'),
    (_etape(type='charts', theatre='T'), 'bases'),
    (_etape(type='charts', theatre='T', bases=['B', 3]), 'bases'),
    (_etape(type='charts', theatre='T', bases=['B'], truc=1), 'truc'),
    (_etape(type='autre', theatre='T', bases=['B']), 'type'),
    (_etape(type='contexte', theatre='T', bases=['B'], marge=0), 'marge'),
    (_etape(type='contexte', theatre='T', bases=['B'], marge='2000'), 'marge'),
    (_etape(type='contexte', theatre='T', bases=['B'], marge=True), 'marge'),
    (_avec(budget_captures_s='long'), 'budget_captures_s'),
    (_avec(budget_captures_s=0), 'budget_captures_s'),
    (_avec(delai_commande_s=-1), 'delai_commande_s'),
    (_avec(eteindre='oui'), 'eteindre'),
    (_avec(ecran_eteint=1), 'ecran_eteint'),
    (_avec(taille_zip_max_o=1.5), 'taille_zip_max_o'),
    (_avec(config=3), 'config'),
    (_avec(tampon={'depot': 'a/b', 'jeton': 'j', 'x': 1}), 'x'),
    (_avec(tampon={'depot': 'a/b'}), 'jeton'),
    (_avec(tampon={'depot': 'sans_barre', 'jeton': 'j'}), 'depot'),
    (_avec(tampon='a/b'), 'tampon'),
    ([], 'objet'),
    ('{pas du json', 'JSON'),
    (_etape(type='theatre', theatre='T', bases=['B']), 'theatre'),
]


@pytest.mark.parametrize('data,mot', REFUS)
def test_verifier_refuse(tmp_path, capsys, data, mot):
    assert nuit.main(['nuit.py', 'verifier', str(ecrire_file(tmp_path, data))]) == 2
    err = capsys.readouterr().err
    assert mot in err and err.startswith('Erreur')


def test_verifier_fichier_absent_et_usage(tmp_path, capsys):
    assert nuit.main(['nuit.py', 'verifier', str(tmp_path / 'absent.json')]) == 2
    assert nuit.main(['nuit.py']) == 2
    assert nuit.main(['nuit.py', 'plus', 'x']) == 2


def test_verifier_en_sous_processus_isole(tmp_path):
    p = ecrire_file(tmp_path, file_exemple())
    r = subprocess.run([sys.executable, '-I', str(ROOT / 'capture' / 'autonome' / 'nuit.py'), 'verifier', str(p)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout.count('\n') >= 5


# ------------------------------------------------------------------ le faux monde

T0 = 1_800_000_000.0


class Monde:
    """Horloge, crochet, outils et Windows simules. Le temps n'avance que par sleep() et par la duree des commandes."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.work = tmp_path / 'work'
        dcs = tmp_path / 'dcs'
        (dcs / 'Mods' / 'terrains').mkdir(parents=True)
        self.cfg = tmp_path / 'config.json'
        self.cfg.write_text(json.dumps({'work_dir': str(self.work), 'dcs_install': str(dcs)}), encoding='utf-8')
        self.auto = self.work / 'autonome'
        self.auto.mkdir(parents=True)
        (self.auto / 'file.lua').write_text('return { arme_le = 1 }\n')
        self.t = T0
        self.vivant = True
        self.vue_reponse = None         # None : le crochet repond le mot demande ; 'erreur' ; 'muet'
        self.demandes, self.reponses = [], []
        self.appels = []
        self.scripts = {}               # n -> {code, duree, fichiers, question, delai, mort}
        self.ecrans = self.arrets = 0
        self.veilles = []
        self.vue_courante = None
        self.pouls()

    # horloge
    def now(self):
        return self.t

    def pouls(self):
        if self.vivant:
            (self.auto / 'battement.txt').write_text(str(int(self.t)))

    def avancer(self, s):
        self.t += s
        self.pouls()

    def sleep(self, s):
        self.avancer(s)
        d = self.auto / 'vue_demande.txt'
        if self.vivant and d.exists():
            mot = d.read_text()
            d.unlink()
            self.demandes.append((self.t, mot))
            if self.vue_reponse != 'muet':
                (self.auto / 'vue_faite.txt').write_text(self.vue_reponse or mot)
                self.reponses.append(self.t)
                if not self.vue_reponse:
                    self.vue_courante = mot

    def fichier(self, rel, contenu=b'x', age=0):
        p = self.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contenu if isinstance(contenu, bytes) else contenu.encode())
        os.utime(p, (self.t - age, self.t - age))
        return p

    # outils
    def lancer(self, argv, log, timeout_s, cwd, tuer):
        n = int(Path(log).name[:2])
        s = self.scripts.get(n, {})
        self.appels.append({'n': n, 'argv': list(argv), 'log': Path(log), 'timeout': timeout_s, 't': self.t, 'vue': self.vue_courante, 'cwd': cwd})
        for rel, contenu in s.get('fichiers', []):
            self.fichier(rel, contenu)
        with open(log, 'a', encoding='utf-8') as f:
            f.write(''.join(f'ligne {i}\n' for i in range(25)))
        if s.get('question'):
            self.fichier('sync/QUESTION.txt', s['question'])
        if s.get('mort'):
            self.vivant = False
        self.avancer(s.get('duree', 60))
        return s.get('code', 0), s.get('delai', False)

    def deps(self, **kw):
        return nuit.Deps(now=self.now, sleep=self.sleep, lancer=self.lancer, veille=self.veilles.append,
                         ecran=lambda: setattr(self, 'ecrans', self.ecrans + 1),
                         shutdown=lambda: setattr(self, 'arrets', self.arrets + 1),
                         capture_dir=ROOT / 'capture', python='PYTHON', **kw)

    def jouer(self, data, **kw):
        data = dict(data, config=str(self.cfg))
        p = ecrire_file(self.tmp, data)
        out = io.StringIO()
        code = nuit.cmd_jouer(str(p), self.deps(**kw), out)
        self.sortie = out.getvalue()
        return code

    @property
    def nuit_dir(self):
        d = list((self.auto / 'nuits').iterdir())
        assert len(d) == 1
        return d[0]

    def bilan(self):
        return json.loads((self.nuit_dir / 'bilan.json').read_text(encoding='utf-8'))


@pytest.fixture
def monde(tmp_path):
    return Monde(tmp_path)


def statuts(b):
    return [c['statut'] for c in b['commandes']]


# ------------------------------------------------------------------ P2 : une nuit jouee

def test_nuit_complete(monde):
    assert monde.jouer(file_exemple()) == 0
    a = monde.appels
    assert [c['n'] for c in a] == [1, 2, 3, 4, 5]
    cap = str(ROOT / 'capture')
    cfg = str(monde.cfg)
    attendu = [
        ['PYTHON', '-I', cap + '/f10capture.py', 'capture', 'GermanyColdWar', 'Mahlwinkel', '--config', cfg],
        ['PYTHON', '-I', cap + '/f10capture.py', 'capture', 'GermanyColdWar', 'Frankfurt', '--config', cfg],
        ['PYTHON', '-I', cap + '/f10capture.py', 'capture', 'GermanyColdWar', 'Mahlwinkel', '--marge', '2000', '--config', cfg],
        ['PYTHON', '-I', cap + '/signcapture.py', 'visee', 'GermanyColdWar', 'Mahlwinkel', '--config', cfg],
        ['PYTHON', '-I', cap + '/signcapture.py', 'capture', 'GermanyColdWar', 'Mahlwinkel', '--config', cfg],
    ]
    assert [c['argv'] for c in a] == attendu
    assert a[0]['timeout'] == 3600
    # deux demandes de vue seulement, f10 puis f11, 5 s de stabilisation apres chacune
    assert [m for _, m in monde.demandes] == ['f10', 'f11']
    assert [c['vue'] for c in a] == ['f10', 'f10', 'f10', 'f11', 'f11']
    assert a[0]['t'] - monde.reponses[0] == pytest.approx(5)
    assert a[3]['t'] - monde.reponses[1] == pytest.approx(5)
    b = monde.bilan()
    assert statuts(b) == ['ok'] * 5 and b['cause'] == 'file_finie'
    assert [c['vue'] for c in b['commandes']] == ['f10', 'f10', 'f10', 'f11', 'f11']
    assert b['commandes'][2]['arguments'] == ['capture', 'GermanyColdWar', 'Mahlwinkel', '--marge', '2000', '--config', cfg]
    assert all(len(c['fin_journal']) == 10 and c['fin_journal'][-1] == 'ligne 24' for c in b['commandes'])
    noms = sorted(p.name for p in monde.nuit_dir.glob('*.log'))
    assert noms == ['01_f10capture_capture_Mahlwinkel.log', '02_f10capture_capture_Frankfurt.log',
                    '03_f10capture_capture_Mahlwinkel.log', '04_signcapture_visee_Mahlwinkel.log',
                    '05_signcapture_capture_Mahlwinkel.log', 'nuit.log']
    assert monde.veilles == [True, False]
    assert monde.ecrans == 0
    assert monde.arrets == 0     # l'arret normal est fait par le crochet


def test_statuts_echec_question_et_visee(monde):
    monde.scripts = {1: {'code': 1}, 2: {'code': 3, 'question': 'Que faire ?'}, 3: {'code': 0},
                     4: {'code': 7}}
    assert monde.jouer(file_exemple()) == 0           # code 0 meme avec des commandes en echec
    b = monde.bilan()
    assert statuts(b) == ['echec', 'question', 'ok', 'echec', 'saute_visee']
    assert b['commandes'][1]['question'] == 'Que faire ?'
    assert not (monde.work / 'sync' / 'QUESTION.txt').exists()
    assert [c['n'] for c in monde.appels] == [1, 2, 3, 4]       # la capture de panneaux n'est pas lancee
    assert b['commandes'][0]['code'] == 1 and b['commandes'][4]['debut'] is None


def test_code_3_sans_question_est_un_echec(monde):
    monde.scripts = {1: {'code': 3}}
    monde.jouer(_etape(type='charts', theatre='T', bases=['B']))
    assert statuts(monde.bilan()) == ['echec']


def test_delai(monde):
    monde.scripts = {1: {'delai': True, 'code': None, 'duree': 3600}}
    monde.jouer(file_exemple(etapes=[{'type': 'charts', 'theatre': 'T', 'bases': ['A', 'B']}]))
    b = monde.bilan()
    assert statuts(b) == ['delai', 'ok'] and b['commandes'][0]['code'] is None
    assert 'delai' in b['commandes'][0]['fin_journal'][-1]


def test_vue_echec_sans_reponse_puis_erreur(monde):
    monde.vue_reponse = 'muet'
    monde.jouer(file_exemple())
    b = monde.bilan()
    assert statuts(b) == ['vue_echec', 'vue_echec', 'vue_echec', 'vue_echec', 'saute_visee']
    assert monde.appels == []
    assert len(monde.demandes) == 4
    assert not (monde.auto / 'vue_demande.txt').exists()
    # 30 s d'attente au plus
    assert monde.t - T0 < 4 * 31 + 5


def test_vue_echec_erreur_du_crochet(tmp_path):
    m = Monde(tmp_path)
    m.vue_reponse = 'erreur'
    m.jouer(_etape(type='charts', theatre='T', bases=['B']))
    assert statuts(m.bilan()) == ['vue_echec'] and m.appels == []


def test_vue_reessayee_apres_un_echec(monde):
    """La vue n'est tenue pour posee qu'apres une reponse bonne : la commande suivante la redemande."""
    monde.vue_reponse = 'erreur'
    d = monde.deps()
    # premiere demande en erreur, puis le crochet repond bien
    sleep0 = d.sleep

    def sleep(s):
        sleep0(s)
        if len(monde.demandes) >= 1:
            monde.vue_reponse = None
    d.sleep = sleep
    p = ecrire_file(monde.tmp, dict(file_exemple(etapes=[{'type': 'charts', 'theatre': 'T', 'bases': ['A', 'B']}]), config=str(monde.cfg)))
    nuit.cmd_jouer(str(p), d, io.StringIO())
    assert statuts(monde.bilan()) == ['vue_echec', 'ok']
    assert len(monde.demandes) == 2


def test_ecran_eteint_apres_la_premiere_vue(monde):
    monde.jouer(file_exemple(ecran_eteint=True))
    assert monde.ecrans == 1


def test_nuit_dossier_refuse_file_invalide(monde):
    assert monde.jouer({'etapes': []}) == 2
    assert monde.appels == [] and not (monde.auto / 'nuits').exists() and not (monde.auto / 'fin.txt').exists()


def test_vue_perimee_effacee_au_depart(monde):
    (monde.auto / 'vue_faite.txt').write_text('f11')
    (monde.auto / 'vue_demande.txt').write_text('f11')
    monde.jouer(_etape(type='charts', theatre='T', bases=['B']))
    assert [m for _, m in monde.demandes] == ['f10']


# ------------------------------------------------------------------ P3 : les arrets de la file

def test_budget_depasse(monde):
    monde.scripts = {1: {'duree': 700}, 2: {'duree': 700}}
    monde.jouer(file_exemple(budget_captures_s=1000))
    b = monde.bilan()
    assert statuts(b) == ['ok', 'ok', 'saute_budget', 'saute_budget', 'saute_budget']
    assert b['cause'] == 'budget'
    assert [c['n'] for c in monde.appels] == [1, 2]
    assert monde.arrets == 0
    assert (monde.auto / 'fin.txt').read_text() == 'eteindre'


@pytest.mark.parametrize('eteindre', [True, False])
def test_pouls_perime(monde, eteindre):
    monde.scripts = {2: {'duree': 400, 'mort': True}}
    monde.jouer(file_exemple(eteindre=eteindre))
    b = monde.bilan()
    assert statuts(b) == ['ok', 'ok', 'dcs_absent', 'dcs_absent', 'dcs_absent']
    assert b['cause'] == 'dcs_absent'
    assert monde.arrets == (1 if eteindre else 0)
    assert (monde.auto / 'fin.txt').read_text() == ('eteindre' if eteindre else 'rester')


def test_pouls_absent_apres_la_grace(monde):
    (monde.auto / 'battement.txt').unlink()
    monde.vivant = False
    # premiere commande dans la grace de 60 s : lancee (la vue est demandee, le crochet muet)
    monde.vue_reponse = None
    d = monde.deps()
    s0 = d.sleep

    def sleep(s):                    # le crochet repond a la vue, sans battre le pouls
        monde.vivant = True
        s0(s)
        monde.vivant = False
        if (monde.auto / 'battement.txt').exists():
            (monde.auto / 'battement.txt').unlink()
    d.sleep = sleep
    monde.scripts = {1: {'duree': 100}}
    p = ecrire_file(monde.tmp, dict(file_exemple(etapes=[{'type': 'charts', 'theatre': 'T', 'bases': ['A', 'B']}]), config=str(monde.cfg)))
    nuit.cmd_jouer(str(p), d, io.StringIO())
    assert statuts(monde.bilan()) == ['ok', 'dcs_absent']


def test_pouls_a_300_s_pile_tolere(monde):
    monde.scripts = {1: {'duree': 60, 'mort': True}}
    d = monde.deps()
    # le dernier pouls date de la fin de la 1re commande ; on attend 300 s : tolere, 301 s : absent
    nuit_obj = None

    class Aiguille(nuit.Nuit):
        pass
    f = nuit.valider_file(file_exemple())
    cfg, _ = __import__('config').load_config(['x', '--config', str(monde.cfg)])
    nuit_obj = Aiguille(f, cfg, d, io.StringIO())
    nuit_obj.t_nuit = T0
    monde.t = T0 + 1000
    (monde.auto / 'battement.txt').write_text(str(int(monde.t - 300)))
    assert nuit_obj.pouls_ok()
    (monde.auto / 'battement.txt').write_text(str(int(monde.t - 301)))
    assert not nuit_obj.pouls_ok()
    (monde.auto / 'battement.txt').write_text('n importe quoi')
    assert not nuit_obj.pouls_ok()


# ------------------------------------------------------------------ P6 : la fin

@pytest.mark.parametrize('eteindre', [True, False])
def test_fin(monde, eteindre):
    monde.scripts = {1: {'code': 1}}
    assert monde.jouer(file_exemple(eteindre=eteindre)) == 0
    assert (monde.auto / 'file.lua').read_text().strip() == 'return false'
    assert (monde.auto / 'fin.txt').read_text() == ('eteindre' if eteindre else 'rester')
    assert monde.arrets == 0


def test_fin_ecrite_meme_apres_une_erreur_interne(monde, monkeypatch):
    def boum(*a, **k):
        raise RuntimeError('boum')
    monkeypatch.setattr(nuit, 'planifier_zips', boum)
    code = monde.jouer(file_exemple())
    assert code == 1
    assert (monde.auto / 'file.lua').read_text().strip() == 'return false'
    assert (monde.auto / 'fin.txt').read_text() == 'eteindre'
    assert monde.veilles == [True, False]


# ------------------------------------------------------------------ P4 : le rassemblement et les zips

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 40
JSONT = ('{"a": "' + 'x' * 5000 + '"}').encode()


def preparer_fichiers(m):
    m.fichier('out/germanycoldwar/ancien.png', PNG, age=500)               # avant la nuit : exclu
    m.fichier('panneaux/T/B/ancien.json', JSONT, age=500)
    m.fichier('sync/STATUS.txt', 'ne doit pas partir')
    m.fichier('osm/T/osm.json', 'non plus')
    m.fichier('autre/x.png', PNG)
    m.fichier('autonome/tampon_jeton.txt', JETON)
    m.scripts = {
        1: {'fichiers': [('out/germanycoldwar/Mahlwinkel/a.png', PNG), ('out/germanycoldwar/Mahlwinkel/b.jpg', b'\xff\xd8\xff' + bytes(2000)),
                         ('out/germanycoldwar/Mahlwinkel/e.json', JSONT)]},
        4: {'fichiers': [('panneaux/T/B/vues.json', JSONT), ('panneaux/T/B/img/v1.png', PNG)]},
        5: {'fichiers': [('taxiways_dcs/t/B.json', JSONT), ('hors/liste.png', PNG)]},
    }


ATTENDUS = [
    'out/germanycoldwar/Mahlwinkel/a.png', 'out/germanycoldwar/Mahlwinkel/b.jpg', 'out/germanycoldwar/Mahlwinkel/e.json',
    'panneaux/T/B/vues.json', 'panneaux/T/B/img/v1.png', 'taxiways_dcs/t/B.json',
]


def membres(m):
    """{nom du zip: {chemin: ZipInfo}} des zips de la nuit."""
    res = {}
    for z in sorted(m.nuit_dir.glob('*.zip')):
        with zipfile.ZipFile(z) as zf:
            res[z.name] = {i.filename: i for i in zf.infolist()}
    return res


def test_rassemblement_un_zip(monde):
    preparer_fichiers(monde)
    monde.jouer(file_exemple())
    z = membres(monde)
    assert len(z) == 1
    (nom, info), = z.items()
    assert nom.startswith('nuit_') and nom.endswith('_1.zip')
    fichiers = sorted(n for n in info if not n.startswith('autonome/'))
    assert fichiers == sorted(ATTENDUS)
    stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(T0))
    assert nom == f'nuit_{stamp}_1.zip'
    # journaux et bilan dedans, chemins relatifs au work_dir, barres obliques
    journaux = sorted(n for n in info if n.startswith('autonome/'))
    base = f'autonome/nuits/{stamp}/'
    assert base + 'bilan.json' in journaux and base + 'nuit.log' in journaux and base + '01_f10capture_capture_Mahlwinkel.log' in journaux
    assert all('\\' not in n for n in info)
    assert not any(n.startswith(('sync/', 'osm/', 'autre/', 'hors/')) for n in info)
    assert not any('ancien' in n for n in info)
    assert not any(n.endswith('.zip') or 'tampon_jeton' in n for n in info)
    # PNG et JPEG stockes, JSON compresses
    for n, i in info.items():
        if n.endswith(('.png', '.jpg')):
            assert i.compress_type == zipfile.ZIP_STORED, n
        elif n.endswith('.json'):
            assert i.compress_type == zipfile.ZIP_DEFLATED, n
    with zipfile.ZipFile(monde.nuit_dir / nom) as zf:
        assert zf.read('out/germanycoldwar/Mahlwinkel/a.png') == PNG
        assert zf.testzip() is None


def test_rassemblement_plusieurs_zips(monde):
    preparer_fichiers(monde)
    monde.jouer(file_exemple(taille_zip_max_o=11000))
    z = membres(monde)
    assert len(z) >= 3
    tailles = {p.name: p.stat().st_size for p in monde.nuit_dir.glob('*.zip')}
    assert all(t <= 11000 for t in tailles.values()), tailles
    tous = [n for info in z.values() for n in info]
    assert len(tous) == len(set(tous))                                  # pas de doublon
    assert sorted(n for n in tous if not n.startswith('autonome/')) == sorted(ATTENDUS)
    stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(T0))
    assert sorted(z) == [f'nuit_{stamp}_{i}.zip' for i in range(1, len(z) + 1)]
    b = monde.bilan()
    assert b['fichiers_trop_gros'] == []
    assert [x['nom'] for x in b['zips']] == sorted(z)
    for x in b['zips']:
        p = monde.nuit_dir / x['nom']
        assert x['taille_o'] == p.stat().st_size and x['sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
        assert x['fichiers'] == len(z[x['nom']])


def test_fichier_plus_gros_que_la_limite_seul_et_signale(monde):
    preparer_fichiers(monde)
    monde.scripts[1]['fichiers'].append(('out/germanycoldwar/Mahlwinkel/gros.png', bytes(range(256)) * 200))   # 51 200 o
    monde.jouer(file_exemple(taille_zip_max_o=20000))
    z = membres(monde)
    alone = [info for info in z.values() if 'out/germanycoldwar/Mahlwinkel/gros.png' in info]
    assert len(alone) == 1 and list(alone[0]) == ['out/germanycoldwar/Mahlwinkel/gros.png']
    assert monde.bilan()['fichiers_trop_gros'] == ['out/germanycoldwar/Mahlwinkel/gros.png']
    tous = [n for info in z.values() for n in info]
    assert len(tous) == len(set(tous)) and set(ATTENDUS) <= set(tous)


def test_sha256_du_bilan_et_bilan_range_dans_le_zip(monde):
    preparer_fichiers(monde)
    monde.jouer(file_exemple())
    b = monde.bilan()
    for x in b['zips']:
        assert hashlib.sha256((monde.nuit_dir / x['nom']).read_bytes()).hexdigest() == x['sha256']
    stamp = b['dossier']
    with zipfile.ZipFile(monde.nuit_dir / b['zips'][0]['nom']) as zf:
        dedans = json.loads(zf.read(f'autonome/nuits/{stamp}/bilan.json'))
    assert dedans['cause'] == 'file_finie' and dedans['zips'] == []       # ecrit avant les zips
    assert b['depot'] == {'statut': 'sans_depot'}


def test_zips_sans_fichier_de_la_nuit(monde):
    monde.jouer(_etape(type='charts', theatre='T', bases=['B']))
    z = membres(monde)
    assert len(z) == 1 and all(n.startswith('autonome/') for n in next(iter(z.values())))


# ------------------------------------------------------------------ P5 : le depot, contre un faux serveur

class FauxGitHub:
    """Faux serveur de l'API de GitHub (local). Enregistre les requetes ; `echecs` : nom d'asset -> nombre d'echecs avant succes."""

    def __init__(self, existe=True, echecs=None, mode_tag=204, echo_jeton=False, upload_base=None):
        self.upload_base = upload_base
        self.requetes, self.existe, self.echecs, self.mode_tag, self.echo_jeton = [], existe, dict(echecs or {}), mode_tag, echo_jeton
        self.assets = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _rep(self, code, corps=None):
                data = json.dumps(corps).encode() if corps is not None else b''
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def _faire(self):
                n = int(self.headers.get('Content-Length') or 0)
                corps = self.rfile.read(n) if n else b''
                h = {k.lower(): v for k, v in self.headers.items()}
                outer.requetes.append({'m': self.command, 'p': self.path, 'h': h, 'corps': corps})
                p = self.path.split('?')[0]
                if self.command == 'GET' and p == '/repos/o/r/releases/tags/nuit':
                    return self._rep(200, {'id': 42}) if outer.existe else self._rep(404, {'message': 'Not Found'})
                if self.command == 'DELETE' and p == '/repos/o/r/releases/42':
                    outer.existe = False
                    return self._rep(204)
                if self.command == 'DELETE' and p == '/repos/o/r/git/refs/tags/nuit':
                    return self._rep(outer.mode_tag, {'message': 'x'} if outer.mode_tag != 204 else None)
                if self.command == 'POST' and p == '/repos/o/r/releases':
                    outer.release = json.loads(corps)
                    return self._rep(201, {'id': 43, 'upload_url': f'{outer.upload_base or "http://127.0.0.1:%d" % outer.port}/uploads/repos/o/r/releases/43/assets{{?name,label}}'})
                if self.command == 'POST' and p == '/uploads/repos/o/r/releases/43/assets':
                    nom = self.path.split('name=')[1]
                    if outer.echecs.get(nom, 0) > 0:
                        outer.echecs[nom] -= 1
                        return self._rep(502, {'message': 'bad gateway', 'eco': self.headers.get('Authorization') if outer.echo_jeton else ''})
                    outer.assets[nom] = corps
                    return self._rep(201, {'name': nom})
                return self._rep(500, {'message': 'inattendu'})
            do_GET = do_POST = do_DELETE = _faire

        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(timeout=10)

    @property
    def api(self):
        return f'http://127.0.0.1:{self.port}'

    @property
    def opener(self):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def trace(self):
        return [(r['m'], r['p'].split('?')[0] + ('?' + r['p'].split('?')[1] if '?' in r['p'] else '')) for r in self.requetes]


def tampon_pour(monde, jeton=JETON, release='nuit'):
    chemin = monde.auto / 'tampon_jeton.txt'
    if jeton is not None:
        chemin.write_text('\n  \n' + jeton + '\nautre ligne\n')
    return {'depot': 'o/r', 'jeton': str(chemin), 'release': release}


def tout_ce_qui_est_ecrit(monde):
    """Les octets de tous les fichiers sous tmp (zips ouverts), hors le fichier du jeton lui-meme."""
    for p in monde.tmp.rglob('*'):
        if p.is_file() and p.name != 'tampon_jeton.txt':
            data = p.read_bytes()
            yield str(p), data
            if p.suffix == '.zip':
                with zipfile.ZipFile(p) as zf:
                    for n in zf.namelist():
                        yield f'{p}!{n}', zf.read(n)


def test_depot_remplace_la_release(monde):
    preparer_fichiers(monde)
    with FauxGitHub(existe=True) as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde), taille_zip_max_o=11000), api=srv.api, opener=srv.opener)
        b = monde.bilan()
        assert b['depot']['statut'] == 'ok', b['depot']
        t = srv.trace()
        noms = [x['nom'] for x in b['zips']]
        assert t[:4] == [('GET', '/repos/o/r/releases/tags/nuit'), ('DELETE', '/repos/o/r/releases/42'),
                         ('DELETE', '/repos/o/r/git/refs/tags/nuit'), ('POST', '/repos/o/r/releases')]
        assert t[4:] == [('POST', '/uploads/repos/o/r/releases/43/assets?name=bilan.json')] + \
                        [('POST', f'/uploads/repos/o/r/releases/43/assets?name={n}') for n in noms]
        assert srv.release == {'tag_name': 'nuit', 'name': 'nuit', 'prerelease': True, 'body': srv.release['body']}
        assert srv.release['body'].splitlines()[0] == 'cause : file_finie' and 'ok : 5' in srv.release['body'].splitlines()
        # en-tetes exacts, sur chaque requete
        for r in srv.requetes:
            assert r['h']['authorization'] == f'Bearer {JETON}'
            assert r['h']['accept'] == 'application/vnd.github+json'
            assert r['h']['x-github-api-version'] == '2022-11-28'
        # les envois : taille annoncee, contenu identique, bilan = celui des zips (sha256)
        for r in srv.requetes[4:]:
            assert int(r['h']['content-length']) == len(r['corps'])
        assert srv.requetes[4]['h']['content-type'] == 'application/json'
        assert srv.requetes[5]['h']['content-type'] == 'application/zip'
        envoye = json.loads(srv.assets['bilan.json'])
        assert [x['sha256'] for x in envoye['zips']] == [hashlib.sha256(srv.assets[n]).hexdigest() for n in noms]
        for n in noms:
            assert srv.assets[n] == (monde.nuit_dir / n).read_bytes()
        assert envoye['depot']['statut'] == 'a_faire'          # le resultat n'est connu qu'apres
    assert (monde.auto / 'fin.txt').exists()


def test_depot_sans_release_existante_ni_etiquette(monde):
    with FauxGitHub(existe=False, mode_tag=422) as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=srv.api, opener=srv.opener)
        assert monde.bilan()['depot']['statut'] == 'ok'
        assert [x[0] + ' ' + x[1] for x in srv.trace()[:3]] == ['GET /repos/o/r/releases/tags/nuit', 'DELETE /repos/o/r/git/refs/tags/nuit',
                                                                'POST /repos/o/r/releases']


def test_depot_televersement_deux_echecs_puis_ok(monde):
    preparer_fichiers(monde)
    with FauxGitHub(echecs={'bilan.json': 2}) as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=srv.api, opener=srv.opener)
        assert monde.bilan()['depot']['statut'] == 'ok'
        assert [r['p'] for r in srv.requetes].count('/uploads/repos/o/r/releases/43/assets?name=bilan.json') == 3
        assert 'bilan.json' in srv.assets


def test_depot_trois_echecs(monde):
    preparer_fichiers(monde)
    with FauxGitHub(echecs={'bilan.json': 3}, echo_jeton=True) as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=srv.api, opener=srv.opener)
        d = monde.bilan()['depot']
        assert d['statut'] == 'echec' and '502' in d['detail']
        assert [r['p'] for r in srv.requetes].count('/uploads/repos/o/r/releases/43/assets?name=bilan.json') == 3
        assert not any(p.endswith('zip') for _, p in srv.trace())          # on s'arrete au premier fichier perdu
    assert list(monde.nuit_dir.glob('*.zip'))                              # les zips restent sur le PC
    assert (monde.auto / 'fin.txt').exists()
    for nom, data in tout_ce_qui_est_ecrit(monde):
        assert JETON.encode() not in data, nom
    assert JETON not in monde.sortie


def test_depot_zip_perdu_trois_fois(monde):
    preparer_fichiers(monde)
    stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(T0))
    with FauxGitHub(echecs={f'nuit_{stamp}_1.zip': 5}) as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=srv.api, opener=srv.opener)
        assert monde.bilan()['depot']['statut'] == 'echec'
        assert 'bilan.json' in srv.assets and list(monde.nuit_dir.glob('*.zip'))


def test_depot_sans_jeton(monde):
    with FauxGitHub() as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde, jeton=None)), api=srv.api, opener=srv.opener)      # fichier absent
        assert monde.bilan()['depot'] == {'statut': 'sans_jeton'} and srv.requetes == []
    assert list(monde.nuit_dir.glob('*.zip'))


def test_depot_jeton_vide(tmp_path):
    m = Monde(tmp_path)
    with FauxGitHub() as srv:
        t = tampon_pour(m, jeton='')
        (m.auto / 'tampon_jeton.txt').write_text('\n   \n\n')
        m.jouer(file_exemple(tampon=t), api=srv.api, opener=srv.opener)
        assert m.bilan()['depot']['statut'] == 'sans_jeton' and srv.requetes == []


def test_depot_serveur_injoignable_sans_fuite_du_jeton(monde):
    with FauxGitHub() as srv:
        api = srv.api
    # le serveur est ferme : connexion refusee
    monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=api, opener=srv.opener)
    d = monde.bilan()['depot']
    assert d['statut'] == 'echec'
    for nom, data in tout_ce_qui_est_ecrit(monde):
        assert JETON.encode() not in data, nom
    assert JETON not in monde.sortie


def test_jeton_nettoye_des_messages():
    class D:
        api, opener, sleep, attente_depot_s = 'http://x', None, staticmethod(lambda s: None), 0
    dep = nuit.Depot('o/r', 'nuit', JETON, D)
    assert JETON not in dep._nettoyer(f'erreur {JETON} fin')
    e = dep._erreur('quoi', 401, f'bad credentials {JETON}'.encode())
    assert JETON not in str(e)


def test_depot_aucune_requete_sans_tampon(monde):
    with FauxGitHub() as srv:
        monde.jouer(file_exemple(), api=srv.api, opener=srv.opener)
        assert srv.requetes == []


def test_upload_url_vers_un_hote_inattendu_refuse(monde):
    with FauxGitHub(upload_base='http://127.0.0.1:1') as srv:
        monde.jouer(file_exemple(tampon=tampon_pour(monde)), api=srv.api, opener=srv.opener)
        d = monde.bilan()['depot']
        assert d['statut'] == 'echec' and 'hote' in d['detail']
        assert JETON not in d['detail']


# ------------------------------------------------------------------ le lanceur reel (Linux) : delai, arbre tue

def test_lanceur_reel_ok_et_echec(tmp_path):
    log = tmp_path / 'l.log'
    log.write_text('entete\n')
    code, delai = nuit.lancer_reel([sys.executable, '-c', 'print("bonjour"); import sys; sys.exit(3)'], log, 30, str(tmp_path), nuit.tuer_arbre_windows)
    assert (code, delai) == (3, False)
    assert log.read_text().splitlines() == ['entete', 'bonjour']


@pytest.mark.skipif(os.name == 'nt', reason='arbre de processus teste sous POSIX')
def test_lanceur_reel_delai_tue_l_arbre(tmp_path):
    log = tmp_path / 'l.log'
    log.write_text('')
    pids = tmp_path / 'pids.txt'
    code_py = (
        'import subprocess, sys, os, time\n'
        f'g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        f'open({str(pids)!r}, "w").write(f"{{os.getpid()}} {{g.pid}}")\n'
        'time.sleep(60)\n')
    t = time.time()
    code, delai = nuit.lancer_reel([sys.executable, '-c', code_py], log, 2, str(tmp_path), nuit.tuer_arbre_windows)
    assert (code, delai) == (None, True)
    assert time.time() - t < 30
    pere, fils = map(int, pids.read_text().split())
    time.sleep(0.5)
    for pid in (pere, fils):
        try:
            os.kill(pid, 0)
            vivant = Path(f'/proc/{pid}/stat').read_text().split()[2] != 'Z' if Path(f'/proc/{pid}/stat').exists() else True
        except (ProcessLookupError, FileNotFoundError):
            vivant = False
        assert not vivant, pid


def test_deps_champ_inconnu():
    with pytest.raises(TypeError):
        nuit.Deps(truc=1)


def test_import_sans_windows_et_fonctions_windows_inertes():
    assert nuit.veille_windows(True) is None or os.name == 'nt'
    assert nuit.ecran_windows() is None or os.name == 'nt'
    assert nuit.arret_windows() is None or os.name == 'nt'
