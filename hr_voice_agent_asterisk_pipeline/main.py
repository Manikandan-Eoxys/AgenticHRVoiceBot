"""
main.py

Entry point for HR Voice Agent (Asterisk Native ARI + AudioSocket).
===================================================================
Usage:
  python main.py
"""

import sys
import os
from pathlib import Path

# Auto-reexec with virtual environment Python if invoked with system Python
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
_venv_python = _project_root / ".venv" / "bin" / "python"
if _venv_python.exists() and sys.executable != str(_venv_python) and sys.prefix == getattr(sys, "base_prefix", sys.prefix):
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

for _p in (_current_dir, _project_root):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

import logging

logger = logging.getLogger("main")


def main():
    logger.info("☎️ [Mode: ASTERISK] Starting Asterisk ARI + AudioSocket Telephony Daemon...")
    from agent import run
    run()


if __name__ == "__main__":
    main()
