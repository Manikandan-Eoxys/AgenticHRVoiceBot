"""
tools/leave_tools.py

LiveKit Function Tools for Leave Management:
1. Balance checks (single type or all leave types)
2. Leave application (two-step: check_leave_availability -> confirm_leave_request)
3. Cancel leave request
4. Check leave request approval status (Approved, Rejected with manager reason, Pending)
5. Send reminder to manager for pending leave approval
"""

from livekit.agents import function_tool, RunContext
from services.leave_service import LeaveService
from tools.tool_helpers import require_verified, get_verified_id

_leave_service = LeaveService()


@function_tool
async def get_leave_balance(leave_type: str, employee_id: str, context: RunContext) -> str:
    """Look up an employee's remaining balance for a specific leave type
    (Casual Leave, Sick Leave, Maternity, Paternity, Comp Off, Bereavement, Short Leave, LWP).
    Always verify the caller first."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _leave_service.get_leave_balance(emp_id, leave_type)
    return res["message"]


@function_tool
async def get_all_leave_balances(employee_id: str, context: RunContext) -> str:
    """Look up the caller's complete leave balance summary across all leave types they are eligible for."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _leave_service.get_all_leave_balances(emp_id)
    return res["message"]


@function_tool
async def check_leave_availability(
    employee_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    context: RunContext,
) -> str:
    """STEP 1 OF LEAVE APPLICATION: Check if the employee has enough balance for
    the requested date range (YYYY-MM-DD format). Reports available days, requested days,
    and remaining balance. Does NOT submit the request or touch the database."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _leave_service.check_leave_availability(emp_id, leave_type, start_date, end_date)
    return res["message"]


@function_tool
async def confirm_leave_request(
    employee_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    reason: str,
    context: RunContext,
) -> str:
    """STEP 2 OF LEAVE APPLICATION: Call this ONLY after the caller has heard the
    availability check result and explicitly said YES to submit. Records the leave request
    in the database, deducts from leave balance, and sends an email notification to HR."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _leave_service.confirm_leave_request(emp_id, leave_type, start_date, end_date, reason)
    return res["message"]


@function_tool
async def cancel_leave_request(
    employee_id: str,
    request_id: int = 0,
    context: RunContext = None,
) -> str:
    """Cancel a previously submitted or approved leave request and restore the leave balance.
    If request_id is not specified, cancels the latest request."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    req_id = request_id if request_id > 0 else None
    res = _leave_service.cancel_leave_request(emp_id, req_id)
    return res["message"]


@function_tool
async def get_leave_request_status(employee_id: str, context: RunContext) -> str:
    """Check the approval status of the caller's latest leave request.
    Informs whether it has been approved by their manager, rejected (along with the manager's reason),
    or is still pending approval."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _leave_service.get_leave_request_status(emp_id)
    return res["message"]


@function_tool
async def send_manager_leave_reminder(
    employee_id: str,
    request_id: int = 0,
    context: RunContext = None,
) -> str:
    """Send an email reminder notification to the caller's manager for a pending leave request awaiting approval."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    req_id = request_id if request_id > 0 else None
    res = _leave_service.send_manager_reminder(emp_id, req_id)
    return res["message"]