"""Importe les faits du jeu releves dans l'editeur de mission de DCS par une sonde (bibliotheque standard seulement).

    python -I tools/import_gamedata.py airfields --theatre <Theatre> --out <fichier> <reponses...>
    python -I tools/import_gamedata.py aircraft --out <fichier> <reponse>

Une reponse : une ligne d'en-tete `-- env gui, <date>`, puis la valeur rendue par la sonde, une chaine Lua au format `%q`
(entre guillemets), puis au plus la ligne `-- second retour : true` du crochet ; tout autre texte apres la chaine est
refuse. Ses lignes, une par fait, champs separes par `;`, tables aplaties en `cle.sous-cle=valeur` :
`V;<version de DCS>;<sources...>`, `N;<nombre>`, `A;<id>;<aerodrome>`, `S;<id>;<cle>;<parking>`, `F;<id>;<frequences>`,
`U;<type>;heli=<true|false>;<champs>` (appareils), `E;<quoi>;...` (une erreur de la sonde).

Sorties : `dcs-airfields-v1` (aerodromes, pistes, radios de tour, balises, parkings d'un theatre) et
`dcs-aircraft-game-v1` (gabarits des appareils). Sortie deterministe ; rien n'est ecrit si une reponse est refusee
(`ARRET : <raison>` sur la sortie d'erreur, code 1).
"""
import argparse
import json
import re
import sys
from pathlib import Path

THEATRES = ('GermanyColdWar', 'Syria', 'Caucasus')
FORMAT_AIRFIELDS = 'dcs-airfields-v1'
FORMAT_AIRCRAFT = 'dcs-aircraft-game-v1'
HEADER = '-- env gui, '
TRAILER = b'-- second retour : true'     # ligne du crochet de la sonde apres la chaine : au plus une fois, ou absente

# bandes des radios de tour (Hz) : HF sous 30 MHz, VHF basse sous 88 MHz, VHF haute sous 200 MHz, UHF au-dela
BANDS = ((30_000_000, 'hf_hz'), (88_000_000, 'vhf_low_hz'), (200_000_000, 'vhf_high_hz'), (None, 'uhf_hz'))
ATC_KEYS = ('vhf_low_hz', 'vhf_high_hz', 'uhf_hz', 'hf_hz')
STAND_FIELDS = ('name', 'crossroad_index', 'x', 'y', 'params.FOR_HELICOPTERS', 'params.FOR_AIRPLANES', 'params.LENGTH',
                'params.WIDTH')

_INT = re.compile(r'^-?\d+$')
_FLOAT = re.compile(r'^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$')
_RUNWAY = re.compile(r'^runways\.(\d+)\.(id|name|start|end)$')
_BEACON = re.compile(r'^beacons\.(\d+)\.(beaconId|runwayId|runwaySide)$')
_FREQ = re.compile(r'^frequencyList\.(\d+)\.1$')


class GameDataError(Exception):
    """Reponse refusee (arret : rien n'est ecrit)."""


# ------------------------------------------------------------------ la chaine %q

def unquote(raw, where):
    """Valeur d'une reponse (octets) : la chaine `%q` qui suit la ligne d'en-tete, sans ses echappements (texte UTF-8)."""
    nl = raw.find(b'\n')
    if nl < 0 or not raw[:nl].decode('utf-8', 'replace').startswith(HEADER):
        raise GameDataError(f'{where} : la ligne 1 n\'est pas une en-tete « {HEADER}<date> »')
    body = raw[nl + 1:]
    if not body.startswith(b'"'):
        raise GameDataError(f'{where} : le corps n\'est pas entre guillemets')
    out = bytearray()
    i, n = 1, len(body)
    while True:
        if i >= n:
            raise GameDataError(f'{where} : le corps n\'est pas entre guillemets (guillemet fermant absent)')
        c = body[i]
        if c == 0x22:                                          # "
            break
        if c == 0x0A:
            raise GameDataError(f'{where} : le corps n\'est pas entre guillemets (saut de ligne non echappe)')
        if c != 0x5C:                                          # pas un \
            out.append(c)
            i += 1
            continue
        e = body[i + 1:i + 2]
        if e == b'\n':
            out.append(0x0A)
            i += 2
        elif e in (b'"', b'\\'):
            out += e
            i += 2
        elif e == b'r':
            out.append(0x0D)
            i += 2
        elif e.isdigit():
            m = re.match(rb'\d{1,3}', body[i + 1:i + 4])
            v = int(m.group())
            if v > 255:
                raise GameDataError(f'{where} : echappement inconnu \\{m.group().decode()}')
            out.append(v)
            i += 1 + len(m.group())
        else:
            raise GameDataError(f'{where} : echappement inconnu \\{e.decode("utf-8", "replace")}')
    rest = body[i + 1:]
    if rest not in (b'', b'\n') + tuple(b'\n' + TRAILER + end for end in (b'', b'\n')):
        first = rest.lstrip(b'\n').split(b'\n', 1)[0].decode('utf-8', 'replace')
        raise GameDataError(f'{where} : le corps n\'est pas entre guillemets (texte apres la chaine : « {first} »)')
    try:
        return out.decode('utf-8')
    except UnicodeDecodeError as e:
        raise GameDataError(f'{where} : chaine qui n\'est pas de l\'UTF-8 ({e})')


def read_lines(path):
    """[(ou, ligne)] d'une reponse ; `ou` nomme le fichier et le numero de ligne de la valeur."""
    p = Path(path)
    text = unquote(p.read_bytes(), p.name)
    return [(f'{p.name}, ligne {k} de la valeur', ln) for k, ln in enumerate(text.split('\n'), 1)]


# ------------------------------------------------------------------ les champs

def number(v, where, what):
    if _INT.match(v):
        return int(v)
    if _FLOAT.match(v):
        return float(v)
    raise GameDataError(f'{where} : {what} n\'est pas un nombre ({v!r})')


def integer(v, where, what):
    if not _INT.match(v):
        raise GameDataError(f'{where} : {what} n\'est pas un entier ({v!r})')
    return int(v)


def flat(fields, where):
    """{cle: valeur} des champs aplatis `cle=valeur`."""
    out = {}
    for f in fields:
        k, eq, v = f.partition('=')
        if not eq:
            raise GameDataError(f'{where} : champ sans « = » ({f!r})')
        out[k] = v
    return out


def collect(files, allowed):
    """Lignes des reponses : (V, N, [(type, ou, champs)]) ; arret sur une ligne `E`, un type inconnu, des V ou des N qui
    different, une reponse sans V ou sans N."""
    v_line = n_val = None
    facts = []
    for path in files:
        seen_v = seen_n = False
        for where, ln in read_lines(path):
            fields = ln.split(';')
            kind = fields[0]
            if kind == 'E':
                raise GameDataError(f'{where} : erreur de la sonde ({ln})')
            if kind == 'V':
                if v_line is not None and ln != v_line:
                    raise GameDataError(f'{where} : V differe ({ln!r} ; avant : {v_line!r})')
                v_line, seen_v = ln, True
            elif kind == 'N':
                n = integer(";".join(fields[1:]), where, "N")
                if n_val is not None and n != n_val:
                    raise GameDataError(f'{where} : N differe ({n} ; avant : {n_val})')
                n_val, seen_n = n, True
            elif kind in allowed:
                facts.append((kind, where, fields[1:]))
            else:
                raise GameDataError(f'{where} : ligne d\'un type inconnu ({ln[:60]!r})')
        if not seen_v or not seen_n:
            raise GameDataError(f'{Path(path).name} : ligne {"V" if not seen_v else "N"} absente')
    if v_line is None:
        raise GameDataError('aucune reponse')
    version = v_line.split(';')[1] if len(v_line.split(';')) > 1 else ''
    if not version:
        raise GameDataError(f'V sans version ({v_line!r})')
    return v_line, version, n_val, facts


# ------------------------------------------------------------------ aerodromes

def _runways(a, where):
    found = {}
    for k, v in a.items():
        m = _RUNWAY.match(k)
        if m:
            found.setdefault(int(m.group(1)), {})[m.group(2)] = v
    if sorted(found) != list(range(len(found))):
        raise GameDataError(f'{where} : indices de pistes discontinus ({sorted(found)})')
    out = []
    for i in range(len(found)):
        r = found[i]
        miss = [f for f in ('id', 'name', 'start', 'end') if f not in r]
        if miss:
            raise GameDataError(f'{where} : piste {i} sans {", ".join(miss)}')
        out.append({'id': integer(r['id'], where, f'runways.{i}.id'), 'name': r['name'], 'start': r['start'],
                    'end': r['end']})
    return out


def _beacons(a, where):
    found = {}
    for k, v in a.items():
        m = _BEACON.match(k)
        if m:
            found.setdefault(int(m.group(1)), {})[m.group(2)] = v
    if sorted(found) != list(range(len(found))):
        raise GameDataError(f'{where} : indices de balises discontinus ({sorted(found)})')
    out = []
    for k in range(len(found)):
        b = found[k]
        if 'beaconId' not in b:
            raise GameDataError(f'{where} : balise {k} sans beaconId')
        rid = b.get('runwayId')
        out.append({'id': b['beaconId'], 'runway_id': None if rid is None else integer(rid, where, f'beacons.{k}.runwayId'),
                    'side': b.get('runwaySide')})
    return out


def _atc(f, where):
    """{vhf_low_hz, vhf_high_hz, uhf_hz, hf_hz} par la regle des bandes ; None sans frequencyList."""
    if f is None:
        return None
    freqs = sorted((int(m.group(1)), v) for k, v in f.items() for m in [_FREQ.match(k)] if m)
    if not freqs:
        return None
    atc = {}
    for j, v in freqs:
        hz = number(v, where, f'frequencyList.{j}.1')
        if hz != int(hz):
            raise GameDataError(f'{where} : frequencyList.{j}.1 n\'est pas un nombre entier de Hz ({v!r})')
        hz = int(hz)
        band = next(name for limit, name in BANDS if limit is None or hz < limit)
        if band in atc:
            raise GameDataError(f'{where} : deux frequences dans la bande {band} ({atc[band]} et {hz})')
        atc[band] = hz
    return {k: atc.get(k) for k in ATC_KEYS}


def _stand(s, where):
    miss = [f for f in STAND_FIELDS if f not in s or s[f] == '']
    if miss:
        raise GameDataError(f'{where} : parking sans {", ".join(miss)}')
    flags = []
    for f in ('params.FOR_HELICOPTERS', 'params.FOR_AIRPLANES'):
        if s[f] not in ('0', '1'):
            raise GameDataError(f'{where} : {f} vaut {s[f]!r} (0 ou 1 attendu)')
        flags.append(s[f] == '1')
    h = s.get('params.HEIGHT')
    return {'name': s['name'], 'crossroad_index': integer(s['crossroad_index'], where, 'crossroad_index'),
            'x': number(s['x'], where, 'x'), 'z': number(s['y'], where, 'y'), 'heli': flags[0], 'plane': flags[1],
            'length': number(s['params.LENGTH'], where, 'params.LENGTH'),
            'width': number(s['params.WIDTH'], where, 'params.WIDTH'),
            'height': None if h in (None, '') else number(h, where, 'params.HEIGHT')}


def airfields_data(theatre, files):
    """Dictionnaire `dcs-airfields-v1` des reponses d'un theatre."""
    if theatre not in THEATRES:
        raise GameDataError(f'theatre {theatre!r} hors de {", ".join(THEATRES)}')
    _, version, n, facts = collect(files, ('A', 'S', 'F'))
    heads, stands, freqs = {}, {}, {}
    for kind, where, fields in facts:
        if kind == 'A':
            aid = integer(fields[0] if fields else '', where, 'l\'id de l\'aerodrome')
            if aid in heads:
                raise GameDataError(f'{where} : id {aid} en double')
            a = flat(fields[1:], where)
            for f in ('display_name', 'reference_point.x', 'reference_point.y'):
                if a.get(f, '') == '':
                    raise GameDataError(f'{where} : aerodrome {aid} sans {f}')
            heads[aid] = {'id': aid, 'name': a['display_name'],
                          'x': number(a['reference_point.x'], where, 'reference_point.x'),
                          'z': number(a['reference_point.y'], where, 'reference_point.y'),
                          'runways': _runways(a, where), 'beacons': _beacons(a, where)}
        elif kind == 'S':
            if len(fields) < 2:
                raise GameDataError(f'{where} : ligne S incomplete')
            aid = integer(fields[0], where, 'l\'id de l\'aerodrome')
            stands.setdefault(aid, []).append((where, _stand(flat(fields[2:], where), where)))
        else:
            aid = integer(fields[0] if fields else '', where, 'l\'id de l\'aerodrome')
            if aid in freqs:
                raise GameDataError(f'{where} : frequences de l\'aerodrome {aid} en double')
            freqs[aid] = (where, flat(fields[1:], where))
    if len(heads) != n:
        raise GameDataError(f'{len(heads)} aerodromes A pour N = {n} (une tranche manquante ?)')
    for aid, lst in stands.items():
        if aid not in heads:
            raise GameDataError(f'{lst[0][0]} : parking sans aerodrome A (id {aid})')
    for aid, (where, _) in freqs.items():
        if aid not in heads:
            raise GameDataError(f'{where} : frequences sans aerodrome A (id {aid})')
    airdromes = []
    for aid in sorted(heads):
        h = heads[aid]
        where, f = freqs.get(aid, (None, None))
        st = sorted((s for _, s in stands.get(aid, [])), key=lambda s: s['crossroad_index'])
        airdromes.append({'id': aid, 'name': h['name'], 'x': h['x'], 'z': h['z'], 'atc': _atc(f, where),
                          'runways': h['runways'], 'beacons': h['beacons'], 'stands': st})
    return {'format': FORMAT_AIRFIELDS, 'theatre': theatre, 'dcs_version': version,
            'source': f'DCS World {version} mission editor (Eagle Dynamics): airfields, runways, tower radios, beacons '
                      f'and parking stands; non-commercial use',
            'airdromes': airdromes}


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def airfields_text(data):
    """Une ligne d'en-tete par aerodrome (tout sauf ses parkings), puis un parking par ligne ; relisible par json.load."""
    out = ['{' + ','.join(f'{_dumps(k)}:{_dumps(data[k])}' for k in ('format', 'theatre', 'dcs_version', 'source'))
           + ',"airdromes":[']
    aps = data['airdromes']
    for i, a in enumerate(aps):
        head = {k: v for k, v in a.items() if k != 'stands'}
        out.append(_dumps(head)[:-1] + ',"stands":[')
        st = a['stands']
        for j, s in enumerate(st):
            out.append(_dumps(s) + (',' if j < len(st) - 1 else ''))
        out.append(']}' + (',' if i < len(aps) - 1 else ''))
    out.append(']}')
    return '\n'.join(out) + '\n'


# ------------------------------------------------------------------ appareils

def aircraft_data(files):
    """Dictionnaire `dcs-aircraft-game-v1` d'une reponse (`me_db_api`)."""
    v_line, version, n, facts = collect(files, ('U',))
    if 'me_db_api=absent' in v_line.split(';')[2:]:
        raise GameDataError(f'me_db_api=absent ({v_line})')
    types = {}
    for _, where, fields in facts:
        if len(fields) < 2 or not fields[0]:
            raise GameDataError(f'{where} : ligne U incomplete')
        typ = fields[0]
        if typ in types:
            raise GameDataError(f'{where} : type {typ} en double')
        u = flat(fields[1:], where)
        if u.get('heli') not in ('true', 'false'):
            raise GameDataError(f'{where} : {typ} : heli vaut {u.get("heli")!r} (true ou false attendu)')
        heli = u['heli'] == 'true'
        width = 'rotor_diameter' if heli else 'wing_span'
        vals = {}
        for out_key, key in (('length', 'length'), ('width', width), ('height', 'height')):
            if u.get(key, '') == '':
                raise GameDataError(f'{where} : {typ} sans {key}')
            vals[out_key] = number(u[key], where, f'{typ} {key}')
        types[typ] = {'heli': heli, **vals}
    if len(types) != n:
        raise GameDataError(f'{len(types)} types U pour N = {n} (une tranche manquante ?)')
    return {'format': FORMAT_AIRCRAFT, 'dcs_version': version,
            'source': f'DCS World {version} mission editor (Eagle Dynamics): aircraft sizes; non-commercial use',
            'aircraft': {k: types[k] for k in sorted(types)}}


def aircraft_text(data):
    out = ['{' + ','.join(f'{_dumps(k)}:{_dumps(data[k])}' for k in ('format', 'dcs_version', 'source')) + ',"aircraft":{']
    items = list(data['aircraft'].items())
    for i, (k, v) in enumerate(items):
        out.append(f'{_dumps(k)}:{_dumps(v)}' + (',' if i < len(items) - 1 else ''))
    out.append('}}')
    return '\n'.join(out) + '\n'


# ------------------------------------------------------------------ commande

def main(argv=None):
    ap = argparse.ArgumentParser(description='Importe les faits du jeu releves dans l\'editeur de mission de DCS.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('airfields', help='aerodromes d\'un theatre (dcs-airfields-v1)')
    a.add_argument('--theatre', required=True, help=', '.join(THEATRES))
    a.add_argument('--out', required=True, help='fichier ecrit (dossier cree s\'il manque)')
    a.add_argument('responses', nargs='+', help='reponses de la sonde (les tranches)')
    c = sub.add_parser('aircraft', help='gabarits des appareils (dcs-aircraft-game-v1)')
    c.add_argument('--out', required=True, help='fichier ecrit (dossier cree s\'il manque)')
    c.add_argument('responses', nargs='+', help='reponse de la sonde')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'airfields':
            data = airfields_data(args.theatre, args.responses)
            text = airfields_text(data)
            line = (f'{args.theatre}: {len(data["airdromes"])} airdromes, '
                    f'{sum(len(x["stands"]) for x in data["airdromes"])} stands')
        else:
            data = aircraft_data(args.responses)
            text = aircraft_text(data)
            line = f'aircraft: {len(data["aircraft"])} types'
    except (GameDataError, OSError) as e:
        print(f'ARRET : {e}', file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode('utf-8'))
    print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
