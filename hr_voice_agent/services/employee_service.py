"""
services/employee_service.py

Business logic and database operations for Employee Management.

Architecture:
  Goals Layer (g01_identity, g11_benefits, etc.)
      ↓
  EmployeeService (Services Layer)
      ↓
  MySQL Database (database.db)
"""

from datetime import datetime
import re
from database.db import get_db_connection


class EmployeeService:
    """Employee service handling database queries and business logic."""

    def _connect(self):
        """Create MySQL connection."""
        return get_db_connection()

    # -------------------------------------------------------
    # Get Employee by ID
    # -------------------------------------------------------
    def get_employee(self, employee_id: int | str):
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

    # -------------------------------------------------------
    # Verification formatting for caller identity check
    # -------------------------------------------------------
    def get_employee_verification(self, employee_id: str) -> str:
        """Returns verification prompt string for caller identity verification."""
        emp_id = re.sub(r"[^0-9]", "", str(employee_id or ""))
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """SELECT e.employee_id, e.full_name, e.status,
                          des.designation_name, d.department_name
                   FROM employees e
                   JOIN designations des ON des.designation_id = e.designation_id
                   JOIN departments d ON d.department_id = e.department_id
                   WHERE e.employee_id = %s
                   LIMIT 1""",
                (emp_id,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            return f"Sorry, something went wrong looking that up ({e})."

        if not rows:
            return f"No employee found with ID {employee_id}. Employee IDs run from 1001 to 1015. Could you confirm the ID?"
        r = rows[0]
        return (
            f"VERIFY WITH CALLER: This is employee ID {r['employee_id']}, {r['full_name']} — "
            f"is that correct? ({r['designation_name']} in {r['department_name']}, status: {r['status']}.) "
            f"Do not proceed with any other request until the caller confirms this is them."
        )

    # -------------------------------------------------------
    # Search Employee by Name
    # -------------------------------------------------------
    def search_employee(self, name: str):
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
            ],
            "employees": [
                {
                    "employee_id": r["employee_id"],
                    "name": r["name"],
                    "department": r["department"]
                }
                for r in rows
            ]
        }

    # -------------------------------------------------------
    # Employee Summary
    # -------------------------------------------------------
    def get_employee_summary(self, employee_id: int | str):
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

    # -------------------------------------------------------
    # Get Employee Email
    # -------------------------------------------------------
    def get_email(self, employee_id: int | str):
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

    # -------------------------------------------------------
    # Get Employee Name
    # -------------------------------------------------------
    def get_name(self, employee_id: int | str):
        result = self.get_employee(employee_id)
        if not result["success"]:
            return result
        return {
            "success": True,
            "name": result["employee"]["name"]
        }

    # -------------------------------------------------------
    # Get Department
    # -------------------------------------------------------
    def get_department(self, employee_id: int | str):
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

    # -------------------------------------------------------
    # Get Designation
    # -------------------------------------------------------
    def get_designation(self, employee_id: int | str):
        result = self.get_employee(employee_id)
        if not result["success"]:
            return result
        return {
            "success": True,
            "designation": result["employee"]["designation"]
        }

    # -------------------------------------------------------
    # Get Manager
    # -------------------------------------------------------
    def get_manager(self, employee_id: int | str):
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

    def get_manager_phone(self, employee_id: int | str):
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

    # -------------------------------------------------------
    # List All Employees
    # -------------------------------------------------------
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

    # -------------------------------------------------------
    # Employee Exists Check
    # -------------------------------------------------------
    def employee_exists(self, employee_id: int | str):
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

        return {
            "success": count > 0,
            "exists": count > 0
        }

    # -------------------------------------------------------
    # Employee Contact Lookup (for outbound calls)
    # -------------------------------------------------------
    def get_employee_contact(self, employee_id: str) -> dict | None:
        """Helper for outbound-calling to look up employee name and phone."""
        emp_id = re.sub(r"[^0-9]", "", str(employee_id or ""))
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT employee_id, full_name, phone_number FROM employees WHERE employee_id = %s LIMIT 1",
                (emp_id,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception:
            return None

        if not rows or not rows[0].get("phone_number"):
            return None
        r = rows[0]
        phone = r["phone_number"].strip()
        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")
        return {"employee_id": r["employee_id"], "full_name": r["full_name"], "phone_number": phone}

    # -------------------------------------------------------
    # Department Members Listing
    # -------------------------------------------------------
    def list_department_employees(self, department_name: str) -> str:
        """List everyone in a given department."""
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """SELECT e.full_name, des.designation_name
                   FROM employees e
                   JOIN departments d ON d.department_id = e.department_id
                   JOIN designations des ON des.designation_id = e.designation_id
                   WHERE d.department_name LIKE %s
                   ORDER BY e.full_name""",
                (f"%{department_name}%",),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            return f"Sorry, something went wrong looking that up ({e})."

        if not rows:
            return f"No department found matching '{department_name}'."
        return "\n".join(f"{r['full_name']} — {r['designation_name']}" for r in rows)

    # -------------------------------------------------------
    # Insurance Plans
    # -------------------------------------------------------
    def list_insurance_plans(self) -> str:
        """List health insurance plans offered by company."""
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT plan_name, sum_insured, description FROM insurance_plans ORDER BY plan_code")
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            return f"Sorry, something went wrong looking that up ({e})."

        return "\n".join(
            f"{r['plan_name']} — {r['description']} (sum insured Rs.{r['sum_insured']:,.0f})"
            for r in rows
        )

    def get_insurance_info(self, employee_id: str) -> str:
        """Check employee health insurance status."""
        emp_id = re.sub(r"[^0-9]", "", str(employee_id or ""))
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT plan_code, plan_name, sum_insured FROM insurance_plans ORDER BY plan_code")
            plan_rows = cur.fetchall()

            cur.execute(
                """SELECT e.full_name, ip.plan_name, ei.applied_date, ei.claimed, ei.claimed_date
                   FROM employee_insurance ei
                   JOIN employees e ON e.employee_id = ei.employee_id
                   JOIN insurance_plans ip ON ip.plan_code = ei.plan_code
                   WHERE ei.employee_id = %s
                   ORDER BY ip.plan_code""",
                (emp_id,),
            )
            emp_rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            return f"Sorry, something went wrong looking that up ({e})."

        plan_list = ", ".join(f"{p['plan_name']} (up to Rs.{p['sum_insured']:,.0f})" for p in plan_rows)

        if not emp_rows:
            return (
                f"There are {len(plan_rows)} office health insurance plans available: {plan_list}. "
                f"Employee {employee_id} has not applied for any of them yet."
            )

        name = emp_rows[0]["full_name"]
        applied = [r["plan_name"] for r in emp_rows]
        claimed = [r["plan_name"] for r in emp_rows if r["claimed"]]

        applied_str = ", ".join(applied)
        claimed_str = ", ".join(claimed) if claimed else "none of them yet"

        return (
            f"There are {len(plan_rows)} office health insurance plans available: {plan_list}. "
            f"Of these, {name} has applied for {len(applied)}: {applied_str}. "
            f"{name} has claimed {claimed_str}."
        )

    # -------------------------------------------------------
    # Employee Schemes
    # -------------------------------------------------------
    def get_employee_schemes(self, employee_id: str) -> str:
        """List statutory schemes employee is enrolled in (ESI, EPF, etc.)."""
        emp_id = re.sub(r"[^0-9]", "", str(employee_id or ""))
        try:
            conn = self._connect()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """SELECT s.scheme_name, s.category, es.enrolled_date
                   FROM employee_schemes es
                   JOIN employees e ON e.employee_id = es.employee_id
                   JOIN schemes s ON s.scheme_id = es.scheme_id
                   WHERE e.employee_id = %s
                   ORDER BY s.scheme_name""",
                (emp_id,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            return f"Sorry, something went wrong looking that up ({e})."

        if not rows:
            return f"No scheme enrollments found for employee {employee_id}. Please confirm the ID."
        return "\n".join(
            f"{r['scheme_name']} ({r['category']}), enrolled {r['enrolled_date']}"
            for r in rows
        )


# Backward compatibility alias
EmployeeTools = EmployeeService