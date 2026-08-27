"""
employee_tools.py
 
Employee-related database operations using MySQL.
 
Matches hr-voicebot-schema-mysql.sql (v3): employees.employee_id is
'1001'..'1015'. get_employee returns enough detail (name, department,
designation, status) for the caller-facing readback verification
("This is employee ID 1013, Kamal — is that correct?") that
security/auth_service.py performs after this returns.
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
                e.phone_number,
                e.status,
                COALESCE(m.full_name, 'HR Manager') AS manager,
                COALESCE(m.phone_number, '') AS manager_phone,
                m.employee_id AS manager_id,
                d.department_name AS department,
                ds.designation_name AS designation,
                e.join_date AS joining_date
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.employee_id
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
                "message": f"No employee found with ID {employee_id}. Employee IDs run from 1001 to 1015. Could you confirm the ID?"
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
                "phone_number": row.get("phone_number"),
                "status": row.get("status"),
                "manager": row["manager"],
                "manager_id": row.get("manager_id"),
                "manager_phone": row.get("manager_phone"),
                "department": row["department"],
                "designation": row["designation"],
                "joining_date": joining_date,
                "years_of_service": current_years
            }
        }
 
    # -------------------------------------------------
    # Search Employee by Name
    # -------------------------------------------------
    def search_employee(self, name: str):
        """Search employees by (partial) name. Used when the caller
        gives a name instead of an employee ID — the prompt should
        still prefer asking for the ID for anything identity-sensitive,
        this is for lookups like 'who is Kamal' / 'find Manikandan'."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
 
        pattern = f"%{name.strip().lower()}%"
        cursor.execute("""
            SELECT
                e.employee_id,
                e.full_name AS name,
                d.department_name AS department,
                ds.designation_name AS designation
            FROM employees e
            LEFT JOIN departments d ON e.department_id = d.department_id
            LEFT JOIN designations ds ON e.designation_id = ds.designation_id
            WHERE LOWER(e.full_name) LIKE %s
            ORDER BY e.full_name
        """, (pattern,))
 
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
 
        if not rows:
            return {
                "success": False,
                "message": f"No employee found matching '{name}'."
            }
 
        return {
            "success": True,
            "matches": [
                {
                    "employee_id": r["employee_id"],
                    "name": r["name"],
                    "department": r["department"],
                    "designation": r["designation"],
                }
                for r in rows
            ]
        }
 
    # -------------------------------------------------
    # Employee Summary (short overview)
    # -------------------------------------------------
    def get_employee_summary(self, employee_id: int):
        result = self.get_employee(employee_id)
        if not result.get("success"):
            return result
 
        emp = result["employee"]
        return {
            "success": True,
            "summary": {
                "employee_id": emp["employee_id"],
                "name": emp["name"],
                "department": emp["department"],
                "designation": emp["designation"],
                "manager": emp["manager"],
                "email": emp["email"],
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
            SELECT m.full_name AS manager_name, m.phone_number AS manager_phone, m.employee_id AS manager_id
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.employee_id
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
        if not row["manager_id"]:
            return {
                "success": False,
                "message": "No manager assigned to this employee."
            }

        mgr_phone = row["manager_phone"] or ""
        if mgr_phone and not mgr_phone.startswith("+"):
            mgr_phone = f"+91{mgr_phone}"

        return {
            "success": True,
            "manager": row["manager_name"],
            "manager_phone": mgr_phone,
            "manager_id": row["manager_id"]
        }

    # -------------------------------------------------
    # Get Manager's Phone Number (for conference call)
    # -------------------------------------------------
    def get_manager_phone(self, employee_id: int):
        """Look up the phone number of the employee's direct manager."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT m.full_name AS manager_name, m.phone_number AS manager_phone
            FROM employees e
            INNER JOIN employees m ON e.manager_id = m.employee_id
            WHERE e.employee_id = %s
        """, (str(employee_id),))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row or not row["manager_phone"]:
            return {
                "success": False,
                "message": "Manager phone number not found in the system."
            }

        mgr_phone = row["manager_phone"] or ""
        if mgr_phone and not mgr_phone.startswith("+"):
            mgr_phone = f"+91{mgr_phone}"

        return {
            "success": True,
            "manager_name": row["manager_name"],
            "manager_phone": mgr_phone
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
 