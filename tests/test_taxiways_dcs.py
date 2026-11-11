"""Moteur : les noms de taxiways tires du jeu (<work_dir>/taxiways_dcs/<theatre>/<Base>.json, ecrit par `attribuer`) remplacent
ceux du fichier OSM de la base, base par base. Les tests de read_osm travaillent sur un revetement factice ; ceux d'`analyze`
demandent les echantillons (DCS_CHARTS_SAMPLES)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from render import analysis
from render.analysis import ChartError


# ---------- read_osm sur un revetement factice : x de 0 a 200 vers le haut de l'image, z de 0 a 200 vers la droite, 1 m/px ----------
class IdentityProj:
    def to_xz(self, lat, lon):
        return lat, lon


@pytest.fixture
def pavement():
    pav = np.zeros((200, 200), bool)
    pav[80:120, :] = True                                   # une bande de revetement : x de 80 a 120 (lignes 80 a 119)
    g = SimpleNamespace(x_top=200.0, z_left=0.0, mpp=1.0, width=200, height=200)
    return pav, g


def way(ref, x, aeroway='taxiway'):
    return {'aeroway': aeroway, 'ref': ref, 'pts': [[x, 10.0], [x, 90.0], [x, 150.0]]}


def write(path, doc):
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def test_read_osm_without_a_game_file_is_unchanged(tmp_path, pavement):
    pav, g = pavement
    osm = write(tmp_path / 'osm.json', {'icao': 'EDXX', 'ways': [
        way('A', 100.0), way('B2', 101.0), way('C', 20.0),              # C : hors du revetement
        way('lower', 100.0), way('Z', 100.0, 'runway')]})               # ref invalide ; pas un taxiway
    icao, ways = analysis.read_osm(osm, IdentityProj(), pav, g, g.mpp)
    assert icao == 'EDXX' and [w['ref'] for w in ways] == ['A', 'B2']


def test_the_game_file_ways_replace_the_osm_ways_and_the_icao_stays_the_osm_one(tmp_path, pavement):
    pav, g = pavement
    osm = write(tmp_path / 'osm.json', {'icao': 'EDXX', 'ways': [way('M', 100.0), way('N', 101.0)]})
    game = write(tmp_path / 'game.json', {'source': 'DCS', 'theatre': 'T', 'base': 'B', 'ways': [way('B', 100.0), way('F', 102.0)]})
    icao, ways = analysis.read_osm(osm, IdentityProj(), pav, g, g.mpp, ways_path=game)
    assert icao == 'EDXX' and [w['ref'] for w in ways] == ['B', 'F']
    assert all(w['length'] > 0 and w['pts_xz'].shape[1] == 2 for w in ways)


def test_the_osm_filter_applies_unchanged_to_the_game_ways(tmp_path, pavement):
    pav, g = pavement
    osm = write(tmp_path / 'osm.json', {'icao': 'EDXX', 'ways': []})
    game = write(tmp_path / 'game.json', {'ways': [way('B', 100.0), way('C', 20.0), way('bad', 100.0), way('F', 100.0, 'runway'),
                                                   {'aeroway': 'taxiway', 'ref': 'G', 'pts': [[100.0, 10.0]]}]})
    assert [w['ref'] for w in analysis.read_osm(osm, IdentityProj(), pav, g, g.mpp, ways_path=game)[1]] == ['B']


def test_a_game_file_alone_gives_ways_and_no_icao(tmp_path, pavement):
    pav, g = pavement
    game = write(tmp_path / 'game.json', {'ways': [way('B', 100.0)]})
    icao, ways = analysis.read_osm(None, IdentityProj(), pav, g, g.mpp, ways_path=game)
    assert icao is None and [w['ref'] for w in ways] == ['B']


def test_an_unreadable_game_file_is_a_chart_error_naming_it(tmp_path, pavement):
    pav, g = pavement
    bad = tmp_path / 'game.json'
    bad.write_text('{pas du json', encoding='utf-8')
    with pytest.raises(ChartError, match='game.json'):
        analysis.read_osm(None, IdentityProj(), pav, g, g.mpp, ways_path=bad)
    with pytest.raises(ChartError, match='absent.json'):
        analysis.read_osm(None, IdentityProj(), pav, g, g.mpp, ways_path=tmp_path / 'absent.json')


def test_game_taxiways_path_is_under_work_dir_by_lowercase_theatre(tmp_path):
    cfg = SimpleNamespace(work_dir=tmp_path)
    assert analysis.game_taxiways_path(cfg, 'GermanyColdWar', 'Frankfurt') == tmp_path / 'taxiways_dcs' / 'germanycoldwar' / 'Frankfurt.json'


# ---------- analyze (echantillons : Konya) ----------
def _game_doc(cfg, data):
    """Un fichier du jeu pour Konya : les taxiways A et B de la chart de reference, rebaptises Z et Y, et un tronc hors revetement."""
    from render import geo
    proj = geo.Projection.for_theatre('Syria')
    ways = []
    for w in data.osm_ways:
        ref = {'A': 'Z', 'B': 'Y'}.get(w['ref'])
        if ref:
            ways.append({'aeroway': 'taxiway', 'ref': ref, 'pts': [list(proj.to_latlon(x, z)) for x, z in w['pts_xz']]})
    x, z = data.arp
    far = [list(proj.to_latlon(x + 20000.0, z + 20000.0 + 100.0 * k)) for k in range(3)]
    ways.append({'aeroway': 'taxiway', 'ref': 'Q', 'pts': far})
    return {'source': 'DCS', 'theatre': 'Syria', 'base': 'Konya', 'ways': ways}


@pytest.fixture(scope='module')
def konya_reference(work):
    lines = []
    data = analysis.analyze(work, 'Syria', 'Konya', 1985, log=lines.append)
    return data, lines


def _write_game(cfg, doc):
    p = analysis.game_taxiways_path(cfg, 'Syria', 'Konya')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding='utf-8')
    return p


def test_without_a_game_file_nothing_changes_and_the_log_says_osm(fresh_work, konya_reference):
    ref, _ = konya_reference
    lines = []
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, log=lines.append)
    assert data.osm_kept == ref.osm_kept and data.osm_icao == ref.osm_icao == 'LTAN' and data.taxiway_source == 'osm'
    assert [w['ref'] for w in data.osm_ways] == [w['ref'] for w in ref.osm_ways]
    src = [ln for ln in lines if ln.startswith('taxiways :')]
    assert src == [f'taxiways : OSM ({fresh_work.work_dir / "osm" / "syria" / "Konya.json"})']


def test_with_a_game_file_the_names_come_from_it_and_the_icao_from_the_osm_file(fresh_work, konya_reference):
    ref, _ = konya_reference
    game = _write_game(fresh_work, _game_doc(fresh_work, ref))
    lines = []
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, log=lines.append)
    assert data.osm_kept == ['Y', 'Z'] and data.taxiway_source == 'jeu'    # Q, hors revetement, est ecarte par le filtre d'OSM
    assert data.osm_icao == 'LTAN' and data.icao == 'LTAN' and data.icao_known
    assert [ln for ln in lines if ln.startswith('taxiways :')] == [f'taxiways : jeu ({game})']
    assert not any('OSM (' in ln for ln in lines)


def test_with_an_explicit_osm_file_the_game_file_is_ignored_and_nothing_is_logged(fresh_work, konya_reference):
    ref, _ = konya_reference
    _write_game(fresh_work, _game_doc(fresh_work, ref))
    lines = []
    osm = fresh_work.work_dir / 'osm' / 'syria' / 'Konya.json'
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, osm_path=str(osm), log=lines.append)
    assert data.osm_kept == ref.osm_kept and data.osm_icao == 'LTAN' and data.taxiway_source == 'osm'
    assert not any(ln.startswith('taxiways :') for ln in lines)


def test_another_airfield_without_a_game_file_keeps_its_osm_names(fresh_work, konya_reference):
    ref, _ = konya_reference
    _write_game(fresh_work, _game_doc(fresh_work, ref))               # un fichier pour Konya seulement
    lines = []
    data = analysis.analyze(fresh_work, 'Syria', 'Adana', 1985, log=lines.append)
    assert data.osm_kept == ['A', 'A1', 'B', 'B1', 'C', 'D', 'D1', 'D2', 'E']
    assert [ln for ln in lines if ln.startswith('taxiways :')][0].startswith('taxiways : OSM (')


def test_a_game_file_without_an_osm_file_gives_the_names_and_no_icao(fresh_work, konya_reference):
    ref, _ = konya_reference
    _write_game(fresh_work, _game_doc(fresh_work, ref))
    empty = fresh_work.work_dir / 'osm_vide'
    empty.mkdir()
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, osm_dir=empty, log=lambda *a: None)
    assert data.osm_kept == ['Y', 'Z'] and data.osm_icao is None and not data.icao_known and data.icao == 'Konya'


def test_the_source_is_the_game_even_when_the_filter_drops_every_game_way(fresh_work, konya_reference):
    ref, _ = konya_reference
    doc = _game_doc(fresh_work, ref)
    doc['ways'] = [w for w in doc['ways'] if w['ref'] == 'Q']          # seul le tracé hors revetement
    _write_game(fresh_work, doc)
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, log=lambda *a: None)
    assert data.osm_ways == [] and data.taxiway_source == 'jeu' and data.osm_icao == 'LTAN'


def test_konya_page_footer_credits_osm_only_for_the_icao_when_the_names_come_from_the_game(fresh_work, konya_reference):
    from render import compose
    ref, _ = konya_reference
    _write_game(fresh_work, _game_doc(fresh_work, ref))
    data = analysis.analyze(fresh_work, 'Syria', 'Konya', 1985, log=lambda *a: None)
    svg = compose.build_pages(data, log=lambda *a: None)[0].svg
    assert 'via pydcs · OACI © OpenStreetMap contributors' in svg and 'taxiways ©' not in svg


# ---------- le pied de page : trois textes, trois marges ----------
BASE_FOOTER = 'DCS AIRFIELD CHARTS · données : carte F10 et fichiers de DCS World (Eagle Dynamics), via pydcs'


def _data(source, icao):
    return SimpleNamespace(taxiway_source=source, osm_icao=icao)


FOOTERS = [
    ('osm', 'LTAN', True, BASE_FOOTER + ' · taxiways © OpenStreetMap contributors'),          # noms d'OSM : inchange
    ('jeu', 'LTAN', True, BASE_FOOTER + ' · OACI © OpenStreetMap contributors'),               # noms du jeu, OACI d'OSM
    ('jeu', 'LTAN', False, BASE_FOOTER + ' · OACI © OpenStreetMap contributors'),              # idem, tous les ways du jeu ecartes
    ('jeu', None, True, BASE_FOOTER),                                                          # noms du jeu, sans OACI d'OSM
    ('osm', None, False, BASE_FOOTER),                                                         # ni noms d'OSM, ni jeu : inchange
]


@pytest.mark.parametrize('source, icao, osm, expected', FOOTERS)
def test_footer_text_credits_osm_only_for_what_comes_from_osm(source, icao, osm, expected):
    from render import page
    assert page.footer_text(_data(source, icao), osm) == expected


@pytest.mark.parametrize('source, icao, osm, expected', FOOTERS)
def test_footer_never_touches_the_right_hand_notice_and_keeps_3_mm(source, icao, osm, expected):
    from render import page
    from render.fonts import pt, text_width
    left = text_width(page.footer_text(_data(source, icao), osm), 'regular', pt(6.1))
    right = text_width(page.FOOTER_RIGHT, 'bold', pt(6.1))
    margin = (page.X1 - right) - (page.X0 + left)
    assert margin >= page.FOOTER_MARGIN_MM == 3.0
    if expected.endswith('taxiways © OpenStreetMap contributors'):
        assert margin == pytest.approx(7.18, abs=0.01)
    elif expected.endswith('OACI © OpenStreetMap contributors'):
        assert margin == pytest.approx(10.44, abs=0.01)
    print(f'marge {source}/{icao}/{osm} : {margin:.2f} mm')


def test_footer_draws_the_text_in_the_svg():
    from render import page
    from render.svgdoc import Doc
    d = Doc()
    page.footer(d, _data('jeu', 'LTAN'), True)
    svg = d.render()
    assert 'via pydcs · OACI © OpenStreetMap contributors' in svg and 'PAS POUR LA NAVIGATION RÉELLE' in svg
