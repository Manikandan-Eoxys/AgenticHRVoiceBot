"""
config/__init__.py

Re-exports typed Config and module-level constants from config.py
so that both `from config import LIVEKIT_URL` and `import config` work seamlessly
whether Python resolves config as a package directory or module.
"""

import importlib.util
from pathlib import Path

_cfg_path = Path(__file__).resolve().parent.parent / "config.py"
if _cfg_path.exists():
    _spec = importlib.util.spec_from_file_location("_config_module", str(_cfg_path))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _attr in dir(_mod):
        if not _attr.startswith("__"):
            globals()[_attr] = getattr(_mod, _attr)
