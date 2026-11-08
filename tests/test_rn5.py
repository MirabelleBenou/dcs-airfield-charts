"""Lecture du graphe des voies (.rn5) : capture/rn5.py, module pur (critere P1)."""
import struct

import pytest

import rn5

TER = ('dcs_install', 'Mods', 'terrains')


def _dir(samples, theatre):
    return samples.joinpath(*TER) / theatre / 'AirfieldsTaxiways'


def _s(text):
    b = text.encode('latin-1')
    return struct.pack('<I', len(b)) + b


def _tiny(seg_id=0, node_id=0):
    """Un .rn5 minimal valide : une largeur, un type, un troncon de 2 points, un noeud a un port."""
    seg = struct.pack('<I', 0) + struct.pack('<7I', seg_id, 0, 0, 0, 0, 0, 0)
    seg += struct.pack('<I', 2) + struct.pack('<6d', 0, 0, 0, 10, 0, 0)
    seg += struct.pack('<I', 0) + struct.pack('<I', 0) + struct.pack('<I', 0) + b'\0' * 12
    pose = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5.0, 0.0, 7.0, 1]
    node = struct.pack('<I', 0) + struct.pack('<3I', node_id, 0, 1) + struct.pack('<I', 0)
    node += struct.pack('<16d', *pose) + b'\0' * 128 + _s('59') + struct.pack('<I', 0)
    return (b'\0' * 32 + _s('t5::rn5file') + struct.pack('<I', 0)
            + struct.pack('<I', 1) + _s('taxiway_15m') + struct.pack('<I', 1) + _s('stand_x')
            + struct.pack('<I', 1) + seg + struct.pack('<I', 1) + node + b'\0' * 100)


def test_parse_minimal_file():
    r = rn5.parse(_tiny(), 'Tiny.rn5')
    assert r.widths == ('taxiway_15m',) and r.types == ('stand_x',)
    s, n = r.segments[0], r.nodes[0]
    assert s.width == 'taxiway_15m' and not s.is_runway and s.length == 10.0 and s.half_width == 7.5
    assert (n.type, n.ports, n.x, n.z, n.name) == ('stand_x', (0,), 5.0, 7.0, '59')


def test_identifier_not_its_rank_is_an_error_naming_the_file():
    with pytest.raises(rn5.Rn5Error, match='Tiny.rn5'):
        rn5.parse(_tiny(seg_id=3), 'Tiny.rn5')
    with pytest.raises(rn5.Rn5Error, match='Tiny.rn5'):
        rn5.parse(_tiny(node_id=2), 'Tiny.rn5')


def test_truncated_file_is_an_error_naming_the_file(tmp_path):
    data = _tiny()
    cut = data[:len(data) - 100 - 30]          # coupe dans l'enregistrement du noeud
    p = tmp_path / 'Cut.rn5'
    p.write_bytes(cut)
    with pytest.raises(rn5.Rn5Error, match='Cut.rn5'):
        rn5.read(p)
    with pytest.raises(rn5.Rn5Error, match='signature'):
        rn5.parse(b'\0' * 32 + _s('autre chose') + b'\0' * 64, 'X.rn5')


def test_runway_width_and_width_in_metres():
    assert rn5.width_m('taxiway_41m') == 41.0 and rn5.width_m('runway_45m') == 45.0 and rn5.width_m('taxiway') == 0.0


def test_real_file_mahlwinkel(samples):
    r = rn5.read(_dir(samples, 'GermanyColdWar') / 'Mahlwinkel.rn5')
    assert len(r.segments) > 200 and len(r.nodes) > 200
    assert all(s.id == i for i, s in enumerate(r.segments)) and all(n.id == i for i, n in enumerate(r.nodes))
    assert any(s.is_runway for s in r.segments)
    # chaque port d'un noeud renvoie a un troncon qui le nomme
    for n in r.nodes:
        for t in n.ports:
            assert n.id in (r.segments[t].node_a, r.segments[t].node_b)
    assert all(p[1] == 0.0 for s in r.segments for p in s.points)      # aucune altitude dans le fichier


def test_all_samples_are_read(samples):
    files = sorted(samples.joinpath(*TER).glob('*/AirfieldsTaxiways/*.rn5'))
    assert len(files) == 452
    for f in files:
        r = rn5.read(f)
        assert r.nodes                      # un heliport n'a aucun troncon


def test_truncated_real_file(samples, tmp_path):
    data = (_dir(samples, 'GermanyColdWar') / 'Mahlwinkel.rn5').read_bytes()
    p = tmp_path / 'Mahlwinkel.rn5'
    p.write_bytes(data[:len(data) // 2])
    with pytest.raises(rn5.Rn5Error, match='Mahlwinkel.rn5'):
        rn5.read(p)
