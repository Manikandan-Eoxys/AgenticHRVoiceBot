"""
goals/g10_reminders.py  —  Goal 10: Scheduled Reminders & Acknowledgements

Actions:
  ├── schedule_employee_reminder()       "remind me tomorrow at 10 AM"
  ├── check_my_scheduled_reminders()     what reminders do I have?
  └── acknowledge_scheduled_reminder()   "yes I've done it" → mark complete

Layer flow:
  Agent / LLM -> Goal 10 (g10_reminders) -> ReminderService (services layer) -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.reminder_service import ReminderService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.reminder_service = ReminderService()

    @function_tool
    async def schedule_employee_reminder(
        self,
        reminder_type: str,
        scheduled_date: str,
        reminder_notes: str,
        context: RunContext,
        employee_id: str = "",
        reminder_topic: str = "",
        scheduled_time: str = "",
    ) -> str:
        """Schedule a future reminder notification or call for the caller (e.g. document submission, tax deadline)."""
        topic = reminder_topic or reminder_type or reminder_notes
        s_time = scheduled_time or scheduled_date
        self.log_action("schedule_employee_reminder", reminder_type=topic, scheduled_date=s_time)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.reminder_service.schedule_reminder(
            employee_id=emp_id,
            reminder_topic=topic,
            scheduled_time_str=s_time,
        )
        return res["message"]

    @function_tool
    async def check_my_scheduled_reminders(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """List upcoming reminders scheduled for the caller."""
        self.log_action("check_my_scheduled_reminders", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.reminder_service.get_scheduled_reminders(emp_id)
        return res["message"]

    @function_tool
    async def acknowledge_scheduled_reminder(
        self,
        context: RunContext,
        reminder_id: int = 0,
        employee_id: str = "",
        reminder_topic_or_id: str = "",
        response_text: str = "",
    ) -> str:
        """Acknowledge or mark complete a delivered reminder or notification."""
        t_id = reminder_topic_or_id or str(reminder_id)
        self.log_action("acknowledge_scheduled_reminder", reminder_id=t_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.reminder_service.acknowledge_reminder(
            employee_id=emp_id,
            reminder_topic_or_id=t_id,
            response_text=response_text or "Confirmed completed by employee",
        )
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_reminders_goal = RemindersService()

async def schedule_employee_reminder(
    reminder_topic: str = "",
    scheduled_time: str = "",
    employee_id: str = "",
    context: RunContext = None,
    reminder_type: str = "",
    scheduled_date: str = "",
    reminder_notes: str = "",
) -> str:
    return await _default_reminders_goal.schedule_employee_reminder(
        reminder_type=reminder_type,
        scheduled_date=scheduled_date,
        reminder_notes=reminder_notes,
        context=context,
        employee_id=employee_id,
        reminder_topic=reminder_topic,
        scheduled_time=scheduled_time,
    )

async def check_my_scheduled_reminders(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_reminders_goal.check_my_scheduled_reminders(context=context, employee_id=employee_id)

async def acknowledge_scheduled_reminder(
    reminder_topic_or_id: str = "",
    response_text: str = "",
    employee_id: str = "",
    context: RunContext = None,
    reminder_id: int = 0,
) -> str:
    return await _default_reminders_goal.acknowledge_scheduled_reminder(
        context=context,
        reminder_id=reminder_id,
        employee_id=employee_id,
        reminder_topic_or_id=reminder_topic_or_id,
        response_text=response_text,
    )

execute_schedule_employee_reminder = schedule_employee_reminder
execute_check_my_scheduled_reminders = check_my_scheduled_reminders
execute_acknowledge_scheduled_reminder = acknowledge_scheduled_reminder
