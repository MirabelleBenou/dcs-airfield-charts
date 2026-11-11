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
import contextlib
import hashlib
import io
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
TAILLE_ZIP_BORNE = 2147483647          # borne de taille_zip_max_o, et taille au-dela de laquelle un zip n'est pas televerse
BUDGET_MARGE_S = 1800                  # rassemblement et depot, pour le budget conseille du crochet
POULS_GRACE_S = 60
POULS_MAX_S = 300
POULS_RELECTURE_S = 2
VUE_ATTENTE_S = 30
VUE_STABILISATION_S = 5
ESSAIS_TELEVERSEMENT = 3
API_DEFAUT = 'https://api.github.com'

TYPES = ('charts', 'contexte', 'panneaux')
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
            raise FileErreur(f'{nom} : "type" doit etre l\'un de {", ".join(TYPES)} (recu : {t!r})')
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
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= TAILLE_ZIP_BORNE:
            raise FileErreur(f'"taille_zip_max_o" doit etre un entier entre 1 et {TAILLE_ZIP_BORNE}')
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


def _nombre_texte(v):
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def budget_conseille(file):
    """budget_s du crochet : au moins les captures, la derniere commande allant jusqu'a son delai, puis le rassemblement et le depot."""
    return file['budget_captures_s'] + file['delai_commande_s'] + BUDGET_MARGE_S


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
    print(f'budget_s du crochet conseille : {_nombre_texte(budget_conseille(file))} s au moins', file=out)
    return 0


# ------------------------------------------------------------------ ce qui est propre a Windows

def veille_windows(actif):
    """Empeche la mise en veille du PC (pas celle de l'ecran) tant que le fil tourne ; actif=False rend la main."""
    if os.name != 'nt':
        return
    import ctypes
    es_continuous, es_system_required = 0x80000000, 0x00000001
    f = ctypes.windll.kernel32.SetThreadExecutionState
    f.argtypes = [ctypes.c_uint]
    f(es_continuous | (es_system_required if actif else 0))


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
        kw['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
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
    """Fichiers de la nuit : ([(chemin, nom dans le zip)] trie, [fichiers sautes]). Modifies depuis t_nuit sous out/, panneaux/,
    taxiways_dcs/, plus tous les fichiers du dossier de la nuit (journaux, bilan.json ; pas les zips). Un fichier ou un dossier
    illisible ou verrouille est saute et note {chemin, raison} : le rassemblement ne perd jamais la nuit."""
    work_dir, nuit_dir = Path(work_dir), Path(nuit_dir)
    trouves, sautes = {}, []

    def saute(chemin, e):
        try:
            nom = Path(chemin).relative_to(work_dir).as_posix()
        except ValueError:
            nom = str(chemin)
        sautes.append({'chemin': nom, 'raison': f'{type(e).__name__}: {e.strerror or e}'})

    racines = [work_dir / 'out', work_dir / 'panneaux', work_dir / 'taxiways_dcs', nuit_dir]
    for racine in racines:
        if not racine.is_dir():
            continue
        for dossier, _, noms in os.walk(racine, onerror=lambda e: saute(e.filename, e)):
            for nom in noms:
                p = Path(dossier) / nom
                if racine == nuit_dir and nom.lower().endswith('.zip'):
                    continue
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    if racine != nuit_dir and p.stat().st_mtime < t_nuit:
                        continue
                    with open(p, 'rb'):
                        pass
                except OSError as e:
                    saute(p, e)
                    continue
                trouves[p.relative_to(work_dir).as_posix()] = p
    return [(trouves[a], a) for a in sorted(trouves)], sautes


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
    """Repartit les fichiers en zips. Retourne (groupes, noms des fichiers plus gros que la limite, fichiers sautes)."""
    groupes, courant, cout, trop, sautes = [], [], ZIP_FIXE, [], []
    for chemin, arc in fichiers:
        try:
            c = _cout_zip(chemin, arc)
        except OSError as e:
            sautes.append({'chemin': arc, 'raison': f'{type(e).__name__}: {e.strerror or e}'})
            continue
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
    return groupes, trop, sautes


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def ecrire_zips(groupes, nuit_dir, stamp):
    """Ecrit les zips ; retourne ([{nom, taille_o, sha256, fichiers}], [fichiers sautes]). Un fichier qui ne s'ouvre pas est saute."""
    zips, sautes = [], []
    for n, groupe in enumerate(groupes, 1):
        nom = f'nuit_{stamp}_{n}.zip'
        chemin = Path(nuit_dir) / nom
        ecrits = 0
        with zipfile.ZipFile(chemin, 'w', strict_timestamps=False) as zf:
            for p, arc in groupe:
                try:
                    zf.write(p, arc, compress_type=zipfile.ZIP_STORED if _est_image(arc) else zipfile.ZIP_DEFLATED)
                    ecrits += 1
                except OSError as e:
                    sautes.append({'chemin': arc, 'raison': f'{type(e).__name__}: {e.strerror or e}'})
        zips.append({'nom': nom, 'taille_o': chemin.stat().st_size, 'sha256': sha256_fichier(chemin), 'fichiers': ecrits})
    return zips, sautes


# ------------------------------------------------------------------ le depot (API REST de GitHub)

class DepotErreur(Exception):
    pass


class Depot:
    def __init__(self, depot, release, jeton, deps):
        self.depot, self.release, self._jeton, self.deps = depot, release, jeton, deps
        self.api = deps.api.rstrip('/')
        self.release_id = None

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
        if not isinstance(cree, dict) or not cree.get('upload_url') or cree.get('id') is None:
            raise DepotErreur('la release creee ne donne pas d\'id ou d\'upload_url')
        self.release_id = cree['id']
        url = cree['upload_url'].split('{')[0]
        hote = urllib.parse.urlsplit(url).netloc
        if hote not in (urllib.parse.urlsplit(self.api).netloc, 'uploads.github.com'):
            raise DepotErreur(f'upload_url vers un hote inattendu : {hote}')
        return url

    def retirer_asset(self, nom):
        """Supprime de la release le fichier de ce nom, s'il y est (reste d'un envoi partiel)."""
        base = f'/repos/{self.depot}'
        _, liste = self._json('GET', f'{base}/releases/{self.release_id}/assets?per_page=100', ok=(200,))
        for a in liste or []:
            if isinstance(a, dict) and a.get('name') == nom and a.get('id') is not None:
                self._json('DELETE', f'{base}/releases/assets/{a["id"]}', ok=(204,), absent=(404,))

    def televerser(self, upload_url, chemin, type_mime):
        nom = Path(chemin).name
        taille = Path(chemin).stat().st_size
        url = upload_url + '?' + urllib.parse.urlencode({'name': nom})
        derniere = None
        for essai in range(1, ESSAIS_TELEVERSEMENT + 1):
            try:
                if essai > 1:
                    self.deps.sleep(self.deps.attente_depot_s)
                    self.retirer_asset(nom)               # un envoi partiel a pu laisser le fichier dans la release
                with open(chemin, 'rb') as f:
                    code, corps = self._requete('POST', url, f, {'Content-Type': type_mime, 'Content-Length': str(taille)})
                if code == 201:
                    return
                derniere = self._erreur(f'televersement de {nom}', code, corps)
            except DepotErreur as e:
                derniere = e
            except OSError as e:
                derniere = DepotErreur(self._nettoyer(f'lecture de {nom} : {e}'))
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
    """Retourne le resultat du depot : {'statut': 'ok'|'echec'|'sans_jeton', ...}. Ne leve jamais ; le jeton n'en sort jamais.
    Un fichier perdu (3 essais) n'arrete pas les autres ; un zip de plus de TAILLE_ZIP_BORNE o n'est pas televerse."""
    jeton = lire_jeton(tampon['jeton'])
    if not jeton:
        return {'statut': 'sans_jeton'}
    depot = Depot(tampon['depot'], tampon['release'], jeton, deps)
    non = []
    try:
        url = depot.remplacer_release(corps_release(bilan['cause'], bilan['commandes']))
        envois = [(bilan_path, 'application/json')] + [(z, 'application/zip') for z in zips_paths]
        for chemin, mime in envois:
            nom = Path(chemin).name
            if mime == 'application/zip' and Path(chemin).stat().st_size > TAILLE_ZIP_BORNE:
                raison = f'plus de {TAILLE_ZIP_BORNE} o : non televerse'
                for z in bilan['zips']:
                    if z['nom'] == nom:
                        z['non_depose'] = raison
                non.append(f'{nom} ({raison})')
                continue
            try:
                depot.televerser(url, chemin, mime)
            except DepotErreur as e:
                non.append(f'{nom} ({e})')
    except DepotErreur as e:
        return {'statut': 'echec', 'detail': depot._nettoyer(e)}
    except Exception as e:           # jamais de trace qui pourrait porter le jeton
        return {'statut': 'echec', 'detail': depot._nettoyer(f'{type(e).__name__}: {e}')}
    if non:
        return {'statut': 'echec', 'detail': depot._nettoyer('fichiers non deposes : ' + '; '.join(non))}
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


def ecrire_echec(dossier, cause):
    """Un echec avant la boucle laisse une trace et rend la main a DCS : erreur.txt (la cause, une ligne), file.lua a
    `return false`, fin.txt a `rester`, dans le dossier de la nuit (ou a cote de la file)."""
    dossier = Path(dossier)
    ligne = ' '.join(str(cause).split())
    for nom, texte in (('erreur.txt', ligne + '\n'), ('file.lua', 'return false\n'), ('fin.txt', 'rester')):
        try:
            dossier.mkdir(parents=True, exist_ok=True)
            _ecrire(dossier / nom, texte)
        except OSError as e:
            print(f'Erreur : {nom} non ecrit dans {dossier} ({e})', file=sys.stderr)


class Nuit:
    def __init__(self, file, cfg, deps, out=None):
        self.file, self.cfg, self.deps = file, cfg, deps
        self.out = out or sys.stdout
        self.work_dir = Path(cfg.work_dir)
        self.dir = self.work_dir / 'autonome'
        self.vue_posee = None
        self.t_nuit = None
        self.nuit_dir = None
        self.stamp = None
        self.journal_path = None

    # -- petits services
    def jr(self, msg):
        ligne = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.deps.now())) + ' ' + msg
        print(ligne, file=self.out, flush=True)
        if self.journal_path:
            try:
                with open(self.journal_path, 'a', encoding='utf-8', newline='\n') as f:
                    f.write(ligne + '\n')
            except OSError:
                pass

    def lire_mot(self, nom):
        """Le contenu du fichier sans les blancs ; None s'il est absent ou illisible. Un fichier vide donne '' : pas encore ecrit."""
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
    def _pouls_frais(self):
        v = self.lire_mot('battement.txt')
        try:
            return self.deps.now() - float(v) <= POULS_MAX_S
        except (TypeError, ValueError):
            return False

    def pouls_ok(self):
        """DCS est tenu pour absent seulement si deux lectures du pouls, a 2 s d'intervalle, disent absent, vide, illisible ou trop ancien."""
        if self.deps.now() - self.t_nuit < POULS_GRACE_S:
            return True
        if self._pouls_frais():
            return True
        self.deps.sleep(POULS_RELECTURE_S)
        return self._pouls_frais()

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
            if reponse:                                  # vide : le crochet est en train de l'ecrire, on attend
                self.supprimer('vue_faite.txt')
                break
            if self.deps.now() - debut >= VUE_ATTENTE_S:
                reponse = None
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
        question = self.work_dir / 'sync' / 'QUESTION.txt'
        try:
            question.unlink()                            # celle d'avant ne compte pas : `question` = ecrite par cette commande
        except OSError:
            pass
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
        elif code == 3 and question.is_file():
            res['question'] = question.read_text(encoding='utf-8', errors='replace').strip()
            try:
                question.unlink()
            except OSError:
                pass
            res['statut'] = 'question'
        else:
            res['statut'] = 'echec'
        res['fin_journal'] = _fin_journal(log)
        self.jr(f'{c["n"]:02d} {res["statut"]} (code {code}, {res["duree_s"]} s)')

    # -- la nuit entiere
    def bilan_base(self, resultats, cause, fin):
        return {'file': self.file, 't_nuit': _iso(self.t_nuit), 't_nuit_epoch': self.t_nuit, 'fin': _iso(fin), 'cause': cause,
                'dossier': self.nuit_dir.name, 'commandes': resultats, 'zips': [], 'fichiers_trop_gros': [], 'fichiers_sautes': [],
                'depot': {'statut': 'a_faire' if self.file['tampon'] else 'sans_depot'}}

    def creer_dossier(self):
        """nuits/<AAAA-MM-JJ_HHMM>/, ou _2, _3... si une autre nuit l'a deja pris dans la meme minute."""
        stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(self.t_nuit))
        (self.dir / 'nuits').mkdir(parents=True, exist_ok=True)
        n = 1
        while True:
            nom = stamp if n == 1 else f'{stamp}_{n}'
            try:
                (self.dir / 'nuits' / nom).mkdir()
            except FileExistsError:
                n += 1
                continue
            self.stamp, self.nuit_dir = nom, self.dir / 'nuits' / nom
            return

    def apres_la_boucle(self, bilan):
        """Rassemblement, zips, depot. Une exception se propage a l'appelant, qui la note au bilan."""
        bilan_path = self.nuit_dir / 'bilan.json'
        self.ecrire_bilan(bilan)
        # le bilan (sans les zips) est range dans les zips, puis reecrit avec leurs empreintes
        fichiers, sautes = rassembler(self.work_dir, self.nuit_dir, self.t_nuit)
        groupes, trop, sautes2 = planifier_zips(fichiers, self.file['taille_zip_max_o'])
        bilan['fichiers_trop_gros'] = trop
        zips, sautes3 = ecrire_zips(groupes, self.nuit_dir, self.stamp)
        bilan['zips'] = zips
        bilan['fichiers_sautes'] = sautes + sautes2 + sautes3
        for f in bilan['fichiers_sautes']:
            self.jr(f'fichier saute : {f["chemin"]} ({f["raison"]})')
        self.ecrire_bilan(bilan)
        for z in zips:
            self.jr(f'zip {z["nom"]} : {z["fichiers"]} fichier(s), {z["taille_o"]} o')
        if self.file['tampon']:
            bilan['depot'] = deposer(self.file['tampon'], bilan, bilan_path, [self.nuit_dir / z['nom'] for z in zips], self.deps)
            self.jr(f'depot : {bilan["depot"]["statut"]}' + (f' ({bilan["depot"]["detail"]})' if bilan['depot'].get('detail') else ''))
            self.ecrire_bilan(bilan)

    def jouer(self):
        d = self.deps
        try:
            self.t_nuit = d.now()
            self.creer_dossier()
            self.journal_path = self.nuit_dir / 'nuit.log'
            cmds = deplier(self.file)
            resultats = [{'n': c['n'], 'outil': c['outil'],
                          'arguments': c['args'] + (['--config', self.file['config']] if self.file['config'] else []),
                          'vue': c['vue'], 'statut': None, 'debut': None, 'duree_s': None, 'code': None, 'fin_journal': []}
                         for c in cmds]
            for nom in ('vue_demande.txt', 'vue_faite.txt'):
                self.supprimer(nom)
        except Exception as e:
            msg = f'demarrage impossible : {type(e).__name__}: {e}'
            print(f'Erreur : {msg}', file=sys.stderr)
            ecrire_echec(self.dir, msg)
            return 2
        code, cause, bilan = 0, 'file_finie', None
        eteindre = self.file['eteindre']
        try:
            try:
                d.veille(True)
            except Exception as e:
                self.jr(f'veille non empechee : {type(e).__name__}: {e}')
            self.jr(f'nuit {self.stamp} : {len(cmds)} commande(s), budget {self.file["budget_captures_s"]} s')
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
            self.jr(f'fin de la file : {cause}')
            bilan = self.bilan_base(resultats, cause, d.now())
            try:
                self.apres_la_boucle(bilan)
            except Exception as e:
                code = 1
                bilan['erreur'] = f'{type(e).__name__}: {e}'
                self.jr(f'erreur apres la file : {bilan["erreur"]}')
                self.ecrire_bilan_sans_echec(bilan)
        except Exception as e:
            code, cause = 1, 'erreur'
            self.jr(f'erreur pendant la file : {type(e).__name__}: {e}')
            for r in resultats:
                r['statut'] = r['statut'] or 'non_joue'
            bilan = bilan or self.bilan_base(resultats, cause, d.now())
            bilan['erreur'] = f'{type(e).__name__}: {e}'
            self.ecrire_bilan_sans_echec(bilan)
        finally:
            try:
                d.veille(False)
            except Exception:
                pass
            try:
                _ecrire(self.dir / 'file.lua', 'return false\n')
                _ecrire(self.dir / 'fin.txt', 'eteindre' if eteindre else 'rester')
            except OSError as e:
                print(f'Erreur : fin de nuit non ecrite ({e})', file=sys.stderr)
            if cause == 'dcs_absent' and eteindre:          # le crochet n'est plus la pour le faire : apres fin.txt, toujours
                try:
                    self.jr('DCS absent : arret du PC')
                    d.shutdown()
                except Exception as e:
                    print(f'Erreur : arret du PC impossible ({type(e).__name__}: {e})', file=sys.stderr)
        return code

    def ecrire_bilan(self, bilan):
        _ecrire(self.nuit_dir / 'bilan.json', json.dumps(bilan, ensure_ascii=False, indent=2) + '\n')

    def ecrire_bilan_sans_echec(self, bilan):
        try:
            self.ecrire_bilan(bilan)
        except Exception as e:
            print(f'Erreur : bilan non ecrit ({type(e).__name__}: {e})', file=sys.stderr)


def _work_dir_connu(chemin):
    """Le work_dir de la configuration de capture, si on peut le lire sans bruit ; sinon None."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from config import load_config
        conf = None
        try:
            data = json.loads(Path(chemin).read_text(encoding='utf-8-sig'))
            if isinstance(data, dict) and isinstance(data.get('config'), str) and data['config'].strip():
                conf = data['config']
        except (OSError, ValueError):
            pass
        with contextlib.redirect_stderr(io.StringIO()):
            cfg, _ = load_config(['nuit.py'] + (['--config', conf] if conf else []))
        return Path(cfg.work_dir)
    except BaseException:                      # SystemExit de load_config compris
        return None


def _echec_jouer(chemin, work_dir, msg):
    """Echec avant la boucle : message, trace dans le dossier de la nuit (ou a cote de la file), code 2."""
    print(f'Erreur : {msg}', file=sys.stderr)
    dossier = (Path(work_dir) / 'autonome') if work_dir else Path(chemin).resolve().parent
    ecrire_echec(dossier, msg)
    return 2


def cmd_jouer(chemin, deps=None, out=None):
    deps = deps or Deps()
    try:
        file = lire_file(chemin)
        deplier(file)
    except FileErreur as e:
        return _echec_jouer(chemin, _work_dir_connu(chemin), f'file refusee : {e}')
    except Exception as e:
        return _echec_jouer(chemin, None, f'file : {type(e).__name__}: {e}')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    cfg, refus = None, None
    try:
        from config import load_config
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                cfg, _ = load_config(['nuit.py'] + (['--config', file['config']] if file['config'] else []))
            except SystemExit:
                refus = 'configuration illisible : ' + (' '.join(err.getvalue().split()) or 'inconnue')
    except Exception as e:
        refus = f'configuration : {type(e).__name__}: {e}'
    if refus:
        return _echec_jouer(chemin, None, refus)
    try:
        return Nuit(file, cfg, deps, out).jouer()
    except Exception as e:
        return _echec_jouer(chemin, cfg.work_dir, f'{type(e).__name__}: {e}')


def main(argv, deps=None):
    if len(argv) != 3 or argv[1] not in ('verifier', 'jouer'):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[1] == 'verifier':
        return cmd_verifier(argv[2])
    return cmd_jouer(argv[2], deps)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
