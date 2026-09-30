"""
goals/email_approval.py

IMAP Email Approval Daemon — Goals Layer Entry Point
======================================================
Background daemon that polls the HR inbox every 15 seconds for manager
replies to leave requests (Subject: "Leave Request LR-*").

When a manager replies "Approved" or "Rejected: <reason>":
  1. Updates MySQL leave_requests to 'Approved' or 'Rejected'.
  2. Restores leave_balances if rejected.
  3. Dispatches official confirmation email to the employee via
     services/notification_service.py.

Usage (called from main.py as a daemon thread):
  from goals.email_approval import start_email_approval_daemon
  thread = start_email_approval_daemon()
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("email_approval")

# ---------------------------------------------------------------------------
# Re-export the core service from services/email_approval_service.py
# This module acts as the goals-layer facade for the approval daemon.
# ---------------------------------------------------------------------------

try:
    from services.email_approval_service import EmailApprovalService
    _service_available = True
except ImportError:
    logger.warning("[EmailApproval] email_approval_service not found — daemon disabled.")
    _service_available = False


def start_email_approval_daemon(poll_interval: int = 15) -> threading.Thread | None:
    """
    Start the IMAP approval polling loop as a background daemon thread.

    Args:
        poll_interval: Seconds between IMAP inbox scans (default: 15).

    Returns:
        threading.Thread instance (already started), or None if service unavailable.
    """
    if not _service_available:
        logger.warning("[EmailApproval] Daemon not started — service unavailable.")
        return None

    service = EmailApprovalService()

    def _run():
        logger.info(f"🚀 [EmailApproval] Daemon thread started (polling every {poll_interval}s).")
        service.run_forever(poll_interval=poll_interval)

    thread = threading.Thread(target=_run, daemon=True, name="EmailApprovalDaemon")
    thread.start()
    return thread
