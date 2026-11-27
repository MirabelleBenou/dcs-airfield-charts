"""Ecrit les places de parking des theatres releves dans le jeu : `<cle>.json` par theatre et `aircraft.json`.

    python -I render/export_parkings.py --out <dossier>

Les faits viennent des fichiers du jeu (`render/data/game/`, releves dans l'editeur de mission de DCS). Seuls les theatres
releves (GAME) sont ecrits ; les autres fichiers du dossier de sortie ne sont ni ecrits ni effaces.
Format `dcs-parkings-v1` (un theatre) et `dcs-aircraft-sizes-v1` (gabarits des appareils). Sortie deterministe :
une place par ligne, un aerodrome par ligne d'en-tete.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from render import dcsdata, gamedata  # noqa: E402

# cle courte du theatre (geo.theatre_module) -> cle (valeur `theatre` d'un .miz) : les fichiers de downloads/parkings/
THEATRES = [
    ('caucasus', 'Caucasus'),
    ('falklands', 'Falklands'),
    ('germany', 'GermanyCW'),
    ('kola', 'Kola'),
    ('marianaislands', 'MarianaIslands'),
    ('nevada', 'Nevada'),
    ('normandy', 'Normandy'),
    ('persiangulf', 'PersianGulf'),
    ('sinai', 'SinaiMap'),
    ('syria', 'Syria'),
    ('thechannel', 'TheChannel'),
]
# cle -> theatre releve dans le jeu (render/data/game/<theatre>.json) ; les 8 autres restent tels que pydcs 84f6335 les a
# produits
GAME = {'Caucasus': 'Caucasus', 'GermanyCW': 'GermanyColdWar', 'Syria': 'Syria'}
GAME_AIRCRAFT = gamedata.GAME_DIR / 'aircraft.json'
PYDCS_NOT_INSTALLED = 'pydcs 84f6335 (LGPL-3.0)'

FORMAT_THEATRE = 'dcs-parkings-v1'
FORMAT_AIRCRAFT = 'dcs-aircraft-sizes-v1'


class ExportError(Exception):
    """Donnee du jeu qui contredit le format (arret du cadrage)."""


def source_text(dcs_version):
    return f'DCS World {dcs_version} mission editor (Eagle Dynamics): parking stands; non-commercial use'


def load_terrain(key):
    """Terrain du jeu d'une cle de GAME (GermanyCW, Syria, Caucasus)."""
    if key not in GAME:
        raise ExportError(f'{key} : theatre non releve dans le jeu')
    try:
        return dcsdata.load_terrain(GAME[key])
    except dcsdata.DataError as e:
        raise ExportError(str(e))


def _r(v, nd):
    return round(float(v), nd)


def theatre_data(key, terrain, source=None):
    """Dictionnaire `dcs-parkings-v1` d'un terrain du jeu (`source` : par defaut, celle de sa version de DCS)."""
    seen_ids = set()
    airdromes = []
    for ap in terrain.airports.values():
        aid = ap.id
        if aid in seen_ids:
            raise ExportError(f'{key} : deux aerodromes avec l\'id {aid} ({ap.name})')
        seen_ids.add(aid)
        idx_seen = set()
        parkings = []
        for s in ap.parking_slots:
            where = f'{key}, aerodrome {aid} ({ap.name}), place {s.crossroad_idx!r}'
            if s.crossroad_idx in idx_seen:
                raise ExportError(f'{where} : crossroad_idx en double')
            idx_seen.add(s.crossroad_idx)
            if s.slot_name is None:
                raise ExportError(f'{where} : slot_name absent')
            if s.length is None or s.width is None:
                raise ExportError(f'{where} : length ou width absent')
            parkings.append({
                'parking': s.crossroad_idx,
                'parking_id': str(s.slot_name),
                'x': _r(s.position.x, 2),
                'y': _r(s.position.y, 2),
                'heli': bool(s.helicopter),
                'plane': bool(s.airplanes),
                'length': _r(s.length, 3),
                'width': _r(s.width, 3),
                'height': None if s.height is None else _r(s.height, 3),
            })
        parkings.sort(key=lambda p: p['parking'])
        airdromes.append({
            'airdromeId': aid,
            'name': ap.name,
            'x': _r(ap.position.x, 2),
            'y': _r(ap.position.y, 2),
            'parkings': parkings,
        })
    airdromes.sort(key=lambda a: a['airdromeId'])
    if source is None:
        source = source_text(terrain.dcs_version)
    return {'format': FORMAT_THEATRE, 'theatre': key, 'source': source, 'airdromes': airdromes}


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def theatre_text(data):
    """Une place par ligne, un aerodrome par ligne d'en-tete ; relisible par json.load."""
    out = ['{' + f'"format":{_dumps(data["format"])},"theatre":{_dumps(data["theatre"])},"source":{_dumps(data["source"])},"airdromes":[']
    aps = data['airdromes']
    for i, a in enumerate(aps):
        head = {k: a[k] for k in ('airdromeId', 'name', 'x', 'y')}
        out.append(_dumps(head)[:-1] + ',"parkings":[')
        ps = a['parkings']
        for j, p in enumerate(ps):
            out.append(_dumps(p) + (',' if j < len(ps) - 1 else ''))
        out.append(']}' + (',' if i < len(aps) - 1 else ''))
    out.append(']}')
    return '\n'.join(out) + '\n'


def aircraft_data(present=None, game=None):
    """Dictionnaire `dcs-aircraft-sizes-v1` : les types du jeu (`render/data/game/aircraft.json`), plus les types du
    fichier `present` (le `aircraft.json` deja dans le dossier de sortie, s'il existe) que le jeu n'a pas, tels quels."""
    path = Path(game) if game else GAME_AIRCRAFT
    if not path.is_file():
        raise ExportError(f'{path} absent')
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('format') != 'dcs-aircraft-game-v1':
        raise ExportError(f'{path} : format {doc.get("format")!r}, attendu dcs-aircraft-game-v1')
    types = {}
    for k, v in doc['aircraft'].items():
        for f in ('length', 'width', 'height'):
            if v.get(f) is None:
                raise ExportError(f'type {k} : {f} absent')
        types[k] = {'heli': bool(v['heli']), 'length': _r(v['length'], 3), 'width': _r(v['width'], 3),
                    'height': _r(v['height'], 3)}
    if present is not None and Path(present).is_file():
        old = json.loads(Path(present).read_text(encoding='utf-8'))
        if old.get('format') != FORMAT_AIRCRAFT:
            raise ExportError(f'{present} : format {old.get("format")!r}, attendu {FORMAT_AIRCRAFT}')
        for k, v in old['aircraft'].items():
            if k not in types:
                types[k] = v
    return {'format': FORMAT_AIRCRAFT,
            'source': f'DCS World {doc["dcs_version"]} mission editor (Eagle Dynamics): aircraft sizes; '
                      f'types not installed: {PYDCS_NOT_INSTALLED}',
            'aircraft': {k: types[k] for k in sorted(types)}}


def aircraft_text(data):
    out = ['{' + f'"format":{_dumps(data["format"])},"source":{_dumps(data["source"])},"aircraft":{{']
    items = list(data['aircraft'].items())
    for i, (k, v) in enumerate(items):
        out.append(f'{_dumps(k)}:{_dumps(v)}' + (',' if i < len(items) - 1 else ''))
    out.append('}}')
    return '\n'.join(out) + '\n'


def build_all(out_dir=None):
    """Calcule les quatre textes sans rien ecrire ; retourne [(nom, texte, ligne de log)]. `out_dir` : le dossier de
    sortie, dont le `aircraft.json` present donne les types que le jeu n'a pas."""
    files = []
    for key in sorted(GAME):
        data = theatre_data(key, load_terrain(key))
        text = theatre_text(data)
        n_places = sum(len(a['parkings']) for a in data['airdromes'])
        files.append((f'{key}.json', text, f'{key}: {len(data["airdromes"])} airdromes, {n_places} stands, '
                                           f'{len(text.encode("utf-8"))} bytes'))
    adata = aircraft_data(None if out_dir is None else Path(out_dir) / 'aircraft.json')
    atext = aircraft_text(adata)
    files.append(('aircraft.json', atext, f'aircraft.json: {len(adata["aircraft"])} types, '
                                          f'{len(atext.encode("utf-8"))} bytes'))
    return files


def export(out_dir, log=print):
    """Ecrit les quatre fichiers dans `out_dir` (rien si un arret ; les autres fichiers du dossier restent) ; retourne
    {nom de fichier: texte}."""
    files = build_all(out_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text, line in files:
        (out / name).write_bytes(text.encode('utf-8'))
        log(line)
    return {n: t for n, t, _ in files}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Ecrit les places de parking des theatres DCS releves dans le jeu.')
    ap.add_argument('--out', required=True, help='dossier de sortie (cree s\'il manque)')
    args = ap.parse_args(argv)
    try:
        export(args.out)
    except ExportError as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
