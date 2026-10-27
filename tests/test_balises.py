"""Lot balises : ILS rattaches par la geometrie, frequence nulle, balises `world_` proches, noms de piste (cas synthetiques,
aucun echantillon)."""
from types import SimpleNamespace

import pytest

from render import analysis, dcsdata, naming


def end(name, az, pos=(0.0, 0.0)):
    return analysis.End(name, az, az, pos, 0)


def loc(direction, x, z, freq=110.7e6, cs='IKK', typ='ILS_LOCALIZER', bid='airfield1_1'):
    return dcsdata.Beacon(bid, typ, cs, freq, None, x, z, direction)


def glide(freq=110.7e6, bid='airfield1_2'):
    return dcsdata.Beacon(bid, 'ILS_GLIDESLOPE', '', freq, None, 0.0, 0.0, 0.0)


# Extremite en (0, 0), atterrissage vers +z (azimut grille 90) ; localizer 3 km plus loin, antenne tournee vers l'approche (270).
def test_localizer_attached_to_the_right_end_with_its_glide():
    e90, e270 = end('09', 90.0), end('27', 270.0, (0.0, 3000.0))
    res = analysis.attach_ils([e90, e270], [loc(270.0, 40.0, 3100.0), glide()])
    assert e90.ils == 'IKK 110.70' and e270.ils is None
    assert res[0]['end'] is e90 and res[0]['lateral_m'] == pytest.approx(40.0)


def test_localizer_without_glide_is_loc():
    e = end('09', 90.0)
    analysis.attach_ils([e], [loc(270.0, 0.0, 3000.0)])
    assert e.ils == 'IKK 110.70 (LOC)'


def test_localizer_glide_frequency_must_match():
    e = end('09', 90.0)
    analysis.attach_ils([e], [loc(270.0, 0.0, 3000.0), glide(freq=111.1e6)])
    assert e.ils == 'IKK 110.70 (LOC)'


def test_localizer_empty_callsign_is_frequency_only():
    e = end('09', 90.0)
    analysis.attach_ils([e], [loc(270.0, 0.0, 3000.0, cs=''), glide()])
    assert e.ils == '110.70'


def test_localizer_refused_at_11_degrees_accepted_at_9():
    e = end('09', 90.0)
    analysis.attach_ils([e], [loc(270.0 + 11.0, 0.0, 3000.0)])
    assert e.ils is None
    analysis.attach_ils([e], [loc(270.0 + 9.0, 0.0, 3000.0)])
    assert e.ils is not None


def test_localizer_course_wraps_around_north():
    e = end('36', 359.0, (0.0, 0.0))
    analysis.attach_ils([e], [loc(185.0, 3000.0, 0.0)])           # cap d'approche 5 deg : a 6 deg de 359
    assert e.ils is not None


def test_localizer_refused_at_151_m_accepted_at_149_m():
    e = end('09', 90.0)
    res = analysis.attach_ils([e], [loc(270.0, 151.0, 3000.0)])
    assert e.ils is None and res[0]['end'] is None
    analysis.attach_ils([e], [loc(270.0, -149.0, 3000.0)])
    assert e.ils is not None


def test_localizer_goes_to_the_closest_axis_of_two_parallel_ends():
    a, b = end('09L', 90.0, (-100.0, 0.0)), end('09R', 90.0, (100.0, 0.0))
    analysis.attach_ils([a, b], [loc(270.0, 70.0, 3000.0)])          # a 170 m de l'axe de a (refuse), a 30 m de celui de b
    assert a.ils is None and b.ils is not None
    analysis.attach_ils([a, b], [loc(270.0, -60.0, 3000.0)])         # a 40 m de a, 160 m de b
    assert a.ils is not None and b.ils is None


def test_zero_frequency_localizer_is_ignored():
    e = end('35R', 90.0)
    res = analysis.attach_ils([e], [loc(270.0, 0.0, 3000.0, freq=0.0, cs='LTK'), loc(270.0, 0.0, 3100.0, freq=1e6, cs='XX')])
    assert e.ils is None and res == []


def test_other_beacon_types_are_not_ils():
    e = end('09', 90.0)
    analysis.attach_ils([e], [loc(270.0, 0.0, 3000.0, typ='VOR_DME')])
    assert e.ils is None


def test_two_localizers_on_one_end_first_in_file_order_stays():
    e = end('09', 90.0)
    res = analysis.attach_ils([e], [loc(270.0, 20.0, 3000.0, cs='AAA', freq=109.1e6), loc(270.0, 10.0, 3000.0, cs='BBB', freq=111.1e6)])
    assert e.ils == 'AAA 109.10 (LOC)'
    assert [r['duplicate'] for r in res] == [False, True]


# ---------- balises world_ ----------
def ap(i, x, y):
    return SimpleNamespace(id=i, position=SimpleNamespace(x=x, y=y))


def world(bid, x, z, typ='AIRPORT_HOMER', cs='KHM', freq=374000.0):
    return dcsdata.Beacon(bid, typ, cs, freq, None, x, z, 0.0)


def test_world_beacon_at_200_m_is_kept_and_at_5100_m_dropped():
    airports = [ap(1, 0.0, 0.0), ap(2, 50000.0, 0.0)]
    beacons = {'world_1': world('world_1', 200.0, 0.0), 'world_2': world('world_2', 5100.0, 0.0, cs='FAR'),
               'world_3': world('world_3', 5000.0, 0.0, cs='LIM'), 'airfield1_1': world('airfield1_1', 10.0, 0.0, cs='OWN')}
    got = dcsdata.world_beacons_near(beacons, airports, 1)
    assert [(b.callsign, round(d)) for b, d in got] == [('KHM', 200), ('LIM', 5000)]
    assert dcsdata.world_beacons_near(beacons, airports, 2) == []


def test_world_beacon_belongs_to_the_nearest_airport_only():
    airports = [ap(1, 0.0, 0.0), ap(2, 3000.0, 0.0)]
    beacons = {'world_1': world('world_1', 1000.0, 0.0)}
    assert len(dcsdata.world_beacons_near(beacons, airports, 1)) == 1
    assert dcsdata.world_beacons_near(beacons, airports, 2) == []


def test_world_beacon_uses_x_and_z_of_the_beacon():
    airports = [ap(1, 0.0, 0.0)]
    beacons = {'world_1': world('world_1', 0.0, 4000.0)}             # beacons.lua : position = {x, altitude, z}
    assert len(dcsdata.world_beacons_near(beacons, airports, 1)) == 1


def test_world_beacons_join_the_banner_without_duplicates_in_order():
    own = [dcsdata.Beacon('airfield1_1', 'VOR_DME', 'KHM', 113.9e6, 86, 0.0, 0.0, 0.0)]
    w = [world('world_1', 0.0, 0.0), world('world_2', 0.0, 0.0, typ='VOR_DME', cs='KHM', freq=113.9e6)]
    w[1].channel = 86
    out = analysis.with_world_beacons(own, w)
    assert [(b.kind, b.callsign, b.text) for b in out] == [('VOR/DME', 'KHM', '113.90 CH86X'), ('NDB', 'KHM', '374')]
    w2 = [world('world_1', 0.0, 0.0), world('world_3', 0.0, 0.0, typ='ILS_LOCALIZER', cs='ZZ')]
    assert [b.kind for b in analysis.with_world_beacons(own, w2)] == ['VOR/DME', 'NDB']         # un localizer n'est pas une balise du bandeau


# ---------- beacons.lua ----------
LUA = """beacons = {
	{
		beaconId = 'world_0';
		type = BEACON_TYPE_AIRPORT_HOMER;
		callsign = 'BAN';
		frequency = 304000.000000;
		position = { 22735.679688, 7.987951, 5935.380859 };
		direction = 0.000000;
	};
	{
		beaconId = 'airfield5_1';
		type = BEACON_TYPE_ILS_LOCALIZER;
		callsign = 'IKK';
		frequency = 110700000.000000;
		position = { -1.5, 7.0, 2.5 };
		direction = 183.5;
	};
	{
		beaconId = 'airfield5_2';
		type = BEACON_TYPE_VOR;
		callsign = 'NOD';
		frequency = 112000000.000000;
		position = { 1.0, 7.0, 2.0 };
	};
}
"""


def test_parse_beacons_lua_reads_world_beacons_and_direction(tmp_path):
    p = tmp_path / 'beacons.lua'
    p.write_text(LUA, encoding='utf-8')
    b = dcsdata.parse_beacons_lua(p)
    assert set(b) == {'world_0', 'airfield5_1', 'airfield5_2'}
    assert b['airfield5_1'].direction == pytest.approx(183.5) and b['airfield5_2'].direction == 0.0
    assert (b['world_0'].x, b['world_0'].z) == pytest.approx((22735.679688, 5935.380859))


# ---------- noms ----------
def test_solo_end_keeps_the_pydcs_letter():
    from tests.test_naming import rw
    app = [('06L', 60.0), ('24R', 240.0)]
    out = naming.name_ends([rw(0, 0, 60)], app)
    assert [e[0] for e in out[0]] == ['06L', '24R']


def test_solo_end_without_letter_stays_plain_and_shared_numbers_keep_geometry():
    from tests.test_naming import rw, APP
    assert [e[0] for e in naming.name_ends([rw(0, 0, 14)], APP)[0]] == ['01', '19']
    app = [('06L', 60.0), ('24R', 240.0), ('06R', 60.0), ('24L', 240.0)]
    out = naming.name_ends([rw(0, 0, 60), rw(-300, 520, 60)], app)
    assert sorted(e[0] for r in out for e in r) == ['06L', '06R', '24L', '24R']
