"""Tests de capture/config.py (bibliotheque standard seulement ; python -m unittest discover -s tests)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'capture'))
import config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.dcs = self.tmp / 'DCS'
        (self.dcs / 'Mods' / 'terrains').mkdir(parents=True)
        self.work = self.tmp / 'travail'
        self.default = self.tmp / 'defaut.json'          # tient lieu de capture/config.json

    def write(self, name, **over):
        data = {'work_dir': str(self.work), 'dcs_install': str(self.dcs)}
        data.update(over)
        for k in [k for k, v in data.items() if v is ...]:
            del data[k]
        p = self.tmp / name
        p.write_text(json.dumps(data), encoding='utf-8')
        return p

    def load(self, argv=('prog',), environ=None):
        return config.load_config(list(argv), environ={} if environ is None else environ,
                                  default_path=self.default)

    def fail(self, *args, **kw):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.load(*args, **kw)
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def test_fichier_par_defaut(self):
        self.write('defaut.json')
        cfg, rest = self.load()
        self.assertEqual(cfg.work_dir, self.work)
        self.assertEqual(cfg.dcs_install, self.dcs)
        self.assertEqual(cfg.terrains, self.dcs / 'Mods' / 'terrains')
        self.assertEqual(rest, ['prog'])

    def test_variable_avant_fichier_par_defaut(self):
        self.write('defaut.json', work_dir=str(self.tmp / 'defaut_wd'))
        env = self.write('env.json', work_dir=str(self.tmp / 'env_wd'))
        cfg, _ = self.load(environ={config.ENV_VAR: str(env)})
        self.assertEqual(cfg.work_dir, self.tmp / 'env_wd')

    def test_option_avant_variable(self):
        env = self.write('env.json', work_dir=str(self.tmp / 'env_wd'))
        opt = self.write('opt.json', work_dir=str(self.tmp / 'opt_wd'))
        cfg, _ = self.load(['prog', '--config', str(opt), 'index', 'Caucasus'],
                           environ={config.ENV_VAR: str(env)})
        self.assertEqual(cfg.work_dir, self.tmp / 'opt_wd')
        self.assertEqual(cfg.source, opt)

    def test_option_retiree_de_argv(self):
        opt = self.write('opt.json')
        _, rest = self.load(['prog', 'capture', '--config', str(opt), 'Caucasus', 'Batumi'])
        self.assertEqual(rest, ['prog', 'capture', 'Caucasus', 'Batumi'])
        _, rest = self.load(['prog', f'--config={opt}', 'index', 'Caucasus'])
        self.assertEqual(rest, ['prog', 'index', 'Caucasus'])

    def test_option_sans_chemin(self):
        self.assertIn('--config', self.fail(['prog', 'index', '--config']))

    def test_fichier_absent(self):
        msg = self.fail()
        self.assertIn(str(self.default), msg)
        self.assertIn('config.example.json', msg)
        absent = self.tmp / 'absent.json'
        self.assertIn(str(absent), self.fail(['prog', '--config', str(absent)]))

    def test_json_invalide(self):
        self.default.write_text('{ pas du json', encoding='utf-8')
        msg = self.fail()
        self.assertIn(str(self.default), msg)
        self.assertIn('JSON', msg)
        self.default.write_text('[1, 2]', encoding='utf-8')
        self.fail()

    def test_cle_manquante(self):
        for key in ('work_dir', 'dcs_install'):
            with self.subTest(key=key):
                self.write('defaut.json', **{key: ...})
                self.assertIn(key, self.fail())

    def test_terrains_absent(self):
        self.write('defaut.json', dcs_install=str(self.tmp / 'ailleurs'))
        msg = self.fail()
        self.assertIn('terrains', msg)
        self.assertIn('config.example.json', msg)

    def test_work_dir_cree(self):
        self.write('defaut.json', work_dir=str(self.work / 'a' / 'b'))
        self.assertFalse(self.work.exists())
        cfg, _ = self.load()
        self.assertTrue(cfg.work_dir.is_dir())

    def test_screen_facultatif(self):
        self.write('defaut.json')
        self.assertIsNone(self.load()[0].screen)
        self.write('defaut.json', screen=[1920, 1080])
        self.assertEqual(self.load()[0].screen, (1920, 1080))

    def test_screen_invalide(self):
        for bad in ([1920], 'grand', [1920, -1], [1920.5, 1080]):
            with self.subTest(screen=bad):
                self.write('defaut.json', screen=bad)
                self.assertIn('screen', self.fail())

    def test_tilde_developpe(self):
        self.write('defaut.json', work_dir='~/dcs_charts_test_inexistant', dcs_install=str(self.dcs))
        home = self.tmp / 'home'
        home.mkdir()
        with unittest.mock.patch.dict('os.environ', {'HOME': str(home), 'USERPROFILE': str(home)}):
            cfg, _ = self.load()
        self.assertEqual(cfg.work_dir, home / 'dcs_charts_test_inexistant')
        self.assertNotIn('~', str(cfg.work_dir))


if __name__ == '__main__':
    unittest.main()
