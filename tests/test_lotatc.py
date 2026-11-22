"""Lot ac-lotatc-v1 : dessins LotAtc. Cas synthetiques, sans les zips."""
import json
import math
import zipfile

import pytest

from render import fiches, geo, lotatc, plans

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
S, OX, OY = 0.05, 100.0, 200.0


def conv(X, Y):
    return lotatc.page_to_game(X, Y, S, OX, OY)


def svg(body):
    return plans.parse_svg(f'<svg {NS}>{body}</svg>'.encode())


def test_page_to_game_is_inverse_of_contract():
    x, y = 1234.5, -678.9
    X, Y = OX + y * S, OY - x * S                  # contrat dcs-plans-v1
    gx, gy = lotatc.page_to_game(X, Y, S, OX, OY)
    assert gx == pytest.approx(x) and gy == pytest.approx(y)


def test_to_latlon_rounds_to_7_decimals():
    proj = geo.Projection.for_theatre('Syria')
    la, lo = lotatc.to_latlon(proj, 0.0, 0.0)
    assert la == round(la, 7) and lo == round(lo, 7)
    assert (la, lo) == tuple(round(v, 7) for v in proj.to_latlon(0.0, 0.0))


def square(X0, Y0, side_mm):
    return [(X0, Y0), (X0 + side_mm, Y0), (X0 + side_mm, Y0 + side_mm), (X0, Y0 + side_mm)]


def d_of(*rings):
    return ' '.join('M ' + ' L '.join(f'{X} {Y}' for X, Y in r) + ' Z' for r in rings)


def test_pavement_rings_split_on_M_and_m_short_piece_and_small_area():
    big = square(0, 0, 4)                         # 4 mm = 80 m a S = 0,05 : 6 400 m2
    small = square(10, 10, 0.8)                   # 16 m de cote : 256 m2 < 400
    d = d_of(big) + ' m 1 1 2 2 ' + d_of(small) + ' ' + d_of(square(20, 20, 2))
    rings = lotatc.pavement_rings(d, conv)
    assert len(rings) == 2                        # le grand, le dernier (40 m : 1 600 m2) ; petit et morceau court omis
    assert all(len(r) == 4 for r in rings)        # sans la repetition finale


def test_pavement_ring_area_threshold_is_inclusive():
    side_mm = 20 * S                              # 20 m de cote : 400 m2 pile
    assert len(lotatc.pavement_rings(d_of(square(0, 0, side_mm)), conv)) == 1
    assert lotatc.pavement_rings(d_of(square(0, 0, side_mm * 0.99)), conv) == []


def test_runway_axis_of_rectangle_and_designator_120m_beyond_nearest_end():
    pts = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 40.0), (0.0, 40.0)]        # metres, piste le long de x
    a, b = lotatc.runway_axis(pts)
    assert a == (1000.0, 20.0) and b == (0.0, 20.0)                        # petits cotes j = 1 puis j = 3 (egaux : par j)
    pos = lotatc.designator_position((900.0, 80.0), [(a, b)])
    assert pos == pytest.approx((1120.0, 20.0))                            # extremite fin la plus proche, +120 m
    pos = lotatc.designator_position((-30.0, 5.0), [(a, b)])
    assert pos == pytest.approx((-120.0, 20.0))
    assert lotatc.designator_position((7.0, 8.0), []) == (7.0, 8.0)


def test_designator_tie_takes_start_end_first():
    a, b = (0.0, 0.0), (100.0, 0.0)
    pos = lotatc.designator_position((50.0, 10.0), [(a, b)])
    assert pos == pytest.approx((-120.0, 0.0))


def plan_svg(extra=''):
    runway = ' '.join(f'{X},{Y}' for X, Y in [(0, 0), (50, 0), (50, 2), (0, 2)])
    return svg(
        f'<g inkscape:label="Pavement"><path d="{d_of(square(0, 0, 4))}"/></g>'
        f'<g inkscape:label="Runways"><polygon points="{runway}"/><polygon points="0,0 1,0 1,1"/></g>'
        '<g inkscape:label="Runway markings"><g transform="translate(55 1)"><text>09</text></g>'
        '<g transform="translate(1,1)"><path d="M0 0"/></g></g>'
        '<g inkscape:label="Taxiway labels"><rect x="10" y="10" width="4" height="2"/><text>A</text>'
        '<rect x="0" y="0" width="2" height="2"/><text>  </text><text>B</text></g>' + extra)


def test_plan_drawings_order_names_and_counts():
    proj = geo.Projection.for_theatre('Syria')
    drawings, counts, pav = lotatc.plan_drawings(plan_svg(), S, OX, OY, proj)
    assert [d['name'] for d in drawings] == ['pavement 1', 'runway 1', 'runway 2', 'RWY 09', 'TWY A', 'TWY B']
    assert counts == {'pavement': 1, 'runway': 2, 'designator': 1, 'taxiway': 2, 'stand': 0}
    assert pav == 4
    twy = {d['name']: d for d in drawings}
    assert twy['TWY A']['text'] == 'A'
    x, y = conv(12.0, 11.0)                                                # centre du rect qui precede
    assert (twy['TWY A']['latitude'], twy['TWY A']['longitude']) == lotatc.to_latlon(proj, x, y)
    x, y = conv(1.0, 1.0)                                                  # B : le dernier rect vu
    assert (twy['TWY B']['latitude'], twy['TWY B']['longitude']) == lotatc.to_latlon(proj, x, y)


def test_designator_placed_beyond_runway_end():
    proj = geo.Projection.for_theatre('Syria')
    drawings, _, _ = lotatc.plan_drawings(plan_svg(), S, OX, OY, proj)
    rwy = next(d for d in drawings if d['name'] == 'RWY 09')
    ax, bx = [conv(X, Y) for X, Y in [(0, 1), (50, 1)]]                    # axe en mm -> jeu
    end, other = bx, ax                                                    # (55, 1) est plus pres de (50, 1)
    L = math.dist(end, other)
    want = (end[0] + (end[0] - other[0]) / L * 120, end[1] + (end[1] - other[1]) / L * 120)
    assert (rwy['latitude'], rwy['longitude']) == lotatc.to_latlon(proj, *want)


def test_taxiway_text_without_rect_stops():
    proj = geo.Projection.for_theatre('Syria')
    root = svg('<g inkscape:label="Taxiway labels"><text>A</text></g>')
    with pytest.raises(lotatc.LotatcError):
        lotatc.plan_drawings(root, S, OX, OY, proj)


def test_empty_plan_has_no_drawing_and_layers_are_found_at_top_level_only():
    proj = geo.Projection.for_theatre('Syria')
    nested = svg('<g><g inkscape:label="Pavement"><path d="%s"/></g></g>' % d_of(square(0, 0, 4)))
    drawings, counts, _ = lotatc.plan_drawings(nested, S, OX, OY, proj)
    assert drawings == [] and sum(counts.values()) == 0


def test_key_order_and_styles():
    d = lotatc.point_drawing('TWY A', (37.1, 32.2), 'A', 'taxiway')
    assert list(d) == ['type', 'name', 'shared', 'visible', 'latitude', 'longitude', 'text',
                       'lineWidth', 'color', 'colorBg', 'brushStyle', 'font']
    assert d['font'] == {'font': 'LotAtc Sans', 'color': '#ffffd200'}
    assert list(d['font']) == ['font', 'color']
    p = lotatc.polygon_drawing('pavement 1', [(1.0, 2.0)] * 4, 'pavement')
    assert list(p) == ['type', 'name', 'shared', 'visible', 'points', 'lineWidth', 'color', 'colorBg', 'brushStyle']
    assert p['points'][0] == {'latitude': 1.0, 'longitude': 2.0}
    assert (p['color'], p['colorBg'], p['brushStyle'], p['lineWidth']) == ('#ffa8a8a8', '#00000000', 0, 1)
    r = lotatc.polygon_drawing('runway 1', [(1.0, 2.0)] * 4, 'runway')
    assert (r['color'], r['colorBg'], r['brushStyle']) == ('#ffffffff', '#66ffffff', 1) and 'font' not in r
    t = lotatc.point_drawing('RWY 09', (1.0, 2.0), '09', 'designator')
    assert (t['color'], t['colorBg'], t['font']['color']) == ('#00000000', '#00000000', '#ffffffff')
    s = lotatc.point_drawing('stand 5', (1.0, 2.0), '5', 'stand')
    assert (s['lineWidth'], s['color'], s['colorBg'], s['brushStyle'], s['font']['color']) == \
        (4, '#ff4fc3f7', '#ff4fc3f7', 1, '#ff4fc3f7')


def test_layer_dict_and_text():
    import uuid
    plan = lotatc.layer_dict('LTAN_Konya', 'plan', [])
    assert list(plan) == ['author', 'drawings', 'enable', 'id', 'name', 'shared', 'timestamp', 'type', 'version']
    assert plan['name'] == 'LTAN Konya (DCS)'
    assert plan['id'] == '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'dcs-airfield-charts/lotatc/LTAN_Konya/plan')) + '}'
    park = lotatc.layer_dict('LTAN_Konya', 'parking', [])
    assert park['name'] == 'LTAN Konya parking (DCS)' and park['id'] != plan['id']
    assert (plan['author'], plan['enable'], plan['shared'], plan['timestamp'], plan['type'], plan['version']) == \
        ('DCS Airfield Charts', 'true', False, '', 'layer', 'hotfixes/220')
    txt = lotatc.layer_text(plan)
    assert txt.endswith('}\n') and txt.startswith('{\n "author"')
    assert json.loads(txt) == plan


def test_parking_drawings_use_exact_positions():
    proj = geo.Projection.for_theatre('Syria')
    ad = {'parkings': [{'parking_id': '26', 'x': 76271.68, 'y': 112805.54}, {'parking_id': 'A1', 'x': 1.0, 'y': 2.0}]}
    ds = lotatc.parking_drawings(ad, proj)
    assert [d['name'] for d in ds] == ['stand 26', 'stand A1'] and ds[0]['text'] == '26'
    assert (ds[0]['latitude'], ds[0]['longitude']) == lotatc.to_latlon(proj, 76271.68, 112805.54)


def result():
    res = lotatc.Result('Syria', 'Syria', 'AC-V9.9.9')
    res.layers = [lotatc.Layer('B_Base_parking.json', '{"b": 1}\n', 'parking'),
                  lotatc.Layer('B_Base.json', '{"a": 1}\n', 'plan'),
                  lotatc.Layer('A_Base_parking.json', '{"c": 1}\n', 'parking')]
    return res


def test_zip_root_order_fixed_date_and_readme(tmp_path):
    folder, z = lotatc.write(result(), tmp_path)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert names == ['A_Base_parking.json', 'B_Base.json', 'B_Base_parking.json', 'README.txt']
        assert all('/' not in n for n in names)
        assert all(i.date_time == fiches.ZIP_DATE and i.compress_type == zipfile.ZIP_DEFLATED for i in zf.infolist())
        readme = zf.read('README.txt').decode()
    assert readme.startswith('DCS Airfield Charts - LotAtc drawings - SYRIA - AC-V9.9.9\n\nOne LotAtc drawing layer')
    assert readme.endswith('Source: https://github.com/MirabelleBenou/dcs-airfield-charts\n')
    assert sorted(p.name for p in folder.iterdir()) == names[:-1]


def test_readme_display_name_for_germany():
    assert lotatc.readme_text('GermanyColdWar', 'AC-V1').splitlines()[0].endswith('GERMANY COLD WAR - AC-V1')


def test_two_passes_same_bytes(tmp_path):
    _, z1 = lotatc.write(result(), tmp_path / 'a')
    _, z2 = lotatc.write(result(), tmp_path / 'b')
    assert z1.read_bytes() == z2.read_bytes()


def test_refuses_to_overwrite(tmp_path):
    lotatc.write(result(), tmp_path)
    with pytest.raises(lotatc.LotatcError):
        lotatc.write(result(), tmp_path)
    (tmp_path / 'DCS_Airfield_Charts_Syria_1985_lotatc.zip').unlink()
    with pytest.raises(lotatc.LotatcError):                                # dossier non vide
        lotatc.check_targets(tmp_path, 'Syria')


def test_plan_dict_json_is_deterministic():
    proj = geo.Projection.for_theatre('Syria')
    a = lotatc.layer_text(lotatc.layer_dict('X', 'plan', lotatc.plan_drawings(plan_svg(), S, OX, OY, proj)[0]))
    b = lotatc.layer_text(lotatc.layer_dict('X', 'plan', lotatc.plan_drawings(plan_svg(), S, OX, OY, proj)[0]))
    assert a == b
