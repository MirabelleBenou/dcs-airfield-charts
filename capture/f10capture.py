"""Capture F10 (DCS) d'une base entiere, depuis l'emprise du .rn4 (voir CADRAGE_CAPTURE_F10_v1.md).

Usage (python -I f10capture.py ...) :
  index   <Theatre>                      -> out/<theatre>/index.json
  capture <Theatre> <Base> [<Base> ...]  -> out/<theatre>/<Base>/{tiles/,tiles.json,mosaic.png,base.json}
  modes   <Theatre> <Base> [map|alt|sat] -> out/<theatre>/<Base>/modes/<mode>.png (une capture par mode ;
                                            sans mode : les trois, avec attente de Entree entre chaque)
"""
import ctypes
import json
import math
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HOST, PORT = '127.0.0.1', 47123
ROOT = Path(r'D:\Claude_AI\DCS_Airfield_Charts')
TERRAINS = Path(r'F:\DCS World OpenBeta\Mods\terrains')
SCREEN = (3440, 1440)
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
MODES = ('map', 'alt', 'sat')


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
SYNC = ROOT / 'sync'
SYNC_POLL_S = 5.0


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


def grab():
    """Ecran principal complet, RGB."""
    global _sct
    if _sct is None:
        import mss
        _sct = mss.mss()
    shot = _sct.grab(_sct.monitors[1])
    img = np.asarray(shot)[:, :, :3][:, :, ::-1].copy()   # BGRA -> RGB
    if (img.shape[1], img.shape[0]) != SCREEN:
        raise SystemExit(f'ARRET : ecran {img.shape[1]}x{img.shape[0]} au lieu de {SCREEN[0]}x{SCREEN[1]}')
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


def rn4_extent(path):
    """Emprise d'un .rn4 (cadrage 3.3). Retourne dict ou None si aucun point."""
    data = Path(path).read_bytes()
    n = len(data)
    px, py, pz = [], [], []
    for a in range(8):
        m = (n - a) // 8
        if m < 6:
            continue
        arr = np.frombuffer(data, dtype='<f8', count=m, offset=a)
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
        return None
    mx, mz = np.median(px), np.median(pz)
    sel = (np.abs(px - mx) < 6000) & (np.abs(pz - mz) < 6000)
    px, py, pz = px[sel], py[sel], pz[sel]
    if px.size == 0:
        return None
    x0, x1, z0, z1 = float(px.min()), float(px.max()), float(pz.min()), float(pz.max())
    return {'x_min': x0, 'x_max': x1, 'z_min': z0, 'z_max': z1, 'y_max': float(py.max()),
            'center': [(x0 + x1) / 2, (z0 + z1) / 2], 'size_m': [x1 - x0, z1 - z0], 'n_points': int(px.size)}


def cam_params(ext):
    """Addendum 2 : altitude camera de la base = max(1350, y_max + 700) ; echelle prevue = alt x 0,00033555 m/px."""
    alt = max(ALT_MIN, ext['y_max'] + ALT_ABOVE_GROUND)
    return alt, alt * MPP_PER_ALT_M


# ---------- grille ----------
def usable_px():
    return SCREEN[0] - MARGIN_L - MARGIN_R, SCREEN[1] - MARGIN_T - MARGIN_B


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
    d = TERRAINS / theatre / 'AirfieldsTaxiways'
    if not d.is_dir():
        raise SystemExit(f'ARRET : dossier introuvable {d}')
    return d


def out_theatre(theatre):
    return ROOT / 'out' / theatre.lower()


def find_rn4(theatre, base):
    d = rn4_dir(theatre)
    p = d / f'{base}.rn4'
    if p.is_file():
        return p
    for q in d.glob('*.rn4'):
        if q.stem.lower() == base.lower():
            return q
    raise SystemExit(f'ARRET : {base}.rn4 introuvable dans {d}')


def cmd_index(theatre):
    entries, skipped = [], 0
    for p in sorted(rn4_dir(theatre).glob('*.rn4'), key=lambda q: q.name.lower()):
        if p.stat().st_size <= MIN_RN4_BYTES:
            skipped += 1
            continue
        ext = rn4_extent(p)
        if ext is None:
            entries.append({'name': p.stem, 'file_bytes': p.stat().st_size, 'erreur': 'aucun point valide'})
            continue
        alt, mpp = cam_params(ext)
        g = plan_grid(zone_of(ext), mpp)
        n = g['nrow'] * g['ncol']
        entries.append({'name': p.stem, 'file_bytes': p.stat().st_size,
                        'alt_cmd': alt, 'y_max_rn4': ext['y_max'], 'm_per_px_predicted': round(mpp, 5),
                        'center': ext['center'], 'extent': {k: ext[k] for k in ('x_min', 'x_max', 'z_min', 'z_max')},
                        'size_m': ext['size_m'], 'grid': [g['nrow'], g['ncol']], 'n_captures': n,
                        'est_duration_s': round(n * EST_PER_CAPTURE_S, 1)})
    ok = [e for e in entries if 'erreur' not in e]
    res = {'theatre': theatre, 'rule': 'cadrage 3.3/3.4 + addendum 2 (altitude par base, echelle prevue)',
           'alt_rule': f'max({ALT_MIN:.0f}, y_max + {ALT_ABOVE_GROUND:.0f})', 'm_per_px_per_alt_m': MPP_PER_ALT_M,
           'n_bases': len(entries), 'n_ignored_small_rn4': skipped,
           'total_n_captures': sum(e['n_captures'] for e in ok),
           'total_est_duration_s': round(sum(e['est_duration_s'] for e in ok), 1),
           'bases': entries}
    out = out_theatre(theatre)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(f"{theatre}: {len(entries)} bases (> 5 Ko), {skipped} rn4 ignores, "
          f"{res['total_n_captures']} captures, ~{res['total_est_duration_s'] / 60:.1f} min")
    for e in entries:
        if 'erreur' in e:
            print('  ERREUR', e['name'], e['erreur'])


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


def calibrate(zone, alt, mpp_pred, odir, label):
    """Etalonnage (addendum 2) : coins NO puis SE de la zone ; a chaque coin capture, +200 m en x, +200 m en z.
    Retourne dict (mesures, moyenne, alignement). Leve AskCdP si une regle de l'addendum est enfreinte."""
    corners = (('nw', zone['x_max'], zone['z_min']), ('se', zone['x_min'], zone['z_max']))
    meas, aligns, peaks, detail = [], [], [], {}
    for name, cx, cz in corners:
        set_status(f'CAPTURE {label} calibration {name.upper()}')
        c0 = crop(move_and_stabilize(cx, cz, alt)[0])
        cxi = crop(move_and_stabilize(cx + CALIB_STEP_M, cz, alt)[0])
        czi = crop(move_and_stabilize(cx, cz + CALIB_STEP_M, alt)[0])
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
        raise AskCdP(f'Etalonnage {label} : ' + ' ; '.join(problems) + f'. Mesures {[round(m, 5) for m in meas]}, '
                     f'moyenne {mean:.5f}, prevue {mpp_pred:.5f}, pics {[round(p, 3) for p in peaks]}. '
                     f'Details : {odir / "calib.json"}')
    return res


def capture_base(theatre, base):
    rn4 = find_rn4(theatre, base)
    ext = rn4_extent(rn4)
    if ext is None:
        raise SystemExit(f'ARRET : aucun point valide dans {rn4}')
    zone = zone_of(ext)
    alt, mpp_pred = cam_params(ext)
    odir = out_theatre(theatre) / rn4.stem
    odir.mkdir(parents=True, exist_ok=True)
    print(f"{rn4.stem}: altitude {alt:.0f} m (y_max {ext['y_max']:.0f}), echelle prevue {mpp_pred:.4f} m/px")
    cal = calibrate(zone, alt, mpp_pred, odir, rn4.stem)
    MPP = cal['mean']
    print(f"  etalonnage : {[round(m, 4) for m in cal['measures']]} -> {MPP:.5f} m/px "
          f"(ecart max {cal['max_spread_rel'] * 100:.2f} %, vs prevue {cal['dev_vs_predicted_rel'] * 100:.2f} %, "
          f"align {cal['align_deg_max']:.3f} deg)")
    g = plan_grid(zone, MPP)
    fh, fw = g['footprint_m']
    n_total = g['nrow'] * g['ncol']
    print(f"  emprise {ext['size_m'][0]:.0f}x{ext['size_m'][1]:.0f} m, grille {g['nrow']}x{g['ncol']} = {n_total} captures")
    tiles, times = [], []
    T0 = time.perf_counter()
    for i, xx in enumerate(g['xs']):
        for j, zz in enumerate(g['zs']):
            tt = time.perf_counter()
            name = f'r{i}_c{j}'
            set_status(f'CAPTURE {rn4.stem} {len(tiles) + 1}/{n_total}')
            n_inc = len(_fg_incidents)
            img, delay, n, ok, cmd = move_and_stabilize(xx, zz, alt)
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
    cover = {'x_min': x_top - H * MPP, 'x_max': x_top, 'z_min': z_left, 'z_max': z_left + W * MPP}
    base_json = {'base': rn4.stem, 'theatre': theatre, 'rn4': str(rn4), 'extent_rn4': ext,
                 'zone_captured': zone, 'mosaic_coverage_m': cover,
                 'zone_fully_covered': bool(cover['x_min'] <= zone['x_min'] and cover['x_max'] >= zone['x_max']
                                            and cover['z_min'] <= zone['z_min'] and cover['z_max'] >= zone['z_max']),
                 'grid': [g['nrow'], g['ncol']], 'footprint_m': [fh, fw], 'step_m': g['step_m'],
                 'alt_cmd': alt, 'y_max_rn4': ext['y_max'], 'm_per_px_predicted': mpp_pred,
                 'm_per_px_measured': {'measures': cal['measures'], 'mean': cal['mean'],
                                       'max_spread_rel': cal['max_spread_rel']},
                 'm_per_px': MPP, 'align_deg': cal['align_deg_max'],
                 'margins_px_lthr_b': [MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B],
                 'tile_px': [usable_px()[0], usable_px()[1]], 'mosaic_px': [W, H],
                 'n_captures': len(tiles), 'n_not_stable': sum(1 for t in tiles if not t['stable']),
                 'total_s': round(total, 2), 'mean_s': round(sum(times) / len(times), 3),
                 'max_s': round(max(times), 3)}
    (odir / 'base.json').write_text(json.dumps(base_json, indent=1), encoding='utf-8')
    print(f"  total {total:.1f}s, moyenne {base_json['mean_s']}s, max {base_json['max_s']}s, "
          f"non stables {base_json['n_not_stable']}, zone couverte {base_json['zone_fully_covered']}")


def cmd_capture(theatre, bases):
    try:
        for b in bases:
            capture_base(theatre, b)
    finally:
        hook_release()


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
    try:
        main(sys.argv)
    except (AskCdP, ForegroundFailure, RuntimeError, OSError) as e:
        hook_release()
        ask(f'{type(e).__name__}: {e}')
        print('QUESTION ecrite :', e)
        sys.exit(3)
