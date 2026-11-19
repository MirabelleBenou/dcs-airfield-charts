"""Tests de la lecture des panneaux de position sur les grands aerodromes (capture/signread.py) : les quatre regles du lot
`lecture-saturation`, une a une. Les tests qui lisent une vue du jeu ont besoin de DCS_CHARTS_SAMPLES (atlas du jeu) et des
vues de mesure (variable DCS_LOTS_LECTURE, comme les tests test_p3 a test_p6 de test_signread.py) : sans eux ils sont ignores."""
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

import signread as sr

YELLOW = (30, 215, 230)      # BGR : jaune de panneau
GRASS = (50, 110, 70)
CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789'


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
    return sr.Templates({c: _glyph_tile(c) for c in CHARS})


def _grass(h, w, seed=3):
    rng = np.random.default_rng(seed)
    img = np.empty((h, w, 3), np.uint8)
    img[:] = GRASS
    return np.clip(img.astype(np.int16) + rng.integers(-12, 12, img.shape), 0, 255).astype(np.uint8)


# ---------- 2.1 le jaune du cadre ----------
def test_chroma_rel_is_035():
    assert sr.CHROMA_REL == 0.35


def test_a_thin_frame_pixel_at_40_percent_chroma_is_yellow_but_not_one_at_30_percent():
    img = np.zeros((4, 4, 3), np.uint8)
    img[0, 0] = (60, 100, 100)       # min(R, G) - B = 40 : 0,40 de max(R, G) : jaune depuis 0,35 (pas a 0,50)
    img[0, 1] = (70, 100, 100)       # 30 : 0,30 de max(R, G) : pas jaune
    soft, _ = sr.yellow_map(img)
    assert soft[0, 0] > 0 and soft[0, 1] == 0


# ---------- 2.2 le second classement des cadres ----------
def test_max_proposals_is_48_and_the_feature_constants_are_named():
    assert sr.MAX_PROPOSALS == 48
    assert (sr.FEAT_BAND, sr.FEAT_CORE_MARGIN, sr.FEAT_DARK, sr.FEAT_LETTER_MIN, sr.FEAT_LETTER_FULL) == (0.12, 0.22, 70, 0.08, 0.1)


def _decoys_and_one_tile(side=24, ch='F', n_decoy=70):
    """70 cadres jaunes minces a interieur gris (parfaits pour le gabarit de cadre, sans coeur sombre ni lettre) et une vraie
    tuile (cadre, coeur sombre, lettre) : le gabarit classe la vraie tuile bien apres le 48e cadre."""
    img = _grass(500, 900)
    for i in range(n_decoy):
        s = 12 if i % 2 else 14
        x, y = 30 + (i % 14) * 60, 30 + (i // 14) * 60
        img[y:y + s, x:x + s] = (110, 110, 110)
        cv2.rectangle(img, (x, y), (x + s - 1, y + s - 1), YELLOW, 1)
    x, y = 700, 400
    img[y:y + side, x:x + side] = cv2.resize(_glyph_tile(ch), (side, side), interpolation=cv2.INTER_AREA)
    return img, (x, y, side)


def test_a_frame_ranked_far_by_the_template_but_first_by_the_second_score_is_read(tpl):
    img, (x, y, s) = _decoys_and_one_tile()
    soft, m = sr.yellow_map(img)
    props = sr.ring_proposals(soft) + sr.bar_proposals(soft)

    def on_tile(p):
        return abs(p[1] + p[3] / 2 - (x + s / 2)) < 0.5 * s and abs(p[2] + p[3] / 2 - (y + s / 2)) < 0.5 * s and 0.6 < p[3] / s < 1.6
    by_template = sorted(props, reverse=True)
    assert min(i for i, p in enumerate(by_template) if on_tile(p)) >= sr.MAX_PROPOSALS      # le gabarit le classe trop loin
    union = sr.rank_union(props, soft, m)
    assert len(union) == len(props)
    assert min(i for i, p in enumerate(union) if on_tile(p)) < sr.MAX_PROPOSALS             # le second score le fait entrer dans la liste lue
    res = sr.read_image(img, tpl)
    assert res['saturee'] and res['ecartees'] > 0                                            # l'image sature bien la liste
    assert [t['car'] for t in res['tuiles'] if t['car'] != '?'] == ['F']


def test_rank_union_interleaves_the_two_rankings_and_keeps_the_template_first_on_a_tie():
    soft = np.zeros((60, 60), np.float32)
    m = np.full((60, 60), 200, np.float32)
    props = [(0.9, 5, 5, 8), (0.8, 25, 5, 8), (0.7, 5, 25, 8)]          # aucun cadre ne vaut quoi que ce soit au second score
    out = sr.rank_union(props, soft, m)
    assert [(p[1], p[2]) for p in out] == [(5, 5), (25, 5), (5, 25)]
    assert [p[0] for p in out] == sorted((p[0] for p in out), reverse=True)
    assert sr.rank_union([], soft, m) == []


def test_feat_is_zero_on_an_edge_frame_and_on_a_frame_without_dark_core():
    soft = np.zeros((60, 60), np.float32)
    m = np.full((60, 60), 200, np.float32)
    assert sr._feat((0.5, 0, 10, 12), soft, m) == 0.0                    # au bord de l'image
    soft[10:22, 10:22] = 1
    assert sr._feat((0.5, 10, 10, 12), soft, m) == 0.0                   # coeur clair (min(R, G) >= 70)


# ---------- 2.3 la lecture non carree ----------
def test_read_shapes_and_scale_gaps_are_named():
    assert sr.ASPECTS_READ == (1.0, 0.88, 0.76)
    assert sr.REFINE_MAX_DIFF == 0.40


def test_coarse_finds_a_narrow_tile_with_a_narrow_box(tpl):
    img = _grass(120, 160)
    h, w = 22, 17                                                          # largeur / hauteur = 0,77
    img[40:40 + h, 60:60 + w] = cv2.resize(_glyph_tile('H'), (w, h), interpolation=cv2.INTER_AREA)
    _, m = sr.yellow_map(img)
    best = sr._coarse(m, tpl, 60 + w / 2, 40 + h / 2, (w + h) / 2)
    sc, x, y, bw, bh = best['H']
    assert sc > 0.8 and bw < 0.9 * bh, best['H']
    assert all(b[4] >= 5 and b[3] >= 5 for b in best.values())


def test_refine_accepts_a_scale_gap_up_to_040(tpl):
    img = _grass(120, 160)
    h, w = 22, 13
    img[40:40 + h, 60:60 + w] = cv2.resize(_glyph_tile('H'), (w, h), interpolation=cv2.INTER_AREA)
    _, m = sr.yellow_map(img)
    box = (61, 44, 11, 14)                                                 # coeur carre-ish trop large : il faut des echelles x et y differentes
    sc = sr._refine(m, tpl.core['H'], box)[0]
    sc09 = sr._refine(m, tpl.core['H'], box, max_diff=0.09)[0]            # l'ecart d'origine
    assert sc > sc09 + 0.01 and sc > 0.95, (sc, sc09)


def test_refine_full_accepts_a_scale_gap_up_to_040(tpl):
    img = _grass(120, 160)
    h, w = 22, 13
    img[40:40 + h, 60:60 + w] = cv2.resize(_glyph_tile('H'), (w, h), interpolation=cv2.INTER_AREA)
    _, m = sr.yellow_map(img)
    tile = (60.0, 40.0, 17.0, 17.0)                                        # tuile carree proposee pour une tuile 13 x 22
    sc = sr._refine_full(m, tpl.tile['H'], tile)[0]
    sc13 = sr._refine_full(m, tpl.tile['H'], tile, max_diff=0.13)[0]       # l'ecart d'origine
    assert sc >= sc13


# ---------- 2.4 les seuils de decide des 10 px ----------
# seconde decision : score 0,83 (>= 0,82 a 10 px), full 0,56 (>= 0,55), depart 0,45, ecart 0,35 ; la premiere (tuile de 10 px : 0,88,
# 0,70, 0,50, 0,40) les refuse
_L = {'car': 'F', 'score': 0.83, 'second': 0.70, 'full': 0.56, 'full2': 0.30, 'depart': 0.45, 'ecart': 0.35, 'indisc': False,
      'tile': (0, 0, 10, 10)}


def test_loose_thresholds_are_named():
    assert (sr.LOOSE_MIN_H, sr.LOOSE_SCORE_L, sr.LOOSE_SCORE_S, sr.LOOSE_FULL, sr.LOOSE_DEPART, sr.LOOSE_ECART) == (10, 0.80, 0.82, 0.55, 0.40, 0.30)


def test_decide_a_10_px_tile_with_the_second_decision_values_gives_the_letter():
    assert sr._decide_strict(_L) == '?'
    assert sr.decide(_L) == 'F'
    assert sr.decide(dict(_L, tile=(0, 0, 10.4, 10.4))) == 'F'


def test_decide_the_same_tile_at_9_px_is_a_question_mark():
    assert sr.decide(dict(_L, tile=(0, 0, 9, 9))) == '?'
    assert sr.decide(dict(_L, tile=(0, 0, 9.4, 9.6))) == '?'
    assert sr.decide(dict(_L, tile=(0, 0, 8, 8))) == '?'


def test_decide_indiscernable_tile_is_a_question_mark_even_with_the_second_decision_values():
    assert sr.decide(dict(_L, indisc=True)) == '?'
    assert sr.decide(dict(_L, indisc=True, tile=(0, 0, 14, 14))) == '?'
    assert sr.decide(None) == '?'


def test_decide_second_decision_still_needs_the_gap_to_the_second_best_letter():
    assert sr.decide(dict(_L, second=0.82)) == '?'                        # score - second = 0,01 < SECOND_GAP
    assert sr.decide(dict(_L, second=0.80)) == 'F'                        # 0,03


def test_decide_second_decision_never_gives_a_letter_without_the_discrimination():
    assert sr.decide(dict(_L, depart=0.39)) == '?'
    assert sr.decide(dict(_L, ecart=0.29)) == '?'
    assert sr.decide(dict(_L, depart=0.5, ecart=0.5, full=0.6, full2=0.9)) == '?'      # le glyphe entier du rival gagne : pas de depart fort
    assert sr.decide(dict(_L, full=0.54)) == '?'
    assert sr.decide(dict(_L, score=0.81)) == '?'                         # 10 a 12 px : 0,82


def test_decide_score_threshold_is_080_from_12_px():
    assert sr.decide(dict(_L, score=0.81, tile=(0, 0, 12, 12))) == 'F'
    assert sr.decide(dict(_L, score=0.79, tile=(0, 0, 12, 12))) == '?'
    assert sr.decide(dict(_L, score=0.81, tile=(0, 0, 11.4, 11.4))) == '?'


def test_decide_a_read_the_first_decision_accepts_is_unchanged():
    r = {'car': 'F', 'score': 0.93, 'second': 0.84, 'full': 0.7, 'full2': 0.3, 'depart': 0.9, 'ecart': 0.9, 'indisc': False, 'tile': (0, 0, 20, 22)}
    assert sr._decide_strict(r) == 'F' and sr.decide(r) == 'F'
    assert sr.decide(dict(r, full=0.61)) == 'F' and sr._decide_strict(dict(r, full=0.61)) == '?'


# ---------- sur le jeu : la tuile Z de la vue 089 (13 x 15, non carree) ----------
def _lots_dir():
    v = os.environ.get('DCS_LOTS_LECTURE')
    if v and (Path(v) / 'mesure_francfort' / 'vues' / '089.png').is_file():
        return Path(v)
    return None


def test_the_z_tile_13_by_15_of_view_089_is_read_z(samples):
    lots = _lots_dir()
    if lots is None:
        pytest.skip('vue 089 absente (variable DCS_LOTS_LECTURE)')
    atlas = sr.load_templates(samples / 'dcs_install', 'GermanyColdWar')
    img = cv2.imread(str(lots / 'mesure_francfort' / 'vues' / '089.png'), cv2.IMREAD_COLOR)
    assert img is not None
    res = sr.read_image(img, atlas)
    bx, by, bw, bh = 3367, 683, 13, 15                                     # la tuile, relevee a l'oeil
    ts = (bw + bh) / 2
    near = [t for t in res['tuiles'] if not t['coupee']
            and abs(t['x'] + t['w'] / 2 - (bx + bw / 2)) <= 0.7 * ts + 3 and abs(t['y'] + t['h'] / 2 - (by + bh / 2)) <= 0.7 * ts + 3
            and 0.5 * ts <= (t['w'] + t['h']) / 2 <= 1.8 * ts]
    assert [t['car'] for t in near if t['car'] != '?'] == ['Z'], res['tuiles']
