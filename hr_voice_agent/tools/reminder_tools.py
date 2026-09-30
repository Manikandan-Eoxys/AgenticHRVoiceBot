"""
tools/reminder_tools.py

LiveKit Function Tools for:
1. Mandatory HR Policy Acknowledgement (e.g. Work From Home Policy)
2. Document / Form Submission Reminders (e.g. PF nomination form)
3. Salary Slip Download Notifications & Reminders
"""

from livekit.agents import function_tool, RunContext
from services.reminder_service import ReminderService
from tools.tool_helpers import require_verified, get_verified_id

_reminder_service = ReminderService()


@function_tool
async def check_pending_policy_acknowledgements(employee_id: str, context: RunContext) -> str:
    """Check if there are any mandatory company HR policies that the employee is required
    to acknowledge before an upcoming deadline (e.g. newly released Work From Home policy)."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _reminder_service.get_pending_policy_acknowledgements(emp_id)
    return res["message"]


@function_tool
async def acknowledge_hr_policy(
    policy_name: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Record the employee's formal acknowledgement of a mandatory HR policy
    (such as the Work From Home Policy) when the employee states 'Yes, I acknowledge the policy'."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _reminder_service.record_policy_acknowledgement(
        employee_id=emp_id,
        policy_name_or_code=policy_name or "Work From Home Policy",
        acknowledgement_note="Acknowledged via HR Voice Assistant",
    )
    return res["message"]


@function_tool
async def schedule_employee_reminder(
    reminder_topic: str,
    scheduled_time: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Schedule a reminder or message for the employee for a future time
    (e.g., 'submit PF nomination form' or 'download salary slip' for 'tomorrow at 10 AM').
    Pass reminder_topic (e.g. 'PF nomination form', 'September salary slip') and scheduled_time (e.g. 'tomorrow at 10 AM')."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _reminder_service.schedule_reminder(
        employee_id=emp_id,
        reminder_topic=reminder_topic,
        scheduled_time_str=scheduled_time,
    )
    return res["message"]


@function_tool
async def check_my_scheduled_reminders(employee_id: str, context: RunContext) -> str:
    """Check what messages or reminders are currently scheduled for the caller
    (such as form submission reminders or salary slip download reminders)."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _reminder_service.get_scheduled_reminders(emp_id)
    return res["message"]


@function_tool
async def acknowledge_scheduled_reminder(
    reminder_topic_or_id: str,
    response_text: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """Record the employee's confirmation or completion of a scheduled reminder task
    (for example when an employee confirms 'Yes, I've submitted it' for their PF nomination form,
    or 'Yes, I've downloaded it' for their salary slip)."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _reminder_service.record_reminder_acknowledgement(
        employee_id=emp_id,
        reminder_topic_or_id=reminder_topic_or_id,
        response_text=response_text or "Yes, completed",
    )
    return res["message"]
