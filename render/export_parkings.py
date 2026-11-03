"""Ecrit les places de parking de chaque theatre de DCS (pydcs seul) : `<cle>.json` par theatre et `aircraft.json`.

    python -I render/export_parkings.py --out <dossier>

Format `dcs-parkings-v1` (un theatre) et `dcs-aircraft-sizes-v1` (gabarits des appareils). Sortie deterministe :
une place par ligne, un aerodrome par ligne d'en-tete.
"""
import argparse
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# module pydcs -> cle (valeur `theatre` d'un .miz)
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

FORMAT_THEATRE = 'dcs-parkings-v1'
FORMAT_AIRCRAFT = 'dcs-aircraft-sizes-v1'


class ExportError(Exception):
    """Donnee de pydcs qui contredit le format (arret du cadrage)."""


def pinned_commit(requirements=None):
    """Commit de pydcs lu dans requirements.txt (`pydcs @ git+...@<sha>`)."""
    path = Path(requirements) if requirements else ROOT / 'requirements.txt'
    m = re.search(r'^\s*pydcs\s*@\s*git\+\S+@([0-9a-fA-F]{7,40})\s*$', path.read_text(encoding='utf-8'), re.M)
    if not m:
        raise ExportError(f'{path} : aucune ligne pydcs @ git+...@<sha>')
    return m.group(1)


def source_text(requirements=None):
    return f'pydcs {pinned_commit(requirements)[:7]} (LGPL-3.0): parking stands of the DCS mission editor'


def installed_commit():
    """Commit de pydcs installe (direct_url.json), ou None."""
    from importlib import metadata
    try:
        txt = metadata.distribution('pydcs').read_text('direct_url.json')
    except metadata.PackageNotFoundError:
        return None
    if not txt:
        return None
    return (json.loads(txt).get('vcs_info') or {}).get('commit_id')


def check_installed(requirements=None, installed=None):
    """Arret si le pydcs installe n'est pas au commit de requirements.txt."""
    want = pinned_commit(requirements)
    have = installed_commit() if installed is None else installed
    if not have or have.lower() != want.lower():
        raise ExportError(f'pydcs installe : {have or "commit inconnu"} ; requirements.txt : {want}')


def load_terrain(module):
    import dcs.terrain.terrain as T
    mod = importlib.import_module(f'dcs.terrain.{module}')
    for obj in vars(mod).values():
        if isinstance(obj, type) and issubclass(obj, T.Terrain) and obj is not T.Terrain:
            return obj()
    raise ExportError(f'pydcs : aucun terrain pour {module}')


def _r(v, nd):
    return round(float(v), nd)


def theatre_data(key, terrain, requirements=None):
    """Dictionnaire `dcs-parkings-v1` d'un terrain pydcs."""
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
    return {'format': FORMAT_THEATRE, 'theatre': key, 'source': source_text(requirements), 'airdromes': airdromes}


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


def aircraft_data(requirements=None):
    from dcs.helicopters import helicopter_map
    from dcs.planes import plane_map
    types = {}
    for table in (plane_map, helicopter_map):
        for cls in table.values():
            for f in ('length', 'width', 'height'):
                if getattr(cls, f, None) is None:
                    raise ExportError(f'type {cls.id} : {f} absent')
            types[cls.id] = {
                'heli': bool(cls.helicopter),
                'length': _r(cls.length, 3),
                'width': _r(cls.width, 3),
                'height': _r(cls.height, 3),
            }
    return {'format': FORMAT_AIRCRAFT, 'source': source_text(requirements),
            'aircraft': {k: types[k] for k in sorted(types)}}


def aircraft_text(data):
    out = ['{' + f'"format":{_dumps(data["format"])},"source":{_dumps(data["source"])},"aircraft":{{']
    items = list(data['aircraft'].items())
    for i, (k, v) in enumerate(items):
        out.append(f'{_dumps(k)}:{_dumps(v)}' + (',' if i < len(items) - 1 else ''))
    out.append('}}')
    return '\n'.join(out) + '\n'


def build_all(requirements=None):
    """Calcule les douze textes sans rien ecrire ; retourne ([(nom, texte, ligne de log)])."""
    check_installed(requirements)
    files = []
    for module, key in THEATRES:
        data = theatre_data(key, load_terrain(module), requirements)
        text = theatre_text(data)
        n_places = sum(len(a['parkings']) for a in data['airdromes'])
        files.append((f'{key}.json', text, f'{key}: {len(data["airdromes"])} airdromes, {n_places} stands, '
                                           f'{len(text.encode("utf-8"))} bytes'))
    adata = aircraft_data(requirements)
    atext = aircraft_text(adata)
    files.append(('aircraft.json', atext, f'aircraft.json: {len(adata["aircraft"])} types, '
                                          f'{len(atext.encode("utf-8"))} bytes'))
    return files


def export(out_dir, requirements=None, log=print):
    """Ecrit les douze fichiers dans `out_dir` (rien si un arret) ; retourne {nom de fichier: texte}."""
    files = build_all(requirements)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text, line in files:
        (out / name).write_bytes(text.encode('utf-8'))
        log(line)
    return {n: t for n, t, _ in files}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Ecrit les places de parking de chaque theatre DCS (pydcs seul).')
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
