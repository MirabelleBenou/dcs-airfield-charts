"""Pilote de la file de captures de nuit (Windows pour l'execution reelle, Python 3.12, bibliotheque standard seulement).

  python -I capture/autonome/nuit.py verifier <nuit.json>   valide la file et affiche les commandes depliees (code 0, sinon 2)
  python -I capture/autonome/nuit.py jouer    <nuit.json>   joue la file (lance par le crochet ChartsAutonome-hook.lua)

Le dossier de la nuit est <work_dir>/autonome/ (le work_dir de la configuration de capture). Le pilote joue les commandes des
outils existants (f10capture.py, signcapture.py) sans les modifier, demande au crochet de poser la vue F10 ou F11 (boite aux lettres
vue_demande.txt / vue_faite.txt), surveille son pouls (battement.txt), rassemble les fichiers de la nuit en zips, les depose en option
comme fichiers d'une release GitHub, puis dit au crochet de fermer DCS (fin.txt).

Tout ce qui est propre a Windows (veille, ecran, arbre de processus, arret du PC), le lancement des outils, l'horloge, les attentes et
la racine de l'API passent par l'objet Deps, que les tests remplacent. Ce module s'importe sous Linux sans erreur.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

CAPTURE_DIR = Path(__file__).resolve().parent.parent

BUDGET_DEFAUT_S = 12600
DELAI_DEFAUT_S = 3600
TAILLE_ZIP_DEFAUT = 1900000000
POULS_GRACE_S = 60
POULS_MAX_S = 300
VUE_ATTENTE_S = 30
VUE_STABILISATION_S = 5
ESSAIS_TELEVERSEMENT = 3
API_DEFAUT = 'https://api.github.com'

TYPES = ('charts', 'contexte', 'panneaux', 'theatre')
CLES_FILE = ('etapes', 'budget_captures_s', 'delai_commande_s', 'eteindre', 'ecran_eteint', 'taille_zip_max_o', 'config', 'tampon')
CLES_ETAPE = ('type', 'theatre', 'bases', 'marge')
CLES_TAMPON = ('depot', 'jeton', 'release')


class FileErreur(Exception):
    """La file est refusee ; le message nomme la faute."""


# ------------------------------------------------------------------ la file

def _nombre_positif(valeur, nom):
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not valeur > 0:
        raise FileErreur(f'"{nom}" doit etre un nombre superieur a 0')
    return valeur


def _texte(valeur, nom):
    if not isinstance(valeur, str) or not valeur.strip():
        raise FileErreur(f'"{nom}" doit etre une chaine non vide')
    return valeur


def valider_file(data):
    """Valide la file lue du JSON (section 2.3). Retourne un dict complet, defauts compris. Leve FileErreur."""
    if not isinstance(data, dict):
        raise FileErreur('la file doit etre un objet JSON')
    for k in data:
        if k not in CLES_FILE:
            raise FileErreur(f'cle inconnue : "{k}"')
    etapes = data.get('etapes')
    if not isinstance(etapes, list) or not etapes:
        raise FileErreur('"etapes" est obligatoire : une liste non vide')
    out = {'etapes': []}
    for i, e in enumerate(etapes):
        nom = f'etapes[{i}]'
        if not isinstance(e, dict):
            raise FileErreur(f'{nom} doit etre un objet')
        for k in e:
            if k not in CLES_ETAPE:
                raise FileErreur(f'{nom} : cle inconnue : "{k}"')
        t = e.get('type')
        if t not in TYPES:
            raise FileErreur(f'{nom} : "type" doit etre l\'un de {", ".join(TYPES)}')
        try:
            theatre = _texte(e.get('theatre'), 'theatre')
            bases = e.get('bases')
            if (not isinstance(bases, list) or not bases
                    or not all(isinstance(b, str) and b.strip() for b in bases)):
                raise FileErreur('"bases" doit etre une liste non vide de chaines')
            etape = {'type': t, 'theatre': theatre, 'bases': list(bases)}
            if t == 'contexte':
                if 'marge' not in e:
                    raise FileErreur('"marge" manquante pour le type contexte')
                etape['marge'] = _nombre_positif(e['marge'], 'marge')
            elif 'marge' in e:
                raise FileErreur(f'"marge" interdite pour le type {t}')
        except FileErreur as err:
            raise FileErreur(f'{nom} : {err}') from None
        out['etapes'].append(etape)
    out['budget_captures_s'] = _nombre_positif(data['budget_captures_s'], 'budget_captures_s') if 'budget_captures_s' in data else BUDGET_DEFAUT_S
    out['delai_commande_s'] = _nombre_positif(data['delai_commande_s'], 'delai_commande_s') if 'delai_commande_s' in data else DELAI_DEFAUT_S
    for k in ('eteindre', 'ecran_eteint'):
        if k in data and not isinstance(data[k], bool):
            raise FileErreur(f'"{k}" doit etre true ou false')
        out[k] = data.get(k, False)
    if 'taille_zip_max_o' in data:
        v = data['taille_zip_max_o']
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise FileErreur('"taille_zip_max_o" doit etre un entier superieur a 0')
        out['taille_zip_max_o'] = v
    else:
        out['taille_zip_max_o'] = TAILLE_ZIP_DEFAUT
    out['config'] = _texte(data['config'], 'config') if 'config' in data else None
    out['tampon'] = None
    if 'tampon' in data:
        t = data['tampon']
        if not isinstance(t, dict):
            raise FileErreur('"tampon" doit etre un objet')
        for k in t:
            if k not in CLES_TAMPON:
                raise FileErreur(f'tampon : cle inconnue : "{k}"')
        for k in ('depot', 'jeton'):
            if k not in t:
                raise FileErreur(f'tampon : "{k}" manquante')
        depot = _texte(t['depot'], 'tampon.depot')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', depot):
            raise FileErreur('"tampon.depot" doit etre <proprietaire>/<depot>')
        out['tampon'] = {'depot': depot, 'jeton': _texte(t['jeton'], 'tampon.jeton'),
                         'release': _texte(t['release'], 'tampon.release') if 'release' in t else 'nuit'}
    return out


def lire_file(chemin):
    try:
        text = Path(chemin).read_text(encoding='utf-8-sig')
    except OSError as e:
        raise FileErreur(f'file illisible : {chemin} ({e.strerror or e})') from None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise FileErreur(f'file invalide (JSON) : {chemin} ({e})') from None
    return valider_file(data)


def _nom_fichier(texte):
    return re.sub(r'[^A-Za-z0-9._-]', '_', texte)


def _marge_texte(m):
    return str(int(m)) if float(m).is_integer() else repr(float(m))


def deplier(file):
    """Les commandes de la file, dans l'ordre. Chacune : dict n, outil (fichier), commande, theatre, base, args, vue, depend_de."""
    cmds = []

    def ajouter(outil, commande, theatre, base, args, vue, depend_de=None):
        cmds.append({'n': len(cmds) + 1, 'outil': outil, 'commande': commande, 'theatre': theatre, 'base': base,
                     'args': args, 'vue': vue, 'depend_de': depend_de})

    for i, e in enumerate(file['etapes']):
        t, th = e['type'], e['theatre']
        if t == 'theatre':
            raise FileErreur(f'etapes[{i}] : le type "theatre" n\'est pas deplie par cet outil (la commande f10capture.py theatre '
                             f'attend des reponses)')
        for b in e['bases']:
            if t == 'charts':
                ajouter('f10capture.py', 'capture', th, b, ['capture', th, b], 'f10')
            elif t == 'contexte':
                ajouter('f10capture.py', 'capture', th, b, ['capture', th, b, '--marge', _marge_texte(e['marge'])], 'f10')
            else:
                ajouter('signcapture.py', 'visee', th, b, ['visee', th, b], 'f11')
                ajouter('signcapture.py', 'capture', th, b, ['capture', th, b], 'f11', depend_de=len(cmds))
    return cmds


def ligne_commande(c, file):
    args = list(c['args'])
    if file['config']:
        args += ['--config', file['config']]
    return c['outil'] + ' ' + ' '.join(args)


def cmd_verifier(chemin, out=None):
    out = out or sys.stdout
    try:
        file = lire_file(chemin)
        cmds = deplier(file)
    except FileErreur as e:
        print(f'Erreur : {e}', file=sys.stderr)
        return 2
    vue = None
    print(f'{len(cmds)} commande(s) :', file=out)
    for c in cmds:
        if c['vue'] != vue:
            vue = c['vue']
            print(f'    -> vue {vue} demandee', file=out)
        print(f'{c["n"]:02d}  [{c["vue"]}]  {ligne_commande(c, file)}', file=out)
    return 0


# ------------------------------------------------------------------ ce qui est propre a Windows

def veille_windows(actif):
    """Empeche la mise en veille du PC (pas celle de l'ecran) tant que le fil tourne ; actif=False rend la main."""
    if os.name != 'nt':
        return
    import ctypes
    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if actif else 0))


def ecran_windows():
    """Eteint l'ecran (SC_MONITORPOWER, 2 = eteint)."""
    if os.name != 'nt':
        return
    import ctypes
    ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)


def tuer_arbre_windows(p):
    """Tue le processus et ses fils."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        import signal
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()


def arret_windows():
    if os.name == 'nt':
        subprocess.run(['shutdown', '/s', '/t', '120'], check=False)


def lancer_reel(argv, log_path, timeout_s, cwd, tuer_arbre):
    """Lance une commande, sortie et erreurs ajoutees a log_path. Retourne (code, delai_depasse)."""
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    with open(log_path, 'ab') as log:
        p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd, **kw)
        try:
            return p.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            tuer_arbre(p)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            return None, True


class Deps:
    """Tout ce que les tests remplacent. Les valeurs par defaut sont les vraies."""

    def __init__(self, **kw):
        self.now = time.time                    # secondes depuis 1970
        self.sleep = time.sleep
        self.lancer = lancer_reel               # (argv, log_path, timeout_s, cwd, tuer_arbre) -> (code ou None, delai_depasse)
        self.tuer_arbre = tuer_arbre_windows
        self.veille = veille_windows
        self.ecran = ecran_windows
        self.shutdown = arret_windows
        self.api = API_DEFAUT
        self.opener = None                      # urllib.request.OpenerDirector ; None : celui d'urllib
        self.python = sys.executable
        self.capture_dir = CAPTURE_DIR
        self.attente_depot_s = 5                # attente entre deux essais de televersement
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError(f'Deps : champ inconnu {k}')
            setattr(self, k, v)


# ------------------------------------------------------------------ le rassemblement

def _iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t))


def _est_image(nom):
    return nom.lower().endswith(('.png', '.jpg', '.jpeg'))


def rassembler(work_dir, nuit_dir, t_nuit):
    """Fichiers de la nuit : [(chemin, nom dans le zip)] triee. Modifies depuis t_nuit sous out/, panneaux/, taxiways_dcs/,
    plus tous les fichiers du dossier de la nuit (journaux, bilan.json ; pas les zips)."""
    work_dir, nuit_dir = Path(work_dir), Path(nuit_dir)
    trouves = {}
    racines = [work_dir / 'out', work_dir / 'panneaux', work_dir / 'taxiways_dcs', nuit_dir]
    for racine in racines:
        if not racine.is_dir():
            continue
        for dossier, _, noms in os.walk(racine):
            for nom in noms:
                p = Path(dossier) / nom
                if racine == nuit_dir and nom.lower().endswith('.zip'):
                    continue
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    if racine != nuit_dir and p.stat().st_mtime < t_nuit:
                        continue
                except OSError:
                    continue
                trouves[p.relative_to(work_dir).as_posix()] = p
    return [(trouves[a], a) for a in sorted(trouves)]


def _cout_zip(chemin, arcname):
    """Taille de l'entree dans un zip (majorant : en-tetes, repertoire central, zip64)."""
    taille = chemin.stat().st_size
    if _est_image(arcname):
        data = taille
    else:
        c = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        data = 0
        with open(chemin, 'rb') as f:
            for bloc in iter(lambda: f.read(1 << 20), b''):
                data += len(c.compress(bloc))
        data += len(c.flush())
    return data + 130 + 2 * len(arcname.encode('utf-8'))


ZIP_FIXE = 100


def planifier_zips(fichiers, limite):
    """Repartit les fichiers en zips. Retourne (groupes, noms des fichiers plus gros que la limite)."""
    groupes, courant, cout, trop = [], [], ZIP_FIXE, []
    for chemin, arc in fichiers:
        c = _cout_zip(chemin, arc)
        if ZIP_FIXE + c > limite:
            if courant:
                groupes.append(courant)
            groupes.append([(chemin, arc)])
            trop.append(arc)
            courant, cout = [], ZIP_FIXE
            continue
        if courant and cout + c > limite:
            groupes.append(courant)
            courant, cout = [], ZIP_FIXE
        courant.append((chemin, arc))
        cout += c
    if courant:
        groupes.append(courant)
    return groupes, trop


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def ecrire_zips(groupes, nuit_dir, stamp):
    zips = []
    for n, groupe in enumerate(groupes, 1):
        nom = f'nuit_{stamp}_{n}.zip'
        chemin = Path(nuit_dir) / nom
        with zipfile.ZipFile(chemin, 'w') as zf:
            for p, arc in groupe:
                zf.write(p, arc, compress_type=zipfile.ZIP_STORED if _est_image(arc) else zipfile.ZIP_DEFLATED)
        zips.append({'nom': nom, 'taille_o': chemin.stat().st_size, 'sha256': sha256_fichier(chemin), 'fichiers': len(groupe)})
    return zips


# ------------------------------------------------------------------ le depot (API REST de GitHub)

class DepotErreur(Exception):
    pass


class Depot:
    def __init__(self, depot, release, jeton, deps):
        self.depot, self.release, self._jeton, self.deps = depot, release, jeton, deps
        self.api = deps.api.rstrip('/')

    def _nettoyer(self, texte):
        texte = str(texte)
        return texte.replace(self._jeton, '***') if self._jeton else texte

    def _entetes(self, extra=None):
        h = {'Authorization': f'Bearer {self._jeton}', 'Accept': 'application/vnd.github+json',
             'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'dcs-airfield-charts-nuit'}
        h.update(extra or {})
        return h

    def _requete(self, methode, url, data=None, extra=None):
        req = urllib.request.Request(url, data=data, method=methode, headers=self._entetes(extra))
        ouvrir = (self.deps.opener or urllib.request.build_opener()).open
        try:
            with ouvrir(req, timeout=600) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise DepotErreur(f'{methode} {urllib.parse.urlsplit(url).path} : {self._nettoyer(e)}') from None

    def _erreur(self, quoi, code, corps):
        txt = corps.decode('utf-8', 'replace')[:300] if isinstance(corps, bytes) else str(corps)
        return DepotErreur(self._nettoyer(f'{quoi} : HTTP {code} {txt}'))

    def _json(self, methode, chemin, charge=None, ok=(200, 201, 204), absent=()):
        data = json.dumps(charge).encode('utf-8') if charge is not None else None
        code, corps = self._requete(methode, self.api + chemin, data, {'Content-Type': 'application/json'} if data else None)
        if code in absent:
            return code, None
        if code not in ok:
            raise self._erreur(f'{methode} {chemin}', code, corps)
        return code, (json.loads(corps) if corps else None)

    def remplacer_release(self, body):
        """Supprime la release de l'etiquette (si elle existe), puis l'etiquette, puis cree la release. Retourne son upload_url."""
        base = f'/repos/{self.depot}'
        tag = urllib.parse.quote(self.release, safe='')
        code, rel = self._json('GET', f'{base}/releases/tags/{tag}', ok=(200,), absent=(404,))
        if code == 200 and isinstance(rel, dict) and rel.get('id') is not None:
            self._json('DELETE', f'{base}/releases/{rel["id"]}', ok=(204,), absent=(404,))
        self._json('DELETE', f'{base}/git/refs/tags/{tag}', ok=(204,), absent=(404, 422))
        _, cree = self._json('POST', f'{base}/releases', {'tag_name': self.release, 'name': self.release, 'prerelease': True,
                                                          'body': body}, ok=(201,))
        if not isinstance(cree, dict) or not cree.get('upload_url'):
            raise DepotErreur('la release creee ne donne pas d\'upload_url')
        url = cree['upload_url'].split('{')[0]
        hote = urllib.parse.urlsplit(url).netloc
        if hote not in (urllib.parse.urlsplit(self.api).netloc, 'uploads.github.com'):
            raise DepotErreur(f'upload_url vers un hote inattendu : {hote}')
        return url

    def televerser(self, upload_url, chemin, type_mime):
        taille = Path(chemin).stat().st_size
        url = upload_url + '?' + urllib.parse.urlencode({'name': Path(chemin).name})
        derniere = None
        for essai in range(1, ESSAIS_TELEVERSEMENT + 1):
            try:
                with open(chemin, 'rb') as f:
                    code, corps = self._requete('POST', url, f, {'Content-Type': type_mime, 'Content-Length': str(taille)})
                if code == 201:
                    return
                derniere = self._erreur(f'televersement de {Path(chemin).name}', code, corps)
            except DepotErreur as e:
                derniere = e
            if essai < ESSAIS_TELEVERSEMENT:
                self.deps.sleep(self.deps.attente_depot_s)
        raise derniere


def lire_jeton(chemin):
    try:
        for ligne in Path(chemin).expanduser().read_text(encoding='utf-8-sig', errors='replace').splitlines():
            if ligne.strip():
                return ligne.strip()
    except OSError:
        pass
    return None


def corps_release(cause, commandes):
    comptes = {}
    for c in commandes:
        comptes[c['statut']] = comptes.get(c['statut'], 0) + 1
    return '\n'.join([f'cause : {cause}'] + [f'{s} : {n}' for s, n in sorted(comptes.items())])


def deposer(tampon, bilan, bilan_path, zips_paths, deps):
    """Retourne le resultat du depot : {'statut': 'ok'|'echec'|'sans_jeton', ...}. Ne leve jamais ; le jeton n'en sort jamais."""
    jeton = lire_jeton(tampon['jeton'])
    if not jeton:
        return {'statut': 'sans_jeton'}
    depot = Depot(tampon['depot'], tampon['release'], jeton, deps)
    try:
        url = depot.remplacer_release(corps_release(bilan['cause'], bilan['commandes']))
        depot.televerser(url, bilan_path, 'application/json')
        for z in zips_paths:
            depot.televerser(url, z, 'application/zip')
    except DepotErreur as e:
        return {'statut': 'echec', 'detail': depot._nettoyer(e)}
    except Exception as e:           # jamais de trace qui pourrait porter le jeton
        return {'statut': 'echec', 'detail': depot._nettoyer(f'{type(e).__name__}: {e}')}
    return {'statut': 'ok', 'depot': tampon['depot'], 'release': tampon['release']}


# ------------------------------------------------------------------ la nuit

def _ecrire(chemin, texte):
    Path(chemin).write_text(texte, encoding='utf-8', newline='\n')


def _fin_journal(chemin, n=10):
    try:
        with open(chemin, 'r', encoding='utf-8', errors='replace') as f:
            return [l.rstrip('\r\n') for l in f.read().splitlines()[-n:]]
    except OSError:
        return []


class Nuit:
    def __init__(self, file, cfg, deps, out=None):
        self.file, self.cfg, self.deps = file, cfg, deps
        self.out = out or sys.stdout
        self.work_dir = Path(cfg.work_dir)
        self.dir = self.work_dir / 'autonome'
        self.vue_posee = None
        self.t_nuit = None
        self.nuit_dir = None
        self.journal_path = None

    # -- petits services
    def jr(self, msg):
        ligne = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.deps.now())) + ' ' + msg
        print(ligne, file=self.out, flush=True)
        if self.journal_path:
            with open(self.journal_path, 'a', encoding='utf-8', newline='\n') as f:
                f.write(ligne + '\n')

    def lire_mot(self, nom):
        try:
            return (self.dir / nom).read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            return None

    def supprimer(self, nom):
        try:
            (self.dir / nom).unlink()
        except OSError:
            pass

    # -- les controles d'avant commande
    def pouls_ok(self):
        t = self.deps.now()
        if t - self.t_nuit < POULS_GRACE_S:
            return True
        v = self.lire_mot('battement.txt')
        try:
            return t - float(v) <= POULS_MAX_S
        except (TypeError, ValueError):
            return False

    def budget_depasse(self):
        return self.deps.now() - self.t_nuit > self.file['budget_captures_s']

    def poser_vue(self, vue):
        """Demande la vue au crochet ; True si posee (puis 5 s de stabilisation)."""
        self.supprimer('vue_faite.txt')
        _ecrire(self.dir / 'vue_demande.txt', vue)
        debut = self.deps.now()
        reponse = None
        while True:
            reponse = self.lire_mot('vue_faite.txt')
            if reponse is not None:
                self.supprimer('vue_faite.txt')
                break
            if self.deps.now() - debut >= VUE_ATTENTE_S:
                break
            self.deps.sleep(0.5)
        if reponse != vue:
            self.supprimer('vue_demande.txt')
            self.jr(f'vue {vue} : ' + ('pas de reponse du crochet' if reponse is None else f'reponse "{reponse}"'))
            return False
        self.deps.sleep(VUE_STABILISATION_S)
        premiere = self.vue_posee is None
        self.vue_posee = vue
        self.jr(f'vue {vue} posee')
        if premiere and self.file['ecran_eteint']:
            self.deps.ecran()
            self.jr('ecran eteint')
        return True

    # -- une commande
    def executer(self, c, res):
        argv = [self.deps.python, '-I', str(Path(self.deps.capture_dir) / c['outil'])] + list(c['args'])
        if self.file['config']:
            argv += ['--config', self.file['config']]
        nom = f'{c["n"]:02d}_{c["outil"][:-3]}_{c["commande"]}_{_nom_fichier(c["base"])}.log'
        log = self.nuit_dir / nom
        _ecrire(log, '# ' + ' '.join(argv) + '\n')
        res['journal'] = nom
        debut = self.deps.now()
        res['debut'] = _iso(debut)
        self.jr(f'{c["n"]:02d} lance : {ligne_commande(c, self.file)}')
        try:
            code, delai = self.deps.lancer(argv, log, self.file['delai_commande_s'], str(Path(self.deps.capture_dir).parent),
                                           self.deps.tuer_arbre)
        except OSError as e:
            code, delai = None, False
            with open(log, 'a', encoding='utf-8') as f:
                f.write(f'[nuit] lancement impossible : {e}\n')
        res['duree_s'] = round(self.deps.now() - debut, 1)
        res['code'] = code
        if delai:
            with open(log, 'a', encoding='utf-8') as f:
                f.write(f'\n[nuit] delai de {self.file["delai_commande_s"]} s depasse : processus tue\n')
            res['statut'] = 'delai'
        elif code == 0:
            res['statut'] = 'ok'
        elif code == 3 and (self.work_dir / 'sync' / 'QUESTION.txt').is_file():
            q = self.work_dir / 'sync' / 'QUESTION.txt'
            res['question'] = q.read_text(encoding='utf-8', errors='replace').strip()
            try:
                q.unlink()
            except OSError:
                pass
            res['statut'] = 'question'
        else:
            res['statut'] = 'echec'
        res['fin_journal'] = _fin_journal(log)
        self.jr(f'{c["n"]:02d} {res["statut"]} (code {code}, {res["duree_s"]} s)')

    # -- la nuit entiere
    def bilan_base(self, cmds, resultats, cause, fin):
        return {'file': self.file, 't_nuit': _iso(self.t_nuit), 't_nuit_epoch': self.t_nuit, 'fin': _iso(fin), 'cause': cause,
                'dossier': self.nuit_dir.name, 'commandes': resultats, 'zips': [], 'fichiers_trop_gros': [],
                'depot': {'statut': 'a_faire' if self.file['tampon'] else 'sans_depot'}}

    def jouer(self):
        d = self.deps
        self.t_nuit = d.now()
        stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(self.t_nuit))
        self.nuit_dir = self.dir / 'nuits' / stamp
        self.nuit_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.nuit_dir / 'nuit.log'
        cmds = deplier(self.file)
        resultats = [{'n': c['n'], 'outil': c['outil'], 'arguments': c['args'] + (['--config', self.file['config']] if self.file['config'] else []),
                      'vue': c['vue'], 'statut': None, 'debut': None, 'duree_s': None, 'code': None, 'fin_journal': []}
                     for c in cmds]
        for nom in ('vue_demande.txt', 'vue_faite.txt'):
            self.supprimer(nom)
        cause = 'file_finie'
        eteindre = self.file['eteindre']
        d.veille(True)
        try:
            self.jr(f'nuit {stamp} : {len(cmds)} commande(s), budget {self.file["budget_captures_s"]} s')
            arret = None
            for c, res in zip(cmds, resultats):
                if arret is None:
                    if not self.pouls_ok():
                        arret, cause = 'dcs_absent', 'dcs_absent'
                        self.jr('pouls absent ou trop ancien : DCS est tenu pour absent, la file s\'arrete')
                    elif self.budget_depasse():
                        arret, cause = 'saute_budget', 'budget'
                        self.jr('budget depasse : la file s\'arrete')
                if arret is not None:
                    res['statut'] = arret
                    continue
                if c['depend_de'] and resultats[c['depend_de'] - 1]['statut'] != 'ok':
                    res['statut'] = 'saute_visee'
                    self.jr(f'{c["n"]:02d} non lancee : la visee a echoue')
                    continue
                if c['vue'] != self.vue_posee and not self.poser_vue(c['vue']):
                    res['statut'] = 'vue_echec'
                    continue
                self.executer(c, res)
            fin = d.now()
            self.jr(f'fin de la file : {cause}')
            bilan = self.bilan_base(cmds, resultats, cause, fin)
            bilan_path = self.nuit_dir / 'bilan.json'
            self.ecrire_bilan(bilan)
            # rassemblement : le bilan (sans les zips) est range dans les zips, puis reecrit avec leurs empreintes
            fichiers = rassembler(self.work_dir, self.nuit_dir, self.t_nuit)
            groupes, trop = planifier_zips(fichiers, self.file['taille_zip_max_o'])
            bilan['fichiers_trop_gros'] = trop
            zips = ecrire_zips(groupes, self.nuit_dir, stamp)
            bilan['zips'] = zips
            self.ecrire_bilan(bilan)
            for z in zips:
                self.jr(f'zip {z["nom"]} : {z["fichiers"]} fichier(s), {z["taille_o"]} o')
            if self.file['tampon']:
                bilan['depot'] = deposer(self.file['tampon'], bilan, bilan_path, [self.nuit_dir / z['nom'] for z in zips], d)
                self.jr(f'depot : {bilan["depot"]["statut"]}' + (f' ({bilan["depot"]["detail"]})' if bilan['depot'].get('detail') else ''))
                self.ecrire_bilan(bilan)
        finally:
            d.veille(False)
            try:
                _ecrire(self.dir / 'file.lua', 'return false\n')
                _ecrire(self.dir / 'fin.txt', 'eteindre' if eteindre else 'rester')
            except OSError as e:
                print(f'Erreur : fin de nuit non ecrite ({e})', file=sys.stderr)
        if cause == 'dcs_absent' and eteindre:
            self.jr('DCS absent : arret du PC')
            d.shutdown()
        return 0

    def ecrire_bilan(self, bilan):
        _ecrire(self.nuit_dir / 'bilan.json', json.dumps(bilan, ensure_ascii=False, indent=2) + '\n')


def cmd_jouer(chemin, deps=None, out=None):
    deps = deps or Deps()
    try:
        file = lire_file(chemin)
        deplier(file)
    except FileErreur as e:
        print(f'Erreur : {e}', file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import load_config
    argv = ['nuit.py'] + (['--config', file['config']] if file['config'] else [])
    try:
        cfg, _ = load_config(argv)
    except SystemExit:
        return 2
    try:
        return Nuit(file, cfg, deps, out).jouer()
    except Exception as e:
        print(f'Erreur : {type(e).__name__}: {e}', file=sys.stderr)
        return 1


def main(argv, deps=None):
    if len(argv) != 3 or argv[1] not in ('verifier', 'jouer'):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[1] == 'verifier':
        return cmd_verifier(argv[2])
    return cmd_jouer(argv[2], deps)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
