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
    ways = [('A', hline(50.0), 100.0), ('B', hline(52.0), 100.0), ('C', np.array([[20.0, 50.0], [26.0, 50.0]]), 6.0)]
    forb = [(0.0, 40.0, 148.0, 49.0), (0.0, 51.0, 148.0, 60.0)]
    old = legacy_place_labels(ways, lambda nm: (W, H), forb, np.empty((0, 2)), 0.6, BOUNDS)
    new, info = run(ways, forb)
    assert len(old) < 3
    for nm, c in old.items():
        assert new[nm] == c and info[nm] == (1, 1.0)


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
    ways = [('A', hline(50.0), 100.0), ('B', hline(50.0), 100.0), ('C', hline(50.0), 100.0)]
    forb = [(0.0, 0.0, 148.0, 49.0), (0.0, 51.0, 148.0, 210.0)]      # une seule bande de 2 mm : pas de place pour une etiquette de 2,6
    placed, info = run(ways, [])
    assert len(placed) == 3
    p1 = [nm for nm in placed if info[nm][0] == 1]
    old = legacy_place_labels(ways, lambda nm: (W, H), [], np.empty((0, 2)), 0.6, BOUNDS)
    assert all(placed[nm] == old[nm] for nm in p1) and set(p1) == set(old)
    names = sorted(placed)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not layout.rects_overlap(rect_of(placed[a], info[a][1]), rect_of(placed[b], info[b][1]))


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
    assert 'etiquettes placees : 1/1' in lines and 'etiquettes : passe 1 0, passe 2 0, passe 3 1' in lines
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
    rc, written = run_main(monkeypatch, tmp_path, ['etiquettes placees : 2/2', 'etiquettes : passe 1 2, passe 2 0, passe 3 0'])
    assert rc == 0 and len(written) == 2


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
    assert 'etiquettes : passe 1 18, passe 2 0, passe 3 0' in logs
    assert sorted(m for m in logs if m.startswith('ERREUR')) == ['ERREUR etiquette de taxiway sans place : C',
                                                                 'ERREUR etiquette de taxiway sans place : D3']


def test_build_chart_konya_exits_3_and_adana_exits_0(work, tmp_path):
    assert build_chart.main(['build_chart.py', 'Syria', 'Adana', '--config', str(work.source), '--out', str(tmp_path / 'a')]) == 0
    assert build_chart.main(['build_chart.py', 'Syria', 'Konya', '--config', str(work.source), '--out', str(tmp_path / 'k')]) == 3
    assert (tmp_path / 'k' / 'syria' / 'LTAN_Konya_10-9.pdf').is_file()
