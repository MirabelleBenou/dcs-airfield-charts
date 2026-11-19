"""Lot ac-plans-v1 : plans d'aerodrome reduits, calage sur les places, abris. Cas synthetiques, sans les zips."""
import json
import zipfile

import pytest

from render import fiches, page, plans

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
S0, OX0, OY0 = 0.037, 1234.5678, -987.6543


def make_places(n=12):
    """Places (x nord, y est) en metres, en grille irreguliere."""
    return [(1000.0 + 37.0 * i + 5.0 * (i % 3), -500.0 + 41.0 * (i * 7 % 11) + 3.0 * i) for i in range(n)]


def to_page(places, S=S0, OX=OX0, OY=OY0):
    return [(OX + y * S, OY - x * S) for x, y in places]


def test_fit_recovers_known_calibration():
    places = make_places()
    S, OX, OY, pair, gap = plans.fit(places, to_page(places))
    assert abs(S - S0) < 1e-6 and abs(OX - OX0) < 1e-4 and abs(OY - OY0) < 1e-4
    assert pair == list(range(len(places)))
    assert gap < 1e-3


def test_fit_with_shuffled_circles():
    places = make_places()
    circles = to_page(places)
    rev = circles[::-1]
    S, OX, OY, pair, _ = plans.fit(places, rev)
    assert abs(S - S0) < 1e-6
    assert [rev[j] for j in pair] == pytest.approx(circles)


def test_fit_refuses_extra_circle():
    places = make_places()
    with pytest.raises(plans.PlansError, match='13 ronds pour 12 places'):
        plans.fit(places, to_page(places) + [(50.0, 50.0)])


def test_fit_refuses_missing_circle():
    places = make_places()
    with pytest.raises(plans.PlansError, match='11 ronds pour 12 places'):
        plans.fit(places, to_page(places)[:-1])


def test_fit_refuses_gap_over_limit():
    places = make_places()
    circles = to_page(places)
    circles[3] = (circles[3][0] + 0.2, circles[3][1] - 0.2)
    with pytest.raises(plans.PlansError, match='ecart maximal'):
        plans.fit(places, circles)


def test_fit_refuses_non_one_to_one():
    places = [(0.0, 0.0), (0.0, 100.0), (0.0, 200.0), (500.0, 100.0)]
    circles = [(0.0, 0.0), (3.7, 0.0), (7.4, 0.0), (3.7, -18.5)]
    circles[3] = (3.7, 0.0)                     # deux places sur le meme rond
    with pytest.raises(plans.PlansError):
        plans.fit(places, circles)


def circle_svg(c, fill='#1A1A1A', sw=None):
    extra = f' stroke="#1A1A1A" stroke-width="{sw}"' if sw is not None else ''
    return f'<circle cx="{c[0]:.3f}" cy="{c[1]:.3f}" r="0.45" fill="{fill}"{extra}/>'


def chart_svg(circles_xml, header='PARKING: 3 (1 SHELTERED)', extra_layers=''):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg {NS} width="148mm" height="210mm" viewBox="0 0 148 210">\n'
            '<defs><clipPath id="clip-plan"><rect x="11" y="31.9" width="126" height="151"/></clipPath></defs>\n'
            '<g inkscape:groupmode="layer" id="layer-background" inkscape:label="Background"><rect width="148" height="210" fill="#FFF"/></g>\n'
            '<g inkscape:groupmode="layer" id="layer-parking" inkscape:label="Parking" clip-path="url(#clip-plan)">\n'
            + '\n'.join(circles_xml) + '\n</g>\n'
            '<g inkscape:groupmode="layer" id="layer-buildings" inkscape:label="Buildings" clip-path="url(#clip-plan)">'
            '<path d="M1 1 2.123456 2.5 3 3" fill="#4D4D4D" fill-rule="evenodd"/></g>\n'
            '<g inkscape:groupmode="layer" id="layer-pavement" inkscape:label="Pavement" clip-path="url(#clip-plan)">'
            '<path d="M0 0 1 1 2 0" fill="#CCCCCC" opacity="0.5" transform="translate(1 2)"/></g>\n'
            + extra_layers +
            '<g inkscape:groupmode="layer" id="layer-header" inkscape:label="Header">'
            f'<text x="142" y="186.94" font-size="2.3">{header}</text></g>\n</svg>\n').encode('utf-8')


def airdrome(places, aid=7, name='Testfield'):
    return {'airdromeId': aid, 'name': name, 'x': 0, 'y': 0,
            'parkings': [{'parking': 10 + i, 'parking_id': str(i), 'x': x, 'y': y} for i, (x, y) in enumerate(places)]}


def three():
    places = [(1000.0, 0.0), (1200.0, 300.0), (900.0, 700.0)]
    return places, to_page(places)


def test_shelters_by_style_and_header_check():
    places, pc = three()
    xml = [circle_svg(pc[0]), circle_svg(pc[1], '#FFFFFF', page.STROKE_SHELTER), circle_svg(pc[2], '#FFFFFF', page.STROKE_HELI)]
    plan = plans.plan_for_chart(chart_svg(xml), airdrome(places))
    assert plan.shelters == [11] and plan.n_places == 3


def test_shelters_use_parking_not_parking_id_and_sorted():
    places, pc = three()
    ad = airdrome(places)
    ad['parkings'][0]['parking'], ad['parkings'][2]['parking'] = 90, 5
    xml = [circle_svg(pc[0], '#FFFFFF', page.STROKE_SHELTER), circle_svg(pc[1]), circle_svg(pc[2], '#FFFFFF', page.STROKE_SHELTER)]
    plan = plans.plan_for_chart(chart_svg(xml, 'PARKING: 3 (2 SHELTERED)'), ad)
    assert plan.shelters == [5, 90]


def test_header_mismatch_stops():
    places, pc = three()
    xml = [circle_svg(c) for c in pc]
    with pytest.raises(plans.PlansError, match='SHELTERED'):
        plans.plan_for_chart(chart_svg(xml, 'PARKING: 3 (1 SHELTERED)'), airdrome(places))
    with pytest.raises(plans.PlansError):
        plans.plan_for_chart(chart_svg(xml, 'PARKING: 4 (0 SHELTERED)'), airdrome(places))


def test_heliport_without_header_has_no_shelter():
    places, pc = three()
    xml = [circle_svg(c, '#FFFFFF', page.STROKE_HELI) for c in pc]
    plan = plans.plan_for_chart(chart_svg(xml, 'HELIPADS: 3'), airdrome(places))
    assert plan.shelters == []
    xml[0] = circle_svg(pc[0], '#FFFFFF', page.STROKE_SHELTER)
    with pytest.raises(plans.PlansError):
        plans.plan_for_chart(chart_svg(xml, 'HELIPADS: 3'), airdrome(places))


def test_reduction_layers_order_and_content():
    places, pc = three()
    extra = ('<g inkscape:groupmode="layer" id="layer-runways" inkscape:label="Runways" clip-path="url(#clip-plan)">'
             '<g><text x="1" y="1">09</text><text x="2" y="2" class="halo">09</text></g>'
             '<g><polygon points="19.803,104.67 118.354,111.29" fill="#1A1A1A"/><text>x</text></g></g>\n'
             '<g inkscape:groupmode="layer" id="layer-runway-markings" inkscape:label="Runway markings"><text>t</text>'
             '<line x1="26.432" y1="104.433" x2="111.817" y2="110.169" stroke="#FFFFFF" stroke-width="0.3"/></g>\n'
             '<g inkscape:groupmode="layer" id="layer-insets" inkscape:label="Insets"><rect width="1" height="1"/></g>\n')
    out = plans.reduce_svg(plans.parse_svg(chart_svg([circle_svg(c) for c in pc], extra_layers=extra))).decode()
    labels = [l.split('"')[0] for l in out.split('inkscape:label="')[1:]]
    assert labels == ['Pavement', 'Runways', 'Runway markings', 'Buildings']
    for bad in ('<text', 'clip-path', 'defs', 'Parking', 'Insets', 'Header', 'Background', 'halo'):
        assert bad not in out
    assert out.count('<g') == 5                          # 4 couches + le g qui garde un polygone ; le g des textes est retire
    assert 'points="19.803,104.67 118.354,111.29"' in out and 'M1 1 2.123456 2.5 3 3' in out
    assert 'opacity="0.5" transform="translate(1 2)"' in out and 'fill-rule="evenodd"' in out
    assert 'viewBox="0 0 148 210"' in out and 'width="148mm"' in out and 'height="210mm"' in out
    assert 'xmlns="http://www.w3.org/2000/svg"' in out and 'xmlns:inkscape=' in out
    assert out.startswith("<?xml version='1.0' encoding='UTF-8'?>\n<svg")


def test_reduction_missing_layer_is_absent():
    places, pc = three()
    out = plans.reduce_svg(plans.parse_svg(chart_svg([circle_svg(c) for c in pc]))).decode()
    assert 'Runways' not in out and out.index('Pavement') < out.index('Buildings')


def make_pkg(shelters=((1, [10, 11]), (2, []))):
    places, pc = three()
    pkg = plans.Package('Syria', 'AC-V1.7.1')
    for aid, sh in shelters:
        pkg.plans[aid] = plans.Plan(aid, f's{aid}', 0.035512996, 9627.7063, -1174.024, 0.001, 3, sh, f'<svg id="{aid}"/>'.encode())
    return pkg


def test_index_shape():
    pkg = make_pkg(((44, []), (9, []), (100, [])))
    assert list(pkg.plans) == [44, 9, 100]
    text = plans.index_json(pkg)
    d = json.loads(text)
    assert list(d) == ['format', 'theatre', 'edition', 'plans']
    assert d['format'] == 'dcs-plans-v1' and d['theatre'] == 'Syria' and d['edition'] == 'AC-V1.7.1'
    assert list(d['plans']) == ['9', '44', '100']
    assert d['plans']['44'] == {'fichier': '44.svg', 'S': 0.035512996, 'OX': 9627.7063, 'OY': -1174.024}
    assert text.startswith('{\n "format": "dcs-plans-v1",\n') and text.endswith('}\n') and not text.endswith('\n\n')
    assert '\n  "44": {\n   "fichier": "44.svg",\n   "S": 0.035512996,' in text


def test_shelters_file_shape():
    text = plans.shelters_text(make_pkg(((44, [12, 10, 11]), (1, []), (100, [3]))))
    lines = text.split('\n')
    assert lines[0] == ('{"format":"dcs-shelters-v1","theatre":"Syria","edition":"AC-V1.7.1","source":"DCS Airfield Charts '
                        'AC-V1.7.1: parking stands drawn as hardened shelters on the 10-9 charts","airdromes":{')
    assert lines[1:] == ['"1":[],', '"44":[10,11,12],', '"100":[3]', '}}', '']
    assert json.loads(text)['airdromes'] == {'1': [], '44': [10, 11, 12], '100': [3]}


def test_write_package_zip_flat_deterministic_and_no_overwrite(tmp_path):
    pkg = make_pkg()
    folder, zpath = plans.write_package(pkg, tmp_path / 'a', 'Syria', tmp_path / 'sh' / 'Syria.json')
    assert folder.name == 'Syria' and zpath.name == 'DCS_Airfield_Charts_Syria_1985_plans.zip'
    assert sorted(p.name for p in folder.iterdir()) == ['1.svg', '2.svg', 'index.json']
    with zipfile.ZipFile(zpath) as z:
        assert z.namelist() == ['index.json', '1.svg', '2.svg']
        assert all(i.compress_type == zipfile.ZIP_DEFLATED and i.date_time == fiches.ZIP_DATE for i in z.infolist())
    plans.write_package(make_pkg(), tmp_path / 'b', 'Syria', tmp_path / 'sh2.json')
    assert (tmp_path / 'b' / 'DCS_Airfield_Charts_Syria_1985_plans.zip').read_bytes() == zpath.read_bytes()
    assert (tmp_path / 'sh2.json').read_bytes() == (tmp_path / 'sh' / 'Syria.json').read_bytes()
    with pytest.raises(plans.PlansError):
        plans.write_package(pkg, tmp_path / 'a', 'Syria')
    with pytest.raises(plans.PlansError, match='abris'):
        plans.write_package(pkg, tmp_path / 'c', 'Syria', tmp_path / 'sh2.json')
    assert not (tmp_path / 'c').exists()


def test_unknown_theatre_stops():
    with pytest.raises(fiches.FichesError):
        plans.check_targets('.', 'Caucasus')


def test_two_names_one_airdrome_stops():
    ads = [{'airdromeId': 1, 'name': 'A'}, {'airdromeId': 2, 'name': 'A'}]
    with pytest.raises(plans.PlansError):
        plans.airdrome_by_name(ads, 'A')
    with pytest.raises(plans.PlansError):
        plans.airdrome_by_name(ads, 'B')
