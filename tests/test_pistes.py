"""Lot pistes-v1 : detection des pistes guidee par pydcs (appariement par cap, pistes non marquees, base sans radio).
Les tests sur mosaique s'executent quand DCS_CHARTS_SAMPLES designe le dossier `echantillons/` du lot ; les tests
d'appariement et de nommage sont purs (aucun echantillon)."""
import html
import re

import pytest

from render import analysis, compose, naming, runways


def texts(svg):
    return [html.unescape(t) for t in re.findall(r'<text[^>]*>([^<]*)</text>', svg)]


# ---------- appariement et nommage : cas synthetiques ----------
def test_pairing_two_runways_10deg_apart_both_offset_6deg():
    """Comme Beirut 16/34 et 17/35 : 160 et 170 dans pydcs, candidats a 166 et 176. Un appariement 'le plus proche
    d'abord' prendrait 166 pour la piste 17 et laisserait 176 sans piste ; l'appariement optimal garde les deux."""
    ref = [170.0, 160.0]                                              # 17/35 puis 16/34
    cands = [{'heading': 166.0, 'blocks': 6, 'width': 45.0}, {'heading': 176.0, 'blocks': 6, 'width': 45.0}]
    assert runways.pair_marked(ref, cands) == {0: 1, 1: 0}


def test_pairing_each_runway_keeps_its_name():
    import math
    # deux pistes a 10 deg d'ecart (caps magnetiques 166 et 176 mesures), pydcs : 16/34 (160/340) et 17/35 (170/350)
    def seg(y, cap):
        a = math.radians(cap)
        return ((0.0, y), (3000 * math.cos(a), y + 3000 * math.sin(a)))
    rws = [seg(0.0, 166.0), seg(900.0, 176.0)]
    approaches = [('17', 170.0), ('35', 350.0), ('16', 160.0), ('34', 340.0)]
    hints = [[('16', 160.0), ('34', 340.0)], [('17', 170.0), ('35', 350.0)]]
    out = naming.name_ends(rws, approaches, hints)
    assert [out[0][0][0], out[0][1][0]] == ['16', '34']
    assert [out[1][0][0], out[1][1][0]] == ['17', '35']
    # sans la piste pydcs appariee, le cap le plus proche donne 17 aux deux : c'est ce que l'appariement evite
    plain = naming.name_ends(rws, approaches)
    assert plain[0][0][0].startswith('17')


def test_pairing_marked_drops_unpaired_taxiway():
    """Cas Paphos : une seule piste pydcs, deux candidats marques ; le mieux apparie (cap, puis blocs) est garde."""
    cands = [{'heading': 107.4, 'blocks': 18, 'width': 59.0}, {'heading': 108.9, 'blocks': 2, 'width': 70.0}]
    assert runways.pair_marked([108.0], cands) == {0: 0}


def test_pairing_beyond_13_degrees_is_refused():
    assert runways.pair_marked([100.0], [{'heading': 113.5, 'blocks': 5, 'width': 40.0}]) == {}
    assert runways.pair_marked([100.0], [{'heading': 112.9, 'blocks': 5, 'width': 40.0}]) == {0: 0}
    assert runways.heading_diff(179.0, 1.0) == pytest.approx(2.0)


def cand(heading, width, length, blocks=0):
    return {'heading': heading, 'width': width, 'length': length, 'blocks': blocks}


def test_pairing_free_ignores_short_wide_block():
    """Cas Nejrab : un bloc large de 403 m ne pese rien face a la piste de 2 714 m (longueur >= 75 % du plus long)."""
    cands = [cand(93.0, 78.0, 403.0), cand(93.2, 50.0, 2714.0, 16)]
    assert runways.pair_free([90.0], [0], cands) == {0: 1}


def test_pairing_free_width_decides_between_equal_lengths():
    """Cas Abu al-Duhur : piste et taxiway parallele de meme longueur, la largeur departage."""
    cands = [cand(271.0, 22.0, 2990.0), cand(266.0, 41.0, 2994.0)]
    assert runways.pair_free([270.0], [0], cands) == {0: 1}


def test_pairing_free_length_share_75_percent():
    cands = [cand(270.0, 60.0, 2000.0), cand(270.0, 30.0, 3000.0)]       # 2000 < 0,75 x 3000 : le large est ecarte
    assert runways.pair_free([270.0], [0], cands) == {0: 1}
    cands = [cand(270.0, 60.0, 2300.0), cand(270.0, 30.0, 3000.0)]       # 2300 >= 2250 : la largeur decide
    assert runways.pair_free([270.0], [0], cands) == {0: 0}


def test_pairing_free_most_runways_first():
    cands = [cand(100.0, 60.0, 3000.0), cand(104.0, 30.0, 3000.0)]
    assert runways.pair_free([100.0, 106.0], [0, 1], cands) == {0: 0, 1: 1}


def test_pairing_free_beyond_13_degrees_refused():
    assert runways.pair_free([100.0], [0], [cand(113.5, 50.0, 3000.0)]) == {}
    assert runways.pair_free([100.0], [0], [cand(112.9, 50.0, 3000.0)]) == {0: 0}


# ---------- echantillons ----------
@pytest.fixture(scope='session')
def pistes(work):
    out = {}
    for base in ('Abu_al-Duhur', 'Paphos', 'Gulechoba'):
        data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
        out[base] = (data, compose.build_pages(data, log=lambda *a: None))
    return out


def test_abu_al_duhur_one_unmarked_runway_27_09(pistes):
    data, pages = pistes['Abu_al-Duhur']
    assert len(data.runways) == 1
    rw = data.runways[0]
    assert rw.marked is False and rw.n_blocks < 2
    assert sorted(e.name for e in data.ends) == ['09', '27']
    assert rw.length == pytest.approx(2994, rel=0.05)
    t = texts(pages[0].svg)
    assert '09' in t and '27' in t


def test_paphos_one_runway_11_29(pistes):
    data, _ = pistes['Paphos']
    assert len(data.runways) == 1
    assert sorted(e.name for e in data.ends) == ['11', '29']


def test_gulechoba_renders_without_tower_radio(pistes):
    data, pages = pistes['Gulechoba']
    assert data.tower == {'vhf': '—', 'uhf': '—', 'fm': '—', 'hf': '—'}
    t = texts(pages[0].svg)
    assert 'GULECHOBA TOWER' in t
    assert t.count('—') >= 4
    assert len(data.runways) == 1


# ---------- cadre du plan (P11) ----------
def test_plan_view_contains_runway_ends():
    """Une piste qui depasse le revetement (rings) doit entrer en entier dans le cadre du plan."""
    from types import SimpleNamespace
    import numpy as np
    ring = np.array([[0.0, 0.0], [0.0, 100.0], [100.0, 100.0], [100.0, 0.0]])
    rw = runways.Runway((50.0, -800.0), (50.0, 900.0), 45.0)
    data = SimpleNamespace(pav_rings=[ring], parkings=[SimpleNamespace(x=50.0, z=50.0)], runways=[rw])
    frame = (10.0, 30.0, 120.0, 100.0)
    view = compose.plan_view(data, frame)
    for p in (rw.p0, rw.p1):
        x, y = view.pt(*p)
        assert frame[0] <= x <= frame[0] + frame[2] and frame[1] <= y <= frame[1] + frame[3]
