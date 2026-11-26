"""Lot ac-corrections : la frequence d'une NDB que DCS donne mille fois trop haute (lecture de beacons.lua, retouche 9 de republish)."""
import re

import pytest

from render import analysis, dcsdata, republish


def _old_parse(path):
    """La lecture de `main` avant le lot (sans la division), pour comparer balise par balise."""
    text = dcsdata.Path(path).read_text(encoding='utf-8', errors='replace')
    out = {}
    for blk in re.split(r'\n\t\{\n', text):
        m = dcsdata._BLOCK.search(blk)
        if not m:
            continue

        def grab(pattern, cast=str):
            mm = re.search(pattern, blk)
            return cast(mm.group(1)) if mm else None
        typ = grab(r'type\s*=\s*BEACON_TYPE_([A-Z_0-9]+)')
        pos = re.search(r'position\s*=\s*\{\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\}', blk)
        if typ is None or pos is None:
            continue
        out[m.group(1)] = dcsdata.Beacon(m.group(1), typ, grab(r"callsign\s*=\s*'([^']*)'") or '',
                                         grab(r'frequency\s*=\s*([-\d.eE+]+)', float) or 0.0, grab(r'channel\s*=\s*(\d+)', int),
                                         float(pos.group(1)), float(pos.group(3)), grab(r'direction\s*=\s*([-\d.eE+]+)', float) or 0.0)
    return out


def _lua(*blocks):
    return 'beacons = {\n' + ''.join(
        f"\t{{\n\t\tbeaconId = '{i}';\n\t\ttype = BEACON_TYPE_{t};\n\t\tcallsign = 'ABC';\n\t\tfrequency = {fr};\n"
        f"\t\tposition = {{ 1.0, 2.0, 3.0 }};\n\t}},\n" for i, t, fr in blocks) + '}\n'


def test_ndb_types_constant():
    assert dcsdata.NDB_TYPES == ('AIRPORT_HOMER', 'AIRPORT_HOMER_WITH_MARKER', 'HOMER')


def test_reading_on_small_texts(tmp_path):
    f = tmp_path / 'beacons.lua'
    f.write_text(_lua(('airfield1_0', 'AIRPORT_HOMER', '320000000'), ('airfield1_1', 'AIRPORT_HOMER_WITH_MARKER', '2000000'),
                      ('airfield1_2', 'HOMER', '2500000'), ('airfield1_3', 'AIRPORT_HOMER', '395000'),
                      ('airfield1_4', 'AIRPORT_HOMER', '1999999'), ('airfield1_5', 'VOR_DME', '113700000'),
                      ('airfield1_6', 'ILS_LOCALIZER', '110300000'), ('airfield1_7', 'TACAN', '2500000'),
                      ('airfield1_8', 'BROADCAST_STATION', '620000000')), encoding='utf-8')
    got = {k: b.frequency for k, b in dcsdata.parse_beacons_lua(f).items()}
    assert got == {'airfield1_0': 320000.0, 'airfield1_1': 2000.0, 'airfield1_2': 2500.0, 'airfield1_3': 395000.0,
                   'airfield1_4': 1999999.0, 'airfield1_5': 113700000.0, 'airfield1_6': 110300000.0, 'airfield1_7': 2500000.0,
                   'airfield1_8': 620000000.0}


def test_reading_on_the_samples(samples):
    terrains = samples / 'dcs_install' / 'Mods' / 'terrains'
    files = sorted(f for d in terrains.iterdir() for f in d.iterdir() if f.name.lower() == 'beacons.lua')      # Caucasus : `Beacons.lua`
    assert len(files) == 3
    for f in files:
        new, old = dcsdata.parse_beacons_lua(f), _old_parse(f)
        assert list(new) == list(old)
        diff = [k for k in new if new[k] != old[k]]
        if f.parent.name == 'Syria':
            assert diff == [k for k in new if new[k].callsign == 'CKR' and new[k].type in dcsdata.NDB_TYPES] and len(diff) == 1
            ckr = new[diff[0]]
            assert ckr.frequency == 320000.0 and old[diff[0]].frequency == 320000000.0
            assert analysis.banner_beacons([ckr])[0].text == '320'
        else:
            assert diff == []


def test_retouch_9_examples():
    svg = ('<g><text x="1" y="2">CKR 320000</text><text x="1" y="2">113.70 · 320000</text><text>320000</text>'
           '<text class="a">1250</text><text>113.70</text><text>CH45X</text><text>100000</text><text>99000</text>'
           '<text>100500</text><text>0320000</text><text>٣٢٠٠٠٠</text><text>320000 M</text><text></text><circle r="320000"/></g>')
    got, n = republish.retouch_ndb_freq(svg)
    want = ('<g><text x="1" y="2">CKR 320</text><text x="1" y="2">113.70 · 320</text><text>320</text>'
            '<text class="a">1250</text><text>113.70</text><text>CH45X</text><text>100</text><text>99000</text>'
            '<text>100500</text><text>320</text><text>٣٢٠٠٠٠</text><text>320 M</text><text></text><circle r="320000"/></g>')
    assert got == want and n == 6


def test_retouch_9_idempotent_and_identity():
    svg = '<g><text x="1">CKR 320000</text><text>ELEV 3407 FT · 1038 M</text></g>'
    once, n = republish.retouch_ndb_freq(svg)
    assert n == 1 and once == '<g><text x="1">CKR 320</text><text>ELEV 3407 FT · 1038 M</text></g>'
    assert republish.retouch_ndb_freq(once) == (once, 0)
    plain = '<g><text>1250</text><text>113.70 CH45X</text><text>A  B</text></g>'
    assert republish.retouch_ndb_freq(plain) == (plain, 0)


def test_retouch_ndb_text_on_extracted_pdf_text():
    assert republish.retouch_ndb_text('CKR 320000\n113.70 · 320000\n1250 CH45X\n') == 'CKR 320\n113.70 · 320\n1250 CH45X\n'
    assert republish.retouch_ndb_text('ELEV 3407 FT') == 'ELEV 3407 FT'
    assert republish.retouch_ndb_text('NDB\n320000320000\n30003000\n') == 'NDB\n320320\n30003000\n'
