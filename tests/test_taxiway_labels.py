"""Lot ac-taxiways-etiquettes : etiquettes de taxiways en trois passes (layout.place_labels_passes), journal de
compose.taxiway_labels, code de sortie 3 de build_chart.py quand un nom reste sans place."""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial import cKDTree

from render import analysis, build_chart, compose, dcsdata, layout
from render.fonts import pt, text_width
from render.page import PageOut
from render.svgdoc import f
from render.svgdoc import Doc

OLD_FRACTIONS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88, 0.05, 0.95)
BOUNDS = (0.0, 0.0, 148.0, 210.0)
W, H = 4.0, 2.6


def legacy_place_labels(ways, box_wh, forbidden, obstacles_pts, pt_pad=0.6, bounds=None):
    """`layout.place_labels` de 66ff09b, recopiee telle quelle (reference de la passe 1)."""
    by_name = {}
    for nm, pts, L in ways:
        if nm not in by_name or L > by_name[nm][1]:
            by_name[nm] = (pts, L)
    placed, out = [], {}
    tree = cKDTree(np.asarray(obstacles_pts, float)) if len(obstacles_pts) else None
    for nm in sorted(by_name, key=dcsdata.natural_key):
        pts = by_name[nm][0]
        w, h = box_wh(nm)
        for fr in OLD_FRACTIONS:
            cx, cy = layout.polyline_point(pts, fr)
            rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            if bounds is not None and not (rect[0] >= bounds[0] and rect[1] >= bounds[1] and rect[2] <= bounds[2] and rect[3] <= bounds[3]):
                continue
            if any(layout.rects_overlap(rect, r) for r in placed) or any(layout._hits(rect, r) for r in forbidden):
                continue
            if tree is not None:
                grown = (rect[0] - pt_pad, rect[1] - pt_pad, rect[2] + pt_pad, rect[3] + pt_pad)
                idx = tree.query_ball_point([cx, cy], math.hypot(w, h) / 2 + pt_pad + 0.1)
                if any(grown[0] <= obstacles_pts[j][0] <= grown[2] and grown[1] <= obstacles_pts[j][1] <= grown[3] for j in idx):
                    continue
            placed.append(rect)
            out[nm] = (cx, cy)
            break
    return out


def hline(y, x0=10.0, x1=110.0):
    return np.array([[x0, y], [x1, y]])


def wh(nm, k=1.0):
    return (W * k, H * k)


def rect_of(c, k=1.0):
    return (c[0] - W * k / 2, c[1] - H * k / 2, c[0] + W * k / 2, c[1] + H * k / 2)


def run(ways, forbidden=(), parks=None):
    parks = np.empty((0, 2)) if parks is None else np.asarray(parks, float)
    return layout.place_labels_passes(ways, wh, list(forbidden), parks, 0.6, BOUNDS)


# ---------- P1 : passe 1 inchangee ----------
def test_pass1_equals_legacy_when_everything_fits():
    ways = [('A', hline(50.0), 100.0), ('B', hline(60.0), 100.0), ('C', np.array([[60.0, 10.0], [60.0, 90.0]]), 80.0),
            ('A1', hline(70.0), 100.0), ('A2', hline(72.0), 100.0), ('A', hline(120.0, 10, 20), 10.0)]
    forb = [(55.0, 45.0, 65.0, 56.0)]
    parks = np.array([[35.0, 50.0], [85.0, 50.0]])
    old = legacy_place_labels(ways, lambda nm: (W, H), forb, parks, 0.6, BOUNDS)
    new, info = run(ways, forb, parks)
    assert len(old) == 5 and new == old
    assert all(v == (1, 1.0) for v in info.values())


def test_pass1_labels_unchanged_when_a_name_is_missing():
    # A et B sont posees par l'ancien algorithme ; C perd sa plus longue ligne (bande interdite) et n'a de place que sur la courte
    ways = [('A', hline(120.0), 100.0), ('B', hline(130.0), 100.0), ('C', hline(50.0, 20.0, 100.0), 80.0),
            ('C', hline(170.0, 20.0, 40.0), 20.0)]
    forb = [(0.0, 45.0, 148.0, 56.0)]
    old = legacy_place_labels(ways, lambda nm: (W, H), forb, np.empty((0, 2)), 0.6, BOUNDS)
    new, info = run(ways, forb)
    assert len(old) >= 2 and 'C' not in old
    for nm, c in old.items():
        assert new[nm] == c and info[nm] == (1, 1.0)
    assert info['C'][0] in (2, 3, 4) and new['C'][1] == pytest.approx(170.0)


# ---------- P2 : passe 2 ----------
def test_pass2_uses_a_shorter_line_when_the_longest_is_blocked():
    ways = [('A', hline(50.0), 100.0), ('A', hline(100.0, 20, 60), 40.0)]
    placed, info = run(ways, [(0.0, 40.0, 148.0, 60.0)])
    assert info['A'] == (2, 1.0)
    assert placed['A'][1] == pytest.approx(100.0) and placed['A'][0] == pytest.approx(40.0)


def test_pass2_tries_the_longest_line_first_and_ties_in_ways_order():
    ways = [('A', hline(50.0), 100.0), ('A', hline(100.0, 20, 60), 40.0), ('A', hline(120.0, 20, 60), 40.0), ('A', hline(140.0), 90.0)]
    placed, _ = run(ways, [(0.0, 40.0, 148.0, 60.0)])
    assert placed['A'][1] == pytest.approx(140.0)                      # la plus longue libre, avant les deux de 40
    placed, _ = run(ways[:3], [(0.0, 40.0, 148.0, 60.0)])
    assert placed['A'][1] == pytest.approx(100.0)                      # egalite : l'ordre de `ways`


def test_pass2_finer_fraction_only_0_25_is_free():
    # ligne de x=10 a x=110 : 0,25 -> x=35 ; tout le reste est interdit
    forb = [(0.0, 40.0, 33.0, 60.0), (37.5, 40.0, 148.0, 60.0)]
    placed, info = run([('A', hline(50.0), 100.0)], forb)
    assert info['A'] == (2, 1.0) and placed['A'][0] == pytest.approx(35.0)


def test_pass2_label_avoids_pass1_labels():
    # A (passe 1) pose son etiquette en x=55 ; B n'a aucune place en passe 1 (zones interdites et points de parking), et en passe 2
    # sa fraction 0,45 (x=55) est prise par A : elle passe a 0,55 (x=65)
    ways = [('A', hline(80.0, 53.0, 57.0), 4.0), ('B', hline(80.0, 10.0, 110.0), 100.0)]
    forb = [(0.0, 70.0, 44.0, 90.0), (76.0, 70.0, 148.0, 90.0)]
    parks = [(60.0, 80.0), (50.0, 80.0), (70.0, 80.0)]
    placed, info = run(ways, forb, parks)
    assert info['A'] == (1, 1.0) and info['B'] == (2, 1.0)
    assert placed['A'][0] == pytest.approx(55.0) and placed['B'][0] == pytest.approx(65.0)
    assert not layout.rects_overlap(rect_of(placed['A']), rect_of(placed['B']))


def test_pass2_label_added_never_overlaps_earlier_ones_and_does_not_move_them():
    ways = [('A', hline(50.0), 100.0), ('A', hline(100.0, 20.0, 60.0), 40.0), ('B', hline(100.0, 20.0, 60.0), 40.0)]
    forb = [(0.0, 40.0, 148.0, 60.0)]                                  # A perd sa plus longue ligne : passe 2, sur la ligne de B
    old = legacy_place_labels(ways, lambda nm: (W, H), forb, np.empty((0, 2)), 0.6, BOUNDS)
    placed, info = run(ways, forb)
    assert set(old) == {'B'} and placed['B'] == old['B'] and info['B'] == (1, 1.0)
    assert info['A'] == (2, 1.0)
    assert not layout.rects_overlap(rect_of(placed['A']), rect_of(placed['B']))


# ---------- P3 : passe 3 ----------
def test_pass3_small_label_when_only_5pt_fits():
    forb = [(0.0, 0.0, 148.0, 48.9), (0.0, 51.1, 148.0, 210.0)]       # bande libre de 2,2 mm : 2,6 ne tient pas, 2,17 oui
    placed, info = run([('A', hline(50.0), 100.0)], forb)
    assert info['A'] == (3, layout.LABEL_SMALL)
    r = rect_of(placed['A'], info['A'][1])
    assert r[3] - r[1] == pytest.approx(2.6 * 5 / 6)


def test_pass3_not_used_when_nothing_fits_at_all():
    placed, info = run([('A', hline(50.0), 100.0)], [(0.0, 0.0, 148.0, 210.0)])
    assert placed == {} and info == {}


# ---------- compose.taxiway_labels : style a 5 pt, journal ----------
class FakeView:
    def pts(self, xz):
        return np.asarray(xz, float)


def fake_data(ways, kept=None):
    return SimpleNamespace(osm_ways=[{'ref': n, 'pts_xz': p, 'length': L} for n, p, L in ways], runways=[],
                           osm_kept=kept or sorted({n for n, _, _ in ways}))


FRAME = (10.0, 10.0, 100.0, 150.0)


def labels(ways, forbidden=()):
    doc, lines = Doc(), []
    data = fake_data(ways)
    placed, rects = compose.taxiway_labels(doc, data, FakeView(), FRAME, ((0, 0, 1, 1), (0, 0, 1, 1)), list(forbidden), np.empty((0, 2)),
                                           lines.append)
    return doc, lines, placed, rects


def test_taxiway_labels_5pt_style_and_log_lines():
    forb = [(0.0, 0.0, 148.0, 48.65), (0.0, 51.35, 148.0, 210.0)]       # bande libre de 2,7 mm : 3,0 ne tient pas, 2,5 oui
    doc, lines, placed, rects = labels([('A', hline(50.0, 20, 100), 80.0)], forb)
    assert 'etiquettes placees : 1/1' in lines and 'etiquettes : passe 1 0, passe 2 0, passe 3 1, deportees 0' in lines
    assert not any('ERREUR' in m for m in lines)
    rect_svg, text_svg = doc.layers['Taxiway labels']
    size = pt(5.0)
    assert 'height="2.5"' in rect_svg and 'fill="#FFCC00"' in rect_svg and 'stroke-width="0.2"' in rect_svg
    assert f'font-size="{f(size)}"' in text_svg and 'font-weight="bold"' in text_svg and 'text-anchor="middle"' in text_svg
    w = text_width('A', 'bold', size) + compose.LABEL_PAD * 5 / 6
    assert f'width="{f(w)}"' in rect_svg
    assert placed['A'][1] == pytest.approx(50.0)


def test_name_outside_the_frame_is_logged_without_error():
    ways = [('A', hline(50.0, 20, 100), 80.0), ('Z', hline(300.0, 20, 100), 80.0)]
    doc, lines, placed, _ = labels(ways)
    assert 'etiquette hors du cadre : Z' in lines
    assert 'etiquettes placees : 1/1' in lines
    assert not any('ERREUR' in m for m in lines) and set(placed) == {'A'}


def test_a_line_crossing_the_frame_without_a_vertex_inside_keeps_its_pass_1_label():
    # sonde de la relecture : la ligne X de (-50 ; 60) a (200 ; 60), cadre (10, 10, 100, 150) : posee en (75 ; 60) comme en 66ff09b
    X = np.array([[-50.0, 60.0], [200.0, 60.0]])
    doc, lines, placed, _ = labels([('X', X, 250.0)])
    assert placed['X'] == pytest.approx((75.0, 60.0))
    assert 'etiquettes placees : 1/1' in lines and not any('hors du cadre' in m or 'ERREUR' in m for m in lines)


def test_name_without_room_logs_an_error_and_the_page_is_written():
    ways = [('A', hline(50.0, 20, 100), 80.0), ('B', hline(80.0, 20, 100), 80.0)]
    doc, lines, placed, _ = labels(ways, [(0.0, 70.0, 148.0, 90.0)])
    assert 'ERREUR etiquette de taxiway sans place : B' in lines
    assert 'etiquettes placees : 1/2' in lines and set(placed) == {'A'}
    assert doc.render()


def test_error_line_is_prefixed_on_the_vicinity_page():
    ways = [('B', hline(80.0, 20, 100), 80.0)]
    lines = []
    logp = lambda m: lines.append(f'10-8 {m}')
    compose.taxiway_labels(Doc(), fake_data(ways), FakeView(), FRAME, ((0, 0, 1, 1), (0, 0, 1, 1)), [(0.0, 70.0, 148.0, 90.0)],
                           np.empty((0, 2)), logp)
    assert '10-8 ERREUR etiquette de taxiway sans place : B' in lines


# ---------- P4 : code de sortie de build_chart ----------
def run_main(monkeypatch, tmp_path, page_logs):
    page = PageOut('10-9', '<svg/>', {})
    monkeypatch.setattr(build_chart.capture_config, 'load_config',
                        lambda argv: (SimpleNamespace(annee=1985, osm_dir=str(tmp_path), work_dir=str(tmp_path)), argv))
    monkeypatch.setattr(build_chart.analysis, 'analyze', lambda *a, **k: SimpleNamespace(icao='XXXX'))

    def build_pages(data, log=print):
        for m in page_logs:
            log(m)
        return [page]
    monkeypatch.setattr(build_chart.compose, 'build_pages', build_pages)
    monkeypatch.setattr(build_chart.vicinity, 'context_folder', lambda *a: None)
    written = []

    def export_page(svg, stem):
        written.append(stem)
        return tmp_path / 'a.svg', tmp_path / 'a.pdf', tmp_path / 'a.png'
    monkeypatch.setattr(build_chart.export, 'export_page', export_page)
    monkeypatch.setattr(build_chart.export, 'merge_pdfs', lambda pdfs, out: written.append(out))
    rc = build_chart.main(['build_chart.py', 'Syria', 'Base', '--out', str(tmp_path)])
    return rc, written


def test_main_returns_3_after_writing_everything_when_a_name_has_no_room(monkeypatch, tmp_path, capsys):
    rc, written = run_main(monkeypatch, tmp_path, ['etiquettes placees : 1/2', 'ERREUR etiquette de taxiway sans place : B'])
    assert rc == 3 and len(written) == 2                              # la page et le recueil sont ecrits
    assert 'ERREUR etiquette de taxiway sans place : B' in capsys.readouterr().out


def test_main_returns_3_for_an_error_on_the_vicinity_page_too(monkeypatch, tmp_path):
    page = PageOut('10-8', '<svg/>', {'frame': FRAME})
    monkeypatch.setattr(build_chart.vicinity, 'context_folder', lambda *a: tmp_path)
    monkeypatch.setattr(build_chart.vicinity, 'build_vicinity_page',
                        lambda data, frame, ctx, log=print: (log('10-8 ERREUR etiquette de taxiway sans place : C'), page)[1])
    monkeypatch.setattr(build_chart.capture_config, 'load_config',
                        lambda argv: (SimpleNamespace(annee=1985, osm_dir=str(tmp_path), work_dir=str(tmp_path)), argv))
    monkeypatch.setattr(build_chart.analysis, 'analyze', lambda *a, **k: SimpleNamespace(icao='XXXX'))
    monkeypatch.setattr(build_chart.compose, 'build_pages', lambda data, log=print: [PageOut('10-9', '<svg/>', {'frame': FRAME})])
    monkeypatch.setattr(build_chart.export, 'export_page', lambda svg, stem: (tmp_path / 'a.svg', tmp_path / 'a.pdf', tmp_path / 'a.png'))
    monkeypatch.setattr(build_chart.export, 'merge_pdfs', lambda pdfs, out: None)
    assert build_chart.main(['build_chart.py', 'Syria', 'Base', '--out', str(tmp_path)]) == 3


def test_main_returns_0_when_everything_is_placed(monkeypatch, tmp_path):
    rc, written = run_main(monkeypatch, tmp_path, ['etiquettes placees : 2/2', 'etiquettes : passe 1 2, passe 2 0, passe 3 0, deportees 0'])
    assert rc == 0 and len(written) == 2


# ---------- P9 : passe 4, etiquette deportee ----------
from shapely.geometry import LineString, box          # noqa: E402

ANCHOR = (52.0, 100.0)
SHORT = [('A', hline(100.0, 50.0, 54.0), 4.0)]
PARKS = [ANCHOR]                                       # un point de parking sur le tronçon : aucune place aux passes 1 a 3


def seg_of(info):
    return LineString([info[2], info[3]])


def test_pass4_attachment_point_and_first_free_place():
    placed, info = run(SHORT, [], PARKS)
    assert info['A'][0] == 4 and info['A'][1] == 1.0
    assert info['A'][2] == pytest.approx(ANCHOR) and info['A'][4] == 3.0 and info['A'][5] == 0.0
    assert placed['A'] == pytest.approx((52.0, 97.0))                    # 3 mm au nord de la page
    assert info['A'][3] == pytest.approx((52.0, 98.3))                   # le trait va au bord le plus proche


def test_pass4_distances_and_order_distance_first_then_direction():
    assert layout.LABEL_LEADER_DIST == (3.0, 4.5, 6.0, 8.0, 10.0, 12.0)
    forb = [(50.0, 95.5, 51.0, 98.0)]                                    # bloque la place nord a 3 mm seulement
    placed, info = run(SHORT, forb, PARKS)
    assert info['A'][4] == 3.0 and info['A'][5] == 22.5                  # a 3 mm, direction suivante (sens horaire), pas 4,5 mm au nord
    assert placed['A'] == pytest.approx((52.0 + 3 * math.sin(math.radians(22.5)), 100.0 - 3 * math.cos(math.radians(22.5))))


def test_pass4_leader_crossing_a_forbidden_geometry_is_refused():
    strip = box(48.0, 98.9, 56.0, 99.1)                                  # piste prolongee : au nord du point d'attache
    placed, info = run(SHORT, [strip], PARKS)
    assert info['A'][0] == 4 and info['A'][5] != 0.0
    assert not seg_of(info['A']).intersects(strip) and not layout._hits(rect_of(placed['A']), strip)


CORRIDOR = [(0.0, 0.0, 49.9, 210.0), (54.1, 0.0, 148.0, 210.0), (49.9, 101.0, 54.1, 210.0)]      # seul le nord est ouvert


def test_pass4_leader_crossing_another_label_is_refused():
    other = ('B', hline(97.2, 51.0, 53.0), 2.0)                          # etiquette de passe 1 entre le point d'attache et le nord
    placed, info = run(SHORT + [other], CORRIDOR, PARKS)
    assert info['B'][0] == 1
    assert 'A' not in placed                                             # 3 et 4,5 mm : recouvre B ; 6 a 12 mm : le trait couperait B
    placed, info = run(SHORT, CORRIDOR, PARKS)                           # sans B, la meme place convient
    assert info['A'][0] == 4 and info['A'][4] == 3.0


def test_pass4_leader_crossing_an_earlier_leader_is_refused():
    ways = [('A', hline(100.0, 50.0, 54.0), 4.0), ('B', hline(100.0, 50.0, 54.0), 4.0)]
    placed, info = run(ways, [], PARKS)                                  # meme point d'attache : le trait de A le touche, B n'a pas de place la
    assert info['A'][0] == 4 and info['A'][6] is False
    assert info['B'][0] == 4 and info['B'][6] is True and info['B'][2] != pytest.approx(ANCHOR)      # B est pose depuis un autre point d'attache
    assert not seg_of(info['B']).intersects(seg_of(info['A'])) and not seg_of(info['B']).intersects(box(*rect_of(placed['A'])))
    assert not seg_of(info['A']).intersects(box(*rect_of(placed['B'])))
    ways = [('A', hline(100.0, 50.0, 54.0), 4.0), ('B', hline(100.0, 56.0, 60.0), 4.0)]
    placed, info = run(ways, [], [ANCHOR, (58.0, 100.0)])
    assert info['A'][0] == 4 and info['B'][0] == 4
    assert not seg_of(info['A']).intersects(seg_of(info['B']))
    assert not layout.rects_overlap(rect_of(placed['A']), rect_of(placed['B']))
    for nm, other in (('A', 'B'), ('B', 'A')):
        assert not seg_of(info[nm]).intersects(box(*rect_of(placed[other])))


def test_pass4_falls_back_to_5pt():
    forb = CORRIDOR + [(0.0, 0.0, 148.0, 95.9)]                          # au nord, 2,5 mm de haut tiennent, 3,0 non
    placed, info = run(SHORT, forb, PARKS)
    assert info['A'][0] == 4 and info['A'][1] == layout.LABEL_SMALL and info['A'][4] == 3.0
    assert placed['A'] == pytest.approx((52.0, 97.0))


def test_pass4_nothing_within_12_mm_leaves_the_name_unplaced():
    placed, info = run(SHORT, [(0.0, 0.0, 148.0, 100.0), (0.0, 100.0, 148.0, 210.0)], PARKS)
    assert placed == {} and info == {}


def test_pass4_anchor_outside_bounds_is_the_continuous_point_nearest_the_centre():
    pts = np.array([[-10.0, 100.0], [20.0, 100.0], [90.0, 100.0], [400.0, 100.0]])        # milieu (195 ; 100) hors du cadre
    assert layout._anchor(pts, [(pts, 410.0)], BOUNDS) == pytest.approx((74.0, 100.0))    # centre du cadre (74 ; 105)
    inside = np.array([[0.0, 0.0], [10.0, 0.0]])
    assert layout._anchor(inside, [(inside, 10.0)], BOUNDS) == (5.0, 0.0)
    far = np.array([[-300.0, 50.0], [-100.0, 50.0], [900.0, 50.0]])                       # milieu hors cadre, ligne qui le traverse
    assert layout._anchor(far, [(far, 1000.0)], BOUNDS) == pytest.approx((74.0, 50.0))
    out = np.array([[-300.0, 50.0], [-250.0, 50.0], [-200.0, 50.0]])                      # la plus longue ligne ne coupe pas le cadre
    short = np.array([[100.0, 190.0], [120.0, 190.0]])
    assert layout._anchor(out, [(out, 100.0), (short, 20.0)], BOUNDS) == pytest.approx((100.0, 190.0))
    assert layout._anchor(out, [(out, 100.0)], BOUNDS) is None


def test_pass4_no_zero_length_leader():
    # etiquette haute de 6 mm, rien au-dessus de la ligne : a 3 mm au sud le rectangle contiendrait le point d'attache (trait nul) :
    # refuse, la place a 4,5 mm convient
    big = lambda nm, k: (4.0, 6.0 * k)
    placed, info = layout.place_labels_passes(SHORT, big, [(40.0, 96.0, 70.0, 99.9)], np.empty((0, 2)), 0.6, BOUNDS)
    assert info['A'][0] == 4 and info['A'][4] == 4.5
    r = rect_of_wh(placed['A'], 4.0, 6.0)
    assert not (r[0] - 0.25 <= ANCHOR[0] <= r[2] + 0.25 and r[1] - 0.25 <= ANCHOR[1] <= r[3] + 0.25)


def rect_of_wh(c, w, h):
    return (c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2)


NORTH_PARKS = PARKS + [(52.0, 97.0), (52.0, 95.5), (52.0, 94.0), (52.0, 92.0), (52.0, 90.0)]       # bloquent 3 a 10 mm au nord


def test_pass4_only_free_place_at_12_mm_is_used_and_nothing_beyond():
    placed, info = run(SHORT, CORRIDOR, NORTH_PARKS)
    assert info['A'][0] == 4 and info['A'][1] == 1.0 and info['A'][4] == 12.0 and info['A'][5] == 0.0
    placed, info = run(SHORT, CORRIDOR, NORTH_PARKS + [(52.0, 88.0)])                  # la place a 12 mm bloquee : rien avant 13 mm
    assert placed == {} and info == {}


def test_pass4_candidate_refused_only_because_its_rectangle_touches_an_earlier_leader():
    slabs = [(55.0, 0.0, 148.0, 98.3), (55.0, 99.4, 148.0, 210.0), (59.5, 98.3, 148.0, 99.4)]      # B ne garde que l'ouest
    ways = [('A', hline(100.0, 50.0, 54.0), 4.0), ('B', hline(98.85, 55.0, 59.0), 4.0)]
    parks = np.array([ANCHOR, (57.0, 98.85)])
    box_wh = lambda nm, k: ((4.0 if nm == 'A' else 4.7) * k, (2.6 if nm == 'A' else 1.0) * k)                              # B : 1 mm de haut (hors de la marge du parking)
    placed, info = layout.place_labels_passes(ways[1:], box_wh, slabs, parks[1:], 0.6, BOUNDS)      # seule : B a 3 mm a l'ouest
    assert info['B'][4] == 3.0 and info['B'][5] == 270.0 and placed['B'] == pytest.approx((54.0, 98.85))
    placed, info = layout.place_labels_passes(ways, box_wh, slabs, parks, 0.6, BOUNDS)              # avec A : son trait (x = 52) traverse ces places
    assert info['A'][0] == 4 and info['A'][5] == 0.0
    # la place refusee reste refusee : B n'est pas pose depuis son point d'aujourd'hui (57 ; 98,85) ; il l'est depuis un autre point,
    # sans croiser le trait de A ni toucher son rectangle
    assert info['B'][6] is True and info['B'][2] != pytest.approx((57.0, 98.85))
    assert not seg_of(info['B']).intersects(seg_of(info['A'])) and not seg_of(info['B']).intersects(box(*rect_of(placed['A'])))
    assert not seg_of(info['A']).intersects(box(*rect_of_wh(placed['B'], 4.7, 1.0)))


def test_pass4_anchor_outside_bounds_is_the_continuous_point_nearest_the_centre():
    pts = np.array([[-10.0, 100.0], [20.0, 100.0], [90.0, 100.0], [400.0, 100.0]])        # milieu (195 ; 100) hors du cadre
    assert layout._anchor(pts, [(pts, 410.0)], BOUNDS) == pytest.approx((74.0, 100.0))    # centre du cadre (74 ; 105)
    inside = np.array([[0.0, 0.0], [10.0, 0.0]])
    assert layout._anchor(inside, [(inside, 10.0)], BOUNDS) == (5.0, 0.0)
    far = np.array([[-300.0, 50.0], [-100.0, 50.0], [900.0, 50.0]])                       # milieu hors cadre, ligne qui le traverse
    assert layout._anchor(far, [(far, 1000.0)], BOUNDS) == pytest.approx((74.0, 50.0))
    out = np.array([[-300.0, 50.0], [-250.0, 50.0], [-200.0, 50.0]])                      # la plus longue ligne ne coupe pas le cadre
    short = np.array([[100.0, 190.0], [120.0, 190.0]])
    assert layout._anchor(out, [(out, 100.0), (short, 20.0)], BOUNDS) == pytest.approx((100.0, 190.0))
    assert layout._anchor(out, [(out, 100.0)], BOUNDS) is None


def test_pass4_no_zero_length_leader():
    # etiquette haute de 6 mm, rien au-dessus de la ligne : a 3 mm au sud le rectangle contiendrait le point d'attache (trait nul) :
    # refuse, la place a 4,5 mm convient
    big = lambda nm, k: (4.0, 6.0 * k)
    placed, info = layout.place_labels_passes(SHORT, big, [(40.0, 96.0, 70.0, 99.9)], np.empty((0, 2)), 0.6, BOUNDS)
    assert info['A'][0] == 4 and info['A'][4] == 4.5
    r = rect_of_wh(placed['A'], 4.0, 6.0)
    assert not (r[0] - 0.25 <= ANCHOR[0] <= r[2] + 0.25 and r[1] - 0.25 <= ANCHOR[1] <= r[3] + 0.25)


def rect_of_wh(c, w, h):
    return (c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2)


NORTH_PARKS = PARKS + [(52.0, 97.0), (52.0, 95.5), (52.0, 94.0), (52.0, 92.0), (52.0, 90.0)]       # bloquent 3 a 10 mm au nord


def test_pass4_only_free_place_at_12_mm_is_used_and_nothing_beyond():
    placed, info = run(SHORT, CORRIDOR, NORTH_PARKS)
    assert info['A'][0] == 4 and info['A'][1] == 1.0 and info['A'][4] == 12.0 and info['A'][5] == 0.0
    placed, info = run(SHORT, CORRIDOR, NORTH_PARKS + [(52.0, 88.0)])                  # la place a 12 mm bloquee : rien avant 13 mm
    assert placed == {} and info == {}


def test_leader_is_drawn_before_the_label_with_a_dot():
    pts = np.array([[20.0, 50.0], [21.0, 50.0]])
    doc2, lines2 = Doc(), []
    data = fake_data([('A', pts, 1.0)])
    _, rects2 = compose.taxiway_labels(doc2, data, FakeView(), FRAME, ((0, 0, 1, 1), (0, 0, 1, 1)), [], np.array([[20.5, 50.0]]),
                                       lines2.append)
    assert [r.split(' ')[0] for r in rects2] == ['<line', '<circle', '<rect']       # la graduation evite aussi le trait et le point
    layer = doc2.layers['Taxiway labels']
    assert layer[0].startswith('<line') and 'stroke="#1A1A1A"' in layer[0] and 'stroke-width="0.2"' in layer[0]
    assert layer[1].startswith('<circle') and 'r="0.25"' in layer[1] and 'fill="#1A1A1A"' in layer[1]
    assert layer[2].startswith('<rect') and layer[3].startswith('<text')
    assert 'etiquettes : passe 1 0, passe 2 0, passe 3 0, deportees 1' in lines2
    assert any(m.startswith('etiquette deportee : A a 3 mm, 6 pt') for m in lines2)


# ---------- P5 : Konya (echantillons) ----------
@pytest.fixture(scope='module')
def konya_logs(work):
    logs = []
    data = analysis.analyze(work, 'Syria', 'Konya', 1985, log=lambda *a: None)
    pages = compose.build_pages(data, log=logs.append)
    return logs, pages


def test_konya_10_9_taxiway_log_lines(konya_logs):
    logs, pages = konya_logs
    assert any(m.startswith('etiquettes placees : ') for m in logs)
    assert 'etiquettes placees : 20/20' in logs
    assert 'etiquettes : passe 1 18, passe 2 0, passe 3 0, deportees 2' in logs
    assert 'etiquette deportee : C a 3 mm, 6 pt' in logs and 'etiquette deportee : D3 a 6 mm, 6 pt' in logs
    assert not any('ERREUR' in m for m in logs)


def test_build_chart_konya_and_adana_exit_0(work, tmp_path):
    assert build_chart.main(['build_chart.py', 'Syria', 'Adana', '--config', str(work.source), '--out', str(tmp_path / 'a')]) == 0
    assert build_chart.main(['build_chart.py', 'Syria', 'Konya', '--config', str(work.source), '--out', str(tmp_path / 'k')]) == 0
    assert (tmp_path / 'k' / 'syria' / 'LTAN_Konya_10-9.pdf').is_file()
