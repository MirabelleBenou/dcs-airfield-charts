"""Import de capture/f10capture.py (module Windows : ctypes.windll en tete de fichier) sur toute plateforme.

Sous Windows, import normal. Ailleurs, un `ctypes.windll` factice est pose le temps de l'import : seules les fonctions
pures (rn4_points, rn4_extent, corrected_coverage...) sont utilisables, pas la capture.
"""
import ctypes
import importlib.util
import sys
from pathlib import Path
from unittest import mock

PATH = Path(__file__).resolve().parent.parent / 'capture' / 'f10capture.py'
_cache = None


def load():
    global _cache
    if _cache is not None:
        return _cache
    spec = importlib.util.spec_from_file_location('f10capture', PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PATH.parent))
    fake = not hasattr(ctypes, 'windll')
    try:
        if fake:
            ctypes.windll = mock.MagicMock()
        sys.modules['f10capture'] = mod
        spec.loader.exec_module(mod)
    finally:
        if fake:
            del ctypes.windll
        sys.path.remove(str(PATH.parent))
    _cache = mod
    return mod
