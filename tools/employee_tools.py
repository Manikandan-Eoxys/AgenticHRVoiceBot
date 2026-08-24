"""
employee_tools.py

Employee-related database operations using MySQL.
"""

from datetime import datetime
from database.db import get_db_connection


class EmployeeTools:
    """Employee database operations"""

    def _connect(self):
        """Create MySQL connection"""
        return get_db_connection()

    # -------------------------------------------------
    # Get Employee by ID
    # -------------------------------------------------
    def get_employee(self, employee_id: int):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                e.employee_id,
                e.full_name AS name,
                e.email,
                'HR Manager' AS manager,
                d.department_name AS department,
                ds.designation_name AS designation,
                e.join_date AS joining_date
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            LEFT JOIN designations ds ON e.designation_id = ds.designation_id
            WHERE e.employee_id = %s
        """, (str(employee_id),))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        joining_date = str(row["joining_date"]) if row["joining_date"] else "2023-01-01"

        try:
            join_dt = datetime.strptime(joining_date, "%Y-%m-%d")
            current_years = max(0, (datetime.now() - join_dt).days // 365)
        except Exception:
            current_years = 1

        return {
            "success": True,
            "employee": {
                "employee_id": row["employee_id"],
                "name": row["name"],
                "email": row["email"],
                "manager": row["manager"],
                "department": row["department"],
                "designation": row["designation"],
                "joining_date": joining_date,
                "years_of_service": current_years
            }
        }

    # -------------------------------------------------
    # Get Employee Email
    # -------------------------------------------------
    def get_email(self, employee_id: int):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT email
            FROM employees
            WHERE employee_id = %s
        """, (str(employee_id),))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "email": row["email"]
        }

    # -------------------------------------------------
    # Get Manager
    # -------------------------------------------------
    def get_manager(self, employee_id: int):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT full_name FROM employees WHERE employee_id = %s
        """, (str(employee_id),))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "manager": "HR Manager"
        }

    # -------------------------------------------------
    # Get Department
    # -------------------------------------------------
    def get_department(self, employee_id: int):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT d.department_name AS department
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            WHERE e.employee_id = %s
        """, (str(employee_id),))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return {
            "success": True,
            "department": row["department"]
        }

    # -------------------------------------------------
    # List Employees
    # -------------------------------------------------
    def list_employees(self):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                e.employee_id,
                e.full_name AS name,
                d.department_name AS department
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            ORDER BY e.employee_id
        """)

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        employees = []
        for row in rows:
            employees.append({
                "employee_id": row["employee_id"],
                "name": row["name"],
                "department": row["department"]
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
            WHERE employee_id = %s
        """, (str(employee_id),))

        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        return count > 0