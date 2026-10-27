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

    # Determine backend: CLI argument has highest priority, then environment variable
    backend = os.environ.get("TELEPHONY_BACKEND", "").strip().lower()
    is_asterisk = "asterisk" in sys.argv or backend == "asterisk"

    if is_asterisk:
        logger.info("☎️ [Mode: ASTERISK] Starting Asterisk ARI + AudioSocket Telephony Daemon...")
        from asterisk.server import run_asterisk_server
        run_asterisk_server()
    else:
        logger.info("🎙️ [Mode: LIVEKIT] Starting LiveKit Worker Agent...")
        from livekit import agents
        from agent import server

        # Strip lingering StreamHandlers so LiveKit CLI has exclusive console logger control
        for h in list(logging.getLogger().handlers):
            if isinstance(h, logging.StreamHandler):
                logging.getLogger().removeHandler(h)
        agents.cli.run_app(server)


if __name__ == "__main__":
    main()
