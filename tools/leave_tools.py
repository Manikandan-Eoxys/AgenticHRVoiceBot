"""
leave_tools.py

Leave Management Tool Functions

This module is responsible for:

1. Check Leave Balance
2. Apply Leave
3. Cancel Leave
4. List Leave Requests
5. Update Leave Balance

Used by LiveKit/OpenAI Function Calling.
"""

import sqlite3
from datetime import datetime
from config import Config


class LeaveTools:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        return sqlite3.connect(self.db)

    # --------------------------------------------------
    # Check Leave Balance
    # --------------------------------------------------
    def check_leave_balance(self, employee_id):

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT
            casual,
            sick,
            earned
        FROM leave_balance
        WHERE employee_id=?
        """, (employee_id,))

        row = cursor.fetchone()

        conn.close()

        if row is None:

            return {
                "success": False,
                "message": "Leave balance not found."
            }

        return {

            "success": True,

            "casual": row[0],

            "sick": row[1],

            "earned": row[2]

        }

    # --------------------------------------------------
    # Apply Leave
    # --------------------------------------------------
    def apply_leave(
            self,
            employee_id,
            from_date,
            to_date,
            leave_type="Casual"):

        conn = self._connect()
        cursor = conn.cursor()

        # -------------------------------
        # Current Balance
        # -------------------------------

        cursor.execute("""
        SELECT casual
        FROM leave_balance
        WHERE employee_id=?
        """, (employee_id,))

        row = cursor.fetchone()

        if row is None:

            conn.close()

            return {
                "success": False,
                "message": "Employee not found."
            }

        balance = row[0]

        # -------------------------------
        # Calculate number of leave days
        # -------------------------------

        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")

        days = (end - start).days + 1

        if days <= 0:

            conn.close()

            return {
                "success": False,
                "message": "Invalid leave dates."
            }

        if balance < days:

            conn.close()

            return {

                "success": False,

                "message": "Insufficient leave balance."

            }

        # -------------------------------
        # Create Leave Request
        # -------------------------------

        cursor.execute("""
        INSERT INTO leave_requests
        (
            employee_id,
            from_date,
            to_date,
            leave_type,
            status
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """, (

            employee_id,
            from_date,
            to_date,
            leave_type,
            "Pending"

        ))

        # -------------------------------
        # Update Balance
        # -------------------------------

        cursor.execute("""
        UPDATE leave_balance
        SET casual = casual - ?
        WHERE employee_id=?
        """, (

            days,
            employee_id

        ))

        conn.commit()

        request_id = cursor.lastrowid

        conn.close()

        return {

            "success": True,

            "request_id": request_id,

            "days": days,

            "status": "Pending",

            "message": "Leave request submitted."

        }

    # --------------------------------------------------
    # Cancel Leave
    # --------------------------------------------------
    def cancel_leave(self, request_id):

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT
            employee_id,
            from_date,
            to_date,
            status
        FROM leave_requests
        WHERE request_id=?
        """, (request_id,))

        row = cursor.fetchone()

        if row is None:

            conn.close()

            return {

                "success": False,

                "message": "Request not found."

            }

        employee_id = row[0]

        start = datetime.strptime(row[1], "%Y-%m-%d")
        end = datetime.strptime(row[2], "%Y-%m-%d")

        days = (end - start).days + 1

        # Restore Balance

        cursor.execute("""
        UPDATE leave_balance
        SET casual = casual + ?
        WHERE employee_id=?
        """, (

            days,
            employee_id

        ))

        # Delete Request

        cursor.execute("""
        DELETE FROM leave_requests
        WHERE request_id=?
        """, (request_id,))

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Leave cancelled."

        }

    # --------------------------------------------------
    # List Leave Requests
    # --------------------------------------------------
    def list_leave_requests(self, employee_id):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
        SELECT

            request_id,

            from_date,

            to_date,

            leave_type,

            status

        FROM leave_requests

        WHERE employee_id=?

        ORDER BY request_id DESC

        """, (employee_id,))

        rows = cursor.fetchall()

        conn.close()

        requests = []

        for row in rows:

            requests.append({

                "request_id": row[0],

                "from": row[1],

                "to": row[2],

                "leave_type": row[3],

                "status": row[4]

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
        SET status='Approved'
        WHERE request_id=?
        """, (request_id,))

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Leave Approved."

        }