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


def txt(x, y, s, extra=''):
    return f'<text x="{x}" y="{y}" font-family="Barlow Condensed" font-size="2"{extra}>{s}</text>'


def page_with_everything():
    """Une page synthetique : halo, doublon, groupe tourne, texte a occurrences, encre dans deux couches."""
    header = layer('Header', txt(6, 11.4, 'LTAN') + txt(6, 11.4, 'LTAN', ' class="halo"') + txt(142, 11.4, '10-9', ' text-anchor="end"'))
    marks = layer('Runway bearings',
                  txt(0, 0.79, '197.4°', ' text-anchor="middle" transform="translate(92.632 51.69) rotate(20.67)"')
                  + txt(0, 0.79, '197.4°', ' text-anchor="middle" transform="translate(85.903 49.223) rotate(20.73)"')
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


def test_text_target_applies_rotated_group_transform(page):
    el = page.find_text('Runway bearings', '197.4°', occurrence=1)
    # (0, 0.79) tourne de 20,67 degres puis translate(92.632 51.69)
    assert el.x == pytest.approx(92.632 - 0.79 * 0.3528, abs=0.01)
    assert el.y == pytest.approx(51.69 + 0.79 * 0.9356, abs=0.01)


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
    spec = {'id': 'chX', 'source': 'p', 'pins': [
        {'n': 1, 'target': {'kind': 'point', 'layer': 'Navaids', 'at': [90.0, 90.0]}, 'badge': [1, 1], 'title': 't'}]}
    with pytest.raises(guide.GuideError, match=r'chX.*pastille 1.*Navaids'):
        guide.resolve_pins(src, spec)
    spec['pins'][0]['target']['at'] = [61.5, 44.2]
    pins = guide.resolve_pins(src, spec)
    assert pins[0].check == 'encre' and pins[0].aim == (61.5, 44.2)


def test_resolve_pins_text_pin_with_nudge():
    src = guide.Sources.__new__(guide.Sources)
    src.defs = {'p': {'zip': 'a', 'file': 'x'}}
    src._svg = {'p': guide.SvgPage('p', page_with_everything())}
    spec = {'id': 'chX', 'source': 'p', 'pins': [
        {'n': 1, 'target': {'kind': 'text', 'layer': 'Header', 'text': 'LTAN', 'nudge': [-1, -1.4]}, 'badge': [-4, 0],
         'title': 'Airfield', 'text': 'x'}]}
    p = guide.resolve_pins(src, spec)[0]
    assert p.check == 'texte' and p.anchor == (6.0, 11.4) and p.aim == (5.0, pytest.approx(10.0))


# --------------------------------------------------------------------------------------------------- chiffres cites
def test_cited_strings_are_checked_on_the_page_text_ignoring_case_and_spacing():
    src = guide.Sources.__new__(guide.Sources)
    src.defs = {'p': {'zip': 'a', 'file': 'x'}}
    svg = svg_page(layer('Header', txt(1, 1, 'KNY  390') + txt(2, 2, 'Karatay City Park 11 KM') + txt(3, 3, 'NDB KNY')
                         + txt(4, 4, '390') + txt(5, 5, 'hidden', ' class="halo"')))
    src._svg = {'p': guide.SvgPage('p', svg)}
    ok = [{'block': 'b', 'page': 'p', 'strings': ['kny 390', 'KARATAY CITY PARK 11 km', 'NDB KNY 390']}]
    assert guide.check_cited(src, ok) == 3
    with pytest.raises(guide.GuideError, match=r'« 4700 FT ».*page p'):
        guide.check_cited(src, [{'block': 'ch8, pin 3', 'page': 'p', 'strings': ['4700 FT']}])
    with pytest.raises(guide.GuideError, match='hidden'):      # un halo n'est pas le texte de la page
        guide.check_cited(src, [{'block': 'b', 'page': 'p', 'strings': ['hidden']}])


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
    pins = [guide.Pin(n, 'page', 'text', 'L', (0, 0), (0, 0), (0, 0), 'TITLE', 'word ' * 60, 'texte', 'p', 'ch98')
            for n in range(1, 6)]
    with pytest.raises(guide.GuideError, match='legendes ne tiennent pas'):
        guide.draw_legend(pg, pins, 137.4, 32.5, 55.0, 80.0)


def test_title_too_long_for_the_header_stops():
    pg = guide.Page('ch97', 'P', 'A title that is far too long for a page header at twenty points ' * 2)
    with pytest.raises(guide.GuideError, match=r'ch97.*titre'):
        guide.page_svg(pg, 1, 'AC-V1.9.0')


# ----------------------------------------------------------------------------------------------------- un mini guide
def mini_zips(tmp_path, svg=None, directory_size=(1748, 2480)):
    from PIL import Image
    svg = svg or page_with_everything()
    a, h = tmp_path / 'a.zip', tmp_path / 'h.zip'
    with zipfile.ZipFile(a, 'w') as z:
        z.writestr('root_airfields/svg/A_10-9.svg', svg)
    with zipfile.ZipFile(h, 'w') as z:
        z.writestr('root_helicopters/svg/B_10-9.svg', svg)
        buf = io.BytesIO()
        Image.new('RGB', directory_size, (230, 220, 200)).save(buf, 'JPEG')
        z.writestr('root_helicopters/heliports/dir_p11.jpg', buf.getvalue())
    return {'airfields': str(a), 'helicopters': str(h)}


def mini_data():
    pin = lambda n, tg, title, text='': {'n': n, 'target': tg, 'badge': [-4, 0], 'title': title, 'text': text}
    return {
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
    assert r.metadata.title == guide.PDF_TITLE and r.metadata.author == guide.PDF_AUTHOR
    assert '/CreationDate' not in r.trailer['/Info'] and '/ModDate' not in r.trailer['/Info']
    assert abs(float(r.pages[0].mediabox.width) - 595.3) < 1 and abs(float(r.pages[0].mediabox.height) - 841.9) < 1
    flat = lambda i: ''.join(r.pages[i].extract_text().split())
    assert 'ReleaseAC-V9.9.9here.' in flat(0)                 # <TAG> = l'argument --edition
    assert 'Textpage' in flat(1) and 'Anatomy' in flat(1)    # le sommaire
    assert 'Lead.Somebodytextwithcode.' in flat(2)
    assert 'READINGGUIDE' in flat(3) and 'AC-V9.9.9' in flat(3)


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
    # le theatre et les pages d'exemple sont au fichier de donnees
    assert d['theatre'] == 'Syria' and {'konya_10-9', 'konya_11-1', 'konya_11-1H', 'naqoura_10-9'} <= set(d['sources'])
    # les pastilles de l'annuaire, seules, n'ont pas de controle de cible
    kinds = {x['target']['kind'] for p in d['pages'] if p.get('source') == 'directory_p11' for x in p['pins']}
    assert kinds == {'image'}
    assert all(x['target']['kind'] != 'image' for p in d['pages'] if p.get('source') != 'directory_p11'
               for x in p.get('pins', []))
    json.dumps(d)
