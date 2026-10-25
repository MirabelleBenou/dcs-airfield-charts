"""Fixtures communes. Les tests qui ont besoin des echantillons du lot (mosaiques, fichiers de DCS) s'executent quand la
variable d'environnement DCS_CHARTS_SAMPLES designe le dossier `echantillons/` ; sinon ils sont ignores (aucun echantillon
n'est dans ce depot)."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / 'capture'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

ENV = 'DCS_CHARTS_SAMPLES'


@pytest.fixture(scope='session')
def samples():
    v = os.environ.get(ENV)
    if not v or not (Path(v) / 'syria').is_dir():
        pytest.skip(f'echantillons absents (variable {ENV})')
    return Path(v)


def _link(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src, dst, target_is_directory=True)
    except OSError:
        shutil.copytree(src, dst)


@pytest.fixture(scope='session')
def work(samples, tmp_path_factory):
    """Dossier de travail : out/syria et osm/syria (les echantillons), dcs_install des echantillons. Retourne la Config."""
    import config
    w = tmp_path_factory.mktemp('work')
    _link(samples / 'syria', w / 'out' / 'syria')
    _link(samples / 'syria' / 'osm', w / 'osm' / 'syria')
    cfg_path = w / 'config.json'
    cfg_path.write_text(json.dumps({'work_dir': str(w), 'dcs_install': str(samples / 'dcs_install')}), encoding='utf-8')
    cfg, _ = config.load_config(['x', '--config', str(cfg_path)])
    return cfg


@pytest.fixture(scope='session')
def charts(work):
    """Analyse et pages (SVG) des trois bases, 1985 : {base: (ChartData, [PageOut])}."""
    from render import analysis, compose
    out = {}
    for base in ('Konya', 'Adana', 'Ramat_David'):
        data = analysis.analyze(work, 'Syria', base, 1985, log=lambda *a: None)
        out[base] = (data, compose.build_pages(data, log=lambda *a: None))
    return out
