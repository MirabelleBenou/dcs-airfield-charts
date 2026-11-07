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

# Syria : les sept vignettes dont la ville, a 36 px, touchait les dimensions ou en etait a moins de 24 px
SYRIA_SMALL = {'HS03': 33, 'HS07': 33, 'HS12': 35, 'HS13': 35.5, 'HS17': 34, 'HT02': 32, 'H_med_orig_09': 32}
SYRIA_PAGES = [30, 31, 32, 33, 43, 46]
FOLDER = 'DCS_Airfield_Charts_Syria_1985_helicopters/'


# ---------- la taille : une seule fonction ----------
def gap(town, dims, w, size):
    """Blanc (px) entre la fin de la ville a `size` et le debut des dimensions."""
    return (w - directory.PLAN_DX - directory.font('regular', 36).getlength(dims)) \
        - (directory.PLAN_DX + directory.font('regular', size).getlength(town))


@pytest.mark.parametrize('town', ['0,5 km NW de Al Safira Military Base', '6,5 km NE de Chemical Weapons Storage Area',
                                  '0,8 km S de City Training and Research Hospital', '0,7 km SW de Army Vehicle Training Ground'])
def test_town_size_is_the_largest_step_leaving_24_px(town):
    w, dims = directory.TILE_W, 'AIRES 42×34 M'
    size = directory.town_size(town, dims, w)
    assert 0 < size <= 36 and size * 2 == int(size * 2)               # pas de 0,5 px
    assert gap(town, dims, w, size) >= 24
    if size < 36:
        assert gap(town, dims, w, size + 0.5) < 24                    # la taille au-dessus ne laisse pas 24 px


def test_town_that_fits_stays_at_36():
    size = directory.town_size('0,5 km NW de Al Safira Military Base', 'AIRES 42×34 M', directory.TILE_W)
    assert size == 36 and isinstance(size, int)
    assert directory.font('regular', 36.0) is directory.font('regular', 36)      # 36 et 36.0 : la meme police


def test_town_size_wider_tile_gives_more_room():
    town, dims = '0,8 km S de City Training and Research Hospital', 'AIRES 42×34 M'
    wide = 2 * directory.TILE_W + directory.TILE_GAP
    assert directory.town_size(town, dims, directory.TILE_W) < 36 == directory.town_size(town, dims, wide)


def test_town_size_stops_when_nothing_fits():
    with pytest.raises(directory.DirectoryError):
        directory.town_size('0,5 km N de Nazareth', 'AIRES 42×34 M', 100)


# ---------- le rejeu de Syria et de Germany ----------
@pytest.fixture(scope='module')
def syria_tiles():
    return republish.directory_tiles('Syria')


def test_syria_replay_gives_the_seven_sites(syria_tiles):
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
    assert towns['HS03'] == '6,5 km NE de Chemical Weapons Storage Area'
    assert towns['HT02'] == '0,8 km S de City Training and Research Hospital'


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
    site.town = '6,5 km NE de Chemical Weapons Storage Area'
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
    town = '0,8 km S de City Training and Research Hospital'
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
    assert size == 32
    assert np.array_equal(band, republish.line_band(x, w, base2, town, dims, size))


# ---------- republish : un zip synthetique aux noms du repertoire publie ----------
def _published_pages(tiles):
    """{numero de page: image} des pages touchees, telles que publiees : chaque vignette a 36 px."""
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


def make_zip(path, pages, n=47, skip_jpeg=None, extra=()):
    """Zip : `n` JPEG p01..pNN (les pages sans vignette a retoucher : octets quelconques, jamais decodes) et le PDF (n pages)."""
    imgs = []
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(FOLDER + 'notes.txt', b'unchanged')
        for k in range(1, n + 1):
            if k in pages:
                data, im = _jpeg(pages[k]), pages[k]
            else:
                im = Image.new('RGB', (40, 50), (255, 255, 255 - k))
                data = b'\xff\xd8not-a-real-page-%02d' % k
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
    assert (d.tiles, d.controlled, d.retouched, d.already, d.pages) == (141, 7, 7, 0, SYRIA_PAGES)
    assert {r[1]: r[3] for r in d.rows} == SYRIA_SMALL
    assert all(r[4] <= 6 < r[5] for r in d.rows)


def test_republish_directory_entries(synth):
    _, zin, out, _, pages, tiles = synth
    with zipfile.ZipFile(zin) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        assert sorted(changed) == sorted([f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg' for k in SYRIA_PAGES]
                                         + [FOLDER + 'Syria_heliports_directory.pdf'])
        for k in SYRIA_PAGES:
            name = f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg'
            old, new = Image.open(io.BytesIO(a.read(name))), Image.open(io.BytesIO(b.read(name)))
            assert new.quantization == old.quantization
            assert JpegImagePlugin.get_sampling(new) == JpegImagePlugin.get_sampling(old) and new.info['dpi'] == (300, 300)
            o, n = np.asarray(old.convert('RGB'), dtype=np.int16), np.asarray(new.convert('RGB'), dtype=np.int16)
            mask = np.ones(o.shape[:2], bool)
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
            if k in SYRIA_PAGES:
                assert ib[k - 1] == b.read(f'{FOLDER}heliports/Syria_heliports_directory_p{k:02d}.jpg')     # les octets du nouveau JPEG
                assert ib[k - 1] != ia[k - 1]
            else:
                assert ib[k - 1] == ia[k - 1]                                                                # a l'octet
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
    assert (d.controlled, d.retouched, d.already, d.pages) == (7, 0, 7, [])
    with zipfile.ZipFile(out) as a, zipfile.ZipFile(again) as b:
        assert a.read(FOLDER + 'Syria_heliports_directory.pdf') == b.read(FOLDER + 'Syria_heliports_directory.pdf')
        assert all(a.read(n) == b.read(n) for n in a.namelist())


def test_republish_summary_line(synth, capsys):
    root, zin, _, _, _, _ = synth
    assert republish.main(['Syria', '--zip', str(zin), '--out', str(root / 'summary.zip')]) == 0
    out = capsys.readouterr().out
    assert ('repertoire des heliports : 141 vignettes ; controlees (ville sous 36 px) : 7 ; retouchees : 7 ; '
            'deja retouchees : 0 ; pages touchees : 6 p30 p31 p32 p33 p43 p46') in out


def test_republish_untouched_directory_is_copied_to_the_byte(tmp_path, syria_tiles):
    """Aucune ville sous 36 px dans le zip (toutes les vignettes sont deja retouchees ... ou rien a retoucher) : rien ne change."""
    tiles, _ = syria_tiles
    pages = {}
    for no, site, x, w, base2, town, dims, size in tiles:
        if no in SYRIA_PAGES:
            im = pages.setdefault(no, Image.new('RGB', directory.PAGE_PX, (255, 255, 255)))
            directory.draw_line2(ImageDraw.Draw(im), x, w, base2, town, dims, size)
    zin = make_zip(tmp_path / 'in.zip', pages)
    out = tmp_path / 'out.zip'
    res = republish.republish_zip('Syria', zin, out)
    assert (res.directory.retouched, res.directory.already, res.directory.pages) == (0, 7, [])
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
