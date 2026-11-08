"""Option --marge de `capture` : lecture, zone, dossier de sortie, margin_m. Sans DCS : tout ce qui bouge la camera ou
capture l'ecran est remplace par des doublures (capture/f10capture.py est importe par render.f10import)."""
import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from render import f10import

SAMPLE_RN4 = Path('dcs_install/Mods/terrains/GermanyColdWar/AirfieldsTaxiways/Mahlwinkel.rn4')


@pytest.fixture(scope='module')
def f10():
    return f10import.load()


def _rn4_bytes(n=400):
    pts = []
    for i in range(n):
        pts += [343000.0 + i * 3.0, 1038.5 + (i % 3) * 0.1, -283000.0 + i * 2.0]
    return struct.pack('<' + 'd' * len(pts), *pts)


# ---------- lecture de l'option ----------
@pytest.mark.parametrize('args, bases, marge', [
    (['A'], ['A'], None),
    (['A', 'B'], ['A', 'B'], None),
    (['--marge', '1500', 'A', 'B'], ['A', 'B'], 1500.0),
    (['A', '--marge', '1500', 'B'], ['A', 'B'], 1500.0),
    (['A', 'B', '--marge', '1500'], ['A', 'B'], 1500.0),
    (['A', '--marge', '1250.5'], ['A'], 1250.5),
    (['A', '--marge', '5000'], ['A'], 5000.0),
    (['A', '--marge', '0.5'], ['A'], 0.5),
    (['A', '--marge', '400'], ['A'], 400.0),
])
def test_parse_marge_accepts(f10, args, bases, marge):
    assert f10.parse_marge(args) == (bases, marge)


@pytest.mark.parametrize('args', [
    ['A', '--marge', '0'], ['A', '--marge', '-5'], ['A', '--marge', '5001'], ['A', '--marge', 'abc'],
    ['A', '--marge', 'nan'], ['A', '--marge', 'inf'], ['A', '--marge'], ['--marge'],
    ['A', '--marge', '100', '--marge', '200'], ['A', '--marge', '100', 'B', '--marge', '100'],
    ['--marge', '1500'],
])
def test_parse_marge_refuses(f10, args):
    with pytest.raises(SystemExit):
        f10.parse_marge(args)


@pytest.mark.parametrize('args', [
    ['A', '--marge', '0'], ['A', '--marge', '-5'], ['A', '--marge', '5001'], ['A', '--marge', 'abc'],
    ['A', '--marge'], ['A', '--marge', '1', '--marge', '2'],
])
def test_refusal_comes_before_any_capture(f10, monkeypatch, args):
    calls = []
    monkeypatch.setattr(f10, 'capture_base', lambda *a, **k: calls.append('capture'))
    monkeypatch.setattr(f10, 'hook_release', lambda: calls.append('hook_release'))
    with pytest.raises(SystemExit):
        f10.main(['f10capture.py', 'capture', 'germanycoldwar'] + args)
    assert calls == []


def test_main_passes_margin_to_capture(f10, monkeypatch):
    got = []
    monkeypatch.setattr(f10, 'capture_base', lambda theatre, base, **k: got.append((theatre, base, k)))
    monkeypatch.setattr(f10, 'hook_release', lambda: None)
    f10.main(['f10capture.py', 'capture', 'T', 'A', '--marge', '1500', 'B'])
    assert got == [('T', 'A', {'margin': 1500.0}), ('T', 'B', {'margin': 1500.0})]
    got.clear()
    f10.main(['f10capture.py', 'capture', 'T', 'A'])
    assert got == [('T', 'A', {'margin': None})]


# ---------- la zone ----------
def test_zone_without_option_is_extent_plus_400(f10, tmp_path):
    p = tmp_path / 'X.rn4'
    p.write_bytes(_rn4_bytes())
    ext = f10.rn4_extent(p)
    assert f10.MARGIN_ZONE_M == 400.0
    for z in (f10.zone_of(ext), f10.zone_of(ext, None)):
        assert z == {'x_min': ext['x_min'] - 400.0, 'x_max': ext['x_max'] + 400.0,
                     'z_min': ext['z_min'] - 400.0, 'z_max': ext['z_max'] + 400.0}


def test_zone_with_margin_is_extent_plus_margin(f10, tmp_path):
    p = tmp_path / 'X.rn4'
    p.write_bytes(_rn4_bytes())
    ext = f10.rn4_extent(p)
    z = f10.zone_of(ext, 1500.0)
    assert z == {'x_min': ext['x_min'] - 1500.0, 'x_max': ext['x_max'] + 1500.0,
                 'z_min': ext['z_min'] - 1500.0, 'z_max': ext['z_max'] + 1500.0}
    assert f10.zone_of(ext, 400.0) == f10.zone_of(ext)


def test_mahlwinkel_zone_plus_1500(f10):
    root = os.environ.get('DCS_CHARTS_SAMPLES')
    if not root or not (Path(root) / SAMPLE_RN4).is_file():
        pytest.skip('echantillons absents (variable DCS_CHARTS_SAMPLES)')
    ext = f10.rn4_extent(Path(root) / SAMPLE_RN4)
    assert ext is not None
    z = f10.zone_of(ext, 1500.0)
    for k, sign in (('x_min', -1), ('x_max', 1), ('z_min', -1), ('z_max', 1)):
        assert z[k] == pytest.approx(ext[k] + sign * 1500.0, abs=1e-6)   # au centimetre pres, largement
    assert z['x_max'] - z['x_min'] == pytest.approx(ext['x_max'] - ext['x_min'] + 3000.0, abs=1e-6)
    assert z['z_max'] - z['z_min'] == pytest.approx(ext['z_max'] - ext['z_min'] + 3000.0, abs=1e-6)
    assert f10.zone_of(ext)['x_max'] - ext['x_max'] == 400.0


# ---------- dossier de sortie et base.json (capture simulee) ----------
@pytest.fixture
def fake_capture(f10, tmp_path, monkeypatch):
    terrains = tmp_path / 'terrains'
    d = terrains / 'Fake' / 'AirfieldsTaxiways'
    d.mkdir(parents=True)
    (d / 'Zeta.rn4').write_bytes(_rn4_bytes())
    work = tmp_path / 'work'
    monkeypatch.setattr(f10, 'CFG', SimpleNamespace(terrains=terrains, work_dir=work))
    zones = []

    def calibrate(zone, alt, mpp_pred, odir, label, prog=None):
        zones.append(zone)
        Path(odir).mkdir(parents=True, exist_ok=True)
        (Path(odir) / 'calib.json').write_text('{}', encoding='utf-8')
        return {'measures': [mpp_pred] * 6, 'mean': mpp_pred, 'max_spread_rel': 0.0, 'dev_vs_predicted_rel': 0.0,
                'align_deg_max': 0.0, 'peaks': [], 'detail': {}}

    def mosaic(tiles, fw, fh, odir, mpp):
        (Path(odir) / 'mosaic.png').write_bytes(b'png')
        x_top = max(t['x'] for t in tiles) + fh / 2
        z_left = min(t['z'] for t in tiles) - fw / 2
        return 10, 10, x_top, z_left

    monkeypatch.setattr(f10, 'calibrate', calibrate)
    monkeypatch.setattr(f10, 'move_and_stabilize',
                        lambda x, z, alt, expect_change=True: (np.zeros((4, 4, 3), np.uint8), 0.1, 2, True, (x, z, alt)))
    monkeypatch.setattr(f10, 'crop', lambda img: img)
    monkeypatch.setattr(f10, 'save_png', lambda img, path: (Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                            Path(path).write_bytes(b'png')))
    monkeypatch.setattr(f10, 'build_mosaic', mosaic)
    monkeypatch.setattr(f10, 'usable_px', lambda: (1000, 700))
    monkeypatch.setattr(f10, 'screen_size', lambda: (1090, 736))
    monkeypatch.setattr(f10, 'set_status', lambda s: None)
    monkeypatch.setattr(f10, 'hook_release', lambda: None)
    return SimpleNamespace(work=work, zones=zones, rn4=d / 'Zeta.rn4')


def _read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def test_capture_without_option_keeps_folder_and_fields(f10, fake_capture):
    f10.main(['f10capture.py', 'capture', 'Fake', 'Zeta'])
    out = fake_capture.work / 'out' / 'fake'
    assert (out / 'Zeta' / 'base.json').is_file() and (out / 'Zeta' / 'mosaic.png').is_file()
    assert (out / 'Zeta' / 'tiles.json').is_file() and (out / 'Zeta' / 'tiles').is_dir()
    assert not (out / 'contexte').exists()
    bj = _read(out / 'Zeta' / 'base.json')
    assert 'margin_m' not in bj
    ext = f10.rn4_extent(fake_capture.rn4)
    assert bj['zone_captured'] == f10.zone_of(ext) == fake_capture.zones[0]


def test_capture_with_margin_goes_to_contexte(f10, fake_capture):
    f10.main(['f10capture.py', 'capture', 'Fake', '--marge', '1500', 'Zeta'])
    out = fake_capture.work / 'out' / 'fake'
    assert not (out / 'Zeta').exists()                  # rien dans le dossier des charts
    ctx = out / 'contexte' / 'Zeta'
    for name in ('base.json', 'tiles.json', 'mosaic.png', 'calib.json'):
        assert (ctx / name).is_file(), name
    assert (ctx / 'tiles').is_dir()
    assert not list(out.glob('*/base.json'))            # invisible pour les outils de rendu (un seul niveau)
    bj = _read(ctx / 'base.json')
    assert bj['margin_m'] == 1500
    ext = f10.rn4_extent(fake_capture.rn4)
    assert bj['zone_captured'] == f10.zone_of(ext, 1500.0) == fake_capture.zones[0]


def test_margin_400_still_goes_to_contexte(f10, fake_capture):
    f10.main(['f10capture.py', 'capture', 'Fake', 'Zeta', '--marge', '400'])
    out = fake_capture.work / 'out' / 'fake'
    assert not (out / 'Zeta').exists()
    bj = _read(out / 'contexte' / 'Zeta' / 'base.json')
    assert bj['margin_m'] == 400
    assert bj['zone_captured'] == f10.zone_of(f10.rn4_extent(fake_capture.rn4))


def test_docstring_has_the_marge_line(f10):
    assert ('  capture <Theatre> <Base> [<Base> ...] [--marge <m>]  -> out/<theatre>/<Base>/'
            '{tiles/,tiles.json,mosaic.png,base.json} ; avec --marge : zone = emprise .rn4 + <m> m, '
            'sous out/<theatre>/contexte/<Base>/') in f10.__doc__
