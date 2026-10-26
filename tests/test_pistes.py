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


def test_pairing_beyond_12_degrees_is_refused():
    assert runways.pair_marked([100.0], [{'heading': 113.0, 'blocks': 5, 'width': 40.0}]) == {}
    assert runways.pair_marked([100.0], [{'heading': 112.0, 'blocks': 5, 'width': 40.0}]) == {0: 0}
    assert runways.heading_diff(179.0, 1.0) == pytest.approx(2.0)


def test_pairing_free_prefers_widest():
    """Regle 3 : le taxiway parallele est plus etroit que sa piste."""
    cands = [{'heading': 271.0, 'width': 22.0}, {'heading': 266.0, 'width': 41.0}]
    assert runways.pair_free([270.0], [0], cands) == {0: 1}


def test_pairing_free_most_runways_first():
    cands = [{'heading': 100.0, 'width': 60.0}, {'heading': 104.0, 'width': 30.0}]
    assert runways.pair_free([100.0, 106.0], [0, 1], cands) == {0: 0, 1: 1}


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
