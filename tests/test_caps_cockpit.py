"""Lot caps-cockpit : le cap d'une extremite de piste est l'azimut grille moins la declinaison (ce que lit le compas du
cockpit, le nord de la grille etant le nord du jeu) ; le bloc du nord montre le nord du jeu (fleche fine, droite) et le nord
magnetique (fleche pleine, tournee de la declinaison). Moteur et `republish.py` partagent les memes fonctions."""
import html
import math
import re

import pytest

from render import analysis, compose, geo, page, republish, svgdoc
from render.fonts import pt
from render.republish import RepublishError

FRAME = (11.0, 38.1, 126.0, 131.0)
S = 0.0633
CX, CY = 21.0, 156.6


def end(label, thr, az, hw=0.7):
    a = math.radians(az)
    return page.CapEnd(label, thr, (math.sin(a), -math.cos(a)), hw, az)


def synthetic(t, p, plan=('090.7°', '270.7°'), table=None, year=1985, extra_arrow=False, var=None):
    """Page 10-9 minimale d'avant le lot (fleches a t et p) : piste verticale, deux caps a leur place d'origine, tableau (colonne
    CAP MAG a x = 20), bloc du nord, barre d'echelle."""
    doc = svgdoc.Doc()
    doc.add_clip('clip-plan', *FRAME)
    hw, top = 0.7, 60.0
    doc.add('Runways', f'<polygon points="{hw + 60},{top} {hw + 60},120 {60 - hw},120 {60 - hw},{top}" fill="#1A1A1A"/>')
    for e, lab in zip((end('x', (60.0, top), 180.0), end('x', (60.0, 120.0), 0.0)), plan):
        cx, cy, rot = page.cap_original(e, S)
        doc.add('Runway bearings', page.cap_element(lab, cx, cy, rot))
    doc.add('Runway table', svgdoc.text(20.0, 170.0, 'CAP MAG', pt(5.2), 'semibold', svgdoc.INK2, spacing=0.4))
    for lab, y in zip(plan if table is None else table, (176.0, 180.0)):
        doc.add('Runway table', svgdoc.text(20.0, y, lab, pt(7.2), 'bold'))
    doc.add('Frame & grid', svgdoc.rect(*FRAME, 'none', svgdoc.INK, 0.35))
    d = p - t
    vtxt, ytxt = geo.var_text(d, year)
    for el in page.north_arrows(CX, CY, t, p):
        doc.add('Frame & grid', el)
    if extra_arrow:
        doc.add('Frame & grid', page.north_arrows(CX, CY, 1.0, 2.0)[1])
    doc.add('Frame & grid', svgdoc.text(CX, CY + 3.4, var or vtxt, pt(5.9), 'bold', svgdoc.INK, 'middle'))
    doc.add('Frame & grid', svgdoc.text(CX, CY + 6.0, ytxt, pt(5.4), 'regular', svgdoc.INK2, 'middle'))
    sb, _ = page.scale_block(94.0, 158.6, S)
    for el in sb:
        doc.add('Frame & grid', el)
    return doc.render()


def layer_texts(svg, label):
    m = re.search(r'inkscape:label="' + re.escape(label.replace('&', '&amp;')) + r'"[^>]*>\n(.*?)\n</g>\n', svg, re.S)
    return re.findall(r'>(\d{3}\.\d°)</text>', m.group(1))


# ---------- M5 : le cas synthetique du cadrage ----------
def test_synthetic_page_thin_7_29_solid_6_905():
    svg = synthetic(7.29, 6.905)
    new, det = republish.retouch_north_and_caps(svg, 'T_10-9')
    assert layer_texts(new, 'Runway bearings') == ['098.0°', '278.0°']
    assert layer_texts(new, 'Runway table') == ['098.0°', '278.0°']
    assert det['t'] == 7.29 and det['p'] == 6.905 and det['thin'] == '0' and det['solid'] == '-0.385'
    assert det['caps'] == [('Runway bearings', '090.7°', '098.0°'), ('Runway bearings', '270.7°', '278.0°'),
                           ('Runway table', '090.7°', '098.0°'), ('Runway table', '270.7°', '278.0°')]
    for el in page.north_arrows(CX, CY, 0.0, -0.385):
        assert el in new
    assert 'rotate(7.29)' not in new and 'rotate(6.905)' not in new


def test_only_the_caps_and_the_two_arrows_change():
    svg = synthetic(7.29, 6.905)
    new, _ = republish.retouch_north_and_caps(svg, 'T_10-9')
    a, b = svg.split('\n'), new.split('\n')
    assert len(a) == len(b)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(diff) == 2 + 4                                   # deux fleches, quatre textes de cap
    assert sum(1 for x, _ in diff if x.startswith('<g transform="translate(21 156.6)')) == 2


def test_republished_page_is_stable():
    new, _ = republish.retouch_north_and_caps(synthetic(7.29, 6.905), 'T_10-9')
    again, det = republish.retouch_north_and_caps(new, 'T_10-9')
    assert again == new and det['t'] == 0 and all(o == w for _, o, w in det['caps'])
    # la meme page, ecrite par le moteur (t = 0), est deja convertie
    again, _ = republish.retouch_north_and_caps(synthetic(0.0, -0.385, plan=('098.0°', '278.0°')), 'T_10-9')
    assert again == synthetic(0.0, -0.385, plan=('098.0°', '278.0°'))


def test_a_360_label_is_left_alone_when_the_page_is_converted():
    svg = synthetic(0.0, 3.0, plan=('360.0°', '180.0°'))
    new, _ = republish.retouch_north_and_caps(svg, 'T_10-9')
    assert new == svg


@pytest.mark.parametrize('old, t, want', [('090.7°', 7.29, '098.0°'), ('359.5°', 1.0, '000.5°'), ('005.0°', -11.194, '353.8°'),
                                          ('055.0°', 11.194, '066.2°'), ('180.0°', 0.0, '180.0°')])
def test_convert_cap(old, t, want):
    assert republish.convert_cap(old, t) == want


# ---------- les arrets du 2.3 ----------
def test_stops_on_three_arrows():
    with pytest.raises(RepublishError, match='3 fleches'):
        republish.retouch_north_and_caps(synthetic(7.29, 6.905, extra_arrow=True), 'T_10-9')


def test_stops_on_a_page_without_arrows():
    with pytest.raises(RepublishError, match='0 fleches'):
        republish.retouch_north_and_caps('<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'T_10-9')


def test_stops_when_var_is_not_the_one_the_engine_writes_for_p_minus_t():
    with pytest.raises(RepublishError, match='VAR'):
        republish.retouch_north_and_caps(synthetic(7.29, 6.905, var='VAR 5°E'), 'T_10-9')


def test_stops_when_plan_and_table_have_different_cap_counts():
    with pytest.raises(RepublishError, match='2 caps sur le plan et 1 dans le tableau'):
        republish.retouch_north_and_caps(synthetic(7.29, 6.905, table=('090.7°',)), 'T_10-9')


def test_stops_when_an_arrow_differs_from_the_engine_arrow():
    svg = synthetic(7.29, 6.905).replace('stroke-width="0.6"', 'stroke-width="0.7"', 1)
    with pytest.raises(RepublishError, match='fleche du nord'):
        republish.retouch_north_and_caps(svg, 'T_10-9')


# ---------- un seul code pour le moteur et pour republish ----------
def test_engine_and_republish_write_the_north_block_with_the_same_function(monkeypatch):
    calls = []
    real = page.north_arrows
    monkeypatch.setattr(page, 'north_arrows', lambda *a: calls.append(a) or real(*a))
    block = page.north_block(CX, CY, 3.29, 1985)
    assert calls == [(CX, CY, 0.0, 3.29)] and block[:2] == real(CX, CY, 0.0, 3.29)
    calls.clear()
    republish.retouch_north_and_caps(synthetic(7.29, 10.58), 'T_10-9')
    assert any(a[:3] == (CX, CY, 0.0) and a[3] == pytest.approx(3.29) for a in calls)       # republish ecrit les fleches par la meme fonction


def test_north_block_arrows_and_texts():
    out = page.north_block(CX, CY, -0.385, 1985)
    assert out[0].startswith(f'<g transform="translate({CX:g} {CY:g}) rotate(0)">') and 'stroke-width="0.3"' in out[0] and '<polyline' in out[0]
    assert out[1].startswith(f'<g transform="translate({CX:g} {CY:g}) rotate(-0.385)">') and 'stroke-width="0.6"' in out[1] and '<polygon' in out[1]
    assert 'VAR 0°W' in out[2] and '(1985)' in out[3]


def test_republish_output_equals_what_the_engine_writes():
    """Une page ecrite avant le lot, convertie, a le bloc du nord que le moteur ecrit pour la meme declinaison."""
    new, _ = republish.retouch_north_and_caps(synthetic(7.29, 10.58), 'T_10-9')
    engine = synthetic(0.0, 3.29)
    assert [l for l in new.split('\n') if 'rotate(' in l and l.startswith('<g transform="translate(21')] == \
        [l for l in engine.split('\n') if l.startswith('<g transform="translate(21')]


# ---------- le moteur, sur les echantillons ----------
@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_engine_caps_are_grid_heading_minus_declination(charts, base):
    data, pages = charts[base]
    for e in data.ends:
        assert e.mag == pytest.approx((e.az_grid - data.decl) % 360.0, abs=1e-9)
    svg = pages[0].svg
    for e in data.ends:
        assert f'{e.mag:05.1f}°' in layer_texts(svg, 'Runway bearings') and f'{e.mag:05.1f}°' in layer_texts(svg, 'Runway table')


@pytest.mark.parametrize('base', ['Konya', 'Adana', 'Ramat_David'])
def test_engine_north_arrows_game_north_and_magnetic_north(charts, base):
    data, pages = charts[base]
    rots = re.findall(r'<g transform="translate\([-\d.]+ [-\d.]+\) rotate\(([-\d.]+)\)"><line [^>]*stroke-width="(0\.[36])"', pages[0].svg)
    assert sorted(rots, key=lambda r: r[1]) == [('0', '0.3'), (f'{data.decl:.3f}'.rstrip('0').rstrip('.'), '0.6')]


def test_engine_page_is_already_converted_for_republish(charts):
    data, pages = charts['Konya']
    t, p, *_ = republish.read_north(pages[0].svg, 'Konya_10-9')
    assert t == 0 and p == pytest.approx(data.decl, abs=0.001)
    new, det = republish.retouch_north_and_caps(pages[0].svg, 'Konya_10-9')
    assert new == pages[0].svg


# ---------- M6 : l'appariement garde la convergence ----------
def test_mag_heading_keeps_the_convergence_for_matching():
    src = open(analysis.__file__, encoding='utf-8').read()
    m = re.search(r'def mag_heading\(ln\):.*?return ([^\n]+)', src, re.S)
    assert 'proj.convergence(*ln[\'p\'])' in m.group(1) and 'decl' in m.group(1)


# ---------- le placement refait voit les nouveaux textes ----------
def test_a_cap_already_placed_along_the_runway_is_found_with_its_old_text_and_placed_with_the_new_one():
    """Un cap pose le long de la piste l'a ete pour la largeur de son ancien texte (les chiffres n'ont pas tous la meme largeur) :
    `retouch_labels` le retrouve avec l'ancien texte (`old_labels`), puis le place avec le nouveau."""
    top = 60.0
    e_old = end('111.1°', (60.0, top), 180.0)
    cx, cy, rot = next(iter(page.cap_candidates(e_old)))
    doc = svgdoc.Doc()
    doc.add_clip('clip-plan', *FRAME)
    doc.add('Runways', f'<polygon points="60.7,{top} 60.7,120 59.3,120 59.3,{top}" fill="#1A1A1A"/>')
    doc.add('Runway bearings', page.cap_element('111.1°', cx, cy, rot))
    e2 = end('x', (60.0, 120.0), 0.0)
    c2 = page.cap_original(e2, S)
    doc.add('Runway bearings', page.cap_element('291.1°', *c2))
    doc.add('Runway table', svgdoc.text(20.0, 176.0, '111.1°', pt(7.2), 'bold'))
    doc.add('Runway table', svgdoc.text(20.0, 180.0, '291.1°', pt(7.2), 'bold'))
    doc.add('Frame & grid', svgdoc.rect(*FRAME, 'none', svgdoc.INK, 0.35))
    for el in page.north_block(CX, CY, 0.0, 1985)[2:]:
        doc.add('Frame & grid', el)
    for el in page.north_arrows(CX, CY, -4.0, -4.0 + 0.0):
        doc.add('Frame & grid', el)
    sb, _ = page.scale_block(94.0, 158.6, S)
    for el in sb:
        doc.add('Frame & grid', el)
    svg = doc.render()
    new, det = republish.retouch_north_and_caps(svg, 'T_10-9')
    old = [o for layer, o, _ in det['caps'] if layer == 'Runway bearings']
    assert old == ['111.1°', '291.1°'] and [n for lay, _, n in det['caps'] if lay == 'Runway bearings'] == ['107.1°', '287.1°']
    with pytest.raises(RepublishError, match='aucune piste'):
        republish.retouch_labels(new, 'T_10-9')                 # sans l'ancien texte, la place publiee n'est pas une position du 2.1
    out, moves, _ = republish.retouch_labels(new, 'T_10-9', old)
    assert 'rotate(' in out and layer_texts(out, 'Runway bearings') == ['107.1°', '287.1°']
