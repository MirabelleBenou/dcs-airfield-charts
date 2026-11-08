"""Lecture du graphe des voies d'un fichier .rn5 de DCS (Mods/terrains/<Theatre>/AirfieldsTaxiways/<Base>.rn5).

Module pur : bibliotheque standard seulement, importable partout. Lecture sequentielle, sans aucune recherche
heuristique : en-tete, tables de noms (largeurs, types de noeuds), troncons, noeuds. Petit-boutiste.

Format :
  32 octets d'en-tete, u32 L + 't5::rn5file', u32 0
  table des largeurs : u32 n, n chaines (u32 L + octets) ; table des types de noeuds : idem
  troncons : u32 n, puis n enregistrements, chacun precede d'un u32 0 :
      7 u32 (identifiant, indice de largeur, noeud a, port a, noeud b, port b, 0),
      u32 p, p points (x, y, z) en doubles (la ligne mediane),
      queue : u32 m, 64*m octets, u32 0, u32 k, 20*k octets, 12 octets
  noeuds : u32 n, puis n enregistrements, chacun precede d'un u32 0 :
      u32 identifiant, u32 indice de type, u32 c (ports), c u32 (le troncon de chaque port),
      deux matrices 4x4 de doubles (pose, inverse ; la position est a la ligne 4), u32 L + nom,
      u32 k, k couples (u32, float)
Apres les noeuds : des octets non decodes, ignores.
"""
import math
import struct
from pathlib import Path
from typing import List, NamedTuple, Tuple

MAGIC = b't5::rn5file'


class Rn5Error(Exception):
    """Fichier .rn5 qui ne se lit pas selon le format (le message nomme le fichier)."""


class Segment(NamedTuple):
    id: int
    width_index: int
    width: str                       # nom de largeur ('taxiway_15m', 'runway_45m'...)
    node_a: int
    port_a: int
    node_b: int
    port_b: int
    points: Tuple[Tuple[float, float, float], ...]   # (x, y, z), ligne mediane

    @property
    def is_runway(self):
        return self.width.startswith('runway')

    @property
    def length(self):
        return polyline_length(self.points)

    @property
    def half_width(self):
        return width_m(self.width) / 2.0


class Node(NamedTuple):
    id: int
    type_index: int
    type: str                        # nom de type ('stand_..', 'runway_taxiway_t'...)
    ports: Tuple[int, ...]           # le troncon branche sur chaque port
    x: float
    z: float
    name: str


class Rn5(NamedTuple):
    path: str
    widths: Tuple[str, ...]
    types: Tuple[str, ...]
    segments: Tuple[Segment, ...]
    nodes: Tuple[Node, ...]


def polyline_length(points):
    """Longueur sur x, z (y est toujours nul)."""
    return sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(points, points[1:]))


def width_m(width_name):
    """Largeur en metres lue dans le nom ('taxiway_41m' -> 41.0) ; 0 si le nom n'en porte pas."""
    digits = ''
    for part in width_name.split('_')[1:]:
        if part.endswith('m') and part[:-1].replace('.', '', 1).isdigit():
            digits = part[:-1]
    return float(digits) if digits else 0.0


class _Reader:
    def __init__(self, data, name):
        self.data, self.name, self.pos = data, name, 0

    def fail(self, what):
        raise Rn5Error(f'{self.name} : {what} (a l\'octet {self.pos})')

    def take(self, n, what):
        if n < 0 or self.pos + n > len(self.data):
            self.fail(f'fin de fichier dans {what}')
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u32(self, what):
        return struct.unpack('<I', self.take(4, what))[0]

    def u32s(self, n, what):
        return struct.unpack(f'<{n}I', self.take(4 * n, what))

    def doubles(self, n, what):
        return struct.unpack(f'<{n}d', self.take(8 * n, what))

    def count(self, what, unit, limit=1 << 22):
        n = self.u32(what)
        if n > limit or self.pos + n * unit > len(self.data):
            self.fail(f'nombre invalide pour {what} : {n}')
        return n

    def string(self, what):
        n = self.count(what, 1, 4096)
        return self.take(n, what).decode('latin-1')

    def zero(self, what):
        v = self.u32(what)
        if v != 0:
            self.fail(f'{what} : 0 attendu, lu {v}')


def parse(data, name='<memoire>'):
    """Lit un .rn5 (bytes). Retourne un Rn5 ; Rn5Error (nommant `name`) si le format n'est pas respecte."""
    r = _Reader(data, name)
    r.take(32, 'l\'en-tete')
    magic = r.string('la signature')
    if magic.encode('latin-1') != MAGIC:
        r.fail(f'signature {magic!r} au lieu de {MAGIC.decode()!r}')
    r.zero('apres la signature')
    widths = tuple(r.string('une largeur') for _ in range(r.count('la table des largeurs', 4)))
    types = tuple(r.string('un type') for _ in range(r.count('la table des types', 4)))

    segments = []
    for i in range(r.count('les troncons', 4 * 8)):
        r.zero(f'le troncon {i}')
        sid, wi, na, pa, nb, pb, z7 = r.u32s(7, f'le troncon {i}')
        if sid != i:
            r.fail(f'troncon d\'identifiant {sid} au rang {i}')
        if wi >= len(widths):
            r.fail(f'troncon {i} : indice de largeur {wi} hors table')
        if z7 != 0:
            r.fail(f'troncon {i} : 0 attendu en fin d\'entete, lu {z7}')
        p = r.count(f'les points du troncon {i}', 24)
        v = r.doubles(3 * p, f'les points du troncon {i}')
        points = tuple((v[3 * k], v[3 * k + 1], v[3 * k + 2]) for k in range(p))
        m = r.count(f'la queue du troncon {i}', 64)
        r.take(64 * m, f'la queue du troncon {i}')
        r.zero(f'la queue du troncon {i}')
        k = r.count(f'la queue du troncon {i}', 20)
        r.take(20 * k + 12, f'la queue du troncon {i}')
        segments.append(Segment(sid, wi, widths[wi], na, pa, nb, pb, points))

    nodes = []
    for i in range(r.count('les noeuds', 4 * 3)):
        r.zero(f'le noeud {i}')
        nid, ti = r.u32s(2, f'le noeud {i}')
        if nid != i:
            r.fail(f'noeud d\'identifiant {nid} au rang {i}')
        if ti >= len(types):
            r.fail(f'noeud {i} : indice de type {ti} hors table')
        c = r.count(f'les ports du noeud {i}', 4)
        ports = r.u32s(c, f'les ports du noeud {i}')
        for t in ports:
            if t >= len(segments):
                r.fail(f'noeud {i} : port sur le troncon {t} qui n\'existe pas')
        pose = r.doubles(16, f'la pose du noeud {i}')
        r.take(128, f'l\'inverse du noeud {i}')
        nm = r.string(f'le nom du noeud {i}')
        k = r.count(f'les distances du noeud {i}', 8)
        r.take(8 * k, f'les distances du noeud {i}')
        nodes.append(Node(nid, ti, types[ti], tuple(ports), pose[12], pose[14], nm))
    return Rn5(name, widths, types, tuple(segments), tuple(nodes))


def read(path):
    """Lit un fichier .rn5. Rn5Error (nommant le fichier) si le format n'est pas respecte."""
    p = Path(path)
    return parse(p.read_bytes(), p.name)


def holding_points(rn5):
    """Les noeuds `taxi_point_holding_position` (points d'attente du jeu)."""
    return [n for n in rn5.nodes if n.type == 'taxi_point_holding_position']
