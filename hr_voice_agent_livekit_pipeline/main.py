"""
main.py

Entry point for HR Voice Agent
LiveKit Agents v1.6.10
"""

import logging
import threading
import time
from livekit import agents
from agent import server
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


if __name__ == "__main__":
    _start_email_approval_daemon()
    # Strip any lingering StreamHandlers so LiveKit CLI has exclusive console logger control
    for h in list(logging.getLogger().handlers):
        if isinstance(h, logging.StreamHandler):
            logging.getLogger().removeHandler(h)
    agents.cli.run_app(server)
