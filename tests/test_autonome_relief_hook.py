"""Etape `relief` de la file de nuit, cote crochet (capture/hook/ChartsAutonome-hook.lua) : joue dans un interpreteur Lua 5.1
(lupa, installe dans le bac a sable de test seulement ; sans lui ces tests sont ignores) avec un faux DCS : horloge pilotee,
`LoGetAltitude(x, z)` = une fonction connue de x, z, `LoGeoCoordinatesToLoCoordinates`, et un faux `net.dostring_in` qui execute
le bout de Lua envoye avec un faux `land` et un faux `a_do_script`. Aucun DCS."""
import json
import math
import re
from pathlib import Path

import pytest

from test_autonome_hook import HOOK, SRC, Hote, T_EPOCH, lignes_de_code, lupa_lua51

FAUX_RELIEF = r'''
FAUX = { cout_alt = 0.015625, alt_echec = {}, geo_echec = nil, net_base = 0, net_point = 0, net_mode = nil, land_echec = {},
         surf_echec = {} }
ALT_APPELS, NET_TAILLES, LAND_APPELS, SURF_APPELS = {}, {}, 0, 0

function ALT_F(x, z) return 100 + x * 0.001 + z * 0.0002 end
function SURF_F(x, z) return (math.floor(x / 100) + math.floor(z / 100)) % 4 + 1 end

function LoGetAltitude(x, z)
  CLOCK.real = CLOCK.real + FAUX.cout_alt
  table.insert(ALT_APPELS, { x, z })
  local mode = FAUX.alt_echec[#ALT_APPELS]
  if mode == 'erreur' then error('altitude en panne') end
  if mode == 'nil' then return nil end
  if mode == 'nan' then return 0 / 0 end
  if mode == 'texte' then return 'haut' end
  return ALT_F(x, z)
end

function LoGeoCoordinatesToLoCoordinates(lon, lat)
  if FAUX.geo_echec == 'erreur' then error('geo en panne') end
  if FAUX.geo_echec then return nil end
  return { x = lat * 1000, z = lon * 1000 }
end

net = {}
function net.dostring_in(env, code)
  if FAUX.net_mode == 'erreur' then error('passerelle en panne') end
  if env ~= 'mission' then return 'mauvais environnement', true end
  local land = {
    getHeight = function(v)
      LAND_APPELS = LAND_APPELS + 1
      local mode = FAUX.land_echec[LAND_APPELS]
      if mode == 'erreur' then error('land en panne') end
      if mode == 'nil' then return nil end
      return ALT_F(v.x, v.y)
    end,
    getSurfaceType = function(v)
      SURF_APPELS = SURF_APPELS + 1
      if FAUX.surf_echec[SURF_APPELS] then return nil end
      return SURF_F(v.x, v.y)
    end,
  }
  local function a_do_script(chunk)               -- DCS 2.9.15+ : valeurs decalees d'un cran, la derniere est perdue
    local f, e = loadstring(chunk)
    if not f then return nil, 'erreur de syntaxe : ' .. tostring(e) end
    setfenv(f, setmetatable({ land = land }, { __index = _G }))
    local a, b = f()
    return nil, a
  end
  local f, e = loadstring(code)
  assert(f, e)
  setfenv(f, setmetatable({ a_do_script = a_do_script }, { __index = _G }))
  local r = f()
  local lignes = 0
  if type(r) == 'string' then for _ in r:gmatch('\n') do lignes = lignes + 1 end end
  table.insert(NET_TAILLES, math.max(lignes - 1, 0))
  CLOCK.real = CLOCK.real + FAUX.net_base + FAUX.net_point * math.max(lignes - 1, 0)
  if FAUX.net_mode == 'sans_ok' then return 'ERR\n' .. r, true end
  if FAUX.net_mode == 'muet' then return '', true end
  if FAUX.net_mode == 'court' then return (r:gsub('\n[^\n]*\n$', '\n')), true end
  if FAUX.net_mode == 'alt_folle' then return (r:gsub('^OK\n[^;\n]*;', 'OK\nabc;')), true end
  if FAUX.net_mode == 'surface_inattendue' then return (r:gsub('^OK\n([^;\n]*);', 'OK\n%1;7')), true end
  return r, true
end
'''

DT = 0.25                    # une image : 1/4 s ; toutes les valeurs de temps sont des fractions exactes de 2
COUT = 0.015625              # 15,625 ms par appel de LoGetAltitude


class HoteRelief(Hote):
    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.L.execute(FAUX_RELIEF)

    def demande(self, nom='z', **plus):
        d = {'id': f'01_{nom}', 'lat': 36.2, 'lon': 37.25, 'n': 5, 'pas_m': 100, 'methode': 'export', 'surface': False,
             'budget_image_ms': 40, 'sortie': str(self.dir / f'{nom}.csv')}
        d.update(plus)
        champs = ', '.join(f'{k} = {self.lua(v)}' for k, v in d.items() if v is not None)
        (self.dir / 'releve_demande.lua').write_text('return { ' + champs + ' }\n', encoding='utf-8')
        return d

    def servir(self):
        """La seconde pleine qui sert la demande (pas() ne passe qu'une fois par seconde)."""
        self.tick(1.0)

    def images(self, n):
        self.tick(DT, n)

    def faux(self, code):
        self.L.execute(code)

    def lua_val(self, expr):
        return self.L.eval(expr)

    @property
    def fait(self):
        return self.fichier('releve_fait.txt')

    def csv(self, nom='z'):
        return (self.dir / f'{nom}.csv').read_text(encoding='utf-8')

    def json(self, nom='z'):
        return json.loads((self.dir / f'{nom}.json').read_text(encoding='utf-8'))

    def appels_alt(self):
        return self.lua_val('#ALT_APPELS')

    def tailles_lots(self):
        t = self.lua_val('NET_TAILLES')
        return [t[i] for i in range(1, len(t) + 1)]

    def jusqu_a_fait(self, max_images=2000):
        for _ in range(max_images):
            if self.fait is not None:
                return
            self.images(1)
        raise AssertionError('releve_fait.txt jamais ecrit')


@pytest.fixture
def h(tmp_path):
    h = HoteRelief(tmp_path)
    h.lancer_la_file()
    return h


def alt_f(x, z):
    return 100 + x * 0.001 + z * 0.0002


def surf_f(x, z):
    return (math.floor(x / 100) + math.floor(z / 100)) % 4 + 1


def attendu_csv(n=5, pas=100, lat=36.2, lon=37.25, surface=False, vides=()):
    x0, z0 = lat * 1000, lon * 1000
    lignes = ['i,j,x,z,alt_m,surface']
    for k in range(n * n):
        i = k // n
        j = k - i * n
        x = x0 + (i - (n - 1) / 2) * pas
        z = z0 + (j - (n - 1) / 2) * pas
        alt = '' if k in vides else '%.2f' % alt_f(x, z)
        surf = str(surf_f(x, z)) if surface else ''
        lignes.append(f'{i},{j},{x:.1f},{z:.1f},{alt},{surf}')
    return '\n'.join(lignes) + '\n'


# ------------------------------------------------------------------ R3 : export

def test_r3_export_n5_lignes_dans_l_ordre_de_k(h):
    h.demande()
    h.servir()
    assert h.fichier('releve_demande.lua') is None                    # lue puis supprimee
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv()
    assert h.csv().count('\n') == 26
    assert h.fait == 'ok' and h.fichier('releve_fait.tmp') is None
    assert h.appels_alt() == 25


def test_r3_export_json_complet(h):
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    j = h.json()
    duree = 8 * DT + 25 * COUT                                         # 8 images de 1/4 s, puis 25 appels de 15,625 ms
    assert list(j) == ['id', 'lat', 'lon', 'x0', 'z0', 'n', 'pas_m', 'methode', 'surface', 'budget_image_ms', 'points', 'erreurs',
                       'duree_s', 'images', 'points_par_s', 'lot_max', 'fin']
    assert j == {'id': '01_z', 'lat': 36.2, 'lon': 37.25, 'x0': 36.2 * 1000, 'z0': 37.25 * 1000, 'n': 5, 'pas_m': 100,
                 'methode': 'export', 'surface': False, 'budget_image_ms': 40, 'points': 25, 'erreurs': 0,
                 'duree_s': pytest.approx(duree, abs=0.0006), 'images': 9, 'points_par_s': pytest.approx(25 / duree, abs=0.006),
                 'lot_max': None, 'fin': 'complet'}


def test_r3_plusieurs_images_si_le_budget_est_petit_et_jamais_plus_d_un_point_au_dela(h):
    h.demande()                                                       # budget 40 ms, un point = 15,625 ms : 3 points par image
    h.servir()
    par_image = [h.appels_alt()]
    while h.fait is None:
        avant = h.appels_alt()
        h.images(1)
        par_image.append(h.appels_alt() - avant)
    assert par_image[:8] == [3] * 8 and par_image[8] == 1
    assert sum(par_image) == 25 and max(par_image) == 3 and h.json()['images'] == 9
    # temps depense dans une image : au plus le budget et un point de plus
    assert all(p * COUT <= 0.040 + COUT for p in par_image)
    assert all((p - 1) * COUT < 0.040 for p in par_image if p)       # le point d'avant n'avait pas epuise le budget


@pytest.mark.parametrize('budget_ms,images', [(1, 25), (15, 25), (16, 13), (31.25, 13), (32, 9), (1000, 1)])
def test_r3_budget_image(h, budget_ms, images):
    h.demande(budget_image_ms=budget_ms)
    h.servir()
    h.jusqu_a_fait()
    assert h.json()['images'] == images and h.csv() == attendu_csv()


def test_r3_une_image_au_moins_un_point_meme_si_un_point_depasse_le_budget(h):
    h.faux('FAUX.cout_alt = 0.5')                                      # un point coute 500 ms, le budget est de 40 ms
    h.demande(n=3)
    h.servir()
    par_image = [h.appels_alt()]
    while h.fait is None:
        avant = h.appels_alt()
        h.images(1)
        par_image.append(h.appels_alt() - avant)
    assert par_image == [1] * 9 and h.json()['images'] == 9


@pytest.mark.parametrize('mode', ['erreur', 'nil', 'nan', 'texte'])
def test_r3_un_point_en_erreur_alt_vide(h, mode):
    h.faux(f"FAUX.alt_echec[7] = '{mode}'")                           # le 7e appel : k = 6
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv(vides={6})
    assert h.csv().splitlines()[7].endswith(',,')
    j = h.json()
    assert j['erreurs'] == 1 and j['points'] == 25 and j['fin'] == 'complet' and h.fait == 'ok'


def test_r3_plusieurs_points_en_erreur(h):
    h.faux("FAUX.alt_echec[1] = 'erreur'; FAUX.alt_echec[25] = 'nil'; FAUX.alt_echec[13] = 'nan'")
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv(vides={0, 12, 24}) and h.json()['erreurs'] == 3


def test_r3_pas_decimal_et_n_pair(h):
    h.demande(n=4, pas_m=12.5, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv(n=4, pas=12.5)
    assert h.json()['pas_m'] == 12.5


def test_r3_les_points_sont_vers_le_nord_puis_vers_l_est(h):
    h.demande(n=3, pas_m=10, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    lignes = [l.split(',') for l in h.csv().splitlines()[1:]]
    x = [float(l[2]) for l in lignes]
    z = [float(l[3]) for l in lignes]
    assert x == [36190.0] * 3 + [36200.0] * 3 + [36210.0] * 3 and z == [37240.0, 37250.0, 37260.0] * 3


def test_r3_le_releve_ne_tourne_que_par_image_sans_servir_deux_fois(h):
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    h.images(50)
    assert h.appels_alt() == 25 and h.fait == 'ok'


# ------------------------------------------------------------------ R4 : mission

def test_r4_mission_memes_lignes_que_export_plus_surface(h):
    h.demande(methode='mission', surface=True, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv(surface=True)
    assert h.fait == 'ok' and h.appels_alt() == 0
    j = h.json()
    assert j['methode'] == 'mission' and j['surface'] is True and j['points'] == 25 and j['erreurs'] == 0
    assert j['lot_max'] == 64 and h.tailles_lots() == [25]


def test_r4_mission_sans_surface_colonne_vide(h):
    h.demande(methode='mission', budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv() and h.lua_val('SURF_APPELS') == 0


def test_r4_mission_et_export_donnent_le_meme_csv(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    a, b = HoteRelief(tmp_path / 'a'), HoteRelief(tmp_path / 'b')
    a.lancer_la_file()
    b.lancer_la_file()
    for hote, methode in ((a, 'export'), (b, 'mission')):
        hote.demande(methode=methode, n=7, pas_m=30, budget_image_ms=1000)
        hote.servir()
        hote.jusqu_a_fait()
    assert a.csv() == b.csv() == attendu_csv(n=7, pas=30)


def test_r4_le_lot_double_puis_se_borne(h):
    h.demande(methode='mission', n=300, budget_image_ms=40)         # appels instantanes : toujours < budget/4
    h.servir()
    h.jusqu_a_fait()
    t = h.tailles_lots()
    assert t[:9] == [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]
    assert t[9:-1] == [16384] * 3 and t[-1] == 90000 - 32704 - 3 * 16384 and sum(t) == 90000
    j = h.json()
    assert j['lot_max'] == 16384 and j['points'] == 90000 and j['images'] == 1
    lignes = h.csv().splitlines()
    assert len(lignes) == 90001 and lignes[1] == attendu_csv(n=300).splitlines()[1] and lignes[-1] == attendu_csv(n=300).splitlines()[-1]


def test_r4_le_lot_est_divise_quand_l_appel_depasse_le_budget_et_se_borne_a_1(h):
    h.faux('FAUX.net_base = 0.125')                                  # 125 ms par appel > budget 40 ms
    h.demande(methode='mission', n=20)
    h.servir()
    h.jusqu_a_fait()
    t = h.tailles_lots()
    assert t[:8] == [64, 32, 16, 8, 4, 2, 1, 1] and set(t[6:]) == {1} and sum(t) == 400
    assert h.json()['lot_max'] == 64 and h.csv() == attendu_csv(n=20)


def test_r4_le_lot_reste_quand_l_appel_est_entre_le_quart_et_le_budget(h):
    h.faux('FAUX.net_base = 0.015625')                               # 15,6 ms : ni < 10 ms (budget/4), ni > 40 ms
    h.demande(methode='mission', n=40, budget_image_ms=40)
    h.servir()
    h.jusqu_a_fait()
    assert set(h.tailles_lots()[:-1]) == {64} and h.json()['lot_max'] == 64


def test_r4_un_lot_ou_plus_par_image_jusqu_au_budget(h):
    h.faux('FAUX.net_base = 0.0625')                                 # un appel = 62,5 ms >= budget : un seul lot par image
    h.demande(methode='mission', n=20, budget_image_ms=40)
    h.servir()
    h.images(2)
    assert len(h.tailles_lots()) == 3                                # 1 (servir) + 2 images


@pytest.mark.parametrize('mode', ['sans_ok', 'muet', 'erreur', 'court', 'alt_folle', 'surface_inattendue'])
def test_r4_reponse_sans_ok_ou_incoherente_erreur_passerelle(h, mode):
    h.faux(f"FAUX.net_mode = '{mode}'")
    h.demande(methode='mission')
    h.servir()
    h.jusqu_a_fait()
    assert h.fait == 'erreur passerelle'
    j = h.json()
    assert j['fin'] == 'erreur' and j['points'] == 0 and h.csv() == 'i,j,x,z,alt_m,surface\n'
    assert 'erreur' in h.journal() and h.sorties == 0                # la nuit continue


def test_r4_erreur_passerelle_au_milieu_garde_les_points_ecrits(h):
    h.faux('FAUX.net_base = 0.125')                                  # un lot par image : 64, 32, 16, ...
    h.demande(methode='mission', n=20)
    h.servir()
    h.images(1)
    h.faux("FAUX.net_mode = 'sans_ok'")
    h.jusqu_a_fait()
    j = h.json()
    assert h.fait == 'erreur passerelle' and j['points'] == 64 + 32 and j['fin'] == 'erreur'
    assert h.csv().count('\n') == 1 + 96


def test_r4_points_en_erreur_de_land(h):
    h.faux("FAUX.land_echec[3] = 'erreur'; FAUX.land_echec[20] = 'nil'")
    h.demande(methode='mission', surface=True, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    csv = attendu_csv(surface=True, vides={2, 19}).splitlines()
    csv[3] = csv[3].rsplit(',', 1)[0] + ',' + csv[3].rsplit(',', 1)[1]   # la surface est lue meme si l'altitude echoue
    assert h.csv().splitlines() == csv
    assert h.json()['erreurs'] == 2 and h.fait == 'ok'


def test_r4_surface_en_erreur_compte_une_erreur_surface_vide(h):
    h.faux('FAUX.surf_echec[5] = true')
    h.demande(methode='mission', surface=True, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    lignes = h.csv().splitlines()
    assert lignes[5].endswith(',') and lignes[5].split(',')[4] != '' and h.json()['erreurs'] == 1


def test_r4_la_passerelle_est_appelee_dans_l_environnement_mission_avec_une_enveloppe(h):
    h.faux("""
      CODES = {}
      local vrai = net.dostring_in
      net.dostring_in = function(env, code) table.insert(CODES, { env, code }); return vrai(env, code) end
    """)
    h.demande(methode='mission', surface=True, budget_image_ms=1000)
    h.servir()
    h.jusqu_a_fait()
    env, code = h.lua_val('CODES[1][1]'), h.lua_val('CODES[1][2]')
    assert env == 'mission' and 'a_do_script([====[' in code and code.rstrip().endswith(']====]))')
    assert 'return table.concat(sortie), 0' in code and 'land.getSurfaceType' in code
    m = re.search(r'local X0, Z0, PAS, N, K0, K1 = ([^\n]*)\nlocal SURF = (true|false)\n', code)
    assert [float(v) for v in m.group(1).split(', ')] == [36200.0, 37250.0, 100.0, 5.0, 0.0, 24.0] and m.group(2) == 'true'
    assert '%.17g' not in m.group(1)                                  # des nombres, pas du format


# ------------------------------------------------------------------ R5 : les cas

DEMANDES_FAUTIVES = [
    ('return 42', 'demande illisible'),
    ('return {{{', 'demande illisible'),
    ('error("x")', 'demande illisible'),
    ('return nil', 'demande illisible'),
    ('return { id = "a", lat = "36", lon = 37, n = 5, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ lat'),
    ('return { id = "a", lat = 36, lon = 37, n = 1, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ n'),
    ('return { id = "a", lat = 36, lon = 37, n = 2.5, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ n'),
    ('return { id = "a", lat = 36, lon = 37, n = 2001, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ n'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 0, methode = "export", sortie = SORTIE }', 'champ pas_m'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "autre", sortie = SORTIE }', 'champ methode'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export", surface = true, sortie = SORTIE }',
     'champ surface avec export'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export", surface = "oui", sortie = SORTIE }',
     'champ surface'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export", budget_image_ms = 0, sortie = SORTIE }',
     'champ budget_image_ms'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export", budget_image_ms = 1001, sortie = SORTIE }',
     'champ budget_image_ms'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export" }', 'champ sortie'),
    ('return { id = 5, lat = 36, lon = 37, n = 5, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ id'),
    ('return { id = "a", lat = 91, lon = 37, n = 5, pas_m = 100, methode = "export", sortie = SORTIE }', 'champ lat'),
    ('return { id = "a", lat = 36, lon = 37, n = 5, pas_m = 100, methode = nil, sortie = SORTIE }', 'champ methode'),
]


@pytest.mark.parametrize('texte,raison', DEMANDES_FAUTIVES)
def test_r5_demande_fautive(h, texte, raison):
    (h.dir / 'releve_demande.lua').write_text(texte.replace('SORTIE', '[[' + str(h.dir / 'z.csv') + ']]'), encoding='utf-8')
    h.servir()
    assert h.fait == 'erreur ' + raison
    assert h.fichier('releve_demande.lua') is None and h.fichier('z.csv') is None
    assert h.sorties == 0
    h.demande()                                                        # et la file continue : un releve sain passe ensuite
    (h.dir / 'releve_fait.txt').unlink()
    h.servir()
    h.jusqu_a_fait()
    assert h.fait == 'ok'


def test_r5_la_demande_saine_apres_une_faute_est_servie(h):
    (h.dir / 'releve_demande.lua').write_text('return 42', encoding='utf-8')
    h.servir()
    assert h.fait == 'erreur demande illisible'
    (h.dir / 'releve_fait.txt').unlink()
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    assert h.fait == 'ok' and h.csv() == attendu_csv()


def test_r5_defauts_de_surface_et_de_budget_absents(h):
    d = h.demande(surface=None, budget_image_ms=None)
    h.servir()
    h.jusqu_a_fait()
    j = h.json()
    assert j['surface'] is False and j['budget_image_ms'] == 40 and h.csv() == attendu_csv()


def test_r5_releve_deja_en_cours(h):
    h.demande(nom='a', n=10)
    h.servir()
    assert h.fait is None
    h.demande(nom='b')
    h.images(4)                                                        # moins d'une seconde : pas de service
    h.tick(1.0)
    assert h.fait == 'erreur deja en cours' and h.fichier('releve_demande.lua') is None and h.fichier('b.csv') is None
    for _ in range(500):
        h.images(1)
        if h.fichier('a.json'):
            break
    assert h.json('a')['fin'] == 'complet' and h.json('a')['points'] == 100 and h.fait == 'ok'     # le premier n'est pas touche
    assert h.csv('a') == attendu_csv(n=10)


def test_r5_geo_en_echec(h):
    h.faux("FAUX.geo_echec = 'nil'")
    h.demande()
    h.servir()
    assert h.fait == 'erreur geo' and h.fichier('z.csv') is None and h.appels_alt() == 0


def test_r5_geo_qui_leve_une_erreur(h):
    h.faux("FAUX.geo_echec = 'erreur'")
    h.demande()
    h.servir()
    assert h.fait == 'erreur geo' and h.sorties == 0


def test_r5_geo_absent_de_l_environnement(h):
    h.faux('LoGeoCoordinatesToLoCoordinates = nil')
    h.demande()
    h.servir()
    assert h.fait == 'erreur geo'


def test_r5_sortie_impossible(h):
    h.demande(sortie=str(h.dir / 'absent' / 'z.csv'))
    h.servir()
    assert h.fait == 'erreur sortie' and h.appels_alt() == 0


def test_r5_altitude_absente_chaque_point_en_erreur_sans_arreter_dcs(h):
    h.faux('LoGetAltitude = nil')
    h.demande()
    h.servir()
    h.jusqu_a_fait()
    assert h.csv() == attendu_csv(vides=set(range(25))) and h.json()['erreurs'] == 25 and h.sorties == 0


def test_r5_releve_annule(h):
    h.demande(n=10)
    h.servir()
    h.images(3)
    fait_avant = h.appels_alt()
    (h.dir / 'releve_annule.txt').write_text('annule')
    h.images(1)
    assert h.fait == 'annule'
    assert h.appels_alt() == fait_avant                                # plus un point apres l'annulation
    j = h.json()
    assert j['fin'] == 'annule' and 0 < j['points'] < 100 and j['points'] == fait_avant
    assert h.csv().count('\n') == 1 + j['points']
    assert h.fichier('releve_annule.txt') is None                      # consomme
    h.images(20)
    assert h.appels_alt() == fait_avant and h.sorties == 0


def test_r5_annulation_verifiee_a_chaque_image_pas_a_la_seconde(h):
    h.demande(n=10)
    h.servir()
    (h.dir / 'releve_annule.txt').write_text('annule')
    h.images(1)                                                        # moins d'une seconde depuis le dernier pas()
    assert h.fait == 'annule'


def test_r5_annulation_en_mission(h):
    h.faux('FAUX.net_base = 0.125')
    h.demande(methode='mission', n=20)
    h.servir()
    h.images(2)
    (h.dir / 'releve_annule.txt').write_text('x')
    h.images(1)
    j = h.json()
    assert h.fait == 'annule' and j['fin'] == 'annule' and j['points'] == 64 + 32 + 16 and j['lot_max'] == 64
    assert h.csv().count('\n') == 1 + j['points']


@pytest.mark.parametrize('comment', ['fin.txt', 'budget'])
def test_r5_fin_de_nuit_pendant_un_releve(h, comment):
    h.demande(n=10)
    h.servir()
    h.images(3)
    points = h.appels_alt()
    if comment == 'fin.txt':
        (h.dir / 'fin.txt').write_text('rester')
        h.tick(1.0)
    else:
        h.tick(4000)                                                   # le budget_s de la nuit (3600) est passe
    assert h.sorties == 1
    j = h.json()
    assert j['fin'] == 'nuit' and h.fait == 'annule'
    assert j['points'] >= points and h.csv().count('\n') == 1 + j['points'] and j['points'] < 100
    assert h.file_lua_a_la_sortie.strip() == 'return false'


def test_r5_arret_de_la_mission_pendant_un_releve(h):
    h.demande(n=10)
    h.servir()
    h.images(2)
    h.arreter()
    j = h.json()
    assert j['fin'] == 'nuit' and h.csv().count('\n') == 1 + j['points']
    h.images(10)
    assert h.appels_alt() == j['points']


def test_r5_la_ligne_de_l_environnement_une_seule_fois_par_nuit(h):
    h.demande(nom='a')
    h.servir()
    h.jusqu_a_fait()
    h.demande(nom='b')
    h.servir()
    h.jusqu_a_fait()
    lignes = [l for l in h.journal().splitlines() if 'environnement du crochet' in l]
    assert len(lignes) == 1
    assert lignes[0].endswith('LoGetAltitude=function, LoGeoCoordinatesToLoCoordinates=function, net=table, net.dostring_in=function, '
                              'Terrain=nil, Terrain.GetHeight=nil, Terrain.GetSurfaceType=nil')


def test_r5_la_ligne_de_l_environnement_dit_ce_qui_manque_et_ce_qui_est_la(tmp_path):
    h = HoteRelief(tmp_path)
    h.faux("LoGetAltitude = nil; net = nil; Terrain = { GetHeight = function() end, GetSurfaceType = 3 }")
    h.lancer_la_file()
    ligne = [l for l in h.journal().splitlines() if 'environnement du crochet' in l]
    assert len(ligne) == 1 and ligne[0].endswith('LoGetAltitude=nil, LoGeoCoordinatesToLoCoordinates=function, net=nil, net.dostring_in=nil, '
                                                'Terrain=table, Terrain.GetHeight=function, Terrain.GetSurfaceType=number')


def test_r5_la_ligne_de_l_environnement_est_ecrite_au_lancement_pas_a_la_demande(tmp_path):
    h = HoteRelief(tmp_path)
    h.armer()
    h.demarrer()
    h.pause(False)
    h.tick(1, 3)
    assert 'environnement' not in h.journal()                          # attente_s pas ecoulee : la file n'est pas partie
    h.tick(1, 8)
    assert 'environnement du crochet' in h.journal() and h.executes


def test_r5_rien_de_neuf_sans_armement(tmp_path):
    h = HoteRelief(tmp_path)
    h.charger('return false')
    h.demande()
    h.demarrer()
    h.pause(False)
    h.tick(1, 100)
    assert h.fichier('releve_demande.lua') is not None and h.fait is None and h.fichier('z.csv') is None
    assert h.appels_alt() == 0 and h.fichier('crochet.log') is None and h.executes == []
    assert h.fichiers() == ['file.lua', 'releve_demande.lua']


def test_r5_pas_de_service_avant_la_marche(tmp_path):
    h = HoteRelief(tmp_path)
    h.armer(attente_s=20)
    h.demarrer()
    h.pause(False)
    h.tick(1, 3)                                                       # phase `attente`
    h.demande()
    h.tick(1, 3)
    assert h.fichier('releve_demande.lua') is not None and h.fait is None
    h.tick(1, 20)                                                       # la file part : les restes d'avant sont effaces
    assert h.fichier('releve_demande.lua') is None and h.fait is None and h.appels_alt() == 0


def test_r5_restes_d_une_autre_nuit_effaces_au_lancement(tmp_path):
    h = HoteRelief(tmp_path)
    for n in ('releve_demande.lua', 'releve_fait.txt', 'releve_annule.txt'):
        (h.dir / n).write_text('return 1' if n.endswith('.lua') else 'ok')
    h.lancer_la_file()
    assert [n for n in h.fichiers() if n.startswith('releve')] == []


@pytest.mark.parametrize('vide', ['', '  ', '\n'])
def test_r5_demande_vide_ni_lue_ni_supprimee(h, vide):
    (h.dir / 'releve_demande.lua').write_text(vide)
    h.servir()
    h.tick(1.0, 3)
    assert h.fait is None and h.fichier('releve_demande.lua') == vide
    h.demande()                                                        # l'autre cote a fini d'ecrire
    h.servir()
    h.jusqu_a_fait()
    assert h.fait == 'ok'


def test_r5_deux_releves_l_un_apres_l_autre(h):
    h.demande(nom='a')
    h.servir()
    h.jusqu_a_fait()
    (h.dir / 'releve_fait.txt').unlink()                              # nuit.py le supprime apres lecture
    h.demande(nom='b', pas_m=50, methode='mission', surface=True)
    h.servir()
    h.jusqu_a_fait()
    assert h.csv('a') == attendu_csv() and h.csv('b') == attendu_csv(pas=50, surface=True)
    assert h.json('a')['lot_max'] is None and h.json('b')['lot_max'] == 64


def test_r5_le_json_a_cote_du_csv_meme_sans_extension_csv(h):
    h.demande(sortie=str(h.dir / 'sortie_sans_ext'))
    h.servir()
    h.jusqu_a_fait()
    assert (h.dir / 'sortie_sans_ext').exists() and (h.dir / 'sortie_sans_ext.json').exists()


def test_r5_horloge_en_panne_un_point_par_image_et_pas_de_boucle_infinie(h):
    h.demande(budget_image_ms=1000)
    h.servir()
    h.panne('realtime')
    avant = h.appels_alt()
    h.tick(1.0)                                                        # pas() ne passe pas sans horloge ; l'image, si
    assert h.appels_alt() - avant <= 1
    h.panne('realtime', False)
    h.jusqu_a_fait()
    assert h.json()['points'] == 25


def test_r5_erreur_interne_dans_un_releve_finit_le_releve_jamais_la_nuit(h):
    h.demande()
    h.servir()
    h.faux("SF = string.format; string.format = function() error('boom') end")      # une panne imprevue dans la lecture
    h.images(1)
    h.faux('string.format = SF')
    lus = h.appels_alt()
    h.images(20)
    assert h.sorties == 0 and h.appels_alt() == lus                    # le releve est fini, la nuit continue
    assert h.fait == 'erreur interne'
    (h.dir / 'releve_fait.txt').unlink()
    h.demande(nom='b')                                                 # et un releve suivant passe
    h.servir()
    h.jusqu_a_fait()
    assert h.csv('b') == attendu_csv()


# ------------------------------------------------------------------ R6 : le crochet, relu

DCS_API = ('LoGetAltitude', 'LoGeoCoordinatesToLoCoordinates', 'net.dostring_in',
           'Sim.getRealTime', 'Sim.getPause', 'Sim.exitProcess', 'Sim.setUserCallbacks', 'Export.LoSetCommand')


def sans_chaines(code):
    code = re.sub(r"'(?:[^'\\]|\\.)*'", "''", code)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)


def corps_du_crochet():
    """Les lignes de code du crochet sans les chaines longues du bout de Lua envoye a la mission (autre environnement)."""
    out, dedans = [], False
    for i, c in lignes_de_code():
        if 'local CHUNK_CORPS = [==[' in c:
            dedans = True
            continue
        if dedans:
            if c.strip() == ']==]':
                dedans = False
            continue
        out.append((i, sans_chaines(c)))
    return out


def test_r6_lua51_interpreteur_lupa():
    L = lupa_lua51.LuaRuntime(unpack_returned_tuples=True)
    ok, err = L.eval('function(s) local f, e = loadstring(s, "hook"); return f ~= nil, e end')(SRC)
    assert ok, err
    assert L.eval('_VERSION') == 'Lua 5.1'


def test_r6_le_bout_envoye_a_la_mission_est_du_lua51_aussi():
    L = lupa_lua51.LuaRuntime(unpack_returned_tuples=True)
    corps = SRC.split('local CHUNK_CORPS = [==[\n', 1)[1].split(']==]', 1)[0]
    ok, err = L.eval('function(s) local f, e = loadstring(s, "bout"); return f ~= nil, e end')(
        'local X0, Z0, PAS, N, K0, K1 = 1, 2, 3, 4, 5, 6\nlocal SURF = true\n' + corps)
    assert ok, err


def test_r6_pas_de_syntaxe_posterieure_a_lua51():
    code = '\n'.join(c for _, c in corps_du_crochet())
    assert not re.search(r'\bgoto\b|::\w+::', code)
    assert '//' not in code and not re.search(r'<<|>>|[^~=<>]~[^=]|\s&\s|\s\|\s', code)
    assert 'table.unpack' not in code and 'utf8' not in code and 'math.tointeger' not in code and 'string.pack' not in code


def test_r6_os_execute_seulement_lancer_et_shutdown():
    appels = [(i, c.strip()) for i, c in lignes_de_code() if 'os.execute' in c]
    assert len(appels) == 2 and any('E.q.lancer' in c for _, c in appels) and any("'shutdown /s /t 120'" in c for _, c in appels)
    assert 'io.popen' not in SRC and 'require' not in '\n'.join(c for _, c in lignes_de_code())


def test_r6_chaque_appel_a_dcs_est_sous_pcall_table():
    trouves = {}
    for i, c in corps_du_crochet():
        for nom in DCS_API:
            if re.search(r'(?<![\w.])' + re.escape(nom) + r'\b', c):
                trouves.setdefault(nom, []).append((i, 'pcall' in c))
    assert set(trouves) == set(DCS_API), set(DCS_API) ^ set(trouves)
    for nom, lignes in trouves.items():
        assert all(sous_pcall for _, sous_pcall in lignes), (nom, lignes)
    # la ligne qui nomme `Sim.setUserCallbacks` etc. est l'une des lignes ci-dessus ; la table complete est au rapport
    assert 'Sim.' not in '\n'.join(c for _, c in corps_du_crochet() if not re.search(r'Sim\.(getRealTime|getPause|exitProcess|setUserCallbacks)', c))


def test_r6_aucun_appel_a_dcs_hors_liste():
    code = '\n'.join(c for _, c in corps_du_crochet())
    assert set(re.findall(r'\b(?:Sim|Export)\.(\w+)', code)) == {'getRealTime', 'getPause', 'exitProcess', 'LoSetCommand', 'setUserCallbacks'}
    assert not re.search(r'\bDCS\.|\bTerrain\.\w+\s*\(|\bland\.|\blfs\.|\bworld\.|\btrigger\.', code)


def test_r6_le_releve_n_ecrit_que_sous_le_dossier_ou_a_la_sortie_demandee():
    ouvertures = [c.strip() for _, c in corps_du_crochet() if 'io.open' in c]
    assert all("chemin(" in c or 'q.sortie' in c or 'R.json' in c for c in ouvertures), ouvertures
