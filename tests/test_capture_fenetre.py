"""Controle de la fenetre de DCS a chaque prise d'ecran (capture/f10capture.py : grab, move_and_stabilize).

Le module est celui que charge `render.f10import.load()` (ctypes.windll factice hors de Windows). La lecture du rectangle
de la fenetre (`_lire_fenetre_dcs`), la remise (`_remettre_fenetre_dcs`), la photo de l'ecran (`_photo_ecran`) et le
rectangle de l'ecran principal (`_moniteur_principal`) sont remplaces ; `time` l'est aussi : aucune attente reelle."""
import ctypes
import re
import types
from pathlib import Path

import numpy as np
import pytest

import config
from render import f10import

f10 = f10import.load()
LIRE_REELLE, REMETTRE_REELLE = f10._lire_fenetre_dcs, f10._remettre_fenetre_dcs

ATTENDUE = (0, 0, 4806, 1440)               # la fenetre de DCS sur les deux ecrans
MONITEUR = (0, 0, 3440, 1440)               # l'ecran principal
ECRAN = (3440, 1440)
SUR_ECRAN_MFD = (3440, 0, 1366, 768)
RETRECIE = (0, 0, 3440, 1440)

LIGNE = re.compile(r'^FENETRE (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}) lue (-?\d+,-?\d+,\d+,\d+) attendue '
                   r'(-?\d+,-?\d+,\d+,\d+|ecran principal) : remise (ok|echec) \(essai (\d)\)$')


class Horloge:
    """Horloge simulee : `sleep` avance le temps, rien n'attend."""

    def __init__(self):
        self.t = 0.0
        self.pauses = []

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.pauses.append(s)
        self.t += s


class Faux:
    """La fenetre de DCS, l'ecran et leurs quatre fonctions de module, remplaces. `rect` : ce que lit la fenetre ;
    `remise` : fonction(rect_demande) qui dit ce que devient la fenetre apres un MoveWindow (defaut : elle y va) ;
    `apres_photo` : fonction(n) appelee apres la n-ieme photo (pour deplacer la fenetre pendant la prise)."""

    def __init__(self, monkeypatch, tmp_path, fenetre_dcs=ATTENDUE, rect=ATTENDUE, moniteur=MONITEUR, ecran=ECRAN):
        self.rect, self.moniteur, self.ecran = tuple(rect), moniteur, ecran
        self.evenements = []                   # ('lire', rect) / ('remettre', rect) / ('photo', n)
        self.photos = 0
        self.remises = []
        self.remise = lambda demande: demande
        self.apres_photo = lambda n: None
        self.horloge = Horloge()
        self.cfg = config.Config(tmp_path, tmp_path, tmp_path, ecran, tmp_path / 'config.json', fenetre_dcs=fenetre_dcs)
        monkeypatch.setattr(f10, 'CFG', self.cfg)
        monkeypatch.setattr(f10, '_screen', None)
        monkeypatch.setattr(f10, '_lire_fenetre_dcs', self.lire)
        monkeypatch.setattr(f10, '_remettre_fenetre_dcs', self.remettre)
        monkeypatch.setattr(f10, '_photo_ecran', self.photo)
        monkeypatch.setattr(f10, '_moniteur_principal', lambda: self.moniteur)
        monkeypatch.setattr(f10, 'time', types.SimpleNamespace(perf_counter=self.horloge.perf_counter,
                                                               sleep=self.horloge.sleep))
        monkeypatch.setattr(f10, 'fenetre_remises', 0)
        monkeypatch.setattr(f10, '_last', None)
        monkeypatch.setattr(f10, '_last_send', 0.0)

    def lire(self):
        self.evenements.append(('lire', self.rect))
        return self.rect

    def remettre(self, rect):
        self.evenements.append(('remettre', tuple(rect)))
        self.remises.append(tuple(rect))
        self.rect = tuple(self.remise(tuple(rect)))

    def photo(self):
        self.photos += 1
        self.evenements.append(('photo', self.photos))
        img = np.full((self.ecran[1], self.ecran[0], 3), self.photos, np.uint8)     # la valeur dit quelle photo
        self.apres_photo(self.photos)
        return img

    def indice(self, genre):
        return [i for i, e in enumerate(self.evenements) if e[0] == genre]


@pytest.fixture
def faux(monkeypatch, tmp_path):
    def make(**kw):
        return Faux(monkeypatch, tmp_path, **kw)
    return make


def lignes(capsys):
    return capsys.readouterr().out.splitlines()


# ------------------------------------------------------------------ grab : avec la cle

def test_fenetre_bonne_une_photo_aucune_remise_rien_sur_la_sortie(faux, capsys):
    d = faux()
    img = f10.grab()
    assert d.photos == 1 and d.remises == [] and (img == 1).all()
    assert img.shape == (1440, 3440, 3)
    assert capsys.readouterr().out == ''
    assert f10.fenetre_remises == 0
    assert d.horloge.pauses == []


def test_mauvaise_avant_la_photo_remise_puis_photo(faux, capsys):
    d = faux(rect=SUR_ECRAN_MFD)
    img = f10.grab()
    assert d.remises == [ATTENDUE]                       # MoveWindow recoit exactement le rectangle attendu
    assert d.photos == 1 and (img == 1).all()
    assert d.indice('remettre')[0] < d.indice('photo')[0]    # l'image rendue est prise apres la remise
    assert f10.fenetre_remises == 1
    ls = lignes(capsys)
    assert len(ls) == 1
    m = LIGNE.match(ls[0])
    assert m, ls[0]
    assert m.group(2, 3, 4, 5) == ('3440,0,1366,768', '0,0,4806,1440', 'ok', '1')
    assert d.horloge.pauses == [f10.FENETRE_ATTENTE_S, f10.FENETRE_RETOUR_S] == [1.0, 2.0]


def test_mauvaise_seulement_apres_la_photo_image_jetee_et_reprise(faux, capsys):
    d = faux()

    def apres_photo(n):
        if n == 1:
            d.rect = RETRECIE                            # DCS retreci pendant la prise
    d.apres_photo = apres_photo
    img = f10.grab()
    assert d.photos == 2
    assert (img == 2).all()                              # la premiere image est jetee : on rend la seconde
    assert d.remises == [ATTENDUE]
    assert d.indice('photo')[0] < d.indice('remettre')[0] < d.indice('photo')[1]
    assert f10.fenetre_remises == 1
    ls = lignes(capsys)
    assert len(ls) == 1 and LIGNE.match(ls[0]).group(2, 3, 4, 5) == ('0,0,3440,1440', '0,0,4806,1440', 'ok', '1')


def test_remise_qui_demande_deux_essais(faux, capsys):
    d = faux(rect=SUR_ECRAN_MFD)
    essais = []

    def remise(demande):
        essais.append(demande)
        return demande if len(essais) == 2 else (0, 0, 3440, 1440)
    d.remise = remise
    img = f10.grab()
    assert (img == 1).all() and len(d.remises) == 2 and f10.fenetre_remises == 1
    ls = [LIGNE.match(l) for l in lignes(capsys)]
    assert [m.group(4, 5) for m in ls] == [('echec', '1'), ('ok', '2')]
    assert ls[0].group(2) == '3440,0,1366,768' and ls[1].group(2) == '0,0,3440,1440'     # la lecture est celle d'avant l'essai


def test_jamais_remise_askcdp_apres_cinq_remises(faux, capsys):
    d = faux(rect=SUR_ECRAN_MFD)
    d.remise = lambda demande: SUR_ECRAN_MFD             # la fenetre ne bouge pas
    with pytest.raises(f10.AskCdP) as e:
        f10.grab()
    assert len(d.remises) == 5 == f10.FENETRE_ESSAIS
    assert d.remises == [ATTENDUE] * 5
    msg = str(e.value)
    assert msg == 'ARRET : fenetre de DCS 3440,0,1366,768 au lieu de 0,0,4806,1440 (5 remises sans effet)'
    assert d.photos == 0 and f10.fenetre_remises == 0
    ls = [LIGNE.match(l) for l in lignes(capsys)]
    assert [m.group(4, 5) for m in ls] == [('echec', str(i)) for i in range(1, 6)]
    assert d.horloge.pauses == [1.0] * 5


def test_fenetre_a_peine_decalee_est_mauvaise(faux):
    d = faux(rect=(0, 0, 4806, 1439))
    f10.grab()
    assert d.remises == [ATTENDUE]


def test_fenetre_negative_acceptee_quand_la_cle_la_dit(faux, capsys):
    d = faux(fenetre_dcs=(-1366, 0, 4806, 1440), rect=(-1366, 0, 4806, 1440))
    f10.grab()
    assert d.remises == [] and capsys.readouterr().out == ''


def test_controle_d_ecran_existant_inchange(faux):
    d = faux(ecran=(3440, 1440))
    d.cfg = d.cfg._replace(screen=(1920, 1080))
    f10.CFG = d.cfg
    with pytest.raises(f10.AskCdP, match='ecran 3440x1440 au lieu de 1920x1080'):
        f10.grab()


# ------------------------------------------------------------------ grab : sans la cle

def test_sans_cle_fenetre_qui_couvre_l_ecran_principal_passe(faux, capsys):
    d = faux(fenetre_dcs=None, rect=(0, 0, 4806, 1440))
    img = f10.grab()
    assert d.remises == [] and d.photos == 1 and (img == 1).all()
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('rect', [(0, 0, 3440, 1440), (-1366, 0, 4806, 1440), (-10, -10, 3500, 1500)])
def test_sans_cle_autres_fenetres_couvrantes(faux, rect):
    d = faux(fenetre_dcs=None, rect=rect)
    f10.grab()
    assert d.remises == [] and d.photos == 1


@pytest.mark.parametrize('rect', [SUR_ECRAN_MFD, (0, 0, 2460, 1440), (1, 0, 3440, 1440), (0, 0, 3440, 1439)])
def test_sans_cle_fenetre_qui_ne_couvre_pas_askcdp_tout_de_suite(faux, rect, capsys):
    d = faux(fenetre_dcs=None, rect=rect)
    with pytest.raises(f10.AskCdP) as e:
        f10.grab()
    assert d.remises == [] and d.photos == 0 and d.horloge.pauses == []
    msg = str(e.value)
    assert 'fenetre_dcs' in msg and 'ne couvre pas l\'ecran principal' in msg
    assert msg == (f'ARRET : fenetre de DCS {rect[0]},{rect[1]},{rect[2]},{rect[3]} ne couvre pas l\'ecran principal '
                   f'(poser "fenetre_dcs" dans config.json)')
    ls = lignes(capsys)
    assert len(ls) == 1 and 'attendue ecran principal' in ls[0] and LIGNE.match(ls[0])


def test_sans_cle_mauvaise_seulement_apres_la_photo(faux):
    d = faux(fenetre_dcs=None)
    d.apres_photo = lambda n: setattr(d, 'rect', SUR_ECRAN_MFD) if n == 1 else None
    with pytest.raises(f10.AskCdP, match='fenetre_dcs'):
        f10.grab()
    assert d.photos == 1 and d.remises == []


# ------------------------------------------------------------------ la fenetre introuvable, le handle, MoveWindow

def test_fenetre_introuvable_askcdp(monkeypatch):
    monkeypatch.setattr(f10, '_dcs_hwnd', None)
    monkeypatch.setattr(f10, 'find_dcs_window', lambda: None)
    with pytest.raises(f10.AskCdP, match='introuvable'):
        LIRE_REELLE()
    with pytest.raises(f10.AskCdP, match='introuvable'):
        REMETTRE_REELLE(ATTENDUE)


def test_grab_fenetre_introuvable_askcdp(faux, monkeypatch):
    d = faux()
    monkeypatch.setattr(f10, '_lire_fenetre_dcs', LIRE_REELLE)
    monkeypatch.setattr(f10, '_dcs_hwnd', None)
    monkeypatch.setattr(f10, 'find_dcs_window', lambda: None)
    with pytest.raises(f10.AskCdP, match='introuvable'):
        f10.grab()
    assert d.photos == 0 and d.remises == []


class FauxUser32:
    """user32 factice : IsWindow dit ce que `vivantes` contient, GetWindowRect remplit la structure, MoveWindow enregistre."""

    def __init__(self, vivantes, rect=(10, 20, 4816, 1460)):
        self.vivantes, self.rect, self.deplacements, self.lectures = set(vivantes), rect, [], []
        self.IsWindow = lambda h: h in self.vivantes

    def GetWindowRect(self, hwnd, ref):
        self.lectures.append(hwnd)
        r = ref._obj
        r.left, r.top, r.right, r.bottom = self.rect
        return 1

    def MoveWindow(self, *args):
        self.deplacements.append(args)
        return 1


def test_lecture_garde_le_handle_et_le_cherche_quand_il_est_perime(monkeypatch):
    u32 = FauxUser32({111})
    cherches = []

    def find():
        cherches.append(1)
        return 111 if len(cherches) == 1 else 222
    monkeypatch.setattr(f10, '_u32', u32)
    monkeypatch.setattr(f10, '_dcs_hwnd', None)
    monkeypatch.setattr(f10, 'find_dcs_window', find)
    assert LIRE_REELLE() == (10, 20, 4806, 1440)       # (gauche, haut, largeur, hauteur)
    assert LIRE_REELLE() == (10, 20, 4806, 1440)
    assert len(cherches) == 1 and u32.lectures == [111, 111]     # le handle se garde
    u32.vivantes = {222}                                         # 111 est perime
    LIRE_REELLE()
    assert len(cherches) == 2 and u32.lectures[-1] == 222


def test_remise_est_un_movewindow_au_rectangle_attendu(monkeypatch):
    u32 = FauxUser32({111})
    monkeypatch.setattr(f10, '_u32', u32)
    monkeypatch.setattr(f10, '_dcs_hwnd', 111)
    REMETTRE_REELLE((-1366, 5, 4806, 1440))
    assert u32.deplacements == [(111, -1366, 5, 4806, 1440, True)]


def test_les_quatre_fonctions_sont_des_fonctions_de_module():
    for nom in ('_lire_fenetre_dcs', '_remettre_fenetre_dcs', '_photo_ecran', '_moniteur_principal'):
        assert isinstance(getattr(f10, nom), types.FunctionType)
    assert ctypes.sizeof(f10._Rect) == 4 * ctypes.sizeof(ctypes.c_long)


# ------------------------------------------------------------------ move_and_stabilize

class Prise:
    """Une prise de move_and_stabilize avec le vrai _move_and_stabilize, un hook et un premier plan factices et une `grab` qui
    simule une remise (fenetre_remises + 1) pendant les prises dont `script` dit oui (une valeur par essai de la prise)."""

    def __init__(self, monkeypatch, script):
        self.horloge = Horloge()
        self.script = list(script)
        self.debuts = []                       # valeur de _last au debut de chaque _move_and_stabilize
        self.grabs = 0
        self.debut = False
        self.image = np.full((300, 370, 3), 100, np.uint8)
        monkeypatch.setattr(f10, 'time', types.SimpleNamespace(perf_counter=self.horloge.perf_counter,
                                                               sleep=self.horloge.sleep))
        monkeypatch.setattr(f10, 'fenetre_remises', 0)
        monkeypatch.setattr(f10, '_last', None)
        monkeypatch.setattr(f10, '_last_send', 0.0)
        monkeypatch.setattr(f10, '_fg_fail_streak', 0)
        monkeypatch.setattr(f10, 'hook_xz', lambda x, z, alt: (x, z, alt))
        monkeypatch.setattr(f10, 'ensure_dcs_front', lambda contexte='': None)
        monkeypatch.setattr(f10, 'dcs_in_front', lambda: True)
        monkeypatch.setattr(f10, 'grab', self.grab)
        reel = f10._move_and_stabilize

        def espion(*a, **k):
            self.debuts.append(f10._last)
            self.debut = True
            return reel(*a, **k)
        monkeypatch.setattr(f10, '_move_and_stabilize', espion)

    def grab(self):
        self.grabs += 1
        if self.debut:
            self.debut = False
            if self.script and self.script.pop(0):
                f10.fenetre_remises += 1
        return self.image


def test_prise_sans_remise_une_seule_fois(monkeypatch):
    p = Prise(monkeypatch, [False])
    f10._last = np.full((300, 370, 3), 7, np.uint8)
    img, delai, n, ok, cmd = f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert ok and cmd == (1.0, 2.0, 3.0) and len(p.debuts) == 1
    assert p.debuts[0] is not None                       # sans remise, la reference d'avant sert comme avant


def test_remise_pendant_la_prise_rejoue_depuis_le_debut(monkeypatch):
    p = Prise(monkeypatch, [True, False])
    avant = np.full((300, 370, 3), 7, np.uint8)
    f10._last = avant
    img, delai, n, ok, cmd = f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert ok and cmd == (1.0, 2.0, 3.0)
    assert len(p.debuts) == 2
    assert p.debuts[0] is avant and p.debuts[1] is None  # _last revient a None avant la relance
    assert f10.fenetre_remises == 1
    assert f10._fg_fail_streak == 0                      # compteur distinct de _fg_fail_streak


def test_trois_relances_permises_la_quatrieme_remise_arrete(monkeypatch):
    p = Prise(monkeypatch, [True] * 10)
    with pytest.raises(f10.AskCdP) as e:
        f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert len(p.debuts) == 4
    assert p.debuts[1:] == [None, None, None]
    assert str(e.value) == 'ARRET : fenetre de DCS remise 3 fois de suite pendant une prise'
    assert f10._fg_fail_streak == 0


def test_trois_relances_puis_une_prise_rendue_remet_le_compteur_a_zero(monkeypatch):
    p = Prise(monkeypatch, [True, True, True, False, True, True, True, False])
    f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert len(p.debuts) == 4
    f10.move_and_stabilize(4.0, 5.0, 6.0, expect_change=False)       # trois relances de plus : encore permises
    assert len(p.debuts) == 8


def test_premier_plan_perdu_pendant_la_prise_se_comporte_comme_avant(monkeypatch):
    p = Prise(monkeypatch, [False, False])
    etat = iter([False, True])
    monkeypatch.setattr(f10, 'dcs_in_front', lambda: next(etat))
    f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert len(p.debuts) == 2 and f10._fg_fail_streak == 1 and f10._fg_incidents[-1]['refocused'] is False


def test_grab_reel_dans_une_prise_la_remise_compte_et_rejoue(faux, monkeypatch, capsys):
    """Les deux ensemble : la remise faite par grab() fait rejouer la prise, qui repart sans image de reference."""
    d = faux(rect=SUR_ECRAN_MFD, ecran=(370, 300), moniteur=(0, 0, 370, 300), fenetre_dcs=(0, 0, 740, 300))
    d.photo = lambda: np.full((300, 370, 3), 100, np.uint8)
    monkeypatch.setattr(f10, '_photo_ecran', d.photo)
    monkeypatch.setattr(f10, 'hook_xz', lambda x, z, alt: (x, z, alt))
    monkeypatch.setattr(f10, 'ensure_dcs_front', lambda contexte='': None)
    monkeypatch.setattr(f10, 'dcs_in_front', lambda: True)
    debuts = []
    reel = f10._move_and_stabilize
    monkeypatch.setattr(f10, '_move_and_stabilize', lambda *a, **k: (debuts.append(f10._last), reel(*a, **k))[1])
    img, delai, n, ok, cmd = f10.move_and_stabilize(1.0, 2.0, 3.0, expect_change=False)
    assert ok and f10.fenetre_remises == 1 and len(debuts) == 2 and debuts == [None, None]
    assert d.remises == [(0, 0, 740, 300)] and len(lignes(capsys)) == 1


# ------------------------------------------------------------------ le module, les fichiers

def test_le_module_s_importe_hors_de_windows_et_garde_ses_noms():
    for nom in ('dcs_in_front', 'ensure_dcs_front', 'bring_dcs_front', 'screen_size', 'crop', 'diff_frac', 'grab',
                'move_and_stabilize', 'find_dcs_window'):
        assert callable(getattr(f10, nom))
    assert f10.FENETRE_RETOUR_S == 2.0 and f10.FENETRE_ESSAIS == 5 and f10.fenetre_remises == 0


def test_la_cle_est_dans_le_modele_de_configuration():
    import json
    modele = Path(__file__).resolve().parent.parent / 'capture' / 'config.example.json'
    assert json.loads(modele.read_text(encoding='utf-8'))['fenetre_dcs'] == [0, 0, 1920, 1080]
