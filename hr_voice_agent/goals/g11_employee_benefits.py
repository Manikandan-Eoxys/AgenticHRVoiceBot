"""
goals/g11_employee_benefits.py  —  Goal 11: Employee Schemes & Insurance

Actions:
  ├── get_employee_schemes()    what schemes/benefits is this employee enrolled in?
  ├── list_insurance_plans()    what company insurance plans are available?
  └── get_insurance_info()      details of the employee's current insurance coverage

Runtime state consumed: verified_employee_id
Durable state read:     employee_schemes, insurance_plans, employee_insurance (DB)
Durable state written:  none (read-only goal)
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.employee_tools import (
    execute_list_insurance_plans,
    execute_get_insurance_info,
    execute_get_employee_schemes,
)


class EmployeeBenefitsService(GoalService):
    """
    Goal 11 (Goal 5 in UI) — Employee Schemes & Insurance

    Actions:
      ├── list_insurance_plans()    what company insurance plans are available?
      ├── get_insurance_info()      details of current insurance coverage
      └── get_employee_schemes()    enrolled schemes (ESI, PF, gratuity)
    """

    meta = GoalMeta(
        id="g11_benefits",
        name="Employee Schemes & Insurance",
        description=(
            "Check which benefit schemes (ESI, PF, gratuity) an employee is enrolled in "
            "and look up their company insurance plan coverage."
        ),
        trigger_phrases=[
            "insurance",
            "my insurance",
            "coverage",
            "esi",
            "provident fund",
            "pf",
            "gratuity",
            "schemes",
            "employee benefits",
            "benefit scheme",
            "insurance plan",
        ],
    )

    @function_tool
    async def list_insurance_plans(self) -> str:
        """List all company-provided insurance plans with coverage amounts and premiums."""
        self.log_action("list_insurance_plans")
        return await execute_list_insurance_plans()

    @function_tool
    async def get_insurance_info(self, context: RunContext, employee_id: str = "") -> str:
        """Look up the caller's active company insurance policy (policy number,
        plan name, coverage amount, valid-through date)."""
        self.log_action("get_insurance_info", employee_id=employee_id)
        return await execute_get_insurance_info(employee_id, context)

    @function_tool
    async def get_employee_schemes(self, context: RunContext, employee_id: str = "") -> str:
        """Look up government and statutory welfare schemes the caller is
        enrolled in (e.g. ESI with insurance number, EPF / Provident Fund
        with UAN number, Gratuity eligibility)."""
        self.log_action("get_employee_schemes", employee_id=employee_id)
        return await execute_get_employee_schemes(employee_id, context)


# Semantic alias matching the user's Goal / UI naming
InsuranceService = EmployeeBenefitsService

