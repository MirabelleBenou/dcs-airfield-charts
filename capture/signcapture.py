"""Panneaux de taxiway : la visee (sans DCS) et la prise de vues (DCS, Windows).

Usage (python -I capture/signcapture.py ...) :
  visee   <Theatre> [<Base> ...]   -> <work_dir>/panneaux/<Theatre>/<Base>/vues.json et <work_dir>/panneaux/<Theatre>/visee.json
  capture <Theatre> <Base> [...]   -> <work_dir>/panneaux/<Theatre>/<Base>/{img/,vignettes/,index.json}
  lire    <Theatre> <Base> [...]   -> <work_dir>/panneaux/<Theatre>/<Base>/{lecture.json,lecture.tsv} (capture/signread.py)
Option : --config <chemin> (sinon variable DCS_CHARTS_CONFIG, sinon capture/config.json ; voir config.example.json)

La visee lit le graphe des voies du .rn5 de chaque base (rn5.py) et planifie ou regarder pour voir les panneaux : sorties de
piste (bretelles) et intersections de taxiways. La prise de vues les realise dans DCS, camera libre (vue F11), DCS au premier
plan, avec le crochet de ce depot (commande `cam`). La lecture des panneaux est dans signread.py (commande `lire`) ; rien n'est ecrit hors de
<work_dir>/panneaux/.
"""
import hashlib
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I n'ajoute pas le dossier du script
import rn5 as rn5mod

# ---------- constantes de la visee ----------
WALK_MAX_M = 400.0            # on n'entre plus dans un troncon des 400 m cumules atteints
ANGLE_TAXIWAY_DEG = 30.0      # branche d'intersection de taxiways
BRETELLE_GROUP_M = 150.0      # deux noeuds a moins de 150 m : meme bretelle
HOLD_MAX_M = 200.0            # point d'attente retenu s'il est a moins de 200 m
FAR_AXIS_M = 70.0             # premier point de la marche a 70 m ou plus de l'axe de piste
S_MAX_M = 90.0                # abscisse visee plafonnee
STEP_M = 2.0                  # pas de recherche de la camera
CAM_FAR_M = 60.0              # camera a 60 m ou plus du point vise
CAM_NEAR_M = 40.0             # a defaut, 40 m ou plus
FROM_RUNWAY_MIN_M = 40.0      # vue depuis la piste : point vise a au moins 40 m de l'axe
FROM_RUNWAY_MAX_M = 70.0      # au-dela, camera ramenee a 60 m
ENTRY_MAX_AXIS_M = 30.0       # entree non typee : noeud taxiway_end a moins de 30 m de l'axe de piste
ENTRY_TYPE = 'taxiway_end'
STAND_BRANCH_M = 150.0        # bretelle de poste : somme des troncons entiers jusqu'a un poste, moins de 150 m
STAND_BRANCH_MAX_SEGS = 20
INTERSECTION_MIN_BRANCHES = 3
INTERSECTION_MIN_WALK_M = 40.0
INTERSECTION_CAM_M = 40.0     # camera au point de la marche a 40 m, point vise = le noeud d'intersection
CAM_HEIGHT_M = 3.0
AIM_HEIGHT_M = 1.5
DEDUP_DIST_M = 15.0
DEDUP_HEADING_DEG = 20.0
NEAR_NODE_M = 130.0           # vue "piste" comptee pour une bretelle si son point vise est a moins de 130 m d'un de ses noeuds
SECONDS_PER_VIEW = 4.5
MIN_RN5_BYTES = 5000

PASS_PREFIXES = ('taxiway_l', 'taxiway_l_mirror', 'auto__', 'taxi_point_holding_position')
HOLD_TYPE = 'taxi_point_holding_position'
INTERSECTION_TYPES = ('taxiway_t', 'taxiway_cross', 'taxiway_x')
SENS_ENTRY_TO = 'vers la piste (entrée non typée)'
SENS_ENTRY_FROM = 'depuis la piste (entrée non typée)'
SENS_INTERSECTION = 'vers l\'intersection (40 m)'

# ---------- constantes de la prise de vues ----------
WAIT_S = 3.0                  # attente apres la pose de la vue
PAIR_GAP_S = 0.5              # delai entre les deux captures
STABLE_TIMEOUT_S = 10.0       # au plus, apres l'attente, pour obtenir une vue stable
KEEPALIVE_S = 2.0             # jamais plus de 2 s entre deux requetes (le crochet relache apres 5 s)
KEEP_DUE_S = 1.0              # la requete de tenue est renvoyee des qu'une seconde s'est ecoulee
FIRST_TRIES_ECHEC = 3         # trois premieres vues tentees d'une base toutes en echec : arret
OLD_HOOK_MESSAGE = ('crochet ancien : recopier capture/hook/ChartsCaptureHook.lua dans '
                    'Saved Games\\...\\Scripts\\ChartsCapture\\')
SLICE_S = 0.5                 # pas des attentes (entretien de la vue entre deux)
VIGNETTE_X, VIGNETTE_Y = 0.10, 0.30   # bande centrale : 10 % retires a gauche et a droite, 30 % en haut et en bas


class Stop(Exception):
    """Arret necessitant une decision (dossier ou base introuvable...)."""


class CaptureStop(RuntimeError):
    """Arret de la prise de vues (crochet ancien, vues toutes en echec) : l'index est ecrit avant de sortir."""


# ---------- geometrie ----------
def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _foot(p, a, b):
    """Pied de p sur le segment ab : (point, distance)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dz * dz
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L2))
    q = (a[0] + t * dx, a[1] + t * dz)
    return q, _dist(p, q)


def _heading(c, a):
    """Cap de c vers a, en degres de 0 a 360 (0 = +x nord, 90 = +z est)."""
    return math.degrees(math.atan2(a[1] - c[1], a[0] - c[0])) % 360.0


def _angle_between(u, v):
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu == 0 or nv == 0:
        return 180.0
    c = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))
    return math.degrees(math.acos(c))


def _hdiff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ---------- le graphe ----------
class Graph:
    """Le .rn5 d'une base, avec l'axe de piste."""

    def __init__(self, r):
        self.r = r
        self.pos = [(n.x, n.z) for n in r.nodes]
        self.line = [[(p[0], p[2]) for p in s.points] for s in r.segments]
        self.runway = [s.is_runway for s in r.segments]
        self.axis = []        # segments de piste : ((x, z), (x, z))
        for s, line in zip(r.segments, self.line):
            if s.is_runway:
                self.axis += list(zip(line, line[1:]))

    def axis_foot(self, p):
        """Pied de p sur l'axe de piste : (point, distance) ; (None, inf) s'il n'y a pas de piste."""
        best = (None, float('inf'))
        for a, b in self.axis:
            q, d = _foot(p, a, b)
            if d < best[1]:
                best = (q, d)
        return best

    def other_end(self, seg_id, node_id):
        s = self.r.segments[seg_id]
        return s.node_b if s.node_a == node_id else s.node_a

    def oriented(self, seg_id, node_id):
        """Ligne mediane du troncon, orientee : le bout le plus proche du noeud en premier."""
        line = self.line[seg_id]
        p = self.pos[node_id]
        if _dist(line[-1], p) < _dist(line[0], p):
            return line[::-1]
        return line

    def seg_length(self, seg_id):
        line = self.line[seg_id]
        return sum(_dist(a, b) for a, b in zip(line, line[1:]))


class Walk:
    """Une marche : points (x, z), abscisses cumulees, abscisses des points d'attente traverses."""

    def __init__(self, start):
        self.pts = [start]
        self.cum = [0.0]
        self.holds = []
        self.segs = []
        self.end_node = None

    @property
    def length(self):
        return self.cum[-1]

    def add(self, points):
        for p in points:
            self.cum.append(self.cum[-1] + _dist(self.pts[-1], p))
            self.pts.append(p)

    def at(self, s):
        """Point d'abscisse s (interpole, borne a la marche)."""
        if s <= 0:
            return self.pts[0]
        if s >= self.cum[-1]:
            return self.pts[-1]
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = 0.0 if seg == 0 else (s - self.cum[lo]) / seg
        a, b = self.pts[lo], self.pts[hi]
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _is_stand(t):
    return t.startswith('stand')


def _is_stop(t):
    return t.startswith('stand') or t.startswith('rwy') or t.startswith('runway')


def _is_pass(node):
    """Noeud de passage : exactement 2 ports et un type de passage (taxiway_l et _mirror ont 3 ports dans les donnees)."""
    return len(node.ports) == 2 and node.type.startswith(PASS_PREFIXES)


def _angle_next(g, node, line_in, cands, max_angle):
    """Regle d'angle : parmi cands, le troncon dont la direction de depart fait le plus petit angle avec la direction
    d'arrivee (deux derniers points) ; il n'est pris que s'il est a moins de max_angle (None : sans limite)."""
    if not cands:
        return None
    d_in = (line_in[-1][0] - line_in[-2][0], line_in[-1][1] - line_in[-2][1]) if len(line_in) >= 2 else (1.0, 0.0)
    best, best_a = None, None
    for p in cands:
        ln = g.oriented(p, node.id)
        j = min(3, len(ln) - 1)
        d_out = (ln[j][0] - ln[0][0], ln[j][1] - ln[0][1])
        a = _angle_between(d_in, d_out)
        if best is None or a < best_a:
            best, best_a = p, a
    if max_angle is not None and not best_a < max_angle:
        return None
    return best


def walk(g, node_id, seg_id, max_angle=None):
    """La marche depuis le noeud node_id le long du troncon seg_id.
    Points : la position du noeud, puis pour chaque troncon sa ligne orientee sans son premier point.
    max_angle : limite de la regle d'angle (30 pour une intersection de taxiways, None pour une bretelle)."""
    nodes = g.r.nodes
    w = Walk(g.pos[node_id])
    used = set()
    cur_node, cur_seg = node_id, seg_id
    while True:
        if w.length >= WALK_MAX_M:
            break
        used.add(cur_seg)
        line = g.oriented(cur_seg, cur_node)
        w.add(line[1:])
        w.segs.append(cur_seg)
        arrive = g.other_end(cur_seg, cur_node)
        w.end_node = arrive
        node = nodes[arrive]
        if node.type == HOLD_TYPE:
            w.holds.append(w.length)
        if _is_stop(node.type):
            break
        if _is_pass(node):
            others = [p for p in node.ports if p != cur_seg]
            nxt = others[0] if others and not g.runway[others[0]] and others[0] not in used else None
        else:
            cands = [p for p in node.ports if p != cur_seg and not g.runway[p] and p not in used]
            nxt = _angle_next(g, node, w.pts, cands, max_angle)
        if nxt is None:
            break
        cur_node, cur_seg = arrive, nxt
    return w


def stand_branch(g, node_id, seg_id):
    """Bretelle de poste : somme des longueurs entieres des troncons jusqu'a un poste, moins de 150 m."""
    nodes = g.r.nodes
    total = 0.0
    cur_node, cur_seg = node_id, seg_id
    for _ in range(STAND_BRANCH_MAX_SEGS):
        total += g.seg_length(cur_seg)
        arrive = g.other_end(cur_seg, cur_node)
        node = nodes[arrive]
        if _is_stand(node.type):
            return total < STAND_BRANCH_M
        if _is_pass(node) and total < STAND_BRANCH_M:
            others = [p for p in node.ports if p != cur_seg]
            if not others:
                return False
            cur_node, cur_seg = arrive, others[0]
            continue
        return False
    return False


# ---------- les vues ----------
def make_view(c, a, genre, sens, bretelle, intersection, troncon):
    d = _dist(c, a)
    return {
        'genre': genre,
        'sens': sens,
        'bretelle': bretelle,
        'intersection': intersection,
        'troncon': troncon,
        'camera': {'x': c[0], 'z': c[1]},
        'hauteur': CAM_HEIGHT_M,
        'cap': _heading(c, a),
        'tangage': math.degrees(math.atan2(AIM_HEIGHT_M - CAM_HEIGHT_M, d)),
        'vise': {'x': a[0], 'z': a[1]},
        'distance': d,
    }


def camera_toward(w, s, a):
    """Camera 'vers' le point vise a, a l'abscisse s de la marche : en avancant de 2 m en 2 m depuis s, le premier point a
    60 m ou plus de a en ligne droite ; a defaut le dernier point essaye a 40 m ou plus ; a defaut None."""
    last = None
    d = s + STEP_M
    L = w.length
    while d <= L:
        p = w.at(d)
        last = p
        if _dist(p, a) >= CAM_FAR_M:
            return p
        d += STEP_M
    if last is not None and _dist(last, a) >= CAM_NEAR_M:
        return last
    return None


def camera_from_runway(g, a):
    """Camera 'depuis la piste' : pied de a sur l'axe ; ramenee a 60 m de a si a est a plus de 70 m ; None si a < 40 m."""
    foot, d = g.axis_foot(a)
    if foot is None or d < FROM_RUNWAY_MIN_M:
        return None
    if d > FROM_RUNWAY_MAX_M:
        t = CAM_FAR_M / d
        return (a[0] + (foot[0] - a[0]) * t, a[1] + (foot[1] - a[1]) * t)
    return foot


def bretelles(g):
    """Les bretelles de piste : liste de listes d'identifiants de noeuds (tries), dans l'ordre de leur plus petit id."""
    cand = []
    for n in g.r.nodes:
        if not (n.type.startswith('runway_taxiway') or n.type == 'rwy_point_takeoff_landing'):
            continue
        if any(g.runway[p] for p in n.ports) and any(not g.runway[p] for p in n.ports):
            cand.append(n.id)
    parent = {i: i for i in cand}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if _dist(g.pos[a], g.pos[b]) < BRETELLE_GROUP_M:
                parent[find(b)] = find(a)
    groups = {}
    for i in cand:
        groups.setdefault(find(i), []).append(i)
    out = [sorted(v) for v in groups.values()]
    out.sort(key=lambda v: v[0])
    return out


def aim_s(g, w):
    """Abscisse visee d'une marche de bretelle."""
    s = None
    for h in w.holds:
        if h < HOLD_MAX_M:
            s = h
        break
    if s is None:
        for p, c in zip(w.pts, w.cum):
            if g.axis_foot(p)[1] >= FAR_AXIS_M:
                s = c
                break
    if s is None:
        s = w.length / 2.0
    return min(s, S_MAX_M)


def views_bretelle(g, nodes, bid):
    inb = set(nodes)
    out = []
    for nid in nodes:
        for t in g.r.nodes[nid].ports:
            if g.runway[t] or g.other_end(t, nid) in inb:
                continue
            w = walk(g, nid, t, None)
            s = aim_s(g, w)
            a = w.at(s)
            c = camera_toward(w, s, a)
            if c is not None:
                out.append(make_view(c, a, 'piste', 'vers la piste', bid, None, t))
            c = camera_from_runway(g, a)
            if c is not None:
                out.append(make_view(c, a, 'piste', 'depuis la piste', bid, None, t))
    return out


def untyped_entries(g):
    """Les entrees de piste non typees : noeuds taxiway_end a moins de 30 m de l'axe de piste (ids, ordre du fichier)."""
    return [n.id for n in g.r.nodes
            if n.type == ENTRY_TYPE and g.axis_foot(g.pos[n.id])[1] < ENTRY_MAX_AXIS_M]


def aim_s_entry(g, w):
    """Abscisse visee d'une entree non typee : en avancant de 2 m en 2 m depuis 0, la premiere abscisse dont le point est a
    70 m ou plus de l'axe de piste ; aucune : la moitie de la longueur de la marche (aucun plafond)."""
    d = 0.0
    while d <= w.length:
        if g.axis_foot(w.at(d))[1] >= FAR_AXIS_M:
            return d
        d += STEP_M
    return w.length / 2.0


def views_entry(g, nid):
    """Les vues d'une entree non typee : marche sans limite d'angle le long de son premier troncon, vue vers la piste (camera
    comme une bretelle) et vue depuis la piste (pied sur l'axe, comme une bretelle)."""
    ports = g.r.nodes[nid].ports
    if not ports:
        return []
    t = ports[0]
    w = walk(g, nid, t, None)
    s = aim_s_entry(g, w)
    a = w.at(s)
    out = []
    c = camera_toward(w, s, a)
    if c is not None:
        out.append(make_view(c, a, 'piste', SENS_ENTRY_TO, nid, None, t))
    c = camera_from_runway(g, a)
    if c is not None:
        out.append(make_view(c, a, 'piste', SENS_ENTRY_FROM, nid, None, t))
    return out


def views_intersection(g, node):
    """Les vues d'une intersection : pour chaque branche gardee (ni piste ni bretelle de poste ; trois au moins), la marche
    (30 degres) ; moins de 40 m : pas de vue ; sinon camera au point de la marche a 40 m, point vise = le noeud."""
    keep = [t for t in node.ports if not g.runway[t] and not stand_branch(g, node.id, t)]
    out = []
    if len(keep) < INTERSECTION_MIN_BRANCHES:
        return out
    a = g.pos[node.id]
    for t in keep:
        w = walk(g, node.id, t, ANGLE_TAXIWAY_DEG)
        if w.length < INTERSECTION_MIN_WALK_M:
            continue
        out.append(make_view(w.at(INTERSECTION_CAM_M), a, 'taxiway', SENS_INTERSECTION, None, node.id, t))
    return out


def dedupe(views):
    kept = []
    for v in views:
        c = (v['camera']['x'], v['camera']['z'])
        if not any(_dist(c, (k['camera']['x'], k['camera']['z'])) < DEDUP_DIST_M
                   and _hdiff(v['cap'], k['cap']) < DEDUP_HEADING_DEG for k in kept):
            kept.append(v)
    return kept


def plan_base(r):
    """La visee d'une base. Retourne (graphe, vues, bretelles, intersections, entrees non typees) ; vues numerotees a partir
    de 1, sans arrondi. Ordre avant dedoublonnage : les bretelles, les entrees non typees, les intersections."""
    g = Graph(r)
    brs = bretelles(g)
    views = []
    for nodes in brs:
        views += views_bretelle(g, nodes, nodes[0])
    entries = untyped_entries(g)
    for nid in entries:
        views += views_entry(g, nid)
    inter = []
    for n in r.nodes:
        if n.type in INTERSECTION_TYPES:
            vs = views_intersection(g, n)
            if vs:
                inter.append(n.id)
            views += vs
    views = dedupe(views)
    for i, v in enumerate(views, 1):
        v['numero'] = i
    return g, views, brs, inter, entries


# ---------- sortie de la visee ----------
def _round_view(v):
    return {
        'numero': v['numero'], 'genre': v['genre'], 'sens': v['sens'],
        'bretelle': v['bretelle'], 'intersection': v['intersection'], 'troncon': v['troncon'],
        'camera': {'x': round(v['camera']['x'], 2), 'z': round(v['camera']['z'], 2)},
        'hauteur': v['hauteur'], 'cap': round(v['cap'], 1), 'tangage': round(v['tangage'], 2),
        'vise': {'x': round(v['vise']['x'], 2), 'z': round(v['vise']['z'], 2)},
        'distance': round(v['distance'], 1),
    }


def summarize(g, views, brs, inter, entries=()):
    """Les bretelles (id, noeuds, vues 'piste' dont le point vise est a moins de 130 m d'un noeud ; les vues des entrees non
    typees ne comptent pas) et les comptes, dont les entrees non typees (ids) et celles sans aucune vue."""
    brets = []
    for nodes in brs:
        n = sum(1 for v in views if v['genre'] == 'piste' and v['sens'] not in (SENS_ENTRY_TO, SENS_ENTRY_FROM)
                and any(_dist((v['vise']['x'], v['vise']['z']), g.pos[i]) < NEAR_NODE_M for i in nodes))
        lateral = any(g.r.nodes[i].type.startswith('runway_taxiway') for i in nodes)
        brets.append({'id': nodes[0], 'noeuds': nodes, 'laterale': lateral, 'vues_piste': n})
    short = [b['id'] for b in brets if b['laterale'] and b['vues_piste'] < 2]
    nothing = [b['id'] for b in brets if not b['laterale'] and b['vues_piste'] == 0]
    seen = {v['bretelle'] for v in views if v['sens'] in (SENS_ENTRY_TO, SENS_ENTRY_FROM)}
    no_view = [i for i in entries if i not in seen]
    counts = {
        'bretelles': len(brets), 'bretelles_laterales_sous_deux_vues': len(short),
        'entrees_de_seuil_sans_vue': len(nothing), 'intersections': len(inter), 'vues': len(views),
        'duree_estimee_s': round(len(views) * SECONDS_PER_VIEW, 1),
        'bretelles_laterales_sous_deux_vues_ids': short, 'entrees_de_seuil_sans_vue_ids': nothing,
        'entrees_non_typees': list(entries), 'entrees_non_typees_sans_vue_ids': no_view,
    }
    return brets, counts


def build_vues_json(theatre, base, source, r):
    """(contenu de vues.json, comptes) d'une base lue."""
    g, views, brs, inter, entries = plan_base(r)
    brets, counts = summarize(g, views, brs, inter, entries)
    doc = {'base': base, 'theatre': theatre, 'source': source, 'bretelles': brets,
           'entrees_non_typees': list(entries), 'intersections': inter, 'vues': [_round_view(v) for v in views]}
    return doc, counts


def _dump(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=1) + '\n').encode('utf-8')


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def panneaux_dir(cfg, theatre):
    return cfg.work_dir / 'panneaux' / theatre


def rn5_dir(cfg, theatre):
    d = cfg.terrains / theatre / 'AirfieldsTaxiways'
    if not d.is_dir():
        raise Stop(f'ARRET : dossier introuvable {d}')
    return d


def list_bases(cfg, theatre):
    """Les bases du dossier AirfieldsTaxiways dont le .rn5 fait au moins 5 000 octets : [(nom, chemin)], triees."""
    d = rn5_dir(cfg, theatre)
    return [(p.stem, p) for p in sorted(d.glob('*.rn5'), key=lambda q: q.name.lower()) if p.stat().st_size >= MIN_RN5_BYTES]


def find_base(cfg, theatre, base):
    d = rn5_dir(cfg, theatre)
    p = d / f'{base}.rn5'
    if p.is_file():
        return p.stem, p
    for q in d.glob('*.rn5'):
        if q.stem.lower() == base.lower():
            return q.stem, q
    raise Stop(f'ARRET : {base}.rn5 introuvable dans {d}')


def cmd_visee(cfg, theatre, bases, log=print):
    """Visee des bases nommees (ou de toutes). Ecrit vues.json par base et visee.json du theatre."""
    todo = [find_base(cfg, theatre, b) for b in bases] if bases else list_bases(cfg, theatre)
    out = panneaux_dir(cfg, theatre)
    vpath = out / 'visee.json'
    summary = {}
    if bases and vpath.is_file():
        try:
            summary = json.loads(vpath.read_text(encoding='utf-8')).get('bases', {})
        except ValueError:
            summary = {}
    t0 = time.perf_counter()
    for name, path in todo:
        r = rn5mod.read(path)
        doc, counts = build_vues_json(theatre, name, path.name, r)
        _write(out / name / 'vues.json', _dump(doc))
        summary[name] = counts
        log(f'{name} : {counts["bretelles"]} bretelles, {len(counts["entrees_non_typees"])} entrees non typees, '
            f'{counts["intersections"]} intersections, {counts["vues"]} vues (~{counts["duree_estimee_s"] / 60:.1f} min)')
    _write(vpath, _dump({'theatre': theatre, 'bases': {k: summary[k] for k in sorted(summary, key=str.lower)}}))
    log(f'visee {theatre} : {len(todo)} base(s) en {time.perf_counter() - t0:.1f} s')
    return summary


# ---------- prise de vues ----------
class Env:
    """Ce que la prise de vues demande a DCS et a l'ecran. Reel : les fonctions de f10capture.py (hook, grab, premier plan,
    diff_frac, save_png), reutilisees. Les tests en fournissent de fausses."""

    def __init__(self, hook, grab, ensure_front, diff_frac, save_png, stab_frac, sleep=time.sleep, now=time.monotonic,
                 release=None, in_front=None):
        self.hook, self.grab, self.ensure_front = hook, grab, ensure_front
        self.in_front = in_front or (lambda: True)
        self.diff_frac, self.save_png, self.stab_frac = diff_frac, save_png, stab_frac
        self.sleep, self.now = sleep, now
        self.release = release or (lambda: None)


def real_env(cfg):
    """Environnement reel (Windows, DCS) : f10capture.py, avec la configuration deja lue."""
    import f10capture
    f10capture.CFG = cfg
    return Env(f10capture.hook, f10capture.grab, f10capture.ensure_dcs_front, f10capture.diff_frac,
               f10capture.save_png, f10capture.STAB_FRAC, release=f10capture.hook_release,
               in_front=f10capture.dcs_in_front)


def vignette(img):
    """La bande centrale : 10 % retires a gauche et a droite, 30 % en haut et en bas."""
    h, w = img.shape[:2]
    y, x = int(h * VIGNETTE_Y), int(w * VIGNETTE_X)
    return img[y:h - y, x:w - x]


def cam_line(v):
    return f'cam {v["camera"]["x"]:.3f} {v["camera"]["z"]:.3f} {v["hauteur"]:.1f} {v["cap"]:.1f} {v["tangage"]:.2f}'


class _Hold:
    """Tenue de la vue : la requete `cam` est renvoyee des qu'elle est due, jamais plus de 2 s entre deux requetes."""

    def __init__(self, env, line):
        self.env, self.line, self.last = env, line, None

    def send(self):
        reply = self.env.hook(self.line).split()
        self.last = self.env.now()
        return reply

    def keep(self):
        """A appeler avant chaque mise au premier plan et chaque capture d'ecran."""
        if self.last is None or self.env.now() - self.last >= KEEP_DUE_S:
            self.send()

    def wait(self, seconds):
        end = self.env.now() + seconds
        while True:
            rem = end - self.env.now()
            if rem <= 0:
                return
            self.env.sleep(min(rem, SLICE_S))
            self.keep()


def take_view(env, v):
    """Prend une vue. Retourne (statut, image, position, erreur) ; statut `faite`, `instable` ou `echec` (image et position
    None, erreur = reponse du crochet). Un crochet qui ne connait pas `cam` : CaptureStop."""
    hold = _Hold(env, cam_line(v))
    reply = hold.send()
    if reply[:2] == ['err', 'unknown_command']:
        raise CaptureStop(OLD_HOOK_MESSAGE)
    if not reply or reply[0] != 'ok' or len(reply) < 4:
        return 'echec', None, None, ' '.join(reply)
    pos = [float(reply[1]), float(reply[2]), float(reply[3])]
    hold.keep()
    env.ensure_front(f'vue {v["numero"]}')
    hold.wait(WAIT_S)
    t0 = env.now()
    while True:
        hold.keep()
        env.ensure_front(f'vue {v["numero"]}')
        hold.keep()
        a = env.grab()
        hold.wait(PAIR_GAP_S)
        hold.keep()
        b = env.grab()
        if env.diff_frac(a, b) < env.stab_frac and env.in_front():
            return 'faite', b, pos, None
        if env.now() - t0 >= STABLE_TIMEOUT_S:
            return 'instable', b, pos, None


def _load_index(path):
    """L'index d'une passe precedente, ou None (absent ou illisible)."""
    if path.is_file():
        try:
            idx = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(idx.get('vues'), list):
                return idx
        except ValueError:
            pass
    return None


def _archive_index(path):
    """Garde l'ancien index sous index.<horodatage>.json."""
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    dst, k = path.with_name(f'index.{stamp}.json'), 1
    while dst.exists():
        k += 1
        dst = path.with_name(f'index.{stamp}-{k}.json')
    path.replace(dst)


def capture_base(cfg, env, theatre, base, log=print):
    """Prend toutes les vues non faites de la base. Retourne True si la base est complete (aucune vue interrompue)."""
    out = panneaux_dir(cfg, theatre) / base
    vpath = out / 'vues.json'
    if not vpath.is_file():
        name, path = find_base(cfg, theatre, base)
        doc, _ = build_vues_json(theatre, name, path.name, rn5mod.read(path))
        _write(vpath, _dump(doc))
    raw = vpath.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    views = json.loads(raw.decode('utf-8'))['vues']
    ipath = out / 'index.json'
    old = _load_index(ipath)
    if old is not None and old.get('vues_sha256') != digest:
        log(f'{base} : vues.json a change depuis la passe precedente, toutes les vues sont refaites')
        _archive_index(ipath)
        old = None
    idx = old or {'base': base, 'theatre': theatre, 'vues_sha256': digest, 'vues': []}
    idx['vues_sha256'] = digest
    done = {e['numero']: e for e in idx['vues']}
    tried = []
    try:
        for v in views:
            n = v['numero']
            name = f'{n:03d}.png'
            e = done.get(n)
            if e and e['statut'] == 'faite' and (out / e['img']).is_file() and (out / e['vignette']).is_file():
                log(f'{base} {n}/{len(views)} deja faite')
                continue
            statut, img, pos, erreur = take_view(env, v)
            entry = {'numero': n, 'statut': statut, 'img': None, 'vignette': None,
                     'heure': datetime.now().isoformat(timespec='seconds'), 'position': pos, 'erreur': erreur}
            if img is not None:
                entry['img'], entry['vignette'] = f'img/{name}', f'vignettes/{name}'
                env.save_png(img, out / 'img' / name)
                env.save_png(vignette(img), out / 'vignettes' / name)
            done[n] = entry
            idx['vues'] = [done[k] for k in sorted(done)]
            _write(ipath, _dump(idx))
            log(f'{base} {n}/{len(views)} {statut}')
            tried.append(statut)
            if len(tried) == FIRST_TRIES_ECHEC and all(t == 'echec' for t in tried):
                raise CaptureStop(f'{base} : les {FIRST_TRIES_ECHEC} premieres vues tentees sont en echec '
                                  f'(derniere reponse du crochet : {erreur})')
    except BaseException:
        idx['vues'] = [done[k] for k in sorted(done)]
        _write(ipath, _dump(idx))
        raise
    finally:
        try:
            env.release()
        except Exception:
            pass
    return True


def cmd_capture(cfg, theatre, bases, env=None, log=print):
    env = env or real_env(cfg)
    for b in bases:
        name, _ = find_base(cfg, theatre, b)
        capture_base(cfg, env, theatre, name, log)


def main(argv, cfg):
    if len(argv) < 3 or argv[1] not in ('visee', 'capture', 'lire'):
        raise SystemExit(__doc__)
    cmd, theatre, bases = argv[1], argv[2], argv[3:]
    if cmd in ('capture', 'lire') and not bases:
        raise SystemExit(__doc__)
    if cmd == 'visee':
        cmd_visee(cfg, theatre, bases)
    elif cmd == 'lire':
        import signread
        try:
            signread.cmd_lire(cfg, theatre, bases, panneaux_dir)
        except signread.ReadError as e:
            raise Stop(f'ARRET : {e}')
    else:
        cmd_capture(cfg, theatre, bases)


if __name__ == '__main__':
    from config import load_config
    _cfg, _argv = load_config(sys.argv)
    try:
        main(_argv, _cfg)
    except Stop as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except (KeyboardInterrupt, OSError, RuntimeError) as e:
        print(f'Arret : {type(e).__name__}: {e} (l\'index est ecrit ; relancer reprend a la premiere vue non faite)',
              file=sys.stderr)
        sys.exit(3)
    except Exception as e:      # ForegroundFailure, AskCdP de f10capture, Rn5Error
        print(f'Arret : {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(3)
