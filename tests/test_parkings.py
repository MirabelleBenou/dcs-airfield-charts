"""Lot parkings : fichiers de downloads/parkings/ face a pydcs (sans echantillon)."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from render import export_parkings as ep

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / 'downloads' / 'parkings'
KEYS = [k for _, k in ep.THEATRES]


@pytest.fixture(scope='module')
def fresh(tmp_path_factory):
    d = tmp_path_factory.mktemp('parkings')
    ep.export(d, log=lambda *_: None)
    return d


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_commited_files_identical_to_command_output(fresh):
    names = sorted(p.name for p in fresh.iterdir())
    assert names == sorted([f'{k}.json' for k in KEYS] + ['aircraft.json'])
    assert len(names) == 12
    for n in names:
        assert (PUB / n).read_bytes() == (fresh / n).read_bytes(), n


def test_two_passes_same_bytes(fresh, tmp_path):
    ep.export(tmp_path, log=lambda *_: None)
    for p in fresh.iterdir():
        assert (tmp_path / p.name).read_bytes() == p.read_bytes()


def test_publication_readme_and_no_extra_file():
    assert (PUB / 'README.md').is_file()
    assert sorted(p.name for p in PUB.iterdir() if p.suffix == '.json') == sorted([f'{k}.json' for k in KEYS] + ['aircraft.json'])


@pytest.mark.parametrize('module,key', ep.THEATRES)
def test_theatre_file(module, key):
    raw = (PUB / f'{key}.json').read_bytes()
    assert len(raw) <= 1048576
    assert raw.endswith(b'\n')
    d = json.loads(raw.decode('utf-8'))
    commit = ep.pinned_commit()
    assert d['format'] == 'dcs-parkings-v1'
    assert d['theatre'] == key
    assert d['source'] == f'pydcs {commit[:7]} (LGPL-3.0): parking stands of the DCS mission editor'
    ids = [a['airdromeId'] for a in d['airdromes']]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    terrain = ep.load_terrain(module)
    assert len(d['airdromes']) == len(terrain.airports)
    n = 0
    by_id = {a['airdromeId']: a for a in d['airdromes']}
    for ap in terrain.airports.values():
        a = by_id[ap.id]
        assert a['name'] == ap.name
        assert a['x'] == round(ap.position.x, 2) and a['y'] == round(ap.position.y, 2)
        pk = [p['parking'] for p in a['parkings']]
        assert pk == sorted(pk) and len(set(pk)) == len(pk)
        assert len(a['parkings']) == len(ap.parking_slots)
        got = {p['parking']: p for p in a['parkings']}
        for s in ap.parking_slots:
            p = got[s.crossroad_idx]
            assert p['parking_id'] == str(s.slot_name)
            assert (p['x'], p['y']) == (round(s.position.x, 2), round(s.position.y, 2))
            assert p['heli'] is bool(s.helicopter) and p['plane'] is bool(s.airplanes)
            assert p['length'] == round(s.length, 3) and p['width'] == round(s.width, 3)
            assert p['height'] == (None if s.height is None else round(s.height, 3))
            assert set(p) == {'parking', 'parking_id', 'x', 'y', 'heli', 'plane', 'length', 'width', 'height'}
            n += 1
    assert n == sum(len(a['parkings']) for a in d['airdromes'])


def test_syria_first_airdrome():
    d = _load(PUB / 'Syria.json')
    a = d['airdromes'][0]
    assert a['airdromeId'] == 1 and a['name'] == 'Abu al-Duhur'
    p = a['parkings'][0]
    assert p['parking'] == 2 and p['parking_id'] == '26'
    assert p['heli'] is True and p['plane'] is True
    assert (p['length'], p['width'], p['height']) == (40, 40, 12)


def test_aircraft_file():
    from dcs.helicopters import helicopter_map
    from dcs.planes import plane_map
    d = _load(PUB / 'aircraft.json')
    assert d['format'] == 'dcs-aircraft-sizes-v1'
    assert d['source'] == f'pydcs {ep.pinned_commit()[:7]} (LGPL-3.0): parking stands of the DCS mission editor'
    keys = list(d['aircraft'])
    assert keys == sorted(keys)
    assert len(keys) == len({c.id for c in plane_map.values()} | {c.id for c in helicopter_map.values()})
    assert len(keys) == len(plane_map) + len(helicopter_map)
    m = d['aircraft']['Mi-24P']
    assert m == {'heli': True, 'length': 20.953, 'width': 17.3, 'height': 4.354}


def _terrain(slots, airports=None):
    ap = NS(id=1, name='Fake', position=NS(x=0.0, y=0.0), parking_slots=slots)
    return NS(airports={0: ap} if airports is None else airports)


def _slot(idx, name='1', length=10, width=10):
    return NS(crossroad_idx=idx, slot_name=name, position=NS(x=1.234, y=2.345), helicopter=True, airplanes=False,
              length=length, width=width, height=None)


def test_stops():
    ok = ep.theatre_data('Fake', _terrain([_slot(1), _slot(2)]))
    assert ok['airdromes'][0]['parkings'][0]['height'] is None
    with pytest.raises(ep.ExportError, match='Fake.*Fake.*crossroad_idx'):
        ep.theatre_data('Fake', _terrain([_slot(5), _slot(5)]))
    with pytest.raises(ep.ExportError, match='slot_name'):
        ep.theatre_data('Fake', _terrain([_slot(1, name=None)]))
    with pytest.raises(ep.ExportError, match='length'):
        ep.theatre_data('Fake', _terrain([_slot(1, length=None)]))
    with pytest.raises(ep.ExportError, match='width'):
        ep.theatre_data('Fake', _terrain([_slot(1, width=None)]))
    a = _terrain([])
    b = _terrain([])
    with pytest.raises(ep.ExportError, match='id'):
        ep.theatre_data('Fake', NS(airports={0: a.airports[0], 1: b.airports[0]}))
