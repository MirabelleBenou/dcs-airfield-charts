"""Lot ac-chevauchements-etiquettes : un libelle de balise evite les traits de graduation (page.place_navaid_labels, moteur,
republish) ; une etiquette de taxiway evite les balises (compose.taxiway_labels, layout.place_labels_passes)."""
import re
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from render import analysis, compose, layout, page, republish, svgdoc
from render.page import elem_geom, elems_geom, navaid_label_elems

FRAME = (11.0, 38.1, 126.0, 131.0)
FX, FY, FW, FH = FRAME
LINES = ('VOR/DME KAD', '112.60')


def old_place_navaid_labels(groups, frame, runways, caps):
    """`page.place_navaid_labels` de cc115f1, recopiee telle quelle (reference de la regle sans traits de graduation)."""
    fx, fy, fw, fh = frame
    inside = box(fx, fy, fx + fw, fy + fh)
    out = []
    for lines, xs, ys in groups:
        chosen = None
        for pos in page.NAVAID_POSITIONS:
            elems = navaid_label_elems(lines, pos, xs, ys)
            g = elems_geom(elems)
            if inside.contains(g) and not any(g.intersects(r) for r in runways) and not any(g.intersects(c) for c in caps):
                chosen = (pos, elems, True)
                break
        out.append(chosen or ('right', navaid_label_elems(lines, 'right', xs, ys), False))
    return out


def geom(pos, x, y):
    return elems_geom(navaid_label_elems(LINES, pos, [x], [y]))


def tick(x1, y1, x2, y2):
    return elem_geom(svgdoc.line(x1, y1, x2, y2, svgdoc.INK, 0.3))


def place(x, y, runways=(), caps=(), ticks=None):
    args = ([(LINES, [x], [y])], FRAME, list(runways), list(caps))
    return (page.place_navaid_labels(*args) if ticks is None else page.place_navaid_labels(*args, ticks))[0]


# ---------- P1 : graduation ----------
def test_top_tick_across_the_right_label_moves_it_to_the_left():
    x, y = 60.0, FY + 3.0                                   # le haut du libelle (FY + 1,4) est dans le trait de 1,8 mm
    g = geom('right', x, y)
    tx = (g.bounds[0] + g.bounds[2]) / 2
    t = tick(tx, FY, tx, FY + 1.8)
    assert t.intersects(g) and place(x, y)[0] == 'right'
    assert place(x, y, ticks=[t])[0] == 'left'


def test_right_edge_tick_across_the_right_label_moves_it():
    g0 = geom('right', 100.0, 90.0)
    w = g0.bounds[2] - 100.0
    x = FX + FW - 0.5 - w                                   # le libelle finit a 0,5 mm du bord droit, dans le cadre
    g = geom('right', x, 90.0)
    t = tick(FX + FW, 89.0, FX + FW - 1.8, 89.0)           # bord droit : un trait, pas de libelle
    assert t.intersects(g) and place(x, 90.0)[0] == 'right'
    assert place(x, 90.0, ticks=[t])[0] == 'left'


def test_left_edge_tick_across_the_left_label_moves_it_below():
    g0 = geom('left', 100.0, 90.0)
    w = 100.0 - g0.bounds[0]
    x = FX + 1.0 + w                                        # le libelle de gauche commence a 1 mm du bord gauche
    g = geom('left', x, 90.0)
    t = tick(FX, 89.0, FX + 1.8, 89.0)
    assert t.intersects(g)
    blocked = [geom('right', x, 90.0)]
    assert place(x, 90.0, runways=blocked)[0] == 'left'
    assert place(x, 90.0, runways=blocked, ticks=[t])[0] == 'below'


def test_every_position_on_a_tick_gives_the_old_fallback_not_free():
    x, y = 60.0, 90.0
    ticks = [elem_geom(svgdoc.line(*(a for a in box(*geom(p, x, y).bounds).exterior.coords[0]), *box(*geom(p, x, y).bounds).exterior.coords[2], svgdoc.INK, 0.3))
             for p in page.NAVAID_POSITIONS]
    pos, elems, free = place(x, y, ticks=ticks)
    assert pos == 'right' and not free and elems == navaid_label_elems(LINES, 'right', [x], [y])


def test_without_ticks_the_result_is_the_one_of_cc115f1():
    groups = [(LINES, [60.0], [100.0]), (('NDB AB', '300'), [FX + 2.0], [FY + 2.0]), (LINES, [FX + FW - 8.0], [90.0]),
              (LINES, [30.0, 33.0], [60.0, 61.0]), (LINES, [80.0], [FY + FH - 1.6])]
    run = [geom('right', 60.0, 100.0)]
    cap = [box(20, 55, 50, 65)]
    for r, c in ((), ()), (run, ()), (run, cap), ((), cap):
        assert page.place_navaid_labels(groups, FRAME, list(r), list(c)) == old_place_navaid_labels(groups, FRAME, list(r), list(c))
        assert page.place_navaid_labels(groups, FRAME, list(r), list(c), []) == old_place_navaid_labels(groups, FRAME, list(r), list(c))


def test_the_approach_page_call_does_not_pass_ticks():
    import inspect
    from render import approach
    src = inspect.getsource(approach)
    assert re.search(r'place_navaid_labels\(\[\(grp\[.lines.\], grp\[.xs.\], grp\[.ys.\]\)\], frame, obstacles, \[\]\)', src)


# ---------- P2 : republication ----------
def ticks_page(label_x, tick_x):
    doc = svgdoc.Doc()
    doc.add_clip('clip-plan', *FRAME)
    for el in compose.navaid_symbol_elems('VOR', label_x - 2.3 - 5.0, FY + 3.0):
        doc.add('Navaids', el)
    for el in navaid_label_elems(LINES, 'right', [label_x - 7.3], [FY + 3.0]):
        doc.add('Navaids', el)
    doc.add('Frame & grid', svgdoc.rect(*FRAME, 'none', svgdoc.INK, 0.35))
    doc.add('Frame & grid', svgdoc.line(tick_x, FY, tick_x, FY + 1.8, svgdoc.INK, 0.3))
    doc.add('Frame & grid', svgdoc.line(20.0, 120.0, 20.0, 121.8, svgdoc.INK, 0.3))         # ni sur un bord, ni de 1,8 mm sur un bord
    return doc.render()


def test_republish_moves_a_navaid_label_off_a_tick():
    svg = ticks_page(label_x=70.0, tick_x=72.0)
    new, moves, unplaced = republish.retouch_labels(svg, 'T_10-9')
    assert [m[0] for m in moves] == ['Navaids'] and moves[0][2].startswith('right') and moves[0][3].startswith('left') and unplaced == []
    again, moves2, _ = republish.retouch_labels(new, 'T_10-9')
    assert again == new and moves2 == []                      # republier une page republiee ne change rien


def test_republish_leaves_a_page_without_the_overlap_unchanged():
    svg = ticks_page(label_x=70.0, tick_x=120.0)
    new, moves, unplaced = republish.retouch_labels(svg, 'T_10-9')
    assert new == svg and moves == [] and unplaced == []


def test_republish_reads_only_the_edge_ticks():
    svg = ticks_page(label_x=70.0, tick_x=72.0)
    fg = republish._layer_lines(svg, 'Frame & grid')
    assert len(republish._read_ticks(fg, FRAME)) == 1


# ---------- P3 : balises ----------
def hline(y, x0=10.0, x1=110.0):
    return np.array([[x0, y], [x1, y]])


BOUNDS = (0.0, 0.0, 148.0, 210.0)


def wh(nm, k=1.0):
    return (4.0 * k, 2.6 * k)


def test_taxiway_label_over_a_navaid_label_takes_the_next_free_place():
    ways = [('A', hline(50.0), 100.0)]
    out, info = layout.place_labels_passes(ways, wh, [], np.empty((0, 2)), 0.6, BOUNDS)
    cx, cy = out['A']
    assert (cx, cy) == (60.0, 50.0) and info['A'][0] == 1
    nav = [box(cx - 1.0, cy - 0.5, cx + 6.0, cy + 1.0)]             # libelle de balise sur la place de la passe 1
    out2, info2 = layout.place_labels_passes(ways, wh, nav, np.empty((0, 2)), 0.6, BOUNDS)
    assert out2['A'] != out['A'] and info2['A'][0] == 1
    assert not box(out2['A'][0] - 2, out2['A'][1] - 1.3, out2['A'][0] + 2, out2['A'][1] + 1.3).intersects(nav[0])


def test_a_leader_label_does_not_run_its_leader_over_a_navaid():
    from shapely.geometry import LineString
    ways = [('A', hline(50.0), 100.0)]
    fr = set(layout.LABEL_FRACTIONS) | set(layout.LABEL_FRACTIONS_FINE)
    # une cale au-dessus de chaque place des passes 1 a 3 (hors du point d'attache) : seule la passe 4 pose le nom
    wedges = [(10.0 + 100.0 * fr_ - 3.0, 48.0, 10.0 + 100.0 * fr_ + 3.0, 49.5) for fr_ in fr]
    out, info = layout.place_labels_passes(ways, wh, wedges, np.empty((0, 2)), 0.6, BOUNDS)
    assert info['A'][0] == 4
    (ax, ay), (ex, ey) = info['A'][2], info['A'][3]
    sym = box((ax + ex) / 2 - 0.2, (ay + ey) / 2 - 0.2, (ax + ex) / 2 + 0.2, (ay + ey) / 2 + 0.2)      # une balise sur le trait
    out2, info2 = layout.place_labels_passes(ways, wh, wedges + [sym], np.empty((0, 2)), 0.6, BOUNDS)
    assert info2['A'][0] == 4 and info2['A'][2:4] != info['A'][2:4]
    assert not LineString([info2['A'][2], info2['A'][3]]).intersects(sym)
    assert not box(out2['A'][0] - 2, out2['A'][1] - 1.3, out2['A'][0] + 2, out2['A'][1] + 1.3).intersects(sym)


class FakeView:
    def pts(self, xz):
        return np.asarray(xz, float)


def test_compose_taxiway_labels_avoids_the_navaid_geometries_it_is_given():
    data = SimpleNamespace(osm_ways=[{'ref': 'H', 'pts_xz': hline(50.0, 20, 100), 'length': 80.0}], runways=[], osm_kept=['H'])
    frame = (10.0, 10.0, 100.0, 150.0)
    corners = ((0, 0, 1, 1), (0, 0, 1, 1))
    base, _ = compose.taxiway_labels(svgdoc.Doc(), data, FakeView(), frame, corners, [], np.empty((0, 2)), lambda m: None)
    cx, cy = base['H']
    nav = [elems_geom(navaid_label_elems(('TACAN PHA', '79X'), 'right', [cx - 6.0], [cy])), box(cx - 1.0, cy - 1.0, cx + 1.0, cy + 1.0)]
    placed, rects = compose.taxiway_labels(svgdoc.Doc(), data, FakeView(), frame, corners, list(nav), np.empty((0, 2)), lambda m: None)
    assert 'H' in placed and placed['H'] != base['H']
    assert not any(elem_geom(r).intersects(n) for r in rects for n in nav)


# ---------- P4, P5 : Konya et Paphos (echantillons) ----------
def build(work, base):
    data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
    return compose.build_pages(data, log=lambda *a: None)


def layer_lines(svg, label):
    return [ln for _, _, ln in republish._layer_lines(svg, label)]


def test_konya_vor_dme_label_touches_no_tick(work):
    svg = build(work, 'Konya')[0].svg
    frame = republish._clip_frame(svg, 'K')
    ticks = republish._read_ticks(republish._layer_lines(svg, 'Frame & grid'), frame)
    assert len(ticks) >= 9
    texts = [ln for ln in layer_lines(svg, 'Navaids') if ln.startswith('<text')]
    assert any('VOR/DME KNY' in t for t in texts)
    assert not any(elem_geom(t).intersects(k) for t in texts for k in ticks)
    assert republish.retouch_labels(svg, 'K_10-9')[1:] == ([], [])       # republier ne bouge plus rien


def test_paphos_h_label_touches_no_navaid(work):
    pages = build(work, 'Paphos')
    svg = pages[0].svg
    nav = [elem_geom(ln) for ln in layer_lines(svg, 'Navaids')]
    assert nav
    rects = [elem_geom(ln) for ln in layer_lines(svg, 'Taxiway labels') if ln.startswith(('<rect', '<line', '<circle'))]
    assert not any(r.intersects(n) for r in rects for n in nav)
    names = sorted(re.search(r'>([^<]*)</text>', ln).group(1) for ln in layer_lines(svg, 'Taxiway labels') if ln.startswith('<text'))
    assert names == ['A', 'B', 'C', 'D', 'E', 'G', 'H', 'J', 'K', 'M', 'U', 'V', 'W', 'Y']
