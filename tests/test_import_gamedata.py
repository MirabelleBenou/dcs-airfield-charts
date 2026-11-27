"""tools/import_gamedata.py : refus et cas nominal, sur de petites reponses fabriquees ici (aucune reponse reelle) ;
le chargeur render/gamedata.py sur les fichiers du jeu commites."""
import importlib.util
import json
from pathlib import Path

import pytest

from render import dcsdata, gamedata

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
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1).replace('reference_point.x=1000.5;', '')]),
            'sans reference_point.x')


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


def test_hook_line_after_the_string(tmp_path, capsys):
    """La ligne du crochet `-- second retour : true` : acceptee (au plus une fois) ou absente ; tout autre texte, refuse."""
    body = quote('\n'.join([V, 'N;1', airdrome(1)]))
    ok = [body, body + '\n-- second retour : true', body + '\n-- second retour : true\n']
    for i, raw in enumerate(ok):
        p = tmp_path / f'ok{i}.txt'
        p.write_bytes(('-- env gui, 2026-11-27 17:39:18\n' + raw).encode('utf-8'))
        out = tmp_path / f'ok{i}.json'
        assert run(['airfields', '--theatre', 'Syria', '--out', str(out), str(p)], capsys)[0] == 0, raw[-30:]
    bad = ['\n-- second retour : false', '\n-- second retour : true\n-- second retour : true', '\n-- autre ligne',
           '\n\n-- second retour : true', 'x']
    for i, tail in enumerate(bad):
        p = tmp_path / f'bad{i}.txt'
        p.write_bytes(('-- env gui, 2026-11-27 17:39:18\n' + body + tail + '\n').encode('utf-8'))
        refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'texte apres la chaine')


def test_refuses_other_header(tmp_path, capsys):
    p = tmp_path / 'r.txt'
    p.write_bytes(('-- env mission, 2026-11-27 17:39:18\n' + quote('\n'.join([V, 'N;1', airdrome(1)])) + '\n').encode('utf-8'))
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], "la ligne 1 n'est pas une en-tete")


def test_refuses_raw_newline_in_the_string(tmp_path, capsys):
    p = response(tmp_path, 'r.txt', None, raw_body='"' + V + '\nN;1"')
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], 'saut de ligne non echappe')


def test_refuses_incomplete_runway_or_beacon(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1).replace('runways.0.start=09;', '')]), 'piste 0 sans start')
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1).replace(';beacons.1.beaconId=airfield1_1', ';beacons.1.runwayId=1')]),
            'balise 1 sans beaconId')


def test_refuses_non_integer_frequency(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'F;1;frequencyList.1.1=122200000.5']),
            "n'est pas un nombre entier de Hz")


def test_refuses_flag_other_than_0_or_1(tmp_path, capsys):
    stand = STAND.format(i=1, name='1', x=0).replace('params.FOR_HELICOPTERS=0', 'params.FOR_HELICOPTERS=2')
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'S;1;1;' + stand]), "params.FOR_HELICOPTERS vaut '2'")


def test_refuses_duplicate_f_line(tmp_path, capsys):
    refused(tmp_path, capsys, af(tmp_path, [V, 'N;1', airdrome(1), 'F;1;name=a', 'F;1;name=b']),
            "frequences de l'aerodrome 1 en double")


def test_refuses_duplicate_aircraft_type(tmp_path, capsys):
    u = 'U;A-10A;heli=false;height=4.47;length=16.26;wing_span=17.53'
    p = response(tmp_path, 'u.txt', ['V;2.9.32.30490;me_db_api=present', 'N;2', u, u])
    refused(tmp_path, capsys, ['aircraft', str(p)], 'type A-10A en double')


UA = 'V;2.9.32.30490;me_db_api=present'
UL = 'U;A-10A;heli=false;height=4.47;length=16.26;wing_span=17.53'
S1 = 'S;1;1;' + STAND.format(i=1, name='P1', x=0)
REFUS = [   # (commande, lignes de la reponse, extrait du message)
    ('airfields', [V, 'N;1', airdrome(1), 'F;7;frequencyList.1.1=122200000'], 'frequences sans aerodrome A (id 7)'),
    ('airfields', [V, 'N;1', airdrome(1), 'S;1'], 'ligne S incomplete'),
    ('aircraft', [UA, 'N;1', 'U;A-10A'], 'ligne U incomplete'),
    ('airfields', [V, 'N;1', airdrome(1).replace('runways.0.', 'runways.1.')], 'indices de pistes discontinus'),
    ('airfields', [V, 'N;1', airdrome(1).replace('beacons.1.', 'beacons.2.')], 'indices de balises discontinus'),
    ('airfields', ['V', 'N;1', airdrome(1)], 'V sans version'),
    ('airfields', ['V;', 'N;1', airdrome(1)], 'V sans version'),
    ('aircraft', [UA, 'N;1', UL.replace('heli=false', 'heli=yes')], "heli vaut 'yes'"),
    ('aircraft', [UA, 'N;1', UL.replace('heli=false;', '')], 'heli vaut None'),
    ('airfields', [V, 'N;x', airdrome(1)], "N n'est pas un entier"),
    ('airfields', [V, 'N;1', airdrome(1) + ';sanségal'], 'champ sans « = »'),
    ('airfields', [V, 'N;1', airdrome(1).replace('beacons.0.runwayId=1', 'beacons.0.runwayId=1.5')],
     "beacons.0.runwayId n'est pas un entier"),
    ('airfields', [V, 'N;1', airdrome(1).replace('runways.0.id=1', 'runways.0.id=x')], "runways.0.id n'est pas un entier"),
    ('airfields', [V, 'N;1', 'A;x;display_name=B;reference_point.x=1;reference_point.y=2'], "l'id de l'aerodrome n'est pas un entier"),
    ('airfields', [V, 'N;1', airdrome(1).replace('reference_point.x=1000.5', 'reference_point.x=abc')],
     "reference_point.x n'est pas un nombre"),
    ('airfields', [V, 'N;1', airdrome(1), S1.replace('params.LENGTH=21', 'params.LENGTH=21m')], "params.LENGTH n'est pas un nombre"),
    ('airfields', [V, airdrome(1)], 'ligne N absente'),
    ('airfields', ['N;1', airdrome(1)], 'ligne V absente'),
    ('aircraft', [UA, 'N;2', UL], '1 types U pour N = 2'),
    ('airfields', [V, 'N;1', airdrome(1).replace('runways.0.start=09', 'runways.0.start=')], "start '' vide ou sans chiffre de tete"),
    ('airfields', [V, 'N;1', airdrome(1).replace('runways.0.end=27', 'runways.0.end=L')], "end 'L' vide ou sans chiffre de tete"),
    ('airfields', [V, 'N;1', airdrome(1), S1, S1.replace('name=P1', 'name=P2')],
     'aerodrome 1 : deux parkings au crossroad_index 1 (P1 et P2)'),
]


@pytest.mark.parametrize('cmd,lines,needle', REFUS, ids=[f'{i}-{c[2][:30]}' for i, c in enumerate(REFUS)])
def test_refuses_each_other_case(tmp_path, capsys, cmd, lines, needle):
    p = response(tmp_path, 'r.txt', lines)
    argv = [cmd] + (['--theatre', 'Syria'] if cmd == 'airfields' else []) + [str(p)]
    refused(tmp_path, capsys, argv, needle)


def test_refuses_invalid_utf8(tmp_path, capsys):
    body = quote('\n'.join([V, 'N;1', airdrome(1, 'B@')])).replace('@', '\\255')
    p = response(tmp_path, 'r.txt', None, raw_body=body)
    refused(tmp_path, capsys, ['airfields', '--theatre', 'Syria', str(p)], "pas de l'UTF-8")


def test_collect_without_response():
    with pytest.raises(ig.GameDataError, match='aucune reponse'):
        ig.collect([], ('A',))


def test_write_is_atomic_and_an_oserror_stops(tmp_path, capsys, monkeypatch):
    a, b = two_slices(tmp_path)
    out = tmp_path / 'game' / 'Syria.json'
    out.parent.mkdir()
    out.write_bytes(b'ancien')
    real = ig.os.replace

    def boom(src, dst):
        raise OSError('disque plein')
    monkeypatch.setattr(ig.os, 'replace', boom)
    code, so, err = run(['airfields', '--theatre', 'Syria', '--out', str(out), str(a), str(b)], capsys)
    assert code == 1 and so == '' and err.startswith('ARRET : ') and 'disque plein' in err
    assert out.read_bytes() == b'ancien' and sorted(p.name for p in out.parent.iterdir()) == ['Syria.json']   # rien a moitie
    monkeypatch.setattr(ig.os, 'replace', real)
    blocker = tmp_path / 'fichier'
    blocker.write_bytes(b'x')
    code, _, err = run(['airfields', '--theatre', 'Syria', '--out', str(blocker / 'sous' / 'f.json'), str(a), str(b)], capsys)
    assert code == 1 and err.startswith('ARRET : ')                           # dossier impossible a creer : OSError
    assert run(['airfields', '--theatre', 'Syria', '--out', str(out), str(a), str(b)], capsys)[0] == 0
    assert json.loads(out.read_text(encoding='utf-8'))['theatre'] == 'Syria'
    assert sorted(p.name for p in out.parent.iterdir()) == ['Syria.json']


# ------------------------------------------------------------------ le chargeur (render/gamedata.py)


# Faits de la base (pydcs 84f6335), recopies : radios, pistes dans l'ordre, caps, balises des bouts de piste ; parkings :
# nombre, premier et dernier, empreinte sha256 de la liste [nom, x, z, heli seul, avions, longueur, largeur] (en flottants).
BASE_SYRIA = {
    'Abu al-Duhur': dict(
        id=1, x=76048.957031, z=111344.925781,
        atc={'vhf_high_hz': 122200000, 'uhf_hz': 250500000, 'vhf_low_hz': 38950000, 'hf_hz': 4025000},
        approaches=[('27', 270.0), ('09', 90.0)], beacons=[[('27', []), ('09', [])]],
        n_slots=36, first=['26', 76271.684679983, 112805.5380375, False, True, 40.0, 40.0],
        last=['30', 76293.3046875, 113035.640625, False, True, 18.449999, 13.049999],
        sha='538d800434927aa07542b4631983256d36b318ef2500015f7f6bf7f8ce3e6b7e'),
    'Incirlik': dict(
        id=16, x=221207.773438, z=-35240.347656,
        atc={'vhf_high_hz': 122100000, 'uhf_hz': 360100000, 'vhf_low_hz': 38750000, 'hf_hz': 3925000},
        approaches=[('05', 50.0), ('23', 230.0)],
        beacons=[[('05', ['airfield16_1', 'airfield16_2']), ('23', ['airfield16_3', 'airfield16_4'])]],
        n_slots=126, first=['49', 221610.19207214, -35769.237346894, False, True, 26.0, 22.0],
        last=['126', 220739.34375, -34893.79296875, False, True, 26.0, 24.0],
        sha='20e32d7cdaf70457b69ee344ee4caabd5806562da6be27e602305b155429233b'),
    'Damascus': dict(
        id=7, x=-178652.320313, z=52081.296875,
        atc={'vhf_high_hz': 118500000, 'uhf_hz': 253250000, 'vhf_low_hz': 41700000, 'hf_hz': 4900000},
        approaches=[('05R', 50.0), ('23L', 230.0), ('05L', 50.0), ('23R', 230.0)],
        beacons=[[('05R', ['airfield7_3', 'airfield7_6']), ('23L', [])], [('05L', []), ('23R', ['airfield7_1', 'airfield7_0'])]],
        n_slots=71, first=['027', -182109.68730281, 50138.411195125, False, True, 26.0, 24.0],
        last=['410', -180626.328125, 51986.86328125, False, True, 26.0, 24.0],
        sha='a1c0af0bb7b0b8157325c891fc452eb52ac808059c79246174cbc0a492eab712'),
}


@pytest.mark.parametrize('name', sorted(BASE_SYRIA))
def test_loader_facts_equal_the_base(name):
    import hashlib
    want = BASE_SYRIA[name]
    ap = dcsdata.load_terrain('Syria').airports[name]
    f = dcsdata.airport_facts(ap)
    assert (f.id, f.name, f.x, f.z) == (want['id'], name, want['x'], want['z'])
    assert f.atc == want['atc']
    assert [(a.name, a.heading) for a in f.approaches] == want['approaches']
    assert [[(e.name, [b.id for b in e.beacons]) for e in (r.main, r.opposite)] for r in ap.runways] == want['beacons']
    slots = [[s.name, float(s.x), float(s.z), s.heli_only, s.airplanes, float(s.length), float(s.width)] for s in f.slots]
    assert len(slots) == want['n_slots'] and slots[0] == want['first'] and slots[-1] == want['last']
    assert hashlib.sha256(json.dumps(slots).encode()).hexdigest() == want['sha']


def test_loader_terrain():
    t = dcsdata.load_terrain('syria')
    assert t.name == 'Syria' and t.dcs_version == '2.9.32.30490' and len(t.airports) == 224
    assert [a.id for a in t.airports.values()] == sorted(a.id for a in t.airports.values())
    assert (t.bounds.top, t.bounds.left, t.bounds.bottom, t.bounds.right) == (-320000, -579986, 300000, 579998)
    g = dcsdata.load_terrain('GermanyColdWar')
    assert g.name == 'GermanyColdWar' and len(g.airports) == 247
    assert (g.bounds.top, g.bounds.left, g.bounds.bottom, g.bounds.right) == (200000, -600000, -1100000, -300000)
    c = dcsdata.load_terrain('Caucasus')
    assert (c.bounds.top, c.bounds.left, c.bounds.bottom, c.bounds.right) == (380000, -560000, -600000, 1130000)
    assert sum(1 for a in c.airports.values() for s in a.parking_slots if s.height is None) == 68
    assert dcsdata.load_terrain('Syria').airports['Incirlik'] is not t.airports['Incirlik']    # objets neufs


def test_loader_order_rules(tmp_path, monkeypatch):
    """Pistes et balises dans l'ordre de pydcs (1..n-1, puis 0) ; une piste d'id -1 n'a ni id ni balise ; cap = chiffres de
    tete x 10 ; parkings par crossroad_index."""
    a = {'id': 5, 'name': 'B', 'x': 1, 'z': 2, 'atc': None,
         'runways': [{'id': 1, 'name': '09-27', 'start': '09', 'end': '27'},
                     {'id': -1, 'name': '05L-23R', 'start': '05L', 'end': '23R'},
                     {'id': 3, 'name': '18-36', 'start': '18', 'end': '36'}],
         'beacons': [{'id': 'b0', 'runway_id': 1, 'side': '27'}, {'id': 'b1', 'runway_id': 1, 'side': '27'},
                     {'id': 'b2', 'runway_id': -1, 'side': '05L'}, {'id': 'b3', 'runway_id': None, 'side': None},
                     {'id': 'b4', 'runway_id': 1, 'side': '27'}],
         'stands': [{'name': 'S2', 'crossroad_index': 9, 'x': 1, 'z': 2, 'heli': True, 'plane': False, 'length': 10,
                     'width': 11, 'height': None},
                    {'name': 'S1', 'crossroad_index': 4, 'x': 3, 'z': 4, 'heli': False, 'plane': True, 'length': 20,
                     'width': 21, 'height': 5}]}
    doc = {'format': 'dcs-airfields-v1', 'theatre': 'Caucasus', 'dcs_version': '9.9', 'source': 's', 'airdromes': [a]}
    (tmp_path / 'Caucasus.json').write_text(json.dumps(doc), encoding='utf-8')
    monkeypatch.setattr(gamedata, 'GAME_DIR', tmp_path)
    gamedata._document.cache_clear()
    try:
        ap = gamedata.load_terrain('Caucasus').airports['B']
    finally:
        gamedata._document.cache_clear()
    assert [(r.id, r.main.name, r.opposite.name) for r in ap.runways] == [(None, '05L', '23R'), (3, '18', '36'), (1, '09', '27')]
    assert [(r.main.heading, r.opposite.heading) for r in ap.runways] == [(50, 230), (180, 360), (90, 270)]
    assert [b.id for b in ap.runways[2].opposite.beacons] == ['b1', 'b4', 'b0']
    assert ap.runways[0].main.beacons == [] and ap.runways[2].main.beacons == []
    assert ap.atc_radio is None and (ap.position.x, ap.position.y) == (1.0, 2.0)
    s1, s2 = ap.parking_slots
    assert (s1.slot_name, s1.crossroad_idx, s1.helicopter, s1.airplanes, s1.length, s1.width, s1.height) == \
        ('S1', 4, False, True, 20.0, 21.0, 5.0)
    assert (s2.slot_name, s2.position.x, s2.position.y, s2.height) == ('S2', 1.0, 2.0, None)


def test_loader_atc_radio():
    atc = dcsdata.load_terrain('Syria').airports['Abu al-Duhur'].atc_radio
    assert (atc.vhf_low_hz, atc.vhf_high_hz, atc.uhf_hz, atc.hf_hz) == (38950000, 122200000, 250500000, 4025000)


def test_loader_refuses_other_theatre_or_missing_file(tmp_path, monkeypatch):
    for name in ('Nevada', 'PersianGulf', 'GermanyCW'):
        with pytest.raises(dcsdata.DataError, match=name):
            dcsdata.load_terrain(name)
    monkeypatch.setattr(gamedata, 'GAME_DIR', tmp_path)
    gamedata._document.cache_clear()
    try:
        with pytest.raises(dcsdata.DataError, match='Syria'):
            dcsdata.load_terrain('Syria')
    finally:
        gamedata._document.cache_clear()


def test_committed_game_files_are_the_import_format():
    for t, n_ap, n_st in (('GermanyColdWar', 247, 7724), ('Syria', 224, 3976), ('Caucasus', 21, 900)):
        raw = (gamedata.GAME_DIR / f'{t}.json').read_bytes()
        assert raw.endswith(b']}\n') and b'\r' not in raw
        d = json.loads(raw.decode('utf-8'))
        assert d['format'] == 'dcs-airfields-v1' and d['theatre'] == t and d['dcs_version'] == '2.9.32.30490'
        assert len(d['airdromes']) == n_ap and sum(len(a['stands']) for a in d['airdromes']) == n_st
        assert json.loads(ig.airfields_text(d)) == d and ig.airfields_text(d).encode('utf-8') == raw
    a = json.loads((gamedata.GAME_DIR / 'aircraft.json').read_text(encoding='utf-8'))
    assert a['format'] == 'dcs-aircraft-game-v1' and len(a['aircraft']) == 172
    assert ig.aircraft_text(a) == (gamedata.GAME_DIR / 'aircraft.json').read_text(encoding='utf-8')
