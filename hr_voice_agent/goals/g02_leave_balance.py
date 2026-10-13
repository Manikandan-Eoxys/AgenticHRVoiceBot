"""
goals/g02_leave_balance.py  —  Goal 2: Leave Balance Inquiry

Actions:
  ├── get_leave_balance()       balance for one specific leave type
  ├── get_all_leave_balances()  full summary across all types
  └── list_leave_types()        what leave types exist

Layer flow:
  Agent / LLM -> Goal 2 (g02_leave_balance) -> LeaveService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.leave_service import LeaveService


class LeaveBalanceService(GoalService):
    """
    Goal 2 — Leave Balance Inquiry

    Actions:
      ├── get_leave_balance()       balance for one specific leave type
      ├── get_all_leave_balances()  full summary across all types
      └── list_leave_types()        what leave types exist
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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.leave_service = LeaveService()

    @function_tool
    async def get_leave_balance(self, leave_type: str, context: RunContext, employee_id: str = "") -> str:
        """Get remaining leave balance for a specific leave type (e.g. casual, sick, maternity, comp off)."""
        self.log_action("get_leave_balance", leave_type=leave_type, employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.get_leave_balance(emp_id, leave_type)
        return res["message"]

    @function_tool
    async def get_all_leave_balances(self, context: RunContext, employee_id: str = "") -> str:
        """Get a summary of remaining balances across all leave types for the caller."""
        self.log_action("get_all_leave_balances", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.get_all_leave_balances(emp_id)
        return res["message"]

    @function_tool
    async def list_leave_types(self) -> str:
        """List all available company leave types and annual allowances."""
        self.log_action("list_leave_types")
        res = self.leave_service.list_leave_types()
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_leave_balance_goal = LeaveBalanceService()

async def get_leave_balance(leave_type: str, employee_id: str = "", context: RunContext = None) -> str:
    return await _default_leave_balance_goal.get_leave_balance(leave_type=leave_type, context=context, employee_id=employee_id)

async def get_all_leave_balances(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_leave_balance_goal.get_all_leave_balances(context=context, employee_id=employee_id)

async def list_leave_types() -> str:
    return await _default_leave_balance_goal.list_leave_types()

execute_get_leave_balance = get_leave_balance
execute_get_all_leave_balances = get_all_leave_balances
execute_list_leave_types = list_leave_types
