"""fetch_osm sans reseau : conversion, serveurs et reprises, reprise d'une passe et --force (critere C11)."""
import json
import re
import urllib.error

import pytest

from render import fetch_osm as F

RESPONSE = {'version': 0.6, 'elements': [
    {'type': 'way', 'id': 1, 'tags': {'aeroway': 'taxiway', 'ref': 'A1'}, 'geometry': [{'lat': 1.12345678, 'lon': 2.0}, {'lat': 1.2, 'lon': 2.1}]},
    {'type': 'way', 'id': 2, 'tags': {'aeroway': 'taxiway'}, 'geometry': [{'lat': 1.0, 'lon': 2.0}]},            # sans ref
    {'type': 'way', 'id': 3, 'tags': {'aeroway': 'apron', 'ref': 'X'}, 'geometry': [{'lat': 1.0, 'lon': 2.0}]},  # pas un taxiway
    {'type': 'node', 'id': 4, 'lat': 1.5, 'lon': 2.5, 'tags': {'aeroway': 'aerodrome', 'icao': 'FAR'}},
    {'type': 'node', 'id': 5, 'lat': 1.1, 'lon': 2.05, 'tags': {'aeroway': 'aerodrome', 'icao': 'NEAR'}},
]}


def test_convert_keeps_refs_rounds_and_picks_nearest_aerodrome():
    icao, ways = F.convert(RESPONSE, (1.1, 2.05))
    assert icao == 'NEAR'
    assert ways == [{'aeroway': 'taxiway', 'ref': 'A1', 'pts': [[1.123457, 2.0], [1.2, 2.1]]}]
    assert F.convert({'elements': []}, (0, 0)) == (None, [])


def test_query_text():
    q = F.build_query(37.1, 32.2, 37.3, 32.4)
    assert q == ('[out:json][timeout:60];(way["aeroway"~"^(taxiway|runway)$"]["ref"](37.100000,32.200000,37.300000,32.400000);'
                 'nwr["aeroway"="aerodrome"]["icao"](37.100000,32.200000,37.300000,32.400000););out tags geom;')


def test_retry_waits_then_next_server():
    calls, waits = [], []

    def post(url, q):
        calls.append(url)
        return (503, b'') if url == F.SERVERS[0] else (200, b'{"elements": []}')
    resp, server = F.overpass_fetch('q', F.SERVERS, post, waits.append)
    assert server == F.SERVERS[1] and resp == {'elements': []}
    assert calls == [F.SERVERS[0]] * 5 + [F.SERVERS[1]]                    # 1 essai + 4 reprises, puis serveur suivant
    assert waits == [2, 4, 8, 16]


def test_timeout_is_retried_and_total_failure_raises():
    def post(url, q):
        raise TimeoutError()
    waits = []
    with pytest.raises(F.FetchError):
        F.overpass_fetch('q', F.SERVERS, post, waits.append)
    assert waits == [2, 4, 8, 16] * 3


def test_other_http_error_goes_straight_to_next_server():
    seen = []

    def post(url, q):
        seen.append(url)
        return (400, b'') if url == F.SERVERS[0] else (200, b'{"elements": []}')
    F.overpass_fetch('q', F.SERVERS, post, lambda s: None)
    assert seen == [F.SERVERS[0], F.SERVERS[1]]


def test_dumps_format():
    s = F.dumps({'a': [1.5, 'é']})
    assert s == '{\n"a": [\n1.5,\n"é"\n]\n}' and not s.endswith('\n')


def _strip_fetched(text):
    return re.sub(r'"fetched": "[^"]*",?\n', '', text)


@pytest.fixture
def sample_work(samples, tmp_path):
    (tmp_path / 'out').mkdir()
    try:
        (tmp_path / 'out' / 'syria').symlink_to(samples / 'syria', target_is_directory=True)
    except OSError:
        pytest.skip('liens symboliques impossibles')
    return tmp_path


def test_recorded_konya_response_gives_the_sample_file(samples, sample_work):
    """C11 : la reponse enregistree de Konya donne Konya.json a l'identique (hors `fetched`)."""
    fixture = (samples / 'syria' / 'osm' / 'Konya.overpass.json').read_bytes()
    calls = []

    def post(url, q):
        calls.append(q)
        return 200, fixture
    report = F.run('Syria', sample_work, wanted=['Konya'], post=post, sleep=lambda s: None)
    assert report['bases']['Konya']['status'] == 'ok' and report['bases']['Konya']['icao'] == 'LTAN'
    out = (sample_work / 'osm' / 'syria' / 'Konya.json').read_text(encoding='utf-8')
    expect = (samples / 'syria' / 'osm' / 'Konya.json').read_text(encoding='utf-8')
    assert _strip_fetched(out) == _strip_fetched(expect)
    assert re.search(r'"fetched": "\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"', out)
    assert len(calls) == 1 and 'out tags geom' in calls[0]
    run = json.loads((sample_work / 'osm' / 'syria' / 'run.json').read_text(encoding='utf-8'))
    assert run['bases']['Konya']['status'] == 'ok'


def test_resume_skips_and_force_refetches(samples, sample_work):
    fixture = (samples / 'syria' / 'osm' / 'Konya.overpass.json').read_bytes()
    n = []

    def post(url, q):
        n.append(1)
        return 200, fixture
    kw = dict(wanted=['Konya'], post=post, sleep=lambda s: None, log=lambda *a: None)
    F.run('Syria', sample_work, **kw)
    r2 = F.run('Syria', sample_work, **kw)
    assert len(n) == 1 and r2['bases']['Konya'] == {'status': 'skipped', 'reason': 'already fetched'}
    F.run('Syria', sample_work, force=True, **kw)
    assert len(n) == 2


def test_failure_does_not_stop_the_pass(samples, sample_work):
    fixture = (samples / 'syria' / 'osm' / 'Konya.overpass.json').read_bytes()

    def post(url, q):
        if '37.9' in q or '37.8' in q or '38.' in q:                       # Konya
            return 200, fixture
        raise urllib.error.URLError('hors ligne')
    report = F.run('Syria', sample_work, wanted=['Adana', 'Konya'], post=post, sleep=lambda s: None, log=lambda *a: None)
    assert report['bases']['Adana']['status'] == 'failed'
    assert report['bases']['Konya']['status'] == 'ok'
    assert not (sample_work / 'osm' / 'syria' / 'Adana.json').exists()
