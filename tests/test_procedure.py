"""Lot ac-approches-v1 : la geometrie des procedures types (render/procedure.py), sur donnees synthetiques : tangente au virage de
base, cotes (egalite -> droite), attente d'alignement, hippodrome, altitudes de couloir, MSA du secteur de l'attente, traversees
des pistes (addendum 1), conversions d'unites."""
import math

import numpy as np
import pytest

from render import msa, procedure as P
from render.procedure import NM, Balise, Entree


# ---------- unites ----------
def test_unit_conversions():
    assert P.m_up10(4700) == 1440                      # 1432,6 m -> 1440
    assert P.m_up10(9100) == 2780 and P.m_up10(7200) == 2200
    assert P.qfe_m(4700, 3407) == 400                  # 394,1 m -> 400
    assert P.qfe_m(3407 + 10 / 0.3048 * 3.28084 * 0.3048, 3407) == 10
    assert P.kmh_label(80) == 150 and P.kmh_label(70) == 130 and P.kmh_label(110) == 205
    assert P.km(3.1) == pytest.approx(5.7412)
    assert round(P.vs_ftmin(120, 3.0)) == 637 and round(P.vs_ftmin(80, 3.78)) == 535
    assert P.ftmin_to_ms(600) == pytest.approx(3.048, abs=1e-3)
    assert P.mmss(3.8) == '3:48' and P.mmss(2.53) == '2:32' and P.mmss(1.999) == '2:00'
    assert P.turn_radius_m(80) / NM == pytest.approx(0.4244, abs=1e-4)
    assert P.turn_radius_m(165) / NM == pytest.approx(0.8754, abs=1e-4)


def test_segment_altitude_margins_and_rounding():
    assert P.round_alt(1085.4) == 4600                 # 3561 ft + 1000 -> 4600
    assert P.round_alt(304.8) == 2000                  # tombe juste sur 1000 ft : 2000, pas 2100
    assert P.round_alt(1499.9) == 6000                 # 4921 + 1000 -> 6000
    assert P.round_alt(1500.0) == 7000                 # 4921 + 2000 -> 7000 (montagne)


def test_mag_and_along_cross():
    assert P.mag_int(20.7, 3.3) == 17 and P.mag_int(2.0, 3.3) == 359
    thr = (0.0, 0.0)
    assert P.along_cross((100.0, 0.0), thr, 0.0) == pytest.approx((100.0, 0.0))     # au-dela du seuil
    assert P.along_cross((0.0, 50.0), thr, 0.0) == pytest.approx((0.0, 50.0))       # a droite (est) d'un atterrissage au nord
    assert P.axis_point(thr, 90.0, 1.0) == pytest.approx((0.0, -NM))


# ---------- couloirs ----------
def grid(n=40, h=100.0):
    return np.full((n, n), h, np.float32)


def test_corridor_takes_the_highest_node_within_the_half_width():
    alt = grid(100)
    alt[50, 60] = 500.0                                # x = 5000, z = 6000
    alt[50, 40] = 900.0                                # x = 5000, z = 4000 : hors du couloir de 100 m
    pts = [(5000.0, 5500.0), (5000.0, 6500.0)]
    assert P.corridor_max_m(alt, 0.0, 0.0, pts, 100.0) == 500.0
    assert P.corridor_max_m(alt, 0.0, 0.0, pts, 1600.0) == 900.0
    assert P.corridor(alt, 0.0, 0.0, pts, 100.0 / NM) == (500.0, P.round_alt(500.0))


def test_corridor_outside_the_grid_stops():
    with pytest.raises(ValueError):
        P.corridor_max_m(grid(), 0.0, 0.0, [(100.0, 100.0), (300.0, 300.0)], 1000.0)


def test_ground_is_bilinear():
    alt = grid(4, 0.0)
    alt[1, 1], alt[2, 1], alt[1, 2], alt[2, 2] = 0.0, 100.0, 0.0, 100.0
    assert P.ground_m(alt, 0.0, 0.0, (150.0, 150.0)) == pytest.approx(50.0)


# ---------- virage de base ----------
@pytest.mark.parametrize('sg', [1, -1])
def test_base_turn_outbound_is_tangent_and_ends_on_the_axis(sg):
    beacon, end, az, r = (0.0, 500.0), (-20000.0, 0.0), 0.0, 1500.0
    C, h, L, T, arc = P.base_turn(beacon, end, az, r, sg)
    assert math.dist(C, T) == pytest.approx(r)
    v1 = (T[0] - beacon[0], T[1] - beacon[1])
    v2 = (T[0] - C[0], T[1] - C[1])
    assert abs(v1[0] * v2[0] + v1[1] * v2[1]) < 1e-6 * L * r      # la tangente est perpendiculaire au rayon
    assert L == pytest.approx(math.dist(beacon, T))
    assert arc[0] == pytest.approx(T) and arc[-1] == pytest.approx(end)
    # a la fin de l'arc, la direction de vol est celle de la finale (az = 0 : vers +x)
    d = (arc[-1][0] - arc[-2][0], arc[-1][1] - arc[-2][1])
    assert d[0] > 0 and abs(d[1]) < 0.1 * abs(d[0])
    # virage a gauche (sg = +1) : centre a gauche du sens d'atterrissage (ouest, z < 0)
    assert (C[1] < 0) == (sg > 0)


def test_base_turn_with_the_beacon_in_the_circle_is_impossible():
    with pytest.raises(P.GeometrieImpossible):
        P.base_turn((-20000.0, 1000.0), (-20000.0, 0.0), 0.0, 1500.0, -1)


def test_sides_tie_goes_right():
    assert P.side_by_distance(300.0, 300.0) == -1
    assert P.side_by_distance(300.5, 300.0) == -1      # a 1 m pres : egalite
    assert P.side_by_distance(302.0, 300.0) == 1
    assert P.side_by_distance(100.0, 300.0) == -1


# ---------- attente d'alignement, hippodrome ----------
@pytest.mark.parametrize('hs', [1, -1])
def test_alignment_hold_inbound_ends_on_the_beacon_and_is_left_straight(hs):
    beacon, inb, leg, r = (1000.0, 2000.0), 210.0, 4815.2, 1203.8
    pts, ob, hb, ha = P.alignment_hold(beacon, inb, leg, r, hs)
    assert pts[1] == beacon and pts[0] == ha
    assert P.az_of(ha, beacon) % 360 == pytest.approx(inb)          # le rapprochement est le cap de l'eloignement qui suit
    assert math.dist(ha, beacon) == pytest.approx(leg)
    assert math.dist(ob, beacon) == pytest.approx(2 * r)
    side = P.along_cross(ob, beacon, inb)[1]
    assert (side > 0) == (hs > 0)                                    # virages a droite : l'eloignement de l'attente est a droite
    assert pts[-1] == pytest.approx(ha)                              # la boucle se referme


@pytest.mark.parametrize('hs', [1, -1])
def test_racetrack_outbound_runs_abeam_the_final_back_to_the_if(hs):
    beacon, if_pt, az, r = (-300.0, 0.0), (-14000.0, 0.0), 0.0, 1620.0
    a1, o_end, a2 = P.racetrack(beacon, if_pt, az, r, hs)
    assert a1[0] == pytest.approx(beacon) and a2[-1] == pytest.approx(if_pt)
    assert a1[-1] == pytest.approx((beacon[0], beacon[1] + hs * 2 * r))
    assert o_end == pytest.approx((if_pt[0], if_pt[1] + hs * 2 * r))
    assert max(p[0] for p in a1) == pytest.approx(beacon[0] + r, rel=1e-3)   # le demi-tour se fait au-dela de la balise


# ---------- procedures completes, sur un relief synthetique ----------
X0 = Z0 = -60000.0


def relief(base=200.0):
    """Grille de 120 km de cote, sol a `base` m, un relief de 900 m au sud-ouest et de 400 m au nord-est."""
    alt = np.full((1201, 1201), base, np.float32)
    alt[300:320, 300:320] = 900.0                      # x, z ~ -30 km : relevement du noeud vers le centre ~ 045
    alt[880:890, 880:890] = 400.0
    return alt


RUNWAY = [(-1500.0, -25.0), (1500.0, -25.0), (1500.0, 25.0), (-1500.0, 25.0)]     # piste nord-sud, 3 000 m x 50 m, seuil sud en (-1500, 0)


def entree(ref, ndb=None, ils=None, thr_ft=660, elev_ft=660, decl=0.0, gamma=0.0):
    return Entree('36', (-1500.0, 0.0), (1500.0, 0.0), 0.0, 50.0, [RUNWAY], thr_ft, elev_ft, decl, gamma, ref, ndb, ils)


def test_avion_beacon_behind_the_threshold_gives_a_base_turn_on_the_far_side():
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)            # au-dela du bout nord, 400 m a droite (est)
    p = P.avion(entree(vor), relief(), X0, Z0)
    assert p.mode == 'base'
    other = 'LEFT' if p.turn == 'RIGHT' else 'RIGHT'
    assert p.dist_sides[p.turn] > p.dist_sides[other]  # le cote ou l'eloignement passe le plus loin de la piste
    assert p.int_ft == P.round_alt(200.0) == 1700
    # FAF : le 3 degres atteint l'altitude intermediaire, au dixieme de DME
    assert p.faf_dme == round(math.dist(vor.pos, p.FAF) / NM, 1)
    assert p.IF == pytest.approx(P.axis_point((-1500.0, 0.0), 0.0, p.faf_nm + 2.0))
    assert p.init_ft == max(p.corridors['out'][1], p.int_ft)
    assert p.mis_direct                                # balise a plus de 0,5 NM au-dela du seuil : directe
    assert p.d_strict_rw > 0 and p.traversals == []


def test_avion_beacon_before_the_threshold_on_the_axis_gives_a_racetrack():
    vor = Balise('VOR/DME', 'TST', '113.00', -1800.0, 100.0)           # 300 m avant le seuil, dans l'axe a 100 m
    p = P.avion(entree(vor), relief(), X0, Z0)
    assert p.mode == 'racetrack' and p.hold_pts is p.out_pts
    assert p.qdr_out == 180 and p.hold_inb_az == 0.0
    assert not p.mis_direct and p.mis_right


def test_hold_altitude_is_the_msa_of_its_sector():
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    alt = relief()
    p = P.avion(entree(vor), alt, X0, Z0)
    m = msa.msa_terrain(alt, X0, Z0, vor.x, vor.z, 0.0, 0.0)
    k = P.sector_of(P.centroid(p.hold_pts), vor.pos, 0.0, 0.0)
    assert p.msa == m and p.hold_msa == m['sectors'][k]['msa_ft']
    assert p.hold_ft == max(p.hold_msa, p.corridors['hold'][1], p.init_ft)
    assert p.hold_msa < m['msa_ft']                    # pas le maximum des quatre secteurs


def test_avion_ils_final_uses_the_glide_antenna():
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    p0 = P.avion(entree(vor), relief(), X0, Z0)
    p1 = P.avion(entree(vor, ils={'s_gs': 300.0, 'tch': 50, 'loc': 'ITST 110.10'}), relief(), X0, Z0)
    assert p1.ils and p1.gp_alt(0.0) == pytest.approx(660 + 300.0 * P.TAN3 * P.FT)
    assert p0.gp_alt(0.0) == 660 + 50


def test_option3_ndb_gives_the_qdm_checks():
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    ndb = Balise('NDB', 'TS', '400', 1800.0, 900.0)
    p = P.avion(entree(vor, ndb=ndb), relief(), X0, Z0)
    assert p.qdm_faf == P.mag_int(P.az_of(p.FAF, ndb.pos), 0.0)
    assert p.qdm_thr == P.mag_int(P.az_of((-1500.0, 0.0), ndb.pos), 0.0)


def test_helico_one_altitude_and_faf_at_3_1_nm():
    ndb = Balise('NDB', 'TS', '400', 1800.0, 700.0)
    p = P.helico(entree(ndb), relief(), X0, Z0)
    assert p.zone_ft == max(p.corridors['hold'][1], p.corridors['out'][1], p.corridors['mis'][1])
    assert p.hold_ft == p.init_ft == p.mis_ft == p.zone_ft
    assert p.FAF == pytest.approx(P.axis_point((-1500.0, 0.0), 0.0, 3.1))
    assert p.arc[-1] == pytest.approx(p.FAF)
    assert p.slope == pytest.approx(math.degrees(math.atan((p.zone_ft - 660 - 50) / (3.1 * NM * P.FT))))
    assert p.t_bt == pytest.approx(p.l_out_nm / 80 * 60)
    # attente : 1 min a 80 kt
    assert math.dist(p.hold_ha, ndb.pos) == pytest.approx(80 / 60 * NM)


# ---------- traversees (addendum 1) ----------
def test_hold_over_the_runway_is_a_traversal_with_its_height():
    vor = Balise('VOR/DME', 'TST', '113.00', 0.0, 300.0)               # au milieu de la piste, 300 m a l'est
    p = P.avion(entree(vor), relief(), X0, Z0)
    assert p.traversals
    for name, k, h in p.traversals:
        assert k == 0
        if name == 'attente':
            assert h == p.hold_ft - 660
        else:
            assert p.init_ft - 660 <= h <= p.hold_ft - 660
    assert p.d_strict_rw > 0


def test_out_alt_follows_the_profile_line():
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    p = P.avion(entree(vor), relief(), X0, Z0)
    assert p.out_alt(vor.pos) == pytest.approx(p.hold_ft)
    assert p.out_alt(p.IF) == pytest.approx(p.init_ft)
    mid = P.axis_point((-1500.0, 0.0), 0.0, (p.if_nm - p.v_par_nm) / 2)
    assert p.out_alt(mid) == pytest.approx((p.hold_ft + p.init_ft) / 2)


def test_crossings_report_the_lowest_altitude_inside_the_polygon():
    from shapely.geometry import Polygon
    poly = Polygon(RUNWAY)
    out = P.crossings([(0.0, -100.0), (0.0, 100.0)], [poly], lambda c: 1000.0 + c[1])
    assert out == [(0, pytest.approx(975.0))]
    assert P.crossings([(0.0, 100.0), (0.0, 300.0)], [poly], lambda c: 0.0) == []


# ---------- addendum 2 : declinaison au seuil, signes, egalites, FAF vers l'exterieur, FAF au-dela de 10 NM ----------
def hand_mag(a, b, decl):
    """Cap magnetique de a vers b, calcule a la main : azimut grille (atan2(dz, dx)) - declinaison, arrondi, 0 a 359."""
    return int(round((math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) - decl) % 360.0)) % 360


def entree_d(ref, ndb=None, decl=3.0, gamma=-4.0, decl_thr=4.0, runways=(RUNWAY,)):
    return Entree('36', (-1500.0, 0.0), (1500.0, 0.0), 0.0, 50.0, list(runways), 660, 660, decl, gamma, ref, ndb, None, decl_thr)


def test_racetrack_bearings_with_declination_and_convergence():
    vor = Balise('VOR/DME', 'TST', '113.00', -1800.0, 0.0)
    ndb = Balise('NDB', 'TS', '400', -1500.0, 1000.0)                  # a l'est du seuil
    p = P.avion(entree_d(vor, ndb), relief(), X0, Z0)
    assert p.mode == 'racetrack'
    assert p.crs == 356                                # finale 000 grille, declinaison au seuil 4 E
    assert p.qdr_out == 176                            # eloignement de l'hippodrome : reciproque de l'axe, declinaison au seuil
    assert p.qdm_thr == 87                             # NDB plein est du seuil (090 grille), declinaison a la balise 3 E
    assert p.qdm_faf == hand_mag(p.FAF, ndb.pos, 3.0)
    assert p.msa == msa.msa_terrain(relief(), X0, Z0, vor.x, vor.z, -4.0, 3.0)      # la convergence ne sert qu'a la MSA


def test_base_turn_bearings_and_radials_with_declination():
    vor = Balise('VOR/DME', 'TST', '113.00', 500.0, 1500.0)            # hors de l'axe (1 500 m), au-dela du seuil
    p = P.avion(entree_d(vor), relief(), X0, Z0)
    assert p.mode == 'base' and p.crs == 356
    assert p.qdr_out == hand_mag(vor.pos, p.T, 3.0)                    # QDR de l'eloignement depuis la balise : declinaison a la balise
    assert p.rad_thr == hand_mag(vor.pos, (-1500.0, 0.0), 3.0) == 214
    assert p.rad_faf == hand_mag(vor.pos, p.FAF, 3.0)


def test_helico_bearings_with_declination():
    ndb = Balise('NDB', 'TS', '400', -1500.0, 1000.0)
    p = P.helico(entree_d(ndb, decl=3.0, gamma=-4.0, decl_thr=4.0), relief(), X0, Z0)
    assert p.crs == 356 and p.qdm_thr == 87
    assert p.qdm_faf == hand_mag(p.FAF, ndb.pos, 3.0)
    assert p.qdr_out == hand_mag(ndb.pos, p.T, 3.0)


def test_ties_go_right_for_the_hold_and_the_racetrack():
    hs, _, d = P._hold_pick((0.0, 0.0), 30.0, 4000.0, 1000.0, [])
    assert hs == 1 and d[1] == d[-1]
    vor = Balise('VOR/DME', 'TST', '113.00', -1800.0, 0.0)            # dans l'axe : les deux cotes sont a la meme distance
    p = P.avion(entree_d(vor, decl=0.0, gamma=0.0, decl_thr=0.0), relief(), X0, Z0)
    assert p.mode == 'racetrack' and abs(p.dist_sides['RIGHT'] - p.dist_sides['LEFT']) < 1.0
    assert p.turn == 'RIGHT' and p.hold_turns == 'RIGHT TURNS'


def test_faf_is_rounded_outward():
    ndb = Balise('NDB', 'TS', '400', 1800.0, 900.0)
    p = P.avion(entree_d(ndb), relief(), X0, Z0)
    raw = (p.int_ft - 660 - 50) / (P.TAN3 * P.FT * NM)
    assert p.faf_nm >= raw and p.faf_nm == math.ceil(raw * 10) / 10
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    p = P.avion(entree_d(vor), relief(), X0, Z0)
    raw = (p.int_ft - 660 - 50) / (P.TAN3 * P.FT * NM)
    assert p.faf_nm >= raw - 1e-9
    assert math.dist(vor.pos, p.FAF) / NM == pytest.approx(p.faf_dme, abs=1e-6)
    assert p.faf_dme == math.ceil(math.dist(vor.pos, P.axis_point((-1500.0, 0.0), 0.0, raw)) / NM * 10) / 10


def test_faf_beyond_10_nm_drops_the_runway_direction():
    alt = relief()
    alt[:, :] = 1400.0                                  # sol haut tout le long de l'axe : intermediaire a 5 600 ft
    vor = Balise('VOR/DME', 'TST', '113.00', 1800.0, 400.0)
    with pytest.raises(P.GeometrieImpossible, match=r"^FAF à 1\d\.\d NM du seuil \(relief dans l'axe\), au-delà de 10 NM$"):
        P.avion(entree_d(vor), alt, X0, Z0)
    ndb = Balise('NDB', 'TS', '400', 1800.0, 700.0)
    assert P.helico(entree_d(ndb), alt, X0, Z0).faf_nm == 3.1       # la page helicoptere n'est pas concernee
