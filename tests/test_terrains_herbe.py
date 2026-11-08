"""Lot terrains-herbe-germany : pistes declarees par le jeu (donnee, lecture, geometrie, tableau). Tests purs : ni
mosaique ni fichier de DCS ; pydcs fournit les pistes de reference."""
import json
import math
from pathlib import Path

import pytest

from render import analysis, dcsdata, naming, page, runways

DATA = Path(__file__).resolve().parent.parent / 'render' / 'data' / 'runways_declared' / 'GermanyColdWar.json'
THEATRE = 'GermanyColdWar'
HERBE = ['Airracing Frankfurt', 'Airracing Koblenz', 'Airracing Lubeck', 'Bienenfarm', 'Dedelow', 'Gardelegen', 'Gelnhausen',
         'Glindbruchkippe', 'Grosse Wiese', 'Hasselfelde', 'Herrenteich', 'Hockenheim', 'Kammermark', 'Langenselbold',
         'Northeim', 'Ober-Morlen', 'Pinnow', 'Revinge', 'Uetersen', 'Verden-Scharnhorst', 'Walldorf', 'Waren Vielist',
         'Weser Wumme', 'Worms']


def decl_rw(course, length, width=20.0, x=1000.0, z=2000.0, name=9):
    return {'Name': name, 'course': course, 'length': length, 'width': width, 'x': x, 'z': z}


# ---------- 2.1 la donnee ----------
def test_file_header_and_layout():
    doc = json.loads(DATA.read_text(encoding='utf-8'))
    assert list(doc)[:2] == ['theatre', 'source']
    assert doc['theatre'] == THEATRE
    assert len(doc['airbases']) == 119
    ids = [b['id'] for b in doc['airbases']]
    assert ids == sorted(ids)
    assert all(b['runways'] for b in doc['airbases'])
    assert set(doc['airbases'][0]['runways'][0]) == {'Name', 'course', 'length', 'width', 'x', 'z'}
    assert DATA.read_text(encoding='utf-8').endswith('}\n')


# ---------- 2.2 la lecture ----------
def test_declared_runways_by_folder_then_pydcs_name_ignoring_case():
    r = dcsdata.declared_runways(THEATRE, 'bienenfarm')
    assert len(r) == 1 and r[0]['length'] == pytest.approx(898, abs=1)
    assert dcsdata.declared_runways(THEATRE, 'Dossier inconnu', 'PINNOW')[0]['Name'] == 20
    assert dcsdata.declared_runways(THEATRE, 'Pinnow', 'Bienenfarm')[0]['Name'] == 20      # le dossier prime
    assert dcsdata.declared_runways(THEATRE, 'Dossier inconnu', 'Autre inconnue') == []
    assert dcsdata.declared_runways(THEATRE, 'Dossier inconnu') == []


def test_declared_runways_other_theatre_has_no_file():
    assert dcsdata.declared_runways('Syria', 'Konya', 'Konya') == []
    assert dcsdata.declared_runways('Syria', 'Paphos') == []


def test_declared_runways_returns_copies():
    a = dcsdata.declared_runways(THEATRE, 'Pinnow')
    a[0]['length'] = 1.0
    assert dcsdata.declared_runways(THEATRE, 'Pinnow')[0]['length'] > 1000


# ---------- 2.3 la geometrie ----------
def test_geometry_heading_is_minus_course():
    course = -0.7
    (rw,) = runways.runways_from_declared([decl_rw(course, 800.0, 30.0, 100.0, -50.0)])
    dx, dz = rw.p1[0] - rw.p0[0], rw.p1[1] - rw.p0[1]
    assert math.degrees(math.atan2(dz, dx)) % 360 == pytest.approx(math.degrees(-course) % 360)
    assert rw.axis_dir == pytest.approx((math.cos(course), -math.sin(course)))
    assert rw.length == pytest.approx(800.0)
    assert rw.center == pytest.approx((100.0, -50.0))
    assert rw.p0 == pytest.approx((100.0 - rw.axis_dir[0] * 400, -50.0 - rw.axis_dir[1] * 400))
    assert rw.width == 30.0 and rw.pave_len == 800.0
    assert rw.surface == 'herbe' and rw.marked is False


def test_geometry_course_zero_is_north_and_quarter_turn_is_west():
    (n,) = runways.runways_from_declared([decl_rw(0.0, 200.0, x=0.0, z=0.0)])
    assert n.p1 == pytest.approx((100.0, 0.0)) and n.p0 == pytest.approx((-100.0, 0.0))
    (w,) = runways.runways_from_declared([decl_rw(math.pi / 2, 200.0, x=0.0, z=0.0)])
    assert w.p1 == pytest.approx((0.0, -100.0), abs=1e-9)          # cap grille 270 : vers l'ouest (z decroissant)


def test_threshold_100_m():
    assert runways.runways_from_declared([decl_rw(1.0, 99.99)]) == []
    assert runways.runways_from_declared([decl_rw(1.0, 10.0)]) == []
    assert len(runways.runways_from_declared([decl_rw(1.0, 100.0)])) == 1
    two = runways.runways_from_declared([decl_rw(1.0, 10.0), decl_rw(1.0, 500.0)])
    assert len(two) == 1 and two[0].length == pytest.approx(500.0)


def test_runway_surface_default_is_hard():
    assert runways.Runway((0, 0), (1, 1), 10.0).surface == 'dur'


def test_declared_numbers_and_end_numbers():
    assert runways.declared_numbers(30) == (30, 12)
    assert runways.declared_numbers(12) == (12, 30)
    assert runways.declared_numbers(36) == (36, 18)
    assert runways.declared_numbers(18) == (18, 36)
    assert runways.declared_numbers(7) == (7, 25)
    assert [runways.end_number(n) for n in ('07', '7', '25L', '09C', '36R')] == [7, 7, 25, 9, 36]


def test_ref_matches_by_name_without_heading_tolerance():
    refs = [('30', '12'), ('10L', '28R')]
    assert runways.matching_refs(30, refs) == [0]
    assert runways.matching_refs(12, refs) == [0]
    assert runways.matching_refs(28, refs) == [1]
    assert runways.matching_refs(7, [('07', '25')]) == [0]            # zero de tete
    assert runways.matching_refs(6, [('06', '24')]) == [0]
    (rw,) = runways.runways_from_declared([decl_rw(1.0, 500.0, name=10)], refs)        # le cap n'entre pas en jeu
    assert rw.ref == 1


def test_ref_none_when_no_or_several_pydcs_runways():
    assert runways.runways_from_declared([decl_rw(1.0, 500.0, name=9)], [('30', '12')])[0].ref is None
    assert runways.runways_from_declared([decl_rw(1.0, 500.0, name=9)])[0].ref is None
    assert runways.runways_from_declared([decl_rw(1.0, 500.0, name=9)], [('09L', '27R'), ('09R', '27L')])[0].ref is None


def _facts(name):
    terrain = dcsdata.load_terrain(THEATRE)
    ap = next(a for a in terrain.airports.values() if a.name.lower() == name.lower())
    return dcsdata.airport_facts(ap)


@pytest.mark.parametrize('name', HERBE)
def test_all_grass_strips_have_one_runway_matched_by_name(name):
    facts = _facts(name)
    declared = dcsdata.declared_runways(THEATRE, name, facts.name)
    refs = [(m.name, o.name) for m, o in facts.rw_refs]
    assert len(runways.matching_refs(declared[0]['Name'], refs)) == 1
    rws = runways.runways_from_declared(declared, refs)
    assert len(rws) == 1
    assert rws[0].ref is not None
    names = naming.name_ends([(rws[0].p0, rws[0].p1)], [(a.name, a.heading) for a in facts.approaches],
                             [[(a.name, a.heading) for a in facts.rw_refs[rws[0].ref]]])
    assert all(n for n, _ in names[0])


def test_landstuhl_has_no_runway():
    facts = _facts('Landstuhl')
    declared = dcsdata.declared_runways(THEATRE, 'Landstuhl', facts.name)
    assert declared and declared[0]['length'] < 100.0
    assert runways.runways_from_declared(declared, []) == []
    assert facts.slots and all(s.heli_only for s in facts.slots)


def test_every_declared_base_is_handled_without_error():
    doc = json.loads(DATA.read_text(encoding='utf-8'))
    for b in doc['airbases']:
        for rw in runways.runways_from_declared(b['runways']):
            assert rw.length >= 100.0 and rw.surface == 'herbe'


# ---------- 2.6 le tableau des pistes ----------
class FakeDoc:
    def __init__(self):
        self.items = []

    def add(self, layer, el):
        self.items.append(el)


def _table(surface):
    rw = runways.Runway((0.0, 0.0), (900.0, 0.0), 20.0, axis_dir=(1.0, 0.0), surface=surface)
    ends = [analysis.End('09', 90.0, 90.0, (0.0, 0.0), 0, 98), analysis.End('27', 270.0, 270.0, (900.0, 0.0), 0, 98)]

    class D:
        runways, parkings, n_shelter, heli_mode = [rw], [], 0, False
    D.ends = ends
    doc = FakeDoc()
    page.runway_table(doc, D, None, 10.0, [20.0, 25.0])
    return ''.join(doc.items)


def test_table_says_grass_strip_on_each_row_right_aligned_on_x1():
    out = _table('herbe')
    assert out.count('PISTE EN HERBE') == 2
    import re
    for m in re.finditer(r'<text[^>]*>PISTE EN HERBE</text>', out):
        tag = m.group(0)
        assert 'text-anchor="end"' in tag and f'x="{page.f(page.X1)}"' in tag
        assert 'font-weight="700"' in tag or 'bold' in tag


def test_table_hard_runway_has_no_grass_text():
    assert 'PISTE EN HERBE' not in _table('dur')
