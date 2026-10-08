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

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
import hr_tools


class LeaveApplicationService(GoalService):
    """
    Goal 3 — Leave Application

    Two-step submit flow:
      Step 1 → check_leave_availability()    validate balance, do NOT write
      Step 2 → confirm_leave_request()       write to DB + email HR (after caller confirms)
      Also   → cancel_leave_request()        cancels a pending/approved leave
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

    @function_tool
    async def check_leave_availability(
        self,
        leave_type: str,
        from_date: str,
        to_date: str,
        context: RunContext,
        employee_id: str = "",
        reason: str = "",
        is_half_day: bool = False,
    ) -> str:
        """Step 1 of applying for leave. ALWAYS call this first when a caller
        says they want to apply for or take leave. Computes the number of
        days requested, checks whether the employee has enough balance,
        and tells you if the leave can be taken.
        Does NOT submit the request and does NOT write anything to the
        database or send any email."""
        self.log_action("check_leave_availability", leave_type=leave_type, from_date=from_date, to_date=to_date)
        return await hr_tools.check_leave_availability(
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=from_date,
            end_date=to_date,
            context=context,
        )

    @function_tool
    async def confirm_leave_request(
        self,
        leave_type: str,
        from_date: str,
        to_date: str,
        reason: str,
        context: RunContext,
        employee_id: str = "",
        is_half_day: bool = False,
    ) -> str:
        """Step 2 of applying for leave. Call this ONLY AFTER the caller has
        explicitly confirmed (e.g. said 'yes, go ahead and submit', 'please
        apply', etc.) that they want the leave request submitted, following
        a successful check_leave_availability call. Inserts the request,
        updates used days, and notifies HR."""
        self.log_action("confirm_leave_request", leave_type=leave_type, from_date=from_date, to_date=to_date)
        return await hr_tools.confirm_leave_request(
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=from_date,
            end_date=to_date,
            reason=reason,
            context=context,
        )

    @function_tool
    async def cancel_leave_request(
        self,
        context: RunContext,
        request_id: int = 0,
        reason: str = "",
        employee_id: str = "",
    ) -> str:
        """Cancel a pending or approved leave request previously submitted by the caller.
        If request_id is 0 or omitted, cancels the most recent request."""
        self.log_action("cancel_leave_request", request_id=request_id)
        return await hr_tools.cancel_leave_request(
            employee_id=employee_id,
            request_id=request_id,
            context=context,
        )

