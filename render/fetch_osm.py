"""Noms de taxiways reels depuis OpenStreetMap (Overpass), pour les charts.

Usage : python -I render/fetch_osm.py <Theatre> [--bases A,B...] [--force] [--config <chemin>]

Pour chaque base capturee (<work_dir>/out/<theatre>/*/base.json) : emprise = zone_captured convertie en lat/lon,
requete Overpass, fichier <osm_dir>/<theatre>/<Base>.json ; bilan dans <osm_dir>/<theatre>/run.json.
Donnees © OpenStreetMap contributors, licence ODbL 1.0.
"""
import argparse
import json
import math
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # python -I n'ajoute pas le dossier du script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'capture'))

SERVERS = ('https://overpass-api.de/api/interpreter',
           'https://overpass.kumi.systems/api/interpreter',
           'https://overpass.private.coffee/api/interpreter')
RETRY_WAITS = (2, 4, 8, 16)                  # secondes avant chaque nouvel essai sur un meme serveur
RETRY_STATUS = (429, 502, 503, 504)
USER_AGENT = 'dcs-airfield-charts'
TIMEOUT_S = 90
QUERY = ('[out:json][timeout:60];(way["aeroway"~"^(taxiway|runway)$"]["ref"]({s},{w},{n},{e});'
         'nwr["aeroway"="aerodrome"]["icao"]({s},{w},{n},{e}););out tags geom;')
SOURCE = {'source': 'OpenStreetMap', 'license': 'ODbL 1.0', 'attribution': '© OpenStreetMap contributors'}


class FetchError(Exception):
    pass


def build_query(s, w, n, e):
    return QUERY.format(s=f'{s:.6f}', w=f'{w:.6f}', n=f'{n:.6f}', e=f'{e:.6f}')


def http_post(url, query, timeout=TIMEOUT_S):
    """Envoie la requete ; retourne (statut, octets). Leve urllib.error.URLError / socket.timeout si pas de reponse."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': query}).encode('utf-8'),
                                 headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, b''


def overpass_fetch(query, servers=SERVERS, post=http_post, sleep=time.sleep, log=print):
    """Reponse JSON (dict) de la requete. Par serveur : essai, puis nouveaux essais apres 2, 4, 8, 16 s sur 429/502/503/504
    ou delai depasse ; puis serveur suivant. Retourne (reponse, serveur)."""
    errors = []
    for url in servers:
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt:
                sleep(RETRY_WAITS[attempt - 1])
            try:
                status, body = post(url, query)
            except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError, OSError) as ex:
                errors.append(f'{url} : {type(ex).__name__}')
                continue
            if status in RETRY_STATUS:
                errors.append(f'{url} : HTTP {status}')
                continue
            if status != 200:
                errors.append(f'{url} : HTTP {status}')
                break                            # erreur definitive pour ce serveur
            try:
                return json.loads(body.decode('utf-8')), url
            except ValueError:
                errors.append(f'{url} : reponse illisible')
                break
    raise FetchError('aucun serveur Overpass n\'a repondu (' + '; '.join(errors[-3:]) + ')')


def _element_center(el):
    if 'lat' in el and 'lon' in el:
        return el['lat'], el['lon']
    pts = el.get('geometry')
    if pts:
        return sum(p['lat'] for p in pts) / len(pts), sum(p['lon'] for p in pts) / len(pts)
    b = el.get('bounds')
    if b:
        return (b['minlat'] + b['maxlat']) / 2, (b['minlon'] + b['maxlon']) / 2
    return None


def _dist_m(a, b):
    k = 111320.0
    return math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * k * math.cos(math.radians(a[0])))


def convert(response, arp_latlon):
    """Reponse Overpass -> (icao, ways) : lignes taxiway/runway portant un `ref`, coordonnees a 6 decimales ; icao du
    tag `icao` de l'aerodrome le plus proche de l'ARP (None s'il n'y en a pas)."""
    ways, best, best_d = [], None, None
    for el in response.get('elements', []):
        tags = el.get('tags') or {}
        if el.get('type') == 'way' and tags.get('aeroway') in ('taxiway', 'runway') and tags.get('ref') and el.get('geometry'):
            ways.append({'aeroway': tags['aeroway'], 'ref': tags['ref'],
                         'pts': [[round(p['lat'], 6), round(p['lon'], 6)] for p in el['geometry']]})
        elif tags.get('aeroway') == 'aerodrome' and tags.get('icao'):
            c = _element_center(el)
            if c is None:
                continue
            d = _dist_m(arp_latlon, c)
            if best_d is None or d < best_d:
                best, best_d = tags['icao'], d
    return best, ways


def dumps(doc):
    """Meme mise en forme que les echantillons : json.dumps(indent=0), UTF-8, sans retour a la ligne final."""
    return json.dumps(doc, indent=0, ensure_ascii=False)


def make_document(response, arp_latlon, fetched=None):
    icao, ways = convert(response, arp_latlon)
    fetched = fetched or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {**SOURCE, 'fetched': fetched, 'icao': icao, 'ways': ways}


def base_bbox_and_arp(theatre, base_json):
    """(s, w, n, e) de zone_captured (4 coins) et (lat, lon) de l'ARP de l'aerodrome pydcs le plus proche."""
    from render import dcsdata, geo
    proj = geo.Projection.for_theatre(theatre)
    zc = base_json['zone_captured']
    corners = [proj.to_latlon(x, z) for x in (zc['x_min'], zc['x_max']) for z in (zc['z_min'], zc['z_max'])]
    lats, lons = [c[0] for c in corners], [c[1] for c in corners]
    bbox = (min(lats), min(lons), max(lats), max(lons))
    cx, cz = base_json['extent_rn4']['center']
    ap = dcsdata.match_airport(dcsdata.load_terrain(theatre), cx, cz)
    arp = proj.to_latlon(ap.position.x, ap.position.y) if ap is not None else ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    return bbox, arp


def list_bases(out_theatre, wanted=None):
    names = sorted(p.parent.name for p in Path(out_theatre).glob('*/base.json'))
    if wanted:
        low = {w.lower() for w in wanted}
        names = [n for n in names if n.lower() in low]
    return names


def run(theatre, work_dir, osm_dir=None, wanted=None, force=False, servers=SERVERS, post=http_post, sleep=time.sleep,
        now=None, log=print):
    """Une passe sur les bases capturees. Retourne le bilan (ecrit aussi dans run.json)."""
    out_theatre = Path(work_dir) / 'out' / theatre.lower()
    osm_theatre = Path(osm_dir if osm_dir else Path(work_dir) / 'osm') / theatre.lower()
    osm_theatre.mkdir(parents=True, exist_ok=True)
    report = {'theatre': theatre, 'started': (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'bases': {}}
    for base in list_bases(out_theatre, wanted):
        target = osm_theatre / f'{base}.json'
        try:
            bj = json.loads((out_theatre / base / 'base.json').read_text(encoding='utf-8'))
            if bj.get('kind') == 'heliport':
                report['bases'][base] = {'status': 'skipped', 'reason': 'heliport'}
                continue
            if target.is_file() and not force:
                report['bases'][base] = {'status': 'skipped', 'reason': 'already fetched'}
                log(f'{base} : deja recupere (--force pour refaire)')
                continue
            bbox, arp = base_bbox_and_arp(theatre, bj)
            response, server = overpass_fetch(build_query(*bbox), servers, post, sleep, log)
            doc = make_document(response, arp, (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'))
            target.write_text(dumps(doc), encoding='utf-8')
            names = sorted({w['ref'] for w in doc['ways'] if w['aeroway'] == 'taxiway'})
            report['bases'][base] = {'status': 'ok', 'server': server, 'icao': doc['icao'], 'ways': len(doc['ways'])}
            log(f'{base} : {len(doc["ways"])} lignes, icao {doc["icao"]}, taxiways {names}')
        except Exception as ex:                 # un echec n'arrete pas la passe
            report['bases'][base] = {'status': 'failed', 'error': f'{type(ex).__name__}: {ex}'}
            log(f'{base} : ECHEC {ex}')
    (osm_theatre / 'run.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    return report


def main(argv):
    import config as capture_config
    cfg, rest = capture_config.load_config(argv)
    ap = argparse.ArgumentParser(prog='fetch_osm.py', description=__doc__.split('\n')[0])
    ap.add_argument('theatre')
    ap.add_argument('--bases', help='noms separes par des virgules (defaut : toutes les bases capturees)')
    ap.add_argument('--force', action='store_true', help='refaire les bases deja recuperees')
    args = ap.parse_args(rest[1:])
    wanted = [b for b in args.bases.split(',') if b] if args.bases else None
    report = run(args.theatre, cfg.work_dir, cfg.osm_dir, wanted, args.force)
    n_fail = sum(1 for v in report['bases'].values() if v['status'] == 'failed')
    print(f'{len(report["bases"])} bases, {n_fail} echecs')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
