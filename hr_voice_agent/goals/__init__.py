"""
goals/__init__.py

Exports all Goal Service classes and the base class.

Ordered list GOALS reflects the canonical order shown in the builder UI.
INBOUND_GOALS  — all goals active for standard inbound calls
OUTBOUND_GOALS — goals added for outbound leave_verification calls
"""

from goals.base import GoalService, GoalMeta

from goals.g01_identity_verification   import IdentityVerificationService
from goals.g02_leave_balance           import LeaveBalanceService
from goals.g03_leave_application       import LeaveApplicationService
from goals.g04_leave_status            import LeaveStatusService
from goals.g05_attendance              import AttendanceService
from goals.g06_wfh                     import WorkFromHomeService
from goals.g07_payroll                 import PayrollService
from goals.g08_hr_policy               import HRPolicyService
from goals.g09_calendar                import CalendarService
from goals.g10_reminders               import RemindersService
from goals.g11_employee_benefits       import EmployeeBenefitsService
from goals.g12_leave_verification_outbound import LeaveVerificationOutboundService

# Canonical ordered list (mirrors builder UI)
ALL_GOAL_CLASSES: list[type[GoalService]] = [
    IdentityVerificationService,
    LeaveBalanceService,
    LeaveApplicationService,
    LeaveStatusService,
    AttendanceService,
    WorkFromHomeService,
    PayrollService,
    HRPolicyService,
    CalendarService,
    RemindersService,
    EmployeeBenefitsService,
    LeaveVerificationOutboundService,
]

# Goals active on every inbound call (g12 is outbound-only)
INBOUND_GOALS: list[type[GoalService]] = [
    g for g in ALL_GOAL_CLASSES
    if g is not LeaveVerificationOutboundService
]

# Extra goals added for outbound leave_verification calls
OUTBOUND_LEAVE_VERIFICATION_GOALS: list[type[GoalService]] = [
    IdentityVerificationService,
    LeaveVerificationOutboundService,
    LeaveStatusService,
]

# Extra goals for outbound scheduled_reminder / policy_acknowledgement calls
OUTBOUND_REMINDER_GOALS: list[type[GoalService]] = [
    IdentityVerificationService,
    RemindersService,
    HRPolicyService,
]

__all__ = [
    "GoalService",
    "GoalMeta",
    "ALL_GOAL_CLASSES",
    "INBOUND_GOALS",
    "OUTBOUND_LEAVE_VERIFICATION_GOALS",
    "OUTBOUND_REMINDER_GOALS",
    "IdentityVerificationService",
    "LeaveBalanceService",
    "LeaveApplicationService",
    "LeaveStatusService",
    "AttendanceService",
    "WorkFromHomeService",
    "PayrollService",
    "HRPolicyService",
    "CalendarService",
    "RemindersService",
    "EmployeeBenefitsService",
    "LeaveVerificationOutboundService",
]
