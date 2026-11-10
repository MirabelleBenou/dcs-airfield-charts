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


def test_tile_cut_by_the_image_edge_is_coupee_and_never_read(tpl):
    w = 600
    img = _scene(size=(300, w))
    _panel(img, w - 70 - 96, 120, 96, 48)
    # le debut du cadre de la tuile, contre le panneau, sort de l'image
    tile = cv2.resize(_glyph_tile('C'), (48, 48), interpolation=cv2.INTER_AREA)
    img[120:168, w - 70:w] = 0
    img[120:168, w - 70:w - 22] = tile
    img[120:168, w - 22:w] = tile[:, :22]
    img = img[:, :w - 22 + 0]                       # image coupee : 22 px de la tuile seulement
    res = sr.read_image(img, tpl)
    cut = [t for t in res['tuiles'] if t['coupee']]
    assert cut and all(t['car'] == '?' for t in cut)
    assert _letters(res) == []


# ---------- fenetre parasite ----------
def test_parasitic_window_is_detected_and_excluded(tpl):
    img = _scene(_glyph_tile('F'), 48, at=(100, 120))
    img[:150, 600 - 120:] = 255
    res = sr.read_image(img, tpl)
    assert res['fenetre'] is not None
    x, y, w, h = res['fenetre']
    assert abs(x - 480) <= 2 and y == 0 and w == 120 and abs(h - 150) <= 2
    assert _letters(res) == ['F']


def test_a_tile_under_the_parasitic_window_is_not_read(tpl):
    img = _scene(_glyph_tile('F'), 48, at=(520, 40))
    img[:150, 600 - 120:] = 255
    assert _letters(sr.read_image(img, tpl)) == []


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


def test_lire_missing_index_stops(tmp_path, tpl, monkeypatch):
    class Cfg:
        dcs_install = tmp_path
        work_dir = tmp_path
    monkeypatch.setattr(sr, 'load_templates', lambda *a: tpl)
    with pytest.raises(sr.ReadError, match='index.json'):
        sr.cmd_lire(Cfg, 'T', ['B'], lambda c, t: tmp_path / 'panneaux' / t, lambda c, t, b: (b, None))


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
