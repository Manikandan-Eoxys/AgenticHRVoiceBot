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

import logging
from typing import Any
from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.employee_tools import (
    execute_confirm_employee_identity,
    execute_get_employee_by_id,
    execute_list_department_employees,
)

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

    @function_tool
    async def confirm_employee_identity(self, employee_id: str, context: RunContext) -> str:
        """Call this immediately after the caller says yes to the identity
        verification question (e.g. 'This is employee 1001, Ravikala, is that
        correct?' -> caller says yes). Locks this call to that employee ID —
        every other tool (leave balance, leave requests, insurance info,
        schemes) will refuse to act on any other employee ID for the rest of
        this call, even if the caller later mentions a different ID. Must be
        called before any leave, balance, or insurance tool."""
        self.log_action("confirm_employee_identity", employee_id=employee_id)
        return await execute_confirm_employee_identity(employee_id, context)

    @function_tool
    async def get_employee_by_id(self, employee_id: str) -> str:
        """Look up an employee by their employee ID (e.g. 1001 through 1015).
        Always call this first when a caller starts a conversation or gives
        an ID, before any other lookup. Returns a verification line — read
        the name back to the caller and get an explicit yes before treating
        them as that employee (e.g. 'This is employee ID 1001, Ravikala —
        is that correct?'). Also returns department, designation, and
        status for your own context."""
        self.log_action("get_employee_by_id", employee_id=employee_id)
        return await execute_get_employee_by_id(employee_id)

    @function_tool
    async def collect_employee_id_via_keypad(self, context: RunContext) -> str:
        """Use this when a spoken employee ID didn't match any employee on
        file, or when the caller denied the name verification question, or
        spoke in another language, or asks to key in their ID. Prompts caller to enter
        their 4-digit employee ID on their phone keypad, keyboard, or speak digits."""
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
        return await execute_list_department_employees(department_name)

