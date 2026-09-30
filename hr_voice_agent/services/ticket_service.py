"""
services/ticket_service.py

Service for raising and managing HR tickets when human intervention is required.

Supported 6 Categories & Ticket Types:
1. Payroll & Salary Issues:
   - PAYROLL_SALARY_DISCREPANCY
   - SALARY_NOT_CREDITED
   - SALARY_DEDUCTION_QUERY
   - PAYSLIP_NOT_AVAILABLE
   - PAYSLIP_CORRECTION
   - BONUS_INCENTIVE_DISCREPANCY
   - TAX_DEDUCTION_QUERY
2. Attendance Issues:
   - ATTENDANCE_REGULARIZATION
   - ATTENDANCE_STATUS_CORRECTION
   - ATTENDANCE_CORRECTION
   - BIOMETRIC_ISSUE
   - ATTENDANCE_SYSTEM_ISSUE
   - LATE_MARK_DISPUTE
3. Leave Issues:
   - LEAVE_REQUEST_ISSUE
   - LEAVE_BALANCE_DISCREPANCY
   - LEAVE_APPROVAL_DELAY
   - LEAVE_CANCELLATION_ISSUE
   - LEAVE_EXCEPTION_REQUEST
   - EMERGENCY_LEAVE_REQUEST
4. HRMS / Employee Profile Issues:
   - EMPLOYEE_DATA_CORRECTION
   - PERSONAL_DETAILS_CORRECTION
   - ADDRESS_UPDATE_ISSUE
   - BANK_DETAILS_UPDATE
   - EMERGENCY_CONTACT_UPDATE
   - EMPLOYEE_ID_ISSUE
5. HR Documents:
   - EXPERIENCE_LETTER_REQUEST
   - EMPLOYMENT_CERTIFICATE_REQUEST
   - SALARY_CERTIFICATE_REQUEST
   - RELIEVING_LETTER_REQUEST
   - HR_DOCUMENT_CORRECTION
6. Work From Home / Hybrid Work:
   - WFH_EXCEPTION_REQUEST
   - WFH_APPROVAL_DELAY
   - WFH_SYSTEM_ISSUE
   - HYBRID_WORK_ISSUE
"""

import logging
import random
import time
from database.db import get_db_connection

logger = logging.getLogger("ticket-service")

VALID_TICKET_CATEGORIES = {
    "Payroll & Salary Issues": [
        "PAYROLL_SALARY_DISCREPANCY",
        "SALARY_NOT_CREDITED",
        "SALARY_DEDUCTION_QUERY",
        "PAYSLIP_NOT_AVAILABLE",
        "PAYSLIP_CORRECTION",
        "BONUS_INCENTIVE_DISCREPANCY",
        "TAX_DEDUCTION_QUERY",
    ],
    "Attendance Issues": [
        "ATTENDANCE_REGULARIZATION",
        "ATTENDANCE_STATUS_CORRECTION",
        "ATTENDANCE_CORRECTION",
        "BIOMETRIC_ISSUE",
        "ATTENDANCE_SYSTEM_ISSUE",
        "LATE_MARK_DISPUTE",
    ],
    "Leave Issues": [
        "LEAVE_REQUEST_ISSUE",
        "LEAVE_BALANCE_DISCREPANCY",
        "LEAVE_APPROVAL_DELAY",
        "LEAVE_CANCELLATION_ISSUE",
        "LEAVE_EXCEPTION_REQUEST",
        "EMERGENCY_LEAVE_REQUEST",
    ],
    "HRMS / Employee Profile Issues": [
        "EMPLOYEE_DATA_CORRECTION",
        "PERSONAL_DETAILS_CORRECTION",
        "ADDRESS_UPDATE_ISSUE",
        "BANK_DETAILS_UPDATE",
        "EMERGENCY_CONTACT_UPDATE",
        "EMPLOYEE_ID_ISSUE",
    ],
    "HR Documents": [
        "EXPERIENCE_LETTER_REQUEST",
        "EMPLOYMENT_CERTIFICATE_REQUEST",
        "SALARY_CERTIFICATE_REQUEST",
        "RELIEVING_LETTER_REQUEST",
        "HR_DOCUMENT_CORRECTION",
    ],
    "Work From Home / Hybrid Work": [
        "WFH_EXCEPTION_REQUEST",
        "WFH_APPROVAL_DELAY",
        "WFH_SYSTEM_ISSUE",
        "HYBRID_WORK_ISSUE",
    ],
}


class TicketService:

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return rows
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
            result = cursor.lastrowid if cursor.lastrowid else cursor.rowcount
            cursor.close()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _match_category_and_type(self, category_hint: str, type_hint: str) -> tuple[str, str]:
        c_hint = (category_hint or "").strip().lower()
        t_hint = (type_hint or "").strip().upper().replace(" ", "_")

        matched_cat = None
        matched_type = None

        # Check direct type match
        for cat, types in VALID_TICKET_CATEGORIES.items():
            if t_hint in types:
                matched_cat = cat
                matched_type = t_hint
                break

        # If not matched directly, check partial type match
        if not matched_type:
            for cat, types in VALID_TICKET_CATEGORIES.items():
                for t in types:
                    if t_hint in t or t in t_hint:
                        matched_cat = cat
                        matched_type = t
                        break
                if matched_type:
                    break

        # If category specified
        if not matched_cat and c_hint:
            for cat in VALID_TICKET_CATEGORIES:
                if any(word in cat.lower() for word in c_hint.split()):
                    matched_cat = cat
                    break

        # Fallback defaults
        if not matched_cat:
            matched_cat = "Payroll & Salary Issues"
        if not matched_type:
            matched_type = VALID_TICKET_CATEGORIES[matched_cat][0]

        return matched_cat, matched_type

    def create_ticket(
        self,
        employee_id: str,
        category: str,
        ticket_type: str,
        description: str,
        priority: str = "Normal"
    ) -> dict:
        """Creates a new HR ticket in hr_tickets with a unique ticket number."""
        cat, t_type = self._match_category_and_type(category, ticket_type)

        # Generate ticket reference e.g. TICK-2026-XXXX
        random_suffix = random.randint(1000, 9999)
        ticket_number = f"TICK-{random_suffix}"

        # Ensure uniqueness
        for _ in range(5):
            existing = self._query("SELECT ticket_id FROM hr_tickets WHERE ticket_number = %s", (ticket_number,))
            if not existing:
                break
            random_suffix = random.randint(1000, 9999)
            ticket_number = f"TICK-{random_suffix}"

        ticket_id = self._execute(
            """INSERT INTO hr_tickets (ticket_number, employee_id, category, ticket_type, description, status, priority)
               VALUES (%s, %s, %s, %s, %s, 'Open', %s)""",
            (ticket_number, str(employee_id), cat, t_type, description or "Assistance requested", priority)
        )

        friendly_type = t_type.replace("_", " ").title()
        return {
            "success": True,
            "ticket_id": ticket_id,
            "ticket_number": ticket_number,
            "category": cat,
            "ticket_type": t_type,
            "priority": priority,
            "status": "Open",
            "message": f"I have created HR ticket {ticket_number} under {cat} for '{friendly_type}'. Our HR operations team will review this and follow up with you."
        }

    def get_employee_tickets(self, employee_id: str, status_filter: str = "all") -> dict:
        """Lists tickets raised by this employee."""
        if status_filter and status_filter.lower() != "all":
            rows = self._query(
                """SELECT ticket_number, category, ticket_type, description, status, priority, created_at
                   FROM hr_tickets
                   WHERE employee_id = %s AND LOWER(status) = %s
                   ORDER BY ticket_id DESC LIMIT 5""",
                (str(employee_id), status_filter.lower())
            )
        else:
            rows = self._query(
                """SELECT ticket_number, category, ticket_type, description, status, priority, created_at
                   FROM hr_tickets
                   WHERE employee_id = %s
                   ORDER BY ticket_id DESC LIMIT 5""",
                (str(employee_id),)
            )

        if not rows:
            return {"success": False, "message": "You currently have no HR tickets on file."}

        summary_list = []
        for r in rows:
            summary_list.append(f"{r['ticket_number']} ({r['ticket_type'].replace('_', ' ').title()} - {r['status']})")

        return {
            "success": True,
            "tickets": rows,
            "message": f"You have {len(rows)} ticket(s): {', '.join(summary_list)}."
        }
