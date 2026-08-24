"""
leave_tools.py

Leave Management Tool Functions using MySQL.

Handles:
1. Check Leave Balance
2. Apply Leave
3. Cancel Leave
4. List Leave Requests
5. Approve Leave
"""

from datetime import datetime
from database.db import get_db_connection


class LeaveTools:

    def _connect(self):
        return get_db_connection()

    # --------------------------------------------------
    # Check Leave Balance
    # --------------------------------------------------
    def check_leave_balance(self, employee_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT leave_code, entitled_days, used_days, available_days
            FROM leave_balances
            WHERE employee_id = %s
        """, (str(employee_id),))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            return {
                "success": False,
                "message": "Leave balance not found."
            }

        balances = {}
        for r in rows:
            code = r["leave_code"]
            avail = r["available_days"] if r["available_days"] is not None else 99
            balances[code] = avail

        casual = balances.get("CL", 0)
        sick = balances.get("SL", 0)
        earned = balances.get("COMP_OFF", 0)

        return {
            "success": True,
            "casual": casual,
            "sick": sick,
            "earned": earned,
            "balances": balances
        }

    # --------------------------------------------------
    # Apply Leave
    # --------------------------------------------------
    def apply_leave(
        self,
        employee_id,
        from_date,
        to_date,
        leave_type="Casual"
    ):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        code_map = {
            "casual": "CL",
            "sick": "SL",
            "earned": "COMP_OFF",
            "maternity": "MATERNITY",
            "paternity": "PATERNITY",
            "bereavement": "BEREAVEMENT",
            "short": "SHORT_LEAVE",
            "short leave": "SHORT_LEAVE",
            "lwp": "LWP",
        }
        leave_code = code_map.get(leave_type.lower(), "CL")

        cursor.execute("""
            SELECT used_days, entitled_days, available_days
            FROM leave_balances
            WHERE employee_id = %s AND leave_code = %s
        """, (str(employee_id), leave_code))

        row = cursor.fetchone()

        if row is None:
            # If specific leave_code row doesn't exist, default fallback to CL
            leave_code = "CL"
            cursor.execute("""
                SELECT used_days, entitled_days, available_days
                FROM leave_balances
                WHERE employee_id = %s AND leave_code = 'CL'
            """, (str(employee_id),))
            row = cursor.fetchone()

        if row is None:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": "Employee or leave balance not found."
            }

        try:
            start = datetime.strptime(from_date, "%Y-%m-%d")
            end = datetime.strptime(to_date, "%Y-%m-%d")
            days = (end - start).days + 1
        except Exception:
            days = 1

        if days <= 0:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": "Invalid leave dates."
            }

        avail = row["available_days"]
        if avail is not None and avail < days:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": f"Insufficient leave balance for {leave_type}. Available: {avail} day(s)."
            }

        cursor.execute("""
            INSERT INTO leave_requests
            (employee_id, leave_code, start_date, end_date, days_requested, status)
            VALUES (%s, %s, %s, %s, %s, 'submitted')
        """, (str(employee_id), leave_code, from_date, to_date, days))

        cursor.execute("""
            UPDATE leave_balances
            SET used_days = used_days + %s
            WHERE employee_id = %s AND leave_code = %s
        """, (days, str(employee_id), leave_code))

        conn.commit()
        request_id = cursor.lastrowid
        cursor.close()
        conn.close()

        return {
            "success": True,
            "request_id": request_id,
            "days": days,
            "status": "Pending",
            "message": f"{leave_type} leave request submitted successfully."
        }

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
            return {
                "success": False,
                "message": "Request not found."
            }

        employee_id = row["employee_id"]
        leave_code = row["leave_code"]
        days = row["days_requested"]

        cursor.execute("""
            UPDATE leave_balances
            SET used_days = GREATEST(0, used_days - %s)
            WHERE employee_id = %s AND leave_code = %s
        """, (days, str(employee_id), leave_code))

        cursor.execute("""
            UPDATE leave_requests
            SET status = 'rejected'
            WHERE request_id = %s
        """, (request_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Leave cancelled successfully."
        }

    # --------------------------------------------------
    # List Leave Requests
    # --------------------------------------------------
    def list_leave_requests(self, employee_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT request_id, start_date, end_date, leave_code, status
            FROM leave_requests
            WHERE employee_id = %s
            ORDER BY request_id DESC
        """, (str(employee_id),))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        requests = []
        for r in rows:
            requests.append({
                "request_id": r["request_id"],
                "from": str(r["start_date"]),
                "to": str(r["end_date"]),
                "leave_type": r["leave_code"],
                "status": r["status"]
            })

        return {
            "success": True,
            "requests": requests
        }

    # --------------------------------------------------
    # Approve Leave (Demo)
    # --------------------------------------------------
    def approve_leave(self, request_id):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE leave_requests
            SET status = 'approved'
            WHERE request_id = %s
        """, (request_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Leave Approved."
        }