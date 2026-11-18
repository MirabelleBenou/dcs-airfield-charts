"""Lot ac-ils-altitude-seuil : l'altitude du seuil quand aucun point de roulage n'est a 250 m (render/analysis.py,
`threshold_elev_ft`). Les cas reels n'existent pas dans les echantillons : les points de roulage sont construits dans le test.
Les tests de bout en bout (journal, page d'approche) partent de Adana et s'executent avec DCS_CHARTS_SAMPLES."""
import json
import os
import shutil

import numpy as np
import pytest

import config
from render import analysis, approach

FT = 3.28084
LINE = 'altitude du seuil {} prise à {} m du seuil (aucun point de roulage à 250 m)'


def old_threshold(pts, pos):
    """Le calcul de 00543a8, recopie."""
    pxz = pts[:, [0, 2]]
    d = np.hypot(pxz[:, 0] - pos[0], pxz[:, 1] - pos[1])
    thr = None
    if d.min() <= analysis.THRESHOLD_ELEV_MAX_M:
        thr = int(round(float(np.median(pts[np.argsort(d)[:analysis.THRESHOLD_ELEV_N], 1])) * FT))
    return thr


def line_pts(dists, alts=None):
    """Points (x, y, z) sur l'axe x, a `dists` m du seuil place en (0, 0)."""
    alts = alts if alts is not None else [10.0 * (i + 1) for i in range(len(dists))]
    return np.array([[d, a, 0.0] for d, a in zip(dists, alts)], float)


def median_ft(alts):
    return int(round(float(np.median(alts)) * FT))


def test_constants():
    assert analysis.THRESHOLD_ELEV_MAX_M == 250.0 and analysis.THRESHOLD_ELEV_N == 5
    assert analysis.THRESHOLD_ELEV_FALLBACK_MAX_M == 1000.0


def test_fallback_at_300_m_takes_the_median_of_the_5_nearest():
    alts = [10, 20, 30, 40, 50, 60, 70]
    pts = line_pts([300, 310, 320, 330, 340, 350, 360], alts)
    thr, d = analysis.threshold_elev_ft(pts, (0.0, 0.0))
    assert thr == median_ft(alts[:5]) and d == pytest.approx(300.0)


def test_nearest_5_are_chosen_by_distance_not_by_order():
    alts = [99, 50, 40, 30, 20, 10, 5]
    pts = line_pts([900, 350, 340, 330, 320, 310, 305], alts)
    thr, _ = analysis.threshold_elev_ft(pts, (0.0, 0.0))
    assert thr == median_ft([5, 10, 20, 30, 40]) and thr != median_ft(alts)


def test_fallback_at_exactly_1000_m():
    alts = [10, 20, 30, 40, 50]
    thr, d = analysis.threshold_elev_ft(line_pts([1000, 1000, 1000, 1000, 1000], alts), (0.0, 0.0))
    assert thr == median_ft(alts) and d == 1000.0


def test_unknown_at_1001_m():
    assert analysis.threshold_elev_ft(line_pts([1001, 1002, 1003, 1004, 1005]), (0.0, 0.0)) == (None, None)


def test_within_250_m_is_the_old_value_without_fallback():
    rng = np.random.default_rng(7)
    for nearest in (0.0, 120.0, 249.9, 250.0):
        pts = np.column_stack([nearest + rng.uniform(0, 400, 40), rng.uniform(0, 80, 40), rng.uniform(-300, 300, 40)])
        pts[0] = [nearest, 33.3, 0.0]
        thr, d = analysis.threshold_elev_ft(pts, (0.0, 0.0))
        assert thr == old_threshold(pts, (0.0, 0.0)) and thr is not None and d is None


def test_old_and_new_agree_whenever_the_old_one_knows():
    rng = np.random.default_rng(3)
    for _ in range(200):
        pts = np.column_stack([rng.uniform(-1500, 1500, 30), rng.uniform(0, 200, 30), rng.uniform(-1500, 1500, 30)])
        pos = (float(rng.uniform(-500, 500)), float(rng.uniform(-500, 500)))
        old = old_threshold(pts, pos)
        new, _ = analysis.threshold_elev_ft(pts, pos)
        if old is not None:
            assert new == old


def test_fewer_than_5_points_takes_the_median_of_those_that_exist():
    alts = [12.0, 30.0, 18.0]
    near = line_pts([100, 120, 140], alts)
    far = line_pts([400, 500, 600], alts)
    assert analysis.threshold_elev_ft(near, (0.0, 0.0)) == (median_ft(alts), None) == (old_threshold(near, (0.0, 0.0)), None)
    thr, d = analysis.threshold_elev_ft(far, (0.0, 0.0))
    assert thr == median_ft(alts) and d == pytest.approx(400.0)
    assert analysis.threshold_elev_ft(line_pts([300], [25.0]), (0.0, 0.0))[0] == median_ft([25.0])


def test_threshold_position_is_x_z_and_height_is_y():
    pts = np.array([[1000.0, 7.0, 1000.0 + 400.0]] * 5)
    thr, d = analysis.threshold_elev_ft(pts, (1000.0, 1000.0))
    assert thr == median_ft([7.0]) and d == pytest.approx(400.0)


# ---------- de bout en bout, depuis Adana (echantillons) ----------
SYNTH_ALTS = [10.0, 12.0, 14.0, 16.0, 18.0]          # les 5 points ajoutes devant chaque seuil : mediane 14 m


@pytest.fixture(scope='module')
def adana_world(samples, charts, tmp_path_factory):
    """Mondes derives de Adana ou aucun point de roulage ne reste a moins de R m des deux seuils, sauf 5 points ajoutes a D m devant
    chaque seuil (dans le prolongement de la piste) : {D: (Config, ChartData de depart)}."""
    base = charts['Adana'][0]
    src = samples / 'syria' / 'Adana'
    pts = json.loads((src / 'rn4_points.json').read_text(encoding='utf-8'))
    p0, p1 = (np.array(e.pos) for e in base.ends)
    worlds = {}
    for D, R in ((400.0, 410.0), (1200.0, 1210.0)):
        w = tmp_path_factory.mktemp(f'w{int(D)}')
        dst = w / 'out' / 'syria' / 'Adana'
        dst.mkdir(parents=True)
        for f in ('mosaic.png', 'base.json', 'tiles.json'):
            try:
                os.symlink(src / f, dst / f)
            except OSError:
                shutil.copy(src / f, dst / f)
        keep = [q for q in pts['points'] if all(np.hypot(q[1] - e.pos[0], q[3] - e.pos[1]) >= R for e in base.ends)]
        for here, other in ((p0, p1), (p1, p0)):
            u = (here - other) / np.hypot(*(here - other))
            for k, y in enumerate(SYNTH_ALTS):
                x, z = here + u * (D + 0.5 * k)
                keep.append([0, float(x), y, float(z)])
        (dst / 'rn4_points.json').write_text(json.dumps(dict(pts, points=keep)), encoding='utf-8')
        (w / 'osm').mkdir()
        (w / 'c.json').write_text(json.dumps({'work_dir': str(w), 'dcs_install': str(samples / 'dcs_install')}), encoding='utf-8')
        cfg, _ = config.load_config(['x', '--config', str(w / 'c.json')])
        worlds[D] = cfg
    return worlds


def test_journal_and_approach_page_with_the_nearest_point_at_400_m(adana_world):
    msgs = []
    data = analysis.analyze(adana_world[400.0], 'Syria', 'Adana', 1985, log=msgs.append)
    lines = [m for m in msgs if m.startswith('altitude du seuil')]
    assert sorted(lines) == sorted(LINE.format(e.name, 400) for e in data.ends) and len(lines) == 2
    assert [e.thr_elev_ft for e in data.ends] == [median_ft(SYNTH_ALTS)] * 2
    log = []
    ok = approach.eligible_ends(data, log=log.append)
    assert [x[0].name for x in ok] == ['05'] and ok[0][0].thr_elev_ft == median_ft(SYNTH_ALTS)
    assert not any('écartée' in m for m in log)
    assert len(approach.build_approach_pages(data, log=lambda *a: None)) == 1


def test_nothing_changes_when_the_nearest_point_is_at_1200_m(adana_world):
    msgs = []
    data = analysis.analyze(adana_world[1200.0], 'Syria', 'Adana', 1985, log=msgs.append)
    assert [e.thr_elev_ft for e in data.ends] == [None, None]
    assert not any(m.startswith('altitude du seuil') for m in msgs)
    log = []
    assert approach.eligible_ends(data, log=log.append) == []
    assert log == ['approche 05 : écartée, altitude du seuil inconnue']


def test_no_line_when_a_point_is_within_250_m(charts, work):
    msgs = []
    data = analysis.analyze(work, 'Syria', 'Adana', 1985, log=msgs.append)
    assert not any(m.startswith('altitude du seuil') for m in msgs)
    assert [e.thr_elev_ft for e in data.ends] == [56, 56]
