"""
services/payroll_service.py

Business logic for Payroll & Salary:
1. Salary disbursement schedule (last working day of each month)
2. Basic salary and pay breakdown inquiry
3. Deductions breakdown (PF, Professional Tax, TDS, LWP)
4. Payslip availability check
"""

import logging
from datetime import date
from database.db import get_db_connection

logger = logging.getLogger("payroll-service")


class PayrollService:

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

    def get_salary_credit_schedule(self) -> dict:
        """Explains standard salary payment timing."""
        return {
            "credit_day": "Last working day of the month",
            "message": "Company salaries are credited to employee bank accounts on the last working day of every calendar month. Monthly payslips are published on the 1st of the following month."
        }

    def get_basic_salary_info(self, employee_id: str) -> dict:
        """Retrieves an employee's basic salary and monthly compensation breakdown."""
        rows = self._query(
            """SELECT month_year, basic_salary, hra, allowances, gross_salary, net_salary, payment_status, payment_date
               FROM payroll_records
               WHERE employee_id = %s
               ORDER BY payroll_id DESC LIMIT 1""",
            (str(employee_id),)
        )
        if not rows:
            return {"success": False, "message": f"No payroll record found for employee {employee_id}."}

        p = rows[0]
        return {
            "success": True,
            "month_year": p["month_year"],
            "basic_salary": float(p["basic_salary"]),
            "hra": float(p["hra"]),
            "allowances": float(p["allowances"]),
            "gross_salary": float(p["gross_salary"]),
            "net_salary": float(p["net_salary"]),
            "payment_status": p["payment_status"],
            "message": f"Your basic salary is ₹{p['basic_salary']:,.2f} per month, with HRA of ₹{p['hra']:,.2f} and allowances of ₹{p['allowances']:,.2f}, totaling a gross salary of ₹{p['gross_salary']:,.2f}."
        }

    def get_salary_deductions_breakdown(self, employee_id: str, month_year: str = None) -> dict:
        """Explains the deductions for a given month (PF, PT, TDS, etc.)."""
        if month_year:
            rows = self._query(
                """SELECT month_year, gross_salary, deductions_pf, deductions_pt, deductions_tax, deductions_other, net_salary
                   FROM payroll_records
                   WHERE employee_id = %s AND LOWER(month_year) LIKE %s LIMIT 1""",
                (str(employee_id), f"%{month_year.lower()}%")
            )
        else:
            rows = self._query(
                """SELECT month_year, gross_salary, deductions_pf, deductions_pt, deductions_tax, deductions_other, net_salary
                   FROM payroll_records
                   WHERE employee_id = %s
                   ORDER BY payroll_id DESC LIMIT 1""",
                (str(employee_id),)
            )

        if not rows:
            return {"success": False, "message": "No salary deductions record found for that period."}

        p = rows[0]
        pf = float(p["deductions_pf"])
        pt = float(p["deductions_pt"])
        tds = float(p["deductions_tax"])
        other = float(p["deductions_other"])
        total_deductions = pf + pt + tds + other

        breakdown_str = f"Provident Fund (PF): ₹{pf:,.2f}, Professional Tax: ₹{pt:,.2f}, TDS (Income Tax): ₹{tds:,.2f}"
        if other > 0:
            breakdown_str += f", Other deductions (unpaid leaves/adjustments): ₹{other:,.2f}"

        return {
            "success": True,
            "month_year": p["month_year"],
            "gross_salary": float(p["gross_salary"]),
            "deductions_pf": pf,
            "deductions_pt": pt,
            "deductions_tax": tds,
            "deductions_other": other,
            "total_deductions": total_deductions,
            "net_salary": float(p["net_salary"]),
            "message": f"For {p['month_year']}, your total deductions were ₹{total_deductions:,.2f}, including {breakdown_str}. Your net take-home salary was ₹{p['net_salary']:,.2f}."
        }

    def check_payslip_status(self, employee_id: str, month_year: str = None) -> dict:
        """Checks if the payslip for a given month is available."""
        if month_year:
            rows = self._query(
                """SELECT month_year, payslip_available, payment_status, payment_date
                   FROM payroll_records
                   WHERE employee_id = %s AND LOWER(month_year) LIKE %s LIMIT 1""",
                (str(employee_id), f"%{month_year.lower()}%")
            )
        else:
            rows = self._query(
                """SELECT month_year, payslip_available, payment_status, payment_date
                   FROM payroll_records
                   WHERE employee_id = %s
                   ORDER BY payroll_id DESC LIMIT 1""",
                (str(employee_id),)
            )

        if not rows:
            return {"success": False, "message": "No payslip found for that month."}

        p = rows[0]
        if p["payslip_available"]:
            msg = f"Your {p['month_year']} payslip is available for download on the employee portal and was also emailed to your registered email address."
        else:
            msg = f"Your {p['month_year']} payslip is currently being processed by payroll and will be published shortly."

        return {
            "success": True,
            "month_year": p["month_year"],
            "payslip_available": bool(p["payslip_available"]),
            "message": msg
        }
