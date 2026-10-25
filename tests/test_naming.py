"""Nommage des extremites de piste (cas synthetiques, pistes paralleles comprises)."""
import math

from render import naming

APP = [('01', 14.0), ('19', 194.0), ('09', 90.0), ('27', 270.0), ('13', 134.0), ('31', 314.0)]


def rw(x, z, az_deg, length=3000.0):
    """Piste centree en (x, z), orientee az_deg (azimut grille du sens p0 -> p1)."""
    a = math.radians(az_deg)
    ux, uz = math.cos(a), math.sin(a)
    return (x - ux * length / 2, z - uz * length / 2), (x + ux * length / 2, z + uz * length / 2)


def test_single_runway_no_letter():
    out = naming.name_ends([rw(0, 0, 14)], APP)
    assert [e[0] for e in out[0]] == ['01', '19']


def test_parallel_pair_left_is_L():
    # deux pistes paralleles de cap 14 deg ; B est a l'est (z plus grand) de A
    a = rw(0, 0, 14)
    b = rw(0, 400, 14)
    out = naming.name_ends([a, b], APP)
    # sens 14 : la gauche est (sin a, -cos a) -> vers l'ouest (z decroissant) : A est a gauche de B
    assert out[0][0][0] == '01L' and out[1][0][0] == '01R'
    # sens inverse (194) : la gauche est l'est : B est a gauche de A
    assert out[0][1][0] == '19R' and out[1][1][0] == '19L'


def test_pair_given_in_other_order_and_direction():
    a = rw(0, 0, 14)
    b = rw(0, 400, 14)
    b_rev = (b[1], b[0])
    out = naming.name_ends([b_rev, a], APP)
    names = {out[0][0][0], out[0][1][0], out[1][0][0], out[1][1][0]}
    assert names == {'01L', '01R', '19L', '19R'}
    assert out[1][0][0] == '01L' and out[0][1][0] == '01R'         # a (ouest) : 01L ; b renversee : son bout p1 = 01R


def test_triple_parallel_L_C_R():
    out = naming.name_ends([rw(0, 0, 14), rw(0, 400, 14), rw(0, 800, 14)], APP)
    assert [out[i][0][0] for i in range(3)] == ['01L', '01C', '01R']


def test_crossing_runways_have_no_letters():
    out = naming.name_ends([rw(0, 0, 134), rw(0, 0, 90)], APP)
    assert {e[0] for r in out for e in r} == {'13', '31', '09', '27'}


def test_nearest_heading_picks_number():
    out = naming.name_ends([rw(0, 0, 20.7)], APP)             # 20,7 deg : 01 (14) plutot que 09 ou 13
    assert out[0][0][0] == '01' and out[0][1][0] == '19'


def test_sort_key():
    names = ['19R', '01R', '01L', '19L', '09']
    assert sorted(names, key=naming.sort_key) == ['01L', '01R', '09', '19L', '19R']
