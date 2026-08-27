"""
services/attendance_service.py

Core Attendance Management Service
Handles:
  1. Absence request submission (sickness, holiday, emergency, funeral,
     compassionate, casual, earned)
  2. Team coverage calculation per Field Manager
  3. Coverage threshold enforcement (configurable, default 8%)
  4. Team roster retrieval
  5. Integration hooks for IFS Cloud and SQL Server sync
"""

from datetime import datetime, date
from config import Config
from database.db import get_db_connection
from modules.date_utils import normalize_date

# Read threshold from Config — set COVERAGE_THRESHOLD=0.08 in .env (8% minimum)
COVERAGE_THRESHOLD = float(getattr(Config, "COVERAGE_THRESHOLD", 0.08))


class AttendanceService:

    def _connect(self):
        return get_db_connection()

    # ----------------------------------------------------------
    # 1. Submit Absence Request
    # ----------------------------------------------------------
    def submit_absence_request(
        self,
        employee_id: int,
        absence_type: str,
        from_date: str,
        to_date: str,
        reason: str = "",
        submitted_at: str = None,
    ) -> dict:
        """
        Submit an absence request.
        absence_type: 'Sickness' | 'Holiday' | 'Emergency' | 'Funeral' | 'Casual' | 'Earned'

        Returns:
          success, request_id, coverage_ok, exception_flag, exception_type, area_manager_id
        """
        if submitted_at is None:
            submitted_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        # Validate employee exists
        cursor.execute(
            "SELECT employee_id, full_name AS name FROM employees WHERE employee_id=%s",
            (str(employee_id),),
        )
        emp = cursor.fetchone()
        if emp is None:
            cursor.close()
            conn.close()
            return {"success": False, "message": "Employee not found."}

        field_manager_id = emp.get("field_manager_id")
        area_manager_id = emp.get("area_manager_id")

        # Map absence_type to leave_code in MySQL
        code_map = {
            "sickness": "SL",
            "sick": "SL",
            "holiday": "CL",
            "emergency": "CL",
            "funeral": "BEREAVEMENT",
            "compassionate": "BEREAVEMENT",
            "casual": "CL",
            "earned": "COMP_OFF",
        }
        leave_code = code_map.get(absence_type.lower(), "CL")

        # Calculate days
        try:
            from_date = normalize_date(from_date)
            to_date   = normalize_date(to_date)
            start_dt  = datetime.strptime(from_date, "%Y-%m-%d")
            end_dt    = datetime.strptime(to_date,   "%Y-%m-%d")
        except ValueError:
            cursor.close()
            conn.close()
            return {"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}

        days = (end_dt - start_dt).days + 1
        if days <= 0:
            cursor.close()
            conn.close()
            return {"success": False, "message": "End date must be on or after start date."}

        # Check leave balance in MySQL leave_balances table
        cursor.execute("""
            SELECT available_days, used_days, entitled_days
            FROM leave_balances
            WHERE employee_id = %s AND leave_code = %s
        """, (str(employee_id), leave_code))
        bal_row = cursor.fetchone()

        if bal_row is None:
            # Fallback to CL
            leave_code = "CL"
            cursor.execute("""
                SELECT available_days, used_days, entitled_days
                FROM leave_balances
                WHERE employee_id = %s AND leave_code = 'CL'
            """, (str(employee_id),))
            bal_row = cursor.fetchone()

        avail = bal_row["available_days"] if bal_row else 99
        if avail is not None and avail < days:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": f"Insufficient {absence_type} leave balance. Available: {avail} day(s), Requested: {days} day(s).",
            }

        # Coverage check (if Field Manager is assigned)
        coverage_ok = True
        coverage_pct = None
        if field_manager_id:
            coverage_result = self.check_coverage_threshold(
                field_manager_id, from_date, prospective_absent_employee_id=employee_id
            )
            coverage_ok  = coverage_result["above_threshold"]
            coverage_pct = coverage_result["coverage_pct"]

        # Detect exception
        from services.exception_service import ExceptionService
        exc_service = ExceptionService()
        exc_detection = exc_service.detect_exception(
            absence_type=absence_type,
            from_date=from_date,
            submitted_at=submitted_at,
            coverage_ok=coverage_ok,
        )
        exception_flag    = exc_detection["is_exception"]
        exception_type    = exc_detection.get("exception_type", "None")

        # Determine initial status
        initial_status = "submitted" if not exception_flag else "pending_exception"

        # Insert into MySQL leave_requests table
        cursor.execute("""
            INSERT INTO leave_requests
            (employee_id, leave_code, start_date, end_date, days_requested, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (str(employee_id), leave_code, from_date, to_date, days, initial_status))
        request_id = cursor.lastrowid

        # Update used_days in leave_balances
        cursor.execute("""
            UPDATE leave_balances
            SET used_days = used_days + %s
            WHERE employee_id = %s AND leave_code = %s
        """, (days, str(employee_id), leave_code))

        # If exception, create exception_request record
        exception_id = None
        if exception_flag and area_manager_id:
            cursor.execute("""
                INSERT INTO exception_requests
                (leave_request_id, employee_id, field_manager_id, area_manager_id,
                 exception_reason, exception_type, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'Pending')
            """, (
                request_id, str(employee_id), str(field_manager_id), str(area_manager_id),
                exc_detection.get("reason", ""), exception_type,
            ))
            exception_id = cursor.lastrowid

        conn.commit()
        cursor.close()
        conn.close()

        result = {
            "success":        True,
            "request_id":     request_id,
            "employee_id":    employee_id,
            "from_date":      from_date,
            "to_date":        to_date,
            "days":           days,
            "absence_type":   absence_type,
            "leave_type":     leave_code,
            "status":         initial_status,
            "coverage_ok":    coverage_ok,
            "coverage_pct":   coverage_pct,
            "exception_flag": exception_flag,
            "exception_type": exception_type,
            "exception_id":   exception_id,
            "area_manager_id":area_manager_id,
            "am_status":      "Pending" if exception_flag else "NotRequired",
            "message":        self._build_submission_message(
                exception_flag, exception_type, coverage_ok, coverage_pct, days, absence_type
            ),
        }
        return result

    def _build_submission_message(
        self, exception_flag, exception_type, coverage_ok, coverage_pct, days, absence_type
    ) -> str:
        pct_str = f"{round(coverage_pct * 100, 1)}%" if coverage_pct is not None else "N/A"
        if exception_flag:
            return (
                f"Your {absence_type.lower()} request for {days} day(s) has been submitted "
                f"and requires Area Manager approval ({exception_type}). "
                f"Team coverage if approved: {pct_str}. You will be notified of the decision."
            )
        return (
            f"Your {absence_type.lower()} request for {days} day(s) has been submitted "
            f"and is pending approval. Team coverage: {pct_str}."
        )

    # ----------------------------------------------------------
    # 2. Get Team Coverage for a Field Manager on a Date
    # ----------------------------------------------------------
    def get_team_coverage(self, field_manager_id: int, coverage_date: str) -> dict:
        """
        Returns coverage info for a Field Manager's team on a given date.
        """
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        # Total engineers in this FM's team
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM employees WHERE field_manager_id=%s",
            (str(field_manager_id),),
        )
        total_row = cursor.fetchone()
        total = total_row["cnt"] if total_row else 0

        if total == 0:
            cursor.close()
            conn.close()
            return {
                "success":         False,
                "message":         "No engineers found under this Field Manager.",
                "field_manager_id":field_manager_id,
                "coverage_date":   coverage_date,
            }

        # Absent engineers: approved OR pending leave on that date
        cursor.execute("""
        SELECT COUNT(DISTINCT lr.employee_id) as absent
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        WHERE e.field_manager_id = %s
          AND lr.start_date <= %s
          AND lr.end_date   >= %s
          AND lr.status IN ('submitted', 'approved', 'pending_exception')
        """, (str(field_manager_id), coverage_date, coverage_date))
        absent_row = cursor.fetchone()
        absent = absent_row["absent"] if absent_row else 0

        # Get FM name
        cursor.execute("SELECT full_name AS name FROM employees WHERE employee_id=%s", (str(field_manager_id),))
        fm_row = cursor.fetchone()
        fm_name = fm_row["name"] if fm_row else "Unknown"

        # Get individual engineer details
        cursor.execute("""
        SELECT e.employee_id, e.full_name AS name,
               CASE WHEN lr.request_id IS NOT NULL THEN 'Absent' ELSE 'Available' END as availability,
               lr.leave_code AS absence_type, lr.status
        FROM employees e
        LEFT JOIN leave_requests lr
          ON  lr.employee_id = e.employee_id
          AND lr.start_date  <= %s
          AND lr.end_date    >= %s
          AND lr.status IN ('submitted', 'approved', 'pending_exception')
        WHERE e.field_manager_id = %s
        ORDER BY e.full_name
        """, (coverage_date, coverage_date, str(field_manager_id)))
        engineers = cursor.fetchall()

        cursor.close()
        conn.close()

        available = total - absent
        coverage_pct = round(available / total, 4)
        below = coverage_pct < COVERAGE_THRESHOLD

        return {
            "success":            True,
            "field_manager_id":   field_manager_id,
            "field_manager_name": fm_name,
            "coverage_date":      coverage_date,
            "total_engineers":    total,
            "absent_count":       absent,
            "available_count":    available,
            "coverage_pct":       coverage_pct,
            "coverage_pct_str":   f"{round(coverage_pct * 100, 1)}%",
            "below_threshold":    below,
            "threshold":          COVERAGE_THRESHOLD,
            "threshold_str":      f"{round(COVERAGE_THRESHOLD * 100)}%",
            "engineers":          engineers,
        }

    # ----------------------------------------------------------
    # 3. Check Coverage Threshold (prospective — before approving)
    # ----------------------------------------------------------
    def check_coverage_threshold(
        self,
        field_manager_id: int,
        coverage_date: str,
        prospective_absent_employee_id: int = None,
    ) -> dict:
        """
        Checks if approving a new absence would breach the threshold.
        prospective_absent_employee_id: the employee about to take leave.
        """
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM employees WHERE field_manager_id=%s",
            (str(field_manager_id),),
        )
        total_row = cursor.fetchone()
        total = total_row["cnt"] if total_row else 0

        if total == 0:
            cursor.close()
            conn.close()
            return {
                "above_threshold":          True,
                "coverage_pct":             1.0,
                "coverage_pct_if_approved": 1.0,
                "total_engineers":          0,
                "current_absent":           0,
                "message":                  "No engineers in team.",
            }

        # Current absences (excluding the prospective one)
        p_id = str(prospective_absent_employee_id) if prospective_absent_employee_id else None
        cursor.execute("""
        SELECT COUNT(DISTINCT lr.employee_id) as absent
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        WHERE e.field_manager_id = %s
          AND lr.start_date <= %s
          AND lr.end_date   >= %s
          AND lr.status IN ('submitted', 'approved', 'pending_exception')
          AND (%s IS NULL OR lr.employee_id != %s)
        """, (str(field_manager_id), coverage_date, coverage_date, p_id, p_id))
        absent_row = cursor.fetchone()
        current_absent = absent_row["absent"] if absent_row else 0
        cursor.close()
        conn.close()

        current_pct        = round((total - current_absent) / total, 4)
        new_absent         = current_absent + (1 if prospective_absent_employee_id else 0)
        pct_if_approved    = round((total - new_absent) / total, 4)
        above_threshold    = pct_if_approved >= COVERAGE_THRESHOLD

        return {
            "above_threshold":          above_threshold,
            "coverage_pct":             current_pct,
            "coverage_pct_if_approved": pct_if_approved,
            "total_engineers":          total,
            "current_absent":           current_absent,
            "threshold":                COVERAGE_THRESHOLD,
            "message": (
                "Coverage would remain above threshold."
                if above_threshold
                else f"Coverage would drop to {round(pct_if_approved*100,1)}%, below the {round(COVERAGE_THRESHOLD*100)}% threshold."
            ),
        }

    # ----------------------------------------------------------
    # 4. Get Team Roster for a Date
    # ----------------------------------------------------------
    def get_team_roster(self, field_manager_id: int, roster_date: str) -> dict:
        """
        Returns full roster (scheduled, absent, on leave) for a FM's team on a date.
        """
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM employees WHERE employee_id=?", (field_manager_id,))
        fm_row = cursor.fetchone()
        fm_name = fm_row["name"] if fm_row else "Unknown"

        cursor.execute("""
        SELECT
            e.employee_id,
            e.name,
            e.designation,
            rs.status     AS shift_status,
            rs.shift_start,
            rs.shift_end,
            lr.absence_type,
            lr.status     AS leave_status,
            lr.reason
        FROM employees e
        LEFT JOIN roster_schedules rs
            ON  rs.employee_id    = e.employee_id
            AND rs.schedule_date  = ?
        LEFT JOIN leave_requests lr
            ON  lr.employee_id   = e.employee_id
            AND lr.from_date    <= ?
            AND lr.to_date      >= ?
            AND lr.status IN ('Approved', 'Pending')
        WHERE e.field_manager_id = ?
          AND e.role = 'Engineer'
        ORDER BY e.name
        """, (roster_date, roster_date, roster_date, field_manager_id))

        roster = []
        for row in cursor.fetchall():
            r = dict(row)
            r["on_leave"] = r["leave_status"] is not None
            roster.append(r)

        available = sum(1 for r in roster if not r["on_leave"])
        total     = len(roster)
        conn.close()

        return {
            "success":            True,
            "field_manager_id":   field_manager_id,
            "field_manager_name": fm_name,
            "roster_date":        roster_date,
            "total_engineers":    total,
            "available_count":    available,
            "absent_count":       total - available,
            "coverage_pct_str":   f"{round(available/total*100,1)}%" if total else "N/A",
            "roster":             roster,
        }

    # ----------------------------------------------------------
    # 5. Get All Pending Absence Requests
    # ----------------------------------------------------------
    def get_pending_requests(self, field_manager_id: int = None) -> dict:
        """
        Returns pending absence requests — optionally filtered to a FM's team.
        """
        conn = self._connect()
        cursor = conn.cursor()

        if field_manager_id:
            cursor.execute("""
            SELECT lr.*, e.name AS employee_name, e.field_manager_id
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.employee_id
            WHERE lr.status = 'Pending'
              AND e.field_manager_id = ?
            ORDER BY lr.submitted_at DESC
            """, (field_manager_id,))
        else:
            cursor.execute("""
            SELECT lr.*, e.name AS employee_name, e.field_manager_id
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.employee_id
            WHERE lr.status = 'Pending'
            ORDER BY lr.submitted_at DESC
            """)

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"success": True, "count": len(rows), "requests": rows}

    # ----------------------------------------------------------
    # Internal: upsert team_coverage snapshot
    # ----------------------------------------------------------
    def _upsert_team_coverage(
        self,
        cursor,
        field_manager_id: int,
        coverage_date: str,
        total_engineers: int,
        absent_count: int,
        coverage_pct: float,
        below_threshold: bool,
    ):
        cursor.execute("""
        INSERT INTO team_coverage
            (field_manager_id, coverage_date, total_engineers, absent_count, coverage_pct, below_threshold)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(field_manager_id, coverage_date)
        DO UPDATE SET
            total_engineers = excluded.total_engineers,
            absent_count    = excluded.absent_count,
            coverage_pct    = excluded.coverage_pct,
            below_threshold = excluded.below_threshold,
            recorded_at     = datetime('now')
        """, (
            field_manager_id, coverage_date, total_engineers,
            absent_count, round(coverage_pct, 4), 1 if below_threshold else 0,
        ))
