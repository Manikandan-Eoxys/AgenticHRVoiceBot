"""
services/attendance_service.py

Business logic for Attendance & Regularization:
1. Query daily attendance status (e.g. why absent yesterday, punch timings)
2. Query monthly late marks count and policy implications (3 late marks = half-day deduction)
3. Submit attendance regularization request (missing punch in/out, late mark dispute)
4. Query regularization request status
"""

import logging
from datetime import date, datetime
from database.db import get_db_connection

logger = logging.getLogger("attendance-service")


class AttendanceService:

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

    def get_attendance_status(self, employee_id: str, date_str: str = "yesterday") -> dict:
        """Looks up attendance status for a specific date or 'yesterday'."""
        today = date.today()
        d_str = str(date_str or "").strip().lower()

        if d_str in ("yesterday", ""):
            target_date = today.fromordinal(today.toordinal() - 1)
        elif d_str == "today":
            target_date = today
        else:
            try:
                target_date = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                return {
                    "success": False,
                    "message": "Invalid date format. Please specify 'yesterday', 'today', or a date as YYYY-MM-DD."
                }

        rows = self._query(
            """SELECT record_date, check_in, check_out, total_hours, status, late_minutes, remarks
               FROM attendance_records
               WHERE employee_id = %s AND record_date = %s""",
            (str(employee_id), target_date.isoformat())
        )

        if not rows:
            return {
                "success": True,
                "record_date": str(target_date),
                "status": "No Record",
                "message": f"No biometric punch record found for {target_date.strftime('%A, %B %-d, %Y')}. If you were in the office, you can submit an attendance regularization request."
            }

        r = rows[0]
        st = r["status"]
        remarks = r["remarks"] or ""
        in_time = str(r["check_in"]) if r["check_in"] else "None"
        out_time = str(r["check_out"]) if r["check_out"] else "None"

        if st == "Absent":
            msg = f"Your attendance for {target_date.strftime('%A, %B %-d')} is showing as Absent. System note: {remarks or 'No biometric punch detected'}. I can submit an attendance regularization request for you if you worked on that day."
        elif st == "Late":
            msg = f"On {target_date.strftime('%A, %B %-d')}, you were marked Late. Your check-in was at {in_time}, which is {r['late_minutes']} minutes past the 9:15 AM grace time."
        elif st == "Present":
            msg = f"On {target_date.strftime('%A, %B %-d')}, your status is Present with check-in at {in_time} and check-out at {out_time} (total {r['total_hours']} hours)."
        elif st == "On_Leave":
            msg = f"On {target_date.strftime('%A, %B %-d')}, you were marked On Leave ({remarks})."
        else:
            msg = f"On {target_date.strftime('%A, %B %-d')}, your status is {st}."

        return {
            "success": True,
            "record_date": str(target_date),
            "status": st,
            "check_in": in_time,
            "check_out": out_time,
            "total_hours": float(r["total_hours"]) if r["total_hours"] else None,
            "late_minutes": r["late_minutes"],
            "remarks": remarks,
            "message": msg
        }

    def get_late_marks_count(self, employee_id: str, month: int = None, year: int = None) -> dict:
        """Returns the number of late marks for the given or current month."""
        today = date.today()
        m = month or today.month
        y = year or today.year

        rows = self._query(
            """SELECT record_date, check_in, late_minutes
               FROM attendance_records
               WHERE employee_id = %s 
                 AND status = 'Late'
                 AND MONTH(record_date) = %s 
                 AND YEAR(record_date) = %s
               ORDER BY record_date ASC""",
            (str(employee_id), m, y)
        )
        count = len(rows)
        month_name = datetime(y, m, 1).strftime("%B %Y")

        if count == 0:
            msg = f"You have 0 late marks for {month_name}. Great job being on time!"
        elif count < 3:
            msg = f"You have {count} late mark(s) in {month_name}. Remember that company policy applies a half-day deduction upon reaching 3 late marks in a month."
        elif count == 3:
            msg = f"You have 3 late marks in {month_name}. Under the attendance policy, 3 late marks result in a half-day salary or leave deduction."
        else:
            msg = f"You have {count} late marks in {month_name}, which exceeds the 3-late-mark threshold."

        return {
            "success": True,
            "month": month_name,
            "late_marks_count": count,
            "records": rows,
            "message": msg
        }

    def submit_regularization(
        self,
        employee_id: str,
        attendance_date: str,
        request_type: str,
        actual_time: str = None,
        reason: str = ""
    ) -> dict:
        """Submits an attendance regularization request."""
        # Normalize request_type
        rt = request_type.lower().replace(" ", "_")
        if "in" in rt or "entry" in rt:
            req_type = "missing_punch_in"
        elif "out" in rt or "exit" in rt:
            req_type = "missing_punch_out"
        elif "late" in rt:
            req_type = "late_regularization"
        else:
            req_type = "status_correction"

        today = date.today()
        if attendance_date.lower() == "today":
            att_date = today
        elif attendance_date.lower() == "yesterday":
            att_date = today.fromordinal(today.toordinal() - 1)
        else:
            try:
                att_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
            except ValueError:
                return {"success": False, "message": "Attendance date must be in YYYY-MM-DD format, or 'today'/'yesterday'."}

        reg_id = self._execute(
            """INSERT INTO attendance_regularizations (employee_id, attendance_date, request_type, actual_time, reason, status)
               VALUES (%s, %s, %s, %s, %s, 'pending')""",
            (str(employee_id), att_date.isoformat(), req_type, actual_time or "09:00:00", reason or "Biometric punch missed")
        )

        friendly_type = req_type.replace("_", " ").title()
        return {
            "success": True,
            "regularization_id": reg_id,
            "attendance_date": str(att_date),
            "request_type": friendly_type,
            "status": "Pending",
            "message": f"Your attendance regularization request for {att_date.strftime('%B %-d')} ({friendly_type}) has been submitted successfully and is pending manager approval."
        }

    def get_regularization_status(self, employee_id: str) -> dict:
        """Checks latest regularization request status."""
        rows = self._query(
            """SELECT regularization_id, attendance_date, request_type, status, manager_remarks
               FROM attendance_regularizations
               WHERE employee_id = %s
               ORDER BY regularization_id DESC LIMIT 1""",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "message": "No attendance regularization requests found."}

        r = rows[0]
        friendly_type = r["request_type"].replace("_", " ").title()
        return {
            "success": True,
            "regularization_id": r["regularization_id"],
            "attendance_date": str(r["attendance_date"]),
            "request_type": friendly_type,
            "status": r["status"],
            "message": f"Your regularization request #{r['regularization_id']} for {r['attendance_date']} ({friendly_type}) has status: {r['status']}."
        }
