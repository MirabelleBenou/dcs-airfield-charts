"""tools/import_gamedata.py : refus et cas nominal, sur de petites reponses fabriquees ici (aucune reponse reelle)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('import_gamedata', ROOT / 'tools' / 'import_gamedata.py')
ig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ig)

V = 'V;2.9.32.30490;getStandList=package.loaded me_parking'
STAND = 'crossroad_index={i};name={name};params.FOR_AIRPLANES=1;params.FOR_HELICOPTERS=0;params.HEIGHT=8;params.LENGTH=21;' \
        'params.WIDTH=15.25;x={x};y=-20.5'


def quote(text):
    """La valeur `text` au format %q de LuaJIT : saut de ligne echappe par `\\`, `"` et `\\` echappes."""
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\\n') + '"'


def response(tmp_path, name, lines, raw_body=None):
    p = tmp_path / name
    body = raw_body if raw_body is not None else quote('\n'.join(lines))
    p.write_bytes(('-- env gui, 2026-11-27 17:39:18\n' + body + '\n').encode('utf-8'))
    return p


def airdrome(aid, name='Base', extra=''):
    return (f'A;{aid};display_name={name};id=Base_{aid};reference_point.x={aid}000.5;reference_point.y=-{aid}00;'
            f'runways.0.end=27;runways.0.id=1;runways.0.name=09-27;runways.0.start=09;'
            f'beacons.0.beaconId=airfield{aid}_0;beacons.0.runwayId=1;beacons.0.runwaySide=27;'
            f'beacons.1.beaconId=airfield{aid}_1' + extra)


def two_slices(tmp_path):
    a = response(tmp_path, 'r1.txt', [V, 'N;2', airdrome(1, 'Abu'),
                                      'S;1;5;' + STAND.format(i=5, name='B2', x=10),
                                      'S;1;3;' + STAND.format(i=3, name='B1', x=11.125).replace('params.HEIGHT=8;', ''),
                                      'F;1;frequencyList.1.1=38950000;frequencyList.1.2=0;frequencyList.2.1=122200000;'
                                      'frequencyList.3.1=250500000;frequencyList.4.1=4025000;name=Abu'])
    # tranche 2 : un nom en \ddd (é = \195\169) ; ecrite a la main pour garder l'echappement decimal
    body = quote('\n'.join([V, 'N;2', airdrome(2, 'Damas@'), 'F;2;name=x'])).replace('@', '\\195\\169')
    b = response(tmp_path, 'r2.txt', None, raw_body=body)
    return a, b


def run(argv, capsys):
    code = ig.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


# ------------------------------------------------------------------ cas nominal

def test_nominal_two_slices(tmp_path, capsys):
    a, b = two_slices(tmp_path)
    assert '\\\n' in a.read_text(encoding='utf-8') and '\\195\\169' in b.read_text(encoding='utf-8')
    out = tmp_path / 'sub' / 'Syria.json'
    code, so, err = run(['airfields', '--theatre', 'Syria', '--out', str(out), str(a), str(b)], capsys)
    assert code == 0 and err == ''
    assert so.strip() == 'Syria: 2 airdromes, 2 stands'
    raw = out.read_bytes()
    d = json.loads(raw.decode('utf-8'))
    assert list(d) == ['format', 'theatre', 'dcs_version', 'source', 'airdromes']
    assert d['format'] == 'dcs-airfields-v1' and d['theatre'] == 'Syria' and d['dcs_version'] == '2.9.32.30490'
    assert d['source'] == ('DCS World 2.9.32.30490 mission editor (Eagle Dynamics): airfields, runways, tower radios, '
                           'beacons and parking stands; non-commercial use')
    a1, a2 = d['airdromes']
    assert (a1['id'], a1['name'], a1['x'], a1['z']) == (1, 'Abu', 1000.5, -100)
    assert a2['name'] == 'Damasé' and a2['atc'] is None                # F sans frequencyList
    assert a1['atc'] == {'vhf_low_hz': 38950000, 'vhf_high_hz': 122200000, 'uhf_hz': 250500000, 'hf_hz': 4025000}
    assert a1['runways'] == [{'id': 1, 'name': '09-27', 'start': '09', 'end': '27'}]
    assert a1['beacons'] == [{'id': 'airfield1_0', 'runway_id': 1, 'side': '27'},
                             {'id': 'airfield1_1', 'runway_id': None, 'side': None}]
    assert [s['crossroad_index'] for s in a1['stands']] == [3, 5]       # tries par crossroad_index
    s3, s5 = a1['stands']
    assert s3 == {'name': 'B1', 'crossroad_index': 3, 'x': 11.125, 'z': -20.5, 'heli': False, 'plane': True,
                  'length': 21, 'width': 15.25, 'height': None}
    assert s5['height'] == 8 and a2['stands'] == []
    lines = raw.decode('utf-8').split('\n')
    assert lines[1].startswith('{"id":1,') and lines[1].endswith(',"stands":[')
    assert lines[2].startswith('{"name":"B1"') and lines[3].startswith('{"name":"B2"')
    assert raw.endswith(b']}\n') and b'\r' not in raw
    out2 = tmp_path / 'Syria2.json'
    assert run(['airfields', '--theatre', 'Syria', '--out', str(out2), str(a), str(b)], capsys)[0] == 0
    assert out2.read_bytes() == raw                                    # deux passes, memes octets


def test_unquote_escapes():
    raw = b'-- env gui, x\n"a\\\nb\\"c\\\\d\\re\\9f\\0659\\195\\169"\n'
    assert ig.unquote(raw, 'r') == 'a\nb"c\\d\re\tfA9é'


def test_aircraft_nominal(tmp_path, capsys):
    p = response(tmp_path, 'u.txt', ['V;2.9.32.30490;me_db_api=present', 'N;2',
                                     'U;Mi-24P;heli=true;height=4.354;length=20.953;rotor_diameter=17.3;wing_span=1',
                                     'U;A-10A;heli=false;height=4.47;length=16.26;wing_span=17.53'])
    out = tmp_path / 'aircraft.json'
    code, so, _ = run(['aircraft', '--out', str(out), str(p)], capsys)
    assert code == 0 and so.strip() == 'aircraft: 2 types'
    d = json.loads(out.read_text(encoding='utf-8'))
    assert list(d) == ['format', 'dcs_version', 'source', 'aircraft']
    assert d['format'] == 'dcs-aircraft-game-v1'
    assert d['source'] == 'DCS World 2.9.32.30490 mission editor (Eagle Dynamics): aircraft sizes; non-commercial use'
    assert list(d['aircraft']) == ['A-10A', 'Mi-24P']
    assert d['aircraft']['Mi-24P'] == {'heli': True, 'length': 20.953, 'width': 17.3, 'height': 4.354}
    assert d['aircraft']['A-10A']['width'] == 17.53


# ------------------------------------------------------------------ refus

def refused(tmp_path, capsys, argv, needle):
    out = tmp_path / 'out' / 'f.json'
    code, so, err = run(argv[:1] + ['--out', str(out)] + argv[1:], capsys)
    assert code == 1 and so == ''
    assert err.startswith('ARRET : ') and needle in err, err
    assert not out.exists() and not out.parent.exists()


def af(tmp_path, *lines_list, theatre='Syria'):
    files = [str(response(tmp_path, f'r{i}.txt', lines)) for i, lines in enumerate(lines_list)]
    return ['airfields', '--theatre', theatre] + files


def test_refuses_e_line(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'E;getTerrainDATA;nil']), 'E;getTerrainDATA;nil')


def test_refuses_unknown_line(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'X;1;a=b']), 'type inconnu')
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'U;A-10A;heli=false']), 'type inconnu')


def test_refuses_v_or_n_differing(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;2', airdrome(1)], [V + 'x', 'N;2', airdrome(2)]), 'V differe')
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;2', airdrome(1)], [V, 'N;3', airdrome(2)]), 'N differe')


def test_refuses_missing_slice(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;3', airdrome(1)], [V, 'N;3', airdrome(2)]), '2 aerodromes A pour N = 3')


def test_refuses_duplicate_id(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;2', airdrome(1)], [V, 'N;2', airdrome(1, 'Autre')]), 'id 1 en double')


def test_refuses_stand_without_airdrome(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'S;7;1;' + STAND.format(i=1, name='1', x=0)]),
            'parking sans aerodrome A (id 7)')


def test_refuses_airdrome_without_name_or_arp(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1).replace('display_name=Base;', '')]), 'sans display_name')
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1).replace('reference_point.y=-100;', '')]),
            'sans reference_point.y')


@pytest.mark.parametrize('field', ig.STAND_FIELDS)
def test_refuses_stand_missing_field(tmp_path, capsys, field):
    parts = [f for f in STAND.format(i=1, name='1', x=0).split(';') if not f.startswith(field + '=')]
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'S;1;1;' + ';'.join(parts)]), f'parking sans {field}')


def test_stand_without_height_is_kept(tmp_path, capsys):
    stand = STAND.format(i=1, name='1', x=0).replace('params.HEIGHT=8;', '')
    out = tmp_path / 'f.json'
    assert run(['airfields', '--theatre', 'Caucasus', '--out', str(out)]
               + af(tmp_path, [V, 'N;1', airdrome(1), 'S;1;1;' + stand])[3:], capsys)[0] == 0
    assert json.loads(out.read_text(encoding='utf-8'))['airdromes'][0]['stands'][0]['height'] is None


def test_refuses_two_frequencies_in_one_band(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'F;1;frequencyList.1.1=122200000;frequencyList.2.1=124000000']),
            'deux frequences dans la bande vhf_high_hz')


def test_refuses_me_db_api_absent(tmp_path, capsys):
    p = response(tmp_path, 'u.txt', ['V;2.9.32.30490;me_db_api=absent', 'N;0'])
    refused(tmp_path, capsys, ['aircraft', str(p)], 'me_db_api=absent')


def test_refuses_aircraft_missing_field(tmp_path, capsys):
    p = response(tmp_path, 'u.txt', ['V;2.9.32.30490;me_db_api=present', 'N;1', 'U;Mi-24P;heli=true;height=4;length=20;wing_span=1'])
    refused(tmp_path, capsys, ['aircraft', str(p)], 'Mi-24P sans rotor_diameter')


def test_refuses_unknown_escape(tmp_path, capsys):
    p = response(tmp_path, 'r.txt', None, raw_body='"' + V + '\\\nN;1\\t"')
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'echappement inconnu \\t')
    p = response(tmp_path, 'r.txt', None, raw_body='"' + V + '\\\nN;1\\256"')
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'echappement inconnu \\256')


def test_refuses_body_not_quoted(tmp_path, capsys):
    p = response(tmp_path, 'r.txt', None, raw_body=V)
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'pas entre guillemets')
    p = response(tmp_path, 'r.txt', None, raw_body='"' + V)
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'pas entre guillemets')


def test_refuses_theatre_outside_the_three(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1)], theatre='Nevada'), "theatre 'Nevada'")
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1)], theatre='GermanyCW'), "theatre 'GermanyCW'")
