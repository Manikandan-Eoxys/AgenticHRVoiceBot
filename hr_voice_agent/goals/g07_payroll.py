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

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.payroll_tools import (
    execute_get_salary_credit_date,
    execute_get_basic_salary_info,
    execute_get_salary_deductions_info,
    execute_check_payslip_status,
)


class PayrollService(GoalService):
    """
    Goal 7 — Payroll & Salary

    Actions:
      ├── get_salary_credit_date()      when will salary be credited?
      ├── get_basic_salary_info()       basic salary and grade
      ├── get_salary_deductions_info()  PF, PT, TDS, LWP breakdown
      └── check_payslip_status()        is this month's payslip available?
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

    @function_tool
    async def get_salary_credit_date(self) -> str:
        """Tell the caller the expected salary credit date for the current month."""
        self.log_action("get_salary_credit_date")
        return await execute_get_salary_credit_date()

    @function_tool
    async def get_basic_salary_info(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Look up the caller's current salary structure details."""
        self.log_action("get_basic_salary_info", employee_id=employee_id)
        return await execute_get_basic_salary_info(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def get_salary_deductions_info(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Explain typical payroll deductions (PF, professional tax, income tax / TDS, leave without pay)."""
        self.log_action("get_salary_deductions_info", employee_id=employee_id)
        return await execute_get_salary_deductions_info(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def check_payslip_status(
        self,
        context: RunContext,
        month: str = "",
        employee_id: str = "",
    ) -> str:
        """Check whether the payslip for a given month is generated and available for download."""
        self.log_action("check_payslip_status", month=month)
        return await execute_check_payslip_status(
            month=month,
            employee_id=employee_id,
            context=context,
        )

