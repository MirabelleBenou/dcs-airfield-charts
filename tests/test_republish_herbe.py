"""Lot terrains-herbe-germany : republish.py accepte la piste en herbe (calque Runways).
Tests sur la base Syria/Konya des echantillons (fixture `work`, DCS_CHARTS_SAMPLES = `echantillons/` du lot) dont les pistes sont
passees en `'herbe'` avant le rendu ; sans les echantillons, ils sont ignores. Le dernier test lit la base Bienenfarm de
`<echantillons>/germanycoldwar` (captures F10 du lot) et est ignore si elle n'y est pas."""
import re
import shutil

import pytest

from render import analysis, compose, republish


def _page(data, grass):
    for r in data.runways:
        r.surface = 'herbe' if grass else 'dur'
    return next(p for p in compose.build_pages(data, log=lambda *a: None) if p.id == '10-9').svg


@pytest.fixture(scope='module')
def konya(work):
    return analysis.analyze(work, 'Syria', 'Konya', 1985, log=lambda *a: None)


def test_retouch_labels_accepts_a_grass_page(konya):
    svg = _page(konya, True)
    assert re.search(r'<polygon points="[^"]+" fill="#FFFFFF" stroke="#1A1A1A" stroke-width="[-\d.]+"/>', svg)
    new, moves, unplaced = republish.retouch_labels(svg, 'X_10-9')
    assert new.count('fill="#FFFFFF" stroke="#1A1A1A"') == svg.count('fill="#FFFFFF" stroke="#1A1A1A"')     # la piste n'est pas touchee


def test_retouch_labels_hard_page_is_unchanged(konya):
    svg = _page(konya, False)
    new, moves, unplaced = republish.retouch_labels(svg, 'X_10-9')
    assert new == svg and moves == [] and unplaced == []


def test_retouch_north_and_caps_accepts_a_grass_page(konya):
    svg = _page(konya, True)
    new, details = republish.retouch_north_and_caps(svg, 'X_10-9')
    assert 'PISTE EN HERBE' in new


def test_retouch_labels_still_refuses_any_other_runway_shape(konya):
    svg = _page(konya, True)
    bad = re.sub(r'(<polygon points="[^"]+" fill="#FFFFFF" stroke="#1A1A1A" stroke-width=")[-\d.]+"/>', r'\g<1>x"/>', svg, count=1)
    assert bad != svg
    with pytest.raises(republish.RepublishError, match='element inattendu dans le calque Runways'):
        republish.retouch_labels(bad, 'X_10-9')
    bad = svg.replace('fill="#FFFFFF" stroke="#1A1A1A"', 'fill="#FF0000" stroke="#1A1A1A"', 1)
    with pytest.raises(republish.RepublishError, match='element inattendu dans le calque Runways'):
        republish.retouch_labels(bad, 'X_10-9')


def test_retouch_labels_on_the_bienenfarm_grass_page(samples, tmp_path):
    src = samples / 'germanycoldwar' / 'Bienenfarm'
    if not src.is_dir():
        pytest.skip('captures de Bienenfarm absentes des echantillons')
    import config
    import json
    w = tmp_path / 'w'
    shutil.copytree(src, w / 'out' / 'germanycoldwar' / 'Bienenfarm')
    shutil.copytree(samples / 'dcs_install', w / 'dcs_install')
    (w / 'dcs_install' / 'Mods' / 'terrains' / 'GermanyColdWar' / 'beacons.lua').write_text('beacons = {\n}\n', encoding='utf-8')
    (w / 'config.json').write_text(json.dumps({'work_dir': str(w), 'dcs_install': str(w / 'dcs_install')}), encoding='utf-8')
    cfg, _ = config.load_config(['x', '--config', str(w / 'config.json')])
    data = analysis.analyze(cfg, 'GermanyColdWar', 'Bienenfarm', 1985, log=lambda *a: None)
    assert data.runways[0].surface == 'herbe'
    svg = next(p for p in compose.build_pages(data, log=lambda *a: None) if p.id == '10-9').svg
    new, moves, unplaced = republish.retouch_labels(svg, 'Bienenfarm_10-9')
    assert 'fill="#FFFFFF" stroke="#1A1A1A"' in new
