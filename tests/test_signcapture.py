"""Panneaux de taxiway : la visee (capture/signcapture.py visee) et la prise de vues (faux crochet, fausse capture d'ecran),
et la commande `cam` du crochet. Les tests de visee sur Mahlwinkel demandent les echantillons (DCS_CHARTS_SAMPLES)."""
import json
import socket
import threading

import numpy as np
import pytest

import rn5
import signcapture as sc
import config


# ---------- graphes synthetiques ----------
def mk(nodes, segs):
    """nodes : [(type, x, z)] ; segs : [(largeur, noeud a, noeud b, [(x, z), ...])]. Retourne un Rn5."""
    ports = {i: [] for i in range(len(nodes))}
    for sid, (_, a, b, _) in enumerate(segs):
        ports[a].append(sid)
        ports[b].append(sid)
    types = tuple(dict.fromkeys(t for t, _, _ in nodes))
    widths = tuple(dict.fromkeys(w for w, _, _, _ in segs))
    ns = tuple(rn5.Node(i, types.index(t), t, tuple(ports[i]), float(x), float(z), '') for i, (t, x, z) in enumerate(nodes))
    ss = tuple(rn5.Segment(i, widths.index(w), w, a, 0, b, 0, tuple((float(x), 0.0, float(z)) for x, z in pts))
               for i, (w, a, b, pts) in enumerate(segs))
    return rn5.Rn5('synth', widths, types, ss, ns)


def chain(types, step=100.0, per_seg=4, width='taxiway_15m'):
    """Noeuds alignes sur x (0, step, 2 step...) relies par des troncons de per_seg points (le premier est en retrait de 5 m)."""
    nodes = [(t, i * step, 0.0) for i, t in enumerate(types)]
    segs = []
    for i in range(len(types) - 1):
        x0 = i * step
        pts = [(x0 + 5.0 + k * (step - 10.0) / (per_seg - 1), 0.0) for k in range(per_seg)]
        segs.append((width, i, i + 1, pts))
    return nodes, segs


def test_walk_drops_first_point_and_stops_at_stand():
    nodes, segs = chain(['taxiway_t', 'auto__a__b', 'stand_x'])
    g = sc.Graph(mk(nodes, segs))
    w = sc.walk(g, 0, 0, 30.0)
    assert w.pts[0] == (0.0, 0.0)
    assert w.pts[1] == (5.0 + 30.0, 0.0)             # 2e point du troncon 0 ; le premier (5, 0) est ecarte
    assert w.segs == [0, 1] and w.end_node == 2
    assert w.length == pytest.approx(195.0)          # 95 (jusqu'au dernier point) puis 100 : le 1er point du troncon 1 est ecarte
    assert w.cum[1] == pytest.approx(35.0)


def test_walk_holding_point_is_noted_and_crossed():
    nodes, segs = chain(['taxiway_t', 'taxi_point_holding_position', 'stand_x'])
    w = sc.walk(sc.Graph(mk(nodes, segs)), 0, 0, None)
    assert w.holds == [pytest.approx(95.0)] and w.segs == [0, 1]


def test_walk_stops_at_runway_and_threshold_nodes():
    for t in ('runway_taxiway_t', 'rwy_point_takeoff_landing', 'stand_y'):
        nodes, segs = chain(['taxiway_t', t, 'taxiway_t'])
        assert sc.walk(sc.Graph(mk(nodes, segs)), 0, 0, None).segs == [0]


def test_walk_stops_after_400_m_before_entering_a_segment():
    nodes, segs = chain(['taxiway_t'] + ['auto__a__b'] * 6 + ['stand_x'], step=150.0)
    w = sc.walk(sc.Graph(mk(nodes, segs)), 0, 0, None)
    assert w.segs == [0, 1, 2] and w.length >= 400.0     # 140 + 150 + 150 = 440 : on n'entre pas dans le 4e


def _fork(type_l):
    """Arrivee de l'ouest sur un noeud central ; sortie droit devant (est), sortie a 90 degres (sud)."""
    nodes = [('taxiway_t', -100, 0), (type_l, 0, 0), ('stand_a', 100, 0), ('stand_b', 0, 100)]
    segs = [('taxiway_15m', 0, 1, [(-95, 0), (-60, 0), (-30, 0), (-5, 0)]),
            ('taxiway_15m', 1, 3, [(0, 5), (0, 30), (0, 60), (0, 95)]),       # branche a 90 degres, portee en premier
            ('taxiway_15m', 1, 2, [(5, 0), (30, 0), (60, 0), (95, 0)])]
    return sc.Graph(mk(nodes, segs))


@pytest.mark.parametrize('type_l', ['taxiway_l', 'taxiway_l_mirror', 'taxiway_t', 'taxiway_cross'])
def test_three_port_nodes_follow_the_angle_rule(type_l):
    g = _fork(type_l)
    assert len(g.r.nodes[1].ports) == 3
    assert sc.walk(g, 0, 0, None).segs == [0, 2]         # le plus petit angle, pas le premier port
    assert sc.walk(g, 0, 0, 30.0).segs == [0, 2]


def test_angle_limit_stops_a_sharp_turn_only_with_a_limit():
    nodes = [('taxiway_t', -100, 0), ('taxiway_t', 0, 0), ('stand_b', 0, 100)]
    segs = [('taxiway_15m', 0, 1, [(-95, 0), (-60, 0), (-30, 0), (-5, 0)]),
            ('taxiway_15m', 1, 2, [(0, 5), (0, 30), (0, 60), (0, 95)])]
    g = sc.Graph(mk(nodes, segs))
    assert sc.walk(g, 0, 0, 30.0).segs == [0]            # 90 degres : au-dela de 30
    assert sc.walk(g, 0, 0, None).segs == [0, 1]         # bretelle : sans limite


def test_two_port_other_type_uses_the_angle_rule():
    nodes = [('taxiway_t', -100, 0), ('taxiway_end_x', 0, 0), ('stand_b', 0, 100)]
    segs = [('taxiway_15m', 0, 1, [(-95, 0), (-60, 0), (-30, 0), (-5, 0)]),
            ('taxiway_15m', 1, 2, [(0, 5), (0, 30), (0, 60), (0, 95)])]
    g = sc.Graph(mk(nodes, segs))
    assert sc.walk(g, 0, 0, 30.0).segs == [0]            # pas un noeud de passage : regle d'angle, 30 degres


def test_walk_never_enters_a_runway_segment():
    nodes = [('taxiway_t', -100, 0), ('auto__a__b', 0, 0), ('taxiway_t', 100, 0)]
    segs = [('taxiway_15m', 0, 1, [(-95, 0), (-60, 0), (-30, 0), (-5, 0)]),
            ('runway_45m', 1, 2, [(5, 0), (30, 0), (60, 0), (95, 0)])]
    assert sc.walk(sc.Graph(mk(nodes, segs)), 0, 0, None).segs == [0]




def test_stand_branch_sums_whole_segments():
    def sb(types, step, first=0):
        nodes, segs = chain(types, step=step)
        return sc.stand_branch(sc.Graph(mk(nodes, segs)), 0, first)
    assert sb(['taxiway_t', 'stand_x'], 100.0) is True                         # 90 m
    assert sb(['taxiway_t', 'stand_x'], 170.0) is False                        # 160 m
    assert sb(['taxiway_t', 'auto__a__b', 'stand_x'], 80.0) is True            # 70 + 70 = 140 m par un noeud de passage
    assert sb(['taxiway_t', 'auto__a__b', 'stand_x'], 90.0) is False           # 80 + 80 = 160 m
    assert sb(['taxiway_t', 'taxiway_t', 'stand_x'], 60.0) is False            # un autre noeud : pas une bretelle de poste


def test_stand_branch_stops_at_three_port_nodes():
    nodes = [('taxiway_t', -60, 0), ('taxiway_l', 0, 0), ('stand_a', 60, 0), ('stand_b', 0, 60)]
    segs = [('taxiway_15m', 0, 1, [(-55, 0), (-40, 0), (-20, 0), (-5, 0)]),
            ('taxiway_15m', 1, 2, [(5, 0), (20, 0), (40, 0), (55, 0)]),
            ('taxiway_15m', 1, 3, [(0, 5), (0, 20), (0, 40), (0, 55)])]
    assert sc.stand_branch(sc.Graph(mk(nodes, segs)), 0, 0) is False


# ---------- camera, bretelles, dedoublonnage ----------
def test_camera_toward_first_point_at_60_m_in_2_m_steps():
    w = sc.Walk((0.0, 0.0))
    w.add([(200.0, 0.0)])
    assert sc.camera_toward(w, 30.0, w.at(30.0)) == (90.0, 0.0)          # >= 60,0 : le test est inclusif


def test_camera_toward_falls_back_to_the_last_point_at_40_m_or_more():
    w = sc.Walk((0.0, 0.0))
    w.add([(75.0, 0.0)])
    assert sc.camera_toward(w, 30.0, w.at(30.0)) == (74.0, 0.0)          # dernier pas essaye, a 44 m
    w2 = sc.Walk((0.0, 0.0))
    w2.add([(60.0, 0.0)])
    assert sc.camera_toward(w2, 30.0, w2.at(30.0)) is None               # 30 m seulement


def test_camera_from_runway():
    nodes = [('rwy_point_takeoff_landing', -500, 0), ('rwy_point_takeoff_landing', 500, 0)]
    g = sc.Graph(mk(nodes, [('runway_45m', 0, 1, [(-500, 0), (0, 0), (500, 0)])]))
    assert sc.camera_from_runway(g, (50.0, 50.0)) == (50.0, 0.0)         # 40 a 70 m : le pied de A
    assert sc.camera_from_runway(g, (50.0, 80.0)) == pytest.approx((50.0, 20.0))   # plus de 70 m : a 60 m de A
    assert sc.camera_from_runway(g, (50.0, 30.0)) is None                # moins de 40 m


def test_bretelles_group_nodes_within_150_m():
    nodes = [('rwy_point_takeoff_landing', -1000, 0), ('rwy_point_takeoff_landing', 1000, 0),
             ('runway_taxiway_t', 0, 0), ('runway_taxiway_l', 100, 0), ('runway_taxiway_t', 300, 0),
             ('stand_a', 0, 100), ('stand_b', 100, 100), ('stand_c', 300, 100)]
    segs = [('runway_45m', 0, 2, [(-1000, 0), (0, 0)]), ('runway_45m', 2, 3, [(0, 0), (100, 0)]),
            ('runway_45m', 3, 4, [(100, 0), (300, 0)]), ('runway_45m', 4, 1, [(300, 0), (1000, 0)]),
            ('taxiway_15m', 2, 5, [(0, 0), (0, 100)]), ('taxiway_15m', 3, 6, [(100, 0), (100, 100)]),
            ('taxiway_15m', 4, 7, [(300, 0), (300, 100)])]
    g = sc.Graph(mk(nodes, segs))
    # les seuils (0 et 1) n'ont aucun troncon de taxiway : pas de bretelle ; 2 et 3 (100 m) vont ensemble, 4 (200 m de 3) seul
    assert sc.bretelles(g) == [[2, 3], [4]]


def _view(x, z, cap):
    return {'camera': {'x': x, 'z': z}, 'cap': cap}


def test_dedupe_needs_both_15_m_and_20_degrees():
    vs = [_view(0, 0, 10), _view(10, 0, 25), _view(10, 0, 40), _view(20, 0, 10), _view(5, 5, 355)]
    kept = sc.dedupe(vs)
    assert [(v['camera']['x'], v['cap']) for v in kept] == [(0, 10), (10, 40), (20, 10)]


# ---------- visee sur Mahlwinkel (echantillons) ----------
MAHL = ('dcs_install', 'Mods', 'terrains', 'GermanyColdWar', 'AirfieldsTaxiways', 'Mahlwinkel.rn5')


@pytest.fixture(scope='module')
def mahl(samples):
    r = rn5.read(samples.joinpath(*MAHL))
    return r, sc.plan_base(r)


def test_mahlwinkel_plan(mahl):
    r, (g, views, brs, inter) = mahl
    assert [b[0] for b in brs] == [17, 52, 199] and len(inter) == 11 and len(views) == 40
    exp = [(-217685.6, -589450.2, 7.6), (-217564.7, -589434.1, 187.5), (-218020.7, -586975.2, 7.6)]
    for v, (x, z, cap) in zip(views, exp):
        assert v['camera']['x'] == pytest.approx(x, abs=0.15) and v['camera']['z'] == pytest.approx(z, abs=0.15)
        assert v['cap'] == pytest.approx(cap, abs=0.1)
    assert [v['numero'] for v in views] == list(range(1, 41))
    assert all(40.0 <= v['distance'] <= 70.0 for v in views)
    assert all(v['hauteur'] == 3.0 and v['tangage'] < 0 for v in views)


def test_mahlwinkel_every_camera_is_on_pavement(mahl):
    r, (g, views, brs, inter) = mahl
    for v in views:
        c = (v['camera']['x'], v['camera']['z'])
        ok = any(sc._foot(c, a, b)[1] <= s.half_width + 0.5
                 for s, line in zip(r.segments, g.line) for a, b in zip(line, line[1:]))
        assert ok, v['numero']


def test_mahlwinkel_walk_from_a_runway_exit(mahl):
    r, (g, views, brs, inter) = mahl
    w = sc.walk(g, 17, 31, None)
    s = sc.aim_s(g, w)
    assert s == 90.0                                     # plafonnee a 90 m : aucun point d'attente a Mahlwinkel
    a = w.at(s)
    assert (a[0], a[1]) == pytest.approx((-217624.1, -589442.0), abs=0.1)


def _cfg_for(dcs_install, tmp_path):
    p = tmp_path / 'config.json'
    p.write_text(json.dumps({'work_dir': str(tmp_path / 'work'), 'dcs_install': str(dcs_install)}), encoding='utf-8')
    return config.load_config(['x', '--config', str(p)])[0]


def test_visee_outputs_and_reproducible(samples, tmp_path):
    cfg = _cfg_for(samples / 'dcs_install', tmp_path)
    sc.cmd_visee(cfg, 'GermanyColdWar', ['Mahlwinkel'], log=lambda *a: None)
    out = tmp_path / 'work' / 'panneaux' / 'GermanyColdWar'
    first = (out / 'Mahlwinkel' / 'vues.json').read_bytes()
    doc = json.loads(first)
    assert doc['base'] == 'Mahlwinkel' and doc['theatre'] == 'GermanyColdWar' and doc['source'] == 'Mahlwinkel.rn5'
    assert len(doc['vues']) == 40 and len(doc['intersections']) == 11 and len(doc['bretelles']) == 3
    assert all(b['vues_piste'] >= 2 for b in doc['bretelles'])
    summ = json.loads((out / 'visee.json').read_bytes())['bases']['Mahlwinkel']
    assert summ['vues'] == 40 and summ['duree_estimee_s'] == 180.0 and summ['bretelles_laterales_sous_deux_vues'] == 0
    sc.cmd_visee(cfg, 'GermanyColdWar', ['Mahlwinkel'], log=lambda *a: None)
    assert (out / 'Mahlwinkel' / 'vues.json').read_bytes() == first


def test_visee_unknown_base_stops(samples, tmp_path):
    with pytest.raises(sc.Stop):
        sc.cmd_visee(_cfg_for(samples / 'dcs_install', tmp_path), 'GermanyColdWar', ['Nulle_part'], log=lambda *a: None)


def test_list_bases_keeps_rn5_of_5000_bytes_or_more(samples, tmp_path):
    bases = sc.list_bases(_cfg_for(samples / 'dcs_install', tmp_path), 'Syria')
    names = [n for n, _ in bases]
    assert 'Konya' in names and 'Ramat_David' in names
    assert all(p.stat().st_size >= 5000 and p.suffix == '.rn5' for _, p in bases)


# ---------- prise de vues : faux crochet (serveur local), fausse capture d'ecran ----------
class FakeHook:
    """Serveur local qui repond comme le crochet (2.4) : `cam` -> `ok x y z` (y = sol + h) ou `err no_altitude`,
    `release` -> `ok released`. Garde la camera courante."""
    GROUND = 100.0

    def __init__(self, no_altitude_x=()):
        self.no_altitude_x = set(no_altitude_x)
        self.cam = None
        self.lines = []
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.alive = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with c:
                data = b''
                while not data.endswith(b'\n'):
                    d = c.recv(256)
                    if not d:
                        break
                    data += d
                line = data.decode().strip()
                self.lines.append(line)
                p = line.split()
                if p[0] == 'cam':
                    x, z, h = float(p[1]), float(p[2]), float(p[3])
                    if x in self.no_altitude_x:
                        reply = 'err no_altitude'
                    else:
                        self.cam = (x, z, h, float(p[4]), float(p[5]))
                        reply = f'ok {x:.3f} {self.GROUND + h:.3f} {z:.3f}'
                elif p[0] == 'release':
                    self.cam = None
                    reply = 'ok released'
                else:
                    reply = 'err unknown_command'
                c.sendall((reply + '\n').encode())

    def close(self):
        self.alive = False
        self.srv.close()


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, dt):
        self.t += dt

    def now(self):
        return self.t


def _make_env(server, clock, unstable_x=(), crash_x=None, size=(100, 200), log=None):
    from render import f10import
    f10 = f10import.load()          # fonctions pures de capture/f10capture.py : diff_frac, save_png, STAB_FRAC
    rng = np.random.default_rng(1)

    def hook(line):
        log.append((clock.now(), line))
        with socket.create_connection(('127.0.0.1', server.port), timeout=5) as s:
            s.sendall((line + '\n').encode())
            buf = b''
            while not buf.endswith(b'\n'):
                buf += s.recv(256)
        return buf.decode().strip()

    def grab():
        cam = server.cam
        if cam is not None and crash_x is not None and cam[0] == crash_x:
            raise KeyboardInterrupt
        h, w = size
        if cam is not None and cam[0] in unstable_x:
            return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        base = np.zeros((h, w, 3), dtype=np.uint8)
        base[:, :, 0] = int(abs(cam[0]) % 200) if cam else 0
        return base

    env = sc.Env(hook, grab, lambda ctx='': None, f10.diff_frac, f10.save_png, f10.STAB_FRAC,
                 sleep=clock.sleep, now=clock.now, release=lambda: hook('release'))
    return env


def _views(n):
    return [{'numero': i, 'genre': 'taxiway', 'sens': 'vers l\'intersection', 'bretelle': None, 'intersection': 1,
             'troncon': i, 'camera': {'x': 1000.0 + i, 'z': -2000.0 - i}, 'hauteur': 3.0, 'cap': 90.0 + i,
             'tangage': -1.4, 'vise': {'x': 0.0, 'z': 0.0}, 'distance': 60.0} for i in range(1, n + 1)]


@pytest.fixture
def capture_setup(tmp_path):
    dcs = tmp_path / 'dcs'
    (dcs / 'Mods' / 'terrains').mkdir(parents=True)
    cfg = _cfg_for(dcs, tmp_path)
    base_dir = cfg.work_dir / 'panneaux' / 'Syria' / 'Test_Base'
    base_dir.mkdir(parents=True)
    (base_dir / 'vues.json').write_text(json.dumps({'base': 'Test_Base', 'vues': _views(6)}), encoding='utf-8')
    return cfg, base_dir


def test_capture_request_sequence_hold_and_index(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    try:
        env = _make_env(server, clock, log=log)
        assert sc.capture_base(cfg, env, 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    lines = [l for _, l in log]
    assert lines[0] == 'cam 1001.000 -2001.000 3.0 91.0 -1.40'
    assert lines[-1] == 'release' and lines.count('release') == 1
    cams = [(t, l) for t, l in log if l.startswith('cam')]
    # chaque vue : une premiere requete puis l'entretien, jamais plus de 2 s entre deux requetes d'une meme vue
    for n in range(1, 7):
        ts = [t for t, l in cams if l.startswith(f'cam {1000 + n}.000 ')]
        assert len(ts) >= 2 and all(b - a <= 2.0 for a, b in zip(ts, ts[1:])), n
        assert ts[-1] - ts[0] >= 3.0                        # l'attente de 3 s est tenue
    idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
    assert [e['statut'] for e in idx['vues']] == ['faite'] * 6
    e = idx['vues'][0]
    assert e['numero'] == 1 and e['img'] == 'img/001.png' and e['vignette'] == 'vignettes/001.png'
    assert e['position'] == [1001.0, 103.0, -2001.0]       # le y renvoye par le crochet : sol + 3 m
    assert e['heure']


def test_capture_files_full_screen_and_central_band(capture_setup):
    from PIL import Image
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    try:
        sc.capture_base(cfg, _make_env(server, clock, log=log, size=(100, 200)), 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    assert Image.open(base_dir / 'img' / '003.png').size == (200, 100)
    assert Image.open(base_dir / 'vignettes' / '003.png').size == (200 - 2 * 20, 100 - 2 * 30)    # 10 % et 30 % retires


def test_capture_unstable_view_after_10_s_and_move_on(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    lines = []
    try:
        env = _make_env(server, clock, unstable_x={1002.0}, log=log)
        sc.capture_base(cfg, env, 'Syria', 'Test_Base', log=lines.append)
    finally:
        server.close()
    idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
    assert [e['statut'] for e in idx['vues']] == ['faite', 'instable', 'faite', 'faite', 'faite', 'faite']
    assert lines[1] == 'Test_Base 2/6 instable' and lines[0] == 'Test_Base 1/6 faite'
    t2 = [t for t, l in log if l.startswith('cam 1002.000')][0]
    t3 = [t for t, l in log if l.startswith('cam 1003.000')][0]
    assert 12.9 <= t3 - t2 <= 13.6                          # 3 s d'attente puis 10 s au plus


def test_capture_no_altitude_is_an_echec_and_continues(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(no_altitude_x={1003.0}), Clock(), []
    try:
        sc.capture_base(cfg, _make_env(server, clock, log=log), 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
    assert [e['statut'] for e in idx['vues']] == ['faite', 'faite', 'echec', 'faite', 'faite', 'faite']
    assert idx['vues'][2]['img'] is None and not (base_dir / 'img' / '003.png').exists()


def test_capture_resume_after_an_interruption(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    try:
        env = _make_env(server, clock, crash_x=1004.0, log=log)          # arret (Ctrl+C) a la 4e vue
        with pytest.raises(KeyboardInterrupt):
            sc.capture_base(cfg, env, 'Syria', 'Test_Base', log=lambda *a: None)
        assert log[-1][1] == 'release'                                   # le crochet est relache
        idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
        assert [e['numero'] for e in idx['vues']] == [1, 2, 3] and all(e['statut'] == 'faite' for e in idx['vues'])
        before = {n: (base_dir / 'img' / f'{n:03d}.png').read_bytes() for n in (1, 2, 3)}
        mtimes = {n: (base_dir / 'img' / f'{n:03d}.png').stat().st_mtime_ns for n in (1, 2, 3)}
        log.clear()
        lines = []
        env2 = _make_env(server, clock, log=log)
        assert sc.capture_base(cfg, env2, 'Syria', 'Test_Base', log=lines.append)
    finally:
        server.close()
    cam_x = [l.split()[1] for _, l in log if l.startswith('cam')]
    assert set(cam_x) == {'1004.000', '1005.000', '1006.000'}            # aucune des trois premieres n'est refaite
    assert lines[:3] == ['Test_Base 1/6 deja faite', 'Test_Base 2/6 deja faite', 'Test_Base 3/6 deja faite']
    assert all((base_dir / 'img' / f'{n:03d}.png').stat().st_mtime_ns == mtimes[n] for n in (1, 2, 3))
    assert all((base_dir / 'img' / f'{n:03d}.png').read_bytes() == before[n] for n in (1, 2, 3))
    idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
    assert [e['numero'] for e in idx['vues']] == [1, 2, 3, 4, 5, 6]


def test_capture_redoes_a_view_whose_file_is_missing(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    try:
        sc.capture_base(cfg, _make_env(server, clock, log=log), 'Syria', 'Test_Base', log=lambda *a: None)
        (base_dir / 'vignettes' / '002.png').unlink()
        log.clear()
        sc.capture_base(cfg, _make_env(server, clock, log=log), 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    assert {l.split()[1] for _, l in log if l.startswith('cam')} == {'1002.000'}
    assert (base_dir / 'vignettes' / '002.png').is_file()


def test_capture_mute_hook_writes_the_index_and_stops(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    env = _make_env(server, clock, log=log)
    real_hook = env.hook
    calls = {'n': 0}

    def flaky(line):
        calls['n'] += 1
        if calls['n'] > 8:
            raise ConnectionRefusedError('crochet muet')
        return real_hook(line)
    env.hook = flaky
    try:
        with pytest.raises(OSError):
            sc.capture_base(cfg, env, 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    idx = json.loads((base_dir / 'index.json').read_text(encoding='utf-8'))
    assert 1 <= len(idx['vues']) < 6 and all(e['statut'] == 'faite' for e in idx['vues'])


def test_capture_writes_only_under_panneaux(capture_setup):
    cfg, base_dir = capture_setup
    server, clock, log = FakeHook(), Clock(), []
    try:
        sc.capture_base(cfg, _make_env(server, clock, log=log), 'Syria', 'Test_Base', log=lambda *a: None)
    finally:
        server.close()
    assert {p.name for p in cfg.work_dir.iterdir()} == {'panneaux'}
    assert {p.name for p in base_dir.iterdir()} == {'vues.json', 'index.json', 'img', 'vignettes'}


# ---------- le crochet : commande `cam` (P6) ----------
HOOK = None


def _hook_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / 'capture' / 'hook' / 'ChartsCaptureHook.lua').read_text(encoding='utf-8')


def test_hook_source_keeps_the_existing_commands():
    src = _hook_src()
    assert "'ok released'" in src and "'err bad_request'" in src and "'err unknown_command'" in src
    assert "string.format('ok %.3f %.3f %.3f', x, z, c)" in src                       # xz et ll
    assert "p = { x = t.x, y = t.alt, z = t.z }" in src and "CC_IDLE_RELEASE_S = 5" in src
    assert "ccSocket.bind('127.0.0.1', 47123)" in src
    assert "'err no_altitude'" in src and "string.format('ok %.3f %.3f %.3f', x, y, z)" in src


PRELUDE = r'''
pending, replies, camcalls, now = {}, {}, {}, 0
local client = { settimeout = function() end, close = function() end,
  receive = function() if #pending > 0 then return table.remove(pending, 1) end return nil, 'timeout' end,
  send = function(_, s) replies[#replies + 1] = s end }
local given = false
local server = { settimeout = function() end, close = function() end,
  accept = function() if not given then given = true return client end return nil end }
function require(name) return { gettime = function() return now end, bind = function() return server end } end
log = { write = function() end, INFO = 0 }
function LoGeoCoordinatesToLoCoordinates(lon, lat) return { x = lat * 10, z = lon * 10 } end
function LoSetCameraPosition(m) camcalls[#camcalls + 1] = m end
function LoGetAltitude(x, z) return 100 + x / 1000 end
function frame(line) pending[#pending + 1] = line; LuaExportBeforeNextFrame() end
'''


@pytest.fixture
def lua():
    lupa = pytest.importorskip('lupa')
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(PRELUDE)
    rt.execute(_hook_src())
    rt.eval('LuaExportStart')()
    return rt


def _replies(rt):
    return [r.rstrip('\n') for r in rt.eval('replies').values()]


def test_hook_cam_places_the_camera_at_ground_plus_h(lua):
    lua.eval('frame')('cam 2000 -3000 3 90 -5')
    assert _replies(lua) == ['ok 2000.000 105.000 -3000.000']                 # LoGetAltitude(2000) = 102, + 3 m
    m = lua.eval('camcalls')[1]
    assert (m.p.x, m.p.y, m.p.z) == (2000, 105, -3000)
    import math
    cp, sp = math.cos(math.radians(-5)), math.sin(math.radians(-5))
    assert m.x.x == pytest.approx(cp * math.cos(math.radians(90)), abs=1e-9) and m.x.z == pytest.approx(cp)
    assert m.x.y == pytest.approx(sp) and m.y.y == pytest.approx(cp) and m.z.x == pytest.approx(-1.0)
    lua.eval('frame')('cam 2000 -3000 3 90 -5')
    assert len(lua.eval('camcalls')) == 2                                        # tenue a chaque image


def test_hook_cam_without_altitude_places_nothing(lua):
    lua.execute('LoGetAltitude = nil')
    lua.eval('frame')('cam 1 2 3 4 5')
    assert _replies(lua) == ['err no_altitude'] and len(lua.eval('camcalls')) == 0
    lua.execute('LoGetAltitude = function() error("boom") end')
    lua.eval('frame')('cam 1 2 3 4 5')
    assert _replies(lua)[-1] == 'err no_altitude' and len(lua.eval('camcalls')) == 0


def test_hook_cam_bad_request_and_release(lua):
    lua.eval('frame')('cam 1 2 3')
    lua.eval('frame')('cam 5 6 3 0 0')
    lua.eval('frame')('release')
    lua.execute('LuaExportBeforeNextFrame()')
    assert _replies(lua) == ['err bad_request', 'ok 5.000 103.005 6.000', 'ok released']
    assert len(lua.eval('camcalls')) == 1                                        # apres release, plus de camera


def test_hook_xz_ll_release_replies_unchanged(lua):
    lua.eval('frame')('xz 1000 2000 1500')
    lua.eval('frame')('ll 34.5 36.5 1800')
    lua.eval('frame')('bogus 1 2 3')
    lua.eval('frame')('xz 1 2')
    lua.eval('frame')('release')
    assert _replies(lua) == ['ok 1000.000 2000.000 1500.000', 'ok 345.000 365.000 1800.000', 'err unknown_command',
                             'err bad_request', 'ok released']
    last = lua.eval('camcalls')[1]                                              # camera de dessus de `xz`
    lua.eval('frame')('xz 7 8 900')
    m = lua.eval('camcalls')[lua.eval('#camcalls')]
    assert (m.x.x, m.x.y, m.x.z, m.y.x, m.z.z, m.p.x, m.p.y, m.p.z) == (0, -1, 0, 1, 1, 7, 900, 8)


def test_hook_camera_released_after_5_s(lua):
    lua.eval('frame')('cam 5 6 3 0 0')
    n = len(lua.eval('camcalls'))
    lua.execute('now = 6')
    lua.execute('LuaExportBeforeNextFrame()')
    assert len(lua.eval('camcalls')) == n
