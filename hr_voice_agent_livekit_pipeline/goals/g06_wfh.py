"""
goals/g06_wfh.py  —  Goal 6: Work From Home / Hybrid Work

Actions:
  ├── check_wfh_eligibility()    is this employee eligible for WFH?
  ├── get_wfh_quota_balance()    how many WFH days left this month?
  ├── apply_wfh_request()        submit a WFH request
  └── get_wfh_request_status()   check approval status of a WFH request

Layer flow:
  Agent / LLM -> Goal 6 (g06_wfh) -> WFHService (services layer) -> DB
"""

from typing import Any, cast
from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.wfh_service import WFHService


class WorkFromHomeService(GoalService):
    """
    Goal 6 — Work From Home / Hybrid Work

    Actions:
      ├── check_wfh_eligibility()    is this employee eligible for WFH?
      ├── get_wfh_quota_balance()    how many WFH days left this month?
      ├── apply_wfh_request()        submit a WFH request
      └── get_wfh_request_status()   check approval status of a WFH request
    """

    meta = GoalMeta(
        id="g06_wfh",
        name="Work From Home",
        description=(
            "Check WFH eligibility and quota, submit WFH requests for specific dates, "
            "and track their approval status."
        ),
        trigger_phrases=[
            "work from home",
            "wfh",
            "work remotely",
            "can i work from home",
            "remote work",
            "hybrid work",
            "home office",
            "wfh balance",
        ],
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.wfh_service: WFHService = WFHService()

    @function_tool
    async def check_wfh_eligibility(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check whether the caller is eligible to work from home under company policy."""
        self.log_action("check_wfh_eligibility", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.wfh_service.check_wfh_eligibility(emp_id)
        return res["message"]

    @function_tool
    async def get_wfh_quota_balance(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check how many WFH days the caller has remaining in their monthly quota."""
        self.log_action("get_wfh_quota_balance", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.wfh_service.get_wfh_quota_balance(emp_id)
        return res["message"]

    @function_tool
    async def apply_wfh_request(
        self,
        from_date: str = "",
        to_date: str = "",
        reason: str = "",
        context: RunContext = None,
        employee_id: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Submit a request to work from home for specific dates."""
        s_date = start_date or from_date
        e_date = end_date or to_date
        self.log_action("apply_wfh_request", from_date=s_date, to_date=e_date)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.wfh_service.submit_wfh_request(emp_id, s_date, e_date, reason)
        return res["message"]

    @function_tool
    async def get_wfh_request_status(
        self,
        context: RunContext,
        employee_id: str = "",
        request_id: int = 0,
    ) -> str:
        """Check the status of the caller's most recent Work From Home request."""
        self.log_action("get_wfh_request_status", request_id=request_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.wfh_service.get_wfh_status(emp_id)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_wfh_goal = WorkFromHomeService()

async def check_wfh_eligibility(employee_id: str = "", context: RunContext | None = None) -> str:
    return await cast(Any, _default_wfh_goal).check_wfh_eligibility(context=context, employee_id=employee_id)

async def get_wfh_quota_balance(employee_id: str = "", context: RunContext | None = None) -> str:
    return await cast(Any, _default_wfh_goal).get_wfh_quota_balance(context=context, employee_id=employee_id)

async def apply_wfh_request(
    start_date: str = "",
    end_date: str = "",
    reason: str = "",
    employee_id: str = "",
    context: RunContext | None = None,
    from_date: str = "",
    to_date: str = "",
) -> str:
    return await cast(Any, _default_wfh_goal).apply_wfh_request(
        from_date=from_date or start_date,
        to_date=to_date or end_date,
        reason=reason,
        context=context,
        employee_id=employee_id,
    )

async def get_wfh_request_status(employee_id: str = "", context: RunContext | None = None, request_id: int = 0) -> str:
    return await cast(Any, _default_wfh_goal).get_wfh_request_status(context=context, employee_id=employee_id, request_id=request_id)

execute_check_wfh_eligibility = check_wfh_eligibility
execute_get_wfh_quota_balance = get_wfh_quota_balance
execute_apply_wfh_request = apply_wfh_request
execute_get_wfh_request_status = get_wfh_request_status
