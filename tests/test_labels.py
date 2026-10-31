"""Lot caps-balises-graduations : placement des caps de piste (2.1), des libelles de balises (2.2) et des graduations (2.3),
dans le moteur et dans `republish` (memes fonctions)."""
import math
import re

import pytest
from shapely.geometry import box

from render import compose, page, republish, svgdoc
from render.fonts import CAP_HEIGHT, text_width

FRAME = (11.0, 38.1, 126.0, 131.0)
S = 0.0633                                   # mm par metre
SIZE = page.BEARING_SIZE
H = SIZE * CAP_HEIGHT


def end(label, thr, az, hw=0.7):
    a = math.radians(az)
    return page.CapEnd(label, thr, (math.sin(a), -math.cos(a)), hw, az)


def top_end():
    """Seuil en haut de la piste verticale (x = 60), atterrissage vers le sud : la place d'origine sort du cadre par le haut."""
    return end('180.0°', (60.0, 40.0), 180.0)


# ---------- 2.1 caps ----------
def test_cap_in_place_when_free():
    e = end('360.0°', (60.0, 120.0), 0.0)
    (cx, cy, rot, poly, moved), = page.place_caps([e], S, FRAME, [], [], [])
    assert not moved and rot == 0.0
    assert (cx, cy) == pytest.approx((60.0, 120.0 + BEARING_GAP(S) + H / 2))


def BEARING_GAP(s):
    return page.BEARING_GAP_M * s


def test_cap_outside_the_frame_runs_along_the_runway_on_the_right_of_the_landing_direction():
    e = top_end()
    (cx, cy, rot, poly, moved), = page.place_caps([e], S, FRAME, [], [], [])
    w = text_width(e.label, 'bold', SIZE)
    assert moved
    assert rot == page.cap_norm(180.0 - 90.0) == 90.0              # parallele a l'axe, dans ]-90 ; 90]
    # u = (0, 1) vers le sud, a droite de u (axes de la page) = l'ouest
    assert cx == pytest.approx(60.0 - (0.7 + 0.8 + H / 2)) and cy == pytest.approx(40.0 + w / 2 + 0.8)
    inner = box(FRAME[0] + 0.6, FRAME[1] + 0.6, FRAME[0] + FRAME[2] - 0.6, FRAME[1] + FRAME[3] - 0.6)
    assert inner.contains(poly)


def test_cap_goes_to_the_left_when_the_right_side_is_blocked():
    e = top_end()
    right = box(55.0, 40.0, 59.0, 60.0)                           # a l'ouest de la piste
    (cx, cy, rot, poly, moved), = page.place_caps([e], S, FRAME, [right], [], [])
    assert moved and cx > 60.0 and not poly.intersects(right)


def test_cap_slides_along_the_runway_by_2_mm_steps_when_both_sides_are_blocked():
    e = top_end()
    w = text_width(e.label, 'bold', SIZE)
    block = box(50.0, 40.0, 70.0, 40.0 + w / 2 + 0.8 + 3.0)       # les deux cotes pour g = 0 et g = 2 (pas la piste : x = 60)
    (cx, cy, rot, poly, moved), = page.place_caps([e], S, FRAME, [block], [], [])
    assert moved and not poly.intersects(block)
    g = cy - (40.0 + w / 2 + 0.8)
    assert g == pytest.approx(round(g / 2) * 2) and g >= 4.0 - 1e-9


def test_cap_without_any_place_raises():
    e = top_end()
    with pytest.raises(page.PlacementError):
        page.place_caps([e], S, FRAME, [box(0, 0, 200, 200)], [], [])


def test_cap_touching_a_cap_already_placed_moves():
    a, b = end('180.0°', (60.0, 80.0), 180.0), end('179.0°', (60.4, 80.0), 179.0)
    res = page.place_caps([a, b], S, FRAME, [], [], [])
    assert not res[0][4] and res[1][4] and not res[0][3].intersects(res[1][3])


def test_cap_avoids_a_navaid_and_an_inset():
    e = end('360.0°', (60.0, 120.0), 0.0)
    cx, cy, _ = page.cap_original(e, S)
    for kw in ({'navaids': [box(cx - 1, cy - 1, cx + 1, cy + 1)]}, {'insets': [box(cx - 1, cy - 1, cx + 1, cy + 1)]}):
        (c, d, rot, poly, moved), = page.place_caps([e], S, FRAME, [], kw.get('navaids', []), kw.get('insets', []))
        assert moved and rot == page.cap_norm(0.0 - 90.0)


# ---------- 2.2 libelles de balises ----------
GROUP = (('VOR/DME KAD', '112.60'), [60.0], [100.0])


def positions(runways=(), caps=(), frame=FRAME, group=GROUP):
    return page.place_navaid_labels([group], frame, list(runways), list(caps))[0]


def test_navaid_label_stays_on_the_right_when_free():
    pos, elems, free = positions()
    assert pos == 'right' and free and f'x="{svgdoc.f(62.3)}"' in elems[0]


def test_navaid_label_crossing_a_runway_goes_left_then_below_then_above():
    right = page.elems_geom(page.navaid_label_elems(GROUP[0], 'right', GROUP[1], GROUP[2]))
    left = page.elems_geom(page.navaid_label_elems(GROUP[0], 'left', GROUP[1], GROUP[2]))
    below = page.elems_geom(page.navaid_label_elems(GROUP[0], 'below', GROUP[1], GROUP[2]))
    assert positions([right])[0] == 'left'
    assert positions([right, left])[0] == 'below'
    assert positions([right, left], [below])[0] == 'above'
    pos, elems, free = positions([right, left, below, page.elems_geom(page.navaid_label_elems(GROUP[0], 'above', GROUP[1], GROUP[2]))])
    assert (pos, free) == ('right', False)


def test_navaid_left_label_is_right_aligned_2_3_mm_from_the_symbol():
    _, elems, _ = positions([page.elems_geom(page.navaid_label_elems(GROUP[0], 'right', GROUP[1], GROUP[2]))])
    assert f'x="{svgdoc.f(57.7)}"' in elems[0] and 'text-anchor="end"' in elems[0] and 'text-anchor="end"' in elems[1]


def test_navaid_label_outside_the_frame_goes_left_and_a_cap_counts_like_a_runway():
    pos, _, _ = positions(frame=(11.0, 38.1, 52.0, 131.0), group=(GROUP[0], [60.0], [100.0]))      # le cadre finit a x = 63
    assert pos == 'left'
    cap = page.elems_geom(page.navaid_label_elems(GROUP[0], 'right', GROUP[1], GROUP[2]))
    assert positions([], [cap])[0] == 'left'


# ---------- 2.3 graduations ----------
def lab(x, y, s='035-28', anchor='middle'):
    return page.GradLabel(x, y, s, anchor)


def test_graduation_leaving_the_frame_slides_along_its_edge_to_0_6_mm_inside():
    (x, y, reason), = page.place_graduations([lab(11.124, 166.7)], FRAME, {})
    w = text_width('035-28', 'regular', page.GRAD_SIZE)
    assert reason == 'glisse' and y == 166.7 and x - w / 2 == pytest.approx(FRAME[0] + 0.6)
    (x, y, reason), = page.place_graduations([lab(15.0, 166.7)], FRAME, {})          # a l'interieur : ne bouge pas
    assert (x, y, reason) == (15.0, 166.7, None)
    # bord gauche : le libelle glisse en y
    (x, y, reason), = page.place_graduations([lab(13.4, 38.5, '33-50', 'start')], FRAME, {})
    h = page.GRAD_SIZE * CAP_HEIGHT
    assert reason == 'glisse' and x == 13.4 and y - h == pytest.approx(FRAME[1] + 0.6)


def test_graduation_within_1_mm_of_an_obstacle_is_removed_beyond_it_stays():
    w = text_width('035-29', 'regular', page.GRAD_SIZE)
    x0 = 56.0 - w / 2                                                   # bord gauche du libelle
    for gap, removed in ((0.9, True), (1.1, False)):
        ob = {'encart': [box(x0 - gap - 5.0, 100.0, x0 - gap, 170.0)]}
        (x, y, reason), = page.place_graduations([lab(56.0, 150.0, '035-29')], FRAME, ob)
        assert (x is None) == removed and (reason == 'encart') == removed


def test_graduation_slides_before_the_obstacle_test():
    w = text_width('035-28', 'regular', page.GRAD_SIZE)
    # sans glissement le libelle est sous l'obstacle ; glisse a 0,6 mm du bord, il en est a plus de 1 mm : il reste
    ob = {'cap': [box(5.0, 160.0, 11.0 + 0.6 - 2.0, 170.0)]}
    (x, y, reason), = page.place_graduations([lab(11.124, 166.7)], FRAME, ob)
    assert x is not None and reason == 'glisse'


def test_engine_graticule_keeps_the_tick_when_the_label_is_removed():
    class D:
        def __init__(self):
            self.items = []

        def add(self, layer, s):
            self.items.append(s)
    view = page.View(0.1, 0.0, 0.0, 74.0, 103.6)
    frame = FRAME

    class Proj:
        def to_latlon(self, x, z):
            return 33.0 + x / 111000.0, 35.0 + z / 93000.0
    d = D()
    res = compose.graticule(d, view, frame, Proj(), {'encart': [box(0, 0, 200, 200)]})
    assert res and all(r[0] is None for r in res)
    assert any(i.startswith('<line') for i in d.items) and not any(i.startswith('<text') for i in d.items)
    d = D()
    compose.graticule(d, view, frame, Proj(), {})
    assert any(i.startswith('<text') for i in d.items)


# ---------- republish : page synthetique ----------
def synthetic_page(cap_dx=0.0, top_y=40.0):
    """Page 10-9 minimale : piste verticale a x = 60 (de y = 40 a 120), caps a leur place d'origine (l'un hors du cadre), une
    balise sur la piste, graduations dont une hors du cadre, nord, echelle."""
    doc = svgdoc.Doc()
    doc.add_clip('clip-plan', *FRAME)
    hw = 0.7
    doc.add('Runways', f'<polygon points="{hw + 60},{top_y} {hw + 60},120 {60 - hw},120 {60 - hw},{top_y}" fill="#1A1A1A"/>')
    for e in (end('180.0°', (60.0, top_y), 180.0), end('360.0°', (60.0, 120.0), 0.0)):
        cx, cy, rot = page.cap_original(e, S)
        doc.add('Runway bearings', page.cap_element(e.label, cx + cap_dx, cy, rot))
    # balise a 60, 80 : son libelle (a droite) est dans la piste
    for el in compose.navaid_symbol_elems('VOR', 60.0 - 6.0, 80.0):
        doc.add('Navaids', el)
    for el in page.navaid_label_elems(('VOR/DME KAD', '112.60'), 'right', [54.0], [80.0]):
        doc.add('Navaids', el)
    doc.add('Frame & grid', svgdoc.rect(*FRAME, 'none', svgdoc.INK, 0.35))
    for ln in (svgdoc.line(11.124, 169.1, 11.124, 167.3, svgdoc.INK, 0.3), svgdoc.line(51.7, 169.1, 51.7, 167.3, svgdoc.INK, 0.3)):
        doc.add('Frame & grid', ln)
        x = float(re.search(r'x1="([^"]+)"', ln).group(1))
        doc.add('Frame & grid', page.grad_element(lab(x, 166.7, '035-28' if x < 20 else '035-29')))
    for el in page.north_block(21.0, 156.6, 0.0, 3.0, 1985):
        doc.add('Frame & grid', el)
    sb, _ = page.scale_block(94.0, 158.6, S)
    for el in sb:
        doc.add('Frame & grid', el)
    return doc.render()


def test_retouch_labels_applies_the_engine_functions_to_a_published_page():
    svg = synthetic_page()
    new, moves, unplaced = republish.retouch_labels(svg, 'T_10-9')
    kinds = [(m[0], m[1]) for m in moves]
    assert ('Runway bearings', '180.0°') in kinds and ('Runway bearings', '360.0°') not in kinds       # seul le cap hors du cadre
    assert ('Navaids', 'VOR/DME KAD / 112.60') in kinds and ('Frame & grid', '035-28') in kinds
    assert any(m[1] == 'Navaids' and m[3].startswith('right') and m[4].startswith('left') for m in [('', *m) for m in moves])
    e = top_end()
    want = page.place_caps([e], S, FRAME, [], [], [])[0]
    assert page.cap_element('180.0°', *want[:3]) in new                                     # le meme element que le moteur
    assert page.navaid_label_elems(('VOR/DME KAD', '112.60'), 'left', [54.0], [80.0])[0] in new
    assert page.grad_element(lab(11.124, 166.7)) not in new and svgdoc.line(11.124, 169.1, 11.124, 167.3, svgdoc.INK, 0.3) in new


def test_retouch_labels_is_idempotent_on_navaids_and_graduations():
    new, moves, _ = republish.retouch_labels(synthetic_page(top_y=60.0), 'T_10-9')
    assert [m[1] for m in moves] == ['VOR/DME KAD / 112.60', '035-28']
    again, moves2, _ = republish.retouch_labels(new, 'T_10-9')
    assert again == new and moves2 == []


def test_retouch_labels_stops_when_the_recomputed_cap_position_differs_from_the_published_one():
    with pytest.raises(republish.RepublishError, match='place d.origine recalculee'):
        republish.retouch_labels(synthetic_page(cap_dx=0.05), 'T_10-9')
    republish.retouch_labels(synthetic_page(cap_dx=0.009), 'T_10-9')                        # 0,01 mm : accepte


def test_retouch_labels_stops_on_a_navaid_label_that_is_not_the_engines():
    svg = synthetic_page().replace('112.60', '112.6', 1)
    svg = svg.replace('x="56.3"', 'x="56.9"', 1)
    with pytest.raises(republish.RepublishError, match='libelle de balise'):
        republish.retouch_labels(svg, 'T_10-9')


def test_retouch_labels_leaves_a_page_without_labels_alone():
    svg = svgdoc.Doc().render()
    assert republish.retouch_labels(svg, 'T_10-9') == (svg, [], [])


def test_engine_and_republish_call_the_same_placement_functions():
    assert compose.place_caps is page.place_caps and compose.place_navaid_labels is page.place_navaid_labels
    assert compose.place_graduations is page.place_graduations
    assert republish.page.place_caps is page.place_caps and republish.page.place_navaid_labels is page.place_navaid_labels
    assert republish.page.place_graduations is page.place_graduations


# ---------- moteur et republish donnent la meme page (echantillons) ----------
def build(data, monkeypatch, identity):
    if identity:                       # le moteur d'avant le lot : place d'origine, libelles a droite, graduations en place
        monkeypatch.setattr(compose, 'place_caps', lambda ends, s, frame, *a, **k: [
            (*page.cap_original(e, s), page.cap_box(e.label, *page.cap_original(e, s)), False) for e in ends])
        monkeypatch.setattr(compose, 'place_navaid_labels', lambda groups, *a, **k: [
            ('right', page.navaid_label_elems(l, 'right', xs, ys), True) for l, xs, ys in groups])
        monkeypatch.setattr(compose, 'place_graduations', lambda labels, *a, **k: [(l.x, l.y, None) for l in labels])
    return compose.build_pages(data, log=lambda *a: None)[0].svg


@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_republish_of_the_old_engine_page_equals_the_new_engine_page(charts, monkeypatch, base):
    """Pour forcer des deplacements sur les trois bases, le cadre vu par le placement est reduit de 14 mm de chaque cote (meme
    reduction dans le moteur et dans republish) : les deux voies doivent donner les memes etiquettes aux memes places."""
    data = charts[base][0]
    real_caps, real_nav, real_grad = page.place_caps, page.place_navaid_labels, page.place_graduations
    shrink = lambda fr: (fr[0] + 14.0, fr[1] + 14.0, fr[2] - 28.0, fr[3] - 28.0)
    forced = []
    with monkeypatch.context() as m:
        m.setattr(compose, 'place_caps', lambda ends, s, frame, *a, **k: real_caps(ends, s, shrink(frame), *a, **k))
        m.setattr(compose, 'place_navaid_labels', lambda groups, frame, *a, **k: real_nav(groups, shrink(frame), *a, **k))
        m.setattr(compose, 'place_graduations', lambda labels, frame, *a, **k: real_grad(labels, shrink(frame), *a, **k))
        new_svg = compose.build_pages(data, log=lambda *a: None)[0].svg
    with monkeypatch.context() as m:
        build_old = build(data, m, True)
    with monkeypatch.context() as m:
        m.setattr(page, 'place_caps', lambda ends, s, frame, *a, **k: real_caps(ends, s, shrink(frame), *a, **k))
        m.setattr(page, 'place_navaid_labels', lambda groups, frame, *a, **k: real_nav(groups, shrink(frame), *a, **k))
        m.setattr(page, 'place_graduations', lambda labels, frame, *a, **k: real_grad(labels, shrink(frame), *a, **k))
        rep_svg, moves, _ = republish.retouch_labels(build_old, base + '_10-9')
    assert new_svg != build_old and moves                               # des etiquettes ont bien ete deplacees
    # memes elements, memes places (a 0,0015 mm pres : le moteur part de metres non arrondis, republish de millimetres arrondis)
    num = re.compile(r'-?\d+\.?\d*')
    a, b = new_svg.split('\n'), rep_svg.split('\n')
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert num.sub('#', x) == num.sub('#', y), (x, y)
        assert all(abs(float(p) - float(q)) <= 0.0015 for p, q in zip(num.findall(x), num.findall(y))), (x, y)


@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_republish_leaves_a_page_of_the_new_engine_alone(charts, base):
    svg = charts[base][1][0].svg
    new, moves, unplaced = republish.retouch_labels(svg, base + '_10-9')
    assert new == svg and moves == [] and unplaced == []
