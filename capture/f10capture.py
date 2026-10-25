"""Capture F10 (DCS) d'une base entiere, depuis l'emprise du .rn4 (voir CADRAGE_CAPTURE_F10_v1.md).

Usage (python -I f10capture.py ...) :
  index   <Theatre>                      -> out/<theatre>/index.json
  capture <Theatre> <Base> [<Base> ...]  -> out/<theatre>/<Base>/{tiles/,tiles.json,mosaic.png,base.json}
  modes   <Theatre> <Base> [map|alt|sat] -> out/<theatre>/<Base>/modes/<mode>.png (une capture par mode ;
                                            sans mode : les trois, avec attente de Entree entre chaque)
  theatre <Theatre> [--force]            -> capture de tout l'index (bases et helistations), voir CADRAGE_CAPTURE_SYRIA_v1.md
  disk    <Theatre>                      -> volume estime des entrees restantes et place libre du disque
Option : --config <chemin> (sinon variable DCS_CHARTS_CONFIG, sinon capture/config.json ; voir config.example.json)
"""
import ctypes
import json
import math
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I n'ajoute pas le dossier du script
from config import load_config

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HOST, PORT = '127.0.0.1', 47123
CFG = None             # configuration (config.py), fixee par init() avant toute commande
MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 0, 36, 90, 0
ALT_MIN = 1350.0
ALT_ABOVE_GROUND = 700.0
MPP_PER_ALT_M = 0.00033555
CALIB_STEP_M = 200.0
CALIB_SPREAD_MAX = 0.005     # ecart max d'une mesure a la moyenne
CALIB_ALIGN_MAX_DEG = 0.5
CALIB_PRED_MAX = 0.02        # ecart max de la moyenne a l'echelle prevue
OVERLAP = 0.20
MARGIN_ZONE_M = 400.0
STAB_FRAC = 0.005      # 0,5 % des pixels
PIX_THR = 8            # seuil par pixel (sur 255)
POLL_S = 0.1
TIMEOUT_S = 10.0
KEEPALIVE_S = 2.0      # le hook relache la camera apres 5 s sans requete
EST_PER_CAPTURE_S = 1.03
MIN_RN4_BYTES = 5 * 1024
N_CALIB_IMAGES = 6           # 2 coins x 3 images
MB_PER_AIRBASE, MB_PER_HELIPORT = 60, 5
DISK_MARGIN = 1.5
MODES = ('map', 'alt', 'sat')
GEOREF_VERSION = 2     # 2 : mosaic_coverage_m corrigee du recadrage de l'ecran (lot rendu-v1)


# ---------- hook ----------
def hook(line):
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.settimeout(5)
        s.sendall((line + '\n').encode())
        buf = b''
        while not buf.endswith(b'\n'):
            d = s.recv(256)
            if not d:
                break
            buf += d
    return buf.decode().strip()


_last_send = 0.0


def hook_xz(x, z, alt):
    global _last_send
    r = hook(f'xz {x:.3f} {z:.3f} {alt:.1f}').split()
    _last_send = time.perf_counter()
    if r[0] != 'ok':
        raise RuntimeError('hook: ' + ' '.join(r))
    return float(r[1]), float(r[2]), float(r[3])


def hook_release():
    try:
        hook('release')
    except Exception:
        pass


# ---------- synchronisation par fichiers (ADDENDUM 1) ----------
SYNC = None            # work_dir / 'sync', fixe par init()
SYNC_POLL_S = 5.0


def init(argv):
    """Charge la configuration (chemins, ecran) ; retourne argv sans l'option --config."""
    global CFG, SYNC
    CFG, rest = load_config(argv)
    SYNC = CFG.work_dir / 'sync'
    return rest


def set_status(state):
    SYNC.mkdir(parents=True, exist_ok=True)
    (SYNC / 'STATUS.txt').write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {state}\n", encoding='utf-8')


def wait_file(name):
    """Attend sync/<name>.txt (poll 5 s, sans limite), le lit, le supprime, retourne son contenu."""
    p = SYNC / f'{name}.txt'
    while not p.exists():
        time.sleep(SYNC_POLL_S)
    time.sleep(0.3)
    txt = p.read_text(encoding='utf-8', errors='replace')
    p.unlink()
    return txt


# ---------- fenetre DCS au premier plan ----------
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32
_u32.GetForegroundWindow.restype = ctypes.c_void_p
_u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u32.IsIconic.argtypes = [ctypes.c_void_p]
_u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


def _win_info(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetWindowTextW(hwnd, buf, 256)
    pid = ctypes.c_ulong()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ''
    h = _k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        n = ctypes.c_ulong(260)
        b = ctypes.create_unicode_buffer(260)
        if _k32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
            exe = Path(b.value).name.lower()
        _k32.CloseHandle(h)
    return buf.value, exe


def _is_dcs(hwnd):
    title, exe = _win_info(hwnd)
    return exe == 'dcs.exe' and ('Digital Combat Simulator' in title or 'DCS' in title)


def find_dcs_window():
    found = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd) and _is_dcs(hwnd):
            found.append(hwnd)
        return True
    _u32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else None


def dcs_in_front():
    hwnd = _u32.GetForegroundWindow()
    return bool(hwnd) and _is_dcs(hwnd)


def bring_dcs_front():
    """Remet DCS au premier plan. Retourne True si DCS est devant apres tentative."""
    hwnd = find_dcs_window()
    if not hwnd:
        return False
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)   # SW_RESTORE
    _u32.keybd_event(0x12, 0, 0, 0)       # Alt enfonce/relache : autorise SetForegroundWindow
    _u32.keybd_event(0x12, 0, 2, 0)
    _u32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    return dcs_in_front()


class ForegroundFailure(Exception):
    pass


_fg_incidents = []
_fg_fail_streak = 0


def ensure_dcs_front(context=''):
    """Verifie DCS devant avant une capture ; sinon le remet (note l'incident). 3 echecs de suite : ForegroundFailure."""
    global _fg_fail_streak
    if dcs_in_front():
        _fg_fail_streak = 0
        return
    ok = bring_dcs_front()
    _fg_incidents.append({'timestamp': datetime.now().isoformat(timespec='milliseconds'), 'context': context,
                          'refocused': ok})
    if ok:
        _fg_fail_streak = 0
    else:
        _fg_fail_streak += 1
        if _fg_fail_streak >= 3:
            raise ForegroundFailure('DCS ne passe pas au premier plan (3 echecs de suite)')


# ---------- capture ----------
_sct = None
_last = None
_screen = None


def screen_size():
    """Taille d'ecran de la configuration ; sinon celle de l'ecran principal (mss), lue a la premiere demande."""
    global _sct, _screen
    if _screen is None:
        if CFG.screen is not None:
            _screen = CFG.screen
        else:
            if _sct is None:
                import mss
                _sct = mss.mss()
            m = _sct.monitors[1]
            _screen = (m['width'], m['height'])
    return _screen


def grab():
    """Ecran principal complet, RGB."""
    global _sct
    if _sct is None:
        import mss
        _sct = mss.mss()
    shot = _sct.grab(_sct.monitors[1])
    img = np.asarray(shot)[:, :, :3][:, :, ::-1].copy()   # BGRA -> RGB
    screen = screen_size()
    if (img.shape[1], img.shape[0]) != screen:
        raise AskCdP(f'ARRET : ecran {img.shape[1]}x{img.shape[0]} au lieu de {screen[0]}x{screen[1]}')
    return img


def crop(img):
    h, w = img.shape[:2]
    return img[MARGIN_T:h - MARGIN_B, MARGIN_L:w - MARGIN_R]


def diff_frac(a, b):
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > PIX_THR).mean())


def move_and_stabilize(x, z, alt, expect_change=True):
    """Comme _move_and_stabilize, en s'assurant que DCS est au premier plan avant et apres (sinon on refait)."""
    global _fg_fail_streak
    while True:
        ensure_dcs_front('avant capture')
        res = _move_and_stabilize(x, z, alt, expect_change)
        if dcs_in_front():
            return res
        _fg_incidents.append({'timestamp': datetime.now().isoformat(timespec='milliseconds'),
                              'context': 'DCS perdu pendant la capture, image rejetee', 'refocused': False})
        _fg_fail_streak += 1
        if _fg_fail_streak >= 3:
            raise ForegroundFailure('DCS perd le premier plan pendant la capture (3 echecs de suite)')


def _move_and_stabilize(x, z, alt, expect_change=True):
    """Commande la camera, attend l'image stable. Retourne (image pleine, delai_s, n_images, stable, (x,z,alt))."""
    global _last
    if _last is None:
        _last = grab()
    prev = _last
    t0 = time.perf_counter()
    cmd = hook_xz(x, z, alt)
    seen_change = not expect_change
    n = 0
    while True:
        t_frame = time.perf_counter()
        cur = grab()
        n += 1
        d = diff_frac(prev, cur)
        prev = cur
        elapsed = time.perf_counter() - t0
        if d > STAB_FRAC:
            seen_change = True
        elif seen_change and (expect_change or elapsed >= 0.3):
            _last = cur
            return cur, elapsed, n, True, cmd
        if elapsed > TIMEOUT_S:
            _last = cur
            return cur, elapsed, n, False, cmd
        if time.perf_counter() - _last_send > KEEPALIVE_S:
            hook_xz(x, z, alt)
        time.sleep(max(0.0, POLL_S - (time.perf_counter() - t_frame)))


def save_png(img, path):
    from PIL import Image
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, format='PNG')


def load_png(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert('RGB'))


# ---------- emprise .rn4 ----------
def _valid(x, y, z):
    with np.errstate(invalid='ignore'):
        return (np.isfinite(x) & np.isfinite(y) & np.isfinite(z) & (y > -500) & (y < 9000)
                & (np.abs(x) > 1000) & (np.abs(x) < 3e6) & (np.abs(z) > 1000) & (np.abs(z) < 3e6))


def is_heliport_file(path):
    return Path(path).stat().st_size <= MIN_RN4_BYTES


def rn4_points(path):
    """Points retenus d'un .rn4 (cadrage 3.3), tableau (N, 3) de colonnes x, y, z ; (0, 3) si aucun point.
    Fichier de helistation (<= 5 Ko) : chaque triplet valide est un point, sans test du triplet suivant."""
    data = Path(path).read_bytes()
    n = len(data)
    heli = n <= MIN_RN4_BYTES
    px, py, pz = [], [], []
    for a in range(8):
        m = (n - a) // 8
        if m < (3 if heli else 6):
            continue
        arr = np.frombuffer(data, dtype='<f8', count=m, offset=a)
        if heli:
            x, y, z = arr[:m - 2], arr[1:m - 1], arr[2:m]
            with np.errstate(all='ignore'):
                keep = _valid(x, y, z)
            px.append(x[keep])
            py.append(y[keep])
            pz.append(z[keep])
            continue
        x, y, z = arr[:m - 5], arr[1:m - 4], arr[2:m - 3]
        x2, y2, z2 = arr[3:m - 2], arr[4:m - 1], arr[5:m]
        with np.errstate(all='ignore'):
            keep = (_valid(x, y, z) & _valid(x2, y2, z2)
                    & (np.abs(x2 - x) < 3000) & (np.abs(z2 - z) < 3000) & (np.abs(y2 - y) < 200))
        px.append(x[keep])
        py.append(y[keep])
        pz.append(z[keep])
    px = np.concatenate(px) if px else np.empty(0)
    py = np.concatenate(py) if py else np.empty(0)
    pz = np.concatenate(pz) if pz else np.empty(0)
    if px.size == 0:
        return np.empty((0, 3))
    mx, mz = np.median(px), np.median(pz)
    sel = (np.abs(px - mx) < 6000) & (np.abs(pz - mz) < 6000)
    return np.stack([px[sel], py[sel], pz[sel]], axis=1)


def rn4_extent(path):
    """Emprise d'un .rn4 (cadrage 3.3). Retourne dict ou None si aucun point."""
    pts = rn4_points(path)
    if pts.shape[0] == 0:
        return None
    px, py, pz = pts[:, 0], pts[:, 1], pts[:, 2]
    x0, x1, z0, z1 = float(px.min()), float(px.max()), float(pz.min()), float(pz.max())
    return {'x_min': x0, 'x_max': x1, 'z_min': z0, 'z_max': z1, 'y_max': float(py.max()),
            'center': [(x0 + x1) / 2, (z0 + z1) / 2], 'size_m': [x1 - x0, z1 - z0], 'n_points': int(px.size)}


def cam_params(ext):
    """Addendum 2 : altitude camera de la base = max(1350, y_max + 700) ; echelle prevue = alt x 0,00033555 m/px."""
    alt = max(ALT_MIN, ext['y_max'] + ALT_ABOVE_GROUND)
    return alt, alt * MPP_PER_ALT_M


# ---------- grille ----------
def usable_px():
    w, h = screen_size()
    return w - MARGIN_L - MARGIN_R, h - MARGIN_T - MARGIN_B


def zone_of(ext):
    return {'x_min': ext['x_min'] - MARGIN_ZONE_M, 'x_max': ext['x_max'] + MARGIN_ZONE_M,
            'z_min': ext['z_min'] - MARGIN_ZONE_M, 'z_max': ext['z_max'] + MARGIN_ZONE_M}


def plan_grid(zone, mpp):
    """Grille de tuiles couvrant la zone. Ligne 0 = nord. Retourne dict (empreinte, pas, centres)."""
    uw, uh = usable_px()
    fw, fh = uw * mpp, uh * mpp                # empreinte au sol : fw selon z (Est), fh selon x (Nord)
    stw, sth = fw * (1 - OVERLAP), fh * (1 - OVERLAP)
    zx, zz = zone['x_max'] - zone['x_min'], zone['z_max'] - zone['z_min']
    ncol = 1 if fw >= zz else math.ceil((zz - fw) / stw) + 1
    nrow = 1 if fh >= zx else math.ceil((zx - fh) / sth) + 1
    cx, cz = (zone['x_min'] + zone['x_max']) / 2, (zone['z_min'] + zone['z_max']) / 2
    xs = [cx + ((nrow - 1) / 2 - i) * (sth if nrow > 1 else 0) for i in range(nrow)]
    zs = [cz + (j - (ncol - 1) / 2) * (stw if ncol > 1 else 0) for j in range(ncol)]
    return {'nrow': nrow, 'ncol': ncol, 'footprint_m': [fh, fw], 'step_m': [sth, stw], 'xs': xs, 'zs': zs}


# ---------- commandes ----------
def rn4_dir(theatre):
    d = CFG.terrains / theatre / 'AirfieldsTaxiways'
    if not d.is_dir():
        raise SystemExit(f'ARRET : dossier introuvable {d}')
    return d


def out_theatre(theatre):
    return CFG.work_dir / 'out' / theatre.lower()


def find_rn4(theatre, base):
    d = rn4_dir(theatre)
    p = d / f'{base}.rn4'
    if p.is_file():
        return p
    for q in d.glob('*.rn4'):
        if q.stem.lower() == base.lower():
            return q
    raise SystemExit(f'ARRET : {base}.rn4 introuvable dans {d}')


def visit_order(entries):
    """Plus proche voisin depuis l'entree la plus au nord-ouest (x - z maximal : x = Nord, z = Est).
    Les entrees sans point (pas de centre) sont placees a la fin."""
    pts = [e for e in entries if 'center' in e]
    rest = [e['name'] for e in entries if 'center' not in e]
    if not pts:
        return rest
    cur = max(pts, key=lambda e: (e['center'][0] - e['center'][1], e['name']))
    pts.remove(cur)
    order = [cur['name']]
    while pts:
        cx, cz = cur['center']
        cur = min(pts, key=lambda e: (math.hypot(e['center'][0] - cx, e['center'][1] - cz), e['name']))
        pts.remove(cur)
        order.append(cur['name'])
    return order + rest


def cmd_index(theatre):
    airbases, heliports = [], []
    for p in sorted(rn4_dir(theatre).glob('*.rn4'), key=lambda q: q.name.lower()):
        size = p.stat().st_size
        kind = 'heliport' if is_heliport_file(p) else 'airbase'
        ext = rn4_extent(p)
        if ext is None:
            e = {'name': p.stem, 'kind': kind, 'file_bytes': size, 'erreur': 'aucun point valide'}
        else:
            alt, mpp = cam_params(ext)
            g = plan_grid(zone_of(ext), mpp)
            n = g['nrow'] * g['ncol']
            e = {'name': p.stem, 'kind': kind, 'file_bytes': size, 'n_points_rn4': ext['n_points'],
                 'alt_cmd': alt, 'y_max_rn4': ext['y_max'], 'm_per_px_predicted': round(mpp, 5),
                 'center': ext['center'], 'extent': {k: ext[k] for k in ('x_min', 'x_max', 'z_min', 'z_max')},
                 'size_m': ext['size_m'], 'grid': [g['nrow'], g['ncol']], 'n_captures': n,
                 'n_calib_images': N_CALIB_IMAGES if kind == 'airbase' else 0,
                 'est_duration_s': round(n * EST_PER_CAPTURE_S, 1)}
        (airbases if kind == 'airbase' else heliports).append(e)
    ok_a = [e for e in airbases if 'erreur' not in e]
    ok_h = [e for e in heliports if 'erreur' not in e]
    grid_a, grid_h = sum(e['n_captures'] for e in ok_a), sum(e['n_captures'] for e in ok_h)
    calib = N_CALIB_IMAGES * len(ok_a)
    res = {'theatre': theatre, 'rule': 'cadrage 3.3/3.4 + addendum 2 (altitude par base, echelle prevue) + capture-syria-v1',
           'alt_rule': f'max({ALT_MIN:.0f}, y_max + {ALT_ABOVE_GROUND:.0f})', 'm_per_px_per_alt_m': MPP_PER_ALT_M,
           'n_bases': len(airbases), 'n_heliports': len(heliports),
           'n_entries_without_point': len(airbases) + len(heliports) - len(ok_a) - len(ok_h),
           'total_n_captures_airbases': grid_a, 'total_n_captures_heliports': grid_h,
           'total_n_captures': grid_a + grid_h, 'total_n_calib_images': calib,
           'total_n_images': grid_a + grid_h + calib,
           'est_s_per_image': EST_PER_CAPTURE_S,
           'total_est_duration_s': round((grid_a + grid_h + calib) * EST_PER_CAPTURE_S, 1),
           'order': visit_order(ok_a + ok_h) + [e['name'] for e in airbases + heliports if 'erreur' in e],
           'bases': airbases, 'heliports': heliports}
    out = out_theatre(theatre)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(f"{theatre}: {len(airbases)} bases, {len(heliports)} helistations ({res['n_entries_without_point']} sans point), "
          f"{res['total_n_captures']} images de grille ({grid_a} bases + {grid_h} helistations) + {calib} d'etalonnage "
          f"= {res['total_n_images']} images, ~{res['total_est_duration_s'] / 60:.1f} min")
    for e in airbases + heliports:
        if 'erreur' in e:
            print('  ERREUR', e['name'], e['kind'], e['erreur'])


def corrected_coverage(cover, mpp):
    """Georeferencement (cadrage 4.1) : la mosaique place chaque image comme si son centre etait la camera, alors que le
    recadrage de l'ecran n'est pas centre (marges haut M_T, droite M_R). Le contenu est donc decale : x_min et x_max
    diminuent de (M_T - M_B)/2 x mpp, z_min et z_max de (M_R - M_L)/2 x mpp."""
    dx = (MARGIN_T - MARGIN_B) / 2 * mpp
    dz = (MARGIN_R - MARGIN_L) / 2 * mpp
    return {'x_min': cover['x_min'] - dx, 'x_max': cover['x_max'] - dx,
            'z_min': cover['z_min'] - dz, 'z_max': cover['z_max'] - dz}


def build_mosaic(tiles, fw, fh, odir, mpp):
    x_top = max(t['x'] for t in tiles) + fh / 2
    z_left = min(t['z'] for t in tiles) - fw / 2
    W = int(round((max(t['z'] for t in tiles) + fw / 2 - z_left) / mpp))
    H = int(round((x_top - (min(t['x'] for t in tiles) - fh / 2)) / mpp))
    canvas = np.zeros((H, W, 3), np.uint8)
    for t in tiles:
        im = load_png(odir / 'tiles' / f"{t['tile']}.png")
        px = int(round((t['z'] - fw / 2 - z_left) / mpp))
        py = int(round((x_top - (t['x'] + fh / 2)) / mpp))
        hh, ww = min(im.shape[0], H - py), min(im.shape[1], W - px)
        canvas[py:py + hh, px:px + ww] = im[:hh, :ww]
    save_png(canvas, odir / 'mosaic.png')
    return W, H, x_top, z_left


class AskCdP(Exception):
    """Arret necessitant une QUESTION au CdP (ADDENDUM 1)."""


class EntryFailure(AskCdP):
    """Echec propre a une entree (etalonnage hors tolerance, image non stable, aucun point) : la commande
    `theatre` l'ecrit dans errors.json et passe a la suivante ; les autres commandes le traitent en QUESTION."""

    def __init__(self, step, message):
        super().__init__(message)
        self.step = step


def phase_shift(a, b):
    """Deplacement (dx, dy) en px du contenu de a vers b : b[y,x] ~ a[y-dy, x-dx]. Retourne aussi le pic."""
    ga = a.astype(np.float32).mean(axis=2)
    gb = b.astype(np.float32).mean(axis=2)
    ga -= ga.mean()
    gb -= gb.mean()
    h, w = ga.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    fa, fb = np.fft.fft2(ga * win), np.fft.fft2(gb * win)
    r = fb * np.conj(fa)
    r /= np.maximum(np.abs(r), 1e-9)
    c = np.fft.ifft2(r).real
    py, px = np.unravel_index(np.argmax(c), c.shape)
    peak = float(c[py, px])

    def sub(cm, c0, cp):
        den = cm - 2 * c0 + cp
        return 0.0 if abs(den) < 1e-12 else 0.5 * (cm - cp) / den

    dx = px + sub(c[py, (px - 1) % w], c[py, px], c[py, (px + 1) % w])
    dy = py + sub(c[(py - 1) % h, px], c[py, px], c[(py + 1) % h, px])
    if dx > w / 2:
        dx -= w
    if dy > h / 2:
        dy -= h
    return float(dx), float(dy), peak


class CalibrationFailed(Exception):
    """Etalonnage en echec (tolerance ou image non stable) : capture_base replie sur l'echelle prevue
    (ADDENDUM 1 capture-syria-v1). `result` : mesures obtenues (dict) ou None."""

    def __init__(self, reason, result=None):
        super().__init__(reason)
        self.result = result


def calibrate(zone, alt, mpp_pred, odir, label, prog=None):
    """Etalonnage (addendum 2) : coins NO puis SE de la zone ; a chaque coin capture, +200 m en x, +200 m en z.
    Retourne dict (mesures, moyenne, alignement). Leve CalibrationFailed si une regle de l'addendum est enfreinte
    ou si une image n'est pas stable."""
    corners = (('nw', zone['x_max'], zone['z_min']), ('se', zone['x_min'], zone['z_max']))
    meas, aligns, peaks, detail = [], [], [], {}

    def shot(x, z):
        if prog:
            prog.step()
        r = move_and_stabilize(x, z, alt)
        if not r[3]:
            raise CalibrationFailed(f'image non stable apres {r[1]:.1f} s (x {x:.0f}, z {z:.0f})')
        return crop(r[0])

    for name, cx, cz in corners:
        if not prog:
            set_status(f'CAPTURE {label} calibration {name.upper()}')
        c0 = shot(cx, cz)
        cxi = shot(cx + CALIB_STEP_M, cz)
        czi = shot(cx, cz + CALIB_STEP_M)
        for tag, im in (('c0', c0), ('cx', cxi), ('cz', czi)):
            save_png(im, odir / 'calib' / f'{name}_{tag}.png')
        ax, ay, p1 = phase_shift(c0, cxi)     # x (Nord) +200 m
        bx, by, p2 = phase_shift(c0, czi)     # z (Est) +200 m
        mx, mz = CALIB_STEP_M / math.hypot(ax, ay), CALIB_STEP_M / math.hypot(bx, by)
        al_x = math.degrees(math.atan2(ax, abs(ay)))
        al_z = math.degrees(math.atan2(by, abs(bx)))
        sens_ok = ay > 0 and bx < 0       # Nord en haut, Est a droite
        meas += [mx, mz]
        aligns += [abs(al_x), abs(al_z)]
        peaks += [p1, p2]
        detail[name] = {'shift_px_x200': [ax, ay], 'shift_px_z200': [bx, by], 'peaks': [p1, p2],
                        'mpp_x': mx, 'mpp_z': mz, 'align_deg_x': al_x, 'align_deg_z': al_z, 'orientation_ok': bool(sens_ok)}
    mean = sum(meas) / len(meas)
    spread = max(abs(m - mean) / mean for m in meas)
    pred_dev = abs(mean - mpp_pred) / mpp_pred
    res = {'measures': meas, 'mean': mean, 'max_spread_rel': spread, 'predicted': mpp_pred,
           'dev_vs_predicted_rel': pred_dev, 'align_deg_max': max(aligns), 'peaks': peaks, 'detail': detail}
    (odir / 'calib.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    problems = []
    if spread > CALIB_SPREAD_MAX:
        problems.append(f'une mesure s\'ecarte de {spread * 100:.2f} % de la moyenne (> 0,5 %)')
    if max(aligns) > CALIB_ALIGN_MAX_DEG:
        problems.append(f'alignement {max(aligns):.3f} deg (> 0,5)')
    if pred_dev > CALIB_PRED_MAX:
        problems.append(f'moyenne {mean:.5f} s\'ecarte de {pred_dev * 100:.2f} % de la prevision {mpp_pred:.5f} (> 2 %)')
    if not all(d['orientation_ok'] for d in detail.values()):
        problems.append('orientation image inattendue (Nord pas en haut / Est pas a droite)')
    if problems:
        msg = (f'Etalonnage {label} : ' + ' ; '.join(problems) + f'. Mesures {[round(m, 5) for m in meas]}, '
               f'moyenne {mean:.5f}, prevue {mpp_pred:.5f}, pics {[round(p, 3) for p in peaks]}. '
               f'Details : {odir / "calib.json"}')
        raise CalibrationFailed(msg, res)
    return res


def capture_base(theatre, base, strict=False, prog=None):
    """Capture d'une entree (base : etalonnee ; helistation : sans etalonnage, echelle prevue).
    Etalonnage en echec (tolerance ou image non stable) : repli sur l'echelle prevue (calibrated false, calib_fallback).
    strict (commande theatre) : aucun point, image de grille non stable -> EntryFailure.
    prog : Progress de la commande theatre (STATUS a chaque image)."""
    rn4 = find_rn4(theatre, base)
    kind = 'heliport' if is_heliport_file(rn4) else 'airbase'
    ext = rn4_extent(rn4)
    if ext is None:
        if strict:
            raise EntryFailure('rn4', f'aucun point valide dans {rn4}')
        raise SystemExit(f'ARRET : aucun point valide dans {rn4}')
    zone = zone_of(ext)
    alt, mpp_pred = cam_params(ext)
    odir = out_theatre(theatre) / rn4.stem
    odir.mkdir(parents=True, exist_ok=True)
    print(f"{rn4.stem} ({kind}): altitude {alt:.0f} m (y_max {ext['y_max']:.0f}), echelle prevue {mpp_pred:.4f} m/px")
    cal, fallback, fallback_meas = None, None, None
    if kind == 'airbase':
        try:
            cal = calibrate(zone, alt, mpp_pred, odir, rn4.stem, prog)
        except CalibrationFailed as f:
            fallback, fallback_meas = str(f), f.result
            print(f"  ETALONNAGE EN ECHEC -> repli sur l'echelle prevue {mpp_pred:.5f} m/px : {fallback}")
    MPP = cal['mean'] if cal else mpp_pred
    if cal:
        print(f"  etalonnage : {[round(m, 4) for m in cal['measures']]} -> {MPP:.5f} m/px "
              f"(ecart max {cal['max_spread_rel'] * 100:.2f} %, vs prevue {cal['dev_vs_predicted_rel'] * 100:.2f} %, "
              f"align {cal['align_deg_max']:.3f} deg)")
    g = plan_grid(zone, MPP)
    if prog:
        prog.set_total((prog.k if fallback else N_CALIB_IMAGES if kind == 'airbase' else 0) + g['nrow'] * g['ncol'])
    fh, fw = g['footprint_m']
    n_total = g['nrow'] * g['ncol']
    print(f"  emprise {ext['size_m'][0]:.0f}x{ext['size_m'][1]:.0f} m, grille {g['nrow']}x{g['ncol']} = {n_total} captures")
    tiles, times = [], []
    T0 = time.perf_counter()
    for i, xx in enumerate(g['xs']):
        for j, zz in enumerate(g['zs']):
            tt = time.perf_counter()
            name = f'r{i}_c{j}'
            if prog:
                prog.step()
            else:
                set_status(f'CAPTURE {rn4.stem} {len(tiles) + 1}/{n_total}')
            n_inc = len(_fg_incidents)
            img, delay, n, ok, cmd = move_and_stabilize(xx, zz, alt)
            if strict and not ok:
                raise EntryFailure('capture', f'image {name} non stable apres {delay:.1f} s (plafond {TIMEOUT_S:.0f} s)')
            tile = crop(img)
            save_png(tile, odir / 'tiles' / f'{name}.png')
            times.append(time.perf_counter() - tt)
            tiles.append({'tile': name, 'x': cmd[0], 'z': cmd[1], 'alt': cmd[2], 'mpp': MPP,
                          'size_px': [tile.shape[1], tile.shape[0]],
                          'stabilization_s': round(delay, 3), 'n_images': n, 'stable': ok,
                          'foreground_incidents': _fg_incidents[n_inc:],
                          'timestamp': datetime.now().isoformat(timespec='milliseconds')})
            print(' ', name, f'{times[-1]:.2f}s', 'stable' if ok else 'NON STABLE')
    total = time.perf_counter() - T0
    (odir / 'tiles.json').write_text(json.dumps(tiles, indent=1), encoding='utf-8')
    W, H, x_top, z_left = build_mosaic(tiles, fw, fh, odir, MPP)
    cover = corrected_coverage({'x_min': x_top - H * MPP, 'x_max': x_top, 'z_min': z_left, 'z_max': z_left + W * MPP},
                               MPP)
    meas = ({'measures': cal['measures'], 'mean': cal['mean'], 'max_spread_rel': cal['max_spread_rel']}
            if cal else None)
    base_json = {'base': rn4.stem, 'theatre': theatre, 'kind': kind, 'calibrated': cal is not None,
                 **({'calib_fallback': fallback,
                     'calib_fallback_measures': fallback_meas and {k: fallback_meas[k] for k in (
                         'measures', 'mean', 'max_spread_rel', 'dev_vs_predicted_rel', 'align_deg_max', 'peaks')}}
                    if fallback else {}),
                 'rn4': str(rn4), 'extent_rn4': ext,
                 'zone_captured': zone, 'mosaic_coverage_m': cover,
                 'zone_fully_covered': bool(cover['x_min'] <= zone['x_min'] and cover['x_max'] >= zone['x_max']
                                            and cover['z_min'] <= zone['z_min'] and cover['z_max'] >= zone['z_max']),
                 'grid': [g['nrow'], g['ncol']], 'footprint_m': [fh, fw], 'step_m': g['step_m'],
                 'alt_cmd': alt, 'y_max_rn4': ext['y_max'], 'm_per_px_predicted': mpp_pred,
                 'm_per_px_measured': meas,
                 'm_per_px': MPP, 'align_deg': cal['align_deg_max'] if cal else None,
                 'georef_version': GEOREF_VERSION,
                 'margins_px_lthr_b': [MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B],
                 'tile_px': [usable_px()[0], usable_px()[1]], 'mosaic_px': [W, H],
                 'n_captures': len(tiles), 'n_not_stable': sum(1 for t in tiles if not t['stable']),
                 'total_s': round(total, 2), 'mean_s': round(sum(times) / len(times), 3),
                 'max_s': round(max(times), 3)}
    (odir / 'base.json').write_text(json.dumps(base_json, indent=1), encoding='utf-8')
    print(f"  total {total:.1f}s, moyenne {base_json['mean_s']}s, max {base_json['max_s']}s, "
          f"non stables {base_json['n_not_stable']}, zone couverte {base_json['zone_fully_covered']}")
    return base_json


def cmd_capture(theatre, bases):
    try:
        for b in bases:
            capture_base(theatre, b)
    finally:
        hook_release()


# ---------- commande theatre (CADRAGE_CAPTURE_SYRIA_v1.md) ----------
def load_index(theatre):
    p = out_theatre(theatre) / 'index.json'
    if not p.is_file():
        raise SystemExit(f'ARRET : {p} introuvable (lancer d\'abord : index {theatre})')
    idx = json.loads(p.read_text(encoding='utf-8'))
    return idx, {e['name']: e for e in idx['bases'] + idx['heliports']}


def entry_done(theatre, name):
    """base.json present et complet (lisible, n_captures renseigne) et mosaique presente."""
    d = out_theatre(theatre) / name
    try:
        bj = json.loads((d / 'base.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(bj.get('n_captures'), int) and (d / 'mosaic.png').is_file()


def entry_images(e):
    return 0 if 'erreur' in e else e['n_captures'] + (N_CALIB_IMAGES if e['kind'] == 'airbase' else 0)


def disk_check(theatre, names, by_name):
    """Estime le volume (60 Mo par base, 5 Mo par helistation) ; AskCdP si la place libre < 1,5 x l'estimation."""
    est = sum((MB_PER_AIRBASE if by_name[n]['kind'] == 'airbase' else MB_PER_HELIPORT) for n in names) * 1024 * 1024
    free = shutil.disk_usage(CFG.work_dir).free
    print(f'volume estime {est / 1e9:.2f} Go, place libre {free / 1e9:.1f} Go (seuil {DISK_MARGIN} x estimation)')
    if free < DISK_MARGIN * est:
        raise AskCdP(f'Place disque insuffisante : libre {free / 1e9:.2f} Go < {DISK_MARGIN} x {est / 1e9:.2f} Go estimes')
    return est, free


def cmd_disk(theatre):
    idx, by_name = load_index(theatre)
    todo = [n for n in idx['order'] if not entry_done(theatre, n)]
    print(f'{len(todo)} entrees a faire sur {len(idx["order"])}')
    disk_check(theatre, todo, by_name)


class Progress:
    """STATUS a chaque image : CAPTURE <i>/<N> <Nom> <n>/<total> | reste ~<min> min (n compte aussi l'etalonnage)."""

    def __init__(self, plan):
        self.plan, self.N = plan, len(plan)
        self.plan_total = sum(plan.values())
        self.i, self.name, self.k, self.entry_total, self.done = 0, '', 0, 0, 0
        self.waited, self.t_first = 0.0, None

    def begin(self, i, name):
        if name == self.name:
            self.done -= self.k             # reprise apres QUESTION : les images de l'essai precedent ne comptent plus
        self.i, self.name, self.k = i, name, 0
        self.entry_total = self.plan[name]

    def set_total(self, total):
        self.plan_total += total - self.entry_total
        self.entry_total = total

    def step(self):
        now = time.perf_counter()
        if self.t_first is None:
            self.t_first = now
        self.k += 1
        self.done += 1
        rate = EST_PER_CAPTURE_S if self.done < 2 else (now - self.t_first - self.waited) / (self.done - 1)
        left = max(0, self.plan_total - self.done + 1) * rate / 60
        set_status(f'CAPTURE {self.i}/{self.N} {self.name} {self.k}/{self.entry_total} | reste ~{left:.0f} min')


def dir_size(d):
    return sum(f.stat().st_size for f in Path(d).rglob('*') if f.is_file())


def log_error(theatre, name, step, message):
    p = out_theatre(theatre) / 'errors.json'
    errs = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else []
    errs = [e for e in errs if e['name'] != name]
    errs.append({'name': name, 'step': step, 'message': message, 'timestamp': datetime.now().isoformat(timespec='seconds')})
    p.write_text(json.dumps(errs, indent=1, ensure_ascii=False), encoding='utf-8')


def clear_error(theatre, name):
    p = out_theatre(theatre) / 'errors.json'
    if p.is_file():
        errs = [e for e in json.loads(p.read_text(encoding='utf-8')) if e['name'] != name]
        p.write_text(json.dumps(errs, indent=1, ensure_ascii=False), encoding='utf-8')


def cmd_theatre(theatre, force=False):
    idx, by_name = load_index(theatre)
    order = idx['order']
    todo = [n for n in order if force or not entry_done(theatre, n)]
    skipped = [n for n in order if n not in todo]
    t_start = datetime.now()
    prog = Progress({n: entry_images(by_name[n]) for n in todo})
    stats = {'done': [], 'failed': [], 'bytes': 0}

    def question(text):
        hook_release()
        ask(text)
        print('QUESTION ecrite :', text)
        t = time.perf_counter()
        ans = wait_file('ANSWER')
        prog.waited += time.perf_counter() - t
        print('ANSWER :', ans.strip())
        return ans

    try:
        disk_check(theatre, todo, by_name)
    except AskCdP as e:
        question(f'{type(e).__name__}: {e}')
    print(f'{len(todo)} entrees a capturer, {len(skipped)} sautees (deja faites), ~{prog.plan_total} images')
    first_airbase_done = False
    try:
        for i, name in enumerate(todo, 1):
            e = by_name[name]
            while True:
                prog.begin(i, name)
                try:
                    if 'erreur' in e:
                        raise EntryFailure('rn4', e['erreur'])
                    capture_base(theatre, name, strict=True, prog=prog)
                    clear_error(theatre, name)
                    stats['done'].append(name)
                    break
                except EntryFailure as f:
                    hook_release()
                    log_error(theatre, name, f.step, str(f))
                    stats['failed'].append(name)
                    print(f'ECHEC {name} ({f.step}) : {f}')
                    break
                except (Exception, SystemExit) as g:      # echec general : le CdP decide, puis on reprend l'entree
                    question(f'{type(g).__name__}: {g} | entree {name} ({i}/{len(todo)}), reprise de l\'entree apres ANSWER')
            hook_release()
            d = out_theatre(theatre) / name
            if d.is_dir():
                stats['bytes'] += dir_size(d)
            if name in stats['done'] and e['kind'] == 'airbase' and not first_airbase_done:
                first_airbase_done = True
                set_status(f'ATTENTE_GO_2 | {name}')
                t = time.perf_counter()
                wait_file('GO')
                prog.waited += time.perf_counter() - t
    finally:
        hook_release()
    t_end = datetime.now()
    wall = (t_end - t_start).total_seconds()
    work = wall - prog.waited
    run = {'theatre': theatre, 'start': t_start.isoformat(timespec='seconds'), 'end': t_end.isoformat(timespec='seconds'),
           'duration_s': round(wall, 1), 'waited_s': round(prog.waited, 1), 'working_duration_s': round(work, 1),
           'n_entries_total': len(order), 'n_entries_done': len(stats['done']), 'n_entries_skipped': len(skipped),
           'n_entries_failed': len(stats['failed']), 'done': stats['done'], 'skipped': skipped, 'failed': stats['failed'],
           'n_images': prog.done, 'mean_s_per_image': round(work / prog.done, 3) if prog.done else None,
           'est_s_per_image': EST_PER_CAPTURE_S, 'est_images_index': prog.plan_total,
           'est_duration_index_s': round(prog.plan_total * EST_PER_CAPTURE_S, 1),
           'bytes_written': stats['bytes'], 'foreground_incidents': len(_fg_incidents), 'force': force}
    (out_theatre(theatre) / 'run.json').write_text(json.dumps(run, indent=1), encoding='utf-8')
    set_status('TERMINE')
    print(f"termine : {len(stats['done'])} faites, {len(skipped)} sautees, {len(stats['failed'])} en echec, "
          f"{prog.done} images, {wall / 60:.1f} min ({stats['bytes'] / 1e6:.0f} Mo)")


def cmd_modes(theatre, base, modes):
    rn4 = find_rn4(theatre, base)
    ext = rn4_extent(rn4)
    if ext is None:
        raise SystemExit(f'ARRET : aucun point valide dans {rn4}')
    cx, cz = ext['center']
    alt = cam_params(ext)[0]
    odir = out_theatre(theatre) / rn4.stem / 'modes'
    try:
        for m in (modes or MODES):
            if not modes:
                input(f'Selectionner le mode {m.upper()} dans la F10, puis Entree... ')
            img, delay, n, ok, cmd = move_and_stabilize(cx, cz, alt)
            save_png(crop(img), odir / f'{m}.png')
            save_png(img, odir / f'{m}_full.png')     # ecran complet, pour verifier les marges (cadrage 3.1)
            print(m, 'stable' if ok else 'NON STABLE', f'{delay:.2f}s', 'centre', cmd)
    finally:
        hook_release()


def cmd_check(theatre, base):
    """ADDENDUM 1 : DCS devant, camera au centre de la base, sync/check.png (ecran complet), camera relachee, ATTENTE_GO."""
    rn4 = find_rn4(theatre, base)
    ext = rn4_extent(rn4)
    cx, cz = ext['center']
    alt = cam_params(ext)[0]
    try:
        ensure_dcs_front('check')
        move_and_stabilize(cx + 500, cz, alt)   # la camera peut deja etre au centre : on force un changement visible
        img, delay, n, ok, cmd = move_and_stabilize(cx, cz, alt)
        save_png(img, SYNC / 'check.png')
        print('check.png', 'stable' if ok else 'NON STABLE', f'{delay:.2f}s', cmd, 'incidents', _fg_incidents)
    finally:
        hook_release()
    set_status('ATTENTE_GO | hook OK, check.png pret' + ('' if ok else ' (NON STABLE)'))


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    cmd, theatre = argv[1], argv[2]
    if cmd == 'wait' and len(argv) == 3:                  # wait GO|ANSWER
        print(wait_file(theatre.upper()))
    elif cmd == 'status' and len(argv) == 3:              # status "<texte>"
        set_status(theatre)
    elif cmd == 'check' and len(argv) == 4:
        cmd_check(theatre, argv[3])
    elif cmd == 'index' and len(argv) == 3:
        cmd_index(theatre)
    elif cmd == 'disk' and len(argv) == 3:
        cmd_disk(theatre)
    elif cmd == 'theatre' and len(argv) in (3, 4) and (len(argv) == 3 or argv[3] == '--force'):
        cmd_theatre(theatre, force=len(argv) == 4)
    elif cmd == 'capture' and len(argv) >= 4:
        cmd_capture(theatre, argv[3:])
    elif cmd == 'modes' and len(argv) >= 4:
        modes = [m.lower() for m in argv[4:]]
        if any(m not in MODES for m in modes):
            raise SystemExit('mode inconnu : ' + ' '.join(modes))
        cmd_modes(theatre, argv[3], modes)
    else:
        raise SystemExit(__doc__)


def ask(text):
    """ADDENDUM 1 : ecrit sync/QUESTION.txt, STATUS = QUESTION ; la reponse est attendue via `wait ANSWER`."""
    SYNC.mkdir(parents=True, exist_ok=True)
    (SYNC / 'QUESTION.txt').write_text(text + '\n', encoding='utf-8')
    set_status('QUESTION')


if __name__ == '__main__':
    _argv = init(sys.argv)
    try:
        main(_argv)
    except (AskCdP, ForegroundFailure, RuntimeError, OSError) as e:
        hook_release()
        ask(f'{type(e).__name__}: {e}')
        print('QUESTION ecrite :', e)
        sys.exit(3)
