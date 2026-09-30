"""
goals/g07_payroll.py  —  Goal 7: Payroll & Salary

Actions:
  ├── get_salary_credit_date()      when will salary be credited?
  ├── get_basic_salary_info()       basic salary and grade
  ├── get_salary_deductions_info()  PF, PT, TDS, LWP breakdown
  └── check_payslip_status()        is this month's payslip available?

Runtime state consumed: verified_employee_id
Durable state read:     employees, payroll records (DB)
Durable state written:  none (read-only goal)

Note: salary DISCREPANCIES that can't be resolved automatically trigger
raise_hr_ticket (g08_hr_policy). This goal only handles read-only inquiries.
"""

from goals.base import GoalService, GoalMeta
from hr_tools import (
    get_salary_credit_date,
    get_basic_salary_info,
    get_salary_deductions_info,
    check_payslip_status,
)


class PayrollService(GoalService):
    """
    Goal 7 — Payroll & Salary

    Read-only salary inquiries: credit dates, basic salary, deductions
    breakdown, and payslip availability.
    """

    meta = GoalMeta(
        id="g07_payroll",
        name="Payroll & Salary",
        description=(
            "Answer salary credit dates, basic salary info, deduction breakdowns "
            "(PF/PT/TDS/LWP), and payslip availability queries."
        ),
        trigger_phrases=[
            "salary",
            "payslip",
            "pay slip",
            "when will salary be credited",
            "salary not credited",
            "deductions",
            "pf deduction",
            "tax deduction",
            "my pay",
            "salary credited",
        ],
    )

    def tools(self) -> list:
        return [
            get_salary_credit_date,
            get_basic_salary_info,
            get_salary_deductions_info,
            check_payslip_status,
        ]
