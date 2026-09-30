"""
goals/g12_leave_verification_outbound.py  —  Goal 12: Outbound Leave Verification

Used ONLY for outbound calls initiated by the system to verify pending
leave requests with employees.

Actions:
  ├── get_pending_leave_request()          find the pending request to discuss
  └── record_leave_verification_reason()  save employee's stated reason to DB

Runtime state consumed: verified_employee_id (confirmed from outbound call metadata)
Durable state read:     leave_requests (DB) — born during an inbound call, must persist
Durable state written:  leave_requests.verification_reason (DB)

This goal's tools must survive across call boundaries by design:
  - leave_requests row is created on call A (inbound, employee submits leave)
  - get_pending_leave_request reads that row on call B (outbound, system calls employee)
  - record_leave_verification_reason updates it on call B
"""

from goals.base import GoalService, GoalMeta
from hr_tools import get_pending_leave_request, record_leave_verification_reason


class LeaveVerificationOutboundService(GoalService):
    """
    Goal 12 — Outbound Leave Verification

    Used when the system initiates a call to ask an employee about a pending
    leave request. The agent greets by name, verifies identity, retrieves the
    pending request, asks for a reason, and records it.

    This goal is disabled on inbound calls by default — enable it only for
    outbound leave_verification call types.
    """

    meta = GoalMeta(
        id="g12_leave_verification_outbound",
        name="Leave Verification (Outbound)",
        description=(
            "Outbound-only goal: retrieve the employee's pending leave request "
            "and record their stated reason to help the manager with approval."
        ),
        trigger_phrases=[],  # Never triggered by speech — only by call_type metadata
        enabled=True,        # Switched to True by AgentToolsController for outbound calls
    )

    def tools(self) -> list:
        return [
            get_pending_leave_request,
            record_leave_verification_reason,
        ]
