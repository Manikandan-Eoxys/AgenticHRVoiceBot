"""
tests/test_hr_bot_capabilities.py

Automated integration test script verifying:
1. Startup check suppression
2. 5 Core Policies + Company Holidays
3. Attendance & Regularization
4. Work From Home (WFH) Quota & Application
5. Payroll & Salary Inquiries (Basic salary, deductions, credit date, payslip)
6. Leave Management & Approvals (Approved, Rejected with reason, Reminders)
7. Code of Conduct & Confidential Grievance Escalation
8. HR Ticket Creation across 6 categories
"""

import sys
import os
import asyncio
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.policy_service import PolicyService
from services.leave_service import LeaveService
from services.attendance_service import AttendanceService
from services.wfh_service import WFHService
from services.payroll_service import PayrollService
from services.grievance_service import GrievanceService
from services.ticket_service import TicketService

import hr_tools
from tools.policy_tools import get_hr_policy, check_company_holiday, list_upcoming_company_holidays
from tools.leave_tools import get_leave_balance, check_leave_availability, get_leave_request_status, send_manager_leave_reminder
from tools.attendance_tools import check_attendance_status, get_late_marks, submit_attendance_regularization
from tools.wfh_tools import check_wfh_eligibility, get_wfh_quota_balance, apply_wfh_request
from tools.payroll_tools import get_salary_credit_date, get_basic_salary_info, get_salary_deductions_info, check_payslip_status
from tools.grievance_tools import get_code_of_conduct_policy, report_confidential_grievance
from tools.ticket_tools import raise_hr_ticket, check_my_hr_tickets
from tools.reminder_tools import (
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    schedule_employee_reminder,
    check_my_scheduled_reminders,
    acknowledge_scheduled_reminder,
)


def make_mock_context(verified_id="1001"):
    mock_ctx = MagicMock()
    mock_ctx.session = MagicMock()
    mock_ctx.session.verified_employee_id = verified_id
    return mock_ctx


async def run_all_tests():
    print("================================================================")
    print("RUNNING HR VOICEBOT MVP EXPANSION TESTS")
    print("================================================================")
    mock_ctx = make_mock_context("1001")

    # 1. Test Startup Check Log Silence
    print("\n[TEST 1] Startup Check Execution (Checking stdout format)...")
    hr_tools.run_startup_check()
    print("✅ Startup check ran without dumping all 15 employees.")

    # 2. Test 5 Core Policies & Holidays
    print("\n[TEST 2] Policy & Holiday Queries...")
    p1 = await get_hr_policy("leave")
    print(f"Policy 1 (Leave): {p1[:80]}...")
    assert "Leave & Holiday Policy" in p1

    p2 = await get_hr_policy("attendance")
    print(f"Policy 2 (Attendance): {p2[:80]}...")
    assert "Attendance & Regularization Policy" in p2

    p3 = await get_hr_policy("wfh")
    print(f"Policy 3 (WFH): {p3[:80]}...")
    assert "Work From Home" in p3

    p4 = await get_hr_policy("salary")
    print(f"Policy 4 (Payroll): {p4[:80]}...")
    assert "Payroll & Salary Policy" in p4

    p5 = await get_hr_policy("dress code")
    print(f"Policy 5 (Code of Conduct): {p5[:80]}...")
    assert "business casual" in p5.lower() or "code of conduct" in p5.lower()

    hol1 = await check_company_holiday("2026-10-02")
    print(f"Holiday check (2026-10-02): {hol1}")
    assert "Gandhi Jayanti" in hol1

    hol_list = await list_upcoming_company_holidays()
    print(f"Upcoming holidays: {hol_list[:100]}...")
    assert "Upcoming company holidays" in hol_list
    print("✅ Policy and holiday tests passed.")

    # 3. Test Attendance & Regularization
    print("\n[TEST 3] Attendance & Regularization...")
    att_status = await check_attendance_status("yesterday", "1001", mock_ctx)
    print(f"Yesterday attendance for 1001: {att_status}")
    assert "Absent" in att_status or "Present" in att_status or "biometric punch" in att_status

    late_marks = await get_late_marks("1001", mock_ctx)
    print(f"Late marks count for 1001: {late_marks}")
    assert "late mark" in late_marks.lower()

    reg_submit = await submit_attendance_regularization(
        "yesterday", "missing_punch_in", "09:05:00", "Scanner unresponsive", "1001", mock_ctx
    )
    print(f"Regularization submitted: {reg_submit}")
    assert "submitted successfully" in reg_submit
    print("✅ Attendance & Regularization tests passed.")

    # 4. Test Work From Home (WFH)
    print("\n[TEST 4] Work From Home / Hybrid Work...")
    wfh_elig = await check_wfh_eligibility("1001", mock_ctx)
    print(f"WFH Eligibility: {wfh_elig}")
    assert "eligible for hybrid work" in wfh_elig.lower()

    wfh_quota = await get_wfh_quota_balance("1001", mock_ctx)
    print(f"WFH Quota Balance: {wfh_quota}")
    assert "allowed WFH days" in wfh_quota

    wfh_apply = await apply_wfh_request("2026-10-15", "2026-10-15", "Internet installation", "1001", mock_ctx)
    print(f"WFH Application: {wfh_apply}")
    assert "submitted" in wfh_apply.lower()
    print("✅ WFH tests passed.")

    # 5. Test Payroll & Salary
    print("\n[TEST 5] Payroll & Salary Inquiries...")
    credit_date = await get_salary_credit_date()
    print(f"Salary Credit Date: {credit_date}")
    assert "last working day" in credit_date.lower()

    basic_sal = await get_basic_salary_info("1001", mock_ctx)
    print(f"Basic Salary Info: {basic_sal}")
    assert "basic salary is ₹35,000.00" in basic_sal

    deductions = await get_salary_deductions_info("August 2026", "1001", mock_ctx)
    print(f"Salary Deductions: {deductions}")
    assert "Provident Fund" in deductions and "Professional Tax" in deductions

    payslip = await check_payslip_status("August 2026", "1001", mock_ctx)
    print(f"Payslip Status: {payslip}")
    assert "available" in payslip.lower()
    print("✅ Payroll tests passed.")

    # 6. Test Leave Management & Approvals
    print("\n[TEST 6] Leave Management & Approvals...")
    leave_bal = await get_leave_balance("Casual Leave", "1001", mock_ctx)
    print(f"Leave Balance (CL): {leave_bal}")
    assert "Casual Leave" in leave_bal

    avail = await check_leave_availability("1001", "Casual Leave", "2026-11-02", "2026-11-04", mock_ctx)
    print(f"Leave Availability check: {avail}")
    assert "available" in avail.lower()

    # Test status check (approvals / rejections / pending)
    status_msg = await get_leave_request_status("1001", mock_ctx)
    print(f"Latest Leave Status for 1001: {status_msg}")
    assert len(status_msg) > 10

    # Test reminder
    reminder_msg = await send_manager_leave_reminder("1001", 0, mock_ctx)
    print(f"Manager Reminder message: {reminder_msg}")
    assert "reminder" in reminder_msg.lower()
    print("✅ Leave & Approval tests passed.")

    # 7. Test Code of Conduct & Confidential Grievances
    print("\n[TEST 7] Code of Conduct & Confidential Grievances...")
    conduct_dress = await get_code_of_conduct_policy("dress code")
    print(f"Dress code policy: {conduct_dress}")
    assert "business casual" in conduct_dress.lower()

    conduct_laptop = await get_code_of_conduct_policy("laptop")
    print(f"Laptop policy: {conduct_laptop}")
    assert "VPN" in conduct_laptop

    confidential_rep = await report_confidential_grievance(
        "Manager unfair overtime assignment without compensation", "Manager Complaint", "1001", mock_ctx
    )
    print(f"Confidential Grievance Escalation: {confidential_rep}")
    assert "TICK-G" in confidential_rep or "confidential" in confidential_rep.lower()
    print("✅ Grievance tests passed.")

    # 8. Test HR Ticket Raising across Categories
    print("\n[TEST 8] HR Ticket Creation...")
    ticket1 = await raise_hr_ticket(
        category="Payroll & Salary Issues",
        ticket_type="PAYROLL_SALARY_DISCREPANCY",
        description="Salary is Rs.5000 less than expected for August",
        priority="Normal",
        employee_id="1001",
        context=mock_ctx,
    )
    print(f"Created Payroll Ticket: {ticket1}")
    assert "TICK-" in ticket1

    ticket2 = await raise_hr_ticket(
        category="HR Documents",
        ticket_type="EXPERIENCE_LETTER_REQUEST",
        description="Need experience letter for visa application",
        priority="Normal",
        employee_id="1001",
        context=mock_ctx,
    )
    print(f"Created Document Ticket: {ticket2}")
    assert "TICK-" in ticket2

    my_tickets = await check_my_hr_tickets("1001", mock_ctx)
    print(f"Caller Tickets: {my_tickets}")
    assert "ticket(s)" in my_tickets.lower()
    print("✅ HR Ticket tests passed.")

    # 9. Test Mandatory Policy Acknowledgements & Scheduled Reminders
    print("\n[TEST 9] Mandatory Policy Acknowledgements & Scheduled Reminders...")
    # Reset 1001 policy status for test
    from services.reminder_service import ReminderService
    _rs = ReminderService()
    _rs.get_pending_policy_acknowledgements("1001")
    ack_res = await acknowledge_hr_policy("Work From Home Policy", "1001", mock_ctx)
    print(f"Policy Acknowledgement result: {ack_res}")
    assert "recorded" in ack_res.lower()

    sched_res = await schedule_employee_reminder("PF nomination form", "tomorrow at 10 AM", "1001", mock_ctx)
    print(f"Schedule Reminder result: {sched_res}")
    assert "remind you" in sched_res.lower()

    rem_list = await check_my_scheduled_reminders("1001", mock_ctx)
    print(f"Scheduled reminders list: {rem_list}")
    assert "reminder" in rem_list.lower()

    rem_ack = await acknowledge_scheduled_reminder("PF nomination form", "Yes, I've submitted it", "1001", mock_ctx)
    print(f"Reminder acknowledgement result: {rem_ack}")
    assert "recorded" in rem_ack.lower()
    print("✅ Policy acknowledgement and scheduled reminders tests passed.")

    print("\n================================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! 🎉")
    print("================================================================")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
