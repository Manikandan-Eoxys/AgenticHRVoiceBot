"""
goals/g05_attendance.py  —  Goal 5: Attendance & Regularization

Actions:
  ├── check_attendance_status()           was I marked present / absent today?
  ├── get_late_marks()                    how many late marks this month?
  ├── submit_attendance_regularization()  request a punch correction
  └── get_regularization_status()         check regularization request outcome

Runtime state consumed: verified_employee_id
Durable state read:     attendance_records (DB)
Durable state written:  attendance_regularizations (DB) — must survive across calls
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.attendance_tools import (
    execute_check_attendance_status,
    execute_get_late_marks,
    execute_submit_attendance_regularization,
    execute_get_regularization_status,
)


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

    @function_tool
    async def check_attendance_status(
        self,
        context: RunContext,
        date_str: str = "today",
        employee_id: str = "",
    ) -> str:
        """Check the attendance punch record (in time, out time, present/absent status) for a specific date (e.g. 'today', 'yesterday', or 'YYYY-MM-DD')."""
        self.log_action("check_attendance_status", date_str=date_str)
        return await execute_check_attendance_status(
            date_str=date_str,
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def get_late_marks(
        self,
        context: RunContext,
        month: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check how many times the caller arrived late this month (or in a specified month 1-12)."""
        self.log_action("get_late_marks", month=month)
        return await execute_get_late_marks(
            month=month,
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def submit_attendance_regularization(
        self,
        punch_date: str,
        punch_type: str,
        actual_time: str,
        reason: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Submit a request to regularize a missed punch (e.g. forgot to punch IN/OUT or biometric machine glitch)."""
        self.log_action("submit_attendance_regularization", punch_date=punch_date, punch_type=punch_type)
        return await execute_submit_attendance_regularization(
            employee_id=employee_id,
            punch_date=punch_date,
            punch_type=punch_type,
            actual_time=actual_time,
            reason=reason,
            context=context,
        )

    @function_tool
    async def get_regularization_status(
        self,
        context: RunContext,
        regularization_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check whether a submitted attendance regularization request was approved, rejected, or is pending."""
        self.log_action("get_regularization_status", regularization_id=regularization_id)
        return await execute_get_regularization_status(
            regularization_id=regularization_id,
            employee_id=employee_id,
            context=context,
        )

