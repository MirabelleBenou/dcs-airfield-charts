"""Capture : georeferencement v2 et points .rn4 (critere C10). Les fonctions pures de capture/f10capture.py sont importees
par render.f10import (module Windows, `ctypes.windll` factice ailleurs)."""
import copy
import json
import struct

import numpy as np
import pytest

from render import f10import, georef


@pytest.fixture(scope='module')
def f10():
    return f10import.load()


def test_corrected_coverage_formula(f10):
    cover = {'x_min': 1000.0, 'x_max': 5000.0, 'z_min': -9000.0, 'z_max': -3000.0}
    mpp = 0.5
    got = f10.corrected_coverage(cover, mpp)
    dx = (f10.MARGIN_T - f10.MARGIN_B) / 2 * mpp
    dz = (f10.MARGIN_R - f10.MARGIN_L) / 2 * mpp
    assert (f10.MARGIN_L, f10.MARGIN_T, f10.MARGIN_R, f10.MARGIN_B) == (0, 36, 90, 0)
    assert got == {'x_min': 1000.0 - dx, 'x_max': 5000.0 - dx, 'z_min': -9000.0 - dz, 'z_max': -3000.0 - dz}
    assert (dx, dz) == (9.0, 22.5)
    assert f10.GEOREF_VERSION == 2


def test_new_capture_writes_version_2_and_corrected_cover():
    """Le code de capture ecrit georef_version et la couverture corrigee (lecture du source : la capture elle-meme
    demande Windows et DCS)."""
    from pathlib import Path
    src = (Path(f10import.PATH)).read_text(encoding='utf-8')
    assert "'georef_version': GEOREF_VERSION" in src
    assert 'cover = corrected_coverage(' in src


def _rn4_bytes(n=400):
    pts = []
    for i in range(n):
        pts += [343000.0 + i * 3.0, 1038.5 + (i % 3) * 0.1, -283000.0 + i * 2.0]
    return struct.pack('<' + 'd' * len(pts), *pts), n


def test_rn4_points_and_extent_use_the_same_rule(f10, tmp_path):
    data, n = _rn4_bytes()
    assert len(data) > f10.MIN_RN4_BYTES
    p = tmp_path / 'X.rn4'
    p.write_bytes(data)
    pts = f10.rn4_points(p)
    assert pts.shape[1] == 3 and len(pts) > 0
    ext = f10.rn4_extent(p)
    assert ext['n_points'] == len(pts)
    assert ext['x_min'] == pts[:, 0].min() and ext['x_max'] == pts[:, 0].max()
    assert ext['z_min'] == pts[:, 2].min() and ext['z_max'] == pts[:, 2].max()
    assert ext['y_max'] == pts[:, 1].max()
    assert ext['center'] == [(ext['x_min'] + ext['x_max']) / 2, (ext['z_min'] + ext['z_max']) / 2]
    assert set(ext) == {'x_min', 'x_max', 'z_min', 'z_max', 'y_max', 'center', 'size_m', 'n_points'}
    # les vrais triplets sont retenus (tous, au moins ceux dont le suivant est coherent)
    assert len(pts) >= n - 1


def test_rn4_without_points(f10, tmp_path):
    p = tmp_path / 'vide.rn4'
    p.write_bytes(b'\x00' * 100)
    assert f10.rn4_points(p).shape == (0, 3)
    assert f10.rn4_extent(p) is None


def test_sample_rn4_points_match_recorded_extent(samples, f10):
    """Les points enregistres des echantillons redonnent l'emprise `.rn4` de leur base.json (regle inchangee)."""
    for base in ('Konya', 'Adana', 'Ramat_David'):
        folder = samples / 'syria' / base
        bj = json.loads((folder / 'base.json').read_text(encoding='utf-8'))
        pts = np.array([q[1:4] for q in json.loads((folder / 'rn4_points.json').read_text(encoding='utf-8'))['points']])
        e = bj['extent_rn4']
        assert pts[:, 0].min() == pytest.approx(e['x_min'], abs=0.01) and pts[:, 0].max() == pytest.approx(e['x_max'], abs=0.01)
        assert pts[:, 2].min() == pytest.approx(e['z_min'], abs=0.01) and pts[:, 2].max() == pytest.approx(e['z_max'], abs=0.01)


def test_v1_and_v2_base_json_give_the_same_georef(samples, f10):
    bj = json.loads((samples / 'syria' / 'Konya' / 'base.json').read_text(encoding='utf-8'))
    assert 'georef_version' not in bj
    g1 = georef.georef_from_base(bj)
    bj2 = copy.deepcopy(bj)
    bj2['mosaic_coverage_m'] = f10.corrected_coverage(bj['mosaic_coverage_m'], bj['m_per_px'])
    bj2['georef_version'] = 2
    g2 = georef.georef_from_base(bj2)
    for a, b in zip(g1, g2):
        assert a == pytest.approx(b, abs=1e-6)
    # et le decalage est bien celui du cadrage : 9 m en x, 22,5 m en z a 0,5 m/px (ici mpp = 0,5832)
    raw = bj['mosaic_coverage_m']
    assert g1.x_top == pytest.approx(raw['x_max'] - 18 * bj['m_per_px'], abs=1e-6)
    assert g1.z_left == pytest.approx(raw['z_min'] - 45 * bj['m_per_px'], abs=1e-6)


def test_runways_same_for_v1_and_v2(samples, f10, work):
    """C10 : le rendu donne le meme resultat (+- 1 m sur les pistes) pour un base.json v1 et son equivalent v2."""
    import shutil
    from render import analysis
    from pathlib import Path
    root = Path(work.work_dir)
    v2 = root / 'v2work'
    (v2 / 'out' / 'syria' / 'Adana').mkdir(parents=True)
    src = samples / 'syria' / 'Adana'
    for name in ('mosaic.png', 'tiles.json', 'rn4_points.json'):
        try:
            (v2 / 'out' / 'syria' / 'Adana' / name).symlink_to(src / name)
        except OSError:
            shutil.copy(src / name, v2 / 'out' / 'syria' / 'Adana' / name)
    bj = json.loads((src / 'base.json').read_text(encoding='utf-8'))
    bj['mosaic_coverage_m'] = f10.corrected_coverage(bj['mosaic_coverage_m'], bj['m_per_px'])
    bj['georef_version'] = 2
    (v2 / 'out' / 'syria' / 'Adana' / 'base.json').write_text(json.dumps(bj), encoding='utf-8')
    cfg2 = work._replace(work_dir=v2)
    d1 = analysis.analyze(work, 'Syria', 'Adana', 1985, log=lambda *a: None)
    d2 = analysis.analyze(cfg2, 'Syria', 'Adana', 1985, log=lambda *a: None)
    assert len(d1.runways) == len(d2.runways) == 1
    for a, b in zip(d1.runways, d2.runways):
        assert np.hypot(a.p0[0] - b.p0[0], a.p0[1] - b.p0[1]) < 1.0
        assert np.hypot(a.p1[0] - b.p1[0], a.p1[1] - b.p1[1]) < 1.0
