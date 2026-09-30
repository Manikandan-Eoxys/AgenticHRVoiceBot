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

from goals.base import GoalService, GoalMeta
from hr_tools import (
    check_wfh_eligibility,
    get_wfh_quota_balance,
    apply_wfh_request,
    get_wfh_request_status,
)


class WorkFromHomeService(GoalService):
    """
    Goal 6 — Work From Home / Hybrid Work

    Handles WFH eligibility checks, quota balance queries, request
    submission, and approval status checks.
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

    def tools(self) -> list:
        return [
            check_wfh_eligibility,
            get_wfh_quota_balance,
            apply_wfh_request,
            get_wfh_request_status,
        ]
