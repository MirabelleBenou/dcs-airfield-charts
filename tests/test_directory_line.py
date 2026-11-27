"""Lot ac-repertoire-ville : la ligne de la ville du repertoire des heliports se reduit juste assez pour laisser un blanc avant les
dimensions des aires ; meme regle dans le moteur (`directory.town_size`) et dans `render/republish.py` (zip *helicopters*).
Tests purs : pydcs seul, aucun echantillon ; les zips sont synthetiques (aux noms du repertoire publie)."""
import io
import zipfile

import numpy as np
import pytest
from PIL import Image, ImageDraw, JpegImagePlugin
from pypdf import PdfReader

from render import directory, republish

# Syria : les cinq vignettes dont la ville, a 36 px, touche les dimensions ou en est a moins de 24 px (textes en anglais : `of`, `PADS`)
SYRIA_SMALL = {'HS03': 34, 'HS07': 34, 'HS17': 34.5, 'HT02': 32.5, 'H_med_orig_09': 32.5}
SYRIA_PAGES = [30, 31, 33, 43, 46]
FOLDER = 'DCS_Airfield_Charts_Syria_1985_helicopters/'


# ---------- la taille : une seule fonction ----------
def gap(town, dims, w, size):
    """Blanc (px) entre la fin de la ville a `size` et le debut des dimensions."""
    return (w - directory.PLAN_DX - directory.font('regular', 36).getlength(dims)) \
        - (directory.PLAN_DX + directory.font('regular', size).getlength(town))


@pytest.mark.parametrize('town', ['0.5 km NW of Al Safira Military Base', '6.5 km NE of Chemical Weapons Storage Area',
                                  '0.8 km S of City Training and Research Hospital', '0.7 km SW of Army Vehicle Training Ground'])
def test_town_size_is_the_largest_step_leaving_24_px(town):
    w, dims = directory.TILE_W, 'PADS 42×34 M'
    size = directory.town_size(town, dims, w)
    assert 0 < size <= 36 and size * 2 == int(size * 2)               # pas de 0,5 px
    assert gap(town, dims, w, size) >= 24
    if size < 36:
        assert gap(town, dims, w, size + 0.5) < 24                    # la taille au-dessus ne laisse pas 24 px


def test_town_that_fits_stays_at_36():
    size = directory.town_size('0.5 km NW of Al Safira Military Base', 'PADS 42×34 M', directory.TILE_W)
    assert size == 36 and isinstance(size, int)
    assert directory.font('regular', 36.0) is directory.font('regular', 36)      # 36 et 36.0 : la meme police


def test_town_size_wider_tile_gives_more_room():
    town, dims = '0.8 km S of City Training and Research Hospital', 'PADS 42×34 M'
    wide = 2 * directory.TILE_W + directory.TILE_GAP
    assert directory.town_size(town, dims, directory.TILE_W) < 36 == directory.town_size(town, dims, wide)


def test_town_size_stops_when_nothing_fits():
    with pytest.raises(directory.DirectoryError):
        directory.town_size('0.5 km N of Nazareth', 'PADS 42×34 M', 100)


# ---------- le rejeu de Syria et de Germany ----------
@pytest.fixture(scope='module')
def syria_tiles():
    return republish.directory_tiles('Syria')


def test_syria_replay_gives_the_five_sites(syria_tiles):
    tiles, n_index = syria_tiles
    assert n_index == 3 and len(tiles) == 141 and max(t[0] for t in tiles) == 47
    small = {t[1]: t[7] for t in tiles if t[7] < 36}
    assert small == SYRIA_SMALL
    assert all(t[7] == 36 for t in tiles if t[1] not in SYRIA_SMALL)
    assert sorted({t[0] for t in tiles if t[1] in SYRIA_SMALL}) == SYRIA_PAGES
    for _, site, x, w, base2, town, dims, size in tiles:
        assert gap(town, dims, w, size) >= 24, site


def test_germany_replay_has_no_small_town():
    tiles, n_index = republish.directory_tiles('GermanyColdWar')
    assert n_index == 3 and len(tiles) == 108 and max(t[0] for t in tiles) == 33
    assert all(t[7] == 36 for t in tiles)


def test_town_text_is_the_build_directory_one(syria_tiles):
    tiles, _ = syria_tiles
    towns = {t[1]: t[5] for t in tiles}
    assert towns['HS03'] == '6.5 km NE of Chemical Weapons Storage Area'
    assert towns['HT02'] == '0.8 km S of City Training and Research Hospital'


# ---------- le moteur et la republication appellent la meme fonction ----------
def test_engine_and_republish_use_the_same_size_function(monkeypatch):
    calls = []

    def fake(town, dims, w):
        calls.append((town, dims, w))
        return 20

    monkeypatch.setattr(directory, 'town_size', fake)
    # republication
    tiles, _ = republish.directory_tiles('Syria')
    assert len(calls) == 141 and all(t[7] == 20 for t in tiles)
    # moteur : draw_tile sur le site de HS03 (plan neutre)
    from render import fiches
    sites, pages, _ = fiches.replay_directory('Syria')
    site = next(s for s in sites if s.name == 'HS03')
    pos = next(p for pg in pages for s, p in pg['entries'] if s is site)
    site.town = '6.5 km NE of Chemical Weapons Storage Area'
    site.lat = site.lon = 0.0
    monkeypatch.setattr(directory, 'draw_plan', lambda site, img, g, c, w, h, first, stats: Image.new('RGB', (w, h), (222, 222, 222)))
    calls.clear()
    page = Image.new('RGB', directory.PAGE_PX, (255, 255, 255))
    directory.draw_tile(page, ImageDraw.Draw(page), site, pos, None, None, [])
    assert calls == [(site.town, directory.dims_line(site.aires), directory.tile_box(site.form, pos)[2])]


def test_engine_draws_the_line_like_the_republication(monkeypatch):
    """draw_tile ecrit, dans la bande, ce que `republish.line_band` redessine : meme fonction de texte, meme place."""
    from render import fiches
    sites, pages, _ = fiches.replay_directory('Syria')
    site = next(s for s in sites if s.name == 'HT02')
    pos = next(p for pg in pages for s, p in pg['entries'] if s is site)
    town = '0.8 km S of City Training and Research Hospital'
    site.town, site.lat, site.lon = town, 0.0, 0.0
    x, y, w, _ = directory.tile_box(site.form, pos)
    base2 = directory.tile_base2(site.form, y)
    page = Image.new('RGB', directory.PAGE_PX, (255, 255, 255))
    monkeypatch.setattr(directory, 'draw_plan', lambda site, img, g, c, w, h, first, stats: Image.new('RGB', (w, h), (255, 255, 255)))
    directory.draw_tile(page, ImageDraw.Draw(page), site, pos, None, None, [])
    x0, y0, x1, y1 = republish.band_box(x, w, base2)
    band = np.asarray(page, dtype=np.int16)[y0:y1 + 1, x0:x1 + 1]
    dims = directory.dims_line(site.aires)
    size = directory.town_size(town, dims, w)
    assert size == 32.5
    assert np.array_equal(band, republish.line_band(x, w, base2, town, dims, size))


# ---------- republish : un zip synthetique aux noms du repertoire publie ----------
def _published_pages(tiles):
    """{numero de page: image} des pages a vignette retouchee, telles que publiees : chaque vignette a 36 px (le pied est ajoute par `make_zip`)."""
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no not in SYRIA_PAGES:
            continue
        im = pages.setdefault(no, Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
        directory.draw_line2(ImageDraw.Draw(im), x, w, base2, town, dims, 36)
    return pages


def _jpeg(im):
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=90, dpi=(300, 300))
    return buf.getvalue()


def _pdf(images):
    buf = io.BytesIO()
    images[0].save(buf, 'PDF', resolution=300.0, save_all=True, append_images=images[1:], quality=90)
    return buf.getvalue()


N_INDEX = 3                                                          # pages d'index de Syria


def old_foot(im, no):
    """Le pied d'une page publiee avant le lot ac-marque-pied : le texte de gauche a MARGIN_X, pas de pictogramme."""
    im = im.copy()
    d = ImageDraw.Draw(im)
    left = republish.FOOT_INDEX_PUBLIE if no <= N_INDEX else republish.FOOT_LEFT_PUBLIE         # le texte publie
    directory.draw_text(d, (directory.MARGIN_X, 2379), left, 'medium', 29, directory.GREY, 'ls')
    directory.draw_text(d, (directory.PAGE_PX[0] - directory.MARGIN_X + 1, 2379), 'NOT FOR REAL-WORLD NAVIGATION', 'bold', 29, directory.INK, 'rs')
    return im


def new_foot(im, no):
    """Le pied d'une page publiee qui porte deja le pictogramme (le texte publie)."""
    return republish.redraw_directory_foot(im, republish.FOOT_INDEX_PUBLIE if no <= N_INDEX else republish.FOOT_LEFT_PUBLIE)


def make_zip(path, pages, n=47, skip_jpeg=None, extra=(), foot=old_foot):
    """Zip : `n` JPEG p01..pNN (les pages absentes de `pages` : pages blanches) et le PDF (n pages) ; `foot(page, numero)` dessine le pied
    de chacune (par defaut celui d'avant le lot : sans pictogramme)."""
    imgs = []
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(FOLDER + 'notes.txt', b'unchanged')
        for k in range(1, n + 1):
            im = foot(pages.get(k) or Image.new('RGB', directory.PAGE_PX, (255, 255, 255)), k)
            data = _jpeg(im)
            imgs.append(im)
            if k != skip_jpeg:
                z.writestr(f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg', data)
        z.writestr(FOLDER + 'Syria_heliports_directory.pdf', _pdf(imgs))
        for name, data in extra:
            z.writestr(name, data)
    return path


@pytest.fixture(scope='module')
def synth(tmp_path_factory, syria_tiles):
    tiles, _ = syria_tiles
    root = tmp_path_factory.mktemp('synth')
    pages = _published_pages(tiles)
    zin = make_zip(root / 'in.zip', pages)
    out = root / 'out.zip'
    res = republish.republish_zip('Syria', zin, out)
    return root, zin, out, res, pages, tiles


def _pdf_image_bytes(data):
    return [pg.images[0].indirect_reference.get_object()._data for pg in PdfReader(io.BytesIO(data)).pages]


def test_republish_directory_counts(synth):
    _, _, _, res, _, _ = synth
    d = res.directory
    assert (d.tiles, d.controlled, d.retouched, d.already, d.pages) == (141, 5, 5, 0, SYRIA_PAGES)
    assert (d.foot, d.foot_kept) == (list(range(1, 48)), [])
    assert all(g > 8 for g in d.foot_gaps.values())
    assert {r[1]: r[3] for r in d.rows} == SYRIA_SMALL
    assert all(r[4] <= 6 < r[5] for r in d.rows)


def test_republish_directory_entries(synth):
    _, zin, out, _, pages, tiles = synth
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        assert sorted(changed) == sorted([f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg' for k in range(1, 48)]
                                         + [FOLDER + 'Syria_heliports_directory.pdf'])
        for k in range(1, 48):
            name = f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg'
            old, new = Image.open(io.BytesIO(a.read(name))), Image.open(io.BytesIO(b.read(name)))
            assert new.quantization == old.quantization
            assert JpegImagePlugin.get_sampling(new) == JpegImagePlugin.get_sampling(old) and new.info['dpi'] == (300, 300)
            o, n = np.asarray(old.convert('RGB'), dtype=np.int16), np.asarray(new.convert('RGB'), dtype=np.int16)
            mask = np.ones(o.shape[:2], bool)
            mask[republish.FOOT_ROWS[0]:republish.FOOT_ROWS[1] + 1] = False             # le pied est redessine
            assert np.abs(o - n)[republish.FOOT_ROWS[0]:republish.FOOT_ROWS[1] + 1].mean() > 8
            for no, site, x, w, base2, town, dims, size in tiles:
                if no == k and size < 36:
                    x0, y0, x1, y1 = republish.band_box(x, w, base2)
                    mask[y0:y1 + 1, x0:x1 + 1] = False
                    assert republish.band_gap(n.astype(np.uint8), x, w, base2, town, dims, size) <= 6
                    assert republish.band_gap(n.astype(np.uint8), x, w, base2, town, dims, 36) > 6
            assert np.abs(o - n)[mask].mean() <= 0.5


def test_republish_directory_pdf(synth):
    _, zin, out, _, _, _ = synth
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        pa, pb = a.read(FOLDER + 'Syria_heliports_directory.pdf'), b.read(FOLDER + 'Syria_heliports_directory.pdf')
        ia, ib = _pdf_image_bytes(pa), _pdf_image_bytes(pb)
        assert len(ia) == len(ib) == 47
        for k in range(1, 48):
            assert ib[k - 1] == b.read(f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg')         # les octets du nouveau JPEG
            assert ib[k - 1] != ia[k - 1]
        ra, rb = PdfReader(io.BytesIO(pa)), PdfReader(io.BytesIO(pb))
        for x, y in zip(ra.pages, rb.pages):
            assert list(x.mediabox) == list(y.mediabox)
        for pg in rb.pages[29:30]:
            img = pg.images[0].indirect_reference.get_object()
            assert img['/Filter'] == '/DCTDecode' and (img['/Width'], img['/Height']) == directory.PAGE_PX


def test_republish_is_idempotent_on_the_directory(synth):
    root, _, out, _, _, _ = synth
    again = root / 'again.zip'
    res = republish.republish_zip('Syria', out, again)
    d = res.directory
    assert (d.controlled, d.retouched, d.already, d.pages) == (5, 0, 5, [])
    assert (d.foot, d.foot_kept) == ([], list(range(1, 48))) and all(g < 1 for g in d.foot_gaps.values())
    with zipfile.ZipFile(out) as a, zipfile.ZipFile(again) as b:
        assert a.read(FOLDER + 'Syria_heliports_directory.pdf') == b.read(FOLDER + 'Syria_heliports_directory.pdf')
        assert all(a.read(n) == b.read(n) for n in a.namelist())


def test_republish_summary_line(synth, capsys):
    root, zin, _, _, _, _ = synth
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(root / 'summary.zip')]) == 0
    out = capsys.readouterr().out
    assert ('repertoire des heliports : 141 vignettes ; controlees (ville sous 36 px) : 5 ; retouchees : 5 ; '
            'deja retouchees : 0 ; pages touchees : 5 p30 p31 p33 p43 p46') in out
    assert 'repertoire des heliports, pictogramme au pied : 47 pages retouchees ; 0 deja porteuses' in out

    again = root / 'summary_again.zip'
    assert republish.main(['Syria', '--zip', str(root / 'summary.zip'), '--out', str(again)]) == 0
    assert 'repertoire des heliports, pictogramme au pied : 0 pages retouchees ; 47 deja porteuses' in capsys.readouterr().out


def test_republish_untouched_directory_is_copied_to_the_byte(tmp_path, syria_tiles):
    """Toutes les vignettes deja retouchees et le pictogramme deja au pied de chaque page : rien ne change, tout est copie a l'octet."""
    tiles, _ = syria_tiles
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no in SYRIA_PAGES:
            im = pages.setdefault(no, Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
            directory.draw_line2(ImageDraw.Draw(im), x, w, base2, town, dims, size)
    zin = make_zip(tmp_path / 'in.zip', pages, foot=new_foot)
    out = tmp_path / 'out.zip'
    res = republish.republish_zip('Syria', zin, out)
    assert (res.directory.retouched, res.directory.already, res.directory.pages) == (0, 5, [])
    assert res.directory.foot == [] and len(res.directory.foot_kept) == 47
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert all(a.read(n) == b.read(n) for n in a.namelist())


# ---------- les arrets : rien n'est ecrit ----------
def test_band_that_matches_neither_size_stops_and_writes_nothing(tmp_path, syria_tiles, capsys):
    tiles, _ = syria_tiles
    pages = _published_pages(tiles)
    no, site, x, w, base2, town, dims, size = next(t for t in tiles if t[1] == 'HS03')
    directory.draw_line2(ImageDraw.Draw(pages[no]), x, w, base2, 'Autre ville que celle du repertoire', dims, 36)
    zin = make_zip(tmp_path / 'in.zip', pages)
    out = tmp_path / 'out.zip'
    with pytest.raises(republish.RepublishError, match='HS03.*ni a la ligne a 36 px'):
        republish.republish_zip('Syria', zin, out)
    assert not out.exists()
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 1
    assert 'ARRET' in capsys.readouterr().err and not out.exists()


def test_band_at_another_size_stops(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no in SYRIA_PAGES:
            im = pages.setdefault(no, Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
            directory.draw_line2(ImageDraw.Draw(im), x, w, base2, town, dims, 30 if site == 'HS17' else 36)
    zin = make_zip(tmp_path / 'in.zip', pages)
    with pytest.raises(republish.RepublishError, match='HS17'):
        republish.republish_zip('Syria', zin, tmp_path / 'out.zip')
    assert not (tmp_path / 'out.zip').exists()


def test_jpeg_count_not_matching_the_replay_stops(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    zin = make_zip(tmp_path / 'in.zip', _published_pages(tiles), skip_jpeg=47)
    with pytest.raises(republish.RepublishError, match='46 JPEG'):
        republish.republish_zip('Syria', zin, tmp_path / 'out.zip')
    assert not (tmp_path / 'out.zip').exists()


def test_pdf_page_count_not_matching_stops(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    zin = make_zip(tmp_path / 'in.zip', _published_pages(tiles))
    short = tmp_path / 'short.zip'
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(short, 'w') as b:
        for i in a.infolist():
            data = a.read(i)
            if i.filename.endswith('.pdf'):
                imgs = [Image.new('RGB', (40, 50), 'white') for _ in range(46)]
                data = _pdf(imgs)
            b.writestr(i.filename, data)
    with pytest.raises(republish.RepublishError, match='46 pages, mais 47 JPEG'):
        republish.republish_zip('Syria', short, tmp_path / 'out.zip')
    assert not (tmp_path / 'out.zip').exists()


def test_pdf_page_without_exactly_one_image_stops(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    zin = make_zip(tmp_path / 'in.zip', _published_pages(tiles))
    blank = tmp_path / 'blank.zip'
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(47):
        w.add_blank_page(100, 100)
    buf = io.BytesIO()
    w.write(buf)
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(blank, 'w') as b:
        for i in a.infolist():
            b.writestr(i.filename, buf.getvalue() if i.filename.endswith('.pdf') else a.read(i))
    with pytest.raises(republish.RepublishError, match='0 images'):
        republish.republish_zip('Syria', blank, tmp_path / 'out.zip')
    assert not (tmp_path / 'out.zip').exists()


def test_directory_pdf_missing_stops(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    zin = make_zip(tmp_path / 'in.zip', _published_pages(tiles))
    nopdf = tmp_path / 'nopdf.zip'
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(nopdf, 'w') as b:
        for i in a.infolist():
            if not i.filename.endswith('.pdf'):
                b.writestr(i.filename, a.read(i))
    with pytest.raises(republish.RepublishError, match='incomplet'):
        republish.republish_zip('Syria', nopdf, tmp_path / 'out.zip')


def test_other_names_keep_the_byte_copy(tmp_path):
    """Un zip dont les entrees ne portent pas les noms du repertoire (airfields, zips factices) : copie a l'octet."""
    zin = tmp_path / 'in.zip'
    with zipfile.ZipFile(zin, 'w') as z:
        z.writestr('pack/heliports/p01.jpg', b'\xff\xd8jpeg')
        z.writestr('pack/dir.pdf', b'%PDF-')
        z.writestr('pack/Other_heliports_directory.pdf', b'%PDF-')
    res = republish.republish_zip('Syria', zin, tmp_path / 'out.zip')
    assert res.directory.tiles == 0
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(tmp_path / 'out.zip') as b:
        assert all(a.read(n) == b.read(n) for n in a.namelist()) and res.copied == 3


# ---------- lot ac-marque-pied : le pictogramme au pied des pages du repertoire ----------
def _white():
    return Image.new('RGB', directory.PAGE_PX, (255, 255, 255))


def test_footer_pictogram_and_texts_are_where_the_cadrage_puts_them():
    import cairosvg
    from render import marque
    page = _white()
    directory.footer(page, ImageDraw.Draw(page), directory.FOOT_LEFT)
    src = (marque.MARQUE_DIR / marque.PICTO_FILE).read_text(encoding='utf-8').strip()
    pic = Image.open(io.BytesIO(cairosvg.svg2png(bytestring=src.encode('utf-8'), output_width=35, output_height=35))).convert('RGBA')
    want = _white()
    want.paste(pic, (directory.MARGIN_X, 2352), pic)
    wd = ImageDraw.Draw(want)
    directory.draw_text(wd, (directory.MARGIN_X + 50, 2379), directory.FOOT_LEFT, 'medium', 29, directory.GREY, 'ls')
    directory.draw_text(wd, (directory.PAGE_PX[0] - directory.MARGIN_X + 1, 2379), 'NOT FOR REAL-WORLD NAVIGATION', 'bold', 29, directory.INK, 'rs')
    assert np.array_equal(np.asarray(page), np.asarray(want))
    ink = np.asarray(page, dtype=np.int16).sum(axis=2) < 3 * 250
    assert ink[:, directory.MARGIN_X:directory.MARGIN_X + 35].nonzero()[0].min() >= 2352 and ink[:, directory.MARGIN_X:directory.MARGIN_X + 35].nonzero()[0].max() < 2352 + 35
    assert ink[2336:2400, directory.MARGIN_X + 36:directory.MARGIN_X + 50].sum() == 0          # entre l'image et le texte : blanc
    assert ink[2336:2400, directory.MARGIN_X + 50:].nonzero()[1].min() >= 0 and ink[:2336].sum() == 0
    assert pic.size == (35, 35) and pic.mode == 'RGBA' and pic.getchannel('A').getextrema() == (0, 255)


def test_footer_text_starts_50_px_after_the_margin_and_the_mention_of_the_right_is_unchanged():
    page = _white()
    d = ImageDraw.Draw(page)
    directory.footer(page, d, directory.FOOT_LEFT)
    base = _white()
    directory.draw_text(ImageDraw.Draw(base), (directory.PAGE_PX[0] - directory.MARGIN_X + 1, 2379), 'NOT FOR REAL-WORLD NAVIGATION', 'bold', 29,
                        directory.INK, 'rs')
    a, b = np.asarray(page), np.asarray(base)
    x1 = directory.PAGE_PX[0] - directory.MARGIN_X + 1
    cols = (b.astype(int).sum(axis=2) < 3 * 250).nonzero()[1]
    right = slice(cols.min(), cols.max() + 1)                                           # la mention de droite, a elle seule
    assert np.array_equal(a[:, right], b[:, right]) and cols.min() > 1000
    left_ink = (a[2336:2400, directory.MARGIN_X + 36:cols.min() - 20].astype(int).sum(axis=2) < 3 * 250).nonzero()[1]
    assert left_ink.min() + directory.MARGIN_X + 36 >= directory.MARGIN_X + 50


def test_engine_index_page_has_the_pictogram_footer(syria_tiles):
    from render import fiches
    sites, pages, n_index = fiches.replay_directory('Syria')
    page = directory.draw_index_page('Syria', sites, 0, n_index)
    want = republish.redraw_directory_foot(_white(), directory.FOOT_INDEX)
    rows = slice(republish.FOOT_ROWS[0], republish.FOOT_ROWS[1] + 1)
    assert np.array_equal(np.asarray(page)[rows], np.asarray(want)[rows])


def test_republish_picks_the_index_text_for_pages_1_to_3_and_the_plan_text_after(synth):
    root, _, out, _, _, _ = synth
    rows = slice(republish.FOOT_ROWS[0], republish.FOOT_ROWS[1] + 1)
    with zipfile.ZipFile(out) as z:
        idx, plan = republish.FOOT_INDEX_PUBLIE, republish.FOOT_LEFT_PUBLIE                # les textes publies
        for no, own, other in ((1, idx, plan), (3, idx, plan), (4, plan, idx), (47, plan, idx)):
            im = Image.open(io.BytesIO(z.read(f'{FOLDER}heliports/Syria_heliports_directory_p{no:02d}.jpg'))).convert('RGB')
            assert republish.foot_gap(im, republish.redraw_directory_foot(_white(), own)) < 1
            assert republish.foot_gap(im, republish.redraw_directory_foot(_white(), other)) > 3
    assert rows.start == 2336 and rows.stop == 2400


def test_republished_directory_foot_keeps_the_published_text(synth):
    """Lot ac-donnees-jeu : le pied redessine d'une page publiee porte le texte publie (« pydcs »), pas celui du moteur."""
    assert republish.FOOT_LEFT_PUBLIE == 'All plans at the same scale · pads and numbers: DCS (pydcs) · background: F10 map · WGS84'
    assert republish.FOOT_INDEX_PUBLIE == 'Page: page of the PDF collection · position: pydcs airfield · nearest town: towns.json list'
    assert directory.FOOT_LEFT != republish.FOOT_LEFT_PUBLIE and directory.FOOT_INDEX != republish.FOOT_INDEX_PUBLIE
    _, _, out, _, _, _ = synth
    with zipfile.ZipFile(out) as z:
        for no, published, engine in ((1, republish.FOOT_INDEX_PUBLIE, directory.FOOT_INDEX),
                                      (4, republish.FOOT_LEFT_PUBLIE, directory.FOOT_LEFT)):
            im = Image.open(io.BytesIO(z.read(f'{FOLDER}heliports/Syria_heliports_directory_p{no:02d}.jpg'))).convert('RGB')
            assert republish.foot_gap(im, republish.redraw_directory_foot(_white(), published)) < 1
            assert republish.foot_gap(im, republish.redraw_directory_foot(_white(), engine)) > 1


def _with_defect(tmp_path, area_w, area_h):
    """Un zip dont toutes les pages portent deja le pictogramme, sauf la page 10 : un bloc noir de area_w x area_h dans le pied."""
    pages = {}
    def foot(im, no):
        im = new_foot(im, no)
        if no == 10:
            ImageDraw.Draw(im).rectangle([600, 2340, 600 + area_w - 1, 2340 + area_h - 1], fill=(0, 0, 0))
        return im
    return make_zip(tmp_path / "in.zip", pages, foot=foot)


@pytest.mark.parametrize('w, h', [(50, 35), (30, 35), (15, 35)])
def test_a_footer_gap_between_1_and_8_stops_and_writes_nothing(tmp_path, capsys, w, h):
    """Le seuil de 3 ne separe plus une page qui porte le pictogramme de celle qui ne le porte pas : arret."""
    zin = _with_defect(tmp_path, w, h)
    out = tmp_path / 'out.zip'
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(out)]) == 1
    err = capsys.readouterr().err
    assert 'repertoire page 10' in err and 'ecart moyen du pied' in err and not out.exists()


def test_a_footer_gap_below_3_means_the_page_carries_the_pictogram_and_above_8_that_it_does_not(tmp_path, syria_tiles):
    tiles, _ = syria_tiles
    zin = make_zip(tmp_path / 'in.zip', _published_pages(tiles), foot=old_foot)
    res = republish.republish_zip('Syria', zin, tmp_path / 'out.zip')
    assert all(g > 8 for g in res.directory.foot_gaps.values()) and len(res.directory.foot) == 47
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no in SYRIA_PAGES:
            directory.draw_line2(ImageDraw.Draw(pages.setdefault(no, _white())), x, w, base2, town, dims, size)
    zin2 = make_zip(tmp_path / 'in2.zip', pages, foot=new_foot)
    res2 = republish.republish_zip('Syria', zin2, tmp_path / 'out2.zip')
    assert all(g < 1 for g in res2.directory.foot_gaps.values()) and len(res2.directory.foot_kept) == 47 and res2.directory.foot == []
