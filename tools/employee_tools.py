"""
employee_tools.py

Employee-related database operations.

This module is used by the AI Agent to fetch employee
information from the HR database.
"""

import sqlite3
from config import Config


class EmployeeTools:
    """Employee database operations"""

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        """Create SQLite connection"""
        return sqlite3.connect(self.db)

    # -------------------------------------------------
    # Get Employee by ID
    # -------------------------------------------------
    def get_employee(self, employee_id: int):

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                employee_id,
                name,
                email,
                manager,
                department,
                designation,
                joining_date,
                years_of_service
            FROM employees
            WHERE employee_id = ?
        """, (employee_id,))

        row = cursor.fetchone()

        if row is None:
            conn.close()
            return {
                "success": False,
                "message": "Employee not found."
            }

        joining_date = row[6] if row[6] else "2023-01-01"
        
        # Calculate accurate current years_of_service based on current system date/time
        from datetime import datetime
        try:
            join_dt = datetime.strptime(joining_date, "%Y-%m-%d")
            current_years = max(0, (datetime.now() - join_dt).days // 365)
        except Exception:
            current_years = row[7] if row[7] is not None else 1

        # Auto-update DB if years_of_service changed over time
        if row[7] != current_years:
            cursor.execute("UPDATE employees SET years_of_service=? WHERE employee_id=?", (current_years, employee_id))
            conn.commit()

        conn.close()

        return {
            "success": True,
            "employee": {
                "employee_id": row[0],
                "name": row[1],
                "email": row[2],
                "manager": row[3],
                "department": row[4],
                "designation": row[5],
                "joining_date": joining_date,
                "years_of_service": current_years
            }
        }

    # -------------------------------------------------
    # Get Employee Email
    # -------------------------------------------------
    def get_email(self, employee_id: int):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
            SELECT email
            FROM employees
            WHERE employee_id=?
        """, (employee_id,))

        row = cursor.fetchone()

        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "email": row[0]
        }

    # -------------------------------------------------
    # Get Manager
    # -------------------------------------------------
    def get_manager(self, employee_id: int):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
            SELECT manager
            FROM employees
            WHERE employee_id=?
        """, (employee_id,))

        row = cursor.fetchone()

        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "manager": row[0]
        }

    # -------------------------------------------------
    # Get Department
    # -------------------------------------------------
    def get_department(self, employee_id: int):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
            SELECT department
            FROM employees
            WHERE employee_id=?
        """, (employee_id,))

        row = cursor.fetchone()

        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "department": row[0]
        }

    # -------------------------------------------------
    # List Employees
    # -------------------------------------------------
    def list_employees(self):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                employee_id,
                name,
                department
            FROM employees
            ORDER BY employee_id
        """)

        rows = cursor.fetchall()

        conn.close()

        employees = []

        for row in rows:

            employees.append({

                "employee_id": row[0],
                "name": row[1],
                "department": row[2]

            })

        return {
            "success": True,
            "employees": employees
        }

    # -------------------------------------------------
    # Check Employee Exists
    # -------------------------------------------------
    def employee_exists(self, employee_id: int):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM employees
            WHERE employee_id=?
        """, (employee_id,))

        count = cursor.fetchone()[0]

        conn.close()

        return count > 0