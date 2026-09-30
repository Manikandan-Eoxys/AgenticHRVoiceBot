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

from goals.base import GoalService, GoalMeta
from hr_tools import (
    check_attendance_status,
    get_late_marks,
    submit_attendance_regularization,
    get_regularization_status,
)


class AttendanceService(GoalService):
    """
    Goal 5 — Attendance & Regularization

    Handles all attendance inquiries (absent/present, late marks) and
    regularization requests (forgot to punch in/out, biometric failure).
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

    def tools(self) -> list:
        return [
            check_attendance_status,
            get_late_marks,
            submit_attendance_regularization,
            get_regularization_status,
        ]
