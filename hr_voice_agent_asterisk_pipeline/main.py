"""
main.py

Entry point for HR Voice Agent
Supports both Asterisk Telephony (Native ARI + AudioSocket) and LiveKit WebRTC backend.
=======================================================================================
Usage:
  # Run Asterisk Telephony Daemon:
  python main.py asterisk
  # OR set TELEPHONY_BACKEND=asterisk in .env:
  python main.py

  # Run LiveKit WebRTC Worker (development / backwards-compatibility):
  python main.py dev
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
from services.monitoring_handler import install_monitoring_handler
from goals.email_approval import start_email_approval_daemon

logger = logging.getLogger("main")

# Attach monitoring handler so all logs stream to frontend UI
try:
    install_monitoring_handler()
except Exception as e:
    logging.warning(f"Could not initialize monitoring handler: {e}")


def _start_email_approval_daemon():
    """Starts background thread to continuously poll for manager leave approvals via email."""
    return start_email_approval_daemon(poll_interval=15)


def main():
    _start_email_approval_daemon()
    logger.info("☎️ [Mode: ASTERISK] Starting Asterisk ARI + AudioSocket Telephony Daemon...")
    from asterisk_main import run
    run()


if __name__ == "__main__":
    main()
