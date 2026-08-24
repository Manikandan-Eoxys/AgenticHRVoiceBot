"""
grievance_tools.py

Grievance Management Tool using MySQL.

Functions:
1. Create Grievance
2. Get Grievance
3. List Employee Grievances
4. Update Status
5. Add Comment
6. Close Grievance
"""

from datetime import datetime
from database.db import get_db_connection


class GrievanceTools:

    def _connect(self):
        return get_db_connection()

    # ----------------------------------------------------
    # Create New Grievance
    # ----------------------------------------------------
    def create_grievance(
        self,
        employee_id,
        title,
        description
    ):
        conn = self._connect()
        cursor = conn.cursor()

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO grievances
            (employee_id, title, description, status, created_at)
            VALUES (%s, %s, %s, 'Open', %s)
        """, (str(employee_id), title, description, created_at))

        grievance_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "grievance_id": grievance_id,
            "status": "Open",
            "message": "Grievance created successfully."
        }

    # ----------------------------------------------------
    # Get Single Grievance
    # ----------------------------------------------------
    def get_grievance(self, grievance_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT grievance_id, employee_id, title, description, status, created_at
            FROM grievances
            WHERE grievance_id = %s
        """, (grievance_id,))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Grievance not found."
            }

        return {
            "success": True,
            "grievance": {
                "grievance_id": row["grievance_id"],
                "employee_id": row["employee_id"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "created_at": str(row["created_at"])
            }
        }

    # ----------------------------------------------------
    # List Employee Grievances
    # ----------------------------------------------------
    def list_grievances(self, employee_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT grievance_id, title, status, created_at
            FROM grievances
            WHERE employee_id = %s
            ORDER BY grievance_id DESC
        """, (str(employee_id),))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        grievances = []
        for r in rows:
            grievances.append({
                "grievance_id": r["grievance_id"],
                "title": r["title"],
                "status": r["status"],
                "created_at": str(r["created_at"])
            })

        return {
            "success": True,
            "grievances": grievances
        }

    # ----------------------------------------------------
    # Update Status
    # ----------------------------------------------------
    def update_status(
        self,
        grievance_id,
        status
    ):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE grievances
            SET status = %s
            WHERE grievance_id = %s
        """, (status, grievance_id))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Status Updated."
        }

    # ----------------------------------------------------
    # Close Grievance
    # ----------------------------------------------------
    def close_grievance(self, grievance_id):
        return self.update_status(grievance_id, "Closed")

    # ----------------------------------------------------
    # Add Comment
    # ----------------------------------------------------
    def add_comment(
        self,
        grievance_id,
        comment
    ):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT description
            FROM grievances
            WHERE grievance_id = %s
        """, (grievance_id,))

        row = cursor.fetchone()

        if row is None:
            cursor.close()
            conn.close()
            return {
                "success": False,
                "message": "Grievance not found."
            }

        updated_description = row["description"] + "\n\nComment:\n" + comment

        cursor.execute("""
            UPDATE grievances
            SET description = %s
            WHERE grievance_id = %s
        """, (updated_description, grievance_id))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Comment added."
        }