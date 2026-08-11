"""
services/exception_service.py

Exception Detection and Approval Workflow Service
Handles:
  1. Detecting whether an absence qualifies as an exception
     (same-day sickness, funeral, compassionate, emergency, coverage breach,
      Monday absence submitted on the preceding Friday)
  2. Routing exception requests to Area Managers
  3. Area Manager approve/reject decisions
  4. Notification triggers after decisions
"""

import sqlite3
from datetime import datetime, timedelta
from config import Config

# Coverage threshold percentage string for messages
_THRESHOLD_PCT = round(float(getattr(Config, "COVERAGE_THRESHOLD", 0.08)) * 100, 1)


# --------------------------------------------------------
# Exception type constants
# --------------------------------------------------------
EXC_MONDAY_FRIDAY       = "MondayAbsenceFridaySubmission"
EXC_LAST_MINUTE         = "LastMinuteEmergency"
EXC_FUNERAL             = "FuneralLeave"
EXC_EMERGENCY           = "EmergencyLeave"
EXC_COVERAGE_THRESHOLD  = "CoverageThresholdBreach"
EXC_SAME_DAY_SICKNESS   = "SameDaySickness"      # NEW: sickness submitted on the day itself
EXC_COMPASSIONATE       = "CompassionateLeave"   # NEW: compassionate/exceptional circumstances

# Last-minute threshold: less than 2 hours notice
LAST_MINUTE_HOURS = 2


class ExceptionService:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    # ----------------------------------------------------------
    # 1. Detect Exception
    # ----------------------------------------------------------
    def detect_exception(
        self,
        absence_type: str,
        from_date: str,
        submitted_at: str = None,
        coverage_ok: bool = True,
    ) -> dict:
        """
        Determines if the absence request qualifies as an exception.

        Returns:
          is_exception (bool), exception_type (str), reason (str)
        """
        if submitted_at is None:
            submitted_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        exceptions_found = []

        try:
            from_dt      = datetime.strptime(from_date,    "%Y-%m-%d")
            submitted_dt = datetime.strptime(submitted_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Fallback: no exception detected on parse failure
            return {"is_exception": False, "exception_type": "None", "reason": ""}

        # ── Rule 1: Monday absence submitted on the preceding Friday ──
        if from_dt.weekday() == 0:  # Monday
            if submitted_dt.weekday() == 4:  # Friday
                exceptions_found.append({
                    "type":   EXC_MONDAY_FRIDAY,
                    "reason": (
                        f"Monday absence ({from_date}) was submitted on Friday "
                        f"({submitted_dt.strftime('%Y-%m-%d')}), which requires Area Manager approval."
                    ),
                })

        # ── Rule 2: Funeral leave ──
        if absence_type.lower() in ("funeral", "bereavement"):
            exceptions_found.append({
                "type":   EXC_FUNERAL,
                "reason": "Funeral/bereavement leave always requires Area Manager approval.",
            })

        # ── Rule 2b: Compassionate / exceptional circumstances leave ──
        if absence_type.lower() in ("compassionate",):
            exceptions_found.append({
                "type":   EXC_COMPASSIONATE,
                "reason": "Compassionate/exceptional leave always requires Area Manager approval.",
            })

        # ── Rule 3: Emergency with very short notice ──
        if absence_type.lower() == "emergency":
            notice_hours = (from_dt - submitted_dt).total_seconds() / 3600
            if notice_hours < LAST_MINUTE_HOURS:
                exceptions_found.append({
                    "type":   EXC_LAST_MINUTE,
                    "reason": (
                        f"Emergency leave submitted with less than {LAST_MINUTE_HOURS} hours notice "
                        f"({abs(round(notice_hours, 1))} hours). Requires Area Manager approval."
                    ),
                })
            else:
                exceptions_found.append({
                    "type":   EXC_EMERGENCY,
                    "reason": "Emergency leave requires Area Manager approval.",
                })

        # ── Rule 3b: Same-day sickness ──
        # Sickness submitted on the same calendar day as the absence start
        # is treated as an exception — the business receives ~20 such calls/day.
        if absence_type.lower() == "sickness":
            if from_dt.date() == submitted_dt.date():
                exceptions_found.append({
                    "type":   EXC_SAME_DAY_SICKNESS,
                    "reason": (
                        f"Sickness absence submitted on the same day ({from_date}). "
                        "Same-day sickness requires Area Manager approval."
                    ),
                })

        # ── Rule 4: Coverage would drop below threshold ──
        if not coverage_ok:
            exceptions_found.append({
                "type":   EXC_COVERAGE_THRESHOLD,
                "reason": (
                    f"Approving this absence would reduce team coverage below the "
                    f"{_THRESHOLD_PCT}% minimum threshold. "
                    "Requires Area Manager approval."
                ),
            })

        if exceptions_found:
            # Use the first exception type as primary; concatenate reasons
            primary = exceptions_found[0]
            all_reasons = " | ".join(e["reason"] for e in exceptions_found)
            return {
                "is_exception":   True,
                "exception_type": primary["type"],
                "all_types":      [e["type"] for e in exceptions_found],
                "reason":         all_reasons,
            }

        return {
            "is_exception":   False,
            "exception_type": "None",
            "all_types":      [],
            "reason":         "",
        }

    # ----------------------------------------------------------
    # 2. Get a Single Exception Request
    # ----------------------------------------------------------
    def get_exception(self, exception_id: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT er.*,
               e.name    AS employee_name,
               e.email   AS employee_email,
               fm.name   AS fm_name,
               am.name   AS am_name,
               am.email  AS am_email
        FROM exception_requests er
        JOIN employees e  ON er.employee_id      = e.employee_id
        LEFT JOIN employees fm ON er.field_manager_id = fm.employee_id
        LEFT JOIN employees am ON er.area_manager_id  = am.employee_id
        WHERE er.exception_id = ?
        """, (exception_id,))
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return {"success": False, "message": "Exception request not found."}
        return {"success": True, "exception": dict(row)}

    # ----------------------------------------------------------
    # 3. Get Pending Exceptions for an Area Manager
    # ----------------------------------------------------------
    def get_pending_exceptions(self, area_manager_id: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT er.*,
               e.name   AS employee_name,
               e.email  AS employee_email,
               fm.name  AS fm_name,
               lr.from_date,
               lr.to_date,
               lr.absence_type,
               lr.reason AS absence_reason
        FROM exception_requests er
        JOIN employees e  ON er.employee_id      = e.employee_id
        LEFT JOIN employees fm ON er.field_manager_id = fm.employee_id
        LEFT JOIN leave_requests lr ON er.leave_request_id = lr.request_id
        WHERE er.area_manager_id = ?
          AND er.status = 'Pending'
        ORDER BY er.created_at DESC
        """, (area_manager_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {
            "success": True,
            "count":   len(rows),
            "exceptions": rows,
        }

    # ----------------------------------------------------------
    # 4. Get All Exceptions for an Area Manager (any status)
    # ----------------------------------------------------------
    def get_all_exceptions(self, area_manager_id: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT er.*,
               e.name  AS employee_name,
               fm.name AS fm_name,
               lr.from_date, lr.to_date, lr.absence_type
        FROM exception_requests er
        JOIN employees e  ON er.employee_id      = e.employee_id
        LEFT JOIN employees fm ON er.field_manager_id = fm.employee_id
        LEFT JOIN leave_requests lr ON er.leave_request_id = lr.request_id
        WHERE er.area_manager_id = ?
        ORDER BY er.created_at DESC
        """, (area_manager_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"success": True, "count": len(rows), "exceptions": rows}

    # ----------------------------------------------------------
    # 5. Area Manager Decision: Approve or Reject
    # ----------------------------------------------------------
    def process_exception_decision(
        self,
        exception_id: int,
        area_manager_id: int,
        decision: str,          # 'Approved' | 'Rejected'
        notes: str = "",
    ) -> dict:
        """
        Records the Area Manager's decision on an exception request.
        Updates both the exception_requests and leave_requests tables.
        Triggers sync if approved.
        """
        decision = decision.strip().capitalize()
        if decision not in ("Approved", "Rejected"):
            return {"success": False, "message": "Decision must be 'Approved' or 'Rejected'."}

        conn = self._connect()
        cursor = conn.cursor()

        # Verify exception exists and belongs to this AM
        cursor.execute("""
        SELECT er.*, lr.employee_id, lr.from_date, lr.to_date, lr.absence_type,
               e.name AS employee_name, e.field_manager_id
        FROM exception_requests er
        JOIN leave_requests lr ON er.leave_request_id = lr.request_id
        JOIN employees e ON er.employee_id = e.employee_id
        WHERE er.exception_id = ?
          AND er.area_manager_id = ?
        """, (exception_id, area_manager_id))
        exc = cursor.fetchone()

        if exc is None:
            conn.close()
            return {
                "success": False,
                "message": "Exception request not found or you are not the designated approver.",
            }

        if exc["status"] != "Pending":
            conn.close()
            return {
                "success": False,
                "message": f"This exception has already been {exc['status'].lower()}.",
            }

        decided_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        # Update exception_requests
        cursor.execute("""
        UPDATE exception_requests
        SET status            = ?,
            am_decision       = ?,
            am_notes          = ?,
            decided_at        = ?,
            notified_employee = 0
        WHERE exception_id = ?
        """, (decision, decision, notes, decided_at, exception_id))

        # Update leave_requests
        new_lr_status = "Approved" if decision == "Approved" else "Rejected"
        cursor.execute("""
        UPDATE leave_requests
        SET status              = ?,
            area_manager_status = ?,
            updated_at          = datetime('now')
        WHERE request_id = ?
        """, (new_lr_status, decision, exc["leave_request_id"]))

        # If rejected, restore leave balance
        if decision == "Rejected":
            from_dt = datetime.strptime(exc["from_date"], "%Y-%m-%d")
            to_dt   = datetime.strptime(exc["to_date"],   "%Y-%m-%d")
            days    = (to_dt - from_dt).days + 1
            absence_type = exc["absence_type"]
            col = "sick" if absence_type == "Sickness" else ("earned" if absence_type == "Earned" else "casual")
            cursor.execute(
                f"UPDATE leave_balance SET {col} = {col} + ? WHERE employee_id=?",
                (days, exc["employee_id"]),
            )

        conn.commit()
        conn.close()

        return {
            "success":         True,
            "exception_id":    exception_id,
            "leave_request_id":exc["leave_request_id"],
            "employee_id":     exc["employee_id"],
            "employee_name":   exc["employee_name"],
            "decision":        decision,
            "notes":           notes,
            "decided_at":      decided_at,
            "message": (
                f"Absence request for {exc['employee_name']} ({exc['from_date']} to {exc['to_date']}) "
                f"has been {decision.lower()}."
            ),
        }

    # ----------------------------------------------------------
    # 6. Mark Employee as Notified
    # ----------------------------------------------------------
    def mark_employee_notified(self, exception_id: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE exception_requests SET notified_employee=1 WHERE exception_id=?",
            (exception_id,),
        )
        conn.commit()
        conn.close()
        return {"success": True}
