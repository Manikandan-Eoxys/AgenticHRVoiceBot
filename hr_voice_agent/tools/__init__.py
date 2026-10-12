"""
tools/__init__.py

Re-exports all LiveKit function tools from domain tool modules.
"""

from tools.employee_tools import (
    confirm_employee_identity,
    get_employee_by_id,
    get_employee_contact,
    list_department_employees,
    list_insurance_plans,
    get_insurance_info,
    get_employee_schemes,
    EmployeeTools,
)
from tools.leave_tools import (
    get_leave_balance,
    get_all_leave_balances,
    check_leave_availability,
    confirm_leave_request,
    cancel_leave_request,
    get_leave_request_status,
    send_manager_leave_reminder,
    list_leave_types,
    get_pending_leave_request,
    record_leave_verification_reason,
)
from tools.attendance_tools import (
    check_attendance_status,
    get_late_marks,
    submit_attendance_regularization,
    get_regularization_status,
)
from tools.wfh_tools import (
    check_wfh_eligibility,
    get_wfh_quota_balance,
    apply_wfh_request,
    get_wfh_request_status,
)
from tools.payroll_tools import (
    get_salary_credit_date,
    get_basic_salary_info,
    get_salary_deductions_info,
    check_payslip_status,
)
from tools.policy_tools import (
    get_hr_policy,
    get_leave_policy,
    check_company_holiday,
    list_upcoming_company_holidays,
)
from tools.grievance_tools import (
    get_code_of_conduct_policy,
    report_confidential_grievance,
)
from tools.reminder_tools import (
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
)
from tools.ticket_tools import (
    raise_hr_ticket,
    check_my_hr_tickets,
)
from tools.calendar_tools import CalendarTools

__all__ = [
    # Employee & Identity & Benefits
    "confirm_employee_identity",
    "get_employee_by_id",
    "get_employee_contact",
    "list_department_employees",
    "list_insurance_plans",
    "get_insurance_info",
    "get_employee_schemes",
    "EmployeeTools",
    # Leave
    "get_leave_balance",
    "get_all_leave_balances",
    "check_leave_availability",
    "confirm_leave_request",
    "cancel_leave_request",
    "get_leave_request_status",
    "send_manager_leave_reminder",
    "list_leave_types",
    "get_pending_leave_request",
    "record_leave_verification_reason",
    # Attendance
    "check_attendance_status",
    "get_late_marks",
    "submit_attendance_regularization",
    "get_regularization_status",
    # WFH
    "check_wfh_eligibility",
    "get_wfh_quota_balance",
    "apply_wfh_request",
    "get_wfh_request_status",
    # Payroll
    "get_salary_credit_date",
    "get_basic_salary_info",
    "get_salary_deductions_info",
    "check_payslip_status",
    # Policy & Holidays
    "get_hr_policy",
    "get_leave_policy",
    "check_company_holiday",
    "list_upcoming_company_holidays",
    # Grievance
    "get_code_of_conduct_policy",
    "report_confidential_grievance",
    # Reminders
    "check_pending_policy_acknowledgements",
    "acknowledge_hr_policy",
    "schedule_employee_reminder",
    "check_my_scheduled_reminders",
    "acknowledge_scheduled_reminder",
    # Tickets
    "raise_hr_ticket",
    "check_my_hr_tickets",
    # Calendar
    "CalendarTools",
]
