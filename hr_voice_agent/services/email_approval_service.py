"""
services/email_approval_service.py

Automated Inbound Email Approval Listener (IMAP)
Monitors the bot inbox for manager email replies to leave requests.
When a manager replies "Approved" or "Rejected: <reason>":
1. Updates MySQL leave_requests table to 'approved' or 'rejected'.
2. Restores leave balances if rejected.
3. Automatically dispatches an official confirmation email to the employee.
"""

import os
import re
import time
import email
import logging
import smtplib
import imaplib
import asyncio
from email.header import decode_header
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

from database.db import get_db_connection

logger = logging.getLogger("email_approval")

# Configuration from environment
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
HR_EMAIL = os.environ.get("HR_EMAIL", "manikandan.eoxys@gmail.com").strip()


class EmailApprovalService:
    def __init__(self):
        self.imap_host = "imap.gmail.com"
        self.imap_port = 993
        self.user = SMTP_USER
        self.password = SMTP_PASS
        self._running = False

    def _connect_imap(self) -> imaplib.IMAP4_SSL | None:
        if not self.user or not self.password:
            logger.warning("[EmailApproval] SMTP_USER or SMTP_PASS not set. Inbound approval disabled.")
            return None
        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            mail.login(self.user, self.password)
            mail.select("INBOX")
            return mail
        except Exception as e:
            logger.warning(f"[EmailApproval] IMAP connection failed: {e}")
            return None

    def _send_employee_notification(self, to_email: str, subject: str, body: str) -> bool:
        """Sends confirmation email to employee. Returns True on success, False if failed."""
        if not to_email or "@" not in to_email:
            logger.warning(f"[EmailApproval] Invalid employee email '{to_email}' — skipping employee notification.")
            return False

        if not self.user or not self.password:
            return False

        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self.user
            msg["To"] = to_email

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, [to_email], msg.as_string())
            logger.info(f"📧 [EmailApproval] Sent status notification email to employee: {to_email}")
            return True
        except Exception as e:
            logger.warning(f"[EmailApproval] Could not send email to employee ({to_email}): {e}")
            return False

    def check_for_approvals(self) -> list[dict]:
        """Polls inbox, detects manager approval/rejection replies, and updates MySQL."""
        mail = self._connect_imap()
        if not mail:
            return []

        processed = []
        try:
            # Search for unread leave request emails specifically
            msg_ids = []
            status, req_msgs = mail.search(None, '(UNSEEN SUBJECT "REQ-")')
            if status == "OK" and req_msgs[0]:
                msg_ids.extend(req_msgs[0].split())

            status, leave_msgs = mail.search(None, '(UNSEEN SUBJECT "Leave")')
            if status == "OK" and leave_msgs[0]:
                for mid in leave_msgs[0].split():
                    if mid not in msg_ids:
                        msg_ids.append(mid)

            if not msg_ids:
                return []

            # Process up to 10 most recent messages
            msg_ids = msg_ids[-10:]
            logger.info(f"📬 [EmailApproval] Found {len(msg_ids)} targeted message(s) to inspect.")

            for mid in msg_ids:
                try:
                    res, data = mail.fetch(mid, "(RFC822)")
                    if res != "OK" or not data:
                        continue

                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # Extract Subject
                    subject_header = decode_header(msg.get("Subject", ""))[0]
                    subject_text = subject_header[0]
                    if isinstance(subject_text, bytes):
                        subject_text = subject_text.decode(subject_header[1] or "utf-8", errors="ignore")

                    # Extract sender
                    from_header = msg.get("From", "")

                    # Extract body plain text
                    body_text = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            if content_type == "text/plain":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body_text += payload.decode("utf-8", errors="ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body_text = payload.decode("utf-8", errors="ignore")

                    # Look for [REQ-xxx] in Subject or Body
                    req_match = re.search(r"REQ[-_](\d+)", subject_text, re.IGNORECASE)
                    if not req_match:
                        req_match = re.search(r"(?:request\s*id|req)[:\s#]*(\d+)", body_text, re.IGNORECASE)

                    if not req_match:
                        continue

                    req_id = int(req_match.group(1))
                    logger.info(f"🔍 [EmailApproval] Found Request ID #{req_id} in email: '{subject_text}' from '{from_header}'")

                    # Analyze decision from body
                    first_lines = "\n".join(body_text.strip().splitlines()[:5]).lower()

                    is_approved = False
                    is_rejected = False
                    reason = ""

                    # Check approval keywords
                    if any(w in first_lines for w in ["approved", "approve", "granted", "yes, approve", "accepted"]):
                        if "not approved" not in first_lines and "unapproved" not in first_lines:
                            is_approved = True

                    # Check rejection keywords
                    if any(w in first_lines for w in ["rejected", "reject", "denied", "cannot approve", "not approved", "no"]):
                        is_rejected = True
                        # Extract rejection reason
                        r_match = re.search(r"(?:reject(?:ed)?|denied|reason)[\s:]*([^\n\r]+)", body_text, re.IGNORECASE)
                        if r_match:
                            reason = r_match.group(1).strip()
                        else:
                            reason = "Rejected by manager via email."

                    if not is_approved and not is_rejected:
                        logger.debug(f"Email for #{req_id} did not contain clear approve/reject decision.")
                        continue

                    # Execute DB Update
                    conn = get_db_connection()
                    cur = conn.cursor(dictionary=True)

                    # Get request details
                    cur.execute(
                        """SELECT r.request_id, r.employee_id, r.leave_code, r.start_date, r.end_date, 
                                  r.days_requested, r.status, e.full_name, e.email, lp.leave_name
                           FROM leave_requests r
                           JOIN employees e ON r.employee_id = e.employee_id
                           LEFT JOIN leave_policy lp ON r.leave_code = lp.leave_code
                           WHERE r.request_id = %s""",
                        (req_id,)
                    )
                    req_row = cur.fetchone()

                    if not req_row:
                        logger.warning(f"Request #{req_id} not found in database.")
                        cur.close()
                        conn.close()
                        continue

                    if req_row["status"] != "submitted":
                        logger.info(f"Request #{req_id} is already '{req_row['status']}' — skipping.")
                        # Mark email as read
                        mail.store(mid, "+FLAGS", "\\Seen")
                        cur.close()
                        conn.close()
                        continue

                    emp_name = req_row["full_name"]
                    emp_email = req_row["email"]
                    leave_name = req_row["leave_name"] or req_row["leave_code"]
                    s_date = str(req_row["start_date"])
                    e_date = str(req_row["end_date"])

                    if is_approved:
                        cur.execute(
                            "UPDATE leave_requests SET status = 'approved', manager_remarks = 'Approved by manager via email' WHERE request_id = %s",
                            (req_id,)
                        )
                        conn.commit()
                        logger.info(f"🎉 [EmailApproval] Request #{req_id} for {emp_name} updated to APPROVED in MySQL.")

                        # Notify employee
                        notify_subj = f"[Approved] Your Leave Request ({leave_name}) Has Been Approved"
                        notify_body = (
                            f"Hi {emp_name},\n\n"
                            f"Great news! Your manager has approved your leave request for {leave_name} "
                            f"from {s_date} to {e_date} ({req_row['days_requested']} day(s)).\n\n"
                            f"Status: Approved\n"
                            f"Request ID: REQ-{req_id}\n\n"
                            f"Best regards,\n"
                            f"HR Operations Team"
                        )
                        self._send_employee_notification(emp_email, notify_subj, notify_body)

                        processed.append({
                            "request_id": req_id,
                            "employee_id": req_row["employee_id"],
                            "status": "approved",
                        })

                    elif is_rejected:
                        cur.execute(
                            "UPDATE leave_requests SET status = 'rejected', rejection_reason = %s WHERE request_id = %s",
                            (reason or "Manager rejected via email", req_id)
                        )
                        # Restore leave balance
                        cur.execute(
                            """UPDATE leave_balances 
                               SET used_days = GREATEST(0, used_days - %s) 
                               WHERE employee_id = %s AND leave_code = %s""",
                            (req_row["days_requested"], req_row["employee_id"], req_row["leave_code"])
                        )
                        conn.commit()
                        logger.info(f"❌ [EmailApproval] Request #{req_id} for {emp_name} updated to REJECTED in MySQL.")

                        # Notify employee
                        notify_subj = f"[Update] Your Leave Request ({leave_name}) Status"
                        notify_body = (
                            f"Hi {emp_name},\n\n"
                            f"Your leave request for {leave_name} from {s_date} to {e_date} was not approved by your manager.\n\n"
                            f"Manager Reason: {reason}\n"
                            f"Your leave balance has been restored.\n\n"
                            f"Best regards,\n"
                            f"HR Operations Team"
                        )
                        self._send_employee_notification(emp_email, notify_subj, notify_body)

                        processed.append({
                            "request_id": req_id,
                            "employee_id": req_row["employee_id"],
                            "status": "rejected",
                        })

                    cur.close()
                    conn.close()

                    # Mark email as read in Gmail
                    mail.store(mid, "+FLAGS", "\\Seen")

                except Exception as inner_err:
                    logger.warning(f"[EmailApproval] Error processing message {mid}: {inner_err}")

        except Exception as err:
            logger.warning(f"[EmailApproval] Polling error: {err}")
        finally:
            try:
                mail.logout()
            except Exception:
                pass

        return processed

    async def run_loop(self, interval_seconds: int = 15):
        """Asynchronous background loop polling for email approvals."""
        self._running = True
        logger.info(f"🚀 [EmailApproval] Background listener started (polling every {interval_seconds}s).")
        while self._running:
            try:
                await asyncio.to_thread(self.check_for_approvals)
            except Exception as e:
                logger.warning(f"[EmailApproval] Background check error: {e}")
            await asyncio.sleep(interval_seconds)

    def stop(self):
        self._running = False


# Singleton instance
email_approval_service = EmailApprovalService()
