"""
goals/g10_reminders.py  —  Goal 10: Scheduled Reminders & Acknowledgements

Actions:
  ├── schedule_employee_reminder()       "remind me tomorrow at 10 AM"
  ├── check_my_scheduled_reminders()     what reminders do I have?
  └── acknowledge_scheduled_reminder()   "yes I've done it" → mark complete

Runtime state consumed: verified_employee_id
Durable state read:     scheduled_reminders (DB)
Durable state written:  scheduled_reminders (DB)
  → Must survive across calls because outbound reminder calls read these rows.

This goal is the bridge between inbound calls (where a reminder is scheduled)
and outbound reminder calls (where the agent delivers it and records the
employee's acknowledgement).
"""

from goals.base import GoalService, GoalMeta
from hr_tools import (
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
)


class RemindersService(GoalService):
    """
    Goal 10 — Scheduled Reminders & Acknowledgements

    Lets employees schedule future reminders (e.g. "remind me to submit
    my PF form tomorrow at 10 AM") and later acknowledge completion.
    Also drives the outbound reminder call flow.
    """

    meta = GoalMeta(
        id="g10_reminders",
        name="Scheduled Reminders & Acknowledgements",
        description=(
            "Schedule HR reminders (PF forms, payslip downloads, policy deadlines), "
            "check what reminders are pending, and acknowledge them when done."
        ),
        trigger_phrases=[
            "remind me",
            "set a reminder",
            "schedule reminder",
            "check my reminders",
            "pending reminders",
            "i have completed",
            "i submitted",
            "acknowledge reminder",
            "mark as done",
        ],
    )

    def tools(self) -> list:
        return [
            schedule_employee_reminder,
            check_my_scheduled_reminders,
            acknowledge_scheduled_reminder,
        ]
