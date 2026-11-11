"""Tests de la lecture des panneaux de position (capture/signread.py).

Sans echantillons : les gabarits sont des glyphes dessines par le test (police OpenCV), les scenes des vignettes de cette
police collees sur de l'herbe, a plusieurs echelles ; aucune image du jeu. Avec les echantillons (DCS_CHARTS_SAMPLES) : les
gabarits viennent de l'atlas du jeu de dcs_install, lu par Pillow."""
import json
import zipfile

import cv2
import numpy as np
import pytest

import signcapture
import signread as sr

YELLOW = (30, 215, 230)      # BGR : jaune de panneau
GRASS = (50, 110, 70)


# ---------- gabarits et scenes synthetiques ----------
def _glyph_tile(ch):
    """Une tuile de 122 x 124 : cadre jaune, fond sombre, caractere jaune (58 % de la hauteur)."""
    t = np.full((122, 124, 3), 8, np.uint8)
    cv2.rectangle(t, (2, 2), (121, 119), YELLOW, 8)
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 1.0
    (w, h), _ = cv2.getTextSize(ch, font, scale, 6)
    scale = scale * 70 / h
    (w, h), _ = cv2.getTextSize(ch, font, scale, 9)
    cv2.putText(t, ch, (62 - w // 2, 61 + h // 2), font, scale, YELLOW, 9, cv2.LINE_AA)
    return t


@pytest.fixture(scope='module')
def tpl():
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789'
    return sr.Templates({c: _glyph_tile(c) for c in chars})


def _scene(tile=None, side=48, size=(300, 600), at=(250, 120), seed=3):
    rng = np.random.default_rng(seed)
    img = np.empty((size[0], size[1], 3), np.uint8)
    img[:] = GRASS
    img = np.clip(img.astype(np.int16) + rng.integers(-12, 12, img.shape), 0, 255).astype(np.uint8)
    if tile is not None:
        x, y = at
        img[y:y + side, x:x + side] = cv2.resize(tile, (side, side), interpolation=cv2.INTER_AREA)
    return img


def _letters(res):
    return [t['car'] for t in res['tuiles'] if t['car'] != '?']


# ---------- lecture d'une tuile ----------
@pytest.mark.parametrize('ch', ['F', 'D', 'C', 'H', 'V'])
@pytest.mark.parametrize('side', [60, 40, 36])
def test_reads_a_position_tile_at_several_scales(tpl, ch, side):
    res = sr.read_image(_scene(_glyph_tile(ch), side), tpl)
    assert _letters(res) == [ch]
    t = [t for t in res['tuiles'] if t['car'] == ch][0]
    assert abs(t['x'] + t['w'] / 2 - (250 + side / 2)) <= 3 and abs(t['y'] + t['h'] / 2 - (120 + side / 2)) <= 3
    assert not t['coupee'] and not t['attente']


@pytest.mark.parametrize('ch', ['F', 'D', 'C', 'H', 'V', 'B', 'Z'])
@pytest.mark.parametrize('side', [20, 14, 11, 9])
def test_small_tile_is_read_right_or_left_as_question_mark_never_wrong(tpl, ch, side):
    res = sr.read_image(_scene(_glyph_tile(ch), side), tpl)
    assert _letters(res) in ([ch], [])


def test_read_image_is_pure_and_size_independent(tpl):
    a = sr.read_image(_scene(_glyph_tile('F'), 40), tpl)
    b = sr.read_image(_scene(_glyph_tile('F'), 40), tpl)
    assert a == b
    big = sr.read_image(_scene(_glyph_tile('F'), 40, size=(900, 1800), at=(1000, 400)), tpl)
    assert _letters(big) == ['F']


def test_grass_alone_gives_no_tile(tpl):
    assert sr.read_image(_scene(), tpl)['tuiles'] == []


def test_a_blank_tile_never_gives_a_letter(tpl):
    blank = _glyph_tile('F')
    blank[17:105, 17:107] = 8
    assert _letters(sr.read_image(_scene(blank, 48), tpl)) == []


def test_direction_sign_dark_letter_on_yellow_is_never_read(tpl):
    """Polarite : une lettre de direction est noire sur jaune ; le meme glyphe en negatif ne se rend pas."""
    t = _glyph_tile('F')
    core = t[17:105, 17:107].copy()
    neg = np.full_like(t, 0)
    neg[:] = YELLOW
    inv = np.where(core.min(axis=2, keepdims=True) > 100, 8, YELLOW).astype(np.uint8)
    neg[17:105, 17:107] = inv
    assert _letters(sr.read_image(_scene(neg, 48), tpl)) == []


def test_two_tiles_in_one_image_are_both_read(tpl):
    img = _scene(_glyph_tile('D'), 40, at=(100, 120))
    x, y = 360, 100
    img[y:y + 40, x:x + 40] = cv2.resize(_glyph_tile('F'), (40, 40), interpolation=cv2.INTER_AREA)
    res = sr.read_image(img, tpl)
    assert sorted(_letters(res)) == ['D', 'F']


def test_shadowed_tile_is_still_read(tpl):
    """A l'ombre : le jaune est olive, la luminance est divisee par deux ; le jaune est juge relativement."""
    img = _scene(_glyph_tile('H'), 48)
    shade = img.astype(np.float32)
    shade[110:180, 240:310] *= 0.5
    img = shade.astype(np.uint8)
    assert _letters(sr.read_image(img, tpl)) in (['H'], [])      # jamais une autre lettre


# ---------- tuile coupee, panneau d'attente ----------
def _panel(img, x, y, w, h):
    img[y:y + h, x:x + w] = (40, 60, 235)
    img[y + h // 4:y + 3 * h // 4, x + w // 6:x + 5 * w // 6:3] = (250, 250, 250)      # inscription blanche
    return img


def test_red_panel_beside_a_tile_sets_attente(tpl):
    img = _scene(_glyph_tile('F'), 48, at=(250, 120))
    _panel(img, 250 - 96, 120, 96, 48)
    res = sr.read_image(img, tpl)
    assert _letters(res) == ['F'] and [t['attente'] for t in res['tuiles'] if t['car'] == 'F'] == [True]


def test_red_panel_is_found_despite_white_lettering():
    img = _panel(_scene(), 100, 100, 90, 44)
    assert any(abs(p[0] - 100) <= 3 and abs(p[2] - 90) <= 4 for p in sr.red_panels(img) if p[3] >= 12)


def _cut_scene(ch, vis, edge, panel):
    img = _scene(size=(300, 600))
    tile = cv2.resize(_glyph_tile(ch), (48, 48), interpolation=cv2.INTER_AREA)
    if edge == 'd':
        img[120:168, 600 - vis:600] = tile[:, :vis]
        if panel:
            _panel(img, 600 - vis - 96, 120, 96, 48)
    elif edge == 'g':
        img[120:168, 0:vis] = tile[:, 48 - vis:]
        if panel:
            _panel(img, vis, 120, 96, 48)
    elif edge == 'h':
        img[0:vis, 200:248] = tile[48 - vis:, :]
    else:
        img[300 - vis:300, 200:248] = tile[:vis, :]
    return img


@pytest.mark.parametrize('panel', [False, True])
@pytest.mark.parametrize('edge', ['d', 'g', 'h', 'b'])
@pytest.mark.parametrize('vis', [20, 24, 30])
@pytest.mark.parametrize('ch', ['F', 'H'])
def test_tile_really_cut_by_an_image_edge_is_signalled_and_never_read(tpl, ch, vis, edge, panel):
    """20 a 30 px visibles d'une tuile de 48, sur les quatre bords, avec et sans panneau rouge : une tuile coupee, `?`."""
    res = sr.read_image(_cut_scene(ch, vis, edge, panel), tpl)
    assert [(t['car'], t['coupee']) for t in res['tuiles']] == [('?', True)]


# ---------- fenetre parasite ----------
def test_parasitic_window_is_detected_and_excluded(tpl):
    img = _scene(_glyph_tile('F'), 48, at=(100, 120))
    img[:150, 600 - 120:] = 255
    res = sr.read_image(img, tpl)
    assert res['fenetre'] is not None
    x, y, w, h = res['fenetre']
    assert abs(x - 480) <= 2 and y == 0 and w == 120 and abs(h - 150) <= 2
    assert _letters(res) == ['F']


def test_only_the_window_rectangle_is_excluded(tpl):
    """Une tuile a droite du rectangle mais sous lui est lue ; une tuile dans le rectangle ne l'est pas."""
    img = _scene(size=(300, 700))
    tile = cv2.resize(_glyph_tile('F'), (48, 48), interpolation=cv2.INTER_AREA)
    img[220:268, 620:668] = tile                    # sous la fenetre (qui finit a la rangee 150)
    img[40:88, 600:648] = tile                      # dans la fenetre : recouverte
    img[:150, 560:] = 255
    res = sr.read_image(img, tpl)
    assert res['fenetre'] == [560, 0, 140, 150]
    assert _letters(res) == ['F'] and [t['y'] for t in res['tuiles'] if t['car'] == 'F'] == [220]


def test_tile_straddling_the_window_edge_is_coupee(tpl):
    img = _scene(size=(300, 700))
    img[120:168, 520:568] = cv2.resize(_glyph_tile('F'), (48, 48), interpolation=cv2.INTER_AREA)
    img[:150, 560:] = 255
    res = sr.read_image(img, tpl)
    assert _letters(res) == [] and any(t['coupee'] for t in res['tuiles'])


def test_no_window_without_a_white_block(tpl):
    assert sr.read_image(_scene(), tpl)['fenetre'] is None


# ---------- image figee ----------
def test_frozen_identical_images():
    a = _scene()
    assert sr.frozen(a, a.copy()) is True


def test_frozen_when_only_40_pixels_differ_but_not_with_2000():
    a = np.zeros((1440, 3440, 3), np.uint8)
    b = a.copy()
    b[10, 100:140] = 255                            # 40 pixels sur 4,95 millions : moins de 0,01 %
    assert sr.frozen(a, b) is True
    c = a.copy()
    c[10:60, 100:140] = 255                         # 2 000 pixels
    assert sr.frozen(a, c) is False


def test_frozen_ignores_tiny_noise_and_needs_same_shape():
    a = _scene()
    noisy = np.clip(a.astype(np.int16) + 5, 0, 255).astype(np.uint8)      # ecart de 5/255 : pas un pixel qui differe
    assert sr.frozen(a, noisy) is True
    assert sr.frozen(a, a[:100]) is False
    assert sr.frozen(None, a) is False


# ---------- lire : index, fichiers ecrits ----------
def _prepare(tmp_path, images, statuts=None):
    base = tmp_path / 'panneaux' / 'T' / 'B'
    (base / 'img').mkdir(parents=True)
    vues = []
    for n, im in enumerate(images, 1):
        if im is not None:
            cv2.imwrite(str(base / 'img' / f'{n:03d}.png'), im)
        vues.append({'numero': n, 'statut': (statuts or {}).get(n, 'faite'), 'img': f'img/{n:03d}.png', 'vignette': None})
    (base / 'index.json').write_text(json.dumps({'base': 'B', 'theatre': 'T', 'vues': vues}), encoding='utf-8')
    return base


def test_read_base_statuses_lue_figee_absente(tpl, tmp_path):
    a = _scene(_glyph_tile('F'), 48, size=(800, 1400))
    b = a.copy()
    b[5, 5:45] = 255                                # 40 pixels sur 1,1 million : figee
    base = _prepare(tmp_path, [a, b, None, _scene()], {4: 'echec'})
    index = json.loads((base / 'index.json').read_text(encoding='utf-8'))
    log = []
    views = sr.read_base(base, index, tpl, 'B', log.append)
    assert [(v['vue'], v['image']) for v in views] == [(1, 'lue'), (2, 'figee'), (3, 'absente')]     # la vue 4 (echec) est sautee
    assert [t['car'] for t in views[0]['tuiles'] if t['car'] != '?'] == ['F']
    assert views[1]['tuiles'] == [] and len(log) == 3


def test_instable_views_are_read_too(tpl, tmp_path):
    base = _prepare(tmp_path, [_scene(_glyph_tile('D'), 48)], {1: 'instable'})
    index = json.loads((base / 'index.json').read_text(encoding='utf-8'))
    assert [v['image'] for v in sr.read_base(base, index, tpl, 'B', lambda *a: None)] == ['lue']


def test_write_lecture_json_and_tsv_and_nothing_else(tpl, tmp_path):
    a = _scene(_glyph_tile('F'), 48)
    base = _prepare(tmp_path, [a])
    before = sorted(p.name for p in base.rglob('*'))
    index = json.loads((base / 'index.json').read_text(encoding='utf-8'))
    views = sr.read_base(base, index, tpl, 'B', lambda *a: None)
    sr.write_lecture(base, 'B', 'T', views)
    after = sorted(p.name for p in base.rglob('*'))
    assert sorted(set(after) - set(before)) == ['lecture.json', 'lecture.tsv']
    doc = json.loads((base / 'lecture.json').read_text(encoding='utf-8'))
    v = doc['vues'][0]
    assert v['vue'] == 1 and v['image'] == 'lue' and v['fenetre_parasite'] is False
    t = [t for t in v['tuiles'] if t['car'] == 'F'][0]
    assert set(t) >= {'x', 'y', 'w', 'h', 'car', 'score', 'depart', 'ecart', 'attente', 'coupee'}
    lines = (base / 'lecture.tsv').read_text(encoding='utf-8').splitlines()
    assert lines[0].split('\t') == list(sr.TSV_HEAD) and any(l.split('\t')[6] == 'F' for l in lines[1:])


# ---------- atlas ----------
def test_missing_atlas_is_a_read_error_naming_the_path(tmp_path):
    with pytest.raises(sr.ReadError, match='introuvable'):
        sr.load_templates(tmp_path, 'Syria')


def test_unreadable_atlas_is_a_read_error(tmp_path):
    p = sr.atlas_path(tmp_path, 'Syria')
    p.parent.mkdir(parents=True)
    p.write_bytes(b'pas un zip')
    with pytest.raises(sr.ReadError, match='illisible'):
        sr.load_templates(tmp_path, 'Syria')
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('autre.txt', 'x')
    with pytest.raises(sr.ReadError, match='abc absente'):
        sr.load_templates(tmp_path, 'Syria')


def test_lire_without_base_is_a_usage_error():
    with pytest.raises(SystemExit):
        signcapture.main(['x', 'lire', 'Syria'], None)


class _Cfg:
    def __init__(self, p):
        self.dcs_install = p
        self.work_dir = p


@pytest.fixture
def lire_env(tmp_path, tpl, monkeypatch):
    monkeypatch.setattr(sr, 'load_templates', lambda *a: tpl)
    return _Cfg(tmp_path), (lambda c, t: tmp_path / 'panneaux' / t)


def test_lire_missing_index_stops(tmp_path, lire_env):
    cfg, pd = lire_env
    (tmp_path / 'panneaux' / 'T' / 'B').mkdir(parents=True)
    with pytest.raises(sr.ReadError, match='index.json'):
        sr.cmd_lire(cfg, 'T', ['B'], pd)
    with pytest.raises(sr.ReadError, match='introuvable'):
        sr.cmd_lire(cfg, 'T', ['Absente'], pd)


@pytest.mark.parametrize('contenu', [b'pas du json', b'\xff\xfe', b'[]', b'{"vues": 3}', b'{"base": "B"}', b'{"vues": [1]}'])
def test_lire_corrupt_index_is_a_read_error_naming_the_file(tmp_path, lire_env, contenu):
    cfg, pd = lire_env
    d = tmp_path / 'panneaux' / 'T' / 'B'
    d.mkdir(parents=True)
    (d / 'index.json').write_bytes(contenu)
    with pytest.raises(sr.ReadError, match='index.json'):
        sr.cmd_lire(cfg, 'T', ['B'], pd)
    assert sorted(p.name for p in d.iterdir()) == ['index.json']        # rien d'ecrit


def test_lire_finds_the_base_folder_without_a_rn5_and_writes_atomically(tmp_path, lire_env):
    cfg, pd = lire_env
    base = _prepare(tmp_path, [_scene(_glyph_tile('F'), 48)])        # panneaux/T/B : aucun .rn5 nulle part
    log = []
    sr.cmd_lire(cfg, 'T', ['b'], pd, log.append)                      # casse ignoree
    assert sorted(p.name for p in base.iterdir()) == ['img', 'index.json', 'lecture.json', 'lecture.tsv']   # pas de .tmp
    assert (base / 'lecture.json').read_bytes().endswith(b'\n') and b'\r' not in (base / 'lecture.tsv').read_bytes()
    assert any('lire T/B' in l for l in log)


def test_saturated_proposals_are_flagged_per_view(tpl, tmp_path):
    img = _scene(size=(900, 1800))
    tile = cv2.resize(_glyph_tile('F'), (24, 24), interpolation=cv2.INTER_AREA)
    for i in range(50):
        x, y = 40 + (i % 10) * 160, 40 + (i // 10) * 160
        img[y:y + 24, x:x + 24] = tile
    res = sr.read_image(img, tpl)
    assert res['saturee'] is True
    flat = np.full((300, 600, 3), GRASS, np.uint8)
    calm = sr.read_image(flat, tpl)
    assert calm['saturee'] is False and calm['ecartees'] == 0 and calm['tuiles'] == []
    assert res['ecartees'] > 0
    base = _prepare(tmp_path, [img])
    views = sr.read_base(base, json.loads((base / 'index.json').read_text(encoding='utf-8')), tpl, 'B', lambda *a: None)
    sr.write_lecture(base, 'B', 'T', views)
    assert json.loads((base / 'lecture.json').read_text(encoding='utf-8'))['vues'][0]['propositions_saturees'] is True
    assert json.loads((base / 'lecture.json').read_text(encoding='utf-8'))['vues'][0]['propositions_ecartees'] > 0


# ---------- avec les echantillons : gabarits de l'atlas du jeu ----------
@pytest.mark.parametrize('theatre, n', [('Syria', 26), ('GermanyColdWar', 35)])
def test_atlas_gives_the_glyphs_of_the_theatre_without_zero(samples, theatre, n):
    t = sr.load_templates(samples / 'dcs_install', theatre)
    assert len(t.chars) == n and '0' not in t.chars
    assert all(c in t.chars for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')


@pytest.mark.parametrize('theatre', ['Syria', 'GermanyColdWar'])
def test_atlas_templates_read_their_own_tiles_at_several_scales(samples, theatre):
    t = sr.load_templates(samples / 'dcs_install', theatre)
    for ch, side in (('F', 50), ('D', 30), ('V', 22), ('Z', 40), ('B', 40), ('A', 26)):
        tile = (np.stack([t.tile[ch]] * 3, axis=2) * 255).astype(np.uint8)
        tile[:, :, 0] //= 8                             # le jaune : B faible, R = G
        res = sr.read_image(_scene(tile, side), t)
        assert _letters(res) in ([ch], []), (theatre, ch, side, res['tuiles'])
    tile = (np.stack([t.tile['F']] * 3, axis=2) * 255).astype(np.uint8)
    tile[:, :, 0] //= 8
    assert _letters(sr.read_image(_scene(tile, 50), t)) == ['F']


def test_a_full_size_image_reads_within_two_seconds(tpl):
    import time
    img = np.empty((1440, 3440, 3), np.uint8)
    img[:] = GRASS
    img[600:648, 1500:1548] = cv2.resize(_glyph_tile('F'), (48, 48), interpolation=cv2.INTER_AREA)
    sr.read_image(img, tpl)
    t0 = time.perf_counter()
    res = sr.read_image(img, tpl)
    assert _letters(res) == ['F']
    assert time.perf_counter() - t0 < 2.0


# ---------- decide : jamais sans departage ----------
_R = {'car': 'F', 'score': 0.93, 'second': 0.84, 'full': 0.7, 'full2': 0.3, 'depart': 0.9, 'ecart': 0.9, 'indisc': False,
      'tile': (0, 0, 20, 22)}


def test_decide_plain_read_needs_every_threshold():
    assert sr.decide(_R) == 'F'
    assert sr.decide(dict(_R, depart=0.1)) == '?'
    assert sr.decide(dict(_R, ecart=0.1)) == '?'
    assert sr.decide(dict(_R, second=0.92)) == '?'                      # deux glyphes a egalite
    assert sr.decide(dict(_R, full=0.61)) == '?'                        # au ras du seuil : pas de marge
    assert sr.decide(dict(_R, tile=(0, 0, 8, 8))) == '?'                # moins de 9 px de haut
    assert sr.decide(dict(_R, depart=0.7, ecart=0.5, full=0.7, full2=0.9)) == '?'    # le glyphe entier du rival gagne nettement


def test_decide_indiscernable_rival_gives_a_question_mark():
    assert sr.decide(dict(_R, indisc=True)) == '?'
    assert sr.decide(dict(_R, indisc=True), attached=True, ring_h=20) == '?'


def test_decide_neighbour_of_a_panel_still_needs_the_discrimination():
    r = dict(_R, score=0.93, second=0.84, full=0.5, depart=0.7, ecart=0.3, tile=(0, 0, 13, 14))
    assert sr.decide(r) == '?'
    assert sr.decide(r, attached=True, ring_h=14) == 'F'
    assert sr.decide(dict(r, depart=-0.9, ecart=-0.4), attached=True, ring_h=14) == '?'    # depart et ecart exiges
    assert sr.decide(dict(r, depart=0.5), attached=True, ring_h=14) == '?'
    assert sr.decide(dict(r, ecart=0.0), attached=True, ring_h=14) == '?'
    assert sr.decide(dict(r, tile=(0, 0, 11, 11)), attached=True, ring_h=11) == '?'       # NEIGHBOUR_H = 13
    assert sr.decide(dict(r, second=0.90), attached=True, ring_h=14) == '?'
    assert sr.decide(dict(r, full=0.3), attached=True, ring_h=14) == '?'


def test_identical_glyphs_are_indistinguishable_and_leave_ecart_empty(tmp_path):
    """B et 8 identiques : aucun pixel ne les separe. La lettre n'est pas rendue, l'ecart est vide au JSON et au TSV."""
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789'
    tiles = {c: _glyph_tile(c) for c in chars}
    tiles['8'] = tiles['B'].copy()
    t = sr.Templates(tiles)
    res = sr.read_image(_scene(_glyph_tile('B'), 48), t)
    assert [x['car'] for x in res['tuiles']] == ['?']
    assert res['tuiles'][0]['ecart'] is None
    sr.write_lecture(tmp_path, 'B', 'T', [{'vue': 1, 'image': 'lue', 'fenetre': None, 'saturee': False, 'tuiles': res['tuiles']}])
    doc = json.loads((tmp_path / 'lecture.json').read_text(encoding='utf-8'))
    assert doc['vues'][0]['tuiles'][0]['ecart'] is None
    row = (tmp_path / 'lecture.tsv').read_text(encoding='utf-8').splitlines()[1].split('\t')
    assert row[sr.TSV_HEAD.index('ecart')] == '' and row[sr.TSV_HEAD.index('car')] == '?'


# ---------- ce qui doit se lire : ombre, petites tuiles, groupes, polarite ----------
def test_shadowed_tile_is_read_at_half_luminance(tpl):
    """A l'ombre : la lettre est exigee au facteur de luminance 0,5 (le jaune est juge relativement)."""
    for ch in 'FDHCEV':
        img = _scene(_glyph_tile(ch), 48).astype(np.float32)
        img[100:200, 200:330] *= 0.5
        assert _letters(sr.read_image(img.astype(np.uint8), tpl)) == [ch], ch


@pytest.mark.parametrize('ch, side', [('I', 9), ('L', 9), ('V', 9), ('A', 10), ('U', 10), ('T', 11), ('Z', 11), ('L', 12)])
def test_small_tiles_down_to_9_px_are_read_for_sharp_glyphs(tpl, ch, side):
    assert _letters(sr.read_image(_scene(_glyph_tile(ch), side), tpl)) == [ch]


def test_tile_under_9_px_is_never_read(tpl):
    for ch in 'ILVAU':
        assert _letters(sr.read_image(_scene(_glyph_tile(ch), 8), tpl)) == []


def _direction_bar(img, x, y, w, h, txt):
    """Un panneau de direction : aplat jaune, lettres noires (les lettres de l'atlas, en negatif)."""
    img[y:y + h, x:x + w] = YELLOW
    cw = int(h * 0.6)
    for i, ch in enumerate(txt):
        g = _glyph_tile(ch)[17:105, 17:107]
        core = cv2.resize(g, (cw, int(h * 0.7)), interpolation=cv2.INTER_AREA)
        inv = np.where(core.min(axis=2, keepdims=True) > 100, 8, YELLOW).astype(np.uint8)
        xx = x + 6 + i * (cw + 4)
        img[y + int(h * 0.15):y + int(h * 0.15) + inv.shape[0], xx:xx + inv.shape[1]] = inv


def test_direction_letters_in_a_frame_are_never_rendered(tpl):
    """Polarite : de vraies lettres de direction (noires sur jaune), dans un cadre ; des candidats existent, aucune lettre ne sort."""
    img = _scene(size=(300, 600))
    for i, ch in enumerate('DFHE'):
        t = np.full((122, 124, 3), 8, np.uint8)
        t[:] = YELLOW
        g = _glyph_tile(ch)[17:105, 17:107]
        t[17:105, 17:107] = np.where(g.min(axis=2, keepdims=True) > 100, 8, YELLOW).astype(np.uint8)
        cv2.rectangle(t, (2, 2), (121, 119), (8, 8, 8), 6)             # cadre sombre : le panneau de direction jaune plein
        x = 60 + i * 120
        img[100:148, x:x + 48] = cv2.resize(t, (48, 48), interpolation=cv2.INTER_AREA)
    dbg = []
    res = sr.read_image(img, tpl, debug=dbg)
    assert _letters(res) == []


@pytest.mark.parametrize('ch, side', [('H', 48), ('D', 48), ('F', 48), ('H', 40), ('D', 40)])
def test_position_tile_in_the_middle_of_a_direction_sign_is_read(tpl, ch, side):
    img = _scene(size=(300, 600))
    img[130:130 + side + 8, 150:450] = YELLOW
    img[134:134 + side, 300:300 + side] = cv2.resize(_glyph_tile(ch), (side, side), interpolation=cv2.INTER_AREA)
    assert _letters(sr.read_image(img, tpl)) == [ch]


@pytest.mark.parametrize('ch, side', [('H', 48), ('D', 48), ('F', 48), ('H', 40), ('D', 40)])
def test_position_tile_at_the_end_of_a_direction_sign_is_read(tpl, ch, side):
    img = _scene(size=(300, 600))
    _direction_bar(img, 150, 130, 150, side + 8, 'DE')
    img[134:134 + side, 300:300 + side] = cv2.resize(_glyph_tile(ch), (side, side), interpolation=cv2.INTER_AREA)
    assert _letters(sr.read_image(img, tpl)) == [ch]


def test_a_tile_proposed_twice_is_output_once(tpl):
    res = sr.read_image(_scene(_glyph_tile('F'), 48), tpl)
    assert len(res['tuiles']) == 1


# ---------- avec les echantillons : la lettre fausse « Q » ----------
def test_o_does_not_become_a_q_beside_a_panel_after_blur(samples):
    """Cas de la relecture : atlas GermanyColdWar, O de 14 px, panneau rouge, flou de 1 px. Le cadre n'avait que 0,644 pour un
    seuil de 0,64 : une marge est exigee, la lettre n'est pas rendue."""
    t = sr.load_templates(samples / 'dcs_install', 'GermanyColdWar')
    tile = (np.stack([t.tile['O']] * 3, axis=2) * 255).astype(np.uint8)
    tile[:, :, 0] //= 8
    for seed in (7, 8, 9):
        img = _scene(tile, 14, seed=seed)
        _panel(img, 222, 120, 28, 14)
        img = cv2.GaussianBlur(img, (0, 0), 1.0)
        assert _letters(sr.read_image(img, t)) == [], seed


# ---------- P3 a P6 sur les fichiers du depot des lots (variable DCS_LOTS_LECTURE) ----------
import math
import os
from pathlib import Path


@pytest.fixture(scope='module')
def lots():
    v = os.environ.get('DCS_LOTS_LECTURE')
    if not v or not (Path(v) / 'mesure_francfort' / 'VERITE.tsv').is_file():
        pytest.skip('fichiers du depot des lots absents (variable DCS_LOTS_LECTURE = lots/ac-panneaux-lecture)')
    return Path(v)


@pytest.fixture(scope='module')
def atlas(samples):
    return {th: sr.load_templates(samples / 'dcs_install', th) for th in ('Syria', 'GermanyColdWar')}


def _imread(p):
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    assert img is not None, p
    return img


def _truth(lots):
    return [l.rstrip('\n').split('\t') for l in (lots / 'mesure_francfort' / 'VERITE.tsv').read_text(encoding='utf-8').splitlines()[1:]]


def test_p3_frankfurt_extracts_at_least_8_right_none_wrong(lots, atlas):
    ok = wrong = 0
    cache = {}
    for t in _truth(lots):
        vue, x, y, w, h, lettre = t[:6]
        x, y, w, h = map(int, (x, y, w, h))
        cx, cy = x + w / 2, y + h / 2
        fs = [f for f in (lots / 'mesure_francfort' / 'extraits').glob(f'{vue}_*.png')
              if int(f.stem.split('_')[1]) <= x < int(f.stem.split('_')[1]) + 480
              and int(f.stem.split('_')[2]) <= y < int(f.stem.split('_')[2]) + 240]
        f = fs[0]
        if f not in cache:
            cache[f] = sr.read_image(_imread(f), atlas['GermanyColdWar'])
        x0, y0 = int(f.stem.split('_')[1]), int(f.stem.split('_')[2])
        for tl in cache[f]['tuiles']:
            if tl['car'] == '?' or math.hypot(tl['x'] + x0 + tl['w'] / 2 - cx, tl['y'] + y0 + tl['h'] / 2 - cy) > 25:
                continue
            if lettre != '?' and tl['car'] == lettre:
                ok += 1
            else:
                wrong += 1
    # aucune lettre fausse nulle part dans les extraits : toute lettre rendue doit etre sur une ligne de verite et juste
    rendered = sum(1 for r in cache.values() for tl in r['tuiles'] if tl['car'] != '?')
    assert wrong == 0 and ok >= 8 and rendered == ok, (ok, wrong, rendered)


def test_p4_negative_extracts_give_no_letter(lots, atlas):
    files = sorted((lots / 'mesure_francfort' / 'extraits').glob('neg_*.png'))
    assert len(files) == 16
    for f in files:
        assert [t for t in sr.read_image(_imread(f), atlas['GermanyColdWar'])['tuiles'] if t['car'] != '?'] == [], f.name


# vérité à l'œil des 12 témoins : (théâtre, lettres attendues de gauche à droite, fenêtre attendue, tuile coupée attendue)
_TEMOINS = {
    'GermanyColdWar_Mahlwinkel_001': ('GermanyColdWar', 'A', False, False),
    'GermanyColdWar_Mahlwinkel_005': ('GermanyColdWar', 'CC', False, False),
    'GermanyColdWar_Mahlwinkel_013': ('GermanyColdWar', '', False, False),
    'GermanyColdWar_Templin_009': ('GermanyColdWar', '', False, True),
    'Syria_Konya_001': ('Syria', '', True, False),
    'Syria_Konya_008': ('Syria', 'FF', False, False),
    'Syria_Konya_014': ('Syria', 'DD', False, False),
    'Syria_Konya_017': ('Syria', 'FF', False, False),
    'Syria_Konya_024': ('Syria', 'DD', True, False),
    'Syria_Konya_029': ('Syria', 'H', False, False),
    'Syria_Konya_033': ('Syria', 'FF', False, False),
    'Syria_Konya_039': ('Syria', 'EE', False, False),
}


@pytest.mark.parametrize('nom', sorted(_TEMOINS))
def test_p5_witness(lots, atlas, nom):
    theatre, lettres, fenetre, coupee = _TEMOINS[nom]
    res = sr.read_image(_imread(lots / 'mesure' / 'temoins' / f'{nom}.png'), atlas[theatre])
    lus = ''.join(t['car'] for t in sorted(res['tuiles'], key=lambda t: t['x']) if t['car'] != '?')
    assert lus == lettres, (nom, res['tuiles'])
    assert (res['fenetre'] is not None) == fenetre
    assert any(t['coupee'] for t in res['tuiles']) == coupee
    if nom.endswith('Mahlwinkel_013'):
        assert res['tuiles'] == []


def test_p5_sixteen_readable_letters_in_all(lots, atlas):
    assert sum(len(v[1]) for v in _TEMOINS.values()) == 16


def test_p6_entry_208_gives_b_and_nothing_else(lots, atlas):
    res = sr.read_image(_imread(lots / 'sonde_visee' / 'extraits' / '001_B_entree_208.png'), atlas['GermanyColdWar'])
    assert [t['car'] for t in res['tuiles']] == ['B', 'B']
