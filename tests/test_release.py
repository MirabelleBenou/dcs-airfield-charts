"""Tests de tools/release.py : git reel (depot temporaire + origin nu), API simulee, aucun acces au reseau."""
import base64
import hashlib
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import release  # noqa: E402

REPO = 'owner/charts'
TOKEN = 'ghs_TOPSECRETTOKEN0123456789'
TITRE = 'AC-V1.7.0 — 16 November 2026'
CHANGELOG = f'# Changelog\n\n## Unreleased\n\n## {TITRE.split(" ")[0]} — 16 November 2026\n\n- x\n'
ZIP = b'PK\x03\x04 not really a zip but bytes'


@pytest.fixture(autouse=True)
def pas_de_reseau(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError('network access in a test')
    monkeypatch.setattr(urllib.request, 'urlopen', interdit)
    monkeypatch.setattr(socket.socket, 'connect', interdit)


def git(cwd, *args):
    p = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class FakeApi:
    def __init__(self, repo=REPO):
        self.repo = repo
        self.calls = []                 # ('METHODE', chemin)
        self.releases = {}              # id -> dict
        self.tags = {}                  # tag -> sha
        self.assets = {}                # id -> [{'name', 'size'}]
        self.fail = {}                  # (methode, sous-chaine du chemin) -> (statut, objet)
        self.next_id = 100
        self.bodies = []

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != 'GET']

    def request(self, methode, chemin, corps=None, fichier=None):
        self.calls.append((methode, chemin))
        for (m, sub), rep in self.fail.items():
            if m == methode and sub in chemin:
                return rep
        base = f'/repos/{self.repo}'
        if methode == 'GET' and chemin.startswith(f'{base}/git/ref/tags/'):
            t = chemin.rsplit('/', 1)[1]
            return (200, {'object': {'type': 'commit', 'sha': self.tags[t]}}) if t in self.tags else (404, {'message': 'Not Found'})
        if methode == 'GET' and chemin.startswith(f'{base}/releases?'):
            return 200, [dict(r) for r in self.releases.values()]
        if methode == 'POST' and chemin == f'{base}/releases':
            self.bodies.append(corps)
            self.next_id += 1
            self.releases[self.next_id] = dict(corps, id=self.next_id, upload_url=f'https://uploads.github.com{base}/releases/{self.next_id}/assets{{?name,label}}')
            self.assets[self.next_id] = []
            return 201, self.releases[self.next_id]
        if methode == 'POST' and chemin.startswith('https://uploads.github.com/'):
            rid = int(chemin.split('/releases/')[1].split('/')[0])
            nom = chemin.split('name=')[1]
            self.assets[rid].append({'name': nom, 'size': Path(fichier).stat().st_size})
            return 201, {'name': nom}
        if methode == 'GET' and '/assets' in chemin:
            rid = int(chemin.split('/releases/')[1].split('/')[0])
            return 200, self.assets[rid]
        if methode == 'PATCH':
            rid = int(chemin.rsplit('/', 1)[1])
            r = self.releases[rid]
            r.update(corps)
            self.tags[r['tag_name']] = r['target_commitish']
            return 200, r
        if methode == 'GET' and chemin.startswith(f'{base}/releases/tags/'):
            t = chemin.rsplit('/', 1)[1]
            for r in self.releases.values():
                if r['tag_name'] == t and not r['draft']:
                    return 200, r
            return 404, {'message': 'Not Found'}
        if methode == 'DELETE':
            del self.releases[int(chemin.rsplit('/', 1)[1])]
            return 204, None
        raise AssertionError(f'unexpected call {methode} {chemin}')


def monte(tmp_path, *, branche='release/AC-V1.7.0', tag='AC-V1.7.0', changelog=CHANGELOG, notes='Release text\n',
          assets=None, manifeste=True, manifest_edit=None, main_avance=False, hors_release=False, extra_asset=None,
          essai=False):
    """Depot origin nu, un commit C sur main, la branche release, puis un clone `action` sur la tete de la branche."""
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    w = tmp_path / 'work'
    subprocess.run(['git', 'clone', '-q', str(origin), str(w)], check=True, capture_output=True)
    git(w, 'checkout', '-q', '-b', 'main')
    (w / 'CHANGELOG.md').write_text(changelog, encoding='utf-8')
    git(w, 'add', 'CHANGELOG.md')
    git(w, 'commit', '-q', '-m', 'C')
    c = git(w, 'rev-parse', 'HEAD')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'checkout', '-q', '-b', branche)
    rel = w / '.release'
    (rel / 'assets').mkdir(parents=True)
    (rel / 'notes.md').write_text(notes, encoding='utf-8')
    if assets is None:
        assets = {'DCS_Airfield_Charts_Caucasus.zip': ZIP, 'DCS_Airfield_Charts_Syria.zip': ZIP + b'2'}
    liste = []
    for nom, data in assets.items():
        (rel / 'assets' / nom).write_bytes(data)
        liste.append({'name': nom, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    if extra_asset:
        (rel / 'assets' / extra_asset).write_bytes(b'x')
    man = {'tag': tag, 'title': TITRE, 'commit': c, 'assets': liste}
    if manifest_edit:
        manifest_edit(man)
    if manifeste:
        (rel / 'manifest.json').write_text(json.dumps(man), encoding='utf-8')
    if hors_release:
        (w / 'README.md').write_text('sneaky', encoding='utf-8')
        git(w, 'add', 'README.md')
    git(w, 'add', '-A')
    git(w, 'commit', '-q', '-m', 'release files')
    git(w, 'push', '-q', 'origin', branche)
    if main_avance:
        git(w, 'checkout', '-q', 'main')
        (w / 'CHANGELOG.md').write_text(changelog + '\nmore\n', encoding='utf-8')
        git(w, 'commit', '-q', '-am', 'later')
        git(w, 'push', '-q', 'origin', 'main')
    act = tmp_path / 'action'
    subprocess.run(['git', 'clone', '-q', str(origin), str(act)], check=True, capture_output=True)
    git(act, 'checkout', '-q', '--detach', f'origin/{branche}')
    api = FakeApi()
    deps = release.Deps(release.Git(act), api, REPO, TOKEN, now=lambda: 1795000000)
    return deps, api, origin, c, branche


def sur_origin(origin, branche, chemin):
    p = subprocess.run(['git', 'show', f'{branche}:{chemin}'], cwd=str(origin), capture_output=True, text=True, encoding='utf-8')
    return p.stdout if p.returncode == 0 else None


def branches(origin):
    return subprocess.run(['git', 'branch', '--format=%(refname:short)'], cwd=str(origin), capture_output=True, text=True).stdout.split()


# ------------------------------------------------------------------ publication

def test_publication_reussie(tmp_path):
    deps, api, origin, c, br = monte(tmp_path)
    assert release.run(deps, br) == 0
    base = f'/repos/{REPO}'
    up = f'https://uploads.github.com{base}/releases/101/assets?name='
    assert api.calls == [
        ('GET', f'{base}/git/ref/tags/AC-V1.7.0'),
        ('GET', f'{base}/releases?per_page=100&page=1'),
        ('POST', f'{base}/releases'),
        ('POST', up + 'DCS_Airfield_Charts_Caucasus.zip'),
        ('POST', up + 'DCS_Airfield_Charts_Syria.zip'),
        ('GET', f'{base}/releases/101/assets?per_page=100'),
        ('PATCH', f'{base}/releases/101'),
        ('GET', f'{base}/releases/tags/AC-V1.7.0'),
        ('GET', f'{base}/git/ref/tags/AC-V1.7.0'),
    ]
    b = api.bodies[0]
    assert b == {'tag_name': 'AC-V1.7.0', 'target_commitish': c, 'name': TITRE, 'body': 'Release text\n',
                 'draft': True, 'prerelease': False}
    r = api.releases[101]
    assert r['draft'] is False and api.tags['AC-V1.7.0'] == c
    assert br not in branches(origin)


def test_pas_de_manifeste_aucun_appel(tmp_path, capsys):
    deps, api, origin, c, br = monte(tmp_path, manifeste=False)
    assert release.run(deps, br) == 0
    assert api.calls == []
    assert 'Waiting for the manifest' in capsys.readouterr().out
    assert br in branches(origin)
    assert sur_origin(origin, br, '.release/FAILED.md') is None


def _m_tag_branche(m):
    m['tag'] = 'AC-V1.8.0'


def _m_cle_en_trop(m):
    m['extra'] = 1


# (cas, options de monte, controle attendu, API avec une release existante ?)
DEFAUTS = [
    ('1-tag-different-de-la-branche', {'manifest_edit': _m_tag_branche}, '1'),
    ('1-cle-inconnue', {'manifest_edit': _m_cle_en_trop}, '1'),
    ('1-nom-de-branche', {'branche': 'release/autre'}, '1'),
    ('2-fichier-hors-release', {'hors_release': True}, '2'),
    ('2-commit-pas-tete-de-main', {'main_avance': True}, '2'),
    ('3-changelog-sans-titre', {'changelog': '# Changelog\n\n## Unreleased\n'}, '3'),
    ('5-fichier-en-trop', {'extra_asset': 'DCS_Airfield_Charts_Extra.zip'}, '5'),
    ('5-nom-invalide', {'assets': {'mauvais nom.zip': ZIP}}, '5'),
    ('6-notes-vides', {'notes': '  \n'}, '6'),
]


@pytest.mark.parametrize('cas,opts,controle', DEFAUTS, ids=[d[0] for d in DEFAUTS])
def test_controle_en_defaut(tmp_path, cas, opts, controle):
    deps, api, origin, c, br = monte(tmp_path, **opts)
    assert release.run(deps, br) == 1
    assert api.writes == []
    f = sur_origin(origin, br, '.release/FAILED.md')
    assert f is not None and f'Control or step: {controle}' in f
    assert br in branches(origin)


def test_controle_4_tag_existant(tmp_path):
    deps, api, origin, c, br = monte(tmp_path)
    api.tags['AC-V1.7.0'] = 'a' * 40
    assert release.run(deps, br) == 1
    assert api.writes == []
    assert 'Control or step: 4' in sur_origin(origin, br, '.release/FAILED.md')


def test_controle_4_brouillon_existant(tmp_path):
    deps, api, origin, c, br = monte(tmp_path)
    api.releases[7] = {'id': 7, 'tag_name': 'AC-V1.7.0', 'draft': True}
    assert release.run(deps, br) == 1
    assert api.writes == []
    assert 'Control or step: 4' in sur_origin(origin, br, '.release/FAILED.md')


def test_controle_5_sha_different(tmp_path):
    def edit(m):
        m['assets'][0]['sha256'] = '0' * 64
    deps, api, origin, c, br = monte(tmp_path, manifest_edit=edit)
    assert release.run(deps, br) == 1
    assert api.writes == []
    assert 'SHA-256' in sur_origin(origin, br, '.release/FAILED.md')


def test_controle_5_taille_differente(tmp_path):
    def edit(m):
        m['assets'][0]['size'] += 1
    deps, api, origin, c, br = monte(tmp_path, manifest_edit=edit)
    assert release.run(deps, br) == 1
    assert 'size' in sur_origin(origin, br, '.release/FAILED.md')


def test_controle_5_aucune_entree(tmp_path):
    deps, api, origin, c, br = monte(tmp_path, assets={})
    assert release.run(deps, br) == 1
    assert 'no asset' in sur_origin(origin, br, '.release/FAILED.md')


def test_televersement_en_echec_supprime_le_brouillon(tmp_path):
    deps, api, origin, c, br = monte(tmp_path)
    api.fail[('POST', 'uploads.github.com')] = (500, {'message': 'boom'})
    assert release.run(deps, br) == 1
    assert [m for m, _ in api.writes] == ['POST', 'POST', 'DELETE']
    assert api.releases == {}
    f = sur_origin(origin, br, '.release/FAILED.md')
    assert 'step 2' in f and 'draft release was deleted' in f
    assert br in branches(origin)


# ------------------------------------------------------------------ essai

def test_essai_reussi(tmp_path):
    deps, api, origin, c, br = monte(tmp_path, branche='release/essai-un', main_avance=True)
    assert release.run(deps, br) == 0
    assert api.writes == []
    check = sur_origin(origin, br, '.release/CHECK.md')
    lignes = check.strip().splitlines()
    assert len(lignes) == 6 and all(' OK ' in l for l in lignes)
    assert 'head of main: no' in lignes[1]
    assert br in branches(origin)
    assert sur_origin(origin, br, '.release/FAILED.md') is None


def test_essai_c_tete_de_main(tmp_path):
    deps, api, origin, c, br = monte(tmp_path, branche='release/essai-un')
    assert release.run(deps, br) == 0
    assert 'head of main: yes' in sur_origin(origin, br, '.release/CHECK.md')


def test_essai_en_defaut(tmp_path):
    deps, api, origin, c, br = monte(tmp_path, branche='release/essai-un', notes='')
    assert release.run(deps, br) == 1
    assert api.writes == []
    lignes = sur_origin(origin, br, '.release/CHECK.md').strip().splitlines()
    assert lignes[-1].startswith('6. FAILED') and all(' OK ' in l for l in lignes[:-1])
    assert sur_origin(origin, br, '.release/FAILED.md') is None


# ------------------------------------------------------------------ le jeton

def _b64():
    return base64.b64encode(f'x-access-token:{TOKEN}'.encode()).decode()


@pytest.mark.parametrize('essai', [False, True])
def test_jeton_jamais_ecrit(tmp_path, capsys, essai):
    opts = {'branche': 'release/essai-un'} if essai else {}
    deps, api, origin, c, br = monte(tmp_path, **opts)
    # l'API renvoie des erreurs qui citent le jeton (et sa forme base64) : au controle 4, puis a la creation du brouillon
    api.fail[('GET', 'git/ref/tags')] = (401, {'message': f'Bad credentials {TOKEN} {_b64()}'})
    assert release.run(deps, br) == 1
    sortie = capsys.readouterr().out
    nom = 'CHECK.md' if essai else 'FAILED.md'
    texte = sur_origin(origin, br, f'.release/{nom}')
    assert 'Bad credentials' in texte
    for t in (sortie, texte):
        assert TOKEN not in t and _b64() not in t


def test_jeton_dans_une_exception_et_au_televersement(tmp_path, capsys):
    deps, api, origin, c, br = monte(tmp_path)
    api.fail[('POST', 'uploads.github.com')] = (403, {'message': f'denied for {TOKEN}'})
    assert release.run(deps, br) == 1
    api.fail.clear()

    class Boum(FakeApi):
        def request(self, *a, **k):
            raise OSError(f'connection with {TOKEN} lost')
    deps2, api2, origin2, c2, br2 = monte(tmp_path / 'b')
    deps2.api = Boum()
    assert release.run(deps2, br2) == 1
    sortie = capsys.readouterr().out
    for o, b in ((origin, br), (origin2, br2)):
        assert TOKEN not in sur_origin(o, b, '.release/FAILED.md')
    assert TOKEN not in sortie and 'connection with' in sortie


def test_succes_sans_jeton_dans_la_sortie(tmp_path, capsys):
    deps, api, origin, c, br = monte(tmp_path)
    assert release.run(deps, br) == 0
    assert TOKEN not in capsys.readouterr().out


# ------------------------------------------------------------------ le workflow

def test_workflow():
    yaml = pytest.importorskip('yaml')
    w = yaml.safe_load((ROOT / '.github' / 'workflows' / 'release.yml').read_text(encoding='utf-8'))
    assert w['permissions'] == {'contents': 'write'}
    assert w[True]['push']['branches'] == ['release/**']          # `on` est lu comme True par YAML 1.1
    assert 'group' in w['concurrency']
    usages = [s['uses'] for j in w['jobs'].values() for s in j['steps'] if 'uses' in s]
    assert usages == ['actions/checkout@v4']
    assert all(j['runs-on'] == 'ubuntu-latest' and 'permissions' not in j for j in w['jobs'].values())
    assert any('tools/release.py' in s.get('run', '') for j in w['jobs'].values() for s in j['steps'])
