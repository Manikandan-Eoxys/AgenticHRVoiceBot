"""
goals/g03_leave_application.py  —  Goal 3: Leave Application

Two-step submit flow:
  Step 1 → check_leave_availability()    validate balance, do NOT write
  Step 2 → confirm_leave_request()       write to DB + email HR (after caller confirms)
  Also   → cancel_leave_request()        cancels a pending/approved leave

Runtime state consumed: verified_employee_id
Durable state written:  leave_requests (DB), leave_balances.used_days (DB)
Durable state read:     leave_balances, leave_policy (DB)
"""

from goals.base import GoalService, GoalMeta
from hr_tools import (
    check_leave_availability,
    confirm_leave_request,
    cancel_leave_request,
)


class LeaveApplicationService(GoalService):
    """
    Goal 3 — Leave Application

    Handles the full two-step leave submission flow and cancellation.
    Step 1 (check_leave_availability) is always called first to validate
    balance without writing anything. Step 2 (confirm_leave_request) is
    called only after the caller explicitly confirms.
    """

    meta = GoalMeta(
        id="g03_leave_application",
        name="Leave Application",
        description=(
            "Two-step flow: first check availability, then submit after caller confirms. "
            "Also handles cancellation of pending or approved leaves."
        ),
        trigger_phrases=[
            "apply for leave",
            "take leave",
            "i want leave",
            "book leave",
            "submit leave",
            "request leave",
            "cancel leave",
            "cancel my leave",
        ],
    )

    def tools(self) -> list:
        return [
            check_leave_availability,
            confirm_leave_request,
            cancel_leave_request,
        ]
