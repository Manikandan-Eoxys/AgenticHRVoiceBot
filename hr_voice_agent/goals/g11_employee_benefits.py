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

from goals.base import GoalService, GoalMeta
from hr_tools import get_employee_schemes, list_insurance_plans, get_insurance_info


class EmployeeBenefitsService(GoalService):
    """
    Goal 11 — Employee Schemes & Insurance

    Lets employees check their enrolled benefit schemes (ESI, PF, gratuity,
    group insurance) and get details of available insurance plans.
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

    def tools(self) -> list:
        return [
            get_employee_schemes,
            list_insurance_plans,
            get_insurance_info,
        ]
