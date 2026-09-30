"""
check_db.py

Helper script to quickly view all HR Voicebot database tables and contents.
Usage:
    ./.venv/bin/python check_db.py
"""

import os
import sys
import mysql.connector

DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "hrbot"),
    "password": os.environ.get("MYSQL_PASSWORD", "Eoxys@110"),
    "database": os.environ.get("MYSQL_DATABASE", "hr_voicebot"),
}

def print_table(title, rows, columns):
    print(f"\n{'='*70}")
    print(f"  {title.upper()} ({len(rows)} rows)")
    print(f"{'='*70}")
    if not rows:
        print("  (No records found)")
        return

    # Calculate column widths
    col_widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val_str = str(row.get(col, ""))
            if len(val_str) > 40:
                val_str = val_str[:37] + "..."
            col_widths[col] = max(col_widths[col], len(val_str))

    header = " | ".join(f"{col.ljust(col_widths[col])}" for col in columns)
    sep = "-+-".join("-" * col_widths[col] for col in columns)
    print(header)
    print(sep)

    for row in rows:
        line_parts = []
        for col in columns:
            val_str = str(row.get(col, ""))
            if len(val_str) > 40:
                val_str = val_str[:37] + "..."
            line_parts.append(val_str.ljust(col_widths[col]))
        print(" | ".join(line_parts))

def main():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"❌ Failed to connect to MySQL: {e}")
        return

    try:
        # 1. Policies
        cur.execute("SELECT policy_code, policy_name, category, summary FROM hr_policies")
        print_table("1. Core HR Policies", cur.fetchall(), ["policy_code", "policy_name", "category", "summary"])

        # 2. Holidays
        cur.execute("SELECT holiday_date, holiday_day, holiday_name FROM company_holidays ORDER BY holiday_date")
        print_table("2. Company Holidays (2026)", cur.fetchall(), ["holiday_date", "holiday_day", "holiday_name"])

        # 3. Attendance Records
        cur.execute("SELECT record_date, employee_id, check_in, check_out, status, late_minutes, remarks FROM attendance_records ORDER BY record_date DESC LIMIT 10")
        print_table("3. Recent Attendance Records", cur.fetchall(), ["record_date", "employee_id", "status", "check_in", "late_minutes", "remarks"])

        # 4. Attendance Regularizations
        cur.execute("SELECT regularization_id, employee_id, attendance_date, request_type, status, reason FROM attendance_regularizations")
        print_table("4. Attendance Regularization Requests", cur.fetchall(), ["regularization_id", "employee_id", "attendance_date", "request_type", "status", "reason"])

        # 5. WFH Requests
        cur.execute("SELECT wfh_id, employee_id, start_date, end_date, days_count, status, reason FROM wfh_requests")
        print_table("5. Work From Home (WFH) Requests", cur.fetchall(), ["wfh_id", "employee_id", "start_date", "days_count", "status", "reason"])

        # 6. Payroll Records
        cur.execute("SELECT employee_id, month_year, basic_salary, gross_salary, net_salary, payment_status, payslip_available FROM payroll_records")
        print_table("6. Monthly Payroll Records", cur.fetchall(), ["employee_id", "month_year", "basic_salary", "gross_salary", "net_salary", "payment_status", "payslip_available"])

        # 7. Leave Requests & Approvals
        cur.execute("SELECT request_id, employee_id, leave_code, start_date, end_date, days_requested, status, rejection_reason FROM leave_requests ORDER BY request_id DESC")
        print_table("7. Leave Requests & Approval Status", cur.fetchall(), ["request_id", "employee_id", "leave_code", "start_date", "end_date", "status", "rejection_reason"])

        # 8. HR Tickets
        cur.execute("SELECT ticket_number, employee_id, category, ticket_type, status, priority FROM hr_tickets ORDER BY ticket_id DESC LIMIT 10")
        print_table("8. HR Support Tickets", cur.fetchall(), ["ticket_number", "employee_id", "category", "ticket_type", "status", "priority"])

        # 9. Confidential Grievances
        cur.execute("SELECT grievance_id, employee_id, title, status, priority, created_at FROM grievances")
        print_table("9. Confidential Grievances", cur.fetchall(), ["grievance_id", "employee_id", "title", "status", "priority"])

        # 10. Mandatory HR Policy Acknowledgements
        cur.execute("SELECT ack_id, employee_id, policy_name, deadline_date, status, acknowledged_at, acknowledgement_note FROM policy_acknowledgements")
        print_table("10. Mandatory Policy Acknowledgements", cur.fetchall(), ["ack_id", "employee_id", "policy_name", "deadline_date", "status", "acknowledged_at", "acknowledgement_note"])

        # 11. Scheduled Reminders & Messages
        cur.execute("SELECT reminder_id, employee_id, reminder_type, title, scheduled_time, status, employee_response FROM scheduled_reminders")
        print_table("11. Scheduled Reminders & Messages", cur.fetchall(), ["reminder_id", "employee_id", "reminder_type", "title", "scheduled_time", "status", "employee_response"])

        print("\n" + "="*70)
        print("  Database check completed successfully.")
        print("="*70 + "\n")

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
