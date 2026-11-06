"""
goals/g03_leave_application.py  —  Goal 3: Leave Application

Two-step submit flow:
  Step 1 → check_leave_availability()    validate balance, do NOT write
  Step 2 → confirm_leave_request()       write to DB + email HR (after caller confirms)
  Also   → cancel_leave_request()        cancels a pending/approved leave

Layer flow:
  Agent / LLM -> Goal 3 (g03_leave_application) -> LeaveService (services layer) -> DB
"""

from typing import Any, cast
from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.leave_service import LeaveService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.leave_service: LeaveService = LeaveService()

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
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Step 1: Check available leave balance and date validity before submitting a request (does not save to DB)."""
        s_date = from_date or start_date
        e_date = to_date or end_date
        self.log_action("check_leave_availability", leave_type=leave_type, from_date=s_date, to_date=e_date)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.check_leave_availability(emp_id, leave_type, s_date, e_date)
        return res["message"]

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
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Step 2: Submit and record a confirmed leave request after availability check and caller approval."""
        s_date = from_date or start_date
        e_date = to_date or end_date
        self.log_action("confirm_leave_request", leave_type=leave_type, from_date=s_date, to_date=e_date)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.leave_service.confirm_leave_request(emp_id, leave_type, s_date, e_date, reason)
        return res["message"]

    @function_tool
    async def cancel_leave_request(
        self,
        context: RunContext,
        request_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Cancel a previously submitted or approved leave request and restore the leave balance."""
        self.log_action("cancel_leave_request", request_id=request_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        req_id = request_id if request_id > 0 else None
        res = self.leave_service.cancel_leave_request(emp_id, req_id)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_leave_app_goal = LeaveApplicationService()

async def check_leave_availability(
    leave_type: str,
    from_date: str = "",
    to_date: str = "",
    context: RunContext | None = None,
    employee_id: str = "",
    start_date: str = "",
    end_date: str = "",
    **kwargs: Any,
) -> str:
    from goals.helpers import clean_id
    is_emp_ctx = hasattr(employee_id, "session")
    is_ctx_ctx = hasattr(context, "session")
    if clean_id(leave_type) and (is_emp_ctx or not clean_id(employee_id if not is_emp_ctx else "")) and from_date and not any(ch.isdigit() for ch in from_date.replace("-", "")):
        actual_emp = leave_type
        actual_type = from_date
        actual_from = to_date
        actual_to = str(context) if isinstance(context, str) else ""
        actual_ctx = employee_id if is_emp_ctx else (context if is_ctx_ctx else None)
        return await cast(Any, _default_leave_app_goal).check_leave_availability(
            leave_type=actual_type,
            from_date=actual_from,
            to_date=actual_to,
            context=actual_ctx,
            employee_id=actual_emp,
            **kwargs,
        )
    return await cast(Any, _default_leave_app_goal).check_leave_availability(
        leave_type=leave_type,
        from_date=from_date or start_date,
        to_date=to_date or end_date,
        context=context,
        employee_id=employee_id,
        **kwargs,
    )

async def confirm_leave_request(
    leave_type: str,
    from_date: str = "",
    to_date: str = "",
    reason: str = "",
    context: RunContext | None = None,
    employee_id: str = "",
    start_date: str = "",
    end_date: str = "",
    **kwargs: Any,
) -> str:
    return await cast(Any, _default_leave_app_goal).confirm_leave_request(
        leave_type=leave_type,
        from_date=from_date or start_date,
        to_date=to_date or end_date,
        reason=reason,
        context=context,
        employee_id=employee_id,
        **kwargs,
    )

async def cancel_leave_request(
    employee_id: str = "",
    request_id: int = 0,
    context: RunContext | None = None,
) -> str:
    return await cast(Any, _default_leave_app_goal).cancel_leave_request(
        context=context,
        request_id=request_id,
        employee_id=employee_id,
    )

execute_check_leave_availability = check_leave_availability
execute_confirm_leave_request = confirm_leave_request
execute_cancel_leave_request = cancel_leave_request
