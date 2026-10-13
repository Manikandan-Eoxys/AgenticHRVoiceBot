"""
goals/g05_attendance.py  —  Goal 5: Attendance & Regularization

Actions:
  ├── check_attendance_status()           was I marked present / absent today?
  ├── get_late_marks()                    how many late marks this month?
  ├── submit_attendance_regularization()  request a punch correction
  └── get_regularization_status()         check regularization request outcome

Layer flow:
  Agent / LLM -> Goal 5 (g05_attendance) -> AttendanceService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.attendance_service import AttendanceService as AttendanceBackendService


class AttendanceService(GoalService):
    """
    Goal 5 — Attendance & Regularization

    Actions:
      ├── check_attendance_status()           was I marked present / absent today?
      ├── get_late_marks()                    how many late marks this month?
      ├── submit_attendance_regularization()  request a punch correction
      └── get_regularization_status()         check regularization request outcome
    """

    meta = GoalMeta(
        id="g05_attendance",
        name="Attendance & Regularization",
        description=(
            "Check daily attendance status, late mark count, and submit "
            "regularization requests for missed punches or biometric issues."
        ),
        trigger_phrases=[
            "attendance",
            "marked absent",
            "did i punch",
            "punch not registered",
            "late mark",
            "regularize attendance",
            "missed punch",
            "biometric issue",
        ],
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.attendance_service = AttendanceBackendService()

    @function_tool
    async def check_attendance_status(
        self,
        context: RunContext,
        date_str: str = "today",
        employee_id: str = "",
    ) -> str:
        """Check the attendance punch record (in time, out time, present/absent status) for a specific date (e.g. 'today', 'yesterday', or 'YYYY-MM-DD')."""
        self.log_action("check_attendance_status", date_str=date_str)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.attendance_service.get_attendance_status(emp_id, date_str)
        return res["message"]

    @function_tool
    async def get_late_marks(
        self,
        context: RunContext,
        month: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check how many times the caller arrived late this month (or in a specified month 1-12)."""
        self.log_action("get_late_marks", month=month)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        target_month = month if month > 0 else None
        res = self.attendance_service.get_late_marks_count(emp_id, month=target_month)
        return res["message"]

    @function_tool
    async def submit_attendance_regularization(
        self,
        punch_date: str = "",
        punch_type: str = "",
        actual_time: str = "",
        reason: str = "",
        context: RunContext = None,
        employee_id: str = "",
        date_str: str = "",
    ) -> str:
        """Submit a request to regularize a missed punch (e.g. forgot to punch IN/OUT or biometric machine glitch)."""
        target_date = punch_date or date_str or "today"
        self.log_action("submit_attendance_regularization", punch_date=target_date, punch_type=punch_type)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.attendance_service.submit_regularization(emp_id, target_date, punch_type, actual_time, reason)
        return res["message"]

    @function_tool
    async def get_regularization_status(
        self,
        context: RunContext,
        employee_id: str = "",
        regularization_id: int = 0,
    ) -> str:
        """Check the status of the caller's most recent attendance regularization request."""
        self.log_action("get_regularization_status", regularization_id=regularization_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.attendance_service.get_regularization_status(emp_id)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_attendance_goal = AttendanceService()

async def check_attendance_status(date_str: str = "today", employee_id: str = "", context: RunContext = None) -> str:
    return await _default_attendance_goal.check_attendance_status(context=context, date_str=date_str, employee_id=employee_id)

async def get_late_marks(employee_id: str = "", context: RunContext = None, month: int = 0) -> str:
    return await _default_attendance_goal.get_late_marks(context=context, month=month, employee_id=employee_id)

async def submit_attendance_regularization(
    punch_date: str = "",
    punch_type: str = "",
    actual_time: str = "",
    reason: str = "",
    employee_id: str = "",
    context: RunContext = None,
    date_str: str = "",
) -> str:
    return await _default_attendance_goal.submit_attendance_regularization(
        punch_date=punch_date,
        punch_type=punch_type,
        actual_time=actual_time,
        reason=reason,
        context=context,
        employee_id=employee_id,
        date_str=date_str,
    )

async def get_regularization_status(employee_id: str = "", context: RunContext = None, regularization_id: int = 0) -> str:
    return await _default_attendance_goal.get_regularization_status(context=context, employee_id=employee_id, regularization_id=regularization_id)

execute_check_attendance_status = check_attendance_status
execute_get_late_marks = get_late_marks
execute_submit_attendance_regularization = submit_attendance_regularization
execute_get_regularization_status = get_regularization_status
