"""
goals/g11_employee_benefits.py  —  Goal 11: Employee Schemes & Insurance

Actions:
  ├── list_insurance_plans()    what company insurance plans are available?
  ├── get_insurance_info()      details of the employee's current insurance coverage
  └── get_employee_schemes()    what schemes/benefits is this employee enrolled in?

Layer flow:
  Agent / LLM -> Goal 11 (g11_benefits) -> EmployeeService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.employee_service import EmployeeService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.employee_service = EmployeeService()

    @function_tool
    async def list_insurance_plans(self) -> str:
        """List all company-provided insurance plans with coverage amounts and premiums."""
        self.log_action("list_insurance_plans")
        return self.employee_service.list_insurance_plans()

    @function_tool
    async def get_insurance_info(self, context: RunContext, employee_id: str = "") -> str:
        """Look up caller's active company insurance policy (policy number, plan name, coverage, validity)."""
        self.log_action("get_insurance_info", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        return self.employee_service.get_insurance_info(emp_id)

    @function_tool
    async def get_employee_schemes(self, context: RunContext, employee_id: str = "") -> str:
        """Look up statutory welfare schemes the caller is enrolled in (ESI, EPF UAN, Gratuity)."""
        self.log_action("get_employee_schemes", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        return self.employee_service.get_employee_schemes(emp_id)


# Semantic alias matching the user's Goal / UI naming
InsuranceService = EmployeeBenefitsService

# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_benefits_goal = EmployeeBenefitsService()

async def list_insurance_plans() -> str:
    return await _default_benefits_goal.list_insurance_plans()

async def get_insurance_info(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_benefits_goal.get_insurance_info(context=context, employee_id=employee_id)

async def get_employee_schemes(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_benefits_goal.get_employee_schemes(context=context, employee_id=employee_id)

execute_list_insurance_plans = list_insurance_plans
execute_get_insurance_info = get_insurance_info
execute_get_employee_schemes = get_employee_schemes
