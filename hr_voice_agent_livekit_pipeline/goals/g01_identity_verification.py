"""
goals/g01_identity_verification.py  —  Goal 1: Identity Verification

Handles all actions related to verifying the caller's identity before
any HR data access is permitted.

Layer flow:
  Agent / LLM -> Goal 1 (g01_identity) -> EmployeeService (services layer) -> DB
"""

import logging
from typing import Any, cast
from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import clean_id
from services.employee_service import EmployeeService

logger = logging.getLogger("goals.g01_identity")


class IdentityVerificationService(GoalService):
    """
    Goal 1 — Identity Verification

    Actions:
      ├── confirm_employee_identity()       lock session to verified ID
      ├── get_employee_by_id()              look up employee, read name back
      ├── collect_employee_id_via_keypad()  DTMF / keyboard fallback
      └── list_department_employees()       browse department staff
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

    def __init__(self, collector: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.collector = collector
        self.employee_service: EmployeeService = EmployeeService()

    @function_tool
    async def confirm_employee_identity(self, employee_id: str, context: RunContext) -> str:
        """Locks the active call session to the verified employee ID. Call immediately after caller confirms their name."""
        self.log_action("confirm_employee_identity", employee_id=employee_id)
        emp_id = clean_id(employee_id)
        if context and hasattr(context, "session"):
            setattr(context.session, "verified_employee_id", emp_id)
        return f"Identity locked to employee {emp_id} for this call."

    @function_tool
    async def get_employee_by_id(self, employee_id: str) -> str:
        """Look up employee name, department, and designation by 4-digit employee ID (1001 to 1015)."""
        self.log_action("get_employee_by_id", employee_id=employee_id)
        return self.employee_service.get_employee_verification(employee_id)

    @function_tool
    async def collect_employee_id_via_keypad(self, context: RunContext) -> str:
        """Capture caller's 4-digit employee ID via phone keypad (DTMF) or digit speech when voice ID fails."""
        self.log_action("collect_employee_id_via_keypad")
        if not self.collector:
            return "Keypad collector not initialized. Please ask the caller to speak their 4-digit employee ID clearly."

        logger.info("⏳ [DTMF Fallback] Listening for 4-digit employee ID from keypad, keyboard, or voice...")
        digits = await self.collector.wait_for_id(timeout=25.0)
        if digits:
            logger.info(f"✅ [DTMF Fallback] Captured 4-digit ID: {digits}")
            return (
                f"Caller entered employee ID {digits}. "
                f"Immediately call get_employee_by_id with employee_id '{digits}' and verify their name with the caller."
            )
        else:
            logger.warning("⚠️ [DTMF Fallback] No digits received before timeout.")
            return (
                "No digits were received from keypad, keyboard, or voice within the timeout. "
                "Ask the caller once more: 'I didn't receive your employee ID. "
                "Could you please enter your 4-digit ID on the keypad, or say it slowly one digit at a time?'"
            )

    @function_tool
    async def list_department_employees(self, department_name: str) -> str:
        """List employees in a given department."""
        self.log_action("list_department_employees", department_name=department_name)
        return self.employee_service.list_department_employees(department_name)


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_identity_goal = IdentityVerificationService()

async def confirm_employee_identity(employee_id: str, context: RunContext | None = None) -> str:
    return await cast(Any, _default_identity_goal).confirm_employee_identity(employee_id, context)

async def get_employee_by_id(employee_id: str) -> str:
    return await cast(Any, _default_identity_goal).get_employee_by_id(employee_id)

async def list_department_employees(department_name: str) -> str:
    return await cast(Any, _default_identity_goal).list_department_employees(department_name)

execute_confirm_employee_identity = confirm_employee_identity
execute_get_employee_by_id = get_employee_by_id
execute_list_department_employees = list_department_employees
