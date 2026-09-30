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
from services.email_approval_service import email_approval_service

logger = logging.getLogger("main")

# Attach monitoring handler so all logs stream to frontend UI
try:
    install_monitoring_handler()
except Exception as e:
    logging.warning(f"Could not initialize monitoring handler: {e}")


def _start_email_approval_daemon():
    """Starts background thread to continuously poll for manager leave approvals via email."""
    def _poll_worker():
        logger.info("🚀 [EmailApproval] Daemon thread started (polling every 15s).")
        time.sleep(3)
        while True:
            try:
                email_approval_service.check_for_approvals()
            except Exception as err:
                logger.debug(f"[EmailApproval] Polling error: {err}")
            time.sleep(15)

    t = threading.Thread(target=_poll_worker, daemon=True, name="email-approval-daemon")
    t.start()


if __name__ == "__main__":
    _start_email_approval_daemon()
    agents.cli.run_app(server)
