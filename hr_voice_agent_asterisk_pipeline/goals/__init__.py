"""
goals/__init__.py

Exports all Goal Service classes, base class, and security/validation helpers.

Ordered list GOALS reflects the canonical order shown in the builder UI.
INBOUND_GOALS  — all goals active for standard inbound calls
OUTBOUND_GOALS — goals added for outbound leave_verification calls
"""

from goals.base import GoalService, GoalMeta
from goals.helpers import clean_id, require_verified, get_verified_id

from goals.g01_identity_verification       import IdentityVerificationService
from goals.g02_leave_balance               import LeaveBalanceService
from goals.g03_leave_application           import LeaveApplicationService
from goals.g04_leave_status                import LeaveStatusService
from goals.g05_attendance                  import AttendanceService
from goals.g06_wfh                         import WorkFromHomeService
from goals.g07_payroll                     import PayrollService
from goals.g08_hr_policy                   import HRPolicyService
from goals.g09_calendar                    import CalendarService
from goals.g10_reminders                   import RemindersService
from goals.g11_employee_benefits           import EmployeeBenefitsService, InsuranceService
from goals.g12_leave_verification_outbound import LeaveVerificationOutboundService
from goals.g13_coordinator_transfer        import CoordinatorTransferService

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
    CoordinatorTransferService,
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
    CoordinatorTransferService,
]

# Extra goals for outbound scheduled_reminder / policy_acknowledgement calls
OUTBOUND_REMINDER_GOALS: list[type[GoalService]] = [
    IdentityVerificationService,
    RemindersService,
    HRPolicyService,
    CoordinatorTransferService,
]

__all__ = [
    "GoalService",
    "GoalMeta",
    "clean_id",
    "require_verified",
    "get_verified_id",
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
    "InsuranceService",
    "LeaveVerificationOutboundService",
    "CoordinatorTransferService",
]
