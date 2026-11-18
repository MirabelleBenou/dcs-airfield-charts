"""Lot noms-sans-place : points d'attache candidats des etiquettes deportees (layout.anchor_candidates, passe 4)."""
import math

import numpy as np
import pytest
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, box

from render import compose, dcsdata, layout
from render.svgdoc import Doc

from test_taxiway_labels import BOUNDS, FRAME, FakeView, fake_data, hline, wh  # noqa: E402


# ---------- reference : la passe 4 de abe78b8 (un seul point d'attache), recopiee telle quelle ----------
def legacy_passes(ways, box_wh, forbidden, obstacles_pts, pt_pad=0.6, bounds=None, to_place=None):
    by_name, lines = {}, {}
    for nm, pts, L in ways:
        lines.setdefault(nm, []).append((pts, L))
        if nm not in by_name or L > by_name[nm][1]:
            by_name[nm] = (pts, L)
    placed, out, info, leaders = [], {}, {}, []
    tree = cKDTree(np.asarray(obstacles_pts, float)) if len(obstacles_pts) else None

    def free(rect, cx, cy, w, h):
        if bounds is not None and not (rect[0] >= bounds[0] and rect[1] >= bounds[1] and rect[2] <= bounds[2] and rect[3] <= bounds[3]):
            return False
        if any(layout.rects_overlap(rect, r) for r in placed) or any(layout._hits(rect, r) for r in forbidden):
            return False
        if leaders and any(layout._hits(rect, ln) for ln in leaders):
            return False
        if tree is not None:
            grown = (rect[0] - pt_pad, rect[1] - pt_pad, rect[2] + pt_pad, rect[3] + pt_pad)
            idx = tree.query_ball_point([cx, cy], math.hypot(w, h) / 2 + pt_pad + 0.1)
            if any(grown[0] <= obstacles_pts[j][0] <= grown[2] and grown[1] <= obstacles_pts[j][1] <= grown[3] for j in idx):
                return False
        return True

    def attempt(nm, candidates, k, npass):
        w, h = box_wh(nm, k)
        for pts, fr_list in candidates:
            for fr in fr_list:
                cx, cy = layout.polyline_point(pts, fr)
                rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if free(rect, cx, cy, w, h):
                    placed.append(rect)
                    out[nm] = (cx, cy)
                    info[nm] = (npass, k)
                    return True
        return False

    names = sorted(by_name, key=dcsdata.natural_key)
    for nm in names:
        attempt(nm, [(by_name[nm][0], layout.LABEL_FRACTIONS)], 1.0, 1)
    todo = [nm for nm in names if to_place is None or nm in to_place]
    for npass, k in ((2, 1.0), (3, layout.LABEL_SMALL)):
        for nm in todo:
            if nm in out:
                continue
            ordered = sorted(lines[nm], key=lambda t: -t[1])                  # tri stable : egalite dans l'ordre de `ways`
            attempt(nm, [(pts, layout.LABEL_FRACTIONS_FINE) for pts, _ in ordered], k, npass)
    # passe 4 : etiquette deportee, reliee par un trait au point d'attache (fraction 0,5 de la plus longue ligne du nom)
    from shapely.geometry import LineString, Point
    for nm in todo:
        if nm in out:
            continue
        anchor = legacy_anchor(by_name[nm][0], lines[nm], bounds)
        if anchor is None:
            continue
        ax, ay = anchor
        for k in (1.0, layout.LABEL_SMALL):
            w, h = box_wh(nm, k)
            done = False
            for dist in layout.LABEL_LEADER_DIST:
                for d in range(16):
                    th = math.radians(d * 22.5)
                    cx, cy = ax + dist * math.sin(th), ay - dist * math.cos(th)
                    rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                    if not free(rect, cx, cy, w, h):
                        continue
                    if rect[0] - 0.25 <= ax <= rect[2] + 0.25 and rect[1] - 0.25 <= ay <= rect[3] + 0.25:
                        continue                                    # pas de trait de longueur nulle
                    ex, ey = min(max(ax, rect[0]), rect[2]), min(max(ay, rect[1]), rect[3])      # point du bord le plus proche
                    seg = LineString([(ax, ay), (ex, ey)])
                    if any(legacy_seg_hits(seg, r) for r in placed) or any(legacy_seg_hits(seg, r) for r in forbidden) \
                            or any(seg.intersects(ln) for ln in leaders):
                        continue
                    placed.append(rect)
                    leaders.append(seg.buffer(0.1))
                    leaders.append(Point(ax, ay).buffer(0.25))
                    out[nm] = (cx, cy)
                    info[nm] = (4, k, (ax, ay), (ex, ey), dist, d * 22.5)
                    done = True
                    break
                if done:
                    break
            if done:
                break
    return out, info


def legacy_anchor(pts, lines_of_name, bounds):
    from shapely.geometry import LineString, Point, box as sbox
    from shapely.ops import nearest_points
    ax, ay = layout.polyline_point(pts, 0.5)
    if bounds is None or (bounds[0] <= ax <= bounds[2] and bounds[1] <= ay <= bounds[3]):
        return float(ax), float(ay)
    ctr = Point((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    frame = sbox(*bounds)
    for q, _ in sorted(lines_of_name, key=lambda t: -t[1]):         # tri stable : egalite dans l'ordre de `ways`
        q = np.asarray(q, float)
        g = Point(q[0]) if len(q) == 1 else LineString(q)
        g = g.intersection(frame)
        if not g.is_empty:
            n = nearest_points(g, ctr)[0]
            return float(n.x), float(n.y)
    return None


def legacy_seg_hits(seg, other):
    if isinstance(other, tuple):
        from shapely.geometry import box
        return seg.intersects(box(*other))
    return seg.intersects(other)

# ---------- scenarios ----------
def dense(y, x0, x1, step=1.5):
    return np.array([[x, y] for x in np.arange(x0, x1 + 1e-9, step)])        # aucun emplacement libre le long de la ligne : rien aux passes 1 a 3


LONG = [('A', hline(100.0, 30.0, 130.0), 100.0)]
PARKS = dense(100.0, 30.0, 130.0)
MID = box(70.0, 99.0, 90.0, 101.0)                                         # contient le milieu (80 ; 100)
NOBOX = ((0, 0, 1, 1), (0, 0, 1, 1))


def run(ways, forbidden=(), parks=PARKS):
    return layout.place_labels_passes(ways, wh, list(forbidden), np.asarray(parks, float), 0.6, BOUNDS)


def run_legacy(ways, forbidden=(), parks=PARKS):
    return legacy_passes(ways, wh, list(forbidden), np.asarray(parks, float), 0.6, BOUNDS)


# ---------- P1 : l'ordre ----------
@pytest.mark.parametrize('forb', [[], [box(0.0, 0.0, 148.0, 90.0)], [box(80.5, 96.0, 82.0, 99.0)], [box(75.0, 100.5, 85.0, 103.0)]])
def test_today_anchor_free_is_tried_first_and_result_is_identical_to_legacy(forb):
    placed, info = run(LONG, forb)
    old_placed, old_info = run_legacy(LONG, forb)
    assert old_info['A'][0] == 4 and placed == old_placed
    assert info['A'][:6] == old_info['A'] and info['A'][6] is False
    assert info['A'][2] == pytest.approx((80.0, 100.0))


def test_identical_to_legacy_on_several_names_when_every_today_anchor_is_free():
    ways = [('A', hline(100.0, 30.0, 130.0), 100.0), ('B', hline(60.0, 30.0, 130.0), 100.0), ('C', hline(150.0, 30.0, 130.0), 100.0)]
    parks = np.vstack([dense(100.0, 30.0, 130.0), dense(60.0, 30.0, 130.0), dense(150.0, 30.0, 130.0)])
    placed, info = run(ways, [], parks)
    old_placed, old_info = run_legacy(ways, [], parks)
    assert len(old_info) == 3 and all(v[0] == 4 for v in old_info.values())
    assert placed == old_placed and {k: v[:6] for k, v in info.items()} == old_info


def test_today_anchor_in_a_forbidden_zone_is_not_tried_next_candidate_succeeds():
    assert run_legacy(LONG, [MID]) == ({}, {})                              # abe78b8 : tout trait part de la zone, rien
    placed, info = run(LONG, [MID])
    assert info['A'][0] == 4 and info['A'][6] is True
    assert info['A'][2] == pytest.approx((55.0, 100.0))                     # fraction 0,25 de la plus longue ligne
    assert not LineString([info['A'][2], info['A'][3]]).intersects(MID)


def test_anchor_candidates_order_fractions_longest_line_first():
    long_ = hline(100.0, 30.0, 130.0)
    short = hline(120.0, 30.0, 50.0)
    lines = [(short, 20.0), (long_, 100.0)]
    assert layout.ANCHOR_FRACTIONS == (0.5, 0.25, 0.75, 0.1, 0.9)
    c = layout.anchor_candidates(long_, lines, BOUNDS, [])
    xy = [p for _, p in c]
    assert c[0][0] is False and all(m is True for m, _ in c[1:])
    assert xy[0] == pytest.approx((80.0, 100.0))                           # _anchor : milieu de la plus longue ligne
    assert [(round(p[0], 6), p[1]) for p in xy[1:5]] == [(55.0, 100.0), (105.0, 100.0), (40.0, 100.0), (120.0, 100.0)]
    assert [(round(p[0], 6), p[1]) for p in xy[5:]] == [(40.0, 120.0), (35.0, 120.0), (45.0, 120.0), (32.0, 120.0), (48.0, 120.0)]


def test_candidates_out_of_bounds_not_free_or_within_half_mm_are_dropped():
    long_ = hline(100.0, -50.0, 150.0)                                       # 0,1 = -30 ; 0,25 = 0 ; milieu 50 ; 0,75 = 100 ; 0,9 = 130
    c = [p for _, p in layout.anchor_candidates(long_, [(long_, 200.0)], (10.0, 0.0, 120.0, 210.0), [])]
    assert [round(p[0], 6) for p in c] == [50.0, 100.0]                      # 0,25 (x = 0), 0,1 et 0,9 sont hors du cadre
    c = [p for _, p in layout.anchor_candidates(long_, [(long_, 200.0)], BOUNDS, [box(95.0, 99.0, 105.0, 101.0)])]
    assert 100.0 not in [round(p[0], 6) for p in c] and 50.0 in [round(p[0], 6) for p in c]      # non libre : ecarte
    near = hline(100.0, 50.0, 51.0)                                          # 1 mm : tous les autres points sont a 0,5 mm ou moins du milieu
    c = [p for _, p in layout.anchor_candidates(near, [(near, 1.0)], BOUNDS, [])]
    assert [round(p[0], 6) for p in c] == [50.5]


def test_candidate_at_exactly_half_mm_is_dropped_and_beyond_is_kept():
    line = hline(100.0, 50.0, 52.0)                                          # 2 mm : milieu 51 ; 0,25 = 50,5 (a 0,5 mm : ecarte) ; 0,75 = 51,5 (idem)
    c = [round(p[0], 6) for _, p in layout.anchor_candidates(line, [(line, 2.0)], BOUNDS, [])]
    assert c[0] == 51.0 and 50.5 not in c and 51.5 not in c
    assert c == [51.0, 50.2, 51.8]                                           # 0,1 (50,2) et 0,9 (51,8) : a 0,8 mm du milieu


def test_free_means_a_quarter_mm_square_touching_no_forbidden_zone():
    line = hline(100.0, 30.0, 130.0)
    z = box(79.7, 99.0, 79.8, 101.0)                                          # a 0,2 mm du milieu : le carre de 0,25 mm le touche
    assert layout.anchor_candidates(line, [(line, 100.0)], BOUNDS, [z])[0][0] is True
    z = box(79.7, 99.0, 79.74, 101.0)                                         # a 0,26 mm : libre
    first = layout.anchor_candidates(line, [(line, 100.0)], BOUNDS, [z])[0]
    assert first[0] is False and first[1] == pytest.approx((80.0, 100.0))
    assert layout.anchor_candidates(line, [(line, 100.0)], BOUNDS, [(79.0, 99.0, 79.7, 101.0)])[0][0] is False        # rectangle (tuple)
    assert layout.anchor_candidates(line, [(line, 100.0)], BOUNDS, [(79.0, 99.0, 79.8, 101.0)])[0][0] is True


def test_whole_line_in_forbidden_zone_no_candidate_name_stays_absent():
    zone = box(25.0, 95.0, 135.0, 105.0)
    assert layout.anchor_candidates(LONG[0][1], [(LONG[0][1], 100.0)], BOUNDS, [zone]) == []
    assert run(LONG, [zone]) == ({}, {}) == run_legacy(LONG, [zone])
    doc, lines = Doc(), []
    placed, _ = compose.taxiway_labels(doc, fake_data([('A', hline(50.0, 20.0, 80.0), 60.0)]), FakeView(), FRAME, NOBOX,
                                       [box(15.0, 45.0, 85.0, 55.0)], dense(50.0, 20.0, 80.0), lines.append)
    assert placed == {} and f'{compose.LOG_ERROR_LABEL} : A' in lines


def test_no_candidate_succeeds_name_absent_as_today():
    full = [box(0.0, 0.0, 148.0, 210.0).difference(box(54.0, 99.0, 56.0, 101.0))]        # un seul point libre, mais aucune place nulle part
    assert run(LONG, full) == ({}, {}) == run_legacy(LONG, full)


# ---------- P2 : le journal ----------
def log_of(forbidden):
    doc, lines = Doc(), []
    ways = [('A', hline(50.0, 20.0, 80.0), 60.0)]
    compose.taxiway_labels(doc, fake_data(ways), FakeView(), FRAME, NOBOX, forbidden, dense(50.0, 20.0, 80.0), lines.append)
    return lines


def test_log_line_marks_a_moved_attachment_point_and_only_then():
    lines = log_of([])
    assert 'etiquette deportee : A a 3 mm, 6 pt' in lines
    assert not any("point d'attache deplace" in m for m in lines)
    lines = log_of([(45.0, 49.0, 55.0, 51.0)])                                # le milieu (50 ; 50) n'est pas libre
    assert "etiquette deportee : A a 3 mm, 6 pt (point d'attache deplace)" in lines
    assert not any(compose.LOG_ERROR_LABEL in m for m in lines)
