"""
tools/policy_tools.py

LiveKit Function Tools for the 5 Core HR Policies & Company Holidays:
1. Leave & Holiday Policy
2. Attendance & Regularization Policy
3. Work From Home / Hybrid Work Policy
4. Payroll & Salary Policy
5. Code of Conduct & Grievance Policy
"""

from livekit.agents import function_tool, RunContext
from services.policy_service import PolicyService

_policy_service = PolicyService()


@function_tool
async def get_hr_policy(policy_name: str) -> str:
    """Look up official details on one of the 5 company HR policies:
    1. Leave & Holiday Policy (entitlements, carry-forward, types of leave)
    2. Attendance & Regularization Policy (working hours, grace period, 3 late marks rule)
    3. Work From Home / Hybrid Work Policy (eligibility, 2 days/week quota, core hours)
    4. Payroll & Salary Policy (credit date, PF/TDS deductions, payslips)
    5. Code of Conduct & Grievance Policy (dress code, laptops, harassment escalation)
    Pass the policy name or keywords such as 'leave', 'attendance', 'wfh', 'payroll', 'dress code'."""
    res = _policy_service.get_policy(policy_name)
    if res["success"]:
        return f"{res['policy_name']} ({res['category']}): {res['details']}"
    return res["message"]


@function_tool
async def check_company_holiday(date_or_day: str) -> str:
    """Check if a specific calendar date (e.g. '2026-10-02'), relative day ('tomorrow', 'today'),
    weekday ('Monday', 'Friday'), or holiday name ('Diwali', 'Gandhi Jayanti') is a company holiday."""
    res = _policy_service.check_is_holiday(date_or_day)
    return res["message"]


@function_tool
async def list_upcoming_company_holidays() -> str:
    """List the upcoming declared company holidays for the current year."""
    holidays = _policy_service.list_upcoming_holidays(limit=5)
    if not holidays:
        return "No upcoming holidays found on the company calendar."
    lines = [f"{h['holiday_date']} ({h['holiday_day']}): {h['holiday_name']}" for h in holidays]
    return "Upcoming company holidays: " + "; ".join(lines)