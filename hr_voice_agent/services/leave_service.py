"""
services/leave_service.py

Business logic for Leave Management:
1. Check leave balances (single or all types)
2. Check leave availability (step 1 of application)
3. Confirm leave request (step 2 — records in leave_requests, updates balance, sends notification email)
4. Cancel leave request
5. Get status of leave requests (Approved, Rejected with manager reason, or Pending)
6. Send manager reminder for pending leave requests
"""

import logging
import os
import re
from datetime import date, datetime
from email.mime.text import MIMEText
import smtplib
from database.db import get_db_connection

logger = logging.getLogger("leave-service")

LEAVE_ALIASES = {
    "cl": "CL", "casual": "CL", "casual leave": "CL",
    "sl": "SL", "sick": "SL", "sick leave": "SL", "medical": "SL", "medical leave": "SL",
    "maternity": "MATERNITY", "maternity leave": "MATERNITY",
    "paternity": "PATERNITY", "paternity leave": "PATERNITY",
    "comp off": "COMP_OFF", "compensatory off": "COMP_OFF", "comp": "COMP_OFF", "earned": "COMP_OFF",
    "bereavement": "BEREAVEMENT", "bereavement leave": "BEREAVEMENT",
    "short leave": "SHORT_LEAVE", "half day": "SHORT_LEAVE", "half-day": "SHORT_LEAVE",
    "hourly": "SHORT_LEAVE", "hourly leave": "SHORT_LEAVE",
    "lwp": "LWP", "lop": "LWP", "loss of pay": "LWP", "leave without pay": "LWP",
}

VALID_LEAVE_CODES = {"CL", "SL", "MATERNITY", "PATERNITY", "COMP_OFF", "BEREAVEMENT", "SHORT_LEAVE", "LWP"}


def resolve_leave_code(leave_type: str) -> str | None:
    if not leave_type:
        return None
    key = leave_type.strip().lower()
    if key in LEAVE_ALIASES:
        return LEAVE_ALIASES[key]
    key_upper = leave_type.strip().upper().replace(" ", "_")
    return key_upper if key_upper in VALID_LEAVE_CODES else None


class LeaveService:

    def __init__(self):
        self.hr_email = os.environ.get("HR_NOTIFY_EMAIL") or os.environ.get("HR_EMAIL") or "manikandan.eoxys@gmail.com"
        self.md_email = os.environ.get("MD_NOTIFY_EMAIL", "").strip()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return rows
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
            result = cursor.lastrowid if cursor.lastrowid else cursor.rowcount
            cursor.close()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _send_notification_email(self, subject: str, body: str, to_email: str = None) -> bool:
        to_addr = to_email or self.hr_email
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS", "")
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))

        if not smtp_user or not smtp_pass:
            logger.info("SMTP credentials not configured; skipping actual email delivery.")
            return False

        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = to_addr
            recipients = [to_addr]
            if self.md_email:
                msg["Cc"] = self.md_email
                recipients.append(self.md_email)

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, recipients, msg.as_string())
            return True
        except Exception as e:
            logger.warning(f"Email sending failed: {e}")
            return False

    # -------------------------------------------------------------------------
    # 1. Leave Balances
    # -------------------------------------------------------------------------
    def get_leave_balance(self, employee_id: str, leave_type: str) -> dict:
        code = resolve_leave_code(leave_type)
        if not code:
            return {"success": False, "message": f"Unknown leave type '{leave_type}'. Valid types: Casual Leave, Sick Leave, Maternity, Paternity, Comp Off, Bereavement, Short Leave, LWP."}

        rows = self._query(
            """SELECT p.leave_name, b.entitled_days, b.used_days, b.available_days
               FROM leave_balances b
               JOIN leave_policy p ON b.leave_code = p.leave_code
               WHERE b.employee_id = %s AND b.leave_code = %s""",
            (str(employee_id), code)
        )
        if not rows:
            return {"success": False, "message": f"Employee {employee_id} is not eligible for or does not have a balance record for {leave_type}."}

        r = rows[0]
        avail = "Unlimited" if r["available_days"] is None else r["available_days"]
        entitled = "Unlimited" if r["entitled_days"] is None else r["entitled_days"]
        return {
            "success": True,
            "leave_code": code,
            "leave_name": r["leave_name"],
            "entitled_days": entitled,
            "used_days": r["used_days"],
            "available_days": avail,
            "message": f"{r['leave_name']}: {avail} days available ({r['used_days']} days used out of {entitled} entitled)."
        }

    def get_all_leave_balances(self, employee_id: str) -> dict:
        rows = self._query(
            """SELECT p.leave_name, b.leave_code, b.entitled_days, b.used_days, b.available_days
               FROM leave_balances b
               JOIN leave_policy p ON b.leave_code = p.leave_code
               WHERE b.employee_id = %s
               ORDER BY FIELD(b.leave_code, 'CL','SL','MATERNITY','PATERNITY','COMP_OFF','BEREAVEMENT','SHORT_LEAVE','LWP')""",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "message": f"No leave balances found for employee {employee_id}."}

        summary = []
        for r in rows:
            avail = "Unlimited" if r["available_days"] is None else r["available_days"]
            summary.append(f"{r['leave_name']}: {avail} available")

        return {
            "success": True,
            "balances": rows,
            "summary_text": "; ".join(summary),
            "message": f"Leave balances for employee {employee_id}: {'; '.join(summary)}."
        }

    # -------------------------------------------------------------------------
    # 2. Check Availability (Step 1)
    # -------------------------------------------------------------------------
    def check_leave_availability(self, employee_id: str, leave_type: str, start_date: str, end_date: str) -> dict:
        code = resolve_leave_code(leave_type)
        if not code:
            return {"success": False, "message": f"Unknown leave type '{leave_type}'."}

        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "message": "Dates must be in YYYY-MM-DD format."}

        if ed < sd:
            return {"success": False, "message": f"End date {end_date} cannot be before start date {start_date}."}

        days_requested = (ed - sd).days + 1

        bal_info = self.get_leave_balance(employee_id, code)
        if not bal_info["success"]:
            return bal_info

        available = bal_info["available_days"]
        if available != "Unlimited" and available < days_requested:
            return {
                "success": False,
                "can_apply": False,
                "leave_name": bal_info["leave_name"],
                "days_requested": days_requested,
                "available_days": available,
                "message": f"Insufficient {bal_info['leave_name']} balance. You requested {days_requested} day(s), but only have {available} day(s) available."
            }

        remaining_after = available if available == "Unlimited" else available - days_requested
        return {
            "success": True,
            "can_apply": True,
            "leave_name": bal_info["leave_name"],
            "leave_code": code,
            "start_date": str(sd),
            "end_date": str(ed),
            "days_requested": days_requested,
            "available_days": available,
            "remaining_after": remaining_after,
            "message": f"You have {available} days of {bal_info['leave_name']} available. You are requesting {days_requested} day(s) from {sd} to {ed}. After this leave, you will have {remaining_after} day(s) remaining."
        }

    # -------------------------------------------------------------------------
    # 3. Confirm Leave Request (Step 2)
    # -------------------------------------------------------------------------
    def confirm_leave_request(self, employee_id: str, leave_type: str, start_date: str, end_date: str, reason: str = "") -> dict:
        check = self.check_leave_availability(employee_id, leave_type, start_date, end_date)
        if not check["success"] or not check.get("can_apply", False):
            return check

        code = check["leave_code"]
        days = check["days_requested"]

        # 1. Insert into leave_requests with status 'submitted'
        req_id = self._execute(
            """INSERT INTO leave_requests (employee_id, leave_code, start_date, end_date, days_requested, reason, status, notified_email)
               VALUES (%s, %s, %s, %s, %s, %s, 'submitted', %s)""",
            (str(employee_id), code, start_date, end_date, days, reason or "VoiceBot leave request", self.hr_email)
        )

        # 2. Increment used_days in leave_balances
        self._execute(
            """UPDATE leave_balances 
               SET used_days = used_days + %s 
               WHERE employee_id = %s AND leave_code = %s""",
            (days, str(employee_id), code)
        )

        # 3. Notify HR via email
        emp_rows = self._query("SELECT full_name, email FROM employees WHERE employee_id = %s", (str(employee_id),))
        emp_name = emp_rows[0]["full_name"] if emp_rows else f"Employee {employee_id}"

        body = (
            f"New Leave Request Submitted via HR Voicebot\n\n"
            f"Employee: {emp_name} (ID: {employee_id})\n"
            f"Leave Type: {check['leave_name']}\n"
            f"Period: {start_date} to {end_date} ({days} day(s))\n"
            f"Reason: {reason or 'Not specified'}\n"
            f"Status: Submitted (Pending Manager Approval)\n"
            f"Request ID: REQ-{req_id}\n\n"
            f"──────────────────────────────────────────────────────────\n"
            f"HOW TO RESPOND TO THIS LEAVE REQUEST:\n"
            f"Simply reply directly to this email with:\n"
            f"  • 'Approved' to approve this request.\n"
            f"  • 'Rejected: <reason>' to reject with a reason.\n"
            f"Your response will automatically update the HR system.\n"
            f"──────────────────────────────────────────────────────────\n"
        )
        self._send_notification_email(f"[REQ-{req_id}] Leave Request: {emp_name} - {check['leave_name']} ({days} days)", body)

        return {
            "success": True,
            "request_id": req_id,
            "leave_name": check["leave_name"],
            "days": days,
            "status": "Submitted",
            "message": f"Your leave request for {days} day(s) of {check['leave_name']} from {start_date} to {end_date} has been submitted successfully. Your manager's approval is pending."
        }

    # -------------------------------------------------------------------------
    # 4. Cancel Leave Request
    # -------------------------------------------------------------------------
    def cancel_leave_request(self, employee_id: str, request_id: int = None) -> dict:
        if request_id:
            rows = self._query(
                "SELECT request_id, leave_code, days_requested, start_date, end_date, status FROM leave_requests WHERE request_id = %s AND employee_id = %s",
                (request_id, str(employee_id))
            )
        else:
            rows = self._query(
                "SELECT request_id, leave_code, days_requested, start_date, end_date, status FROM leave_requests WHERE employee_id = %s AND status IN ('submitted', 'approved') ORDER BY request_id DESC LIMIT 1",
                (str(employee_id),)
            )

        if not rows:
            return {"success": False, "message": "No active or pending leave request found to cancel."}

        req = rows[0]
        if req["status"] == "cancelled":
            return {"success": False, "message": f"Leave request #{req['request_id']} has already been cancelled."}

        # Update status to cancelled
        self._execute("UPDATE leave_requests SET status = 'cancelled' WHERE request_id = %s", (req["request_id"],))

        # Restore used_days in leave_balances
        self._execute(
            "UPDATE leave_balances SET used_days = GREATEST(0, used_days - %s) WHERE employee_id = %s AND leave_code = %s",
            (req["days_requested"], str(employee_id), req["leave_code"])
        )

        return {
            "success": True,
            "request_id": req["request_id"],
            "message": f"Your leave request for {req['days_requested']} day(s) from {req['start_date']} to {req['end_date']} has been cancelled, and your leave balance has been restored."
        }

    # -------------------------------------------------------------------------
    # 5. Status of Leave Requests (Approvals / Rejections / Pending)
    # -------------------------------------------------------------------------
    def get_leave_request_status(self, employee_id: str) -> dict:
        rows = self._query(
            """SELECT r.request_id, p.leave_name, r.start_date, r.end_date, r.days_requested, 
                      r.status, r.rejection_reason, r.manager_remarks
               FROM leave_requests r
               JOIN leave_policy p ON r.leave_code = p.leave_code
               WHERE r.employee_id = %s
               ORDER BY r.request_id DESC LIMIT 1""",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "message": "You have no leave requests on file."}

        req = rows[0]
        status = req["status"]
        sd = req["start_date"]
        ed = req["end_date"]
        leave_name = req["leave_name"]

        if status == "approved":
            msg = f"Your leave request for {leave_name} from {sd} to {ed} has been approved by your manager."
        elif status == "rejected":
            reason = req["rejection_reason"] or req["manager_remarks"] or "operational requirements"
            msg = f"Your leave request for {leave_name} for {sd} was not approved. Your manager provided the reason that {reason}."
        elif status in ("submitted", "pending"):
            msg = f"Your leave request for {leave_name} from {sd} to {ed} is still awaiting manager approval. I can send a reminder to your manager if you'd like."
        elif status == "cancelled":
            msg = f"Your leave request for {leave_name} from {sd} to {ed} was cancelled."
        else:
            msg = f"Your leave request for {leave_name} from {sd} to {ed} has status: {status}."

        return {
            "success": True,
            "request_id": req["request_id"],
            "status": status,
            "leave_name": leave_name,
            "start_date": str(sd),
            "end_date": str(ed),
            "rejection_reason": req["rejection_reason"],
            "message": msg
        }

    # -------------------------------------------------------------------------
    # 6. Send Manager Reminder
    # -------------------------------------------------------------------------
    def send_manager_reminder(self, employee_id: str, request_id: int = None) -> dict:
        if request_id:
            rows = self._query(
                """SELECT r.request_id, r.leave_code, p.leave_name, r.start_date, r.end_date, r.status, e.manager_id, e.full_name
                   FROM leave_requests r
                   JOIN leave_policy p ON r.leave_code = p.leave_code
                   JOIN employees e ON r.employee_id = e.employee_id
                   WHERE r.request_id = %s AND r.employee_id = %s""",
                (request_id, str(employee_id))
            )
        else:
            rows = self._query(
                """SELECT r.request_id, r.leave_code, p.leave_name, r.start_date, r.end_date, r.status, e.manager_id, e.full_name
                   FROM leave_requests r
                   JOIN leave_policy p ON r.leave_code = p.leave_code
                   JOIN employees e ON r.employee_id = e.employee_id
                   WHERE r.employee_id = %s AND r.status IN ('submitted', 'pending')
                   ORDER BY r.request_id DESC LIMIT 1""",
                (str(employee_id),)
            )

        if not rows:
            return {"success": False, "message": "There is no pending leave request awaiting manager approval to send a reminder for."}

        req = rows[0]
        # Update reminder timestamp
        self._execute(
            "UPDATE leave_requests SET reminder_sent_at = CURRENT_TIMESTAMP WHERE request_id = %s",
            (req["request_id"],)
        )

        # Notify manager or HR
        body = (
            f"REMINDER: Pending Leave Request Approval\n\n"
            f"Employee: {req['full_name']} (ID: {employee_id})\n"
            f"Leave Type: {req['leave_name']}\n"
            f"Period: {req['start_date']} to {req['end_date']}\n"
            f"Request ID: #{req['request_id']}\n"
            f"Please review and approve or reject this request at your earliest convenience."
        )
        self._send_notification_email(f"Reminder: Pending Leave Approval - {req['full_name']}", body)

        return {
            "success": True,
            "request_id": req["request_id"],
            "message": f"I have sent a reminder notification to your manager regarding your pending {req['leave_name']} request for {req['start_date']} to {req['end_date']}."
        }

    def list_leave_types(self) -> dict:
        try:
            rows = self._query(
                """SELECT leave_code, leave_name, annual_range, short_note, applies_to
                   FROM leave_policy
                   ORDER BY FIELD(leave_code, 'CL','SL','MATERNITY','PATERNITY','COMP_OFF','BEREAVEMENT','SHORT_LEAVE','LWP')"""
            )
            if not rows:
                return {"success": False, "message": "No leave types found in the policy."}
            lines = []
            for r in rows:
                scope = "" if r["applies_to"] == "All" else f", {r['applies_to']} employees only"
                lines.append(f"{r['leave_name']} ({r['annual_range']}{scope}) — {r['short_note']}.")
            return {"success": True, "message": "\n".join(lines)}
        except Exception as e:
            logger.error("Error listing leave types: %s", e)
            return {"success": False, "message": f"Sorry, something went wrong looking that up ({e})."}

    def get_pending_leave_request(self, employee_id: str) -> dict:
        try:
            rows = self._query(
                """SELECT lr.request_id, lr.start_date, lr.end_date, lr.reason,
                          lp.leave_name
                   FROM leave_requests lr
                   JOIN leave_policy lp ON lp.leave_code = lr.leave_code
                   WHERE lr.employee_id = %s AND lr.status = 'submitted'
                   ORDER BY lr.requested_at DESC
                   LIMIT 1""",
                (str(employee_id),),
            )
            if not rows:
                return {"success": False, "message": "No leave request is currently pending approval for this employee."}
            r = rows[0]
            sd, ed = r["start_date"], r["end_date"]
            if hasattr(sd, "strftime") and hasattr(ed, "strftime"):
                if sd == ed:
                    date_phrase = sd.strftime("%B %d, %Y")
                elif sd.year == ed.year and sd.month == ed.month:
                    date_phrase = f"{sd.strftime('%B %d')} to {ed.strftime('%d, %Y')}"
                else:
                    date_phrase = f"{sd.strftime('%B %d, %Y')} to {ed.strftime('%B %d, %Y')}"
            else:
                date_phrase = f"{sd} to {ed}"
            existing = r["reason"].strip() if r["reason"] else ""
            reason_note = f" A reason is already on file: '{existing}'." if existing else " No reason is on file yet."
            return {
                "success": True,
                "request_id": r["request_id"],
                "message": (
                    f"Pending request found — request ID {r['request_id']}, {r['leave_name']}, "
                    f"{date_phrase}.{reason_note} Read the dates back to the "
                    f"caller and ask for a brief reason to pass along for approval."
                )
            }
        except Exception as e:
            logger.error("Error getting pending leave request: %s", e)
            return {"success": False, "message": f"Sorry, something went wrong looking that up ({e})."}

    def record_leave_verification_reason(self, request_id: int, reason: str) -> dict:
        try:
            reason_clean = (reason or "").strip()
            result = self._execute(
                "UPDATE leave_requests SET reason = %s WHERE request_id = %s AND status = 'submitted'",
                (reason_clean, request_id),
            )
            if result == 0:
                return {
                    "success": False,
                    "message": (
                        f"Request ID {request_id} was not found, or is no longer pending "
                        f"(already approved/rejected). Let the caller know their reason "
                        f"could not be attached and HR should be contacted directly."
                    )
                }
            return {
                "success": True,
                "message": (
                    "Reason recorded against the pending request. Let the caller know "
                    "this has been noted and passed along for approval — it does not "
                    "mean the leave is approved yet."
                )
            }
        except Exception as e:
            logger.error("Error recording leave verification reason: %s", e)
            return {"success": False, "message": f"Sorry, the reason could not be saved ({e})."}