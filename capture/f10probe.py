"""Sonde de capture F10 (DCS) : liaison, stabilisation, echelle/alignement, grille 2x2 km.

Usage : python -I f10probe.py {A|B|C|D}   (voir CADRAGE_SONDE_CAPTURE_F10.md)
Sorties : ChartsCapture/out/bitburg/
"""
import ctypes
import json
import math
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import mss
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I n'ajoute pas le dossier du script
from config import load_config

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HOST, PORT = '127.0.0.1', 47123
LAT, LON = 49.9453, 6.5650
OUT = None             # work_dir / 'out' / 'bitburg', fixe apres load_config
STAB_FRAC = 0.005      # 0,5 % des pixels
PIX_THR = 8            # seuil par pixel (sur 255)
POLL_S = 0.1
TIMEOUT_S = 10.0
ALTS = [1350, 2500, 5000]
ZONE_M = 2000.0
OVERLAP = 0.20


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


def hook_xz(x, z, alt):
    r = hook(f'xz {x:.3f} {z:.3f} {alt:.1f}').split()
    if r[0] != 'ok':
        raise RuntimeError('hook: ' + ' '.join(r))
    return float(r[1]), float(r[2]), float(r[3])


def hook_ll(lat, lon, alt):
    r = hook(f'll {lat} {lon} {alt}').split()
    if r[0] != 'ok':
        raise RuntimeError('hook: ' + ' '.join(r))
    return float(r[1]), float(r[2]), float(r[3])


# ---------- capture ----------
_sct = None
_last = None


def grab():
    global _sct
    if _sct is None:
        _sct = mss.mss()
    shot = _sct.grab(_sct.monitors[1])
    return np.asarray(shot)[:, :, :3][:, :, ::-1].copy()   # BGRA -> RGB


def diff_frac(a, b):
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > PIX_THR).mean())


def move_and_stabilize(x, z, alt, expect_change=True):
    """Commande la camera, attend l'image stable. Retourne (image, delai_s, n_images, stable, (x,z,alt) commande)."""
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
        time.sleep(max(0.0, POLL_S - (time.perf_counter() - t_frame)))


def save_png(img, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, format='PNG')


def load_png(path):
    return np.asarray(Image.open(path).convert('RGB'))


# ---------- mesure ----------
def phase_shift(a, b):
    """Deplacement (dx, dy) en px du contenu de a vers b : b[y,x] ~ a[y-dy, x-dx]."""
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


def useful_margins(c0, cx, cz):
    """Marges (gauche, haut, droite, bas) en px : zone ou l'image bouge quand la camera bouge."""
    dyn = np.zeros(c0.shape[:2], bool)
    for c in (cx, cz):
        dyn |= np.abs(c0.astype(np.int16) - c.astype(np.int16)).max(axis=2) > PIX_THR
    cols, rows = dyn.mean(axis=0), dyn.mean(axis=1)
    thr = 0.3
    xs, ys = np.where(cols > thr)[0], np.where(rows > thr)[0]
    h, w = dyn.shape
    return int(xs[0]), int(ys[0]), int(w - 1 - xs[-1]), int(h - 1 - ys[-1])


def selftest_phase():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, (400, 600, 3), dtype=np.uint8)
    base = np.asarray(Image.fromarray(base).resize((600, 400), Image.BICUBIC))
    moved = np.roll(np.roll(base, 7, axis=1), -12, axis=0)   # contenu: +7 en x, -12 en y
    dx, dy, _ = phase_shift(base, moved)
    assert abs(dx - 7) < 0.3 and abs(dy + 12) < 0.3, (dx, dy)


# ---------- etapes ----------
def center_xz():
    x, z, _ = hook_ll(LAT, LON, 2500)
    return x, z


def step_A():
    x, z = center_xz()
    img, delay, n, ok, cmd = move_and_stabilize(x, z, 2500)
    save_png(img, OUT / 'A_centre.png')
    print('commande', cmd, 'stable', ok, f'{delay:.2f}s', 'taille', img.shape[1], 'x', img.shape[0])
    (OUT / 'A.json').write_text(json.dumps({'center_xz': [x, z], 'cmd': cmd, 'stable': ok,
                                            'delay_s': delay, 'size_px': [img.shape[1], img.shape[0]]}, indent=1))


def step_B():
    x, z = center_xz()
    alt = 2500
    move_and_stabilize(x, z, alt, expect_change=False)
    res = {}
    for name, dz in (('300m', 300.0), ('5km', 5000.0)):
        delays = []
        for i in range(3):
            tgt = (x, z + dz) if i % 2 == 0 else (x, z)
            _, delay, n, ok, _ = move_and_stabilize(tgt[0], tgt[1], alt)
            delays.append({'delay_s': round(delay, 3), 'n_images': n, 'stable': ok})
            print(name, delays[-1])
        res[name] = delays
    move_and_stabilize(x, z, alt)
    (OUT / 'B.json').write_text(json.dumps(res, indent=1))


def step_C():
    x, z = center_xz()
    res = {}
    for alt in ALTS:
        move_and_stabilize(x, z, alt, expect_change=False)
        c0, d0, _, ok0, _ = move_and_stabilize(x, z, alt, expect_change=False)
        cx, dx_, _, okx, _ = move_and_stabilize(x + 200, z, alt)
        cz, dz_, _, okz, _ = move_and_stabilize(x, z + 200, alt)
        for tag, im in (('c0', c0), ('cx', cx), ('cz', cz)):
            save_png(im, OUT / 'C' / f'alt{alt}_{tag}.png')
        res[str(alt)] = analyse_alt(alt, c0, cx, cz)
        res[str(alt)]['stable'] = [ok0, okx, okz]
        print(alt, json.dumps(res[str(alt)]))
    move_and_stabilize(x, z, 2500, expect_change=False)
    ok = {a: r for a, r in res.items() if r['mpp'] <= 0.6 and r['mpp'] is not None}
    if ok:
        pick = min(ok, key=lambda a: abs(ok[a]['mpp'] - 0.5))
        note = ''
    else:
        pick = min(res, key=lambda a: res[a]['mpp'])
        note = 'aucune altitude <= 0,6 m/px : plus fine retenue'
    res['retenue'] = {'alt': int(pick), 'note': note}
    (OUT / 'C.json').write_text(json.dumps(res, indent=1))
    print('retenue', res['retenue'])


def analyse_alt(alt, c0, cx, cz):
    l, t, r, b = useful_margins(c0, cx, cz)
    h, w = c0.shape[:2]
    sl = (slice(t, h - b), slice(l, w - r))
    ax, ay, pk1 = phase_shift(c0[sl], cx[sl])    # decalage x (Nord) +200 m
    bx, by, pk2 = phase_shift(c0[sl], cz[sl])    # decalage z (Est) +200 m
    mpp_x = 200.0 / math.hypot(ax, ay)
    mpp_z = 200.0 / math.hypot(bx, by)
    # composante principale attendue : x -> axe vertical image ; z -> axe horizontal image
    par_x = math.degrees(math.atan2(ax, ay)) if ay != 0 else 90.0
    par_z = math.degrees(math.atan2(by, bx)) if bx != 0 else 90.0
    return {
        'margins_lhrb': [l, t, r, b], 'useful_px': [w - l - r, h - t - b],
        'shift_px_x200': [ax, ay], 'shift_px_z200': [bx, by], 'peaks': [pk1, pk2],
        'mpp_x': mpp_x, 'mpp_z': mpp_z, 'mpp': (mpp_x + mpp_z) / 2,
        'parasite_px_x': ax, 'parasite_px_z': by,
        'align_deg_x': math.degrees(math.atan2(ax, abs(ay))), 'align_deg_z': math.degrees(math.atan2(by, abs(bx))),
        'sens': {'x_plus_image_dy_sign': 'bas' if ay > 0 else 'haut', 'z_plus_image_dx_sign': 'droite' if bx > 0 else 'gauche'},
    }


def step_D():
    C = json.loads((OUT / 'C.json').read_text())
    alt = C['retenue']['alt']
    a = C[str(alt)]
    mpp = a['mpp']
    l, t, r, b = a['margins_lhrb']
    # Camera monte en +x : le contenu glisse vers le bas (dy>0) => Nord en haut ; z+ => contenu vers la gauche (dx<0) => Est a droite.
    if not (a['shift_px_x200'][1] > 0 and a['shift_px_z200'][0] < 0):
        raise SystemExit('ARRET : orientation image inattendue (Nord pas en haut / Est pas a droite) : ' + json.dumps(a['sens']))
    uw, uh = a['useful_px']
    fw, fh = uw * mpp, uh * mpp
    stw, sth = fw * (1 - OVERLAP), fh * (1 - OVERLAP)
    ncol = 1 if fw >= ZONE_M else math.ceil((ZONE_M - fw) / stw) + 1
    nrow = 1 if fh >= ZONE_M else math.ceil((ZONE_M - fh) / sth) + 1
    cx0, cz0 = center_xz()
    xs = [cx0 + ((nrow - 1) / 2 - i) * (sth if nrow > 1 else 0) for i in range(nrow)]   # ligne 0 = nord
    zs = [cz0 + (j - (ncol - 1) / 2) * (stw if ncol > 1 else 0) for j in range(ncol)]
    print(f'alt {alt} m/px {mpp:.3f} utile {uw}x{uh}px = {fw:.0f}x{fh:.0f} m ; grille {nrow}x{ncol}')
    move_and_stabilize(cx0, cz0, alt, expect_change=False)
    tiles, times = [], []
    T0 = time.perf_counter()
    for i, xx in enumerate(xs):
        for j, zz in enumerate(zs):
            tt = time.perf_counter()
            img, delay, n, ok, cmd = move_and_stabilize(xx, zz, alt)
            tile = img[t:img.shape[0] - b, l:img.shape[1] - r]
            save_png(tile, OUT / 'tiles' / f'r{i}_c{j}.png')
            tiles.append({'tile': f'r{i}_c{j}', 'x': cmd[0], 'z': cmd[1], 'alt': alt, 'mpp': mpp,
                          'size_px': [tile.shape[1], tile.shape[0]],
                          'axes': 'x(Nord)=haut image, z(Est)=droite image',
                          'stabilization_s': round(delay, 3), 'stable': ok,
                          'timestamp': datetime.now().isoformat(timespec='milliseconds')})
            times.append(time.perf_counter() - tt)
            print(tiles[-1]['tile'], f'{times[-1]:.2f}s', 'stable' if ok else 'NON STABLE')
    total = time.perf_counter() - T0
    (OUT / 'tiles.json').write_text(json.dumps(tiles, indent=1))
    # mosaique par simple placement
    x_top = max(tl['x'] for tl in tiles) + fh / 2
    z_left = min(tl['z'] for tl in tiles) - fw / 2
    W = int(round((max(tl['z'] for tl in tiles) + fw / 2 - z_left) / mpp))
    H = int(round((x_top - (min(tl['x'] for tl in tiles) - fh / 2)) / mpp))
    canvas = np.zeros((H, W, 3), np.uint8)
    for tl in tiles:
        im = load_png(OUT / 'tiles' / f"{tl['tile']}.png")
        px = int(round((tl['z'] - fw / 2 - z_left) / mpp))
        py = int(round((x_top - (tl['x'] + fh / 2)) / mpp))
        hh, ww = im.shape[:2]
        canvas[py:py + hh, px:px + ww] = im[:H - py, :W - px] if (py + hh > H or px + ww > W) else im
    save_png(canvas, OUT / 'mosaic.png')
    stats = {'n_captures': len(tiles), 'total_s': round(total, 2), 'mean_s': round(sum(times) / len(times), 2),
             'max_s': round(max(times), 2), 'mosaic_px': [W, H], 'grid': [nrow, ncol],
             'footprint_m': [fw, fh], 'step_m': [stw, sth],
             'extrapolation_5km_s': round(total * (5000 * 5000) / (ZONE_M * ZONE_M), 1)}
    (OUT / 'D_stats.json').write_text(json.dumps(stats, indent=1))
    move_and_stabilize(cx0, cz0, 2500, expect_change=False)
    print(json.dumps(stats, indent=1))


if __name__ == '__main__':
    CFG, _argv = load_config(sys.argv)
    OUT = CFG.work_dir / 'out' / 'bitburg'
    selftest_phase()
    OUT.mkdir(parents=True, exist_ok=True)
    step = _argv[1].upper() if len(_argv) > 1 else ''
    try:
        {'A': step_A, 'B': step_B, 'C': step_C, 'D': step_D}[step]()
    finally:
        try:
            hook('release')
        except Exception:
            pass
