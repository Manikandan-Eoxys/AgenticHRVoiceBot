"""
tools/wfh_tools.py

LiveKit Function Tools for Work From Home / Hybrid Work:
1. Eligibility check
2. Monthly WFH quota & remaining allowance check (up to 8 days/month, 2 days/week)
3. Apply for WFH (pending manager approval)
4. Check WFH request status
"""

from livekit.agents import function_tool, RunContext
from services.wfh_service import WFHService
from tools.tool_helpers import require_verified, get_verified_id

_wfh_service = WFHService()


@function_tool
async def execute_check_wfh_eligibility(employee_id: str, context: RunContext) -> str:
    """Check if the caller is eligible for hybrid work / Work From Home."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _wfh_service.check_wfh_eligibility(emp_id)
    return res["message"]


@function_tool
async def execute_get_wfh_quota_balance(employee_id: str, context: RunContext) -> str:
    """Check how many Work From Home days the caller has used this month and how many are remaining
    (company policy allows up to 2 days per week or 8 days per month with manager approval)."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _wfh_service.get_wfh_quota_balance(emp_id)
    return res["message"]


@function_tool
async def execute_apply_wfh_request(
    start_date: str = "",
    end_date: str = "",
    reason: str = "",
    employee_id: str = "",
    context: RunContext = None,
    from_date: str = "",
    to_date: str = "",
) -> str:
    """Submit a Work From Home application for specific dates (YYYY-MM-DD format).
    The request is recorded in the database with status 'pending' awaiting manager approval."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    s_date = start_date or from_date
    e_date = end_date or to_date
    res = _wfh_service.submit_wfh_request(emp_id, s_date, e_date, reason)
    return res["message"]


@function_tool
async def execute_get_wfh_request_status(
    employee_id: str = "",
    context: RunContext = None,
    request_id: int = 0,
) -> str:
    """Check the status of the caller's most recent Work From Home request."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _wfh_service.get_wfh_status(emp_id)
    return res["message"]


# ── Backward compatibility aliases ────────────────────────────────────────────
check_wfh_eligibility = execute_check_wfh_eligibility
get_wfh_quota_balance = execute_get_wfh_quota_balance
apply_wfh_request = execute_apply_wfh_request
get_wfh_request_status = execute_get_wfh_request_status
