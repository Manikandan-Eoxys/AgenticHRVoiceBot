"""
services/policy_service.py

Business Logic for HR Policies (5 Core MVP Policies + Company Holidays).

Supported Policies:
1. Leave & Holiday Policy (LEAVE_HOLIDAY)
2. Attendance & Regularization Policy (ATTENDANCE_REG)
3. Work From Home / Hybrid Work Policy (WFH_HYBRID)
4. Payroll & Salary Policy (PAYROLL_SALARY)
5. Code of Conduct & Grievance Policy (CONDUCT_GRIEVANCE)
"""

import logging
from datetime import date, datetime
from database.db import get_db_connection

logger = logging.getLogger("policy-service")

POLICY_ALIASES = {
    "leave": "LEAVE_HOLIDAY",
    "holiday": "LEAVE_HOLIDAY",
    "holidays": "LEAVE_HOLIDAY",
    "leave & holiday": "LEAVE_HOLIDAY",
    "casual leave": "LEAVE_HOLIDAY",
    "sick leave": "LEAVE_HOLIDAY",
    "attendance": "ATTENDANCE_REG",
    "regularization": "ATTENDANCE_REG",
    "late mark": "ATTENDANCE_REG",
    "punch": "ATTENDANCE_REG",
    "wfh": "WFH_HYBRID",
    "work from home": "WFH_HYBRID",
    "hybrid": "WFH_HYBRID",
    "remote": "WFH_HYBRID",
    "payroll": "PAYROLL_SALARY",
    "salary": "PAYROLL_SALARY",
    "payslip": "PAYROLL_SALARY",
    "deduction": "PAYROLL_SALARY",
    "conduct": "CONDUCT_GRIEVANCE",
    "code of conduct": "CONDUCT_GRIEVANCE",
    "grievance": "CONDUCT_GRIEVANCE",
    "harassment": "CONDUCT_GRIEVANCE",
    "dress code": "CONDUCT_GRIEVANCE",
    "laptop": "CONDUCT_GRIEVANCE",
}


class PolicyService:

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

    def get_policy(self, query: str) -> dict:
        """Looks up one of the 5 official policies by code or keyword."""
        q = (query or "").strip().lower()
        policy_code = POLICY_ALIASES.get(q)

        if not policy_code:
            for k, v in POLICY_ALIASES.items():
                if k in q:
                    policy_code = v
                    break

        if policy_code:
            rows = self._query(
                "SELECT policy_code, policy_name, category, summary, details FROM hr_policies WHERE policy_code = %s",
                (policy_code,)
            )
        else:
            pattern = f"%{q}%"
            rows = self._query(
                """SELECT policy_code, policy_name, category, summary, details 
                   FROM hr_policies 
                   WHERE LOWER(policy_name) LIKE %s OR LOWER(summary) LIKE %s OR LOWER(details) LIKE %s LIMIT 1""",
                (pattern, pattern, pattern)
            )

        if rows:
            p = rows[0]
            return {
                "success": True,
                "policy_code": p["policy_code"],
                "policy_name": p["policy_name"],
                "category": p["category"],
                "summary": p["summary"],
                "details": p["details"],
            }

        return {
            "success": False,
            "message": f"No policy matching '{query}' found. We have policies for: Leave & Holiday, Attendance & Regularization, Work From Home, Payroll & Salary, and Code of Conduct & Grievances."
        }

    def list_all_policies(self) -> list[dict]:
        """Lists all 5 core policies."""
        return self._query("SELECT policy_code, policy_name, category, summary FROM hr_policies ORDER BY policy_code")

    def check_is_holiday(self, date_or_day_str: str) -> dict:
        """Checks if a given date (YYYY-MM-DD) or weekday name (e.g. 'Monday', 'tomorrow') is a company holiday."""
        s = str(date_or_day_str or "").strip().lower()
        today = date.today()

        # Handle 'today' / 'tomorrow'
        if s == "today":
            target_date = today
        elif s == "tomorrow":
            target_date = today.fromordinal(today.toordinal() + 1)
        else:
            try:
                target_date = datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                target_date = None

        if target_date:
            rows = self._query(
                "SELECT holiday_name, holiday_date, holiday_day FROM company_holidays WHERE holiday_date = %s",
                (target_date.isoformat(),)
            )
            if rows:
                h = rows[0]
                return {
                    "is_holiday": True,
                    "holiday_name": h["holiday_name"],
                    "holiday_date": str(h["holiday_date"]),
                    "holiday_day": h["holiday_day"],
                    "message": f"Yes, {h['holiday_date']} ({h['holiday_day']}) is a company holiday for {h['holiday_name']}."
                }
            return {
                "is_holiday": False,
                "message": f"No, {target_date.strftime('%A, %B %-d, %Y')} is not a company holiday."
            }

        # If caller asked about a day of week like "Monday" or "Friday"
        days_map = {
            "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
            "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday"
        }
        for d_key, d_name in days_map.items():
            if d_key in s:
                # Find the next occurrence of that weekday
                days_ahead = (list(days_map.values()).index(d_name) - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = today.fromordinal(today.toordinal() + days_ahead)
                rows = self._query(
                    "SELECT holiday_name, holiday_date, holiday_day FROM company_holidays WHERE holiday_date = %s",
                    (target_date.isoformat(),)
                )
                if rows:
                    h = rows[0]
                    return {
                        "is_holiday": True,
                        "holiday_name": h["holiday_name"],
                        "holiday_date": str(h["holiday_date"]),
                        "holiday_day": h["holiday_day"],
                        "message": f"Yes, upcoming {d_name} ({h['holiday_date']}) is a company holiday for {h['holiday_name']}."
                    }
                return {
                    "is_holiday": False,
                    "message": f"No, upcoming {d_name} ({target_date.isoformat()}) is not a company holiday."
                }

        # Search by holiday name (e.g. "Diwali", "Gandhi Jayanti")
        rows = self._query(
            "SELECT holiday_name, holiday_date, holiday_day FROM company_holidays WHERE LOWER(holiday_name) LIKE %s LIMIT 1",
            (f"%{s}%",)
        )
        if rows:
            h = rows[0]
            return {
                "is_holiday": True,
                "holiday_name": h["holiday_name"],
                "holiday_date": str(h["holiday_date"]),
                "holiday_day": h["holiday_day"],
                "message": f"{h['holiday_name']} is on {h['holiday_date']} ({h['holiday_day']})."
            }

        return {
            "is_holiday": False,
            "message": f"Could not determine if '{date_or_day_str}' is a holiday. Please provide a date as YYYY-MM-DD or specify the holiday name."
        }

    def list_upcoming_holidays(self, limit: int = 5) -> list[dict]:
        """Returns upcoming company holidays from today onwards."""
        today_str = date.today().isoformat()
        rows = self._query(
            """SELECT holiday_name, holiday_date, holiday_day 
               FROM company_holidays 
               WHERE holiday_date >= %s 
               ORDER BY holiday_date ASC LIMIT %s""",
            (today_str, limit)
        )
        if not rows:
            rows = self._query(
                "SELECT holiday_name, holiday_date, holiday_day FROM company_holidays ORDER BY holiday_date ASC LIMIT %s",
                (limit,)
            )
        return rows