"""Ecrit les codes OACI et les balises radio des aerodromes de chaque theatre de DCS : `<cle>.json` par theatre.

    python -I render/export_navaids.py --ourairports <airports.csv> --beacons <dossier> --out <dossier>

Format `dcs-navaids-v1`. Les aerodromes viennent de pydcs, le code OACI d'OurAirports (par la position), les balises du
`beacons.lua` du jeu (`<dossier>` est organise comme `Mods/terrains` de DCS). Sortie deterministe : un aerodrome par
ligne, une balise de route par ligne.
"""
import argparse
import bisect
import csv
import hashlib
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from render import dcsdata  # noqa: E402
from render.export_parkings import (THEATRES, ExportError, _dumps, check_installed, load_terrain,  # noqa: E402
                                    pinned_commit)

FORMAT = 'dcs-navaids-v1'
OURAIRPORTS_COMMIT = '64af12951161ed52bc733eb4101224643c48850a'
OURAIRPORTS_SHA256 = '5754df21f25cb08d2def8a80b3eb609ddc52a4217afa8eb48280b6428c06e78f'
EARTH_RADIUS_KM = 6371.0088
MATCH_MAX_KM = 3.0
GAME_FOLDER = {'GermanyCW': 'GermanyColdWar'}    # cle -> dossier du jeu, quand elles different

_AIRFIELD_ID = re.compile(r'^airfield(\d+)_(\d+)$')
_WORLD_ID = re.compile(r'^world_(\d+)$')
_ICAO = re.compile(r'^[A-Z]{4}$')


# ---------------------------------------------------------------- OurAirports

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def check_ourairports(path, expected=OURAIRPORTS_SHA256):
    """Refuse un airports.csv dont le sha256 differe de celui du commit fige."""
    have = sha256_file(path)
    if have != expected:
        raise ExportError(f'{path} : sha256 {have}, attendu {expected} (OurAirports {OURAIRPORTS_COMMIT[:7]})')


def row_code(row):
    """Code d'une ligne d'OurAirports : icao_code, sinon gps_code puis ident s'ils font 4 lettres majuscules, sinon None."""
    icao = (row.get('icao_code') or '').strip()
    if icao:
        return icao
    for f in ('gps_code', 'ident'):
        v = (row.get(f) or '').strip()
        if _ICAO.match(v):
            return v
    return None


def read_ourairports(path):
    """Lignes d'OurAirports : liste de (id, lat, lon, code), toutes les lignes ayant une position."""
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row['latitude_deg']), float(row['longitude_deg'])
            except (TypeError, ValueError):
                continue
            out.append((row['id'], lat, lon, row_code(row)))
    return out


def great_circle_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


class RowIndex:
    """Les lignes d'OurAirports triees par latitude, pour ne mesurer que celles qui sont a portee."""

    def __init__(self, rows):
        self.rows = sorted(enumerate(rows), key=lambda t: t[1][1])
        self.lats = [t[1][1] for t in self.rows]
        self.dlat = math.degrees(MATCH_MAX_KM / EARTH_RADIUS_KM) * 1.01

    def nearest(self, lat, lon):
        """(distance km, ordre dans le fichier, ligne) de la ligne la plus proche, ou None."""
        lo = bisect.bisect_left(self.lats, lat - self.dlat)
        hi = bisect.bisect_right(self.lats, lat + self.dlat)
        best = None
        for order, row in self.rows[lo:hi]:
            key = (great_circle_km(lat, lon, row[1], row[2]), order)
            if best is None or key < best[:2]:
                best = key + (row,)
        return best


def match_icao(airdromes, rows):
    """Code OACI de chaque aerodrome. `airdromes` : [(airdromeId, lat, lon)] ; `rows` : [(id, lat, lon, code)].
    Retourne {airdromeId: code ou None}. Regles du cadrage 2.2."""
    index = RowIndex(rows)
    claims = {}                                   # id de ligne -> [(distance, airdromeId, ligne)]
    for aid, lat, lon in airdromes:
        best = index.nearest(lat, lon)
        if best is not None and best[0] <= MATCH_MAX_KM:
            claims.setdefault(best[2][0], []).append((best[0], aid, best[2]))
    codes = {aid: None for aid, _, _ in airdromes}
    for cl in claims.values():
        _, aid, row = min(cl, key=lambda c: (c[0], c[1]))
        codes[aid] = row[3]
    return codes


# ------------------------------------------------------------------- balises

def find_beacons_file(beacons_dir, key):
    """`<dossier du jeu>/beacons.lua`, nom sans egard a la casse ; None si le dossier ou le fichier manque."""
    folder = Path(beacons_dir) / GAME_FOLDER.get(key, key)
    if not folder.is_dir():
        return None
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name.lower() == 'beacons.lua':
            return p
    return None


def _freq(b):
    f = round(b.frequency)
    return int(f) if f else None


def beacons_data(key, terrain, beacons_file):
    """({airdromeId: [balises]}, [balises de route]) ; (None, None) sans fichier."""
    if beacons_file is None:
        return None, None
    parsed = dcsdata.parse_beacons_lua(beacons_file)
    ids = {ap.id for ap in terrain.airports.values()}
    # piste de chaque balise : nom du bout de piste qui la cite
    ends = {}
    for ap in terrain.airports.values():
        for rw in ap.runways:
            for app in (rw.main, rw.opposite):
                for rb in app.beacons:
                    if rb.id in ends and ends[rb.id][1] != app.name:
                        raise ExportError(f'{key} : la balise {rb.id} est citee a deux bouts de piste '
                                          f'({ends[rb.id][1]} et {app.name})')
                    ends[rb.id] = (ap.id, app.name)
    per_airfield = {aid: [] for aid in ids}
    enroute = []
    for bid, b in parsed.items():
        m = _AIRFIELD_ID.match(bid)
        if m:
            aid, n = int(m.group(1)), int(m.group(2))
            if aid not in ids:
                raise ExportError(f'{key} : la balise {bid} n\'a pas d\'aerodrome (airdromeId {aid} absent de pydcs)')
            runway = ends[bid][1] if bid in ends and ends[bid][0] == aid else None
            per_airfield[aid].append((n, {'type': b.type, 'callsign': b.callsign, 'frequency': _freq(b),
                                          'channel': b.channel, 'runway': runway}))
            continue
        m = _WORLD_ID.match(bid)
        if m:
            ll = dcsdata_point(b.x, b.z, terrain).latlng()
            enroute.append((int(m.group(1)), {'type': b.type, 'callsign': b.callsign, 'frequency': _freq(b),
                                              'channel': b.channel, 'lat': round(ll.lat, 6), 'lon': round(ll.lng, 6)}))
    return ({aid: [d for _, d in sorted(v, key=lambda t: t[0])] for aid, v in per_airfield.items()},
            [d for _, d in sorted(enroute, key=lambda t: t[0])])


def dcsdata_point(x, z, terrain):
    from dcs.mapping import Point
    return Point(x, z, terrain)


# ------------------------------------------------------------------- fichier

def theatre_data(key, terrain, rows_index_codes, beacons_file, requirements=None):
    """Dictionnaire `dcs-navaids-v1` d'un terrain pydcs. `rows_index_codes` : {airdromeId: code ou None}."""
    per_airfield, enroute = beacons_data(key, terrain, beacons_file)
    airdromes = []
    seen = set()
    for ap in terrain.airports.values():
        if ap.id in seen:
            raise ExportError(f'{key} : deux aerodromes avec l\'id {ap.id} ({ap.name})')
        seen.add(ap.id)
        airdromes.append({'airdromeId': ap.id, 'name': ap.name, 'icao': rows_index_codes.get(ap.id),
                          'beacons': None if per_airfield is None else per_airfield[ap.id]})
    airdromes.sort(key=lambda a: a['airdromeId'])
    folder = GAME_FOLDER.get(key, key)
    return {
        'format': FORMAT,
        'theatre': key,
        'source': {
            'airfields': f'pydcs {pinned_commit(requirements)[:7]} (LGPL-3.0): airfield names and positions',
            'icao': f"OurAirports {OURAIRPORTS_COMMIT[:7]} (public domain): today's ICAO codes, "
                    f'matched by position within 3 km',
            'beacons': None if beacons_file is None else f'DCS game files: Mods/terrains/{folder}/beacons.lua',
        },
        'airdromes': airdromes,
        'enroute': enroute,
    }


def theatre_text(data):
    """Un aerodrome par ligne, une balise de route par ligne ; relisible par json.load."""
    out = ['{' + f'"format":{_dumps(data["format"])},"theatre":{_dumps(data["theatre"])},'
                 f'"source":{_dumps(data["source"])},"airdromes":[']
    aps = data['airdromes']
    for i, a in enumerate(aps):
        out.append(_dumps(a) + (',' if i < len(aps) - 1 else ''))
    if data['enroute'] is None:
        out.append('],"enroute":null}')
    else:
        out.append('],"enroute":[')
        er = data['enroute']
        for i, b in enumerate(er):
            out.append(_dumps(b) + (',' if i < len(er) - 1 else ''))
        out.append(']}')
    return '\n'.join(out) + '\n'


def build_all(ourairports, beacons_dir, requirements=None):
    """Calcule les onze textes sans rien ecrire ; retourne [(nom, texte, ligne de log)]."""
    check_installed(requirements)
    check_ourairports(ourairports)
    rows = read_ourairports(ourairports)
    files = []
    for module, key in THEATRES:
        terrain = load_terrain(module)
        pts = []
        for ap in terrain.airports.values():
            ll = ap.position.latlng()
            pts.append((ap.id, ll.lat, ll.lng))
        codes = match_icao(pts, rows)
        data = theatre_data(key, terrain, codes, find_beacons_file(beacons_dir, key), requirements)
        text = theatre_text(data)
        n_icao = sum(1 for a in data['airdromes'] if a['icao'])
        if data['enroute'] is None:
            n_af, n_er = '-', '-'
        else:
            n_af, n_er = sum(len(a['beacons']) for a in data['airdromes']), len(data['enroute'])
        files.append((f'{key}.json', text, f'{key}: {len(data["airdromes"])} airdromes, {n_icao} with ICAO, '
                                           f'{n_af} airfield beacons, {n_er} en-route beacons, '
                                           f'{len(text.encode("utf-8"))} bytes'))
    return files


def export(ourairports, beacons_dir, out_dir, requirements=None, log=print):
    """Ecrit les onze fichiers dans `out_dir` (rien si un arret) ; retourne {nom de fichier: texte}."""
    files = build_all(ourairports, beacons_dir, requirements)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text, line in files:
        (out / name).write_bytes(text.encode('utf-8'))
        log(line)
    return {n: t for n, t, _ in files}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Ecrit les codes OACI et les balises des aerodromes de chaque theatre DCS.')
    ap.add_argument('--ourairports', required=True, help='airports.csv d\'OurAirports (au commit fige)')
    ap.add_argument('--beacons', required=True, help='dossier organise comme Mods/terrains de DCS')
    ap.add_argument('--out', required=True, help='dossier de sortie (cree s\'il manque)')
    args = ap.parse_args(argv)
    try:
        export(args.ourairports, args.beacons, args.out)
    except ExportError as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
