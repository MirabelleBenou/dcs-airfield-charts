"""Guide de lecture des charts (render/guide.py, render/build_guide.py). Cas synthetiques : ni les zips d'une release ni les
echantillons ne sont necessaires."""
import copy
import io
import json
import zipfile

import pytest

from render import build_guide, guide

NS = ('xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"')


def layer(label, inner, extra=''):
    return f'<g inkscape:groupmode="layer" id="layer-{label.lower()}" inkscape:label="{label}"{extra}>{inner}</g>'


def svg_page(*layers):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg {NS} width="148mm" height="210mm" viewBox="0 0 148 210">\n'
            f'<defs><clipPath id="clip-plan"><rect x="0" y="0" width="148" height="210"/></clipPath></defs>\n'
            + '\n'.join(layers) + '\n</svg>\n')


def txt(x, y, s, extra='', size=2):
    return f'<text x="{x}" y="{y}" font-family="Barlow Condensed" font-size="{size}"{extra}>{s}</text>'


def page_with_everything():
    """Une page synthetique : halo, doublon, groupe tourne, texte a occurrences, encre dans deux couches."""
    header = layer('Header', txt(6, 11.4, 'LTAN') + txt(6, 11.4, 'LTAN', ' class="halo"') + txt(142, 11.4, '10-9', ' text-anchor="end"'))
    marks = layer('Runway bearings',
                  '<g transform="translate(92.632 51.69) rotate(200.67)">' + txt(0, 0.79, '197.4°', ' text-anchor="middle"') + '</g>'
                  + '<g transform="translate(85.903 49.223) rotate(20.73)">' + txt(0, 0.79, '197.4°', ' text-anchor="middle"') + '</g>'
                  + '<g transform="translate(10 20)"><g transform="scale(2)">' + txt(5, 5, 'NESTED') + '</g></g>')
    ink = layer('Navaids', '<circle cx="61.5" cy="44.2" r="1.35" fill="#FFFFFF" stroke="#1A1A1A" stroke-width="0.3"/>')
    other = layer('Frame', '<rect x="100" y="100" width="10" height="10" fill="#000000"/>' + txt(50, 60, 'TWICE') + txt(52, 80, 'TWICE'))
    return svg_page(header, marks, ink, other)


@pytest.fixture
def page():
    return guide.SvgPage('synth', page_with_everything())


# --------------------------------------------------------------------------------------------- cibles : un texte
def test_text_target_ignores_halo_duplicates(page):
    el = page.find_text('Header', 'LTAN')
    assert (el.x, el.y) == (6.0, 11.4)
    assert [t.text for t in page.texts() if t.layer == 'Header'] == ['LTAN', '10-9']


def test_text_target_applies_rotated_parent_group_transform_beyond_180_degrees(page):
    el = page.find_text('Runway bearings', '197.4°', occurrence=1)
    # (0, 0.79) tourne de 200,67 degres (rotation portee par le <g> parent) puis translate(92.632 51.69)
    assert el.x == pytest.approx(92.632 + 0.79 * 0.3528, abs=0.01)
    assert el.y == pytest.approx(51.69 - 0.79 * 0.9356, abs=0.01)


def test_text_box_follows_anchor_width_and_rotation(page):
    el = page.find_text('Runway bearings', '197.4°', occurrence=1)
    c = el.corners()
    w = guide.fonts.text_width('197.4°', 'regular', 2.0)
    side = ((c[1][0] - c[0][0]) ** 2 + (c[1][1] - c[0][1]) ** 2) ** 0.5
    assert side == pytest.approx(w)                              # la largeur mesuree, tournee avec le groupe
    cx = sum(p[0] for p in c) / 4
    cy = sum(p[1] for p in c) / 4
    ex, ey = guide.apply(el.m, 0.0, 0.79 + (guide.LINE_DESCENT - guide.LINE_ASCENT) * 2.0 / 2)   # milieu de la boite de ligne (text-anchor middle)
    assert (cx, cy) == pytest.approx((ex, ey), abs=1e-6)
    start = page.find_text('Header', 'LTAN').box()
    end = page.find_text('Header', '10-9').box()
    wl, wr = guide.fonts.text_width('LTAN', 'regular', 2.0), guide.fonts.text_width('10-9', 'regular', 2.0)
    assert start[0] == 6.0 and start[2] == pytest.approx(6.0 + wl)          # text-anchor start
    assert end[2] == 142.0 and end[0] == pytest.approx(142.0 - wr)          # text-anchor end


def test_rotated_text_box_is_not_its_bounding_rectangle():
    svg = svg_page(layer('L', '<g transform="translate(50 50) rotate(45)">' +
                         txt(0, 0, 'HELLO WORLD', size=4) + '</g>'))
    el = guide.SvgPage('r', svg).find_text('L', 'HELLO WORLD')
    x0, y0, x1, y1 = el.box()
    assert guide.box_distance((x0 + x1) / 2, (y0 + y1) / 2, el.corners()) == 0.0
    assert guide.box_distance(x1, y0, el.corners()) > 1.0        # le coin du rectangle englobant est hors du texte


def test_text_target_applies_nested_group_transforms(page):
    el = page.find_text('Runway bearings', 'NESTED')
    assert (el.x, el.y) == pytest.approx((10 + 2 * 5, 20 + 2 * 5))


def test_text_target_occurrence_follows_document_order(page):
    a = page.find_text('Frame', 'TWICE', occurrence=1)
    b = page.find_text('Frame', 'TWICE', occurrence=2)
    assert (a.x, a.y) == (50.0, 60.0) and (b.x, b.y) == (52.0, 80.0)


def test_text_target_ambiguous_without_occurrence_stops(page):
    with pytest.raises(guide.GuideError, match='ambigue.*TWICE'):
        page.find_text('Frame', 'TWICE')
    with pytest.raises(guide.GuideError, match='ambigue.*197.4'):
        page.find_text('Runway bearings', '197.4°')


def test_text_target_missing_stops_and_names_page_and_layer(page):
    with pytest.raises(guide.GuideError, match=r'introuvable.*Header.*ABSENT.*synth'):
        page.find_text('Header', 'ABSENT')
    with pytest.raises(guide.GuideError, match='introuvable'):
        page.find_text('Frame', 'LTAN')                       # le texte existe, mais dans une autre couche
    with pytest.raises(guide.GuideError, match='occurrence 3'):
        page.find_text('Frame', 'TWICE', occurrence=3)


def test_parse_transform_order():
    m = guide.parse_transform('translate(10 20) rotate(90)')
    assert guide.apply(m, 1, 0) == pytest.approx((10, 21))


# --------------------------------------------------------------------------------------------------- cibles : un point
def test_point_target_with_ink_is_accepted_and_without_ink_stops(page):
    assert page.has_ink('Navaids', 61.5, 44.2)                # le centre du cercle (remplissage blanc opaque)
    assert page.has_ink('Navaids', 61.5 + 1.5, 44.2)          # le trait, a 0,15 mm du point
    assert not page.has_ink('Navaids', 70.0, 44.2)
    assert not page.has_ink('Navaids', 61.5, 44.2 + 2.5)      # a 1,15 mm du trait : plus d'encre a moins de 0,5 mm
    assert page.has_ink('Frame', 105.0, 105.0)
    assert not page.has_ink('Frame', 98.0, 105.0)


def test_point_target_ink_is_that_of_the_layer_alone(page):
    # la couche Frame est rendue seule : le carre noir de Frame n'est pas de l'encre de Navaids
    assert not page.has_ink('Navaids', 105.0, 105.0)


def test_resolve_pins_stops_on_point_without_ink(tmp_path):
    src = guide.Sources.__new__(guide.Sources)
    src.defs = {'p': {'zip': 'a', 'file': 'x'}}
    src._svg = {'p': guide.SvgPage('p', page_with_everything())}
    spec = {'id': 'chX', 'kind': 'anatomy', 'source': 'p', 'pins': [
        {'n': 1, 'target': {'kind': 'point', 'layer': 'Navaids', 'at': [90.0, 90.0]}, 'badge': [1, 1], 'title': 't'}]}
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*Navaids'):
        guide.resolve_pins(src, spec)
    spec['pins'][0]['target']['at'] = [61.5, 44.2]
    pins = guide.resolve_pins(src, spec)
    assert pins[0].check == 'encre' and pins[0].aim == (61.5, 44.2)


def src_of(svg, key='p'):
    src = guide.Sources.__new__(guide.Sources)
    src.defs = {key: {'zip': 'a', 'file': 'x'}}
    src._svg = {key: guide.SvgPage(key, svg)}
    return src


def tpin(n, text, badge, at='start', on='page', layer='Header', occ=None):
    tg = {'kind': 'text', 'layer': layer, 'text': text, 'at': at}
    if occ:
        tg['occurrence'] = occ
    d = {'n': n, 'target': tg, 'badge': list(badge), 'title': 't', 'text': 'x'}
    if on != 'page':
        d['on'] = on
    return d


def pspec(*pins, **kw):
    return {'id': 'chX', 'kind': 'anatomy', 'source': 'p', 'pins': list(pins), **kw}


def test_text_pin_dot_is_outside_the_box_on_the_pin_side():
    src = src_of(page_with_everything())
    w = guide.fonts.text_width('LTAN', 'regular', 2.0)
    d = (guide.DOT_R + guide.DOT_GAP_AIM) / guide.S                    # centre du point a 1,15 mm A4 du bord de la boite
    mid_y = 11.4 + (guide.LINE_DESCENT - guide.LINE_ASCENT) * 2.0 / 2   # ligne mediane de la boite
    (a,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6), 'start')))
    (b,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (3, -6), 'end')))
    (c,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (0, -6), 'middle')))
    assert a.aim == pytest.approx((6.0 - d, mid_y)) and b.aim == pytest.approx((6.0 + w + d, mid_y))
    top = 11.4 - guide.LINE_ASCENT * 2.0
    assert c.aim == pytest.approx((6.0 + w / 2, top - d))               # middle : au bord le plus proche de la pastille
    (c2,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (0, 6), 'middle')))
    assert c2.aim == pytest.approx((6.0 + w / 2, 11.4 + guide.LINE_DESCENT * 2.0 + d))
    for p in (a, b, c, c2):
        assert p.check == 'texte' and guide.box_distance(*p.aim, list(p.box)) > 0.0
        gap = guide.box_distance(*p.aim, list(p.box)) * p.scale - guide.DOT_R
        assert gap == pytest.approx(0.6) and 0.2 <= gap <= 1.0


def test_text_pin_with_nudge_or_unknown_at_stops():
    src = src_of(page_with_everything())
    bad = tpin(1, 'LTAN', (-4, -8))
    bad['target']['nudge'] = [-1, -1.4]
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*nudge'):
        guide.resolve_pins(src, pspec(bad))
    with pytest.raises(guide.GuideError, match=r'pastille 1.*« at » inconnu'):
        guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-4, -8), 'left')))


# --------------------------------------------------------------------------- A1 : une pastille ne couvre aucun texte
def test_pin_disc_over_its_own_target_text_stops():
    src = src_of(page_with_everything())
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*disque.*recouvre le texte « LTAN »'):
        guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (1, 0))))
    assert guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6))))            # decalee : acceptee


def test_pin_disc_over_another_text_of_the_page_stops():
    src = src_of(page_with_everything())
    # de la 1re occurrence de TWICE (50, 60) vers la 2e (52, 80) : le disque tombe sur l'autre texte
    with pytest.raises(guide.GuideError, match=r'pastille 1.*recouvre le texte « TWICE »'):
        guide.resolve_pins(src, pspec(tpin(1, 'TWICE', (2.4, 16.0), 'start', layer='Frame', occ=1)))


def test_pin_disc_is_checked_against_the_rotated_box_not_the_bounding_rectangle():
    svg = svg_page(layer('L', '<g transform="translate(50 50) rotate(45)">' + txt(0, 0, 'HELLO WORLD', size=4) + '</g>'))
    src = src_of(svg)
    el = guide.SvgPage('r', svg).find_text('L', 'HELLO WORLD')
    x0, y0, x1, y1 = el.box()
    # une pastille dont le centre est au coin du rectangle englobant (hors du texte tourne) est acceptee
    aim = el.point_at('middle')
    badge = ((x1 - 0.2 - aim[0]) * 0.8, (y0 + 0.2 - aim[1]) * 0.8)
    assert guide.resolve_pins(src, pspec({'n': 1, 'target': {'kind': 'text', 'layer': 'L', 'text': 'HELLO WORLD', 'at': 'middle'},
                                          'badge': list(badge), 'title': 't'}))


def test_pin_that_leaves_the_page_stops():
    src = src_of(page_with_everything())
    with pytest.raises(guide.GuideError, match=r'pastille 1.*sort de la page'):
        guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-30, 0))))


# ------------------------------------------------------------------------------ A2 : le point dessine est controle
def test_drawn_point_inside_the_box_stops():
    src = src_of(page_with_everything())
    (p,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6), 'start')))
    guide.check_drawn(p, *p.aim)                                          # le point choisi par l'outil passe
    cx = sum(c[0] for c in p.box) / 4
    cy = sum(c[1] for c in p.box) / 4
    with pytest.raises(guide.GuideError, match=r'pastille 1.*dans la boite du texte « LTAN »'):
        guide.check_drawn(p, cx, cy)


def test_drawn_point_too_far_or_too_close_stops():
    src = src_of(page_with_everything())
    (p,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6), 'start')))
    x0 = min(c[0] for c in p.box)
    y = p.aim[1]
    for gap, ok in ((0.2, True), (1.0, True), (0.1, False), (1.2, False), (3.0, False)):
        x = x0 - (guide.DOT_R + gap) / guide.S
        if ok:
            guide.check_drawn(p, x, y)
        else:
            with pytest.raises(guide.GuideError, match=r'pastille 1.*bord du point dessine est a .* mm de la boite.*0\.2 a 1\.0'):
                guide.check_drawn(p, x, y)


def test_drawn_point_on_the_wrong_side_of_its_text_stops():
    import dataclasses
    src = src_of(page_with_everything())
    (p,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6), 'start')))      # point a gauche, pastille a gauche
    bad = dataclasses.replace(p, badge=(3, -6))                                     # pastille de l'autre cote du texte
    with pytest.raises(guide.GuideError, match=r'pastille 1.*du mauvais cote du texte « LTAN »'):
        guide.check_drawn(bad, *p.aim)
    with pytest.raises(guide.GuideError, match=r'pastille 1.*du mauvais cote'):
        guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (3, -6), 'start')))


def test_leader_line_through_its_own_target_box_stops(monkeypatch):
    box = [(10.0, 10.0), (20.0, 10.0), (20.0, 12.0), (10.0, 12.0)]
    assert guide.segment_crosses_box((5.0, 11.0), (25.0, 11.0), box)             # de part en part
    assert guide.segment_crosses_box((5.0, 11.0), (15.0, 11.0), box)             # finit dedans
    assert not guide.segment_crosses_box((5.0, 9.0), (25.0, 9.0), box)           # passe au-dessus
    assert not guide.segment_crosses_box((5.0, 11.0), (9.0, 5.0), box)
    # le controle du trait est branche dans check_drawn : si le trait traverse la boite, arret nomme
    src = src_of(page_with_everything())
    (p,) = guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-3, -6), 'start')))
    monkeypatch.setattr(guide, 'segment_crosses_box', lambda a, b, c: True)
    with pytest.raises(guide.GuideError, match=r'pastille 1.*trait de rappel traverse la boite de sa cible « LTAN »'):
        guide.check_drawn(p, *p.aim)


def test_dot_over_another_text_stops():
    svg = svg_page(layer('Header', txt(6, 11.4, 'LTAN') + txt(6, 17.0, 'ELEV')))
    src = src_of(svg)
    # le point (au-dessus de LTAN) tomberait sur ELEV : texte voisin trop proche
    with pytest.raises(guide.GuideError, match=r'pastille 1.*(point dessine|disque).*recouvre le texte'):
        guide.resolve_pins(src, pspec(tpin(1, 'ELEV', (0, -3), 'middle')))


def test_drawn_point_of_an_ink_target_is_the_one_checked():
    src = src_of(page_with_everything())
    (p,) = guide.resolve_pins(src, pspec({'n': 1, 'target': {'kind': 'point', 'layer': 'Navaids', 'at': [61.5, 44.2]},
                                          'badge': [-4, 0], 'title': 't'}))
    guide.check_drawn(p, 61.5, 44.2)
    with pytest.raises(guide.GuideError, match=r'point dessine.*couche « Navaids »'):
        guide.check_drawn(p, 63.0, 44.2)


def test_pins_report_gives_the_checked_box_and_the_drawn_point(tmp_path):
    g = guide.build(mini_zips(tmp_path), 'AC-V9.9.9', mini_data())
    rows = guide.pins_report(g).splitlines()
    assert rows[0].split('\t')[4:] == ['boite_controlee_mm', 'point_dessine_mm', 'ecart_bord_point_boite_mm']
    f = rows[1].split('\t')
    assert 'LTAN' in f[3] and f[4].startswith('(') and f[5].startswith('(') and f[6] == '0.60'
    assert 'nudge' not in ''.join(rows)


# --------------------------------------------------------------------------------------------- A3 : les zooms
def zoom_spec(*pins, same=True):
    z = {'region': [0.0, 0.0, 50.0, 40.0], 'scale': 3.0, 'caption': 'c', 'frame_label': 'f'}
    if not same:
        z['page'] = 'next'
    return pspec(*pins, zoom=z)


def test_zoom_pin_whose_point_leaves_the_zoomed_region_stops():
    src = src_of(page_with_everything())
    inside = tpin(1, 'LTAN', (-4, -8), on='zoom')                     # (6, 10,7) est dans [0, 0, 50, 40]
    assert guide.resolve_pins(src, zoom_spec(inside))
    outside = tpin(1, '10-9', (-4, -8), 'end', on='zoom')              # (142, ...) est hors de la zone agrandie
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*sort de la zone agrandie'):
        guide.resolve_pins(src, zoom_spec(outside))


def test_zoom_pin_on_a_page_without_zoom_stops():
    src = src_of(page_with_everything())
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*n\'a pas de zoom'):
        guide.resolve_pins(src, pspec(tpin(1, 'LTAN', (-4, -8), on='zoom')))


def test_unknown_on_stops():
    src = src_of(page_with_everything())
    with pytest.raises(guide.GuideError, match=r'pastille 1.*« on » inconnu : « zoom2 »'):
        guide.resolve_pins(src, zoom_spec(tpin(1, 'LTAN', (-4, -8), on='zoom2')))


def test_page_pin_on_a_zoom_only_page_stops():
    src = src_of(page_with_everything())
    spec = {'id': 'chZ', 'kind': 'zoom_only', 'source': 'p', 'region': [0.0, 0.0, 50.0, 40.0], 'scale': 3.0,
            'pins': [tpin(1, 'LTAN', (-4, -8))]}
    with pytest.raises(guide.GuideError, match=r'chZ.*pastille 1.*ne montre que le zoom'):
        guide.resolve_pins(src, spec)
    spec['pins'] = [tpin(1, 'LTAN', (-4, -8), on='zoom')]
    assert guide.resolve_pins(src, spec)


def test_pin_of_the_directory_page_outside_the_page_stops(tmp_path):
    d = mini_data()
    d['pages'][4]['pins'][0]['target']['at'] = [160, 10]
    with pytest.raises(guide.GuideError, match=r'ch3.*pastille 1.*sort de la page'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', d)


# --------------------------------------------------------------------------------------------------- chiffres cites
def test_cited_strings_are_checked_element_by_element_ignoring_case_and_spacing():
    svg = svg_page(layer('Header', txt(1, 1, 'KNY  390') + txt(2, 2, 'Karatay City Park 11 KM') + txt(3, 3, 'NDB')
                         + txt(4, 4, '390') + txt(5, 5, 'hidden', ' class="halo"')))
    src = src_of(svg)
    ok = [{'block': 'b', 'page': 'p', 'strings': ['kny 390', 'KARATAY CITY PARK 11 km']}]
    assert guide.check_cited(src, ok) == 2
    with pytest.raises(guide.GuideError, match=r'« 4700 FT ».*page p'):
        guide.check_cited(src, [{'block': 'ch8, pin 3', 'page': 'p', 'strings': ['4700 FT']}])
    with pytest.raises(guide.GuideError, match='hidden'):      # un halo n'est pas le texte de la page
        guide.check_cited(src, [{'block': 'b', 'page': 'p', 'strings': ['hidden']}])
    with pytest.raises(guide.GuideError, match='NDB 390'):      # a cheval sur deux elements : plus accepte
        guide.check_cited(src, [{'block': 'b', 'page': 'p', 'strings': ['NDB 390']}])


def test_cited_strings_need_word_boundaries():
    def cite(svg, s):
        return guide.check_cited(src_of(svg), [{'block': 'b', 'page': 'p', 'strings': [s]}])
    only_13_40 = svg_page(layer('Header', txt(1, 1, 'ETA 13:40')))
    with pytest.raises(guide.GuideError, match='3:40'):            # « 3:40 » n'est pas dans « 13:40 »
        cite(only_13_40, '3:40')
    assert cite(only_13_40, '13:40') == 1
    for text, short in (('v10', 'v1'), ('v1.2', 'v1'), ('X180 KT', '180 KT'), ('180 KTS', '180 KT'), ('A_v1', 'v1')):
        with pytest.raises(guide.GuideError):
            cite(svg_page(layer('Header', txt(1, 1, text))), short)
    ok = svg_page(layer('Header', txt(1, 1, '(3:40)') + txt(2, 2, 'AT 80 KT · 150 KM/H') + txt(3, 3, 'v1')))
    assert cite(ok, '3:40') == 1 and cite(ok, '80 KT') == 1 and cite(ok, 'v1') == 1 and cite(ok, 'V1') == 1


def test_cited_entry_without_its_keys_stops_and_names_it():
    with pytest.raises(guide.GuideError, match='cle'):
        guide.check_cited(src_of(svg_page()), [{'block': 'b', 'strings': ['x']}])


# --------------------------------------------------------------------------------------------------------------- texte
def test_wrap_breaks_at_the_measured_width_not_an_average():
    width = 40.0
    for text in ('iiii ' * 40, 'WWWW ' * 40, 'Taxiway names come from OpenStreetMap, or from the signs read in DCS. ' * 3):
        lines = guide.wrap_runs([(text, 'regular')], width, 9.5, 't')
        for ln in lines:
            end = ln[-1][2] + guide.tw(ln[-1][1], ln[-1][0], 9.5)
            assert end <= width + 1e-6
        # chaque ligne est pleine : le mot suivant n'aurait pas tenu
        words = text.split()
        used = sum(len(' '.join(w for _, w, _ in ln).split()) for ln in lines)
        assert used == len(words)
        for a, b in zip(lines, lines[1:]):
            nxt = b[0][1].split()[0]
            last = a[-1]
            assert last[2] + guide.tw(last[1] + ' ' + nxt, last[0], 9.5) > width
    narrow = guide.wrap_runs([(('i' * 6 + ' ') * 30, 'regular')], 40, 9.5, 't')
    wide = guide.wrap_runs([(('W' * 6 + ' ') * 30, 'regular')], 40, 9.5, 't')
    assert len(wide) > len(narrow)                          # des largeurs de glyphes differentes, des coupures differentes


def test_wrap_mixed_weights_and_glued_punctuation():
    runs = guide.parse_rich('**Can receive**: NDB (ARK-15M). `a/b` stays plain **bold** end')
    assert runs[0] == ('Can receive', 'bold') and '`' not in ''.join(t for t, _ in runs)
    (line,) = guide.wrap_runs(runs, 200, 9.5, 't')
    texts = [(w, t) for w, t, _ in line]
    assert texts[0] == ('bold', 'Can receive')
    assert texts[1][1].startswith(': NDB')                   # les deux-points restent colles au gras
    assert line[1][2] == pytest.approx(guide.tw('Can receive', 'bold', 9.5))
    assert [w for w, t, _ in line][-1] == 'regular'


def test_text_that_overflows_its_zone_stops_and_names_page_and_block():
    pg = guide.Page('ch99', 'PART', 'T')
    runs = guide.parse_rich('word ' * 400)
    with pytest.raises(guide.GuideError, match=r'page ch99.*bloc « paragraphe 1 ».*deborde'):
        guide.block_text(pg, 'paragraphe 1', runs, 12, 40, 186, 80, 9.5, 4.1)
    assert guide.block_text(pg, 'ok', guide.parse_rich('a few words'), 12, 40, 186, 80, 9.5, 4.1) > 40


def test_word_wider_than_the_zone_stops():
    with pytest.raises(guide.GuideError, match='plus large que la zone'):
        guide.wrap_runs([('A' * 80, 'regular')], 20, 9.5, 'page x')


def test_legend_column_too_long_stops():
    pg = guide.Page('ch98', 'P', 'T')
    pins = [guide.Pin(n, 'page', 'text', 'L', (0, 0), (), (0, 0), 'TITLE', 'word ' * 60, 'texte', 'p', 'ch98')
            for n in range(1, 6)]
    with pytest.raises(guide.GuideError, match='legendes ne tiennent pas'):
        guide.draw_legend(pg, pins, 137.4, 32.5, 55.0, 80.0)


def test_title_too_long_for_the_header_stops(monkeypatch):
    monkeypatch.setattr(guide, '_LABELS', mini_labels())
    pg = guide.Page('ch97', 'P', 'A title that is far too long for a page header at twenty points ' * 2)
    with pytest.raises(guide.GuideError, match=r'ch97.*titre'):
        guide.page_svg(pg, 1, 'AC-V1.9.0')


# ----------------------------------------------------------------------------------------------------- un mini guide
def mini_zips(tmp_path, svg=None, directory_size=(1748, 2480), directory_bytes=None):
    from PIL import Image
    svg = svg or page_with_everything()
    a, h = tmp_path / 'a.zip', tmp_path / 'h.zip'
    with zipfile.ZipFile(a, 'w') as z:
        z.writestr('root_airfields/svg/A_10-9.svg', svg)
    with zipfile.ZipFile(h, 'w') as z:
        z.writestr('root_helicopters/svg/B_10-9.svg', svg)
        buf = io.BytesIO()
        Image.new('RGB', directory_size, (230, 220, 200)).save(buf, 'JPEG')
        z.writestr('root_helicopters/heliports/dir_p11.jpg', directory_bytes if directory_bytes is not None else buf.getvalue())
    return {'airfields': str(a), 'helicopters': str(h)}


def mini_labels():
    return {'parts': {'1': 'PART 1 · MINI', '2': 'PART 2 · MINI', 'annex': 'APPENDICES', 'contents': 'MINI · READING GUIDE'},
            'footer_left': 'MINI GUIDE · example {tag}', 'footer_right': 'NOT FOR REAL-WORLD NAVIGATION',
            'how_to_use': 'HOW TO USE THIS', 'pdf_title': 'Mini guide', 'pdf_author': 'Mini author'}


def mini_data():
    pin = lambda n, tg, title, text='': {'n': n, 'target': tg, 'badge': [0, -5], 'title': title, 'text': text}
    return {
        'labels': mini_labels(),
        'sources': {'a': {'zip': 'airfields', 'file': 'svg/A_10-9.svg'},
                    'b': {'zip': 'helicopters', 'file': 'svg/B_10-9.svg'},
                    'd': {'zip': 'helicopters', 'file': 'heliports/dir_p11.jpg', 'size': [1748, 2480]}},
        'pages': [
            {'id': 'cover', 'kind': 'cover', 'title': 'Guide title', 'subtitle': 'Sub', 'line': 'Release <TAG> here.',
             'background': 'a', 'background_opacity': 0.12},
            {'id': 'contents', 'kind': 'contents', 'title': 'Contents', 'groups': [
                {'heading': 'Part 1', 'entries': [{'num': '1', 'name': 'Text page', 'ref': 'ch1'},
                                                  {'num': '2', 'name': 'Anatomy', 'ref': 'ch2'},
                                                  {'num': '3', 'name': 'Directory', 'ref': 'ch3'}]}]},
            {'id': 'ch1', 'kind': 'text', 'part': 1, 'num': '1', 'title': 'Text page',
             'paragraphs': ['**Lead.** Some body text with `code`.']},
            {'id': 'ch2', 'kind': 'anatomy', 'part': 1, 'num': '2', 'title': 'Anatomy', 'source': 'a',
             'pins': [pin(1, {'kind': 'text', 'layer': 'Header', 'text': 'LTAN'}, 'Airfield', 'The code.'),
                      pin(2, {'kind': 'point', 'layer': 'Navaids', 'at': [61.5, 44.2]}, 'Beacon', 'A **beacon**.')],
             'use': ['First.', 'Second.'], 'cited': [{'block': 'ch2', 'page': 'a', 'strings': ['LTAN', '10-9']}]},
            {'id': 'ch3', 'kind': 'anatomy', 'part': 1, 'num': '3', 'title': 'Directory', 'source': 'd',
             'pins': [pin(1, {'kind': 'image', 'at': [10, 10]}, 'Box', 'Text.')], 'use': ['One.']},
            {'id': 'annex', 'kind': 'annex', 'part': 'annex', 'num': '', 'title': 'Appendices', 'refs': ['annexA'],
             'sections': [{'heading': 'Appendix A', 'paragraphs': [[['ADF', 'direction finder'], ['ARP', 'reference']]]}]},
        ]}


def test_mini_guide_builds_and_is_deterministic(tmp_path):
    z = mini_zips(tmp_path)
    g1 = guide.build(z, 'AC-V9.9.9', mini_data())
    g2 = guide.build(z, 'AC-V9.9.9', mini_data())
    assert g1.pdf == g2.pdf and len(g1.pdf) > 1000            # mêmes entrees, mêmes octets
    assert g1.n_pages == 6
    assert g1.toc['ch1'] == 3 and g1.toc['ch2'] == 4 and g1.toc['ch3'] == 5 and g1.toc['annexA'] == 6
    assert [p.check for p in g1.pins] == ['texte', 'encre', 'aucun']
    assert g1.cited == 2
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(g1.pdf))
    assert len(r.pages) == 6
    assert r.metadata.title == 'Mini guide' and r.metadata.author == 'Mini author'
    assert '/CreationDate' not in r.trailer['/Info'] and '/ModDate' not in r.trailer['/Info']
    assert abs(float(r.pages[0].mediabox.width) - 595.3) < 1 and abs(float(r.pages[0].mediabox.height) - 841.9) < 1
    flat = lambda i: ''.join(r.pages[i].extract_text().split())
    assert 'ReleaseAC-V9.9.9here.' in flat(0)                 # <TAG> = l'argument --edition
    assert 'Textpage' in flat(1) and 'Anatomy' in flat(1)    # le sommaire
    assert 'Lead.Somebodytextwithcode.' in flat(2)
    assert 'MINIGUIDE' in flat(3) and 'AC-V9.9.9' in flat(3) and 'HOWTOUSETHIS' in flat(3) and 'PART1·MINI' in flat(3)


def test_summary_and_pins_report(tmp_path):
    g = guide.build(mini_zips(tmp_path), 'AC-V9.9.9', mini_data())
    s = guide.summary(g)
    assert '6 pages' in s and 'texte : 1' in s and 'encre : 1' in s and 'annuaire : 1' in s
    rows = guide.pins_report(g).splitlines()
    assert rows[0].startswith('page\t') and len(rows) == 4
    assert rows[1].split('\t')[:3] == ['ch2', 'a', '1'] and 'Header' in rows[1]
    assert rows[3].split('\t')[3] == 'page-image'


def test_missing_page_in_the_zips_stops_and_names_it(tmp_path):
    data = mini_data()
    data['sources']['gone'] = {'zip': 'airfields', 'file': 'svg/NOT_THERE_11-1.svg'}
    with pytest.raises(guide.GuideError, match=r'page gone.*NOT_THERE_11-1.svg'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', data)


def test_directory_image_must_keep_its_dimensions(tmp_path):
    with pytest.raises(guide.GuideError, match='1748 × 2480'):
        guide.build(mini_zips(tmp_path, directory_size=(1000, 1400)), 'AC-V9.9.9', mini_data())


def test_lost_target_cited_figure_and_overflow_stop_the_build(tmp_path):
    z = mini_zips(tmp_path)
    d = mini_data()
    d['pages'][3]['pins'][0]['target']['text'] = 'GONE'
    with pytest.raises(guide.GuideError, match=r'pastille 1.*GONE'):
        guide.build(z, 'AC-V9.9.9', d)
    d = mini_data()
    d['pages'][3]['cited'][0]['strings'].append('4700 FT')
    with pytest.raises(guide.GuideError, match='4700 FT'):
        guide.build(z, 'AC-V9.9.9', d)
    d = mini_data()
    d['pages'][2]['paragraphs'] = ['word ' * 3000]
    with pytest.raises(guide.GuideError, match=r'ch1.*deborde'):
        guide.build(z, 'AC-V9.9.9', d)


def test_invalid_edition_stops(tmp_path):
    with pytest.raises(guide.GuideError, match='edition'):
        guide.build(mini_zips(tmp_path), 'AC V1', mini_data())


# --------------------------------------------------------------------------------- A3 : tout texte du guide se mesure
def build_with(tmp_path, spec=None, edition='AC-V9.9.9', edit=None):
    d = mini_data()
    if spec is not None:
        d['pages'].insert(-1, spec)
    if edit:
        edit(d)
    return guide.build(mini_zips(tmp_path), edition, d)


STRIP = {'id': 'chS', 'kind': 'strip', 'part': 1, 'num': '2', 'title': 'Strip', 'text': 'Some text.',
         'thumbs': [{'source': 'a', 'code': '10-9', 'name': 'Airfield', 'line': 'A short line.'}] * 4}
OVERVIEW = {'id': 'chO', 'kind': 'overview', 'part': 2, 'num': '', 'title': '11.0 Overview', 'caption': 'cap',
            'col_x': [12, 60], 'header': ['aircraft', 'NDB'], 'rows': [['Mi-24P', 'yes']], 'note': 'A note.'}
FLIGHT = {'id': 'chF', 'kind': 'flight', 'part': 2, 'num': '10', 'title': 'Flight',
          'steps': [{'thumbs': ['a'], 'text': f'**Step {i}.** Some words.'} for i in range(1, 9)]}


def test_good_strip_overview_and_flight_pages_build(tmp_path):
    for spec in (STRIP, OVERVIEW, FLIGHT):
        assert build_with(tmp_path, spec).n_pages == 7


def test_strip_thumbnail_code_and_name_are_measured(tmp_path):
    for key, val, where in (('code', 'X' * 60, 'code'), ('name', 'N' * 60, 'nom')):
        spec = copy.deepcopy(STRIP)
        spec['thumbs'][0] = {**spec['thumbs'][0], key: val}
        with pytest.raises(guide.GuideError, match=rf'page chS, vignette.*{where}.*deborde'):
            build_with(tmp_path, spec)


def test_overview_header_and_title_are_measured(tmp_path):
    spec = copy.deepcopy(OVERVIEW)
    spec['header'][0] = ' '.join(['alpha bravo charlie delta'] * 12)
    with pytest.raises(guide.GuideError, match=r'page chO, en-tete.*ne tiennent pas dans la bande'):
        build_with(tmp_path, spec)
    spec = copy.deepcopy(OVERVIEW)
    spec['caption'] = 'a caption far too long for its line ' * 8
    with pytest.raises(guide.GuideError, match=r'page chO, titre du tableau.*deborde'):
        build_with(tmp_path, spec)
    spec = copy.deepcopy(OVERVIEW)
    spec['rows'] = [['Mi-24P', 'yes']] * 17                  # 42 + 12 + 17 x 17 mm : sous le pied de page
    with pytest.raises(guide.GuideError, match=r'page chO, tableau.*pied de page'):
        build_with(tmp_path, spec)


def test_overview_cell_is_measured(tmp_path):
    spec = copy.deepcopy(OVERVIEW)
    spec['rows'] = [['Mi-24P', 'word ' * 60]]
    with pytest.raises(guide.GuideError, match=r'page chO, cellule'):
        build_with(tmp_path, spec)


def test_contents_that_does_not_fit_above_the_footer_stops(tmp_path):
    def edit(d):
        d['pages'][1]['groups'][0]['entries'] = [{'num': str(i), 'name': 'Text page', 'ref': 'ch1'} for i in range(40)]
    with pytest.raises(guide.GuideError, match=r'page contents.*sommaire passe sous le pied de page'):
        build_with(tmp_path, edit=edit)


def test_part_line_is_measured(tmp_path):
    def edit(d):
        d['labels']['parts']['1'] = 'PART 1 · ' + 'VERY LONG ' * 20
    with pytest.raises(guide.GuideError, match=r'page ch1, ligne de partie.*deborde'):
        build_with(tmp_path, edit=edit)


def test_footer_halves_never_overlap_whatever_the_edition(tmp_path):
    assert build_with(tmp_path, edition='AC-V1.9.0').n_pages == 6
    with pytest.raises(guide.GuideError, match=r'pied de page.*edition trop longue'):
        build_with(tmp_path, edition='AC-V' + '9' * 250)


def test_flight_step_that_does_not_fit_stops_instead_of_going_under_the_footer(tmp_path):
    spec = copy.deepcopy(FLIGHT)
    spec['steps'][7]['text'] = 'word ' * 200
    with pytest.raises(guide.GuideError, match=r'page chF, bloc « etape 8 ».*deborde'):
        build_with(tmp_path, spec)


def test_flight_thumbnails_below_the_footer_stop():
    pg = guide.Page('chF', 'P', 'T')
    emb = guide.Embedder(src_of(page_with_everything(), 'a'))
    spec = copy.deepcopy(FLIGHT)
    spec['steps'] = spec['steps'] * 2                         # 16 etapes : 8 rangees, sous le pied
    with pytest.raises(guide.GuideError, match=r'etape \d+ : les vignettes passent sous le pied'):
        guide.layout_flight(spec, emb, 'P', 'T')


def test_letter_spacing_counts_in_the_line_breaks():
    text = 'aaaa bbbb'
    width = guide.tw(text, 'regular', 9.5) + 0.5
    assert len(guide.wrap_runs([(text, 'regular')], width, 9.5, 't')) == 1
    assert len(guide.wrap_runs([(text, 'regular')], width, 9.5, 't', 0.3)) == 2          # 9 lettres x 0,3 mm de plus
    pg = guide.Page('chK', 'P', 'T')
    with pytest.raises(guide.GuideError, match='deborde'):
        guide.block_text(pg, 'b', [(text, 'regular')], 12, 40, width, 40, 9.5, 4.0, spacing=0.3)
    assert guide.block_text(pg, 'b', [(text, 'regular')], 12, 40, width, 40, 9.5, 4.0) > 40


# ------------------------------------------------------------------ A3 : les textes fixes sont au fichier de donnees
def test_fixed_texts_come_from_the_data_file_not_from_the_code(tmp_path):
    for name in ('PART_LABELS', 'FOOTER_LEFT', 'FOOTER_RIGHT', 'PDF_TITLE', 'PDF_AUTHOR'):
        assert not hasattr(guide, name), name
    d = guide.load_data()
    assert d['labels']['how_to_use'] == 'HOW TO USE IT'
    assert d['labels']['parts']['1'] == 'PART 1 · READING THE CHARTS'
    assert d['labels']['footer_right'] == 'NOT FOR REAL-WORLD NAVIGATION' and '{tag}' in d['labels']['footer_left']
    def edit(dd):
        dd['labels']['how_to_use'] = 'CHANGED HEADING'
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(build_with(tmp_path, edit=edit).pdf))
    assert 'CHANGEDHEADING' in ''.join(r.pages[3].extract_text().split())
    with pytest.raises(guide.GuideError, match=r'labels\.how_to_use'):
        build_with(tmp_path, edit=lambda dd: dd['labels'].pop('how_to_use'))
    with pytest.raises(guide.GuideError, match='labels'):
        build_with(tmp_path, edit=lambda dd: dd.pop('labels'))


# ------------------------------------------------------------- A3 : toute erreur de lecture est un arret nomme
def test_unreadable_attribute_in_a_page_stops_and_names_page_and_layer():
    svg = svg_page(layer('Header', txt('abc', 5, 'LTAN')))
    with pytest.raises(guide.GuideError, match=r'page p, couche « Header », texte « LTAN » : attribut x="abc" illisible'):
        guide.SvgPage('p', svg).texts()
    svg = svg_page(layer('Header', txt(1, 5, 'LTAN', ' letter-spacing="wide"')))
    with pytest.raises(guide.GuideError, match='letter-spacing'):
        guide.SvgPage('p', svg).texts()


def test_unreadable_transform_stops_and_names_the_page():
    svg = svg_page(layer('Header', '<g transform="rotate(abc)">' + txt(1, 5, 'LTAN') + '</g>'))
    with pytest.raises(guide.GuideError, match=r'page p.*transformation illisible'):
        guide.SvgPage('p', svg).texts()


def test_corrupt_jpeg_stops_and_names_the_page(tmp_path):
    with pytest.raises(guide.GuideError, match=r'page d.*dir_p11.jpg.*JPEG'):
        guide.build(mini_zips(tmp_path, directory_bytes=b'\xff\xd8\xff\xe0 not a jpeg at all'), 'AC-V9.9.9', mini_data())


def test_damaged_zip_member_stops_and_names_the_page(tmp_path):
    z = mini_zips(tmp_path)
    raw = bytearray((tmp_path / 'a.zip').read_bytes())
    i = raw.index(b'LTAN')
    raw[i:i + 4] = b'LTAX'                                   # le contenu change, le CRC du zip ne colle plus
    (tmp_path / 'a.zip').write_bytes(bytes(raw))
    with pytest.raises(guide.GuideError, match=r'page a.*illisible dans le zip airfields'):
        guide.build(z, 'AC-V9.9.9', mini_data())


@pytest.mark.parametrize('where,drop', [
    (('pages', 3), 'title'), (('pages', 3), 'source'), (('pages', 3), 'use'), (('pages', 2), 'paragraphs'),
    (('pages', 4), 'pins'), (('pages', 0), 'title'), (('pages', 5), 'sections'), (('pages', 3), 'kind')])
def test_missing_key_in_the_data_stops_and_names_the_page(tmp_path, where, drop):
    d = mini_data()
    pid = d[where[0]][where[1]]['id']
    del d[where[0]][where[1]][drop]
    with pytest.raises(guide.GuideError, match=rf'page {pid} : donnees illisibles \(cle manquante'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', d)


def test_missing_key_in_a_pin_or_a_source_stops_and_names_the_entry(tmp_path):
    d = mini_data()
    del d['pages'][3]['pins'][0]['badge']
    with pytest.raises(guide.GuideError, match=r'page ch2 : donnees illisibles \(cle manquante .badge'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', d)
    d = mini_data()
    del d['sources']['b']['file']
    with pytest.raises(guide.GuideError, match=r'source b : entree illisible'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', d)
    d = mini_data()
    del d['sources']
    with pytest.raises(guide.GuideError, match='donnees du guide illisibles'):
        guide.build(mini_zips(tmp_path), 'AC-V9.9.9', d)


def test_unreadable_data_file_stops(tmp_path):
    bad = tmp_path / 'guide.json'
    bad.write_text('{"pages": [', encoding='utf-8')
    with pytest.raises(guide.GuideError, match='fichier de donnees du guide illisible'):
        guide.load_data(bad)
    with pytest.raises(guide.GuideError, match='illisible'):
        guide.load_data(tmp_path / 'absent.json')


def test_unexpected_error_in_the_command_is_a_named_stop_not_a_traceback(tmp_path, monkeypatch, capsys):
    def boom(zips, edition, data=None):
        raise RuntimeError('something odd')
    monkeypatch.setattr(guide, 'build', boom)
    out = tmp_path / 'guide.pdf'
    assert run(tmp_path, out) == 1
    err = capsys.readouterr().err
    assert err.startswith('ARRET') and 'RuntimeError' in err and 'Traceback' not in err and not out.exists()


# ---------------------------------------------------------------- A3 : la liste « au moins » du cadrage est citee
REQUIRED_CITED = {                                    # cadrage 2.3, plus `80 KT` et `v1` (addendum 1)
    'konya_10-9': ['KNY 390', 'KNY 111.20', 'IKNY 108.90', 'KON 88X', 'VAR 3°E', '132.400', 'v1'],
    'konya_10-8': ['KARATAY CITY PARK 11 km'],
    'konya_10-9A': ['87–124', '1:9329'],
    'konya_11-1': ['R-210', 'MSA 25 NM KNY', 'ILS OR VOR/DME OR NDB RWY 01L', 'ILS, VOR/DME OR ADF REQUIRED',
                   'TACAN KON', 'VOR/DME KNY'],
    'konya_11-1H': ['COPTER NDB RWY 01L', '4700 FT', 'QFE 400 M', '203°', '3:40', '80 KT', '150 KM/H', '017°'],
}


def test_the_cited_figures_of_the_data_file_include_the_required_list_page_by_page():
    d = guide.load_data()
    by_page, by_chapter = {}, {}
    for p in d['pages']:
        for c in p.get('cited', []):
            by_page.setdefault(c['page'], set()).update(c['strings'])
            by_chapter.setdefault(p['id'], {}).setdefault(c['page'], set()).update(c['strings'])
    for page, strings in REQUIRED_CITED.items():
        assert set(strings) <= by_page.get(page, set()), (page, set(strings) - by_page.get(page, set()))
    # les blocs que le cadrage nomme
    assert set(REQUIRED_CITED['konya_10-9']) <= by_chapter['ch4']['konya_10-9']
    assert {'COPTER NDB RWY 01L', '4700 FT', 'QFE 400 M', '203°', '3:40', '80 KT', '150 KM/H'} <= by_chapter['ch8']['konya_11-1H']
    assert {'QFE 400 M', '203°', '3:40', '80 KT', '150 KM/H', '017°'} <= by_chapter['ch11-1']['konya_11-1H']   # fiche du Mi-24P
    assert {'TACAN KON', 'VOR/DME KNY'} <= by_chapter['ch11-6']['konya_11-1']                 # la fiche du F-5E
    assert sum(len(c['strings']) for p in d['pages'] for c in p.get('cited', [])) >= 40


# ------------------------------------------------------------------------------------------------------- la commande
@pytest.fixture
def cmd_data(monkeypatch):
    monkeypatch.setattr(guide, 'load_data', mini_data)
    monkeypatch.setattr(guide, 'DATA_FILE', None)
    real = guide.build
    monkeypatch.setattr(guide, 'build', lambda zips, edition, data=None: real(zips, edition, mini_data()))


def run(tmp_path, out, *extra):
    z = mini_zips(tmp_path) if not (tmp_path / 'a.zip').exists() else {'airfields': str(tmp_path / 'a.zip'),
                                                                      'helicopters': str(tmp_path / 'h.zip')}
    return build_guide.main(['--zips', z['airfields'], z['helicopters'], '--edition', 'AC-V9.9.9', '--out', str(out), *extra])


def test_command_writes_the_pdf_and_the_pins_list(tmp_path, cmd_data, capsys):
    out, pins = tmp_path / 'out' / 'guide.pdf', tmp_path / 'out' / 'pins.txt'
    assert run(tmp_path, out, '--pins', str(pins)) == 0
    assert out.read_bytes()[:5] == b'%PDF-' and pins.read_text(encoding='utf-8').startswith('page\t')
    assert 'Reading guide AC-V9.9.9 : 6 pages' in capsys.readouterr().out


def test_command_refuses_to_overwrite(tmp_path, cmd_data, capsys):
    out = tmp_path / 'guide.pdf'
    out.write_bytes(b'precious')
    assert run(tmp_path, out) == 1
    assert out.read_bytes() == b'precious'
    assert 'ARRET' in capsys.readouterr().err
    pins = tmp_path / 'pins.txt'
    pins.write_text('precious', encoding='utf-8')
    assert run(tmp_path, tmp_path / 'other.pdf', '--pins', str(pins)) == 1
    assert pins.read_text(encoding='utf-8') == 'precious' and not (tmp_path / 'other.pdf').exists()


def test_command_stop_writes_nothing(tmp_path, monkeypatch, capsys):
    bad = mini_data()
    bad['pages'][3]['pins'][0]['target']['text'] = 'GONE'
    real = guide.build
    monkeypatch.setattr(guide, 'build', lambda zips, edition, data=None: real(zips, edition, bad))
    out, pins = tmp_path / 'sub' / 'guide.pdf', tmp_path / 'sub' / 'pins.txt'
    assert run(tmp_path, out, '--pins', str(pins)) == 1
    assert not out.exists() and not pins.exists() and not (tmp_path / 'sub').exists()
    err = capsys.readouterr().err
    assert 'ARRET' in err and 'GONE' in err


# --------------------------------------------------------------------------- A3 : les destinations de la commande
def test_command_refuses_the_same_file_for_out_and_pins(tmp_path, cmd_data, capsys):
    out = tmp_path / 'x.pdf'
    assert run(tmp_path, out, '--pins', str(tmp_path / 'sub' / '..' / 'x.pdf')) == 1
    err = capsys.readouterr().err
    assert err.startswith('ARRET') and 'meme fichier' in err
    assert not out.exists() and not (tmp_path / 'sub').exists()


def test_command_checks_both_parent_directories_before_writing(tmp_path, cmd_data, capsys):
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file, not a directory', encoding='utf-8')
    out = tmp_path / 'new' / 'guide.pdf'
    assert run(tmp_path, out, '--pins', str(blocker / 'pins.txt')) == 1
    assert 'pas un dossier' in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / 'new').exists()                  # rien d'ecrit, aucun dossier cree
    assert run(tmp_path, blocker / 'guide.pdf', '--pins', str(tmp_path / 'new' / 'pins.txt')) == 1
    assert not (tmp_path / 'new').exists()


def test_write_all_creates_exclusively_and_removes_what_it_made_on_failure(tmp_path):
    precious = tmp_path / 'precious.txt'
    precious.write_text('precious', encoding='utf-8')
    first = tmp_path / 'd1' / 'a.pdf'
    with pytest.raises(FileExistsError):                          # creation exclusive : jamais d'ecrasement
        build_guide.write_all([(first, b'x'), (precious, b'y')])
    assert precious.read_text(encoding='utf-8') == 'precious' and not first.exists() and not (tmp_path / 'd1').exists()
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    with pytest.raises(OSError):                                  # la seconde ecriture echoue : la premiere est retiree
        build_guide.write_all([(tmp_path / 'n' / 'a.pdf', b'x'), (blocker / 'sub' / 'p.txt', b'y')])
    assert not (tmp_path / 'n').exists()
    ok = tmp_path / 'ok' / 'a.pdf'
    build_guide.write_all([(ok, b'x')])
    assert ok.read_bytes() == b'x'


def test_command_second_write_failure_leaves_nothing(tmp_path, cmd_data, monkeypatch, capsys):
    real = open

    def fake(path, mode='r', *a, **k):
        if str(path).endswith('pins.txt'):
            raise PermissionError('disk full')
        return real(path, mode, *a, **k)
    monkeypatch.setattr(build_guide, 'open', fake, raising=False)
    out, pins = tmp_path / 'sub' / 'guide.pdf', tmp_path / 'sub' / 'pins.txt'
    assert run(tmp_path, out, '--pins', str(pins)) == 1
    assert 'ARRET' in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / 'sub').exists()


# ------------------------------------------------------------------------------------------------ le fichier de donnees
def test_data_file_is_well_formed():
    d = guide.load_data()
    ids = [p['id'] for p in d['pages']]
    assert len(ids) == len(set(ids)) and ids[0] == 'cover' and ids[1] == 'contents'
    refs = {r for p in d['pages'] for r in p.get('refs', [p['id']])}
    contents = next(p for p in d['pages'] if p['kind'] == 'contents')
    for grp in contents['groups']:
        for e in grp['entries']:
            assert e['ref'] in refs, e
    for p in d['pages']:
        if p['kind'] in ('anatomy', 'zoom_only'):
            assert p['source'] in d['sources']
            assert [x['n'] for x in p['pins']] == sorted(x['n'] for x in p['pins'])
            for x in p['pins']:
                assert x['target']['kind'] in ('text', 'point', 'image') and len(x['badge']) == 2
        for c in p.get('cited', []):
            assert c['page'] in d['sources'] and c['strings']
    assert {'parts', 'footer_left', 'footer_right', 'how_to_use', 'pdf_title', 'pdf_author'} <= set(d['labels'])
    assert 'nudge' not in json.dumps(d)
    for p in d['pages']:
        for x in p.get('pins', []):
            if x['target']['kind'] == 'text':
                assert x['target'].get('at', 'middle') in ('start', 'middle', 'end')
    # le theatre et les pages d'exemple sont au fichier de donnees
    assert d['theatre'] == 'Syria' and {'konya_10-9', 'konya_11-1', 'konya_11-1H', 'naqoura_10-9'} <= set(d['sources'])
    # les pastilles de l'annuaire, seules, n'ont pas de controle de cible
    kinds = {x['target']['kind'] for p in d['pages'] if p.get('source') == 'directory_p11' for x in p['pins']}
    assert kinds == {'image'}
    assert all(x['target']['kind'] != 'image' for p in d['pages'] if p.get('source') != 'directory_p11'
               for x in p.get('pins', []))
    json.dumps(d)


# ------------------------------------------------------------------------------------------------------------ la marque
MARQUE_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360" width="360" height="360">'


def test_marque_svg_replaces_the_expected_tag_once():
    out = guide.marque_svg(guide.INSIGNE_FILE, 20, 44, 36)
    assert out.startswith('<svg x="20" y="44" width="36" height="36" viewBox="0 0 360 360">')
    assert out.count('<svg') == 1 and MARQUE_OPEN not in out
    body = (guide.MARQUE_DIR / guide.INSIGNE_FILE).read_text(encoding='utf-8').strip()
    assert out.endswith(body[len(MARQUE_OPEN):])               # le reste est insere tel quel
    assert guide.marque_svg(guide.PICTO_FILE, 12, 287.1, 3).startswith('<svg x="12" y="287.1" width="3" height="3" viewBox')


@pytest.mark.parametrize('first', ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 360">',
                                   '<svg viewBox="0 0 360 360" width="360" height="360">',
                                   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'])
def test_marque_svg_with_another_tag_stops(tmp_path, first):
    (tmp_path / 'x.svg').write_text(f'\n {first}<circle r="1"/></svg>\n', encoding='utf-8')
    with pytest.raises(guide.GuideError, match='balise attendue'):
        guide.marque_svg('x.svg', 0, 0, 1, directory=tmp_path)
    with pytest.raises(guide.GuideError, match='illisible'):
        guide.marque_svg('absent.svg', 0, 0, 1, directory=tmp_path)


def test_marque_files_start_with_the_expected_tag():
    for n in (guide.INSIGNE_FILE, guide.PICTO_FILE):
        assert (guide.MARQUE_DIR / n).read_text(encoding='utf-8').strip().startswith(MARQUE_OPEN)


def _svgs(monkeypatch, tmp_path):
    monkeypatch.setattr(guide, 'render_pdf', lambda svgs: svgs)
    monkeypatch.setattr(guide, '_LABELS', mini_labels())
    g = guide.build(mini_zips(tmp_path), 'AC-V9.9.9', mini_data())
    return g.pdf


def test_insignia_on_the_cover_only_and_pictogram_on_every_other_page(monkeypatch, tmp_path):
    svgs = _svgs(monkeypatch, tmp_path)
    assert len(svgs) == 6
    insigne = guide.marque_svg(guide.INSIGNE_FILE, *guide.COVER_INSIGNE)
    picto = guide.marque_svg(guide.PICTO_FILE, *guide.FOOTER_PICTO)
    assert guide.COVER_INSIGNE == (20.0, 44.0, 36.0) and guide.FOOTER_PICTO == (12.0, 287.1, 3.0)
    cover = svgs[0]
    assert insigne in cover and picto not in cover
    assert cover.index('fill="#C8102E"') < cover.index(insigne)     # juste apres le rect rouge
    for s in svgs[1:]:
        assert insigne not in s and s.count(picto) == 1
        assert s.index(picto) < s.index('MINI GUIDE')                # avant le texte de gauche
        assert 'x="16.20"' in s[s.index(picto):]


def test_footer_left_text_starts_at_mx0_plus_4_2(monkeypatch, tmp_path):
    svgs = _svgs(monkeypatch, tmp_path)
    for s in svgs[1:]:
        line = [l for l in s.split('\n') if 'MINI GUIDE' in l][0]
        assert 'x="16.20"' in line and 'y="289.50"' in line
        right = [l for l in s.split('\n') if 'NOT FOR REAL-WORLD' in l][0]
        assert 'x="198.00"' in right


def test_footer_check_counts_the_4_2_mm_of_the_pictogram(monkeypatch):
    monkeypatch.setattr(guide, '_LABELS', mini_labels())
    room = guide.MX1 - guide.MX0 - guide.FOOTER_GAP_MM
    monkeypatch.setattr(guide, 'tw', lambda text, weight, size, spacing=0.0: (room - 4.2) / 2)
    assert guide.footer_texts('E')[0].endswith('E')                 # 4.2 + wl + wr + gap == largeur : passe
    monkeypatch.setattr(guide, 'tw', lambda text, weight, size, spacing=0.0: (room - 4.2) / 2 + 0.01)
    with pytest.raises(guide.GuideError, match='pied de page'):    # 0,02 mm de trop : arret
        guide.footer_texts('E')
    monkeypatch.setattr(guide, 'tw', lambda text, weight, size, spacing=0.0: (room - 4.0) / 2)
    with pytest.raises(guide.GuideError, match='pied de page'):    # tenait sans les 4,2 mm : arrete maintenant
        guide.footer_texts('E')


# ------------------------------------------------------------------------------- figures nommees et compose (site du guide)
def anat_page(data, pid='ch2'):
    return next(s for s in data['pages'] if s['id'] == pid)


def zoomed_data(page='same', region=(98.0, 0.0, 148.0, 40.0), frame=None, pos=None):
    """Le mini guide, sa page d'anatomie dotee d'un zoom (et d'une pastille posee sur le zoom)."""
    d = mini_data()
    z = {'region': list(region), 'scale': 1.2, 'caption': 'ZOOM CAPTION', 'frame_label': 'LABEL'}
    if page != 'same':
        z['page'] = page
    if frame:
        z['frame'] = list(frame)
    if pos:
        z['frame_label_pos'] = pos
    spec = anat_page(d)
    spec['zoom'] = z
    spec['pins'].append({'n': 3, 'on': 'zoom', 'target': {'kind': 'text', 'layer': 'Header', 'text': '10-9', 'at': 'start'},
                         'badge': [-6, 0], 'title': 'Zoomed', 'text': 'In the zoom.'})
    return d


def disc(p, ox, oy, rx, ry, scale):
    """Le disque (centre) d'une pastille posee a (ox, oy) pour le point (rx, ry) de la page source, a l'echelle `scale`."""
    return (ox + (p.aim[0] - rx) * scale + p.badge[0], oy + (p.aim[1] - ry) * scale + p.badge[1])


def test_figure_frame_is_the_smallest_box_of_its_rectangles_plus_0_6_mm():
    f = guide.Figure(rects=[(10.0, 20.0, 40.0, 50.0), (5.0, 30.0, 6.0, 31.0), (38.0, 52.0, 45.0, 53.0)])
    assert f.frame() == pytest.approx((4.4, 19.4, 41.2, 34.2))
    assert guide.Figure(rects=[(1.0, 2.0, 3.0, 4.0)]).frame() == pytest.approx((0.4, 1.4, 3.2, 3.2))
    with pytest.raises(guide.GuideError, match='figure'):
        guide.Figure().frame()


def test_a_page_only_has_figures_when_one_is_named():
    pg = guide.Page('x', 'P', 'T')
    pg.add('<a/>', '<b/>')
    assert pg.figs == {} and pg.els == ['<a/>', '<b/>']
    pg.add('<c/>', fig='page')
    pg.frame('page', 1, 2, 3, 4)
    assert pg.els == ['<a/>', '<b/>', '<c/>'] and pg.figs['page'].els == ['<c/>'] and pg.figs['page'].rects == [(1, 2, 3, 4)]


def test_anatomy_page_figure_is_its_source_page_with_the_pins_of_the_page(tmp_path):
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', mini_data())
    figs = dict(c.figures)
    assert [i for i, _ in c.figures] == ['ch1', 'ch2', 'ch3', 'annex']          # ni la couverture ni le sommaire
    assert list(figs['ch2']) == ['page']
    fig = figs['ch2']['page']
    x0, y0 = guide.CX, guide.CY
    w, h = guide.PAGE_W * guide.S, guide.PAGE_H * guide.S
    p1, p2 = c.pins[0], c.pins[1]
    discs = [disc(p, x0, y0, 0.0, 0.0, guide.S) for p in (p1, p2)]
    right = max([x0 + w] + [d[0] + guide.PIN_R for d in discs])
    left = min([x0] + [d[0] - guide.PIN_R for d in discs])
    top = min([y0] + [d[1] - guide.PIN_R for d in discs])
    bottom = max([y0 + h] + [d[1] + guide.PIN_R for d in discs])
    assert fig.frame() == pytest.approx((left - 0.6, top - 0.6, right - left + 1.2, bottom - top + 1.2), abs=1e-6)
    joined = ''.join(fig.els)
    assert joined.count('<circle') >= 2 and '<svg' in joined and 'HOW TO USE' not in joined
    assert 'Airfield' not in joined and 'The code.' not in joined            # ni titre ni texte de legende
    assert all(e in ''.join(c.svgs) for e in fig.els)                        # les memes chaines que la page A4


def test_a_pin_that_overflows_the_page_widens_the_frame(tmp_path):
    d = mini_data()
    spec = anat_page(d)
    spec['pins'][0] = {'n': 1, 'target': {'kind': 'text', 'layer': 'Header', 'text': 'LTAN', 'at': 'start'},
                       'badge': [-3.6, 0], 'title': 'Edge', 'text': ''}
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', d)
    fig = dict(c.figures)['ch2']['page']
    cx, _ = disc(c.pins[0], guide.CX, guide.CY, 0.0, 0.0, guide.S)
    assert cx - guide.PIN_R < guide.CX                                       # la pastille depasse le bord de la page
    x, y, w, hh = fig.frame()
    assert x == pytest.approx(cx - guide.PIN_R - 0.6) and x < guide.CX - 0.6


def test_zoom_label_background_is_in_the_page_figure_but_its_caption_is_not(tmp_path):
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', zoomed_data())
    figs = dict(c.figures)['ch2']
    assert list(figs) == ['page', 'zoom']
    page, zoom = figs['page'], figs['zoom']
    fx1 = 148.0
    lx, ly = guide.CX + fx1 * guide.S, guide.CY - 1.4                         # etiquette au-dessus du cadre, a droite
    lw = guide.fit('t', 'LABEL', 'bold', 6.2, 1e9, 0.2)
    bg = (lx - lw - 0.6, ly - 2.0, lx + 0.6, ly + 0.7)
    x, y, w, h = page.frame()
    assert y == pytest.approx(bg[1] - 0.6)                                   # le fond blanc de l'etiquette est dans le cadre...
    assert x + w == pytest.approx(bg[2] + 0.6)
    assert bg[1] < guide.CY and bg[2] > guide.CX + guide.PAGE_W * guide.S    # ... et il debordait de la page
    assert any('fill="#FFFFFF"' in e and 'fill-opacity="0.9"' in e for e in page.els)
    assert any('stroke-dasharray' in e for e in page.els)
    for f in (page, zoom):
        assert 'ZOOM CAPTION' not in ''.join(f.els)                          # la legende du zoom n'est dans aucune figure
    assert 'ZOOM CAPTION' in ''.join(c.svgs)
    zw, zh = 50.0 * 1.2, 40.0 * 1.2                                          # la region agrandie, a l'echelle 1,2
    zx, zy = (guide.W - zw) / 2, guide.CY + guide.PAGE_H * guide.S + 9
    zx0, zy0, fw, fh = zoom.frame()
    assert zx0 == pytest.approx(zx - 0.6) and zy0 == pytest.approx(zy - 0.6)
    assert fw >= zw + 1.2 - 1e-6 and fh >= zh + 1.2 - 1e-6                    # le rectangle du zoom entier, et ses pastilles


def test_zoom_pins_belong_to_the_zoom_figure_only(tmp_path):
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', zoomed_data())
    figs = dict(c.figures)['ch2']
    count = lambda f: ''.join(f.els).count('r="2.1"')
    assert count(figs['page']) == 2 and count(figs['zoom']) == 1
    assert '>3</text>' in ''.join(figs['zoom'].els) and '>3</text>' not in ''.join(figs['page'].els)


def test_inside_label_background_is_in_the_frame_too(tmp_path):
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', zoomed_data(pos='inside', frame=(98.0, 0.0, 148.0, 40.0)))
    page = dict(c.figures)['ch2']['page']
    lw = guide.fit('t', 'LABEL', 'bold', 6.2, 1e9, 0.2)
    lx = guide.CX + 148.0 * guide.S - 1.0                                     # etiquette a l'interieur du cadre : fond de 1,8 mm de plus
    x, y, w, h = page.frame()
    assert any('height="3.2" fill="#FFFFFF"' in e for e in page.els)
    assert x <= guide.CX - 0.6 + 1e-6 and x + w >= guide.CX + guide.PAGE_W * guide.S + 0.6 - 1e-6
    assert lx - lw - 0.8 > guide.CX                                           # ce fond est dans la page : le cadre ne grandit pas pour lui


def test_zoom_on_the_next_page_puts_both_a4_pages_figures_under_one_id(tmp_path):
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', zoomed_data(page='next'))
    assert c.n_pages == 7
    figs = dict(c.figures)['ch2']
    assert list(figs) == ['page', 'zoom']
    assert [i for i, _ in c.figures] == ['ch1', 'ch2', 'ch3', 'annex']       # une seule entree pour les deux pages A4
    assert 'ZOOM CAPTION' not in ''.join(figs['zoom'].els) and 'ZOOM CAPTION' in c.svgs[4]
    assert ''.join(figs['zoom'].els).count('r="2.1"') == 1 and ''.join(figs['page'].els).count('r="2.1"') == 2
    assert not any('stroke-dasharray' in e for e in figs['zoom'].els)


def test_zoom_only_page_has_a_zoom_figure_without_its_caption(tmp_path):
    d = mini_data()
    d['pages'].insert(-1, {'id': 'chZ', 'kind': 'zoom_only', 'part': 1, 'num': '9', 'title': 'Zoom', 'source': 'a',
                           'region': [0.0, 0.0, 50.0, 40.0], 'scale': 3.0, 'caption': 'ONLY CAPTION', 'text': 'Text below.',
                           'pins': [{'n': 1, 'on': 'zoom', 'target': {'kind': 'text', 'layer': 'Header', 'text': 'LTAN', 'at': 'end'},
                                     'badge': [6, 0], 'title': 'Code', 'text': 't'}]})
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', d)
    figs = dict(c.figures)['chZ']
    assert list(figs) == ['zoom']
    assert 'ONLY CAPTION' not in ''.join(figs['zoom'].els) and 'Text below.' not in ''.join(figs['zoom'].els)
    x, y, w, h = figs['zoom'].frame()
    assert (x, y) == pytest.approx((guide.MX0 - 0.6, guide.CY + 1.0 - 0.6)) and w >= 150.0 + 1.2 - 1e-6   # 50 mm x 3


def test_no_figure_outside_anatomy_and_zoom_pages(tmp_path):
    d = mini_data()
    for spec in (STRIP, OVERVIEW, FLIGHT):
        d['pages'].insert(-1, spec)
    c = guide.compose(mini_zips(tmp_path), 'AC-V9.9.9', d)
    figs = dict(c.figures)
    for pid in ('ch1', 'chS', 'chO', 'chF', 'annex'):
        assert figs[pid] == {}, pid
    assert sorted(k for k, v in figs.items() if v) == ['ch2', 'ch3']
    assert list(figs['ch3']) == ['page']                                     # la page-image de l'annuaire est une figure aussi


def test_compose_makes_the_same_pages_as_build_and_leaves_no_labels(monkeypatch, tmp_path):
    z = mini_zips(tmp_path)
    c = guide.compose(z, 'AC-V9.9.9', mini_data())
    assert guide._LABELS == {}
    seen = []
    monkeypatch.setattr(guide, 'render_pdf', lambda svgs: seen.append(svgs) or b'%PDF')
    g = guide.build(z, 'AC-V9.9.9', mini_data())
    assert seen == [c.svgs] and g.pdf == b'%PDF' and guide._LABELS == {}
    assert (g.n_pages, g.cited, g.toc, g.edition) == (c.n_pages, c.cited, c.toc, c.edition) and len(g.pins) == len(c.pins)
    assert guide.compose(z, 'AC-V9.9.9', mini_data()).svgs == c.svgs         # deterministe


def test_compose_stops_with_the_same_errors_as_build(tmp_path):
    z = mini_zips(tmp_path)
    d = mini_data()
    d['pages'][3]['pins'][0]['target']['text'] = 'GONE'
    with pytest.raises(guide.GuideError, match=r'pastille 1.*GONE'):
        guide.compose(z, 'AC-V9.9.9', d)
    d = mini_data()
    d['pages'][2]['paragraphs'] = ['word ' * 3000]
    with pytest.raises(guide.GuideError, match=r'ch1.*deborde'):
        guide.compose(z, 'AC-V9.9.9', d)
    with pytest.raises(guide.GuideError, match='edition'):
        guide.compose(z, 'AC V1', mini_data())
    with pytest.raises(guide.GuideError, match='1748 × 2480'):
        guide.compose(mini_zips(tmp_path, directory_size=(1000, 1400)), 'AC-V9.9.9', mini_data())
    assert guide._LABELS == {}
