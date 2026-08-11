"""
services/notification_service.py

Notification Service
Handles outbound notifications for:
  1. Area Manager — new exception request requires their approval
  2. Employee — decision made on their absence request
  3. Field Manager — coverage warning alert

Currently implemented as structured stubs.
Wire to real SMTP / MS Teams webhook / SMS by setting environment variables.
"""

import logging
import sqlite3
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


class NotificationService:

    def __init__(self):
        self.db        = Config.DATABASE_PATH
        self.smtp_host = getattr(Config, "SMTP_HOST",          None)
        self.smtp_port = getattr(Config, "SMTP_PORT",          587)
        self.smtp_user = getattr(Config, "SMTP_USER",          None)
        self.smtp_pass = getattr(Config, "SMTP_PASS",          None)
        self.teams_url = getattr(Config, "TEAMS_WEBHOOK_URL",  None)

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_employee(self, employee_id: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM employees WHERE employee_id=?", (employee_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else {}

    # ----------------------------------------------------------
    # 1. Notify Area Manager — Exception Requires Approval
    # ----------------------------------------------------------
    def notify_area_manager(
        self,
        area_manager_id: int,
        exception_id: int,
        employee_id: int,
        exception_type: str,
        from_date: str,
        to_date: str,
        reason: str = "",
    ) -> dict:
        """
        Notifies the Area Manager that a new exception request needs their decision.
        """
        am   = self._get_employee(area_manager_id)
        emp  = self._get_employee(employee_id)

        subject = f"[Action Required] Absence Exception: {emp.get('name', 'Employee')} — {from_date}"
        body = (
            f"Dear {am.get('name', 'Area Manager')},\n\n"
            f"An absence request requires your approval.\n\n"
            f"  Employee   : {emp.get('name')} (ID: {employee_id})\n"
            f"  Dates      : {from_date} to {to_date}\n"
            f"  Type       : {exception_type}\n"
            f"  Reason     : {reason or 'N/A'}\n"
            f"  Exception ID: {exception_id}\n\n"
            f"Please log in to approve or reject this request.\n\n"
            f"-- HR Voice Bot"
        )

        sent = self._send_notification(
            to_email    = am.get("email"),
            to_name     = am.get("name"),
            subject     = subject,
            body        = body,
            channel     = "email",
        )

        logger.info(
            "[NotificationService] AM notification sent: exception_id=%s → %s (%s)",
            exception_id, am.get("name"), am.get("email"),
        )

        return {
            "success":          sent,
            "notification_type":"area_manager_approval",
            "area_manager_id":  area_manager_id,
            "exception_id":     exception_id,
            "channel":          "email",
            "message":          f"Area Manager {am.get('name')} notified of exception {exception_id}.",
        }

    # ----------------------------------------------------------
    # 2. Notify Employee — Decision on Their Request
    # ----------------------------------------------------------
    def notify_employee(
        self,
        employee_id: int,
        decision: str,          # 'Approved' | 'Rejected'
        request_id: int,
        from_date: str,
        to_date: str,
        am_notes: str = "",
    ) -> dict:
        """
        Notifies the employee of the Area Manager's decision on their exception.
        """
        emp = self._get_employee(employee_id)

        subject = f"[HR] Your Absence Request {decision} — {from_date}"
        if decision == "Approved":
            body = (
                f"Dear {emp.get('name', 'Employee')},\n\n"
                f"Your absence request ({from_date} to {to_date}) has been APPROVED.\n"
                + (f"\nArea Manager Notes: {am_notes}\n" if am_notes else "")
                + f"\n-- HR Voice Bot"
            )
        else:
            body = (
                f"Dear {emp.get('name', 'Employee')},\n\n"
                f"Your absence request ({from_date} to {to_date}) has been REJECTED.\n"
                + (f"\nReason: {am_notes}\n" if am_notes else "")
                + f"\nPlease speak to your Field Manager for further guidance.\n\n-- HR Voice Bot"
            )

        sent = self._send_notification(
            to_email = emp.get("email"),
            to_name  = emp.get("name"),
            subject  = subject,
            body     = body,
            channel  = "email",
        )

        logger.info(
            "[NotificationService] Employee notification sent: employee_id=%s decision=%s",
            employee_id, decision,
        )

        return {
            "success":          sent,
            "notification_type":"employee_decision",
            "employee_id":      employee_id,
            "decision":         decision,
            "request_id":       request_id,
            "channel":          "email",
            "message":          f"Employee {emp.get('name')} notified: request {decision.lower()}.",
        }

    # ----------------------------------------------------------
    # 3. Notify Field Manager — Coverage Warning
    # ----------------------------------------------------------
    def notify_field_manager_coverage_alert(
        self,
        field_manager_id: int,
        coverage_pct: float,
        coverage_date: str,
    ) -> dict:
        """
        Alerts the Field Manager that team coverage is approaching or below threshold.
        """
        fm = self._get_employee(field_manager_id)
        threshold_pct = round(float(getattr(Config, "COVERAGE_THRESHOLD", 0.80)) * 100)
        current_pct   = round(coverage_pct * 100, 1)

        subject = f"[Alert] Team Coverage {current_pct}% — {coverage_date}"
        body = (
            f"Dear {fm.get('name', 'Field Manager')},\n\n"
            f"Your team's attendance coverage on {coverage_date} is {current_pct}%, "
            f"which is {'BELOW' if coverage_pct < float(getattr(Config, 'COVERAGE_THRESHOLD', 0.80)) else 'near'} "
            f"the {threshold_pct}% minimum threshold.\n\n"
            f"Please review the roster and ensure adequate coverage.\n\n"
            f"-- HR Voice Bot"
        )

        sent = self._send_notification(
            to_email = fm.get("email"),
            to_name  = fm.get("name"),
            subject  = subject,
            body     = body,
            channel  = "email",
        )

        logger.info(
            "[NotificationService] FM coverage alert: fm_id=%s coverage=%.1f%% date=%s",
            field_manager_id, current_pct, coverage_date,
        )

        return {
            "success":          sent,
            "notification_type":"coverage_alert",
            "field_manager_id": field_manager_id,
            "coverage_pct":     coverage_pct,
            "coverage_date":    coverage_date,
            "channel":          "email",
        }

    # ----------------------------------------------------------
    # Internal: Send Notification (stub → wire to real provider)
    # ----------------------------------------------------------
    def _send_notification(
        self,
        to_email: str,
        to_name:  str,
        subject:  str,
        body:     str,
        channel:  str = "email",
    ) -> bool:
        """
        Stub dispatcher.
        - If SMTP credentials are configured: send real email via aiosmtplib/smtplib.
        - If Teams webhook is configured: post to MS Teams channel.
        - Otherwise: log to console (demo mode).
        """
        if self.smtp_host and self.smtp_user and self.smtp_pass:
            return self._send_email(to_email, subject, body)

        if self.teams_url:
            return self._send_teams(to_name, subject, body)

        # Demo / local mode: log to console
        logger.info(
            "[NOTIFICATION STUB] To: %s <%s>\nSubject: %s\n%s",
            to_name, to_email, subject, body,
        )
        return True  # Simulate success in demo mode

    def _send_email(self, to_email: str, subject: str, body: str) -> bool:
        """Real SMTP email sender. Uses smtplib (sync). Wire aiosmtplib for async FastAPI."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg          = MIMEText(body)
            msg["Subject"] = subject
            msg["From"]    = self.smtp_user
            msg["To"]      = to_email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.smtp_user, [to_email], msg.as_string())
            return True
        except Exception as exc:
            logger.error("[NotificationService] Email send failed: %s", exc)
            return False

    def _send_teams(self, to_name: str, subject: str, body: str) -> bool:
        """MS Teams webhook notification."""
        try:
            import requests
            payload = {
                "@type":    "MessageCard",
                "@context": "http://schema.org/extensions",
                "summary":  subject,
                "sections": [{"activityTitle": subject, "text": body.replace("\n", "<br>")}],
            }
            r = requests.post(self.teams_url, json=payload, timeout=10)
            return r.status_code in (200, 202)
        except Exception as exc:
            logger.error("[NotificationService] Teams notification failed: %s", exc)
            return False
