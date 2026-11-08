"""Lot ac-oaci-balises-export : codes OACI et balises de downloads/navaids/ (sans echantillon ; la derniere partie en demande)."""
import json
import os
import re
from pathlib import Path

import pytest

from render import export_navaids as en
from render import export_parkings as ep

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / 'downloads' / 'navaids'
PARK = ROOT / 'downloads' / 'parkings'
KEYS = [k for _, k in ep.THEATRES]
WITH_BEACONS = ['Caucasus', 'GermanyCW', 'Syria']
HEADER = 'id,ident,type,name,latitude_deg,longitude_deg,icao_code,gps_code\n'


def _csv(tmp_path, lines):
    p = tmp_path / 'airports.csv'
    p.write_text(HEADER + ''.join(lines), encoding='utf-8')
    return p


def _rows(tmp_path, lines):
    return en.read_ourairports(_csv(tmp_path, lines))


# ------------------------------------------------------------------ OACI

def test_code_icao_then_gps_then_ident(tmp_path):
    rows = _rows(tmp_path, [
        '1,"AAA1","small_airport","a",0.0,0.0,"LFAA","ZZZZ"\n',     # icao_code d'abord
        '2,"AAA2","small_airport","b",1.0,0.0,,"LFBB"\n',          # puis gps_code
        '3,"LFCC","small_airport","c",2.0,0.0,,\n',                # puis ident
        '4,"LF1C","small_airport","d",3.0,0.0,,"lfdd"\n',          # ni l'un ni l'autre (pas 4 majuscules)
        '5,"LFEE","small_airport","e",4.0,0.0,,"G1"\n',            # gps_code invalide : ident
    ])
    assert [r[3] for r in rows] == ['LFAA', 'LFBB', 'LFCC', None, 'LFEE']
    codes = en.match_icao([(i, float(i - 1), 0.0) for i in range(1, 6)], rows)
    assert codes == {1: 'LFAA', 2: 'LFBB', 3: 'LFCC', 4: None, 5: 'LFEE'}


def test_nearest_row_without_code_leaves_null(tmp_path):
    # Soganlug : une ligne sans code est plus proche qu'une ligne avec code (a 2,6 km)
    rows = _rows(tmp_path, [
        '1,"UGTB","large_airport","Tbilisi",0.0,0.0,"UGTB","UGTB"\n',
        '2,"X1","small_airport","base",0.0,0.0234,,\n',            # 2,6 km de la premiere
    ])
    assert en.match_icao([(30, 0.0, 0.0225)], rows) == {30: None}   # plus proche de la ligne sans code
    assert en.match_icao([(31, 0.0, 0.0)], rows) == {31: 'UGTB'}


def test_three_km_kept_beyond_not(tmp_path):
    rows = _rows(tmp_path, ['1,"LFAA","small_airport","a",0.0,0.0,"LFAA","LFAA"\n'])
    d = en.great_circle_km(0, 0, 0, 0.0269)
    assert d < 3.0 < en.great_circle_km(0, 0, 0, 0.0271)
    assert en.match_icao([(1, 0.0, 0.0269)], rows) == {1: 'LFAA'}
    assert en.match_icao([(1, 0.0, 0.0271)], rows) == {1: None}


def test_great_circle_radius():
    assert en.great_circle_km(0, 0, 0, 1) == pytest.approx(2 * 3.141592653589793 * 6371.0088 / 360, rel=1e-12)


def test_two_airdromes_one_row_nearest_keeps_it(tmp_path):
    rows = _rows(tmp_path, ['1,"LFAA","small_airport","a",0.0,0.0,"LFAA","LFAA"\n'])
    codes = en.match_icao([(5, 0.0, 0.010), (6, 0.0, 0.005), (7, 0.0, -0.005)], rows)
    assert codes == {5: None, 6: 'LFAA', 7: None}      # 6 et 7 a egalite : le plus petit airdromeId... 6 < 7
    codes = en.match_icao([(9, 0.0, 0.005), (8, 0.0, -0.005)], rows)
    assert codes == {8: 'LFAA', 9: None}


def test_wrong_sha256_refused(tmp_path):
    p = _csv(tmp_path, ['1,"LFAA","small_airport","a",0.0,0.0,"LFAA","LFAA"\n'])
    with pytest.raises(ep.ExportError) as e:
        en.check_ourairports(p)
    assert en.OURAIRPORTS_SHA256 in str(e.value) and en.sha256_file(p) in str(e.value)
    en.check_ourairports(p, expected=en.sha256_file(p))


def test_pinned_constants():
    assert en.OURAIRPORTS_COMMIT == '64af12951161ed52bc733eb4101224643c48850a'
    assert en.OURAIRPORTS_SHA256 == '5754df21f25cb08d2def8a80b3eb609ddc52a4217afa8eb48280b6428c06e78f'


# --------------------------------------------------------------- balises

def _block(bid, typ, callsign, freq, pos, channel=None):
    s = f"\t{{\n\t\tdisplay_name = _('X');\n\t\tbeaconId = '{bid}';\n\t\ttype = BEACON_TYPE_{typ};\n\t\tcallsign = '{callsign}';\n"
    if freq is not None:
        s += f'\t\tfrequency = {freq:.6f};\n'
    if channel is not None:
        s += f'\t\tchannel = {channel};\n'
    return s + f'\t\tposition = {{ {pos[0]}, 7.0, {pos[1]} }};\n\t\tdirection = 0.000000;\n\t}};\n'


@pytest.fixture(scope='module')
def caucasus():
    return ep.load_terrain('caucasus')


def _fake_lua(path, terrain):
    ap = next(a for a in terrain.airports.values() if any(rb for rw in a.runways for rb in rw.main.beacons))
    rb = next(rb for rw in ap.runways for rb in rw.main.beacons)
    rw_name = next(rw.main.name for rw in ap.runways if rb in rw.main.beacons)
    n = int(rb.id.split('_')[1])
    pre = f'airfield{ap.id}_'
    text = ('beaconsTableFormat = 2\nbeacons = {\n'
            + _block(pre + str(n + 10), 'TACAN', 'TCN', None, (0, 0), channel=44)
            + _block(rb.id, 'ILS_LOCALIZER', 'ILS', 110300000.0, (1, 1))
            + _block('world_2', 'HOMER', 'WB', 400000.4, (-100000, 300000))
            + _block('world_1', 'VOR_DME', 'WA', 0.0, (-100000, 300000), channel=5)
            + '}\n')
    p = path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    return ap, rb, rw_name, n


def test_fake_beacons_lua(tmp_path, caucasus):
    f = tmp_path / 'Caucasus' / 'Beacons.lua'            # Caucasus l'ecrit avec une majuscule
    ap, rb, rw_name, n = _fake_lua(f, caucasus)
    assert en.find_beacons_file(tmp_path, 'Caucasus') == f
    per, enr = en.beacons_data('Caucasus', caucasus, f)
    mine = per[ap.id]
    assert [b['callsign'] for b in mine] == (['ILS', 'TCN'])          # tri par n croissant
    assert mine[0] == {'type': 'ILS_LOCALIZER', 'callsign': 'ILS', 'frequency': 110300000, 'channel': None, 'runway': rw_name}
    assert mine[1]['frequency'] is None and mine[1]['channel'] == 44 and mine[1]['runway'] is None
    assert all(v == [] for k, v in per.items() if k != ap.id)
    assert [b['callsign'] for b in enr] == ['WA', 'WB']               # tri par n, sans rattachement
    assert enr[0]['frequency'] is None and enr[0]['channel'] == 5
    assert enr[1]['frequency'] == 400000 and isinstance(enr[1]['frequency'], int)
    assert set(enr[0]) == {'type', 'callsign', 'frequency', 'channel', 'lat', 'lon'}
    assert enr[0]['lat'] == round(enr[0]['lat'], 6) and 40 < enr[0]['lat'] < 50 and 30 < enr[0]['lon'] < 45


def test_folder_without_file_gives_null(tmp_path, caucasus):
    assert en.find_beacons_file(tmp_path, 'Caucasus') is None
    (tmp_path / 'Caucasus').mkdir()
    assert en.find_beacons_file(tmp_path, 'Caucasus') is None
    data = en.theatre_data('Caucasus', caucasus, {}, None)
    assert data['enroute'] is None and data['source']['beacons'] is None
    assert all(a['beacons'] is None and a['icao'] is None for a in data['airdromes'])
    assert json.loads(en.theatre_text(data)) == data


def test_germany_folder_name(tmp_path):
    (tmp_path / 'GermanyColdWar').mkdir()
    (tmp_path / 'GermanyColdWar' / 'BEACONS.LUA').write_text('x', encoding='utf-8')
    assert en.find_beacons_file(tmp_path, 'GermanyCW') == tmp_path / 'GermanyColdWar' / 'BEACONS.LUA'


def test_unknown_airfield_stops(tmp_path, caucasus):
    f = tmp_path / 'beacons.lua'
    f.write_text('beacons = {\n' + _block('airfield9999_0', 'HOMER', 'ZZ', 300000.0, (0, 0)) + '}\n', encoding='utf-8')
    with pytest.raises(ep.ExportError):
        en.beacons_data('Caucasus', caucasus, f)


# ------------------------------------------------------ fichiers commites

def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _commit():
    return re.search(r'^pydcs @ git\+\S+@([0-9a-f]{40})$', (ROOT / 'requirements.txt').read_text(encoding='utf-8'), re.M).group(1)


def test_eleven_files_and_readme():
    assert sorted(p.name for p in PUB.iterdir()) == sorted([f'{k}.json' for k in KEYS] + ['README.md'])
    assert len(KEYS) == 11


@pytest.mark.parametrize('module,key', ep.THEATRES)
def test_committed_theatre(module, key):
    raw = (PUB / f'{key}.json').read_bytes()
    assert raw.endswith(b'\n') and not raw.endswith(b'\n\n')
    d = json.loads(raw.decode('utf-8'))
    assert d['format'] == 'dcs-navaids-v1' and d['theatre'] == key
    src = d['source']
    assert src['airfields'] == f'pydcs {_commit()[:7]} (LGPL-3.0): airfield names and positions'
    assert src['icao'] == "OurAirports 64af129 (public domain): today's ICAO codes, matched by position within 3 km"
    terrain = ep.load_terrain(module)
    aps = d['airdromes']
    assert len(aps) == len(terrain.airports)
    assert [a['airdromeId'] for a in aps] == sorted(a['airdromeId'] for a in aps)
    assert {a['airdromeId']: a['name'] for a in aps} == {ap.id: ap.name for ap in terrain.airports.values()}
    park = _load(PARK / f'{key}.json')['airdromes']
    assert [(a['airdromeId'], a['name']) for a in aps] == [(a['airdromeId'], a['name']) for a in park]
    for a in aps:
        assert set(a) == {'airdromeId', 'name', 'icao', 'beacons'}
        assert a['icao'] is None or re.match(r'^[A-Z0-9]+$', a['icao'])
    if key in WITH_BEACONS:
        folder = en.GAME_FOLDER.get(key, key)
        assert src['beacons'] == f'DCS game files: Mods/terrains/{folder}/beacons.lua'
        assert all(isinstance(a['beacons'], list) for a in aps)
        assert isinstance(d['enroute'], list) and d['enroute']
        for a in aps:
            for b in a['beacons']:
                assert set(b) == {'type', 'callsign', 'frequency', 'channel', 'runway'}
    else:
        assert src['beacons'] is None and d['enroute'] is None
        assert all(a['beacons'] is None for a in aps)
    assert [ln for ln in raw.decode('utf-8').split('\n') if ln.startswith('{"airdromeId"')].__len__() == len(aps)


def test_committed_icao_counts():
    counts = {k: sum(1 for a in _load(PUB / f'{k}.json')['airdromes'] if a['icao']) for k in KEYS}
    assert counts['Caucasus'] == 12 and counts['PersianGulf'] == 28 and counts['Syria'] == 47 and counts['GermanyCW'] == 81
    assert sum(counts.values()) == 292


def test_soganlug_has_no_code():
    d = _load(PUB / 'Caucasus.json')
    by = {a['name']: a for a in d['airdromes']}
    assert by['Soganlug']['icao'] is None and by['Tbilisi-Lochini']['icao'] == 'UGTB'


# ------------------------------------------------- avec les echantillons

def test_beacons_match_game_files(samples, tmp_path):
    terrains = samples / 'dcs_install' / 'Mods' / 'terrains'
    for module, key in ep.THEATRES:
        if key not in WITH_BEACONS:
            continue
        terrain = ep.load_terrain(module)
        f = en.find_beacons_file(terrains, key)
        assert f is not None
        per, enr = en.beacons_data(key, terrain, f)
        d = _load(PUB / f'{key}.json')
        assert d['enroute'] == enr
        assert {a['airdromeId']: a['beacons'] for a in d['airdromes']} == per
    for key in set(KEYS) - set(WITH_BEACONS):
        assert en.find_beacons_file(terrains, key) is None
