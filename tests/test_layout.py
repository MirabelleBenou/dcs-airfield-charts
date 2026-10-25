"""Zones, regle des parkings (cadrage 4.5 et addendum 2), placement des etiquettes et des numeros."""
import math

import numpy as np
import pytest

from render import layout
from render.layout import Zone


class P:
    def __init__(self, name, x, z):
        self.name, self.x, self.z, self.zone = name, x, z, -1


def Z(i, ext_x, ext_z, s):
    return Zone(i, f'P{i + 1}', [i], 0.0, ext_x, 0.0, ext_z, s, '01', '09')


# ---------- zones ----------
def test_zones_single_link_sorted_by_count():
    pk = [P('1', 0, 0), P('2', 0, 100), P('3', 0, 200), P('4', 0, 440),       # liens de 100 m : 1-2-3, puis 4 a 240 m de 3
          P('5', 2000, 0), P('6', 2100, 0)]
    zones = layout.make_zones(pk)
    assert [z.label for z in zones] == ['P1', 'P2']
    assert [z.count for z in zones] == [4, 2]                    # 240 m < 250 m : 4 rejoint la zone de 1-2-3
    assert [p.zone for p in pk] == [0, 0, 0, 0, 1, 1]
    far = layout.make_zones([P('1', 0, 0), P('2', 0, 251)])
    assert [z.count for z in far] == [1, 1]


def test_reading_scale_bounds_and_value():
    pts = [(0, 0), (0, 20), (0, 40), (0, 60)]                    # plus proche voisin : 20 m partout
    assert layout.reading_scale(pts) == pytest.approx(3.8 / 20)
    assert layout.reading_scale([(0, 0), (0, 2)]) == layout.READ_MAX
    assert layout.reading_scale([(0, 0), (0, 500)]) == layout.READ_MIN
    assert layout.ratio_text(0.1515) == '1:6 601'


# ---------- regle des pages (cadrage 4.5) ----------
FRAME = (10.0, 40.0, 126.0, 130.0)


def occ(full):
    w, h = int(FRAME[2] / layout.CELL_MM), int(FRAME[3] / layout.CELL_MM)
    return np.full((h, w), full, bool)


def center_of(z):
    return 70.0, 100.0


def test_rule1_numbers_on_plan_when_plan_scale_is_enough():
    zones = [Z(0, 300, 300, 0.05), Z(1, 300, 300, 0.04)]
    d = layout.decide_mode(zones, 0.041, occ(False), (FRAME[0], FRAME[1]), FRAME, center_of)
    assert d['mode'] == 1                                        # 0,041 >= 80 % de 0,05 et de 0,04


def test_rule2_insets_in_voids_when_they_fit():
    zones = [Z(0, 300, 300, 0.2), Z(1, 200, 200, 0.2), Z(2, 100, 100, 0.2)]
    d = layout.decide_mode(zones, 0.02, occ(False), (FRAME[0], FRAME[1]), FRAME, center_of)
    assert d['mode'] == 2 and d['f'] >= layout.F_MIN
    boxes = d['boxes']
    assert len(boxes) == 3
    for b in boxes:                                              # dans le cadre
        assert b.x >= FRAME[0] and b.x + b.w <= FRAME[0] + FRAME[2]
        assert b.y - layout.TITLE_MM >= FRAME[1] and b.y + b.h <= FRAME[1] + FRAME[3]
    for i, a in enumerate(boxes):                                # sans recouvrement
        for b in boxes[i + 1:]:
            assert (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y - layout.TITLE_MM
                    or b.y + b.h <= a.y - layout.TITLE_MM)


def test_rule2_f_goes_down_in_steps_of_005_to_the_largest_that_fits():
    zones = [Z(0, 480, 480, 0.2)]                                # 570 m x 0,2 = 114 mm a f = 1 : tient ; 126 mm de large
    d1 = layout.decide_mode(zones, 0.02, occ(False), (FRAME[0], FRAME[1]), FRAME, center_of)
    assert d1['mode'] == 2 and d1['f'] == 1.0
    big = [Z(0, 780, 780, 0.2)]                                  # 870 m x 0,2 = 174 mm a f = 1 : f doit descendre
    d2 = layout.decide_mode(big, 0.02, occ(False), (FRAME[0], FRAME[1]), FRAME, center_of)
    assert d2['mode'] == 2 and d2['f'] < 1.0 and abs(d2['f'] * 20 - round(d2['f'] * 20)) < 1e-6


def test_rule3_pages_when_no_void_and_fewest_pages():
    zones = [Z(i, 900, 900, 0.2) for i in range(4)]
    d = layout.decide_mode(zones, 0.02, occ(True), (FRAME[0], FRAME[1]), FRAME, center_of)
    assert d['mode'] == 3
    n_boxes = sum(len(p) for p in d['pages'])
    assert n_boxes == 4 and d['reduced'] == {}
    # le nombre de pages est le minimum sur tous les f de [0,65 ; 1], rangements en lignes ou en colonnes confondus
    best = min(len(layout._layout_pages(zones, f, {})[1]) for f in layout.f_values())
    assert len(d['pages']) == best
    # le plus grand f qui garde ce nombre de pages
    assert all(len(layout._layout_pages(zones, f, {})[1]) > best for f in layout.f_values() if f > d['f'])


def test_pages_inside_usable_area_without_overlap():
    zones = [Z(i, 300 + 40 * i, 250 + 30 * i, 0.15) for i in range(7)]
    d = layout.plan_pages(zones)
    ux, uy, W, H = layout.USABLE
    for page in d['pages']:
        for i, a in enumerate(page):
            assert a.x >= ux - 1e-6 and a.x + a.w <= ux + W + 1e-6
            assert a.y - layout.TITLE_MM >= uy - 1e-6 and a.y + a.h <= uy + H + 1e-6
            for b in page[i + 1:]:
                assert (a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6 or a.y + a.h <= b.y - layout.TITLE_MM + 1e-6
                        or b.y + b.h <= a.y - layout.TITLE_MM + 1e-6)


def test_oversize_inset_is_brought_back_to_usable_area():
    z = Z(0, 3000, 3000, 0.4)
    b = layout.make_inset(z, 1.0, layout.USABLE[2], layout.USABLE[3])
    assert b.w <= layout.USABLE[2] + 1e-9 and b.total_h <= layout.USABLE[3] + 1e-9


# ---------- addendum 2 : reduction des plus grands encarts ----------
def test_reduction_saves_a_page_with_k_in_range():
    zones = [Z(0, 700, 700, 0.2)] + [Z(i, 200, 300, 0.2) for i in (1, 2, 3)]
    assert len(layout._layout_pages(zones, layout.F_MIN, {})[1]) == 2          # sans reduction : 2 pages
    plan = layout.plan_pages(zones)
    assert len(plan['pages']) == 1
    assert list(plan['reduced']) == ['P1']
    assert layout.K_MIN <= plan['reduced']['P1'] < 1.0
    assert plan['f'] == layout.F_MIN
    inset = [b for b in plan['pages'][0] if b.zone.label == 'P1'][0]
    assert inset.k == plan['reduced']['P1']
    assert inset.s == pytest.approx(0.2 * layout.F_MIN * inset.k)             # son propre rapport d'echelle


def test_reduction_one_by_one_largest_first():
    zones = [Z(0, 800, 800, 0.2)] + [Z(i, 200, 300, 0.2) for i in range(1, 6)]
    plan = layout.plan_pages(zones)
    assert len(plan['pages']) == 1
    assert list(plan['reduced'])[0] == 'P1' and plan['reduced']['P1'] == layout.K_MIN
    assert 'P2' in plan['reduced'] and layout.K_MIN <= plan['reduced']['P2'] < 1.0


def test_no_reduction_when_it_cannot_save_a_page():
    zones = [Z(i, 900, 900, 0.2) for i in range(4)]
    plan = layout.plan_pages(zones)
    assert plan['reduced'] == {}
    assert all(b.k == 1.0 for pg in plan['pages'] for b in pg)


def test_no_reduction_when_already_one_page():
    plan = layout.plan_pages([Z(0, 300, 300, 0.2), Z(1, 200, 200, 0.2)])
    assert plan['reduced'] == {} and len(plan['pages']) == 1


# ---------- numeros et etiquettes ----------
def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_numbers_first_free_position_and_no_overlap():
    pts = [(10.0, 10.0), (10.0, 12.0), (12.5, 10.0), (50.0, 50.0)]
    out = layout.place_numbers(pts, [2.0] * 4, 1.8, 0.8)
    assert out[3][0] == 'NE'                                      # isole : premiere position
    rects = [o[1] for o in out]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not overlap(rects[i], rects[j])
    for k, (cx, cy) in enumerate(pts):                            # aucun numero sur un point
        for r in rects:
            assert not layout.circle_rect_overlap(cx, cy, 0.8, r)


def test_labels_do_not_overlap_each_other_forbidden_zones_or_parkings():
    ways = [('A', np.array([[10.0, 50.0], [110.0, 50.0]]), 100.0),
            ('B', np.array([[10.0, 52.0], [110.0, 52.0]]), 100.0),          # presque confondue avec A
            ('C', np.array([[60.0, 10.0], [60.0, 90.0]]), 80.0),
            ('A', np.array([[10.0, 80.0], [20.0, 80.0]]), 10.0)]             # meme nom, plus courte : ignoree
    forbidden = [(55.0, 45.0, 65.0, 56.0)]
    parks = np.array([[35.0, 50.0], [85.0, 50.0]])
    w, h = 4.0, 2.6
    placed = layout.place_labels(ways, lambda nm: (w, h), forbidden, parks, 0.6, (0.0, 0.0, 148.0, 210.0))
    assert set(placed) <= {'A', 'B', 'C'} and 'A' in placed
    rects = {nm: (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2) for nm, (cx, cy) in placed.items()}
    names = sorted(rects)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not overlap(rects[a], rects[b])
        for fz in forbidden:
            assert not layout._hits(rects[a], fz)
        for px, py in parks:
            assert not (rects[a][0] - 0.6 <= px <= rects[a][2] + 0.6 and rects[a][1] - 0.6 <= py <= rects[a][3] + 0.6)
    assert placed['A'][1] == pytest.approx(50.0)                  # sur la plus longue ligne de ce nom


def test_label_not_placed_is_omitted():
    ways = [('A', np.array([[10.0, 50.0], [30.0, 50.0]]), 20.0)]
    placed = layout.place_labels(ways, lambda nm: (4.0, 2.6), [(0.0, 40.0, 148.0, 60.0)], np.empty((0, 2)), 0.6, None)
    assert placed == {}
