"""
goals/g01_identity_verification.py  —  Goal 1: Identity Verification

Handles all actions related to verifying the caller's identity before
any HR data access is permitted.

Runtime state (CallSession):
  - verified_employee_id  → set when confirmed, read as security gate
  - verified_employee_name

Config state (AgentRuntimeConfig / DB):
  - employee_id_range (future: configurable per deployment)
"""

from goals.base import GoalService, GoalMeta
from hr_tools import (
    get_employee_by_id,
    confirm_employee_identity,
    list_department_employees,
)
from controllers.agentLoginController import DtmfCollector   # for keypad tool type hint


class IdentityVerificationService(GoalService):
    """
    Goal 1 — Identity Verification

    Actions:
      ├── get_employee_by_id()              look up employee, read name back
      ├── confirm_employee_identity()       lock session to verified ID
      └── collect_employee_id_via_keypad()  DTMF / keyboard fallback (on Agent class)

    Note: collect_employee_id_via_keypad is defined directly on the Agent
    class in agent.py because it needs access to the DtmfCollector instance.
    The other two are registered here.
    """

    meta = GoalMeta(
        id="g01_identity",
        name="Identity Verification",
        description=(
            "Verifies the caller's 4-digit employee ID before any HR action. "
            "Locks the session to one employee for the duration of the call."
        ),
        trigger_phrases=[
            "employee id",
            "my id is",
            "i am employee",
            "verify identity",
            "who am i",
        ],
    )

    def tools(self) -> list:
        return [
            get_employee_by_id,
            confirm_employee_identity,
            list_department_employees,
        ]
