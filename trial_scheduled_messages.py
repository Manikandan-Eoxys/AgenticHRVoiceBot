"""
trial_scheduled_messages.py

Enterprise HR Voicebot Trial & Verification Script for:
1. Mandatory HR Policy Acknowledgement (Work From Home Policy)
2. Document / Form Submission Reminder (PF Nomination Form)
3. Salary / Payslip Notification & Scheduled Download Reminder

Usage:
    ./.venv/bin/python trial_scheduled_messages.py
    ./.venv/bin/python trial_scheduled_messages.py --interactive
    ./.venv/bin/python trial_scheduled_messages.py --questions
"""

import sys
import os
import asyncio
from unittest.mock import MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from database.db import get_db_connection
from services.reminder_service import ReminderService
from tools.reminder_tools import (
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
)
from tools.payroll_tools import check_payslip_status

def make_mock_context(employee_id="1014"):
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.verified_employee_id = employee_id
    return ctx


def query_db(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def print_section(title: str):
    print("\n" + "=" * 76)
    print(f"  {title.upper()}")
    print("=" * 76)


def print_turn(speaker: str, text: str):
    icon = "👤" if speaker in ("HR", "Employee") else "🤖"
    print(f"\n{icon} {speaker}:")
    print(f"   \"{text}\"")


def show_trial_questions():
    print_section("Trial Question & Dialogue Guide (Voice & Trial Testing)")
    print("""
The following are the exact questions and conversational flows you can use
when speaking to the HR Voicebot (or running in testing):

----------------------------------------------------------------------------
SCENARIO 1: MANDATORY HR POLICY ACKNOWLEDGEMENT
----------------------------------------------------------------------------
Target Employee: Mani (ID: 1014) or Ravikala (ID: 1001)

1. Check pending mandatory policies:
   User: "Do I have any pending HR policies to acknowledge?"
   Bot:  "A new Work From Home Policy has been released. Please review the
          policy and confirm whether you acknowledge it."

2. Give acknowledgement:
   User: "Yes, I acknowledge the Work From Home policy."
   Bot:  "Thank you. Your acknowledgement has been recorded."

3. Verify status after acknowledgement:
   User: "Are there any other policies I need to acknowledge?"
   Bot:  "You have no pending policy acknowledgements at this time."

----------------------------------------------------------------------------
SCENARIO 2: DOCUMENT / FORM SUBMISSION REMINDER
----------------------------------------------------------------------------
Target Employee: Mani (ID: 1014)

1. Schedule a reminder for tomorrow:
   User: "I need to submit my PF nomination form. Can you remind me tomorrow at 10 AM?"
   Bot:  "Sure. I'll remind you tomorrow at 10 AM."

2. Check scheduled messages:
   User: "What scheduled reminders do I have?"
   Bot:  "You have 1 active scheduled reminder: 'PF Nomination Form Submission'
          scheduled for Wednesday at 10:00 AM."

3. Respond to reminder prompt / confirm completion:
   Bot:  "Good morning. This is a reminder to submit your PF nomination form.
          Have you completed it?"
   User: "Yes, I've submitted it."
   Bot:  "Thank you. I've recorded your acknowledgement."

----------------------------------------------------------------------------
SCENARIO 3: SALARY / PAYSLIP NOTIFICATION & SCHEDULED DOWNLOAD REMINDER
----------------------------------------------------------------------------
Target Employee: Mani (ID: 1014)

1. Inquire about payslip:
   User: "Is my September salary slip available?"
   Bot:  "Your September 2026 payslip is available for download on the employee
          portal. Would you like me to remind you later to download it?"

2. Schedule reminder:
   User: "Yes, remind me tomorrow at 10 AM."
   Bot:  "Sure. I'll remind you tomorrow at 10 AM."

3. Respond to reminder / confirm download:
   Bot:  "Your salary slip is available. Have you downloaded it?"
   User: "Yes."
   Bot:  "Thank you. Your acknowledgement has been recorded."
----------------------------------------------------------------------------
""")


async def run_automated_trial():
    print_section("Running Automated Trial Simulation for All 3 Use Cases")
    emp_id = "1014"  # Manikandan (Mani)
    ctx = make_mock_context(emp_id)

    # Ensure clean state for trial employee so the demo runs identically every time
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE policy_acknowledgements SET status = 'Pending', acknowledged_at = NULL, acknowledgement_note = NULL WHERE employee_id = %s", (emp_id,))
    conn.commit()
    cur.close()
    conn.close()

    # ------------------------------------------------------------------
    # USE CASE 1: MANDATORY HR POLICY ACKNOWLEDGEMENT
    # ------------------------------------------------------------------
    print_section("Use Case 1: Mandatory HR Policy Acknowledgement")
    print_turn("HR", "We have released a new Work From Home policy. All employees need to acknowledge it before Friday.")

    # 1. Bot checks pending policy acknowledgements
    pending_msg = await check_pending_policy_acknowledgements(emp_id, ctx)
    print_turn("AI", pending_msg)

    # 2. Employee acknowledges
    print_turn("Employee", "Yes, I acknowledge the policy.")
    ack_response = await acknowledge_hr_policy("Work From Home Policy", emp_id, ctx)
    print_turn("AI", ack_response)

    # DB Verification
    rows = query_db(
        "SELECT policy_name, status, acknowledged_at, acknowledgement_note FROM policy_acknowledgements WHERE employee_id = %s",
        (emp_id,)
    )
    print("\n📊 Database Status in `policy_acknowledgements`:")
    for r in rows:
        print(f"   Policy: {r['policy_name']} | Status: {r['status']} | Acknowledged At: {r['acknowledged_at']}")
    assert rows and rows[0]["status"] == "Acknowledged"
    print("   ✅ Verified: Policy acknowledgement recorded in database.")

    # ------------------------------------------------------------------
    # USE CASE 2: DOCUMENT / FORM SUBMISSION REMINDER
    # ------------------------------------------------------------------
    print_section("Use Case 2: Document / Form Submission Reminder")
    print_turn("Employee", "I need to submit my PF nomination form. Can you remind me tomorrow at 10 AM?")

    # 1. AI schedules reminder
    sched_msg = await schedule_employee_reminder(
        reminder_topic="PF nomination form",
        scheduled_time="tomorrow at 10 AM",
        employee_id=emp_id,
        context=ctx,
    )
    print_turn("AI", sched_msg)

    # 2. Check my scheduled reminders
    my_reminders = await check_my_scheduled_reminders(emp_id, ctx)
    print("\n   [Checking Scheduled Reminders Tool]:", my_reminders)

    # 3. Next day / reminder execution prompt
    print("\n⏰ [Next Day: Scheduled Reminder Triggered]")
    print_turn("AI", "Good morning. This is a reminder to submit your PF nomination form. Have you completed it?")
    print_turn("Employee", "Yes, I've submitted it.")

    # 4. AI records acknowledgement
    rec_ack = await acknowledge_scheduled_reminder("PF nomination form", "Yes, I've submitted it", emp_id, ctx)
    print_turn("AI", rec_ack)

    # DB Verification
    r_rows = query_db(
        "SELECT title, status, scheduled_time, acknowledged_at, employee_response FROM scheduled_reminders WHERE employee_id = %s AND title LIKE '%PF%' ORDER BY reminder_id DESC LIMIT 1",
        (emp_id,)
    )
    print("\n📊 Database Status in `scheduled_reminders`:")
    for r in r_rows:
        print(f"   Title: {r['title']} | Status: {r['status']} | Response: {r['employee_response']} | At: {r['acknowledged_at']}")
    assert r_rows and r_rows[0]["status"] == "Acknowledged"
    print("   ✅ Verified: Form submission acknowledgement recorded in database.")

    # ------------------------------------------------------------------
    # USE CASE 3: SALARY / PAYSLIP NOTIFICATION & REMINDER
    # ------------------------------------------------------------------
    print_section("Use Case 3: Salary / Payslip Notification & Scheduled Download Reminder")
    print_turn("HR", "Salary slips are available.")

    # 1. Check September payslip status
    slip_status = await check_payslip_status("September 2026", emp_id, ctx)
    print("\n   [Payslip Status Tool]:", slip_status)

    # 2. AI notifies employee
    print_turn("AI", "Your September salary slip is now available in the employee portal. Would you like me to remind you later to download it?")
    print_turn("Employee", "Yes, remind me tomorrow at 10 AM.")

    # 3. AI schedules payslip reminder
    slip_sched = await schedule_employee_reminder(
        reminder_topic="September salary slip download",
        scheduled_time="tomorrow at 10 AM",
        employee_id=emp_id,
        context=ctx,
    )
    print_turn("AI", slip_sched)

    # 4. Next day: reminder delivery
    print("\n⏰ [Next Day: Payslip Reminder Triggered]")
    print_turn("AI", "Your salary slip is available. Have you downloaded it?")
    print_turn("Employee", "Yes.")

    # 5. AI records acknowledgement
    slip_ack = await acknowledge_scheduled_reminder("salary slip", "Yes, downloaded", emp_id, ctx)
    print_turn("AI", slip_ack)

    # DB Verification
    s_rows = query_db(
        "SELECT title, status, scheduled_time, acknowledged_at, employee_response FROM scheduled_reminders WHERE employee_id = %s AND title LIKE '%Salary%' ORDER BY reminder_id DESC LIMIT 1",
        (emp_id,)
    )
    print("\n📊 Database Status in `scheduled_reminders`:")
    for r in s_rows:
        print(f"   Title: {r['title']} | Status: {r['status']} | Response: {r['employee_response']} | At: {r['acknowledged_at']}")
    assert s_rows and s_rows[0]["status"] == "Acknowledged"
    print("   ✅ Verified: Payslip download reminder acknowledgement recorded in database.")

    print("\n" + "=" * 76)
    print("  🎉 ALL 3 ENTERPRISE USE CASES VERIFIED SUCCESSFULLY IN DATABASE!")
    print("=" * 76 + "\n")


async def run_interactive():
    print_section("Interactive HR Reminders & Acknowledgements Trial")
    emp_id = input("Enter Employee ID to test (default: 1014 for Mani): ").strip() or "1014"
    ctx = make_mock_context(emp_id)

    while True:
        print("\nChoose an action to test:")
        print("1. Check Pending Policy Acknowledgements")
        print("2. Acknowledge HR Policy ('Yes, I acknowledge the policy')")
        print("3. Schedule a Reminder ('PF nomination form' or 'Salary slip' for tomorrow 10 AM)")
        print("4. View All My Scheduled Reminders")
        print("5. Acknowledge a Scheduled Reminder ('Yes, I've completed it')")
        print("6. Check September 2026 Payslip Status")
        print("7. View Trial Questions Guide")
        print("8. Exit")

        choice = input("\nEnter choice (1-8): ").strip()
        if choice == "1":
            res = await check_pending_policy_acknowledgements(emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "2":
            pol = input("Policy name (default: Work From Home Policy): ").strip() or "Work From Home Policy"
            res = await acknowledge_hr_policy(pol, emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "3":
            topic = input("Reminder topic (e.g. PF nomination form / salary slip): ").strip() or "PF nomination form"
            time_str = input("When to remind (default: tomorrow at 10 AM): ").strip() or "tomorrow at 10 AM"
            res = await schedule_employee_reminder(topic, time_str, emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "4":
            res = await check_my_scheduled_reminders(emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "5":
            topic = input("Reminder topic/title to acknowledge (e.g. PF / salary): ").strip() or "PF"
            ans = input("Your answer (default: Yes, I've submitted it): ").strip() or "Yes, I've submitted it"
            res = await acknowledge_scheduled_reminder(topic, ans, emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "6":
            res = await check_payslip_status("September 2026", emp_id, ctx)
            print(f"\n🤖 AI: \"{res}\"")
        elif choice == "7":
            show_trial_questions()
        elif choice == "8":
            print("Exiting trial.")
            break
        else:
            print("Invalid option. Please choose 1-8.")


if __name__ == "__main__":
    if "--questions" in sys.argv:
        show_trial_questions()
    elif "--interactive" in sys.argv:
        asyncio.run(run_interactive())
    else:
        asyncio.run(run_automated_trial())
        show_trial_questions()
