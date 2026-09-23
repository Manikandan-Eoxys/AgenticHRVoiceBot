"""
services/wfh_service.py

Business logic for Work From Home / Hybrid Work:
1. Check WFH eligibility (confirmed employee, completed probation)
2. Check monthly WFH quota balance (up to 8 days per month / 2 days per week)
3. Submit WFH application (pending manager approval)
4. Query WFH request status
"""

import logging
from datetime import date, datetime
from database.db import get_db_connection

logger = logging.getLogger("wfh-service")

MONTHLY_WFH_CAP = 8
WEEKLY_WFH_CAP = 2


class WFHService:

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

    def check_wfh_eligibility(self, employee_id: str) -> dict:
        """Checks if employee is eligible for hybrid work/WFH."""
        rows = self._query(
            "SELECT full_name, join_date, status FROM employees WHERE employee_id = %s",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "eligible": False, "message": "Employee not found."}

        emp = rows[0]
        # Full-time confirmed active employees are eligible
        if emp["status"] != "active":
            return {
                "success": True,
                "eligible": False,
                "message": f"Hybrid work is only available for active full-time staff. Current status: {emp['status']}."
            }

        return {
            "success": True,
            "eligible": True,
            "employee_name": emp["full_name"],
            "message": f"{emp['full_name']} is eligible for hybrid work (up to 2 days per week or 8 days per month with manager approval)."
        }

    def get_wfh_quota_balance(self, employee_id: str, month: int = None, year: int = None) -> dict:
        """Calculates WFH days availed this month vs remaining allowance."""
        today = date.today()
        m = month or today.month
        y = year or today.year

        rows = self._query(
            """SELECT SUM(days_count) as used_days
               FROM wfh_requests
               WHERE employee_id = %s 
                 AND status IN ('approved', 'pending')
                 AND MONTH(start_date) = %s 
                 AND YEAR(start_date) = %s""",
            (str(employee_id), m, y)
        )
        used = int(rows[0]["used_days"] or 0) if rows else 0
        remaining = max(0, MONTHLY_WFH_CAP - used)
        month_name = datetime(y, m, 1).strftime("%B %Y")

        return {
            "success": True,
            "month": month_name,
            "monthly_cap": MONTHLY_WFH_CAP,
            "weekly_cap": WEEKLY_WFH_CAP,
            "used_days": used,
            "remaining_days": remaining,
            "message": f"For {month_name}, you have used {used} out of {MONTHLY_WFH_CAP} allowed WFH days. You have {remaining} WFH day(s) remaining."
        }

    def submit_wfh_request(self, employee_id: str, start_date: str, end_date: str = None, reason: str = "") -> dict:
        """Submits a WFH request pending manager approval."""
        today = date.today()
        end_date = end_date or start_date

        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "message": "Dates must be formatted as YYYY-MM-DD."}

        if ed < sd:
            return {"success": False, "message": f"End date {end_date} cannot be earlier than start date {start_date}."}

        days_count = (ed - sd).days + 1
        quota = self.get_wfh_quota_balance(employee_id, sd.month, sd.year)
        if days_count > quota["remaining_days"]:
            return {
                "success": False,
                "message": f"Cannot submit WFH for {days_count} day(s). You only have {quota['remaining_days']} WFH day(s) remaining for {quota['month']}."
            }

        wfh_id = self._execute(
            """INSERT INTO wfh_requests (employee_id, start_date, end_date, days_count, reason, status)
               VALUES (%s, %s, %s, %s, %s, 'pending')""",
            (str(employee_id), sd.isoformat(), ed.isoformat(), days_count, reason or "Remote work request")
        )

        return {
            "success": True,
            "wfh_id": wfh_id,
            "start_date": str(sd),
            "end_date": str(ed),
            "days_count": days_count,
            "status": "Pending",
            "message": f"Your WFH request for {days_count} day(s) from {sd} to {ed} has been submitted and is pending manager approval."
        }

    def get_wfh_status(self, employee_id: str) -> dict:
        """Queries status of latest WFH request."""
        rows = self._query(
            """SELECT wfh_id, start_date, end_date, days_count, status, manager_remarks
               FROM wfh_requests
               WHERE employee_id = %s
               ORDER BY wfh_id DESC LIMIT 1""",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "message": "No Work From Home requests on file."}

        r = rows[0]
        return {
            "success": True,
            "wfh_id": r["wfh_id"],
            "start_date": str(r["start_date"]),
            "end_date": str(r["end_date"]),
            "days_count": r["days_count"],
            "status": r["status"],
            "manager_remarks": r["manager_remarks"],
            "message": f"Your WFH request #{r['wfh_id']} for {r['start_date']} to {r['end_date']} has status: {r['status']}."
        }
