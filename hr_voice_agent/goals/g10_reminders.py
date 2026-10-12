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

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.reminder_tools import (
    execute_schedule_employee_reminder,
    execute_check_my_scheduled_reminders,
    execute_acknowledge_scheduled_reminder,
)


class RemindersService(GoalService):
    """
    Goal 10 — Scheduled Reminders & Acknowledgements

    Actions:
      ├── schedule_employee_reminder()       "remind me tomorrow at 10 AM"
      ├── check_my_scheduled_reminders()     what reminders do I have?
      └── acknowledge_scheduled_reminder()   "yes I've done it" → mark complete
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

    @function_tool
    async def schedule_employee_reminder(
        self,
        reminder_type: str,
        scheduled_date: str,
        reminder_notes: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Schedule a future reminder notification or call for the caller (e.g. document submission, tax deadline)."""
        self.log_action("schedule_employee_reminder", reminder_type=reminder_type, scheduled_date=scheduled_date)
        return await execute_schedule_employee_reminder(
            employee_id=employee_id,
            reminder_topic=reminder_type or reminder_notes,
            scheduled_time=scheduled_date,
            context=context,
        )

    @function_tool
    async def check_my_scheduled_reminders(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """List upcoming reminders scheduled for the caller."""
        self.log_action("check_my_scheduled_reminders", employee_id=employee_id)
        return await execute_check_my_scheduled_reminders(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def acknowledge_scheduled_reminder(
        self,
        context: RunContext,
        reminder_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Acknowledge or mark complete a delivered reminder or notification."""
        self.log_action("acknowledge_scheduled_reminder", reminder_id=reminder_id)
        return await execute_acknowledge_scheduled_reminder(
            reminder_topic_or_id=str(reminder_id),
            response_text="Completed",
            employee_id=employee_id,
            context=context,
        )

