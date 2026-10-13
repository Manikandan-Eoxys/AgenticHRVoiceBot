"""
goals/g12_leave_verification_outbound.py  —  Goal 12: Outbound Leave Verification

Used ONLY for outbound calls initiated by the system to verify pending
leave requests with employees.

Actions:
  ├── get_pending_leave_request()          find the pending request to discuss
  └── record_leave_verification_reason()  save employee's stated reason to DB

Layer flow:
  Agent / LLM -> Goal 12 (g12_leave_verification_outbound) -> LeaveService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.leave_service import LeaveService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.leave_service = LeaveService()

    @function_tool
    async def get_pending_leave_request(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Retrieve details of any currently pending leave requests for the employee to verify during an outbound call."""
        self.log_action("get_pending_leave_request", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.get_pending_leave_request(emp_id)
        return res["message"]

    @function_tool
    async def record_leave_verification_reason(
        self,
        request_id: int,
        reason: str,
        context: RunContext,
    ) -> str:
        """Record the employee's verbal explanation for their pending leave request so the approving manager has full context."""
        self.log_action("record_leave_verification_reason", request_id=request_id)
        res = self.leave_service.record_leave_verification_reason(request_id, reason)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_outbound_goal = LeaveVerificationOutboundService()

async def get_pending_leave_request(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_outbound_goal.get_pending_leave_request(context=context, employee_id=employee_id)

async def record_leave_verification_reason(request_id: int, reason: str, context: RunContext = None) -> str:
    return await _default_outbound_goal.record_leave_verification_reason(request_id=request_id, reason=reason, context=context)

execute_get_pending_leave_request = get_pending_leave_request
execute_record_leave_verification_reason = record_leave_verification_reason
