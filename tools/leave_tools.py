"""
leave_tools.py
 
Leave Management Tool Functions using MySQL.
 
Matches hr-voicebot-schema-mysql.sql (v3) — 8 leave types in
leave_policy: CL, SL, MATERNITY, PATERNITY, COMP_OFF, BEREAVEMENT,
SHORT_LEAVE, LWP. Ported from the old (working) hr_tools.py's proven
two-step flow:
 
  1. check_leave_availability — computes days requested, compares
     against the balance, reports whether there's enough leave.
     Does NOT touch the database or send any email.
  2. confirm_leave_request — called only after the caller has
     explicitly confirmed. Re-validates the balance, inserts a row
     into leave_requests, increments leave_balances.used_days, and
     emails the request to HR.
 
Handles:
1. Check Leave Balance (single type or full summary)
2. Check Leave Availability (step 1 — no DB write)
3. Confirm / Apply Leave (step 2 — writes + emails HR)
4. Cancel Leave
5. List Leave Requests
6. Approve Leave
"""
 
import logging
import re
import smtplib
from datetime import date, datetime
from email.mime.text import MIMEText
 
from database.db import get_db_connection
from config import Config
 
logger = logging.getLogger("leave-tools")
 
# Friendly aliases so a caller saying "sick leave" or "half day" still
# resolves to the right leave_code without the LLM having to know the
# exact codes stored in leave_policy.
LEAVE_ALIASES = {
    "cl": "CL", "casual": "CL", "casual leave": "CL",
    "sl": "SL", "sick": "SL", "sick leave": "SL", "medical": "SL", "medical leave": "SL",
    "maternity": "MATERNITY", "maternity leave": "MATERNITY",
    "paternity": "PATERNITY", "paternity leave": "PATERNITY",
    "comp off": "COMP_OFF", "compensatory off": "COMP_OFF", "comp": "COMP_OFF",
    "earned": "COMP_OFF",
    "bereavement": "BEREAVEMENT", "bereavement leave": "BEREAVEMENT",
    "short leave": "SHORT_LEAVE", "half day": "SHORT_LEAVE", "half-day": "SHORT_LEAVE",
    "hourly": "SHORT_LEAVE", "hourly leave": "SHORT_LEAVE",
    "lwp": "LWP", "lop": "LWP", "loss of pay": "LWP", "leave without pay": "LWP",
}
 
VALID_LEAVE_CODES = {"CL", "SL", "MATERNITY", "PATERNITY", "COMP_OFF", "BEREAVEMENT", "SHORT_LEAVE", "LWP"}
 
# Human-readable order used everywhere balances/policy are listed.
_CODE_ORDER = "'CL','SL','MATERNITY','PATERNITY','COMP_OFF','BEREAVEMENT','SHORT_LEAVE','LWP'"
 
HR_NOTIFY_EMAIL = Config.HR_EMAIL or "hr@example.com"
 
 
def _resolve_leave_code(leave_type: str) -> str | None:
    if not leave_type:
        return None
    key = leave_type.strip().lower()
    if key in LEAVE_ALIASES:
        return LEAVE_ALIASES[key]
    key_upper = leave_type.strip().upper().replace(" ", "_")
    return key_upper if key_upper in VALID_LEAVE_CODES else None
 
 
def _clean_id(employee_id) -> str:
    """Digits only — phone STT often transcribes '1 0 0 1' / '1-001'."""
    return re.sub(r"[^0-9]", "", str(employee_id or ""))
 
 
def _parse_date(value: str):
    """Returns a date, or an error message string if invalid."""
    value = (value or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return f"'{value}' is not a valid date. Dates must be YYYY-MM-DD."
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return f"'{value}' is not a valid calendar date."
 
 
def _validate_leave_date_range(sd: date, ed: date) -> str | None:
    """Guards against a stale/wrong year slipping through."""
    today = date.today()
    if ed < today:
        return (
            f"'{sd.isoformat()}' to '{ed.isoformat()}' is entirely in the past — "
            f"today is {today.isoformat()}. Please confirm the correct year."
        )
    if sd.year - today.year > 1:
        return (
            f"'{sd.isoformat()}' is more than a year in the future — "
            f"today is {today.isoformat()}. Please confirm the year."
        )
    return None
 
 
def _send_hr_email(subject: str, body: str, reply_to: str = "") -> str | None:
    """Plain SMTP send using config.py's SMTP_HOST/PORT/USER/PASS.
    Returns None on success, or an error string on failure. If SMTP
    isn't configured, logs the email instead of raising — leave
    requests must never fail just because mail isn't set up yet."""
    if not Config.SMTP_HOST or not Config.SMTP_USER or not Config.SMTP_PASS:
        logger.warning(
            "SMTP not configured (SMTP_HOST/SMTP_USER/SMTP_PASS) — "
            "leave request email NOT sent. Would have sent:\nTo: %s\nSubject: %s\n%s",
            HR_NOTIFY_EMAIL, subject, body,
        )
        return "SMTP_HOST / SMTP_USER / SMTP_PASS are not configured."
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = Config.SMTP_USER
        msg["To"] = HR_NOTIFY_EMAIL
        if reply_to:
            msg["Reply-To"] = reply_to
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT) as server:
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASS)
            server.sendmail(Config.SMTP_USER, [HR_NOTIFY_EMAIL], msg.as_string())
        return None
    except Exception as e:
        logger.exception("SMTP email send failed")
        return str(e)
 
 
def _format_date_range(sd: date, ed: date) -> str:
    if sd == ed:
        return sd.strftime("%B %-d, %Y")
    if sd.year == ed.year and sd.month == ed.month:
        return f"{sd.strftime('%B %-d')} to {ed.strftime('%-d, %Y')}"
    return f"{sd.strftime('%B %-d, %Y')} to {ed.strftime('%B %-d, %Y')}"
 
 
class LeaveTools:
 
    def _connect(self):
        return get_db_connection()
 
    # --------------------------------------------------
    # Check Leave Balance — single type, or full summary
    # if leave_type is omitted (matches leave_balance tool
    # in agent.py, which only ever passes employee_id).
    # --------------------------------------------------
    def check_leave_balance(self, employee_id, leave_type: str | None = None):
        emp_id = _clean_id(employee_id)
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
 
        if leave_type:
            code = _resolve_leave_code(leave_type)
            if not code:
                cursor.close()
                conn.close()
                return {
                    "success": False,
                    "message": (
                        f"'{leave_type}' isn't one of the leave types on file. Available "
                        f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity "
                        f"Leave, Comp Off, Bereavement Leave, Short Leave, and Leave "
                        f"Without Pay."
                    ),
                }
            cursor.execute("""
                SELECT e.full_name, lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
                FROM leave_balances lb
                JOIN employees e ON e.employee_id = lb.employee_id
                JOIN leave_policy lp ON lp.leave_code = lb.leave_code
                WHERE lb.employee_id = %s AND lb.leave_code = %s
                LIMIT 1
            """, (emp_id, code))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if not row:
                return {
                    "success": False,
                    "message": f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID.",
                }
            return {
                "success": True,
                "employee_name": row["full_name"],
                "leave_type": row["leave_name"],
                "entitled_days": row["entitled_days"],
                "used_days": row["used_days"],
                "available_days": row["available_days"],
            }
 
        # No specific type — full summary across every leave type that
        # applies to this employee (matches "what's my leave balance").
        cursor.execute(f"""
            SELECT lp.leave_code, lp.leave_name, lb.entitled_days, lb.used_days, lb.available_days
            FROM leave_balances lb
            JOIN leave_policy lp ON lp.leave_code = lb.leave_code
            WHERE lb.employee_id = %s
            ORDER BY FIELD(lb.leave_code, {_CODE_ORDER})
        """, (emp_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
 
        if not rows:
            return {
                "success": False,
                "message": f"No leave records found for employee {employee_id}. Please confirm the ID.",
            }
 
        balances = [
            {
                "leave_code": r["leave_code"],
                "leave_type": r["leave_name"],
                "entitled_days": r["entitled_days"],
                "used_days": r["used_days"],
                "available_days": r["available_days"],
            }
            for r in rows
        ]
        return {"success": True, "balances": balances}
 
    # --------------------------------------------------
    # STEP 1 — Check Leave Availability (no DB write)
    # --------------------------------------------------
    def check_leave_availability(self, employee_id, leave_type: str, from_date: str, to_date: str):
        code = _resolve_leave_code(leave_type)
        if not code:
            return {
                "success": False,
                "message": (
                    f"'{leave_type}' isn't one of the leave types on file. Available "
                    f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity "
                    f"Leave, Comp Off, Bereavement Leave, Short Leave, and Leave "
                    f"Without Pay."
                ),
            }
 
        sd = _parse_date(from_date)
        if isinstance(sd, str):
            return {"success": False, "message": sd}
        ed = _parse_date(to_date)
        if isinstance(ed, str):
            return {"success": False, "message": ed}
        if ed < sd:
            return {"success": False, "message": "The end date can't be before the start date. Please confirm the dates."}
        date_error = _validate_leave_date_range(sd, ed)
        if date_error:
            return {"success": False, "message": date_error}
 
        days_requested = (ed - sd).days + 1
        emp_id = _clean_id(employee_id)
 
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.full_name, lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
            FROM leave_balances lb
            JOIN employees e ON e.employee_id = lb.employee_id
            JOIN leave_policy lp ON lp.leave_code = lb.leave_code
            WHERE lb.employee_id = %s AND lb.leave_code = %s
            LIMIT 1
        """, (emp_id, code))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
 
        if not row:
            return {
                "success": False,
                "message": f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID.",
            }
 
        result = {
            "success": True,
            "employee_name": row["full_name"],
            "leave_type": row["leave_name"],
            "days_requested": days_requested,
            "start_date": sd.isoformat(),
            "end_date": ed.isoformat(),
        }
 
        if row["entitled_days"] is None:
            result["unlimited"] = True
            result["available_days"] = None
            result["can_proceed"] = True
            return result
 
        available = row["available_days"]
        result["available_days"] = available
        result["can_proceed"] = days_requested <= available
        if not result["can_proceed"]:
            result["message"] = (
                f"Not enough balance: {row['full_name']} has only {available} day(s) of "
                f"{row['leave_name']} available, but {days_requested} day(s) were requested."
            )
        return result
 
    # --------------------------------------------------
    # STEP 2 — Confirm / Apply Leave (writes + emails HR)
    # --------------------------------------------------
    def confirm_leave_request(self, employee_id, leave_type: str, from_date: str, to_date: str, reason: str = ""):
        code = _resolve_leave_code(leave_type)
        if not code:
            return {
                "success": False,
                "message": (
                    f"'{leave_type}' isn't one of the leave types on file. Available "
                    f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity "
                    f"Leave, Comp Off, Bereavement Leave, Short Leave, and Leave "
                    f"Without Pay."
                ),
            }
 
        sd = _parse_date(from_date)
        if isinstance(sd, str):
            return {"success": False, "message": sd}
        ed = _parse_date(to_date)
        if isinstance(ed, str):
            return {"success": False, "message": ed}
        if ed < sd:
            return {"success": False, "message": "The end date can't be before the start date. Please confirm the dates."}
        date_error = _validate_leave_date_range(sd, ed)
        if date_error:
            return {"success": False, "message": date_error}
 
        days_requested = (ed - sd).days + 1
        emp_id = _clean_id(employee_id)
 
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.full_name, e.email AS employee_email, d.designation_name,
                   lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
            FROM leave_balances lb
            JOIN employees e ON e.employee_id = lb.employee_id
            JOIN designations d ON d.designation_id = e.designation_id
            JOIN leave_policy lp ON lp.leave_code = lb.leave_code
            WHERE lb.employee_id = %s AND lb.leave_code = %s
            LIMIT 1
        """, (emp_id, code))
        row = cursor.fetchone()
 
        if not row:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID.",
            }
 
        if row["entitled_days"] is not None and days_requested > row["available_days"]:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": (
                    f"Cannot confirm: {row['full_name']} only has {row['available_days']} day(s) "
                    f"of {row['leave_name']} available, which is less than the {days_requested} "
                    f"day(s) requested."
                ),
            }
 
        # 1. Insert the leave request record.
        cursor.execute("""
            INSERT INTO leave_requests
                (employee_id, leave_code, start_date, end_date, days_requested, reason, status, notified_email)
            VALUES (%s, %s, %s, %s, %s, %s, 'submitted', %s)
        """, (emp_id, code, sd.isoformat(), ed.isoformat(), days_requested, reason.strip(), HR_NOTIFY_EMAIL))
        request_id = cursor.lastrowid
 
        # 2. Update the used/available balance.
        cursor.execute("""
            UPDATE leave_balances
            SET used_days = used_days + %s
            WHERE employee_id = %s AND leave_code = %s
        """, (days_requested, emp_id, code))
 
        conn.commit()
        cursor.close()
        conn.close()
 
        # 3. Email HR.
        days_word = "day" if days_requested == 1 else "days"
        date_phrase = _format_date_range(sd, ed)
        reason_clean = reason.strip().strip(".").strip()
        reason_clause = f" for {reason_clean}" if reason_clean else ""
 
        subject = f"{row['full_name']} ({emp_id}) — {row['leave_name']} Request"
        signature = f"{row['full_name']}\n{row.get('designation_name', '')}".strip()
        body = (
            f"Hi,\n\n"
            f"I would like to request {row['leave_name']} for {days_requested} {days_word}, "
            f"from {date_phrase}{reason_clause}. I will be unable to be present at work "
            f"during this period.\n\n"
            f"Best regards,\n{signature}\n"
        )
        email_error = _send_hr_email(subject, body, reply_to=row.get("employee_email") or "")
 
        return {
            "success": True,
            "request_id": request_id,
            "days": days_requested,
            "leave_type": row["leave_name"],
            "start_date": sd.isoformat(),
            "end_date": ed.isoformat(),
            "status": "submitted",
            "hr_email_sent": email_error is None,
            "hr_email_error": email_error,
            "notified_email": HR_NOTIFY_EMAIL,
            "message": f"{row['leave_name']} leave request submitted successfully.",
        }
 
    # --------------------------------------------------
    # Apply Leave — thin alias to confirm_leave_request,
    # kept for any code path still calling apply_leave directly.
    # --------------------------------------------------
    def apply_leave(self, employee_id, from_date, to_date, leave_type="Casual", reason=""):
        return self.confirm_leave_request(employee_id, leave_type, from_date, to_date, reason)
 
    # --------------------------------------------------
    # Cancel Leave
    # --------------------------------------------------
    def cancel_leave(self, request_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
 
        cursor.execute("""
            SELECT employee_id, leave_code, days_requested, status
            FROM leave_requests
            WHERE request_id = %s
        """, (request_id,))
        row = cursor.fetchone()
 
        if row is None:
            cursor.close()
            conn.close()
            return {"success": False, "message": "Request not found."}
 
        cursor.execute("""
            UPDATE leave_balances
            SET used_days = GREATEST(0, used_days - %s)
            WHERE employee_id = %s AND leave_code = %s
        """, (row["days_requested"], row["employee_id"], row["leave_code"]))
 
        cursor.execute("""
            UPDATE leave_requests
            SET status = 'rejected'
            WHERE request_id = %s
        """, (request_id,))
 
        conn.commit()
        cursor.close()
        conn.close()
 
        return {"success": True, "message": "Leave cancelled successfully."}
 
    # --------------------------------------------------
    # List / History of Leave Requests
    # --------------------------------------------------
    def list_leave_requests(self, employee_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
 
        cursor.execute("""
            SELECT lr.request_id, lr.start_date, lr.end_date, lp.leave_name, lr.status
            FROM leave_requests lr
            JOIN leave_policy lp ON lp.leave_code = lr.leave_code
            WHERE lr.employee_id = %s
            ORDER BY lr.request_id DESC
        """, (_clean_id(employee_id),))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
 
        return {
            "success": True,
            "requests": [
                {
                    "request_id": r["request_id"],
                    "from": str(r["start_date"]),
                    "to": str(r["end_date"]),
                    "leave_type": r["leave_name"],
                    "status": r["status"],
                }
                for r in rows
            ],
        }
 
    # Alias — agent.py's leave_history tool expects this name.
    def leave_history(self, employee_id):
        return self.list_leave_requests(employee_id)
 
    # --------------------------------------------------
    # Approve Leave (Demo)
    # --------------------------------------------------
    def approve_leave(self, request_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE leave_requests SET status = 'approved' WHERE request_id = %s", (request_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"success": True, "message": "Leave Approved."}
 