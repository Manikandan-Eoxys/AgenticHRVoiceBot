"""
tools/ticket_tools.py

LiveKit Function Tools for Raising and Managing HR Tickets across 6 categories:
1. Payroll & Salary Issues (PAYROLL_SALARY_DISCREPANCY, SALARY_NOT_CREDITED, etc.)
2. Attendance Issues (ATTENDANCE_REGULARIZATION, ATTENDANCE_STATUS_CORRECTION, etc.)
3. Leave Issues (LEAVE_REQUEST_ISSUE, LEAVE_BALANCE_DISCREPANCY, etc.)
4. HRMS / Employee Profile Issues (EMPLOYEE_DATA_CORRECTION, PERSONAL_DETAILS_CORRECTION, etc.)
5. HR Documents (EXPERIENCE_LETTER_REQUEST, EMPLOYMENT_CERTIFICATE_REQUEST, etc.)
6. Work From Home / Hybrid Work (WFH_EXCEPTION_REQUEST, WFH_APPROVAL_DELAY, etc.)
"""

from livekit.agents import function_tool, RunContext
from services.ticket_service import TicketService
from tools.tool_helpers import require_verified, get_verified_id

_ticket_service = TicketService()


@function_tool
async def raise_hr_ticket(
    category: str,
    ticket_type: str,
    description: str,
    priority: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Create an HR ticket when an issue requires human intervention or escalation.
    Categories and common types:
    1. 'Payroll & Salary Issues': PAYROLL_SALARY_DISCREPANCY, SALARY_NOT_CREDITED, SALARY_DEDUCTION_QUERY, PAYSLIP_NOT_AVAILABLE, PAYSLIP_CORRECTION, BONUS_INCENTIVE_DISCREPANCY, TAX_DEDUCTION_QUERY.
    2. 'Attendance Issues': ATTENDANCE_REGULARIZATION, ATTENDANCE_STATUS_CORRECTION, ATTENDANCE_CORRECTION, BIOMETRIC_ISSUE, ATTENDANCE_SYSTEM_ISSUE, LATE_MARK_DISPUTE.
    3. 'Leave Issues': LEAVE_REQUEST_ISSUE, LEAVE_BALANCE_DISCREPANCY, LEAVE_APPROVAL_DELAY, LEAVE_CANCELLATION_ISSUE, LEAVE_EXCEPTION_REQUEST, EMERGENCY_LEAVE_REQUEST.
    4. 'HRMS / Employee Profile Issues': EMPLOYEE_DATA_CORRECTION, PERSONAL_DETAILS_CORRECTION, ADDRESS_UPDATE_ISSUE, BANK_DETAILS_UPDATE, EMERGENCY_CONTACT_UPDATE, EMPLOYEE_ID_ISSUE.
    5. 'HR Documents': EXPERIENCE_LETTER_REQUEST, EMPLOYMENT_CERTIFICATE_REQUEST, SALARY_CERTIFICATE_REQUEST, RELIEVING_LETTER_REQUEST, HR_DOCUMENT_CORRECTION.
    6. 'Work From Home / Hybrid Work': WFH_EXCEPTION_REQUEST, WFH_APPROVAL_DELAY, WFH_SYSTEM_ISSUE, HYBRID_WORK_ISSUE.
    Priority can be 'Normal', 'Urgent', or 'Confidential'."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _ticket_service.create_ticket(emp_id, category, ticket_type, description, priority or "Normal")
    return res["message"]


@function_tool
async def check_my_hr_tickets(
    employee_id: str,
    context: RunContext,
) -> str:
    """Check the status and history of HR tickets submitted by the caller."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _ticket_service.get_employee_tickets(emp_id)
    return res["message"]
