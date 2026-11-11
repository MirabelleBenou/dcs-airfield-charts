"""Crochet du GUI de DCS (capture/hook/ChartsAutonome-hook.lua) : syntaxe et comportement, joues dans un interpreteur Lua 5.1
(lupa, installe dans le bac a sable de test seulement ; sans lui ces tests sont ignores) avec une fausse API de DCS.
Aucun DCS."""
import re
from pathlib import Path

import pytest

lupa_lua51 = pytest.importorskip('lupa.lua51')

HOOK = Path(__file__).resolve().parent.parent / 'capture' / 'hook' / 'ChartsAutonome-hook.lua'
SRC = HOOK.read_text(encoding='utf-8')
T_EPOCH = 1_800_000_000

FAUX_DCS = r'''
CALLS = { execute = {}, setcmd = {}, exit = 0, callbacks = nil, exit_file_lua = nil }
CLOCK = { real = 100.0, pause = true, time = %d }
FAIL = {}
Sim = {
  getPause = function() if FAIL.pause then error('getPause en panne') end return CLOCK.pause end,
  getRealTime = function() if FAIL.realtime then error('horloge en panne') end return CLOCK.real end,
  exitProcess = function()
    CALLS.exit = CALLS.exit + 1
    local f = io.open(DOSSIER_TEST .. 'file.lua', 'r'); CALLS.exit_file_lua = f and f:read('*a') or nil; if f then f:close() end
    if FAIL.exit then error('exitProcess en panne') end
  end,
  setUserCallbacks = function(cb) CALLS.callbacks = cb end,
}
Export = { LoSetCommand = function(c) table.insert(CALLS.setcmd, c); if FAIL.setcmd then error('boom') end end }
os.execute = function(c) table.insert(CALLS.execute, c); return 0 end
os.time = function() return CLOCK.time end
'''


class Hote:
    """Un DCS factice qui charge le crochet avec DOSSIER pointe sur un dossier de test."""

    def __init__(self, tmp_path, dossier='set', src=None, avec_slash=True):
        self.dir = tmp_path / 'autonome'
        self.dir.mkdir(exist_ok=True)
        self.L = lupa_lua51.LuaRuntime(unpack_returned_tuples=True)
        chemin = str(self.dir) + ('/' if avec_slash else '')
        self.L.execute(f'DOSSIER_TEST = {chemin!r}'.replace("'", '"') + '\n' + FAUX_DCS % T_EPOCH)
        src = src or SRC
        if dossier == 'set':
            assert src.startswith('local DOSSIER = nil'), 'DOSSIER doit etre sur la premiere ligne'
            src = src.replace('local DOSSIER = nil', f'local DOSSIER = "{chemin}"', 1)
        self.L.execute(src)
        self.tick_lua = self.L.eval('function(dt) CLOCK.real = CLOCK.real + dt; if CALLS.callbacks then CALLS.callbacks.onSimulationFrame() end end')

    # --- pilotage
    def charger(self, texte):
        (self.dir / 'file.lua').write_text(texte, encoding='utf-8')

    def armer(self, age_s=0, **plus):
        t = {'arme_le': T_EPOCH - age_s, 'budget_s': 3600, 'attente_s': 5, 'eteindre_au_budget': False,
             'lancer': 'start "nuit" /min python nuit.py jouer nuit.json'}
        t.update(plus)
        champs = ', '.join(f'{k} = {self.lua(v)}' for k, v in t.items() if v is not None)
        self.charger('return { ' + champs + ' }\n')

    @staticmethod
    def lua(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return repr(v)
        return '[==[' + v + ']==]'

    def demarrer(self):
        self.L.eval('CALLS.callbacks.onSimulationStart')()

    def arreter(self):
        self.L.eval('CALLS.callbacks.onSimulationStop')()

    def pause(self, etat):
        self.L.execute(f'CLOCK.pause = {"true" if etat else "false"}')

    def tick(self, dt=1.0, n=1):
        for _ in range(n):
            self.tick_lua(dt)

    def panne(self, quoi, etat=True):
        self.L.execute(f'FAIL.{quoi} = {"true" if etat else "nil"}')

    def temps(self, t):
        self.L.execute(f'CLOCK.time = {t}')

    # --- lecture
    @property
    def executes(self):
        return [self.L.eval('CALLS.execute')[i] for i in range(1, len(self.L.eval('CALLS.execute')) + 1)]

    @property
    def commandes_vue(self):
        t = self.L.eval('CALLS.setcmd')
        return [t[i] for i in range(1, len(t) + 1)]

    @property
    def sorties(self):
        return self.L.eval('CALLS.exit')

    @property
    def file_lua_a_la_sortie(self):
        return self.L.eval('CALLS.exit_file_lua')

    def fichier(self, nom):
        p = self.dir / nom
        return p.read_text(encoding='utf-8') if p.exists() else None

    def journal(self):
        return self.fichier('crochet.log') or ''

    def fichiers(self):
        return sorted(p.name for p in self.dir.iterdir())

    def lancer_la_file(self, **plus):
        """Arme, demarre, leve la pause, attend attente_s : la file part."""
        self.armer(**plus)
        self.demarrer()
        self.pause(False)
        self.tick(1, 3)
        self.tick(1, 8)
        assert len(self.executes) == 1


@pytest.fixture
def hote(tmp_path):
    return Hote(tmp_path)


# ------------------------------------------------------------------ syntaxe

def test_syntaxe_lua51():
    L = lupa_lua51.LuaRuntime(unpack_returned_tuples=True)
    ok, err = L.eval('function(s) local f, e = loadstring(s, "hook"); return f ~= nil, e end')(SRC)
    assert ok, err
    assert L.eval('_VERSION') == 'Lua 5.1'


def test_syntaxe_luajit():
    lj = pytest.importorskip('lupa.luajit21')
    L = lj.LuaRuntime(unpack_returned_tuples=True)
    ok, err = L.eval('function(s) local f, e = loadstring(s, "hook"); return f ~= nil, e end')(SRC)
    assert ok, err


def test_dossier_sur_la_premiere_ligne():
    assert SRC.splitlines()[0].startswith('local DOSSIER = nil')
    assert len(re.findall(r'local DOSSIER = nil', SRC)) == 1


# ------------------------------------------------------------------ rien sans DOSSIER ni table

def test_sans_dossier_rien_du_tout(tmp_path):
    h = Hote(tmp_path, dossier=None)
    assert h.L.eval('CALLS.callbacks') is None              # pas meme de crochet enregistre
    assert h.fichiers() == [] and h.executes == []


@pytest.mark.parametrize('contenu', [None, 'return false', 'return nil', 'return 42', 'return "x"', 'return {{{', 'error("x")', ''])
def test_rien_sans_table(tmp_path, contenu):
    h = Hote(tmp_path)
    if contenu is not None:
        h.charger(contenu)
    h.demarrer()
    h.pause(False)
    h.tick(1, 200)
    h.arreter()
    assert h.executes == [] and h.commandes_vue == [] and h.sorties == 0
    assert h.fichiers() == ([] if contenu is None else ['file.lua'])      # ni journal ni pouls


def test_arme_mais_dossier_sans_slash_final(tmp_path):
    h = Hote(tmp_path, avec_slash=False)
    h.lancer_la_file()
    assert len(h.executes) == 1 and h.fichier('battement.txt') == str(T_EPOCH)


# ------------------------------------------------------------------ l'armement

def test_armement_de_plus_de_12_heures_ignore(hote):
    hote.armer(age_s=12 * 3600 + 1)
    hote.demarrer()
    hote.pause(False)
    hote.tick(1, 100)
    assert hote.executes == [] and hote.fichier('battement.txt') is None
    assert 'ignore' in hote.journal() and '12 heures' in hote.journal()


def test_armement_de_moins_de_12_heures_accepte(hote):
    hote.lancer_la_file(age_s=12 * 3600 - 5)
    assert len(hote.executes) == 1


@pytest.mark.parametrize('plus', [{'arme_le': None}, {'budget_s': None}, {'lancer': None}, {'lancer': 'python nuit.py'},
                                  {'lancer': 'cmd /c start x'}])
def test_armement_incomplet_ou_lancer_sans_start_ignore(hote, plus):
    hote.armer(**plus)
    hote.demarrer()
    hote.pause(False)
    hote.tick(1, 50)
    assert hote.executes == [] and 'ignore' in hote.journal()


def test_armement_relu_a_chaque_demarrage_et_pas_ailleurs(hote):
    hote.charger('return false')
    hote.demarrer()
    hote.pause(False)
    hote.tick(1, 20)
    hote.armer()                       # arme apres le demarrage : le crochet ne relit pas
    hote.tick(1, 50)
    assert hote.executes == []
    hote.arreter()
    hote.pause(True)
    hote.demarrer()                    # nouvelle mission : relu, arme
    hote.pause(False)
    hote.tick(1, 20)
    assert len(hote.executes) == 1


# ------------------------------------------------------------------ le depart

def test_attend_la_fin_de_la_pause_puis_attente_s(hote):
    hote.armer(attente_s=10)
    hote.demarrer()
    hote.tick(1, 60)                              # pause vraie : briefing affiche
    assert hote.executes == [] and hote.fichier('battement.txt') is None
    hote.pause(False)
    hote.tick(1, 9)
    assert hote.executes == []
    hote.tick(1, 3)
    assert hote.executes == ['start "nuit" /min python nuit.py jouer nuit.json']
    assert hote.fichier('battement.txt') == str(T_EPOCH)


def test_une_pause_revenue_remet_l_attente_a_zero(hote):
    hote.armer(attente_s=10)
    hote.demarrer()
    hote.pause(False)
    hote.tick(1, 8)
    hote.pause(True)
    hote.tick(1, 3)
    hote.pause(False)
    hote.tick(1, 8)
    assert hote.executes == []
    hote.tick(1, 4)
    assert len(hote.executes) == 1


def test_lancer_une_seule_fois(hote):
    hote.lancer_la_file()
    hote.tick(1, 500)
    assert len(hote.executes) == 1
    hote.arreter()
    hote.pause(True)
    hote.demarrer()                                # meme session DCS, la file n'est pas rearmee tant qu'elle est armee
    hote.pause(False)
    hote.tick(1, 100)
    assert len(hote.executes) == 1 and 'deja partie' in hote.journal()


def test_frequence_1_hz(hote):
    hote.lancer_la_file()
    hote.L.execute('CALLS.execute = {}')
    (hote.dir / 'vue_demande.txt').write_text('f10')
    hote.tick(0.1)                                  # moins d'une seconde depuis le dernier pas : rien
    assert hote.fichier('vue_demande.txt') == 'f10'
    hote.tick(1.0)
    assert hote.fichier('vue_demande.txt') is None


def test_nettoie_les_restes_de_la_nuit_d_avant(hote):
    for n in ('fin.txt', 'vue_demande.txt', 'vue_faite.txt', 'battement.txt'):
        (hote.dir / n).write_text('eteindre' if n == 'fin.txt' else 'f10')
    hote.lancer_la_file()
    assert hote.sorties == 0 and hote.fichier('fin.txt') is None and hote.fichier('vue_faite.txt') is None
    assert hote.fichier('battement.txt') == str(T_EPOCH)


# ------------------------------------------------------------------ le pouls et les vues

def test_pouls_toutes_les_30_secondes(hote):
    hote.lancer_la_file()                            # 5 s ecoulees depuis le lancement, pouls ecrit au lancement
    hote.tick(1, 24)                                 # 29 s
    assert hote.fichier('battement.txt') == str(T_EPOCH)
    hote.temps(T_EPOCH + 40)
    hote.tick(1, 1)                                  # 30 s : nouveau pouls, avec l'heure du moment
    assert hote.fichier('battement.txt') == str(T_EPOCH + 40)
    hote.temps(T_EPOCH + 50)
    hote.tick(1, 29)                                 # 59 s
    assert hote.fichier('battement.txt') == str(T_EPOCH + 40)
    hote.tick(1, 1)                                  # 60 s
    assert hote.fichier('battement.txt') == str(T_EPOCH + 50)


@pytest.mark.parametrize('mot,cmd', [('f10', 15), ('f11', 16), ('f10\n', 15), (' f11 ', 16)])
def test_vue_a_la_demande(hote, mot, cmd):
    hote.lancer_la_file()
    (hote.dir / 'vue_demande.txt').write_text(mot)
    hote.tick(1)
    assert hote.commandes_vue == [cmd]
    assert hote.fichier('vue_demande.txt') is None
    assert hote.fichier('vue_faite.txt') == mot.strip()


@pytest.mark.parametrize('mot', ['f12', 'F10', 'f10 f11', '', 'autre'])
def test_vue_inconnue_erreur(hote, mot):
    hote.lancer_la_file()
    (hote.dir / 'vue_demande.txt').write_text(mot)
    hote.tick(1)
    assert hote.commandes_vue == [] and hote.fichier('vue_demande.txt') is None
    assert hote.fichier('vue_faite.txt') == 'erreur' and 'inconnue' in hote.journal()


def test_vue_commande_en_panne_erreur_sans_arreter_dcs(hote):
    hote.lancer_la_file()
    hote.panne('setcmd')
    (hote.dir / 'vue_demande.txt').write_text('f10')
    hote.tick(1)
    assert hote.fichier('vue_faite.txt') == 'erreur' and 'erreur' in hote.journal() and hote.sorties == 0
    hote.panne('setcmd', False)
    (hote.dir / 'vue_demande.txt').write_text('f11')
    hote.tick(1)
    assert hote.commandes_vue == [15, 16] and hote.fichier('vue_faite.txt') == 'f11'


# ------------------------------------------------------------------ la fin

def test_fin_eteindre(hote):
    hote.lancer_la_file()
    (hote.dir / 'fin.txt').write_text('eteindre')
    hote.tick(1)
    assert hote.executes[-1] == 'shutdown /s /t 120' and len(hote.executes) == 2
    assert hote.sorties == 1
    assert hote.file_lua_a_la_sortie.strip() == 'return false'          # remis a return false AVANT Sim.exitProcess()
    assert hote.fichier('file.lua').strip() == 'return false'


def test_fin_rester(hote):
    hote.lancer_la_file()
    (hote.dir / 'fin.txt').write_text('rester\n')
    hote.tick(1)
    assert len(hote.executes) == 1 and hote.sorties == 1
    assert hote.file_lua_a_la_sortie.strip() == 'return false'


@pytest.mark.parametrize('eteindre', [True, False])
def test_budget(hote, eteindre):
    hote.lancer_la_file(budget_s=600, eteindre_au_budget=eteindre)
    hote.tick(300)                                 # 5 s deja ecoulees depuis le lancement
    hote.tick(290)                                 # 595 s
    assert hote.sorties == 0
    hote.tick(6)                                   # 601 s depuis le lancement
    assert hote.sorties == 1
    assert 'budget' in hote.journal()
    assert hote.file_lua_a_la_sortie.strip() == 'return false'
    assert (hote.executes[-1] == 'shutdown /s /t 120') == eteindre
    assert len(hote.executes) == (2 if eteindre else 1)


def test_budget_avec_fin_rester_n_eteint_pas(hote):
    hote.lancer_la_file(budget_s=600, eteindre_au_budget=True)
    (hote.dir / 'fin.txt').write_text('rester')
    hote.tick(1)
    assert len(hote.executes) == 1 and hote.sorties == 1


def test_la_fin_n_est_faite_qu_une_fois(hote):
    hote.lancer_la_file()
    (hote.dir / 'fin.txt').write_text('eteindre')
    hote.tick(1, 20)
    assert hote.sorties == 1 and len(hote.executes) == 2


def test_exitprocess_en_panne_ne_propage_pas(hote):
    hote.lancer_la_file()
    hote.panne('exit')
    (hote.dir / 'fin.txt').write_text('rester')
    hote.tick(1)
    assert hote.sorties == 1 and 'exitProcess' in hote.journal()


# ------------------------------------------------------------------ chaque appel a DCS sous pcall

def test_getpause_en_panne_ne_propage_pas(hote):
    hote.armer()
    hote.demarrer()
    hote.panne('pause')
    hote.tick(1, 5)
    assert 'getPause' in hote.journal() and hote.executes == []
    hote.panne('pause', False)
    hote.pause(False)
    hote.tick(1, 12)
    assert len(hote.executes) == 1


def test_horloge_en_panne_ne_propage_pas(hote):
    hote.lancer_la_file()
    hote.panne('realtime')
    hote.tick(1, 5)
    assert hote.sorties == 0


def test_stop_de_mission_sans_fin(hote):
    hote.lancer_la_file()
    hote.arreter()
    hote.tick(1, 100)
    assert hote.sorties == 0 and 'mission arretee' in hote.journal()


# ------------------------------------------------------------------ revue statique (la table de P7)

def lignes_de_code():
    out = []
    for i, l in enumerate(SRC.splitlines(), 1):
        code = l.split('--')[0] if not l.lstrip().startswith('--') else ''
        if code.strip():
            out.append((i, code))
    return out


def test_os_execute_seulement_lancer_et_shutdown():
    appels = [(i, c.strip()) for i, c in lignes_de_code() if 'os.execute' in c]
    assert len(appels) == 2, appels
    assert any("'shutdown /s /t 120'" in c for _, c in appels) and any('E.q.lancer' in c for _, c in appels)


def test_chaque_appel_a_dcs_est_sous_pcall():
    for i, c in lignes_de_code():
        if re.search(r'\b(Sim\.\w+|Export\.\w+)\s*\(', c):
            assert 'pcall(' in c, (i, c)
    assert not re.search(r'\bDCS\.', '\n'.join(c for _, c in lignes_de_code()))


def test_aucun_appel_hors_liste():
    code = '\n'.join(c for _, c in lignes_de_code())
    assert set(re.findall(r'\b(?:Sim|Export)\.(\w+)', code)) == {'getRealTime', 'getPause', 'exitProcess', 'LoSetCommand', 'setUserCallbacks'}
    assert 'os.execute' in code and 'io.popen' not in code and 'require' not in code
