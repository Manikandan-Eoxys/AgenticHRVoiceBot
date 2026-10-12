"""
goals/g02_leave_balance.py  —  Goal 2: Leave Balance Inquiry

Actions:
  ├── list_leave_types()        what leave types exist
  ├── get_leave_balance()       balance for one specific leave type
  └── get_all_leave_balances()  full summary across all types

Runtime state consumed: verified_employee_id (from CallSession via _require_verified)
Durable state read:     leave_balances, leave_policy tables (DB)
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.leave_tools import (
    execute_get_leave_balance,
    execute_get_all_leave_balances,
    execute_list_leave_types,
)


class LeaveBalanceService(GoalService):
    """
    Goal 2 — Leave Balance Inquiry

    Actions:
      ├── get_leave_balance()       balance for one specific leave type
      ├── get_all_leave_balances()  full summary across all types
      ├── list_leave_types()        what leave types exist
      └── get_leave_policy()        policy rules per leave type
    """

    meta = GoalMeta(
        id="g02_leave_balance",
        name="Leave Balance Inquiry",
        description="Check remaining leave days for one or all leave types.",
        trigger_phrases=[
            "leave balance",
            "how many leaves",
            "how many days left",
            "check my leave",
            "leave remaining",
            "casual leave balance",
            "sick leave balance",
        ],
    )

    @function_tool
    async def get_leave_balance(self, leave_type: str, context: RunContext, employee_id: str = "") -> str:
        """Look up the remaining leave balance for a specific leave type for
        the caller. leave_type can be casual leave, sick leave, maternity leave,
        paternity leave, comp off, bereavement leave, short leave, or leave without pay."""
        self.log_action("get_leave_balance", leave_type=leave_type, employee_id=employee_id)
        return await execute_get_leave_balance(leave_type=leave_type, employee_id=employee_id, context=context)

    @function_tool
    async def get_all_leave_balances(self, context: RunContext, employee_id: str = "") -> str:
        """Look up the caller's remaining leave balances across ALL leave
        types at once. Use this when the caller asks generally 'how many leaves
        do I have left?' or wants a full summary."""
        self.log_action("get_all_leave_balances", employee_id=employee_id)
        return await execute_get_all_leave_balances(employee_id=employee_id, context=context)

    @function_tool
    async def list_leave_types(self) -> str:
        """List all valid leave types in the company policy and how many
        annual days each allows."""
        self.log_action("list_leave_types")
        return await execute_list_leave_types()

