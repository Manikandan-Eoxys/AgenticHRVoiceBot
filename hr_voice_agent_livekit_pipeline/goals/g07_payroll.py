"""
goals/g07_payroll.py  —  Goal 7: Payroll & Salary

Actions:
  ├── get_salary_credit_date()      when will salary be credited?
  ├── get_basic_salary_info()       basic salary and grade
  ├── get_salary_deductions_info()  PF, PT, TDS, LWP breakdown
  └── check_payslip_status()        is this month's payslip available?

Layer flow:
  Agent / LLM -> Goal 7 (g07_payroll) -> PayrollService (services layer) -> DB
"""

from typing import Any, cast
from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.payroll_service import PayrollService as PayrollBackendService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.payroll_service: PayrollBackendService = PayrollBackendService()

    @function_tool
    async def get_salary_credit_date(self) -> str:
        """Tell the caller the expected salary credit date for the current month."""
        self.log_action("get_salary_credit_date")
        res = self.payroll_service.get_salary_credit_schedule()
        return res["message"]

    @function_tool
    async def get_basic_salary_info(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Look up the caller's current salary structure details."""
        self.log_action("get_basic_salary_info", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.payroll_service.get_basic_salary_info(emp_id)
        return res["message"]

    @function_tool
    async def get_salary_deductions_info(
        self,
        context: RunContext,
        employee_id: str = "",
        month_year: str = "",
        month: str = "",
    ) -> str:
        """Explain typical payroll deductions (PF, professional tax, income tax / TDS, leave without pay)."""
        self.log_action("get_salary_deductions_info", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        target_month = month_year or month or None
        res = self.payroll_service.get_salary_deductions_breakdown(emp_id, target_month)
        return res["message"]

    @function_tool
    async def check_payslip_status(
        self,
        context: RunContext,
        month: str = "",
        employee_id: str = "",
        month_year: str = "",
    ) -> str:
        """Check whether the payslip for a given month is generated and available for download."""
        self.log_action("check_payslip_status", month=month or month_year)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        target_month = month_year or month or None
        res = self.payroll_service.check_payslip_status(emp_id, target_month)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_payroll_goal = PayrollService()

async def get_salary_credit_date() -> str:
    return await cast(Any, _default_payroll_goal).get_salary_credit_date()

async def get_basic_salary_info(employee_id: str = "", context: RunContext | None = None) -> str:
    return await cast(Any, _default_payroll_goal).get_basic_salary_info(context=context, employee_id=employee_id)

async def get_salary_deductions_info(
    month_year: str = "",
    employee_id: str = "",
    context: RunContext | None = None,
    month: str = "",
) -> str:
    return await cast(Any, _default_payroll_goal).get_salary_deductions_info(
        context=context,
        employee_id=employee_id,
        month_year=month_year,
        month=month,
    )

async def check_payslip_status(
    month_year: str = "",
    employee_id: str = "",
    context: RunContext | None = None,
    month: str = "",
) -> str:
    return await cast(Any, _default_payroll_goal).check_payslip_status(
        context=context,
        employee_id=employee_id,
        month=month,
        month_year=month_year,
    )

execute_get_salary_credit_date = get_salary_credit_date
execute_get_basic_salary_info = get_basic_salary_info
execute_get_salary_deductions_info = get_salary_deductions_info
execute_check_payslip_status = check_payslip_status
