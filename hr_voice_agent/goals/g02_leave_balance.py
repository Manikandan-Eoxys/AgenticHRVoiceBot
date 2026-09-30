"""
goals/g02_leave_balance.py  —  Goal 2: Leave Balance Inquiry

Actions:
  ├── list_leave_types()        what leave types exist
  ├── get_leave_balance()       balance for one specific leave type
  └── get_all_leave_balances()  full summary across all types

Runtime state consumed: verified_employee_id (from CallSession via _require_verified)
Durable state read:     leave_balances, leave_policy tables (DB)
"""

from goals.base import GoalService, GoalMeta
from hr_tools import list_leave_types, get_leave_balance, get_all_leave_balances


class LeaveBalanceService(GoalService):
    """
    Goal 2 — Leave Balance Inquiry

    Lets employees check how many days of each leave type they have
    remaining, either for one specific type or all at once.
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

    def tools(self) -> list:
        return [
            list_leave_types,
            get_leave_balance,
            get_all_leave_balances,
        ]
