"""Panneaux de taxiway : situer les lettres lues, les rattacher aux sorties de piste, le fichier du jeu
(capture/signattr.py, commande `attribuer`). Les tests sur Frankfurt demandent les echantillons (DCS_CHARTS_SAMPLES) ; les autres
travaillent sur des graphes et des cameras synthetiques."""
import json
import math

import pytest

import config
import rn5
import signattr as sa
import signcapture as sc

from test_signcapture import mk


# ---------- cameras et tuiles synthetiques ----------
SIZE = sa.IMAGE_DEFAUT


def mkview(cap, tangage=-2.0, hauteur=3.0):
    return {'cap': cap, 'tangage': tangage, 'hauteur': hauteur}


def project(cam, point, size=SIZE, champ=90.0, side=10):
    """Le sens inverse de sa.ray : la tuile (carre de `side` px) qui montre `point` (x, y, z) a la camera."""
    pos, _, avant, haut, droite = cam
    v = [point[i] - pos[i] for i in range(3)]
    depth = sum(v[i] * avant[i] for i in range(3))
    assert depth > 0
    f = (size[0] / 2.0) / math.tan(math.radians(champ) / 2.0)
    px = (size[0] - 1) / 2.0 + f * sum(v[i] * droite[i] for i in range(3)) / depth
    py = (size[1] - 1) / 2.0 - f * sum(v[i] * haut[i] for i in range(3)) / depth
    return {'x': px - side / 2.0, 'y': py - side / 2.0, 'w': side, 'h': side}


def tile_of(cam, point, vue, car, **kw):
    t = project(cam, point, **kw)
    t.update({'vue': vue, 'car': car})
    return t


def cam_of(view, pos):
    return sa.camera(view, pos)


GROUND = 100.0                      # y du sol dans les tests : la camera est a y = 103,0, a 3,0 m du sol


def spot(x, z, h=sa.HAUTEUR_PANNEAU_M):
    return (x, GROUND + h, z)


# ---------- 2.1.1 les lettres prises ----------
def test_tiles_taken_skip_question_marks_cut_tiles_and_unread_views_but_not_the_hold_flag():
    lecture = {'vues': [
        {'vue': 1, 'image': 'lue', 'tuiles': [
            {'x': 1, 'y': 2, 'w': 3, 'h': 4, 'car': 'B', 'attente': False, 'coupee': False},
            {'x': 5, 'y': 6, 'w': 7, 'h': 8, 'car': '?', 'attente': False, 'coupee': False},
            {'x': 9, 'y': 9, 'w': 9, 'h': 9, 'car': 'C', 'attente': False, 'coupee': True},
            {'x': 4, 'y': 4, 'w': 4, 'h': 4, 'car': 'D', 'attente': True, 'coupee': False}]},
        {'vue': 2, 'image': 'figee', 'tuiles': [{'x': 1, 'y': 1, 'w': 1, 'h': 1, 'car': 'E', 'attente': False, 'coupee': False}]},
        {'vue': 3, 'image': 'absente', 'tuiles': []}]}
    assert [(t['vue'], t['car']) for t in sa.tiles_from_lecture(lecture)] == [(1, 'B'), (1, 'D')]
    assert sa.tiles_from_lecture(lecture)[0] == {'vue': 1, 'x': 1, 'y': 2, 'w': 3, 'h': 4, 'car': 'B'}


# ---------- 2.1.2 et 2.1.3 la camera et le rayon ----------
def test_camera_frame_follows_the_hook():
    pos, h, avant, haut, droite = sa.camera({'cap': 0.0, 'tangage': 0.0, 'hauteur': 3.0}, (1.0, 103.0, 2.0))
    assert avant == pytest.approx((1, 0, 0)) and haut == pytest.approx((0, 1, 0)) and droite == pytest.approx((0, 0, 1))
    pos, h, avant, haut, droite = sa.camera({'cap': 90.0, 'tangage': 0.0, 'hauteur': 3.0}, (0, 103.0, 0))
    assert avant == pytest.approx((0, 0, 1)) and droite == pytest.approx((-1, 0, 0))
    c, t = math.radians(30.0), math.radians(-20.0)
    pos, h, avant, haut, droite = sa.camera({'cap': 30.0, 'tangage': -20.0, 'hauteur': 3.0}, (0, 103.0, 0))
    assert avant == pytest.approx((math.cos(t) * math.cos(c), math.sin(t), math.cos(t) * math.sin(c)))
    assert haut == pytest.approx((-math.sin(t) * math.cos(c), math.cos(t), -math.sin(t) * math.sin(c)))
    assert droite == pytest.approx((-math.sin(c), 0.0, math.cos(c)))


def test_ray_of_the_image_centre_is_the_forward_axis_and_the_edge_is_half_the_field():
    cam = cam_of(mkview(37.0, -5.0), (0, 103.0, 0))
    cx, cy = (SIZE[0] - 1) / 2.0, (SIZE[1] - 1) / 2.0
    assert sa.ray(cam, {'x': cx - 5, 'y': cy - 5, 'w': 10, 'h': 10}, SIZE, 90.0) == pytest.approx(cam[2])
    # f = 1 720 px pour 3 440 px et 90 degres : 1 720 px a droite du centre, 45 degres
    cam0 = cam_of(mkview(0.0, 0.0), (0, 103.0, 0))
    d = sa.ray(cam0, {'x': cx + 1720.0 - 5, 'y': cy - 5, 'w': 10, 'h': 10}, SIZE, 90.0)
    assert math.degrees(math.atan2(d[2], d[0])) == pytest.approx(45.0, abs=1e-9)
    d = sa.ray(cam0, {'x': cx - 5, 'y': cy - 1720.0 - 5, 'w': 10, 'h': 10}, SIZE, 90.0)      # 1 720 px au-dessus du centre
    assert math.degrees(math.atan2(d[1], d[0])) == pytest.approx(45.0, abs=1e-9)


def test_the_field_is_a_parameter_with_90_degrees_by_default():
    cam = cam_of(mkview(0.0, 0.0), (0, 103.0, 0))
    t = {'x': 2000.0, 'y': 700.0, 'w': 10, 'h': 10}
    d90 = sa.ray(cam, t, SIZE, 90.0)
    d60 = sa.ray(cam, t, SIZE, 60.0)
    assert d90 != pytest.approx(d60)
    assert sa.CHAMP_DEG == 90.0
    assert sa.ray(cam, t, SIZE, sa.CHAMP_DEG) == pytest.approx(d90)


# ---------- 2.1.4 la position au sol ----------
def test_a_letter_is_placed_on_the_ground_plus_70_cm_whatever_the_pose():
    for cap, tang in ((0.0, -2.0), (123.0, -4.0), (300.0, -1.43)):
        pos = (0.0, 103.0, 0.0)
        cam = cam_of(mkview(cap, tang), pos)
        c = math.radians(cap)
        # un point devant la camera, a ~70 m
        p = (70.0 * math.cos(c) + 8.0, 70.0 * math.sin(c) - 6.0)
        t = tile_of(cam, spot(*p), 1, 'F')
        letters, _ = sa.place_letters([t], {1: mkview(cap, tang)}, {1: pos})
        assert letters[0]['xz'] == pytest.approx(p, abs=0.05) and letters[0]['source'] == sa.SOURCE_PLAN


def test_the_ground_is_the_camera_height_below_it_and_the_panel_is_70_cm_up():
    cam = cam_of({'cap': 0.0, 'tangage': -2.0, 'hauteur': 3.0}, (0.0, 103.0, 0.0))
    t = tile_of(cam, spot(50.0, 0.0), 1, 'A')
    d = sa.ray(cam, t, SIZE, 90.0)
    x, z = sa.ground_point(cam, d)
    assert (x, z) == pytest.approx((50.0, 0.0), abs=0.05)
    assert sa.HAUTEUR_PANNEAU_M == 0.70
    # a 0,2 m au lieu de 0,7 m : plus loin ; un sol de reference plus haut ne change que la hauteur de la camera
    x2, _ = sa.ground_point(cam, d, 0.2)
    assert x2 > x
    cam_low = cam_of({'cap': 0.0, 'tangage': -2.0, 'hauteur': 2.0}, (0.0, 103.0, 0.0))
    assert sa.ground_point(cam_low, d)[0] < x


def test_a_ray_that_does_not_descend_is_not_placed_and_counted():
    pos = (0.0, 103.0, 0.0)
    views = {1: mkview(0.0, 10.0), 2: mkview(0.0, -2.0)}
    up = {'x': 1700.0, 'y': 100.0, 'w': 10, 'h': 10, 'vue': 1, 'car': 'A'}
    ok = tile_of(cam_of(views[2], pos), spot(60.0, 5.0), 2, 'B')
    letters, pairs = sa.place_letters([up, ok], views, {1: pos, 2: pos})
    assert letters[0]['xz'] is None and letters[0]['motif'] == sa.MOTIF_NON_SITUEE
    assert letters[1]['xz'] is not None
    sa.attach(letters, [[1]], [(60.0, 5.0), (0.0, 0.0)])
    exits = sa.name_exits(letters, [[1]])
    doc = sa.build_attribution('T', 'B', letters, pairs, exits, 90.0)
    assert doc['comptes']['non_situees'] == 1 and doc['lettres'][0]['position'] is None and not doc['lettres'][0]['rattachee']


def test_a_view_without_position_is_not_placed():
    views = {1: mkview(0.0)}
    letters, _ = sa.place_letters([{'vue': 1, 'x': 1700.0, 'y': 700.0, 'w': 10, 'h': 10, 'car': 'A'}], views, {1: None})
    assert letters[0]['xz'] is None and letters[0]['motif'] == sa.MOTIF_SANS_POSE


# ---------- 2.1.5 la triangulation ----------
def _two_views(point_a, point_b=None, car_a='D', car_b='D', pa=(0.0, 103.0, 0.0), pb=(0.0, 103.0, 120.0), cap_a=60.0, cap_b=-60.0):
    """Deux cameras qui regardent le meme coin de terrain (cap_a et cap_b l'un vers l'autre) ; chaque tuile montre son point."""
    va, vb = mkview(cap_a, -2.0), mkview(cap_b, -2.0)
    ca, cb = cam_of(va, pa), cam_of(vb, pb)
    tiles = [tile_of(ca, point_a, 1, car_a), tile_of(cb, point_b or point_a, 2, car_b)]
    return tiles, {1: va, 2: vb}, {1: pa, 2: pb}


def test_a_sign_seen_twice_is_triangulated_at_the_same_height_in_both_views():
    P = (70.0, 60.0)
    tiles, vues, pos = _two_views(spot(*P))
    letters, pairs = sa.place_letters(tiles, vues, pos)
    assert len(pairs) == 1
    for L in letters:
        assert L['source'] == sa.SOURCE_TRI and L['xz'] == pytest.approx(P, abs=0.05)
        assert L['hauteurs'] == pytest.approx((0.7, 0.7), abs=0.02)      # 0,7 m dans chacune
    assert letters[0]['paire'] == 1 and letters[1]['paire'] == 0


def test_triangulation_needs_the_same_letter_and_two_different_views():
    P = spot(70.0, 60.0)
    tiles, vues, pos = _two_views(P, car_b='E')
    assert sa.place_letters(tiles, vues, pos)[1] == []
    tiles, vues, pos = _two_views(P)
    tiles[1]['vue'] = 1                                  # la meme vue : pas une paire
    vues2, pos2 = {1: vues[1]}, {1: pos[1]}
    letters, pairs = sa.place_letters(tiles, vues2, pos2)
    assert pairs == [] and all(L['source'] == sa.SOURCE_PLAN for L in letters)


@pytest.mark.parametrize('h, retained', [(0.30, False), (0.45, True), (0.70, True), (1.05, True), (1.30, False)])
def test_triangulation_keeps_heights_between_40_cm_and_1_10_m(h, retained):
    tiles, vues, pos = _two_views(spot(70.0, 60.0, h))
    assert bool(sa.place_letters(tiles, vues, pos)[1]) is retained


@pytest.mark.parametrize('ha, hb, ok', [(0.6, 0.8, True), (0.5, 0.9, False), (0.5, 0.74, True), (0.5, 0.76, False)])
def test_triangulation_needs_the_two_heights_within_25_cm(ha, hb, ok):
    P = (70.0, 60.0)
    va, vb = mkview(60.0, -2.0), mkview(-60.0, -2.0)
    pa, pb = (0.0, 103.0, 0.0), (0.0, 103.0, 120.0)
    # chaque rayon passe par le meme point (x, z) du sol, a sa propre hauteur : les traces se coupent en P, les hauteurs sont ha et hb
    tiles = [tile_of(cam_of(va, pa), spot(*P, ha), 1, 'D'), tile_of(cam_of(vb, pb), spot(*P, hb), 2, 'D')]
    letters, pairs = sa.place_letters(tiles, {1: va, 2: vb}, {1: pa, 2: pb})
    inter = sa.intersect(letters[0]['cam'], letters[0]['d'], letters[1]['cam'], letters[1]['d'])
    assert inter['hauteurs'] == pytest.approx((ha, hb), abs=0.01)
    assert bool(pairs) is ok


def test_retained_applies_both_bounds_and_the_gap():
    r = lambda a, b: sa.retained({'hauteurs': (a, b)})
    assert r(0.7, 0.7) and r(0.40, 0.60) and r(1.10, 0.90)
    assert not r(0.39, 0.5) and not r(0.5, 1.11) and not r(0.4, 0.66) and not r(-3.0, -3.0)


def test_rays_that_cross_behind_a_camera_do_not_make_a_pair():
    # deux cameras qui s'eloignent l'une de l'autre : les traces se coupent derriere elles
    pa, pb = (0.0, 103.0, 0.0), (0.0, 103.0, 120.0)
    ca, cb = cam_of(mkview(-60.0, -2.0), pa), cam_of(mkview(60.0, -2.0), pb)
    da = sa.ray(ca, {'x': 1720.0, 'y': 800.0, 'w': 10, 'h': 10}, SIZE, 90.0)
    db = sa.ray(cb, {'x': 1720.0, 'y': 800.0, 'w': 10, 'h': 10}, SIZE, 90.0)
    assert sa.intersect(ca, da, cb, db) is None
    # paralleles
    cc = cam_of(mkview(10.0, -2.0), (0.0, 103.0, 40.0))
    cd = cam_of(mkview(10.0, -2.0), (0.0, 103.0, 0.0))
    t = {'x': 1720.0, 'y': 800.0, 'w': 10, 'h': 10}
    assert sa.intersect(cc, sa.ray(cc, t, SIZE, 90.0), cd, sa.ray(cd, t, SIZE, 90.0)) is None


def test_a_letter_in_several_pairs_takes_the_pair_with_the_widest_rays():
    P = (70.0, 60.0)
    va, vb, vc = mkview(60.0, -2.0), mkview(-60.0, -2.0), mkview(-20.0, -2.0)
    pa, pb, pc = (0.0, 103.0, 0.0), (0.0, 103.0, 120.0), (-40.0, 103.0, 110.0)
    ca, cb, cc = cam_of(va, pa), cam_of(vb, pb), cam_of(vc, pc)
    tiles = [tile_of(ca, spot(*P), 1, 'D'), tile_of(cb, spot(*P), 2, 'D'), tile_of(cc, spot(*P), 3, 'D')]
    letters, pairs = sa.place_letters(tiles, {1: va, 2: vb, 3: vc}, {1: pa, 2: pb, 3: pc})
    assert len(pairs) == 3
    angles = {(i, j): inter['angle'] for i, j, inter in pairs}
    # la lettre 0 est dans les paires (0, 1) et (0, 2) : elle prend celle dont l'angle est le plus proche de 90
    want = min(((0, 1), (0, 2)), key=lambda p: abs(angles[p] - 90.0))
    assert letters[0]['paire'] == want[1]
    assert letters[0]['source'] == sa.SOURCE_TRI


# ---------- 2.2 rattacher ----------
def _nodes(*xs):
    return [(float(x), 0.0) for x in xs]


def _letter_at(x, z=0.0, car='B', vue=1):
    return {'vue': vue, 'tuile': {'x': 0, 'y': 0, 'w': 1, 'h': 1}, 'car': car, 'xz': (x, z), 'source': sa.SOURCE_PLAN,
            'hauteurs': None, 'paire': None, 'motif': None, 'cam': None, 'd': None}


def test_attach_needs_150_m_at_most_and_the_next_exit_100_m_further():
    groups = [[0], [1]]                                 # sortie 0 en x = 0, sortie 1 en x = 600
    nodes = _nodes(0, 600)
    for x, ok, motif in ((150.0, True, None), (150.1, False, sa.MOTIF_TROP_LOIN), (-80.0, True, None)):
        L = _letter_at(x)
        sa.attach([L], groups, nodes)
        assert L['rattachee'] is ok and L['motif'] == motif and (L['sortie'] == 0) is ok
    L = _letter_at(100.0)                               # 100 et 100 : ambigue
    sa.attach([L], groups, _nodes(0, 200.0))
    assert not L['rattachee'] and L['motif'] == sa.MOTIF_AMBIGUE and L['sortie'] is None
    L = _letter_at(60.0)                                # 60 et 100 : 40 m de plus, ambigue
    sa.attach([L], groups, _nodes(0, 160.0))
    assert L['motif'] == sa.MOTIF_AMBIGUE
    L = _letter_at(60.0)                                # 60 et 160 : 100 m de plus exactement, rattachee
    sa.attach([L], groups, _nodes(0, 220.0))
    assert L['rattachee'] and L['sortie'] == 0


def test_attach_distance_is_to_the_nearest_node_of_the_group_and_reports_the_two_nearest():
    groups = [[0, 1, 2], [3]]
    nodes = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (900.0, 0.0)]
    L = _letter_at(190.0, 20.0)
    sa.attach([L], groups, nodes)
    assert L['sortie'] == 0 and L['distances'][0] == (0, pytest.approx(math.hypot(10.0, 20.0)))
    assert L['distances'][1] == (3, pytest.approx(math.hypot(710.0, 20.0)))


def test_a_single_exit_has_no_competitor():
    L = _letter_at(100.0)
    sa.attach([L], [[0]], _nodes(0))
    assert L['rattachee'] and len(L['distances']) == 1


def test_an_unplaced_letter_is_not_attached_and_keeps_its_reason():
    L = _letter_at(0.0)
    L['xz'], L['motif'] = None, sa.MOTIF_NON_SITUEE
    sa.attach([L], [[0]], _nodes(0))
    assert not L['rattachee'] and L['motif'] == sa.MOTIF_NON_SITUEE and L['distances'] == []


def test_one_exit_one_name_a_conflict_gives_none_and_a_letter_may_name_two_exits():
    groups = [[0], [1], [2]]
    nodes = _nodes(0, 1000, 2000)
    ls = [_letter_at(0, car='F'), _letter_at(10, car='F'),            # sortie 0 : F, F
          _letter_at(1000, car='F'),                                  # sortie 1 : F (la meme lettre sur deux sorties)
          _letter_at(2000, car='B'), _letter_at(2010, car='C')]       # sortie 2 : B et C
    sa.attach(ls, groups, nodes)
    exits = sa.name_exits(ls, groups)
    assert [(e['id'], e['nom'], e['conflit']) for e in exits] == [(0, 'F', False), (1, 'F', False), (2, None, True)]
    assert exits[0]['lettres'] == ['F', 'F'] and exits[2]['lettres'] == ['B', 'C']


def test_an_exit_without_letters_has_no_name_and_no_conflict():
    groups = [[0], [1]]
    ls = [_letter_at(0)]
    sa.attach(ls, groups, _nodes(0, 1000))
    exits = sa.name_exits(ls, groups)
    assert exits[1] == {'id': 1, 'noeuds': [1], 'nom': None, 'lettres': [], 'conflit': False}


# ---------- 2.3 le fichier du jeu ----------
class _Proj:
    def to_latlon(self, x, z):
        return 50.0 + x / 1e5, 8.0 + z / 1e5


def _exit_graph():
    """Une piste (troncon 0, axe x) et deux sorties : l'une a x = 0 (troncons 1 et 2 hors piste), l'autre a x = 600 (troncon 3)."""
    nodes = [('runway_taxiway_t', 0, 0), ('taxiway_t', 0, 100), ('taxiway_end_a', 0, -100),
             ('runway_taxiway_t', 600, 0), ('taxiway_end_b', 600, 100), ('rwy_point_takeoff_landing', 1000, 0)]
    segs = [('runway_45m', 0, 3, [(0, 0), (300, 0), (600, 0)]),
            ('taxiway_15m', 0, 1, [(0, 5), (0, 50), (0, 95)]),
            ('taxiway_15m', 0, 2, [(0, -5), (0, -50), (0, -95)]),
            ('taxiway_15m', 3, 4, [(600, 5), (600, 50), (600, 95)]),
            ('runway_45m', 3, 5, [(600, 0), (1000, 0)])]
    return sc.Graph(mk(nodes, segs))


def test_game_ways_one_walk_per_named_exit_node_and_non_runway_segment():
    g = _exit_graph()
    groups = sc.bretelles(g)
    assert groups == [[0], [3]]
    exits = [{'id': 0, 'noeuds': [0], 'nom': 'B', 'lettres': ['B'], 'conflit': False},
             {'id': 3, 'noeuds': [3], 'nom': None, 'lettres': [], 'conflit': False}]
    ways = sa.game_ways(g, groups, exits, _Proj())
    assert [(w['aeroway'], w['ref']) for w in ways] == [('taxiway', 'B'), ('taxiway', 'B')]
    # la marche part du noeud (0, 0) puis suit la ligne du troncon sans son premier point
    assert ways[0]['pts'] == [[50.0, 8.0], [50.0, 8.0005], [50.0, 8.00095]]
    assert ways[1]['pts'] == [[50.0, 8.0], [50.0, 7.9995], [50.0, 7.99905]]
    exits[1]['nom'] = 'D'
    ways = sa.game_ways(g, groups, exits, _Proj())
    assert [w['ref'] for w in ways] == ['B', 'B', 'D']


def _long_exit(step, n):
    """Une sortie dont le troncon taxiway suit z = step, 2 step, ... n step depuis le noeud (0, 0)."""
    nodes = [('runway_taxiway_t', 0, 0), ('taxiway_end_a', 0, n * step + 10), ('rwy_point_takeoff_landing', 300, 0)]
    segs = [('runway_45m', 0, 2, [(0, 0), (300, 0)]),
            ('taxiway_15m', 0, 1, [(0, 10)] + [(0, k * step) for k in range(1, n + 1)])]
    g = sc.Graph(mk(nodes, segs))
    groups = sc.bretelles(g)
    ex = [{'id': 0, 'noeuds': [0], 'nom': 'K', 'lettres': ['K'], 'conflit': False}]
    return sa.game_ways(g, groups, ex, _Proj())


def test_a_game_way_stops_at_the_last_point_within_300_m_of_the_node():
    assert sa.TRACE_MAX_M == 300.0
    ways = _long_exit(100.0, 8)                      # points a 100, 200, 300, 400... m : on garde jusqu'a 300 m inclus
    assert len(ways) == 1
    assert [round((p[1] - 8.0) * 1e5) for p in ways[0]['pts']] == [0, 100, 200, 300]
    ways = _long_exit(110.0, 8)                      # 110, 220, 330... : 330 m est de trop
    assert [round((p[1] - 8.0) * 1e5) for p in ways[0]['pts']] == [0, 110, 220]
    ways = _long_exit(50.0, 4)                       # toute la marche (200 m) quand elle est plus courte
    assert [round((p[1] - 8.0) * 1e5) for p in ways[0]['pts']] == [0, 50, 100, 150, 200]


def test_a_game_way_follows_the_walk_through_pass_nodes_without_an_angle_limit():
    nodes = [('runway_taxiway_t', 0, 0), ('auto__a__b', 0, 100), ('taxiway_end_a', 100, 100), ('rwy_point_takeoff_landing', 300, 0)]
    segs = [('runway_45m', 0, 3, [(0, 0), (300, 0)]),
            ('taxiway_15m', 0, 1, [(0, 10), (0, 50), (0, 90)]),
            ('taxiway_15m', 1, 2, [(10, 100), (50, 100), (90, 100)])]          # un virage a 90 degres
    g = sc.Graph(mk(nodes, segs))
    ways = sa.game_ways(g, sc.bretelles(g), [{'id': 0, 'noeuds': [0], 'nom': 'K', 'lettres': ['K'], 'conflit': False}], _Proj())
    assert len(ways) == 1 and len(ways[0]['pts']) == 1 + 2 + 2     # le noeud, puis 2 points par troncon (le premier de chacun est ecarte)
    assert ways[0]['pts'][-1] == [50.0009, 8.001]


# ---------- la commande, de bout en bout, sur un graphe synthetique ----------
def _cfg_for(tmp_path, dcs):
    p = tmp_path / 'config.json'
    p.write_text(json.dumps({'work_dir': str(tmp_path / 'work'), 'dcs_install': str(dcs)}), encoding='utf-8')
    return config.load_config(['x', '--config', str(p)])[0]


@pytest.fixture
def synth(tmp_path, monkeypatch):
    """Une base synthetique (graphe _exit_graph) et sa prise de vues : une lettre B pres de la sortie 0, une lettre C pres de
    la sortie 3. Le .rn5 est remplace par le graphe (rn5.read est substitue) ; le theatre est Syria (projection pydcs)."""
    dcs = tmp_path / 'dcs'
    (dcs / 'Mods' / 'terrains' / 'Syria' / 'AirfieldsTaxiways').mkdir(parents=True)
    (dcs / 'Mods' / 'terrains' / 'Syria' / 'AirfieldsTaxiways' / 'Test.rn5').write_bytes(b'x' * 6000)
    cfg = _cfg_for(tmp_path, dcs)
    r = _exit_graph().r
    monkeypatch.setattr(rn5, 'read', lambda path: r)
    d = cfg.work_dir / 'panneaux' / 'Syria' / 'Test'
    d.mkdir(parents=True)
    pos = (-60.0, 103.0, 20.0)
    view = {'numero': 1, 'cap': 0.0, 'tangage': -2.0, 'hauteur': 3.0, 'camera': {'x': pos[0], 'z': pos[2]}}
    cam = cam_of(view, pos)
    tb = project(cam, spot(20.0, 60.0))                  # a ~80 m de la sortie 0
    (d / 'vues.json').write_text(json.dumps({'vues': [view]}), encoding='utf-8')
    (d / 'index.json').write_text(json.dumps({'vues': [{'numero': 1, 'statut': 'faite', 'img': None, 'position': list(pos)}]}),
                                  encoding='utf-8')

    def write_lecture(tiles):
        doc = {'theatre': 'Syria', 'base': 'Test', 'vues': [{'vue': 1, 'image': 'lue', 'tuiles': [
            dict(t, score=0.9, depart=0.9, ecart=1.0, attente=False, coupee=False) for t in tiles]}]}
        (d / 'lecture.json').write_text(json.dumps(doc), encoding='utf-8')

    write_lecture([dict(tb, car='B')])
    return cfg, d, write_lecture, tb


def _run(cfg, champ=None):
    lines = []
    import signattr
    signattr.cmd_attribuer(cfg, 'Syria', ['Test'], sc.panneaux_dir, sc.find_base, signattr.CHAMP_DEG if champ is None else champ,
                           log=lines.append)
    return lines


def test_command_writes_attribution_and_the_game_file_and_two_passes_are_identical(synth):
    cfg, d, write_lecture, tb = synth
    lines = _run(cfg)
    assert len(lines) == 1 and lines[0].startswith('attribuer Syria/Test :') and '0 B' in lines[0]
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert [s['nom'] for s in att['sorties']] == ['B', None]
    assert att['lettres'][0]['sortie'] == 0 and att['lettres'][0]['rattachee'] and att['lettres'][0]['source'] == 'plan'
    game = cfg.work_dir / 'taxiways_dcs' / 'syria' / 'Test.json'
    doc = json.loads(game.read_text(encoding='utf-8'))
    assert doc['source'] == 'DCS' and doc['theatre'] == 'Syria' and doc['base'] == 'Test' and 'icao' not in doc
    assert [w['ref'] for w in doc['ways']] == ['B', 'B'] and all(w['aeroway'] == 'taxiway' for w in doc['ways'])
    assert all(isinstance(p[0], float) and 30 < p[0] < 40 for w in doc['ways'] for p in w['pts'])   # lat/lon pydcs de Syria
    first = (game.read_bytes(), (d / 'attribution.json').read_bytes())
    _run(cfg)
    assert (game.read_bytes(), (d / 'attribution.json').read_bytes()) == first


def test_command_writes_nothing_else(synth):
    cfg, d, _, _ = synth
    _run(cfg)
    files = sorted(str(p.relative_to(cfg.work_dir)).replace('\\', '/') for p in cfg.work_dir.rglob('*') if p.is_file())
    assert files == ['panneaux/Syria/Test/attribution.json', 'panneaux/Syria/Test/index.json', 'panneaux/Syria/Test/lecture.json',
                     'panneaux/Syria/Test/vues.json', 'taxiways_dcs/syria/Test.json']


def test_command_without_a_named_exit_writes_no_file_and_removes_an_old_one(synth):
    cfg, d, write_lecture, tb = synth
    _run(cfg)
    game = cfg.work_dir / 'taxiways_dcs' / 'syria' / 'Test.json'
    assert game.is_file()
    write_lecture([dict(tb, car='?')])                       # plus aucune lettre prise
    lines = _run(cfg)
    assert not game.exists() and '0 sortie(s) nommee(s)' in lines[0]
    assert json.loads((d / 'attribution.json').read_text(encoding='utf-8'))['comptes']['lettres'] == 0
    write_lecture([dict(tb, car='B'), dict(tb, car='C')])    # deux lettres differentes a la meme place : conflit
    lines = _run(cfg)
    assert not game.exists() and '1 en conflit' in lines[0]


def test_command_field_option_changes_the_placement(synth):
    cfg, d, _, _ = synth
    _run(cfg, champ=90.0)
    a = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))['lettres'][0]['position']
    _run(cfg, champ=80.0)
    b = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert b['champ'] == 80.0 and b['lettres'][0]['position'] != a


def test_command_unknown_survey_folder_stops(synth):
    cfg, _, _, _ = synth
    import signattr
    with pytest.raises(signattr.AttrError):
        signattr.cmd_attribuer(cfg, 'Syria', ['Nope'], sc.panneaux_dir, sc.find_base, log=lambda *a: None)


def test_main_parses_the_field_option_and_needs_a_base(synth, monkeypatch):
    cfg, d, _, _ = synth
    seen = []
    import signattr
    monkeypatch.setattr(signattr, 'cmd_attribuer', lambda cfg, th, bases, pd, fb, champ=None, log=print: seen.append((th, bases, champ)))
    sc.main(['x', 'attribuer', 'Syria', 'Test'], cfg)
    sc.main(['x', 'attribuer', 'Syria', 'A', '--champ', '75', 'B'], cfg)
    sc.main(['x', 'attribuer', 'Syria', 'A', '--champ=60'], cfg)
    assert seen == [('Syria', ['Test'], signattr.CHAMP_DEG), ('Syria', ['A', 'B'], 75.0), ('Syria', ['A'], 60.0)]
    for bad in (['x', 'attribuer', 'Syria'], ['x', 'attribuer', 'Syria', 'A', '--champ'], ['x', 'attribuer', 'Syria', 'A', '--champ', 'x'],
                ['x', 'attribuer', 'Syria', 'A', '--champ', '0'], ['x', 'attribuer', 'Syria', '--champ', '90']):
        with pytest.raises(SystemExit):
            sc.main(bad, cfg)


def test_image_size_is_read_from_the_image_file_when_it_exists(tmp_path):
    from PIL import Image
    (tmp_path / 'img').mkdir()
    Image.new('RGB', (200, 100)).save(tmp_path / 'img' / '001.png')
    assert sa.image_size(tmp_path, {'img': 'img/001.png'}) == (200, 100)
    assert sa.image_size(tmp_path, {'img': 'img/002.png'}) == sa.IMAGE_DEFAUT
    assert sa.image_size(tmp_path, None) == sa.IMAGE_DEFAUT


# ---------- Frankfurt (echantillons) : les six lettres de la lecture d'AC main ----------
FRANKFURT = ('dcs_install', 'Mods', 'terrains', 'GermanyColdWar', 'AirfieldsTaxiways', 'Frankfurt.rn5')
# vues.json / index.json de la sonde du 10 novembre : cap, tangage, position (hauteur 3,0 m partout)
F_VUES = {15: (349.2, -1.43, (-444024.47, 103.0, -852789.12)), 17: (349.2, -1.43, (-444543.27, 103.0, -852689.85)),
          30: (181.2, -1.43, (-443517.98, 103.0, -851507.94)), 84: (340.5, -2.15, (-444752.74, 103.0, -853192.44)),
          105: (259.5, -2.15, (-443915.38, 103.0, -855108.81))}
# les tuiles de lettres de la lecture (lecture.json) : vue, x, y, w, h, caractere
F_LECTURE = [(15, 273, 732, 24, 30, 'F'), (15, 2865, 732, 28, 28, 'F'), (17, 3275, 754, 37, 40, 'F'),
             (30, 3039, 728, 25, 27, 'B'), (84, 1550, 672, 8, 9, 'G'), (105, 978, 680, 12, 14, 'A')]


@pytest.fixture
def frankfurt(samples, tmp_path):
    dcs = samples / 'dcs_install'
    cfg = _cfg_for(tmp_path, dcs)
    d = cfg.work_dir / 'panneaux' / 'GermanyColdWar' / 'Frankfurt'
    d.mkdir(parents=True)
    vues = [{'numero': n, 'cap': c, 'tangage': t, 'hauteur': 3.0} for n, (c, t, _) in sorted(F_VUES.items())]
    index = [{'numero': n, 'statut': 'faite', 'img': None, 'position': list(p)} for n, (_, _, p) in sorted(F_VUES.items())]
    (d / 'vues.json').write_text(json.dumps({'vues': vues}), encoding='utf-8')
    (d / 'index.json').write_text(json.dumps({'vues': index}), encoding='utf-8')
    views = {}
    for n, x, y, w, h, car in F_LECTURE:
        views.setdefault(n, []).append({'x': x, 'y': y, 'w': w, 'h': h, 'car': car, 'attente': False, 'coupee': False})
    doc = {'vues': [{'vue': n, 'image': 'lue', 'tuiles': t} for n, t in sorted(views.items())]}
    (d / 'lecture.json').write_text(json.dumps(doc), encoding='utf-8')
    return cfg, d


def test_frankfurt_exits_are_the_ones_of_the_survey(samples):
    g = sc.Graph(rn5.read(samples.joinpath(*FRANKFURT)))
    ids = [b[0] for b in sc.bretelles(g)]
    for i in (27, 233, 398, 431):
        assert i in ids


def test_frankfurt_attribution_names_b_f_f_g_and_leaves_the_a_of_view_105_unattached(frankfurt):
    cfg, d = frankfurt
    import signattr
    signattr.cmd_attribuer(cfg, 'GermanyColdWar', ['Frankfurt'], sc.panneaux_dir, sc.find_base, log=lambda *a: None)
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert {s['id']: s['nom'] for s in att['sorties'] if s['nom']} == {27: 'B', 233: 'F', 398: 'F', 431: 'G'}
    assert not any(s['conflit'] for s in att['sorties'])
    got = [(L['vue'], L['car'], L['sortie'], L['distances'][0]['m'], L['distances'][1]['m']) for L in att['lettres']]
    exp = [(15, 'F', 398, 79.2, 328.4), (15, 'F', 398, 77.8, 411.8), (17, 'F', 233, 94.1, 423.4), (30, 'B', 27, 77.4, 569.6),
           (84, 'G', 431, 97.6, 573.5), (105, 'A', None, 374.0, 395.7)]
    for a, b in zip(got, exp):
        assert a[:3] == b[:3] and a[3] == pytest.approx(b[3], abs=0.2) and a[4] == pytest.approx(b[4], abs=0.2)
    assert att['lettres'][5]['motif'] == sa.MOTIF_TROP_LOIN and not att['lettres'][5]['rattachee']
    doc = json.loads((cfg.work_dir / 'taxiways_dcs' / 'germanycoldwar' / 'Frankfurt.json').read_text(encoding='utf-8'))
    assert sorted({w['ref'] for w in doc['ways']}) == ['B', 'F', 'G'] and doc['source'] == 'DCS'


def test_frankfurt_two_passes_are_identical_to_the_byte(frankfurt):
    cfg, d = frankfurt
    import signattr
    out = []
    for _ in range(2):
        signattr.cmd_attribuer(cfg, 'GermanyColdWar', ['Frankfurt'], sc.panneaux_dir, sc.find_base, log=lambda *a: None)
        out.append(((cfg.work_dir / 'taxiways_dcs' / 'germanycoldwar' / 'Frankfurt.json').read_bytes(),
                    (d / 'attribution.json').read_bytes()))
    assert out[0] == out[1]


# ---------- addendum 1 : base sans sortie de piste ; taille de l'image tracee ----------
def test_a_base_without_any_exit_leaves_the_letter_unattached_with_the_reason_and_no_exception():
    L = _letter_at(10.0)
    sa.attach([L], [], [])
    assert not L['rattachee'] and L['sortie'] is None and L['distances'] == [] and L['motif'] == sa.MOTIF_AUCUNE_SORTIE
    assert sa.name_exits([L], []) == []


def test_command_on_a_graph_without_exit_writes_attribution_no_game_file_and_goes_on(synth, monkeypatch):
    cfg, d, _, _ = synth
    nodes = [('taxiway_t', 0, 100), ('taxiway_end_a', 0, -100)]
    segs = [('taxiway_15m', 0, 1, [(0, 90), (0, 0), (0, -90)])]
    monkeypatch.setattr(rn5, 'read', lambda path: mk(nodes, segs))
    # une deuxieme base apres la premiere : elle doit etre traitee
    (cfg.terrains / 'Syria' / 'AirfieldsTaxiways' / 'Suite.rn5').write_bytes(b'x' * 6000)
    d2 = cfg.work_dir / 'panneaux' / 'Syria' / 'Suite'
    d2.mkdir(parents=True)
    for n in ('vues.json', 'index.json', 'lecture.json'):
        (d2 / n).write_bytes((d / n).read_bytes())
    lines = []
    import signattr
    signattr.cmd_attribuer(cfg, 'Syria', ['Test', 'Suite'], sc.panneaux_dir, sc.find_base, log=lines.append)
    assert len(lines) == 2 and lines[1].startswith('attribuer Syria/Suite :')
    for base_dir in (d, d2):
        att = json.loads((base_dir / 'attribution.json').read_text(encoding='utf-8'))
        L = att['lettres'][0]
        assert L['position'] is not None and not L['rattachee'] and L['motif'] == 'aucune sortie' and L['distances'] == []
        assert att['sorties'] == [] and att['comptes']['sorties'] == 0
    assert not (cfg.work_dir / 'taxiways_dcs').exists()


def test_attribution_carries_the_image_size_and_its_source_per_letter_and_the_line_says_how_many_views_used_the_default(synth):
    from PIL import Image
    cfg, d, write_lecture, tb = synth
    lines = _run(cfg)                                          # aucune image : taille par defaut
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert att['lettres'][0]['image'] == {'largeur': 3440, 'hauteur': 1440, 'source': 'défaut'}
    assert lines[0].endswith(' ; 1 vue(s) sans image : taille par défaut 3440x1440')
    (d / 'img').mkdir()
    Image.new('RGB', (3440, 1440)).save(d / 'img' / '001.png')
    idx = json.loads((d / 'index.json').read_text(encoding='utf-8'))
    idx['vues'][0]['img'] = 'img/001.png'
    (d / 'index.json').write_text(json.dumps(idx), encoding='utf-8')
    lines = _run(cfg)                                          # l'image est la : sa taille, sa source, rien en fin de ligne
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert att['lettres'][0]['image'] == {'largeur': 3440, 'hauteur': 1440, 'source': 'image'}
    assert 'sans image' not in lines[0] and lines[0].endswith('0 B)')


def test_the_size_read_from_the_image_is_the_one_used_for_the_ray(synth):
    from PIL import Image
    cfg, d, _, _ = synth
    _run(cfg)
    default = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))['lettres'][0]['position']
    (d / 'img').mkdir()
    Image.new('RGB', (1720, 720)).save(d / 'img' / '001.png')
    idx = json.loads((d / 'index.json').read_text(encoding='utf-8'))
    idx['vues'][0]['img'] = 'img/001.png'
    (d / 'index.json').write_text(json.dumps(idx), encoding='utf-8')
    _run(cfg)
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))['lettres'][0]
    assert att['image'] == {'largeur': 1720, 'hauteur': 720, 'source': 'image'} and att['position'] != default


def test_the_line_counts_distinct_views_that_used_the_default_size(synth):
    from PIL import Image
    cfg, d, write_lecture, tb = synth
    views = json.loads((d / 'vues.json').read_text(encoding='utf-8'))['vues']
    v2, v3 = dict(views[0], numero=2), dict(views[0], numero=3)
    (d / 'vues.json').write_text(json.dumps({'vues': views + [v2, v3]}), encoding='utf-8')
    idx = json.loads((d / 'index.json').read_text(encoding='utf-8'))
    e = idx['vues'][0]
    idx['vues'] += [dict(e, numero=2), dict(e, numero=3)]
    (d / 'img').mkdir()
    Image.new('RGB', (3440, 1440)).save(d / 'img' / '001.png')
    idx['vues'][0]['img'] = 'img/001.png'              # la vue 1 a son image ; les vues 2 et 3 n'en ont pas
    (d / 'index.json').write_text(json.dumps(idx), encoding='utf-8')
    tile = dict(tb, attente=False, coupee=False, car='B', score=0.9, depart=0.9, ecart=1.0)
    doc = {'vues': [{'vue': n, 'image': 'lue', 'tuiles': [tile, tile] if n == 2 else [tile]} for n in (1, 2, 3)]}
    (d / 'lecture.json').write_text(json.dumps(doc), encoding='utf-8')
    lines = _run(cfg)
    assert lines[0].endswith(' ; 2 vue(s) sans image : taille par défaut 3440x1440')
    att = json.loads((d / 'attribution.json').read_text(encoding='utf-8'))
    assert [L['image']['source'] for L in att['lettres']] == ['image', 'défaut', 'défaut', 'défaut']
