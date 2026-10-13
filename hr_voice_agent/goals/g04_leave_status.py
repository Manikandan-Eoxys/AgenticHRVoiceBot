"""
goals/g04_leave_status.py  —  Goal 4: Leave Status & Manager Reminders

Actions:
  ├── get_leave_request_status()      check approved / rejected / pending
  └── send_manager_leave_reminder()   nudge manager on pending requests

Layer flow:
  Agent / LLM -> Goal 4 (g04_leave_status) -> LeaveService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.leave_service import LeaveService


class LeaveStatusService(GoalService):
    """
    Goal 4 — Leave Status & Manager Reminders

    Actions:
      ├── get_leave_request_status()      check approved / rejected / pending
      └── send_manager_leave_reminder()   nudge manager on pending requests
    """

    meta = GoalMeta(
        id="g04_leave_status",
        name="Leave Status & Reminders",
        description=(
            "Check the status of a submitted leave request (approved / rejected / pending) "
            "and optionally send a reminder to the approving manager."
        ),
        trigger_phrases=[
            "leave status",
            "was my leave approved",
            "check my leave status",
            "leave approved",
            "leave rejected",
            "pending leave",
            "remind manager",
            "send reminder",
        ],
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.leave_service = LeaveService()

    @function_tool
    async def get_leave_request_status(
        self,
        context: RunContext,
        request_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check the approval status of a submitted leave request. If request_id
        is 0 or omitted, looks up the caller's most recent request."""
        self.log_action("get_leave_request_status", request_id=request_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.get_leave_request_status(emp_id)
        return res["message"]

    @function_tool
    async def send_manager_leave_reminder(
        self,
        context: RunContext,
        request_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Send a gentle notification reminder to the manager asking them to review a pending leave request."""
        self.log_action("send_manager_leave_reminder", request_id=request_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        req_id = request_id if request_id > 0 else None
        res = self.leave_service.send_manager_reminder(emp_id, req_id)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_leave_status_goal = LeaveStatusService()

async def get_leave_request_status(employee_id: str = "", request_id: int = 0, context: RunContext = None) -> str:
    return await _default_leave_status_goal.get_leave_request_status(context=context, request_id=request_id, employee_id=employee_id)

async def send_manager_leave_reminder(employee_id: str = "", request_id: int = 0, context: RunContext = None) -> str:
    return await _default_leave_status_goal.send_manager_leave_reminder(context=context, request_id=request_id, employee_id=employee_id)

execute_get_leave_request_status = get_leave_request_status
execute_send_manager_leave_reminder = send_manager_leave_reminder
