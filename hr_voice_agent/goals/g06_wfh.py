"""
goals/g06_wfh.py  —  Goal 6: Work From Home / Hybrid Work

Actions:
  ├── check_wfh_eligibility()    is this employee eligible for WFH?
  ├── get_wfh_quota_balance()    how many WFH days left this month?
  ├── apply_wfh_request()        submit a WFH request
  └── get_wfh_request_status()   check approval status of a WFH request

Runtime state consumed: verified_employee_id
Durable state read:     wfh_requests, employees (DB)
Durable state written:  wfh_requests (DB) — survives across calls for status checks
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
import hr_tools


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

    @function_tool
    async def check_wfh_eligibility(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check whether the caller is eligible to work from home under company policy."""
        self.log_action("check_wfh_eligibility", employee_id=employee_id)
        return await hr_tools.check_wfh_eligibility(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def get_wfh_quota_balance(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check how many WFH days the caller has remaining in their monthly quota."""
        self.log_action("get_wfh_quota_balance", employee_id=employee_id)
        return await hr_tools.get_wfh_quota_balance(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def apply_wfh_request(
        self,
        from_date: str,
        to_date: str,
        reason: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Submit a request to work from home for specific dates."""
        self.log_action("apply_wfh_request", from_date=from_date, to_date=to_date)
        return await hr_tools.apply_wfh_request(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            reason=reason,
            context=context,
        )

    @function_tool
    async def get_wfh_request_status(
        self,
        context: RunContext,
        request_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check the approval status of a submitted WFH request."""
        self.log_action("get_wfh_request_status", request_id=request_id)
        return await hr_tools.get_wfh_request_status(
            request_id=request_id,
            employee_id=employee_id,
            context=context,
        )

