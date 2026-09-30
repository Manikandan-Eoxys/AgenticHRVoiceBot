"""
goals/g04_leave_status.py  —  Goal 4: Leave Status & Manager Reminders

Actions:
  ├── get_leave_request_status()      check approved / rejected / pending
  └── send_manager_leave_reminder()   nudge manager on pending requests

Runtime state consumed: verified_employee_id
Durable state read:     leave_requests (DB) — must survive across calls
Durable state written:  none (reminder is a push notification, no DB write)
"""

from goals.base import GoalService, GoalMeta
from hr_tools import get_leave_request_status, send_manager_leave_reminder


class LeaveStatusService(GoalService):
    """
    Goal 4 — Leave Status & Manager Reminders

    Lets employees check whether their submitted leave was approved,
    rejected, or is still pending — and optionally nudge their manager.
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

    def tools(self) -> list:
        return [
            get_leave_request_status,
            send_manager_leave_reminder,
        ]
