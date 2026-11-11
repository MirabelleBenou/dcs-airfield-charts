"""Panneaux de taxiway : les lettres lues (lecture.json) situees au sol, rattachees a une sortie de piste du graphe, et
donnees au moteur des charts.

Usage : python -I capture/signcapture.py attribuer <Theatre> <Base> [...] [--champ 90]
  lit <work_dir>/panneaux/<Theatre>/<Base>/{lecture.json, index.json, vues.json} et le .rn5 de la base ;
  ecrit attribution.json a cote de lecture.json, et <work_dir>/taxiways_dcs/<theatre en minuscules>/<Base>.json (au format des
  fichiers OSM du moteur, `ways` de `ref` = le nom lu) ; rien d'autre. Aucune image n'est necessaire.

Methode :
  1. chaque lettre lue (car different de `?`, tuile non coupee, vue `lue`) est le rayon de la camera de la vue (pose de
     vues.json, position de index.json, repere du crochet ChartsCaptureHook.lua), coupe par le plan sol + 0,70 m ;
  2. un meme panneau vu de deux vues (deux lettres identiques, rayons qui se coupent au sol devant les deux cameras, hauteurs
     impliquees concordantes) est triangule ;
  3. une lettre est rattachee a la sortie de piste (groupe de
     noeuds de signcapture.bretelles, ou entree non typee a 100 m au moins de toute bretelle) dont le noeud est le plus
     proche, si elle est a 150 m au plus et si la suivante est a 100 m de plus ; une sortie ne recoit un nom que si toutes ses
     lettres sont la meme.
Jamais OSM, ni comme verite, ni comme recours, pour un nom.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I n'ajoute pas le dossier du script
sys.path.insert(0, str(ROOT))                              # ... ni la racine du depot (render.geo)

# ---------- constantes ----------
CHAMP_DEG = 90.0              # champ horizontal de la camera libre (mesure : 90,0 deg)
HAUTEUR_PANNEAU_M = 0.70      # centre de la tuile au-dessus du sol (mesure : 0,61 a 0,82 m)
IMAGE_DEFAUT = (3440, 1440)   # taille de l'image d'une vue quand le fichier de l'image est absent (mesure : toutes les vues)
TRI_HAUTEUR_M = (0.40, 1.10)  # hauteur du panneau acceptee dans chaque vue d'une paire
TRI_ECART_M = 0.25            # ecart maximal entre les deux hauteurs d'une paire
RATTACHE_MAX_M = 150.0        # la sortie la plus proche est a 150 m au plus
RATTACHE_ECART_M = 100.0      # la suivante est a 100 m de plus au moins
SOURCE_PLAN, SOURCE_TRI = 'plan', 'triangulée'
MOTIF_TROP_LOIN, MOTIF_AMBIGUE = 'trop loin', 'ambiguë'
MOTIF_AUCUNE_SORTIE = 'aucune sortie'
SOURCE_IMAGE, SOURCE_DEFAUT = 'image', 'défaut'
MOTIF_NON_SITUEE = 'non située (rayon qui ne descend pas)'
MOTIF_SANS_POSE = 'non située (vue sans position)'
LAT_LON_DECIMALES = 7
TRACE_MAX_M = 300.0           # un `way` du jeu : la marche depuis le noeud de la sortie, jusqu'a 300 m


class AttrError(Exception):
    """Entree illisible ou incoherente (le message nomme le fichier)."""


# ---------- les entrees ----------
def _load_json(path):
    path = Path(path)
    if not path.is_file():
        raise AttrError(f'fichier introuvable : {path}')
    try:
        return json.loads(path.read_bytes().decode('utf-8'))
    except (ValueError, OSError) as e:
        raise AttrError(f'fichier illisible : {path} ({e})')


def tiles_from_lecture(lecture):
    """Les lettres prises dans un lecture.json : les tuiles des vues `lue` dont `car` n'est pas `?` et qui ne sont pas coupees.
    Le drapeau `attente` n'est pas un filtre. Retourne [{'vue', 'x', 'y', 'w', 'h', 'car'}], dans l'ordre du fichier."""
    out = []
    for v in lecture.get('vues', []):
        if v.get('image') != 'lue':
            continue
        for t in v.get('tuiles', []):
            if t.get('car') == '?' or t.get('coupee'):
                continue
            out.append({'vue': v['vue'], 'x': t['x'], 'y': t['y'], 'w': t['w'], 'h': t['h'], 'car': t['car']})
    return out


# ---------- la camera et le rayon ----------
def camera(vue, position):
    """La camera d'une vue : (position (x, y, z), hauteur, avant, haut, droite). Repere de ccApplyCamera (le crochet) :
    avant = (cos t cos c, sin t, cos t sin c), haut = (-sin t cos c, cos t, -sin t sin c), droite = (-sin c, 0, cos c), avec
    c le cap et t le tangage."""
    c, t = math.radians(vue['cap']), math.radians(vue['tangage'])
    cc, sc_, ct, st = math.cos(c), math.sin(c), math.cos(t), math.sin(t)
    avant = (ct * cc, st, ct * sc_)
    haut = (-st * cc, ct, -st * sc_)
    droite = (-sc_, 0.0, cc)
    return tuple(position), float(vue['hauteur']), avant, haut, droite


def ray(cam, tile, size, champ_deg):
    """Le rayon (direction, non normalisee) du centre de la tuile : avant + droite (x - cx) / f + haut (cy - y) / f, avec
    f = (largeur / 2) / tan(champ / 2) et le centre de l'image ((largeur - 1) / 2, (hauteur - 1) / 2)."""
    _, _, avant, haut, droite = cam
    w, h = size
    f = (w / 2.0) / math.tan(math.radians(champ_deg) / 2.0)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    px, py = tile['x'] + tile['w'] / 2.0, tile['y'] + tile['h'] / 2.0
    a, b = (px - cx) / f, (cy - py) / f
    return tuple(avant[i] + droite[i] * a + haut[i] * b for i in range(3))


def ground_point(cam, d, hauteur_panneau=HAUTEUR_PANNEAU_M):
    """Le rayon coupe par le plan sol + hauteur_panneau, le sol etant y de la camera moins sa hauteur : (x, z), ou None si le
    rayon ne descend pas."""
    pos, hauteur, _, _, _ = cam
    if not d[1] < 0:
        return None
    t = (hauteur_panneau - hauteur) / d[1]
    if not t > 0:
        return None
    return (pos[0] + t * d[0], pos[2] + t * d[2])


# ---------- la triangulation ----------
def intersect(cam_a, d_a, cam_b, d_b):
    """Les rayons projetes au sol se coupent-ils devant les deux cameras ? Retourne None, ou
    {'xz', 'hauteurs' (au-dessus du sol, a chaque rayon), 'angle' (entre les rayons projetes, degres)}."""
    pa, pb = cam_a[0], cam_b[0]
    rx, rz, sx, sz = d_a[0], d_a[2], d_b[0], d_b[2]
    det = rx * (-sz) - (-sx) * rz                  # M = [[rx, -sx], [rz, -sz]], M (t, u) = (q - p)
    if abs(det) < 1e-12:
        return None
    qx, qz = pb[0] - pa[0], pb[2] - pa[2]
    t = (qx * (-sz) - (-sx) * qz) / det
    u = (rx * qz - rz * qx) / det
    if not (t > 0 and u > 0):
        return None
    na, nb = math.hypot(rx, rz), math.hypot(sx, sz)
    cosang = max(-1.0, min(1.0, (rx * sx + rz * sz) / (na * nb)))
    return {'xz': (pa[0] + t * rx, pa[2] + t * rz),
            'hauteurs': (cam_a[1] + t * d_a[1], cam_b[1] + u * d_b[1]),
            'angle': math.degrees(math.acos(cosang))}


def retained(inter):
    """Paire retenue : les deux hauteurs dans [0,40 ; 1,10] m et a 0,25 m l'une de l'autre au plus."""
    ha, hb = inter['hauteurs']
    lo, hi = TRI_HAUTEUR_M
    return lo <= ha <= hi and lo <= hb <= hi and abs(ha - hb) <= TRI_ECART_M


def triangulate(letters):
    """Complete les lettres situees (liste de dicts avec 'cam', 'd', 'car', 'vue', 'xz') : deux lettres identiques de deux vues
    differentes, paire retenue -> position triangulee. Une lettre dans plusieurs paires retenues prend celle des rayons les plus
    ouverts (angle le plus proche de 90 deg ; a egalite, la premiere dans l'ordre des lettres). Retourne les paires retenues."""
    best = {}
    pairs = []
    for i, a in enumerate(letters):
        if a['d'] is None:
            continue
        for j in range(i + 1, len(letters)):
            b = letters[j]
            if b['d'] is None or a['car'] != b['car'] or a['vue'] == b['vue']:
                continue
            inter = intersect(a['cam'], a['d'], b['cam'], b['d'])
            if inter is None or not retained(inter):
                continue
            pairs.append((i, j, inter))
            gap = abs(inter['angle'] - 90.0)
            for k in (i, j):
                if k not in best or gap < best[k][0]:
                    best[k] = (gap, i, j, inter)
    for k, (_, i, j, inter) in best.items():
        other = j if k == i else i
        L = letters[k]
        L['xz'], L['source'] = inter['xz'], SOURCE_TRI
        L['hauteurs'] = inter['hauteurs'] if k == i else (inter['hauteurs'][1], inter['hauteurs'][0])
        L['paire'] = other
    return pairs


# ---------- situer ----------
def image_info(base_dir, entry):
    """((largeur, hauteur), source) de l'image d'une vue : l'en-tete du fichier `img` de index.json s'il existe (source `image`),
    sinon IMAGE_DEFAUT (source `défaut`)."""
    img = (entry or {}).get('img')
    if img and (Path(base_dir) / img).is_file():
        try:
            from PIL import Image
            with Image.open(Path(base_dir) / img) as im:
                return tuple(im.size), SOURCE_IMAGE
        except Exception:
            pass
    return IMAGE_DEFAUT, SOURCE_DEFAUT


def image_size(base_dir, entry):
    """Taille (largeur, hauteur) de l'image d'une vue (voir image_info)."""
    return image_info(base_dir, entry)[0]


def place_letters(tiles, vues, positions, champ_deg=CHAMP_DEG, sizes=None, sources=None):
    """Les rayons et la position au sol de chaque lettre (tuiles de tiles_from_lecture), puis la triangulation.
    vues : {numero: vue de vues.json} ; positions : {numero: (x, y, z) ou None} (index.json) ; sizes : {numero: (largeur, hauteur)} ;
    sources : {numero: `image` ou `défaut`} (defaut : `défaut` pour une vue absente de sizes, sinon `image`).
    Retourne (lettres, paires retenues) ; une lettre non situee a xz None et un motif."""
    sizes, sources = sizes or {}, sources or {}
    letters = []
    for t in tiles:
        n = t['vue']
        size = sizes.get(n, IMAGE_DEFAUT)
        L = {'vue': n, 'tuile': t, 'car': t['car'], 'taille': size,
             'taille_source': sources.get(n, SOURCE_IMAGE if n in sizes else SOURCE_DEFAUT), 'cam': None, 'd': None, 'xz': None, 'source': None,
             'hauteurs': None, 'paire': None, 'motif': None}
        pos = positions.get(n)
        if n not in vues:
            raise AttrError(f'vue {n} absente de vues.json')
        if pos is None:
            L['motif'] = MOTIF_SANS_POSE
        else:
            L['cam'] = camera(vues[n], pos)
            L['d'] = ray(L['cam'], t, size, champ_deg)
            L['xz'] = ground_point(L['cam'], L['d'])
            if L['xz'] is None:
                L['d'] = None
                L['motif'] = MOTIF_NON_SITUEE
            else:
                L['source'] = SOURCE_PLAN
        letters.append(L)
    pairs = triangulate(letters)
    return letters, pairs


# ---------- les sorties ----------
def exit_groups(g):
    """Les sorties de l'attribution : les bretelles de signcapture.bretelles(g), dans leur ordre, puis le groupe d'un noeud
    [e] pour chaque entree non typee e de signcapture.untyped_entries(g), dans son ordre, si e est a RATTACHE_ECART_M au moins
    de chacun des noeuds de toutes les bretelles (distance au sol, g.pos) ; sinon e n'est pas une sortie (un bout de bretelle)."""
    import signcapture as sc
    brs = sc.bretelles(g)
    nodes = [n for b in brs for n in b]
    extra = [[e] for e in sc.untyped_entries(g)
             if all(math.dist(g.pos[e], g.pos[n]) >= RATTACHE_ECART_M for n in nodes)]
    return brs + extra


# ---------- rattacher ----------
def exit_distance(group, node_pos, xz):
    """Distance (x, z) de xz au noeud le plus proche du groupe."""
    return min(math.hypot(xz[0] - node_pos[i][0], xz[1] - node_pos[i][1]) for i in group)


def attach(letters, groups, node_pos):
    """Rattache chaque lettre situee a une sortie (groupes de noeuds ; l'identifiant est le plus petit noeud). Remplit
    'sortie', 'distances' ([(identifiant, distance)] des deux plus proches), 'rattachee' et 'motif'."""
    for L in letters:
        L['sortie'], L['distances'], L['rattachee'] = None, [], False
        if L['xz'] is None:
            continue
        if not groups:                                     # une base sans sortie de piste : rien a quoi rattacher
            L['motif'] = MOTIF_AUCUNE_SORTIE
            continue
        ds = sorted(((exit_distance(gr, node_pos, L['xz']), gr[0]) for gr in groups))
        L['distances'] = [(sid, d) for d, sid in ds[:2]]
        first = ds[0][0]
        second = ds[1][0] if len(ds) > 1 else math.inf
        if first > RATTACHE_MAX_M:
            L['motif'] = MOTIF_TROP_LOIN
        elif second - first < RATTACHE_ECART_M:
            L['motif'] = MOTIF_AMBIGUE
        else:
            L['sortie'], L['rattachee'], L['motif'] = ds[0][1], True, None


def name_exits(letters, groups):
    """Une sortie, un nom : [{'id', 'noeuds', 'nom', 'lettres', 'conflit'}] pour chaque sortie du graphe. Toutes les lettres
    rattachees identiques : le nom ; deux lettres differentes : aucun nom (conflit)."""
    out = []
    for gr in groups:
        cars = [L['car'] for L in letters if L['rattachee'] and L['sortie'] == gr[0]]
        distinct = sorted(set(cars))
        out.append({'id': gr[0], 'noeuds': list(gr), 'nom': distinct[0] if len(distinct) == 1 else None,
                    'lettres': cars, 'conflit': len(distinct) > 1})
    return out


# ---------- la sortie ----------
def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def build_attribution(theatre, base, letters, pairs, exits, champ_deg):
    """Le contenu de attribution.json."""
    lettres = []
    for L in letters:
        t = L['tuile']
        rec = {'vue': L['vue'], 'boite': {'x': t['x'], 'y': t['y'], 'w': t['w'], 'h': t['h']}, 'car': L['car'],
               'position': None if L['xz'] is None else {'x': _r(L['xz'][0]), 'z': _r(L['xz'][1])},
               'image': {'largeur': L['taille'][0], 'hauteur': L['taille'][1], 'source': L['taille_source']},
               'source': L['source'],
               'hauteurs': None if L['hauteurs'] is None else [_r(h) for h in L['hauteurs']],
               'paire': None if L['paire'] is None else {'vue': letters[L['paire']]['vue'],
                                                          'boite': {k: letters[L['paire']]['tuile'][k] for k in 'xywh'}},
               'sortie': L['sortie'],
               'distances': [{'sortie': sid, 'm': _r(d, 1)} for sid, d in L['distances']],
               'rattachee': bool(L['rattachee']), 'motif': L['motif']}
        lettres.append(rec)
    comptes = {
        'lettres': len(letters),
        'non_situees': sum(1 for L in letters if L['xz'] is None),
        'paires_triangulees': len(pairs),
        'lettres_triangulees': sum(1 for L in letters if L['source'] == SOURCE_TRI),
        'rattachees': sum(1 for L in letters if L['rattachee']),
        'trop_loin': sum(1 for L in letters if L['motif'] == MOTIF_TROP_LOIN),
        'ambigues': sum(1 for L in letters if L['motif'] == MOTIF_AMBIGUE),
        'sorties': len(exits),
        'sorties_nommees': sum(1 for e in exits if e['nom']),
        'sorties_en_conflit': sum(1 for e in exits if e['conflit']),
    }
    return {'theatre': theatre, 'base': base, 'champ': champ_deg, 'hauteur_panneau': HAUTEUR_PANNEAU_M,
            'comptes': comptes, 'lettres': lettres, 'sorties': exits}


def game_ways(g, groups, exits, proj):
    """Un `way` par sortie nommee, par noeud du groupe et par troncon non-piste qui en part : la marche
    signcapture.walk(g, noeud, troncon) (sans limite d'angle), ses points jusqu'au dernier dont l'abscisse cumulee depuis le noeud
    est de TRACE_MAX_M au plus. {'aeroway': 'taxiway', 'ref': nom, 'pts': [[lat, lon], ...]}, points (x, z) passes en lat/lon."""
    import signcapture as sc
    by_id = {gr[0]: gr for gr in groups}
    ways = []
    for e in exits:
        if not e['nom']:
            continue
        for nid in by_id[e['id']]:
            for sid in g.r.nodes[nid].ports:
                if g.runway[sid]:
                    continue
                w = sc.walk(g, nid, sid, None)
                n = max(i for i, c in enumerate(w.cum) if c <= TRACE_MAX_M) + 1
                pts = []
                for x, z in w.pts[:n]:
                    lat, lon = proj.to_latlon(x, z)
                    pts.append([round(lat, LAT_LON_DECIMALES), round(lon, LAT_LON_DECIMALES)])
                if len(pts) >= 2:
                    ways.append({'aeroway': 'taxiway', 'ref': e['nom'], 'pts': pts})
    return ways


def _dump(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=1) + '\n').encode('utf-8')


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def game_file(cfg, theatre, base):
    """<work_dir>/taxiways_dcs/<theatre en minuscules>/<Base>.json"""
    return Path(cfg.work_dir) / 'taxiways_dcs' / theatre.lower() / f'{base}.json'


def attribute_base(cfg, theatre, base, base_dir, rn5_path, champ_deg=CHAMP_DEG, log=print):
    """L'attribution d'une base : ecrit attribution.json et le fichier du jeu (ou retire celui d'une passe precedente).
    Retourne le contenu de attribution.json."""
    import rn5 as rn5mod
    import signcapture as sc
    lecture = _load_json(base_dir / 'lecture.json')
    index = _load_json(base_dir / 'index.json')
    vdoc = _load_json(base_dir / 'vues.json')
    vues = {v['numero']: v for v in vdoc.get('vues', [])}
    entries = {e['numero']: e for e in index.get('vues', []) if isinstance(e, dict) and 'numero' in e}
    positions = {n: (tuple(e['position']) if e.get('position') else None) for n, e in entries.items()}
    tiles = tiles_from_lecture(lecture)
    infos = {n: image_info(base_dir, entries.get(n)) for n in {t['vue'] for t in tiles}}
    sizes = {n: i[0] for n, i in infos.items()}
    sources = {n: i[1] for n, i in infos.items()}

    g = sc.Graph(rn5mod.read(rn5_path))
    groups = exit_groups(g)
    letters, pairs = place_letters(tiles, vues, positions, champ_deg, sizes, sources)
    attach(letters, groups, g.pos)
    exits = name_exits(letters, groups)
    doc = build_attribution(theatre, base, letters, pairs, exits, champ_deg)
    _write(base_dir / 'attribution.json', _dump(doc))

    named = [e for e in exits if e['nom']]
    target = game_file(cfg, theatre, base)
    if named:
        from render import geo
        ways = game_ways(g, groups, exits, geo.Projection.for_theatre(theatre))
        _write(target, _dump({'source': 'DCS', 'theatre': theatre, 'base': base, 'ways': ways}))
    elif target.is_file():
        target.unlink()
    c = doc['comptes']
    n_defaut = len({L['vue'] for L in letters if L['taille_source'] == SOURCE_DEFAUT})
    log(f'attribuer {theatre}/{base} : {c["lettres"]} lettre(s), {c["rattachees"]} rattachee(s), '
        f'{c["paires_triangulees"]} paire(s) triangulee(s), {len(named)} sortie(s) nommee(s)'
        + (' (' + ', '.join(f'{e["id"]} {e["nom"]}' for e in named) + ')' if named else '')
        + (f', {c["sorties_en_conflit"]} en conflit' if c['sorties_en_conflit'] else '')
        + (f' ; {n_defaut} vue(s) sans image : taille par défaut {IMAGE_DEFAUT[0]}x{IMAGE_DEFAUT[1]}' if n_defaut else ''))
    return doc


def find_base_dir(root, base):
    """Le dossier <root>/<Base> d'une prise de vues (casse ignoree)."""
    root = Path(root)
    if (root / base).is_dir():
        return root / base
    if root.is_dir():
        for q in sorted(root.iterdir()):
            if q.is_dir() and q.name.lower() == base.lower():
                return q
    raise AttrError(f'dossier de prise de vues introuvable : {root / base}')


def cmd_attribuer(cfg, theatre, bases, panneaux_dir, find_base, champ_deg=CHAMP_DEG, log=print):
    """attribuer <Theatre> <Base> [...] [--champ 90] : voir l'en-tete. find_base : celui de signcapture (nom, chemin du .rn5)."""
    for b in bases:
        base_dir = find_base_dir(panneaux_dir(cfg, theatre), b)
        name, rn5_path = find_base(cfg, theatre, base_dir.name)
        attribute_base(cfg, theatre, name, base_dir, rn5_path, champ_deg, log)
