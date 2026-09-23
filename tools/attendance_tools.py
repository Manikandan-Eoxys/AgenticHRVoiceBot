"""
tools/attendance_tools.py

LiveKit Function Tools for Attendance & Regularization:
1. Daily attendance inquiry (e.g. why marked absent yesterday, punch timings)
2. Monthly late marks count and policy penalty rules
3. Submit attendance regularization request (missing punch in/out, late mark dispute)
4. Check regularization status
"""

from livekit.agents import function_tool, RunContext
from services.attendance_service import AttendanceService
from tools.tool_helpers import require_verified, get_verified_id

_attendance_service = AttendanceService()


@function_tool
async def check_attendance_status(
    date_str: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Check the caller's attendance status for a specific date (YYYY-MM-DD), 'yesterday', or 'today'.
    Answers questions like 'Why is yesterday showing as absent?' or 'Did my punch in register today?'."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _attendance_service.get_attendance_status(emp_id, date_str)
    return res["message"]


@function_tool
async def get_late_marks(employee_id: str, context: RunContext) -> str:
    """Check how many late marks the caller has accumulated in the current month,
    and explains the policy consequence (e.g. 3 late marks result in a half-day deduction)."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _attendance_service.get_late_marks_count(emp_id)
    return res["message"]


@function_tool
async def submit_attendance_regularization(
    date_str: str,
    punch_type: str,
    actual_time: str,
    reason: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Submit an attendance regularization request when the caller forgot to punch in, forgot to punch out,
    experienced a biometric scanner failure, or wants to regularize a late mark.
    Parameters:
    - date_str: date of attendance ('today', 'yesterday', or YYYY-MM-DD)
    - punch_type: 'missing_punch_in', 'missing_punch_out', or 'late_regularization'
    - actual_time: estimated actual entry/exit time (e.g. '09:05:00' or '18:15:00')
    - reason: brief reason for the regularization request"""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _attendance_service.submit_regularization(emp_id, date_str, punch_type, actual_time, reason)
    return res["message"]


@function_tool
async def get_regularization_status(employee_id: str, context: RunContext) -> str:
    """Check the status of the caller's most recent attendance regularization request."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _attendance_service.get_regularization_status(emp_id)
    return res["message"]
