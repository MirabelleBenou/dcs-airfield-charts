"""Procedures d'approche aux instruments types (lot ac-approches-v1) : la geometrie, en fonctions pures.

Ni pydcs ni projection : les positions sont dans le repere du jeu (x vers le Nord de la grille, z vers l'Est, metres), les
azimuts sont des azimuts grille (degres, 0 = x, 90 = z), la declinaison `decl` et la convergence `gamma` sont donnees par
l'appelant. Le relief est la grille de `build_relief.py` (`alt`, float32, case (i, j) au point x = x0 + 100 i, z = z0 + 100 j).

Deux familles de pages, construites par des schemas types sur les donnees de DCS (ce ne sont pas des procedures publiees) :
- avion : attente d'alignement + eloignement + virage de base qui finit au IF dans l'axe, ou hippodrome sur la balise quand
  elle est devant le seuil et dans l'axe ; segment intermediaire de 2 NM en palier ; finale de 3 degres (ou l'ILS) dans l'axe ;
- helicoptere : NDB seul, une seule altitude, 80 kt, virage de base qui finit au FAF a 3,1 NM du seuil, pente FAF -> seuil.
Chaque segment est au-dessus du sol le plus haut de son couloir + 1 000 ft (2 000 ft des 1 500 m), arrondi a la centaine.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from . import msa

NM = 1852.0                       # metres par mille nautique
FT = 3.28084                      # pieds par metre (comme `page.FT`)
SLOPE_DEG = 3.0
TAN3 = math.tan(math.radians(SLOPE_DEG))
PAS_M = 100.0

# avion (cadrage 4.4, 4.5)
V_TURN_KT = 165.0                 # vitesse de trace des virages (rayon au taux 1)
HOLD_LEG_M, HOLD_R_M = 2.6 * NM, 0.65 * NM          # attente avion : dessin de la maquette validee (1 min)
INT_NM = 2.0                      # segment intermediaire, IF -> FAF, en palier
INT_AXIS_NM = 14.0                # couloir de l'intermediaire : l'axe, du seuil a 14 NM (maquette validee)
FIN_AXIS_NM = 9.0                 # couloir de la finale (information)
MIS_STRAIGHT_NM = 2.0             # interrompue : montee dans l'axe avant le virage vers la balise
AXIS_TOL_NM = 0.3                 # balise « dans l'axe »
AHEAD_NM = 0.5                    # interrompue : balise devant a plus de 0,5 NM -> directe
HALF = {'hold': 2.5, 'out': 2.5, 'int': 1.5, 'fin': 1.0, 'mis': 2.0}          # demi-largeurs des couloirs avion (NM)
CAT_AB_KT, CAT_CD_KT = 120.0, 180.0
TCH_DEFAULT_FT = 50

# helicoptere (cadrage 4.6)
HELI_KT = 80.0
HELI_KMH = 150
HELI_FAF_NM = 3.1
HELI_HOLD_MIN = 1.0
HELI_MIS_STRAIGHT_NM = 1.5
HALF_HELI = {'hold': 1.0, 'out': 1.5, 'fin': 1.0, 'mis': 1.5}
TIE_M = 1.0                       # cotes : ecart de moins de 1 m = egalite (virages a droite)

KINDS = ('VOR/DME', 'VOR', 'NDB', 'TACAN', 'RSBN')                   # ordre de la balise de reference (4.3.1)
DME_KINDS = ('VOR/DME', 'TACAN', 'RSBN')


class GeometrieImpossible(Exception):
    """Le schema type ne se construit pas (balise dans le cercle du virage, tangente absente...) : le sens est ecarte."""


TRAVERSAL_MIN_FT = 1000           # addendum 1 : traversee d'une piste admise a AD ELEV + 1 000 ft au moins


# ---------- vecteurs et caps ----------
def unit(az):
    a = math.radians(az)
    return math.cos(a), math.sin(a)


def right_of(az):
    """Vecteur unitaire a droite de la direction `az` (x Nord, z Est)."""
    a = math.radians(az)
    return -math.sin(a), math.cos(a)


def az_of(a, b):
    """Azimut grille de a vers b (degres, -180 a 180)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def mag(az_grid, decl):
    """Cap magnetique (compas du cockpit) d'un azimut grille : grille - declinaison (lot caps-cockpit)."""
    return (az_grid - decl) % 360.0


def mag_int(az_grid, decl):
    """Cap magnetique arrondi au degre, 0 a 359."""
    return int(round(mag(az_grid, decl))) % 360


def along_cross(p, thr, az):
    """(le long de l'axe, en travers) de p par rapport au seuil : metres, le long > 0 au-dela du seuil (sens d'atterrissage),
    en travers > 0 a droite du sens d'atterrissage."""
    u = unit(az)
    d = (p[0] - thr[0], p[1] - thr[1])
    return d[0] * u[0] + d[1] * u[1], d[1] * u[0] - d[0] * u[1]


def axis_point(thr, az, d_nm):
    """Point de l'axe a `d_nm` NM avant le seuil (cote approche)."""
    u = unit(az)
    return thr[0] - u[0] * d_nm * NM, thr[1] - u[1] * d_nm * NM


def offset(p, az, d_m):
    u = unit(az)
    return p[0] + u[0] * d_m, p[1] + u[1] * d_m


# ---------- unites ----------
def ft_to_m(ft):
    return ft / FT


def m_up10(ft):
    """Altitude en metres, arrondie au-dessus a 10 m."""
    return int(math.ceil(round(ft / FT / 10.0, 6)) * 10)


def qfe_m(ft, elev_ft):
    """Hauteur QFE (au-dessus de l'AD ELEV) en metres, arrondie au-dessus a 10 m."""
    return int(math.ceil(round((ft - elev_ft) / FT / 10.0, 6)) * 10)


def kmh(kt):
    """Vitesse en km/h (non arrondie)."""
    return kt * NM / 1000.0


def kmh_label(kt):
    """km/h arrondis a 5 (tableau des vitesses sol de la page helicoptere : 80 kt -> 150 km/h)."""
    return int(round(kmh(kt) / 5.0)) * 5


def km(nm):
    return nm * NM / 1000.0


def vs_ftmin(gs_kt, slope_deg):
    """Vitesse verticale (ft/min) d'une pente `slope_deg` a la vitesse sol `gs_kt`."""
    return gs_kt * NM * FT / 60.0 * math.tan(math.radians(slope_deg))


def ftmin_to_ms(v):
    return v / FT / 60.0


def turn_radius_m(kt):
    """Rayon du virage au taux 1 (360 degres en 2 min) a `kt`."""
    return kt / (60.0 * math.pi) * NM


def mmss(minutes):
    """Duree en min:s (secondes arrondies)."""
    s = int(round(minutes * 60.0))
    return f'{s // 60}:{s % 60:02d}'


def round_alt(h_m, marge_ft=None):
    """Altitude d'un segment (ft) sur un sol de `h_m` m : + 1 000 ft (2 000 ft des 1 500 m), arrondie a la centaine au-dessus."""
    if marge_ft is None:
        marge_ft = msa.MARGE_MONTAGNE_FT if h_m >= msa.SEUIL_MONTAGNE_M else msa.MARGE_FT
    return msa.arrondir_msa_ft(h_m, marge_ft)


# ---------- relief ----------
def ground_m(alt, x0, z0, p):
    """Sol (m) en p, interpole bilineairement entre les quatre noeuds ; ValueError hors de la grille ou sur un noeud NaN."""
    u, v = (p[0] - x0) / PAS_M, (p[1] - z0) / PAS_M
    i, j = int(math.floor(u)), int(math.floor(v))
    if i < 0 or j < 0 or i + 1 >= alt.shape[0] or j + 1 >= alt.shape[1]:
        raise ValueError(f'point hors de la grille : {p}')
    a, b = u - i, v - j
    g = np.asarray(alt[i:i + 2, j:j + 2], dtype=np.float64)
    if np.isnan(g).any():
        raise ValueError(f'relief absent au point {p}')
    return float(g[0, 0] * (1 - a) * (1 - b) + g[1, 0] * a * (1 - b) + g[0, 1] * (1 - a) * b + g[1, 1] * a * b)


def corridor_max_m(alt, x0, z0, pts, half_m):
    """Sol le plus haut (m) des noeuds de la grille a `half_m` au plus de la polyligne `pts`. ValueError si le couloir sort de
    la grille ou n'a que des noeuds NaN."""
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    i0 = int((min(xs) - half_m - x0) // PAS_M) - 1
    i1 = int((max(xs) + half_m - x0) // PAS_M) + 2
    j0 = int((min(zs) - half_m - z0) // PAS_M) - 1
    j1 = int((max(zs) + half_m - z0) // PAS_M) + 2
    if i0 < 0 or j0 < 0 or i1 > alt.shape[0] or j1 > alt.shape[1]:
        raise ValueError('couloir hors de la grille du relief')
    sub = np.asarray(alt[i0:i1, j0:j1], dtype=np.float64)
    X = (x0 + PAS_M * np.arange(i0, i1))[:, None] + 0 * sub
    Z = (z0 + PAS_M * np.arange(j0, j1))[None, :] + 0 * sub
    m = np.zeros(sub.shape, bool)
    for a, b in zip(pts, pts[1:]) if len(pts) > 1 else [(pts[0], pts[0])]:
        dx, dz = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dz * dz or 1.0
        t = np.clip(((X - a[0]) * dx + (Z - a[1]) * dz) / L2, 0, 1)
        m |= np.hypot(X - a[0] - t * dx, Z - a[1] - t * dz) <= half_m
    v = np.where(m & ~np.isnan(sub), sub, -np.inf)
    h = float(v.max())
    if not math.isfinite(h):
        raise ValueError('couloir sans relief')
    return h


def corridor(alt, x0, z0, pts, half_nm):
    """(sol le plus haut en m, au dixieme ; altitude du segment en ft)."""
    h = corridor_max_m(alt, x0, z0, pts, half_nm * NM)
    return round(h, 1), round_alt(h)


# ---------- distances aux pistes ----------
def runway_polygons(runways):
    """Polygones shapely des pistes (listes de quatre coins (x, z))."""
    return [Polygon(r) for r in runways]


def dist_runways(pts, polys):
    """Plus courte distance (m) de la polyligne `pts` aux polygones des pistes ; 0 si elle en coupe un."""
    g = LineString(pts) if len(pts) > 1 else Point(pts[0])
    return min((g.distance(p) for p in polys), default=float('inf'))


def side_by_distance(d_left, d_right):
    """+1 (gauche) si le cote gauche passe plus loin des pistes, -1 (droite) sinon ; egalite a 1 m pres : droite."""
    return 1 if d_left > d_right + TIE_M else -1


# ---------- constructions ----------
def half_turn(c, a0, sens, r, n=28):
    """Demi-cercle de centre c, de l'angle a0 (degres, repere du jeu), sens +1 a droite (horaire sur la carte), -1 a gauche."""
    return [(c[0] + r * math.cos(math.radians(a0 + sens * 180.0 * k / n)),
             c[1] + r * math.sin(math.radians(a0 + sens * 180.0 * k / n))) for k in range(n + 1)]


def base_turn(beacon, end, az, r, sg, n=48):
    """Virage de base de rayon r qui finit en `end`, au cap `az`, depuis la balise : l'eloignement est la tangente de la balise au
    cercle. sg = +1 virage a gauche, -1 a droite. Retourne (centre, cap grille de l'eloignement, longueur de l'eloignement en m,
    point de tangence, points de l'arc de T a end). GeometrieImpossible si la balise est dans le cercle."""
    rg = right_of(az)
    C = (end[0] - sg * rg[0] * r, end[1] - sg * rg[1] * r)
    dC = math.dist(beacon, C)
    if dC <= r * (1 + 1e-9):
        raise GeometrieImpossible('balise dans le cercle du virage de base')
    h = az_of(beacon, C) + sg * math.degrees(math.asin(r / dC))
    L = math.sqrt(dC ** 2 - r ** 2)
    u = unit(h)
    T = (beacon[0] + L * u[0], beacon[1] + L * u[1])
    aT, aF = az_of(C, T), az_of(C, end)
    sw = ((aT - aF) % 360.0) if sg > 0 else ((aF - aT) % 360.0)
    arc = [(C[0] + r * math.cos(math.radians(aT - sg * sw * k / n)), C[1] + r * math.sin(math.radians(aT - sg * sw * k / n)))
           for k in range(n + 1)]
    return C, h, L, T, arc


def alignment_hold(beacon, inbound_az, leg_m, r, hs, n=28):
    """Attente d'alignement (PANS-OPS) sur la balise : rapprochement au cap `inbound_az`, on la quitte tout droit a la verticale
    de la balise. hs = +1 virages a droite, -1 a gauche. Retourne (points, debut de l'eloignement, fin de l'eloignement,
    debut du rapprochement)."""
    dH, rH = unit(inbound_az), right_of(inbound_az)
    HA = (beacon[0] - dH[0] * leg_m, beacon[1] - dH[1] * leg_m)
    c1 = (beacon[0] + hs * rH[0] * r, beacon[1] + hs * rH[1] * r)
    c2 = (HA[0] + hs * rH[0] * r, HA[1] + hs * rH[1] * r)
    pts = [HA, beacon] + half_turn(c1, az_of(c1, beacon), hs, r, n)
    ob = pts[-1]
    hb = (ob[0] - dH[0] * leg_m, ob[1] - dH[1] * leg_m)
    return pts + [hb] + half_turn(c2, az_of(c2, hb), hs, r, n), ob, hb, HA


def racetrack(beacon, if_pt, az, r, hs, n=28):
    """Hippodrome (PANS-OPS) sur la balise : rapprochement = la finale (cap `az`) ; demi-tour a la verticale de la balise,
    eloignement jusqu'au travers du IF, demi-tour qui ramene au IF. hs = +1 a droite, -1 a gauche.
    Retourne (premier demi-tour, fin de l'eloignement, second demi-tour)."""
    rg = right_of(az)
    c1 = (beacon[0] + hs * rg[0] * r, beacon[1] + hs * rg[1] * r)
    a1 = half_turn(c1, az_of(c1, beacon), hs, r, n)
    c2 = (if_pt[0] + hs * rg[0] * r, if_pt[1] + hs * rg[1] * r)
    o_end = (if_pt[0] + hs * rg[0] * 2 * r, if_pt[1] + hs * rg[1] * 2 * r)
    a2 = half_turn(c2, az_of(c2, o_end), hs, r, n)
    return a1, o_end, a2


def centroid(pts):
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def sector_of(p, center, gamma, decl):
    """Secteur MSA (0 a 3) du point p, autour de `center` (regle de `msa.numero_secteur`)."""
    return int(msa.numero_secteur(np.array([p[0] - center[0]]), np.array([p[1] - center[1]]), gamma, decl)[0])


def axis_at_distance(thr, az, beacon, d_target, d_guess, span_nm=1.0, step_nm=0.005):
    """Le point de l'axe (distance au seuil en NM) le plus proche de `d_guess` dont la distance a la balise vaut `d_target` NM,
    cherche a +-span ; None s'il n'y en a pas."""
    def f_(d):
        return math.dist(beacon, axis_point(thr, az, d)) / NM - d_target
    n = int(round(2 * span_nm / step_nm))
    xs = [d_guess - span_nm + k * step_nm for k in range(n + 1)]
    best = None
    for a, b in zip(xs, xs[1:]):
        fa, fb = f_(a), f_(b)
        if fa == 0 or fa * fb < 0:
            lo, hi = a, b
            for _ in range(60):
                mid = (lo + hi) / 2
                if f_(lo) * f_(mid) <= 0:
                    hi = mid
                else:
                    lo = mid
            r = (lo + hi) / 2
            if best is None or abs(r - d_guess) < abs(best - d_guess):
                best = r
    return best


# ---------- la page avion ----------
@dataclass
class Balise:
    kind: str                     # VOR/DME, VOR, NDB, TACAN, RSBN
    callsign: str
    freq: str                     # '111.20', '390', '88X', 'CH5'
    x: float
    z: float
    channel: str = ''             # 'CH49X' d'un VOR/DME qui a un canal

    @property
    def pos(self):
        return self.x, self.z

    @property
    def dme(self):
        return self.kind in DME_KINDS


@dataclass
class Entree:
    sens: str                     # '01L'
    thr: tuple                    # seuil (x, z)
    end: tuple                    # l'autre extremite de la piste
    az: float                     # azimut grille du sens d'atterrissage
    width_m: float
    runways: list                 # polygones (quatre coins (x, z)) de toutes les pistes du terrain
    thr_ft: int
    elev_ft: int
    decl: float                   # declinaison a la balise de reference (caps)
    gamma: float                  # convergence a la balise de reference (MSA)
    ref: Balise                   # balise de reference (avion) ou NDB (helicoptere)
    ndb: Balise = None            # avion : NDB de l'option 3
    ils: dict = None              # {'s_gs' (m), 'tch' (ft), 'loc' (texte)} si la base a une page ILS pour ce seuil


@dataclass
class Procedure:
    famille: str                  # 'avion' ou 'helico'
    e: Entree
    mode: str                     # 'base' ou 'racetrack'
    turn: str                     # 'LEFT' / 'RIGHT'
    hold_turns: str               # 'RIGHT TURNS' / 'LEFT TURNS'
    crs: int
    qdr_out: int
    hold_pts: list
    out_pts: list                 # eloignement + virage (ou hippodrome)
    hold_inb_az: float
    out_az: float                 # azimut grille de l'eloignement
    T: tuple = None               # fin de l'eloignement (tangence ou travers du IF)
    C: tuple = None               # centre du virage de base
    arc: list = field(default_factory=list)
    a1: list = field(default_factory=list)
    a2: list = field(default_factory=list)
    hold_ob: tuple = None
    hold_hb: tuple = None
    hold_ha: tuple = None
    IF: tuple = None
    FAF: tuple = None
    faf_nm: float = 0.0           # NM du seuil
    if_nm: float = 0.0
    faf_dme: float = None
    if_dme: float = None
    thr_dme: float = None
    out_dme: float = None         # limite d'eloignement (DME)
    l_out_nm: float = 0.0
    t_ab: float = 0.0
    t_cd: float = 0.0
    tch: int = TCH_DEFAULT_FT
    s_gs: float = 0.0
    ils: bool = False
    int_ft: int = 0
    init_ft: int = 0
    hold_ft: int = 0
    mis_ft: int = 0
    zone_ft: int = 0
    hold_msa: int = 0
    msa: dict = None
    corridors: dict = field(default_factory=dict)       # nom -> (sol m, altitude ft)
    mis_pts: list = field(default_factory=list)
    mis_direct: bool = False      # balise devant : directe vers elle
    mis_right: bool = False       # virage de l'interrompue a droite
    v_par_nm: float = 0.0
    v_lat_m: float = 0.0
    qdm_faf: int = None           # QDM du NDB au FAF et au seuil
    qdm_thr: int = None
    rad_faf: int = None           # radiale de la reference au FAF et au seuil (reference hors de l'axe)
    rad_thr: int = None
    d_hold_rw: float = 0.0        # distance minimale aux pistes (m) : attente, eloignement (et virage ou hippodrome)
    d_out_rw: float = 0.0
    d_int_rw: float = 0.0
    dist_sides: dict = field(default_factory=dict)
    slope: float = SLOPE_DEG
    t_bt: float = 0.0             # helicoptere : duree de l'eloignement a 80 kt (min)
    traversals: list = field(default_factory=list)      # [(segment, piste (indice), hauteur au-dessus de l'AD ELEV en ft)]
    d_strict_rw: float = 0.0      # distance minimale aux pistes du virage de base (ou des virages de l'hippodrome) et de l'intermediaire
    strict_parts: dict = field(default_factory=dict)    # nom -> distance minimale (m)

    def out_alt(self, p):
        """Altitude (ft) du profil de l'eloignement au point p, comme sur la page : de l'attente a la verticale de la balise a
        l'altitude initiale au IF, lineaire en distance au seuil le long de l'axe (bornee aux deux altitudes)."""
        if self.famille == 'helico':
            return float(self.zone_ft)
        d = -along_cross(p, self.e.thr, self.e.az)[0] / NM
        d_v = -self.v_par_nm
        if abs(self.if_nm - d_v) < 1e-9:
            return float(self.init_ft)
        a = self.hold_ft + (self.init_ft - self.hold_ft) * (d - d_v) / (self.if_nm - d_v)
        return float(min(max(a, self.init_ft), self.hold_ft))

    def gp_alt(self, d_nm):
        """Altitude (ft) du plan de descente a d NM avant le seuil."""
        e = self.e
        if self.famille == 'helico':
            return e.thr_ft + self.tch + d_nm * NM * FT * math.tan(math.radians(self.slope))
        if self.ils:
            return e.thr_ft + (d_nm * NM + self.s_gs) * TAN3 * FT
        return e.thr_ft + self.tch + d_nm * NM * TAN3 * FT

    def dme(self, p):
        return math.dist(self.e.ref.pos, p) / NM


def crossings(pts, polys, alt_fn):
    """[(piste, altitude minimale en ft)] des traversees de la polyligne `pts` sur les polygones des pistes ; l'altitude est celle
    de `alt_fn` aux extremites des morceaux de la polyligne dans le polygone (elle varie lineairement le long d'un segment)."""
    g = LineString(pts)
    out = []
    for k, poly in enumerate(polys):
        inter = g.intersection(poly)
        if inter.is_empty:
            continue
        coords = []
        for part in getattr(inter, 'geoms', [inter]):
            coords.extend(part.coords)
        out.append((k, min(alt_fn(c) for c in coords)))
    return out


def check_runways(P, polys):
    """Addendum 1 : traversees de l'attente, de l'eloignement ou de l'hippodrome (admises a AD ELEV + 1 000 ft au moins) et
    distances aux pistes du virage de base, des virages de l'hippodrome et de l'intermediaire (aucune traversee admise)."""
    elev = P.e.elev_ft
    hold_alt = lambda c: float(P.hold_ft)
    if P.mode == 'base':
        segs = [('attente', P.hold_pts, hold_alt), ('éloignement', [P.e.ref.pos, P.T], P.out_alt)]
        strict = {'virage de base': P.arc}
    else:
        segs = [('hippodrome', [P.e.ref.pos] + P.a1 + [P.T] + P.a2, P.out_alt)]
        strict = {'virage d\'entrée de l\'hippodrome': P.a2, 'premier virage de l\'hippodrome': P.a1}
    if P.famille == 'avion':
        strict['intermédiaire'] = [P.IF, P.FAF]
    P.traversals = []
    for name, pts, fn in segs:
        for k, a in crossings(pts, polys, fn):
            P.traversals.append((name, k, a - elev))
    P.strict_parts = {n: dist_runways(pts, polys) for n, pts in strict.items()}
    P.d_strict_rw = min(P.strict_parts.values())


def _hold_pick(beacon, inbound_az, leg_m, r, polys):
    cands = {hs: alignment_hold(beacon, inbound_az, leg_m, r, hs) for hs in (1, -1)}
    d = {hs: dist_runways(cands[hs][0], polys) for hs in (1, -1)}
    hs = 1 if d[1] + TIE_M >= d[-1] else -1                         # a egalite (1 m pres), virages a droite
    return hs, cands[hs], d


def avion(e, alt, x0, z0):
    """La procedure avion du sens `e` (Entree) : Procedure ; GeometrieImpossible si le schema ne se construit pas."""
    polys = runway_polygons(e.runways)
    ref = e.ref.pos
    az = e.az
    uf = unit(az)
    crs = mag_int(az, e.decl)
    v_par_m, v_lat = along_cross(ref, e.thr, az)
    v_par = v_par_m / NM
    mode = 'racetrack' if v_par < 0 and abs(v_lat) < AXIS_TOL_NM * NM else 'base'
    ils = e.ils is not None
    s_gs = float(e.ils['s_gs']) if ils else 0.0
    tch = int(e.ils['tch']) if ils else TCH_DEFAULT_FT
    P = Procedure('avion', e, mode, '', '', crs, 0, [], [], 0.0, 0.0, tch=tch, s_gs=s_gs, ils=ils, v_par_nm=v_par, v_lat_m=v_lat)
    cor = P.corridors
    cor['int'] = corridor(alt, x0, z0, [e.thr, axis_point(e.thr, az, INT_AXIS_NM)], HALF['int'])
    cor['fin'] = corridor(alt, x0, z0, [e.thr, axis_point(e.thr, az, FIN_AXIS_NM)], HALF['fin'])
    P.int_ft = cor['int'][1]
    if ils:
        d_faf = (P.int_ft - e.thr_ft) / (TAN3 * FT * NM) - s_gs / NM
    else:
        d_faf = (P.int_ft - e.thr_ft - tch) / (TAN3 * FT * NM)
    if d_faf <= 0:
        raise GeometrieImpossible('FAF au-dela du seuil')
    if e.ref.dme:
        P.faf_dme = round(math.dist(ref, axis_point(e.thr, az, d_faf)) / NM, 1)
        snap = axis_at_distance(e.thr, az, ref, P.faf_dme, d_faf)
        if snap is None:
            raise GeometrieImpossible(f'FAF : aucun point de l\'axe a D{P.faf_dme:.1f}')
        d_faf = snap
    else:
        d_faf = round(d_faf, 1)
    P.faf_nm, P.if_nm = d_faf, d_faf + INT_NM
    P.FAF, P.IF = axis_point(e.thr, az, P.faf_nm), axis_point(e.thr, az, P.if_nm)
    R = turn_radius_m(V_TURN_KT)
    if mode == 'base':
        cands, d = {}, {}
        for sg in (1, -1):
            try:
                cands[sg] = base_turn(ref, P.IF, az, R, sg)
                d[sg] = dist_runways([ref, cands[sg][3]], polys)
            except GeometrieImpossible:
                pass
        if not cands:
            raise GeometrieImpossible('balise dans le cercle du virage de base, des deux cotes')
        sg = (side_by_distance(d[1], d[-1]) if len(cands) == 2 else next(iter(cands)))
        P.dist_sides = {('LEFT' if k > 0 else 'RIGHT'): round(v) for k, v in d.items()}
        P.C, h, L, P.T, P.arc = cands[sg]
        P.turn = 'LEFT' if sg > 0 else 'RIGHT'
        P.out_pts = [ref, P.T] + P.arc
        P.out_az = h
        hs, (pts, ob, hb, ha), dh = _hold_pick(ref, h, HOLD_LEG_M, HOLD_R_M, polys)
        P.hold_pts, P.hold_ob, P.hold_hb, P.hold_ha = pts, ob, hb, ha
        P.hold_turns = 'RIGHT TURNS' if hs > 0 else 'LEFT TURNS'
        P.hold_inb_az = h
        P.qdr_out = mag_int(h, e.decl)
        P.l_out_nm = L / NM
        if e.ref.dme:
            P.out_dme = round(math.dist(ref, P.T) / NM, 1)
    else:
        cands, d = {}, {}
        for hs in (1, -1):
            cands[hs] = racetrack(ref, P.IF, az, R, hs)
            d[hs] = dist_runways([cands[hs][0][-1], cands[hs][1]], polys)
        hs = 1 if d[1] + TIE_M >= d[-1] else -1
        P.dist_sides = {('RIGHT' if k > 0 else 'LEFT'): round(v) for k, v in d.items()}
        P.a1, o_end, P.a2 = cands[hs]
        if along_cross(o_end, e.thr, az)[0] >= along_cross(P.a1[-1], e.thr, az)[0]:
            raise GeometrieImpossible('hippodrome : la balise est au-dela du IF, eloignement de longueur nulle')
        P.T = o_end
        P.turn = 'RIGHT' if hs > 0 else 'LEFT'
        P.hold_turns = P.turn + ' TURNS'
        P.out_pts = [ref] + P.a1 + [o_end] + P.a2
        P.hold_pts = P.out_pts
        P.hold_inb_az = az
        P.out_az = (az + 180.0) % 360.0
        P.qdr_out = mag_int(az + 180.0, e.decl)
        P.l_out_nm = math.dist(P.a1[-1], o_end) / NM
        if e.ref.dme:
            P.out_dme = round(math.dist(ref, o_end) / NM, 1)
    P.d_hold_rw = dist_runways(P.hold_pts, polys)
    P.d_out_rw = dist_runways(P.out_pts, polys)
    P.d_int_rw = dist_runways([P.IF, P.FAF], polys)
    cor['hold'] = corridor(alt, x0, z0, P.hold_pts, HALF['hold'])
    cor['out'] = corridor(alt, x0, z0, P.out_pts, HALF['out'])
    P.mis_direct = v_par > AHEAD_NM
    P.mis_right = v_lat > 0
    P.mis_pts = ([e.thr, ref, offset(ref, az, NM)] if P.mis_direct
                 else [e.thr, offset(e.thr, az, MIS_STRAIGHT_NM * NM), ref])
    cor['mis'] = corridor(alt, x0, z0, P.mis_pts, HALF['mis'])
    P.init_ft = max(cor['out'][1], P.int_ft)
    P.msa = msa.msa_terrain(alt, x0, z0, ref[0], ref[1], e.gamma, e.decl)
    sect = sector_of(centroid(P.hold_pts), ref, e.gamma, e.decl)
    P.hold_msa = P.msa['sectors'][sect]['msa_ft']
    P.hold_ft = max(P.hold_msa, cor['hold'][1], P.init_ft)
    P.mis_ft = max(cor['mis'][1], P.init_ft)
    P.t_ab, P.t_cd = P.l_out_nm / CAT_AB_KT * 60.0, P.l_out_nm / CAT_CD_KT * 60.0
    if e.ref.dme:
        P.if_dme, P.thr_dme = round(P.dme(P.IF), 1), round(P.dme(e.thr), 1)
    ndb = e.ndb if e.ndb is not None else (e.ref if e.ref.kind == 'NDB' else None)
    if ndb is not None:
        P.qdm_faf = mag_int(az_of(P.FAF, ndb.pos), e.decl)
        P.qdm_thr = mag_int(az_of(e.thr, ndb.pos), e.decl)
    if e.ref.kind != 'NDB' and abs(v_lat) > AXIS_TOL_NM * NM:
        P.rad_faf = mag_int(az_of(ref, P.FAF), e.decl)
        P.rad_thr = mag_int(az_of(ref, e.thr), e.decl)
    check_runways(P, polys)
    return P


def helico(e, alt, x0, z0):
    """La procedure helicoptere (NDB seul) du sens `e` : Procedure ; GeometrieImpossible si le schema ne se construit pas."""
    polys = runway_polygons(e.runways)
    ndb = e.ref.pos
    az = e.az
    crs = mag_int(az, e.decl)
    v_par_m, v_lat = along_cross(ndb, e.thr, az)
    tch = int(e.ils['tch']) if e.ils is not None else TCH_DEFAULT_FT
    P = Procedure('helico', e, 'base', '', '', crs, 0, [], [], 0.0, 0.0, tch=tch, v_par_nm=v_par_m / NM, v_lat_m=v_lat)
    R = turn_radius_m(HELI_KT)
    P.faf_nm = HELI_FAF_NM
    P.FAF = axis_point(e.thr, az, HELI_FAF_NM)
    cands, d = {}, {}
    for sg in (1, -1):
        try:
            cands[sg] = base_turn(ndb, P.FAF, az, R, sg)
            d[sg] = dist_runways([ndb, cands[sg][3]], polys)
        except GeometrieImpossible:
            pass
    if not cands:
        raise GeometrieImpossible('NDB dans le cercle du virage de base, des deux cotes')
    sg = side_by_distance(d[1], d[-1]) if len(cands) == 2 else next(iter(cands))
    P.dist_sides = {('LEFT' if k > 0 else 'RIGHT'): round(v) for k, v in d.items()}
    P.C, h, L, P.T, P.arc = cands[sg]
    P.turn = 'LEFT' if sg > 0 else 'RIGHT'
    P.out_pts = [ndb, P.T] + P.arc
    P.out_az = h
    hs, (pts, ob, hb, ha), _ = _hold_pick(ndb, h, HELI_KT / 60.0 * HELI_HOLD_MIN * NM, R, polys)
    P.hold_pts, P.hold_ob, P.hold_hb, P.hold_ha = pts, ob, hb, ha
    P.hold_turns = 'RIGHT TURNS' if hs > 0 else 'LEFT TURNS'
    P.hold_inb_az = h
    P.qdr_out = mag_int(h, e.decl)
    P.l_out_nm = L / NM
    P.t_bt = P.l_out_nm / HELI_KT * 60.0
    P.d_hold_rw = dist_runways(P.hold_pts, polys)
    P.d_out_rw = dist_runways(P.out_pts, polys)
    cor = P.corridors
    cor['hold'] = corridor(alt, x0, z0, P.hold_pts, HALF_HELI['hold'])
    cor['out'] = corridor(alt, x0, z0, P.out_pts, HALF_HELI['out'])
    cor['fin'] = corridor(alt, x0, z0, [P.FAF, e.thr], HALF_HELI['fin'])
    P.mis_direct = v_par_m > AHEAD_NM * NM
    P.mis_right = v_lat > 0
    P.mis_pts = ([e.thr, ndb, offset(ndb, az, NM)] if P.mis_direct
                 else [e.thr, offset(e.thr, az, HELI_MIS_STRAIGHT_NM * NM), ndb])
    cor['mis'] = corridor(alt, x0, z0, P.mis_pts, HALF_HELI['mis'])
    P.zone_ft = max(cor['hold'][1], cor['out'][1], cor['mis'][1])
    P.hold_ft = P.init_ft = P.int_ft = P.mis_ft = P.zone_ft
    P.msa = msa.msa_terrain(alt, x0, z0, ndb[0], ndb[1], e.gamma, e.decl)
    P.hold_msa = P.msa['sectors'][sector_of(centroid(P.hold_pts), ndb, e.gamma, e.decl)]['msa_ft']
    rise = P.zone_ft - e.thr_ft - tch
    if rise <= 0:
        raise GeometrieImpossible('altitude de la zone sous le seuil + TCH')
    P.slope = math.degrees(math.atan(rise / (HELI_FAF_NM * NM * FT)))
    P.qdm_faf = mag_int(az_of(P.FAF, ndb), e.decl)
    P.qdm_thr = mag_int(az_of(e.thr, ndb), e.decl)
    check_runways(P, polys)
    return P
