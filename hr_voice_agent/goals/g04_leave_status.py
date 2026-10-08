"""
goals/g04_leave_status.py  —  Goal 4: Leave Status & Manager Reminders

Actions:
  ├── get_leave_request_status()      check approved / rejected / pending
  └── send_manager_leave_reminder()   nudge manager on pending requests

Runtime state consumed: verified_employee_id
Durable state read:     leave_requests (DB) — must survive across calls
Durable state written:  none (reminder is a push notification, no DB write)
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
import hr_tools


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
        return await hr_tools.get_leave_request_status(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def send_manager_leave_reminder(
        self,
        context: RunContext,
        request_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Send a gentle notification reminder to the manager asking them to review a pending leave request."""
        self.log_action("send_manager_leave_reminder", request_id=request_id)
        return await hr_tools.send_manager_leave_reminder(
            employee_id=employee_id,
            request_id=request_id,
            context=context,
        )

