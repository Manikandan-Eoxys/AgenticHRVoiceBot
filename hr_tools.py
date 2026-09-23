"""
hr_tools.py

Read/write tools over the `hr_voicebot` MySQL schema (employees,
departments, designations, leave_policy, leave_balances, leave_requests,
employee_schemes, insurance_plans, employee_insurance) for the voice
agent to call.

Matches hr-voicebot-schema-mysql.sql (v3):
- employees.employee_id is '1001'..'1015' — the bot always asks for this
  ID first, then reads the name back to the caller for verification
  before doing anything else (see get_employee_by_id).
- 8 leave types in leave_policy: CL, SL, MATERNITY, PATERNITY, COMP_OFF,
  BEREAVEMENT, SHORT_LEAVE, LWP.
- Leave application is a three-step flow across two tools:
    1. check_leave_availability — computes days requested, compares
       against the balance, and reports whether there's enough leave.
       Does NOT touch the database or send any email.
    2. confirm_leave_request — called only after the caller has
       explicitly said they want to go ahead. Re-validates the balance
       (in case anything changed), inserts a row into leave_requests,
       increments leave_balances.used_days, and emails the request to
       HR at LEAVE_REQUEST_EMAIL.
- Insurance is 3 fixed company plans (insurance_plans) with a
  per-employee applied/claimed table (employee_insurance).
"""

import os
import re
import logging
import smtplib
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

import mysql.connector
from livekit.agents import function_tool, RunContext

# Load env variables from .env or .env.local
_env_path = Path(__file__).resolve().parent / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent / ".env.local"
load_dotenv(_env_path)

logger = logging.getLogger("hr-tools")

DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "hrbot"),
    "password": os.environ.get("MYSQL_PASSWORD", "Eoxys@110"),
    "database": os.environ.get("MYSQL_DATABASE", "hr_voicebot"),
}

# All confirmed leave requests are emailed to HR at this address.
LEAVE_REQUEST_EMAIL = (
    os.environ.get("HR_NOTIFY_EMAIL")
    or os.environ.get("HR_EMAIL")
    or "manikandan.eoxys@gmail.com"
)

# Cc'd on every leave request email, e.g. the MD.
MD_NOTIFY_EMAIL = os.environ.get("MD_NOTIFY_EMAIL", "").strip()

# Company name used in signature
COMPANY_NAME = os.environ.get("COMPANY_NAME", "ABC Technologies").strip()

SMTP_CONFIG = {
    "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "port": int(os.environ.get("SMTP_PORT", "587")),
    "user": os.environ.get("SMTP_USER", ""),
    "password": os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS", ""),
    "from_addr": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
}

MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "smtp").strip().lower()

GRAPH_CONFIG = {
    "tenant_id": os.environ.get("MS_TENANT_ID", ""),
    "client_id": os.environ.get("MS_CLIENT_ID", ""),
    "client_secret": os.environ.get("MS_CLIENT_SECRET", ""),
    "sender": os.environ.get("MS_SENDER_EMAIL", ""),
}


def _query(sql: str, params: tuple = ()) -> list[dict] | str:
    """Runs a SELECT and returns rows, or a '__DB_ERROR__: ...' sentinel
    string on failure."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        logger.exception("DB connection failed (host=%s db=%s user=%s)",
                          DB_CONFIG["host"], DB_CONFIG["database"], DB_CONFIG["user"])
        return f"__DB_ERROR__: connection failed — {e}"

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    except mysql.connector.Error as e:
        logger.exception("Query failed: %s | params=%s", sql, params)
        return f"__DB_ERROR__: query failed — {e}"
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()) -> int | str:
    """Runs an INSERT/UPDATE and returns lastrowid (or affected rowcount
    for updates), or a '__DB_ERROR__: ...' sentinel on failure."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        logger.exception("DB connection failed (host=%s db=%s user=%s)",
                          DB_CONFIG["host"], DB_CONFIG["database"], DB_CONFIG["user"])
        return f"__DB_ERROR__: connection failed — {e}"

    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        result = cur.lastrowid if cur.lastrowid else cur.rowcount
        cur.close()
        return result
    except mysql.connector.Error as e:
        conn.rollback()
        logger.exception("Write failed: %s | params=%s", sql, params)
        return f"__DB_ERROR__: write failed — {e}"
    finally:
        conn.close()


def _is_error(rows) -> bool:
    return isinstance(rows, str) and rows.startswith("__DB_ERROR__:")


def _send_email_smtp(subject: str, body: str, to_addr: str, cc_addr: str = "", reply_to: str = "") -> str | None:
    """Sends via plain SMTP (e.g. Gmail). Returns None on success, or an error string."""
    if not SMTP_CONFIG["user"] or not SMTP_CONFIG["password"]:
        msg = "SMTP_USER / SMTP_PASSWORD are not configured in the environment."
        logger.warning(msg)
        return msg
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_CONFIG["from_addr"] or SMTP_CONFIG["user"]
        msg["To"] = to_addr
        recipients = [to_addr]
        if cc_addr:
            msg["Cc"] = cc_addr
            recipients.append(cc_addr)
        if reply_to:
            msg["Reply-To"] = reply_to
        with smtplib.SMTP(SMTP_CONFIG["host"], SMTP_CONFIG["port"]) as server:
            server.starttls()
            server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
            server.sendmail(SMTP_CONFIG["from_addr"] or SMTP_CONFIG["user"], recipients, msg.as_string())
        return None
    except Exception as e:
        logger.exception("SMTP email send failed")
        return str(e)


_graph_token_cache = {"token": None, "expires_at": 0}


def _get_graph_token() -> tuple[str | None, str | None]:
    """Fetches (and caches) an app-only Graph API access token via OAuth2."""
    import time
    now = time.time()
    if _graph_token_cache["token"] and _graph_token_cache["expires_at"] > now + 60:
        return _graph_token_cache["token"], None

    if not all([GRAPH_CONFIG["tenant_id"], GRAPH_CONFIG["client_id"], GRAPH_CONFIG["client_secret"]]):
        return None, "MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET are not configured in the environment."

    try:
        import requests
        url = f"https://login.microsoftonline.com/{GRAPH_CONFIG['tenant_id']}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": GRAPH_CONFIG["client_id"],
            "client_secret": GRAPH_CONFIG["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        }
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        token = payload["access_token"]
        _graph_token_cache["token"] = token
        _graph_token_cache["expires_at"] = now + int(payload.get("expires_in", 3600))
        return token, None
    except Exception as e:
        logger.exception("Graph token fetch failed")
        return None, str(e)


def _send_email_graph(subject: str, body: str, to_addr: str, cc_addr: str = "", reply_to: str = "") -> str | None:
    """Sends via Microsoft Graph API."""
    if not GRAPH_CONFIG["sender"]:
        msg = "MS_SENDER_EMAIL is not configured in the environment."
        logger.error(msg)
        return msg

    token, err = _get_graph_token()
    if err:
        logger.error("Graph auth failed: %s", err)
        return err

    try:
        import requests
        url = f"https://graph.microsoft.com/v1.0/users/{GRAPH_CONFIG['sender']}/sendMail"
        message = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_addr}}],
        }
        if cc_addr:
            message["ccRecipients"] = [{"emailAddress": {"address": cc_addr}}]
        if reply_to:
            message["replyTo"] = [{"emailAddress": {"address": reply_to}}]
        payload = {"message": message, "saveToSentItems": "true"}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 202:
            return None
        msg = f"Graph sendMail failed ({resp.status_code}): {resp.text}"
        logger.error(msg)
        return msg
    except Exception as e:
        logger.exception("Graph email send failed")
        return str(e)


def _send_email(subject: str, body: str, to_addr: str = LEAVE_REQUEST_EMAIL,
                 cc_addr: str = "", reply_to: str = "") -> str | None:
    """Dispatches to the configured provider (MAIL_PROVIDER=smtp or graph)."""
    if MAIL_PROVIDER == "graph":
        return _send_email_graph(subject, body, to_addr, cc_addr, reply_to)
    elif MAIL_PROVIDER == "smtp":
        return _send_email_smtp(subject, body, to_addr, cc_addr, reply_to)
    else:
        msg = f"Unknown MAIL_PROVIDER '{MAIL_PROVIDER}' — must be 'smtp' or 'graph'."
        logger.error(msg)
        return msg


# Friendly aliases so a caller saying "sick leave" or "half day" still
# resolves to the right leave_code without the LLM having to know the
# exact codes stored in the DB.
_LEAVE_ALIASES = {
    "cl": "CL", "casual": "CL", "casual leave": "CL",
    "sl": "SL", "sick": "SL", "sick leave": "SL", "medical": "SL", "medical leave": "SL", "sicleo": "SL",
    "maternity": "MATERNITY", "maternity leave": "MATERNITY",
    "paternity": "PATERNITY", "paternity leave": "PATERNITY",
    "comp off": "COMP_OFF", "compensatory off": "COMP_OFF", "comp": "COMP_OFF", "earned": "COMP_OFF",
    "bereavement": "BEREAVEMENT", "bereavement leave": "BEREAVEMENT",
    "short leave": "SHORT_LEAVE", "half day": "SHORT_LEAVE", "half-day": "SHORT_LEAVE",
    "hourly": "SHORT_LEAVE", "hourly leave": "SHORT_LEAVE",
    "lwp": "LWP", "lop": "LWP", "loss of pay": "LWP", "leave without pay": "LWP",
}

_VALID_LEAVE_CODES = {"CL", "SL", "MATERNITY", "PATERNITY", "COMP_OFF", "BEREAVEMENT", "SHORT_LEAVE", "LWP"}


def _resolve_leave_code(leave_type: str) -> str | None:
    if not leave_type:
        return None
    key = leave_type.strip().lower()
    if key in _LEAVE_ALIASES:
        return _LEAVE_ALIASES[key]
    key_upper = leave_type.strip().upper().replace(" ", "_")
    return key_upper if key_upper in _VALID_LEAVE_CODES else None


def _clean_id(employee_id: str) -> str:
    """Normalizes an employee ID to digits only."""
    return re.sub(r"[^0-9]", "", str(employee_id or ""))


def _parse_date(value: str) -> date | str:
    """Parses an ISO 'YYYY-MM-DD' date string. Returns a date, or an
    error message string if it can't be parsed."""
    value = str(value or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return f"'{value}' is not a valid date. Please provide dates as YYYY-MM-DD."
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return f"'{value}' is not a valid calendar date."


def _validate_leave_date_range(sd: date, ed: date) -> str | None:
    """Guards against a wrong year slipping through."""
    today = date.today()
    if ed < today:
        return (
            f"'{sd.isoformat()}' to '{ed.isoformat()}' is entirely in the past — today is "
            f"{today.isoformat()}. This is likely a wrong year. Ask the caller to confirm the "
            f"year and re-submit with the correct dates; do not guess."
        )
    if sd.year - today.year > 1:
        return (
            f"'{sd.isoformat()}' is more than a year in the future — today is "
            f"{today.isoformat()}. Double check the year with the caller before proceeding."
        )
    return None


def _require_verified(context: RunContext, employee_id: str) -> str | None:
    """Security gate: every leave/insurance/balance tool must only ever
    act on the employee ID that was actually verified for THIS call."""
    verified = getattr(context.session, "verified_employee_id", None)
    if verified is None:
        return ("No employee has been verified yet on this call. Verify the "
                "caller's identity with get_employee_by_id and confirm_employee_identity first.")
    if _clean_id(employee_id) != verified:
        return (
            f"This call is verified for employee {verified} only. I can't access or submit "
            f"anything for employee {employee_id} — they would need to call in and verify "
            f"their own identity first."
        )
    return None


@function_tool
async def confirm_employee_identity(employee_id: str, context: RunContext) -> str:
    """Call this immediately after the caller says yes to the identity
    verification question (e.g. 'This is employee 1001, Ravikala, is that
    correct?' -> caller says yes). Locks this call to that employee ID —
    every other tool (leave balance, leave requests, insurance info,
    schemes) will refuse to act on any other employee ID for the rest of
    this call, even if the caller later mentions a different ID. Must be
    called before any leave, balance, or insurance tool."""
    emp_id = _clean_id(employee_id)
    context.session.verified_employee_id = emp_id
    return f"Identity locked to employee {emp_id} for this call."


@function_tool
async def get_employee_by_id(employee_id: str) -> str:
    """Look up an employee by their employee ID (e.g. 1001 through 1015).
    Always call this first when a caller starts a conversation or gives
    an ID, before any other lookup. Returns a verification line — read
    the name back to the caller and get an explicit yes before treating
    them as that employee (e.g. 'This is employee ID 1001, Ravikala —
    is that correct?'). Also returns department, designation, and
    status for your own context."""
    rows = _query(
        """SELECT e.employee_id, e.full_name, e.status,
                  des.designation_name, d.department_name
           FROM employees e
           JOIN designations des ON des.designation_id = e.designation_id
           JOIN departments d ON d.department_id = e.department_id
           WHERE e.employee_id = %s
           LIMIT 1""",
        (_clean_id(employee_id),),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"No employee found with ID {employee_id}. Employee IDs run from 1001 to 1015. Could you confirm the ID?"
    r = rows[0]
    return (
        f"VERIFY WITH CALLER: This is employee ID {r['employee_id']}, {r['full_name']} — "
        f"is that correct? ({r['designation_name']} in {r['department_name']}, status: {r['status']}.) "
        f"Do not proceed with any other request until the caller confirms this is them."
    )


def get_employee_contact(employee_id: str) -> dict | None:
    """Plain helper (NOT a function_tool — not exposed to the LLM).
    Used by the outbound-calling script to look up an employee's name
    and phone number before dialing them."""
    rows = _query(
        "SELECT employee_id, full_name, phone_number FROM employees WHERE employee_id = %s LIMIT 1",
        (_clean_id(employee_id),),
    )
    if _is_error(rows) or not rows or not rows[0].get("phone_number"):
        return None
    r = rows[0]
    phone = r["phone_number"].strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")
    return {"employee_id": r["employee_id"], "full_name": r["full_name"], "phone_number": phone}


@function_tool
async def list_department_employees(department_name: str) -> str:
    """List everyone in a given department, by department name."""
    rows = _query(
        """SELECT e.full_name, des.designation_name
           FROM employees e
           JOIN departments d ON d.department_id = e.department_id
           JOIN designations des ON des.designation_id = e.designation_id
           WHERE d.department_name LIKE %s
           ORDER BY e.full_name""",
        (f"%{department_name}%",),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"No department found matching '{department_name}'."
    return "\n".join(f"{r['full_name']} — {r['designation_name']}" for r in rows)


@function_tool
async def list_leave_types() -> str:
    """List all leave types available in the policy, with a one-line
    summary of each. Use this when a caller asks about leave in general,
    or asks 'what leave can I apply for' without naming a specific type,
    since there are 8 possible kinds and they may not know them."""
    rows = _query(
        """SELECT leave_code, leave_name, annual_range, short_note, applies_to
           FROM leave_policy
           ORDER BY FIELD(leave_code, 'CL','SL','MATERNITY','PATERNITY','COMP_OFF','BEREAVEMENT','SHORT_LEAVE','LWP')"""
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    lines = []
    for r in rows:
        scope = "" if r["applies_to"] == "All" else f", {r['applies_to']} employees only"
        lines.append(f"{r['leave_name']} ({r['annual_range']}{scope}) — {r['short_note']}.")
    return "\n".join(lines)


@function_tool
async def get_leave_policy(leave_type: str) -> str:
    """Get the full written policy description for one leave type, e.g.
    Casual Leave, Sick Leave, Maternity, Paternity, Comp Off, Bereavement,
    Short Leave, or LWP. Use this when the caller asks how a specific
    leave type works, its eligibility, or how many days it covers."""
    code = _resolve_leave_code(leave_type)
    if not code:
        return (
            f"'{leave_type}' isn't one of the leave types on file. The available "
            f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity Leave, "
            f"Comp Off, Bereavement Leave, Short Leave, and Leave Without Pay."
        )
    rows = _query(
        "SELECT leave_name, annual_range, applies_to, description FROM leave_policy WHERE leave_code = %s",
        (code,),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"No policy found for '{leave_type}'."
    r = rows[0]
    scope = "" if r["applies_to"] == "All" else f" This applies to {r['applies_to'].lower()} employees only."
    return f"{r['leave_name']} ({r['annual_range']}): {r['description']}{scope}"


@function_tool
async def get_leave_balance(employee_id: str, leave_type: str, context: RunContext) -> str:
    """Get an employee's balance for one specific leave type: entitled
    days, days used, and days available. If the employee has no record
    for that type (for example Maternity for a male employee), say
    plainly that it does not apply to them, without guessing why."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    code = _resolve_leave_code(leave_type)
    if not code:
        return (
            f"'{leave_type}' isn't one of the leave types on file. The available "
            f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity Leave, "
            f"Comp Off, Bereavement Leave, Short Leave, and Leave Without Pay."
        )
    rows = _query(
        """SELECT e.full_name, lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
           FROM leave_balances lb
           JOIN employees e ON e.employee_id = lb.employee_id
           JOIN leave_policy lp ON lp.leave_code = lb.leave_code
           WHERE lb.employee_id = %s AND lb.leave_code = %s
           LIMIT 1""",
        (_clean_id(employee_id), code),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID."
    r = rows[0]
    if r["entitled_days"] is None:
        return (
            f"{r['full_name']} has taken {r['used_days']} day(s) of {r['leave_name']} so far. "
            f"This leave type has no fixed entitlement — it's unpaid and used only once other "
            f"leave balances are exhausted."
        )
    return (
        f"{r['full_name']} has used {r['used_days']} of {r['entitled_days']} days of "
        f"{r['leave_name']}, leaving {r['available_days']} available."
    )


@function_tool
async def get_all_leave_balances(employee_id: str, context: RunContext) -> str:
    """Get an employee's balance across every leave type that applies to
    them. Use this when the caller just asks 'what's my leave balance'
    generally, without naming a type."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    rows = _query(
        """SELECT lp.leave_name, lb.entitled_days, lb.used_days, lb.available_days
           FROM leave_balances lb
           JOIN leave_policy lp ON lp.leave_code = lb.leave_code
           WHERE lb.employee_id = %s
           ORDER BY FIELD(lb.leave_code, 'CL','SL','MATERNITY','PATERNITY','COMP_OFF','BEREAVEMENT','SHORT_LEAVE','LWP')""",
        (_clean_id(employee_id),),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"No leave records found for employee {employee_id}. Please confirm the ID."
    lines = []
    for r in rows:
        if r["entitled_days"] is None:
            lines.append(f"{r['leave_name']}: {r['used_days']} day(s) taken, no fixed cap")
        else:
            lines.append(f"{r['leave_name']}: {r['used_days']} of {r['entitled_days']} used, {r['available_days']} available")
    return "\n".join(lines)


@function_tool
async def check_leave_availability(
    employee_id: str, leave_type: str, start_date: str, end_date: str, context: RunContext
) -> str:
    """STEP 1 of applying for leave. Call this first whenever a caller
    asks to take leave on specific dates (e.g. 'I want sick leave on
    August 27 and 28'). Convert whatever dates the caller says into
    YYYY-MM-DD before calling (assume the current year unless they say
    otherwise). This tool only checks the balance and reports back — it
    does NOT submit anything or send any email. If there's enough leave
    available, tell the caller how many days are available and ask if
    they're ready to go ahead; only call confirm_leave_request after
    they explicitly say yes."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    code = _resolve_leave_code(leave_type)
    if not code:
        return (
            f"'{leave_type}' isn't one of the leave types on file. The available "
            f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity Leave, "
            f"Comp Off, Bereavement Leave, Short Leave, and Leave Without Pay."
        )
    sd = _parse_date(start_date)
    if isinstance(sd, str):
        return sd
    ed = _parse_date(end_date)
    if isinstance(ed, str):
        return ed
    if ed < sd:
        return "The end date can't be before the start date. Could you confirm the dates?"
    date_error = _validate_leave_date_range(sd, ed)
    if date_error:
        return date_error
    days_requested = (ed - sd).days + 1

    rows = _query(
        """SELECT e.full_name, lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
           FROM leave_balances lb
           JOIN employees e ON e.employee_id = lb.employee_id
           JOIN leave_policy lp ON lp.leave_code = lb.leave_code
           WHERE lb.employee_id = %s AND lb.leave_code = %s
           LIMIT 1""",
        (_clean_id(employee_id), code),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID."
    r = rows[0]

    if r["entitled_days"] is None:
        return (
            f"{r['leave_name']} has no fixed entitlement for {r['full_name']} — it's unpaid "
            f"leave, usable once other balances are exhausted. Requested: {days_requested} day(s) "
            f"from {sd.isoformat()} to {ed.isoformat()}. Ask the caller if they're ready to proceed."
        )

    available = r["available_days"]
    if days_requested > available:
        return (
            f"Not enough balance: {r['full_name']} has only {available} day(s) of "
            f"{r['leave_name']} available, but {days_requested} day(s) were requested "
            f"({sd.isoformat()} to {ed.isoformat()}). Let the caller know they don't have "
            f"enough balance and cannot proceed with these dates."
        )

    return (
        f"Available: {r['full_name']} has {available} day(s) of {r['leave_name']} available, "
        f"and is requesting {days_requested} day(s) from {sd.isoformat()} to {ed.isoformat()}. "
        f"Tell the caller how many days they have available and ask if they're ready to take "
        f"this leave. Only call confirm_leave_request once they explicitly confirm yes."
    )


def _format_date_range(sd: date, ed: date) -> str:
    """'August 27, 2026' for a single day, or 'August 27 to August 29,
    2026' for a range — used in the natural-language email body."""
    if sd == ed:
        return sd.strftime("%B %-d, %Y")
    if sd.year == ed.year and sd.month == ed.month:
        return f"{sd.strftime('%B %-d')} to {ed.strftime('%-d, %Y')}"
    return f"{sd.strftime('%B %-d, %Y')} to {ed.strftime('%B %-d, %Y')}"


@function_tool
async def confirm_leave_request(
    employee_id: str, leave_type: str, start_date: str, end_date: str, context: RunContext, reason: str = ""
) -> str:
    """STEP 2 of applying for leave. Call this ONLY after the caller has
    explicitly confirmed they want to go ahead, following a prior
    check_leave_availability call for the same employee, leave type,
    and dates. This re-checks the balance, records the leave request,
    updates the balance, and emails the request to HR. Ask the caller
    for a brief reason first if they haven't given one; pass an empty
    string if they decline to give one."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    code = _resolve_leave_code(leave_type)
    if not code:
        return (
            f"'{leave_type}' isn't one of the leave types on file. The available "
            f"types are Casual Leave, Sick Leave, Maternity Leave, Paternity Leave, "
            f"Comp Off, Bereavement Leave, Short Leave, and Leave Without Pay."
        )
    sd = _parse_date(start_date)
    if isinstance(sd, str):
        return sd
    ed = _parse_date(end_date)
    if isinstance(ed, str):
        return ed
    if ed < sd:
        return "The end date can't be before the start date. Could you confirm the dates?"
    date_error = _validate_leave_date_range(sd, ed)
    if date_error:
        return date_error
    days_requested = (ed - sd).days + 1
    emp_id = _clean_id(employee_id)

    rows = _query(
        """SELECT e.full_name, e.email AS employee_email, d.designation_name,
                  lb.entitled_days, lb.used_days, lb.available_days, lp.leave_name
           FROM leave_balances lb
           JOIN employees e ON e.employee_id = lb.employee_id
           JOIN designations d ON d.designation_id = e.designation_id
           JOIN leave_policy lp ON lp.leave_code = lb.leave_code
           WHERE lb.employee_id = %s AND lb.leave_code = %s
           LIMIT 1""",
        (emp_id, code),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"{leave_type} does not apply to employee {employee_id}, or no employee was found with that ID."
    r = rows[0]

    if r["entitled_days"] is not None and days_requested > r["available_days"]:
        return (
            f"Cannot confirm: {r['full_name']} only has {r['available_days']} day(s) of "
            f"{r['leave_name']} available, which is less than the {days_requested} day(s) "
            f"requested. Let the caller know this can't go through as-is."
        )

    # 1. Insert the leave request record.
    insert_result = _execute(
        """INSERT INTO leave_requests
               (employee_id, leave_code, start_date, end_date, days_requested, reason, status, notified_email)
           VALUES (%s, %s, %s, %s, %s, %s, 'submitted', %s)""",
        (emp_id, code, sd.isoformat(), ed.isoformat(), days_requested, reason.strip(), LEAVE_REQUEST_EMAIL),
    )
    if _is_error(insert_result):
        return f"Sorry, the request could not be recorded ({insert_result}). Please try again."

    # 2. Update the used balance (available_days is auto-computed by MySQL stored column).
    update_result = _execute(
        "UPDATE leave_balances SET used_days = used_days + %s WHERE employee_id = %s AND leave_code = %s",
        (days_requested, emp_id, code),
    )
    if _is_error(update_result):
        logger.error("Leave request %s was recorded but balance update failed: %s", insert_result, update_result)

    # 3. Email HR (Cc MD if configured), Reply-To the employee so a
    #    reply from HR lands straight in the employee's own inbox.
    days_word = "day" if days_requested == 1 else "days"
    date_phrase = _format_date_range(sd, ed)
    reason_clean = reason.strip().strip(".").strip()
    if reason_clean:
        if reason_clean.lower().startswith(("to ", "because ", "as ", "since ")):
            reason_clause = f" {reason_clean}"
        else:
            reason_clause = f" for {reason_clean}"
    else:
        reason_clause = ""

    req_id = insert_result
    subject = f"[REQ-{req_id}] {r['full_name']} ({emp_id}) — {r['leave_name']} Request"

    signature_lines = [r["full_name"]]
    if r.get("designation_name"):
        signature_lines.append(r["designation_name"])
    if COMPANY_NAME:
        signature_lines.append(COMPANY_NAME)
    signature = "\n".join(signature_lines)

    bot_reply_to = SMTP_CONFIG["from_addr"] or SMTP_CONFIG["user"] or "manikandan2034511@gmail.com"

    body = (
        f"Hi Mam/sir,\n\n"
        f"I would like to kindly request {r['leave_name']} for {days_requested} {days_word}, "
        f"from {date_phrase}{reason_clause}. I will be unable to be present at work during this "
        f"period. I would be grateful for your consideration.\n\n"
        f"Best regards,\n"
        f"{signature}\n\n"
        f"──────────────────────────────────────────────────────────\n"
        f"HOW TO RESPOND TO THIS LEAVE REQUEST:\n"
        f"Simply reply directly to this email with:\n"
        f"  • 'Approved' to approve this request.\n"
        f"  • 'Rejected: <reason>' to reject with a reason.\n"
        f"Your response will automatically update the HR system.\n"
        f"──────────────────────────────────────────────────────────\n"
    )
    email_error = _send_email(
        subject, body,
        to_addr=LEAVE_REQUEST_EMAIL,
        cc_addr=MD_NOTIFY_EMAIL,
        reply_to=bot_reply_to,
    )
    if email_error:
        return (
            f"The leave request was recorded (request ID {insert_result}) and the balance "
            f"was updated, but the notification email to HR could not be sent ({email_error}). "
            f"Let the caller know the request is in but HR should be notified manually."
        )

    return (
        f"Confirmed: {r['full_name']}'s request for {days_requested} day(s) of {r['leave_name']} "
        f"from {sd.isoformat()} to {ed.isoformat()} has been recorded (request ID {insert_result}) "
        f"and emailed to HR at {LEAVE_REQUEST_EMAIL}. Let the caller know it's been submitted."
    )


@function_tool
async def get_pending_leave_request(employee_id: str, context: RunContext) -> str:
    """Look up this employee's most recent leave request that is still
    awaiting approval (status = 'submitted'). Use this at the start of
    an OUTBOUND leave-verification call, right after the caller's
    identity has been confirmed, to find out which request you're
    calling about."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    rows = _query(
        """SELECT lr.request_id, lr.start_date, lr.end_date, lr.reason,
                  lp.leave_name
           FROM leave_requests lr
           JOIN leave_policy lp ON lp.leave_code = lr.leave_code
           WHERE lr.employee_id = %s AND lr.status = 'submitted'
           ORDER BY lr.requested_at DESC
           LIMIT 1""",
        (_clean_id(employee_id),),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return "No leave request is currently pending approval for this employee."
    r = rows[0]
    sd, ed = r["start_date"], r["end_date"]
    existing = r["reason"].strip() if r["reason"] else ""
    reason_note = f" A reason is already on file: '{existing}'." if existing else " No reason is on file yet."
    return (
        f"Pending request found — request ID {r['request_id']}, {r['leave_name']}, "
        f"{_format_date_range(sd, ed)}.{reason_note} Read the dates back to the "
        f"caller and ask for a brief reason to pass along for approval."
    )


@function_tool
async def record_leave_verification_reason(request_id: int, reason: str, context: RunContext) -> str:
    """Save the reason an employee gives during an OUTBOUND leave
    verification call, for the request ID returned by
    get_pending_leave_request."""
    reason_clean = reason.strip()
    result = _execute(
        "UPDATE leave_requests SET reason = %s WHERE request_id = %s AND status = 'submitted'",
        (reason_clean, request_id),
    )
    if _is_error(result):
        return f"Sorry, the reason could not be saved ({result})."
    if result == 0:
        return (
            f"Request ID {request_id} was not found, or is no longer pending "
            f"(already approved/rejected). Let the caller know their reason "
            f"could not be attached and HR should be contacted directly."
        )
    return (
        "Reason recorded against the pending request. Let the caller know "
        "this has been noted and passed along for approval — it does not "
        "mean the leave is approved yet."
    )


@function_tool
async def get_employee_schemes(employee_id: str, context: RunContext) -> str:
    """List the statutory or company schemes an employee is enrolled in,
    such as EPF, Maternity Benefit, or Paternity Benefit."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    rows = _query(
        """SELECT s.scheme_name, s.category, es.enrolled_date
           FROM employee_schemes es
           JOIN employees e ON e.employee_id = es.employee_id
           JOIN schemes s ON s.scheme_id = es.scheme_id
           WHERE e.employee_id = %s
           ORDER BY s.scheme_name""",
        (_clean_id(employee_id),),
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    if not rows:
        return f"No scheme enrollments found for employee {employee_id}. Please confirm the ID."
    return "\n".join(
        f"{r['scheme_name']} ({r['category']}), enrolled {r['enrolled_date']}"
        for r in rows
    )


@function_tool
async def list_insurance_plans() -> str:
    """List the office health insurance plans the company offers, with
    coverage amounts."""
    rows = _query(
        "SELECT plan_name, sum_insured, description FROM insurance_plans ORDER BY plan_code"
    )
    if _is_error(rows):
        return f"Sorry, something went wrong looking that up ({rows})."
    return "\n".join(
        f"{r['plan_name']} — {r['description']} (sum insured Rs.{r['sum_insured']:,.0f})"
        for r in rows
    )


@function_tool
async def get_insurance_info(employee_id: str, context: RunContext) -> str:
    """Check an employee's office health insurance status."""
    guard = _require_verified(context, employee_id)
    if guard:
        return guard
    plan_rows = _query(
        "SELECT plan_code, plan_name, sum_insured FROM insurance_plans ORDER BY plan_code"
    )
    if _is_error(plan_rows):
        return f"Sorry, something went wrong looking that up ({plan_rows})."

    emp_rows = _query(
        """SELECT e.full_name, ip.plan_name, ei.applied_date, ei.claimed, ei.claimed_date
           FROM employee_insurance ei
           JOIN employees e ON e.employee_id = ei.employee_id
           JOIN insurance_plans ip ON ip.plan_code = ei.plan_code
           WHERE ei.employee_id = %s
           ORDER BY ip.plan_code""",
        (_clean_id(employee_id),),
    )
    if _is_error(emp_rows):
        return f"Sorry, something went wrong looking that up ({emp_rows})."

    plan_list = ", ".join(f"{p['plan_name']} (up to Rs.{p['sum_insured']:,.0f})" for p in plan_rows)

    if not emp_rows:
        return (
            f"There are {len(plan_rows)} office health insurance plans available: {plan_list}. "
            f"Employee {employee_id} has not applied for any of them yet, or no employee was "
            f"found with that ID."
        )

    name = emp_rows[0]["full_name"]
    applied = [r["plan_name"] for r in emp_rows]
    claimed = [r["plan_name"] for r in emp_rows if r["claimed"]]

    applied_str = ", ".join(applied)
    claimed_str = ", ".join(claimed) if claimed else "none of them yet"

    return (
        f"There are {len(plan_rows)} office health insurance plans available: {plan_list}. "
        f"Of these, {name} has applied for {len(applied)}: {applied_str}. "
        f"{name} has claimed {claimed_str}."
    )


def run_startup_check() -> None:
    """Sanity check on startup to log database connectivity and employee count."""
    print(f"[hr_tools startup check] Leave request emails will be sent to: {LEAVE_REQUEST_EMAIL}")
    print(f"[hr_tools startup check] Cc (MD): {MD_NOTIFY_EMAIL or '(not set — no Cc)'}")
    print(f"[hr_tools startup check] MAIL_PROVIDER = {MAIL_PROVIDER}")
    if MAIL_PROVIDER == "graph":
        missing = [k for k in ("tenant_id", "client_id", "client_secret", "sender") if not GRAPH_CONFIG[k]]
        if missing:
            print(f"[hr_tools startup check] WARNING: Graph config missing: {missing} — "
                  "emails will fail to send even though the leave request will still be recorded.")
        else:
            print(f"[hr_tools startup check] Graph configured: sending as {GRAPH_CONFIG['sender']} "
                  f"via tenant {GRAPH_CONFIG['tenant_id']}")
    elif MAIL_PROVIDER == "smtp":
        if not SMTP_CONFIG["user"] or not SMTP_CONFIG["password"]:
            print("[hr_tools startup check] WARNING: SMTP_USER/SMTP_PASSWORD not set — "
                  "emails will fail to send even though the leave request will still be recorded.")
        else:
            print(f"[hr_tools startup check] SMTP configured: {SMTP_CONFIG['user']} via "
                  f"{SMTP_CONFIG['host']}:{SMTP_CONFIG['port']}")

    target = f"{DB_CONFIG['host']}/{DB_CONFIG['database']} as {DB_CONFIG['user']}"
    rows = _query("SELECT employee_id, full_name FROM employees ORDER BY employee_id")
    if _is_error(rows):
        logger.error("STARTUP CHECK FAILED — could not reach %s (%s)", target, rows)
        print(f"[hr_tools startup check] FAILED connecting to {target}: {rows}")
        return
    print(f"[hr_tools startup check] Connected to {target} — {len(rows)} employee(s) ready.")


# ---------------------------------------------------------------------------
# Import modular domain tools
# ---------------------------------------------------------------------------
from tools.policy_tools import (
    get_hr_policy,
    check_company_holiday,
    list_upcoming_company_holidays,
)
from tools.leave_tools import (
    cancel_leave_request,
    get_leave_request_status,
    send_manager_leave_reminder,
)
from tools.attendance_tools import (
    check_attendance_status,
    get_late_marks,
    submit_attendance_regularization,
    get_regularization_status,
)
from tools.wfh_tools import (
    check_wfh_eligibility,
    get_wfh_quota_balance,
    apply_wfh_request,
    get_wfh_request_status,
)
from tools.payroll_tools import (
    get_salary_credit_date,
    get_basic_salary_info,
    get_salary_deductions_info,
    check_payslip_status,
)
from tools.grievance_tools import (
    get_code_of_conduct_policy,
    report_confidential_grievance,
)
from tools.ticket_tools import (
    raise_hr_ticket,
    check_my_hr_tickets,
)
from tools.reminder_tools import (
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
)

# All tools list exported for Agent
ALL_TOOLS = [
    # Core Identity & Directory
    get_employee_by_id,
    confirm_employee_identity,
    list_department_employees,
    get_employee_schemes,
    list_insurance_plans,
    get_insurance_info,

    # 1. Leave & Holiday Policy & Actions
    list_leave_types,
    get_leave_policy,
    get_leave_balance,
    get_all_leave_balances,
    check_leave_availability,
    confirm_leave_request,
    cancel_leave_request,
    get_leave_request_status,
    send_manager_leave_reminder,
    get_pending_leave_request,
    record_leave_verification_reason,
    check_company_holiday,
    list_upcoming_company_holidays,

    # 2. Attendance & Regularization Policy & Actions
    check_attendance_status,
    get_late_marks,
    submit_attendance_regularization,
    get_regularization_status,

    # 3. Work From Home / Hybrid Work Policy & Actions
    check_wfh_eligibility,
    get_wfh_quota_balance,
    apply_wfh_request,
    get_wfh_request_status,

    # 4. Payroll & Salary Policy & Inquiries
    get_salary_credit_date,
    get_basic_salary_info,
    get_salary_deductions_info,
    check_payslip_status,

    # 5. Code of Conduct & Grievance Policy & Escalations
    get_hr_policy,
    get_code_of_conduct_policy,
    report_confidential_grievance,

    # 6. HR Ticket Creation (Human Escalation across 6 categories)
    raise_hr_ticket,
    check_my_hr_tickets,

    # 7. Mandatory Policy Acknowledgements & Scheduled Reminders
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
]

