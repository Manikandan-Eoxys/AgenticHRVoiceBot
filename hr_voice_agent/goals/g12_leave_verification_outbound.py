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

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.leave_tools import (
    execute_get_pending_leave_request,
    execute_record_leave_verification_reason,
)


class LeaveVerificationOutboundService(GoalService):
    """
    Goal 12 — Outbound Leave Verification

    Actions:
      ├── get_pending_leave_request()          find the pending request to discuss
      └── record_leave_verification_reason()  save employee's stated reason to DB
    """

    meta = GoalMeta(
        id="g12_leave_verification_outbound",
        name="Leave Verification (Outbound)",
        description=(
            "Outbound-only goal: retrieve the employee's pending leave request "
            "and record their stated reason to help the manager with approval."
        ),
        trigger_phrases=[],  # Never triggered by speech — only by call_type metadata
        enabled=True,
    )

    @function_tool
    async def get_pending_leave_request(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Retrieve details of any currently pending leave requests for the employee to verify during an outbound call."""
        self.log_action("get_pending_leave_request", employee_id=employee_id)
        return await execute_get_pending_leave_request(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def record_leave_verification_reason(
        self,
        request_id: int,
        reason: str,
        context: RunContext,
    ) -> str:
        """Record the employee's verbal explanation for their pending leave request so the approving manager has full context."""
        self.log_action("record_leave_verification_reason", request_id=request_id)
        return await execute_record_leave_verification_reason(
            request_id=request_id,
            reason=reason,
            context=context,
        )

