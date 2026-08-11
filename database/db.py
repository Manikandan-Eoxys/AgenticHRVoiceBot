"""
database/db.py

HR + Workforce Attendance Database Schema
Supports:
  - Employee roles (Engineer / FieldManager / AreaManager)
  - Team-based field workforce
  - Absence management with coverage threshold enforcement
  - Exception routing to Area Managers
  - IFS Cloud & SQL Server sync audit logs
  - Roster scheduling
"""

import os
import sys
import sqlite3

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import Config

DATABASE = Config.DATABASE_PATH


def create_database():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Drop all tables (fresh init)
    tables = [
        "sql_server_sync_log",
        "ifs_sync_log",
        "exception_requests",
        "roster_schedules",
        "team_coverage",
        "calendar_events",
        "grievances",
        "leave_requests",
        "leave_balance",
        "employees",
        "policies",
    ]
    for tbl in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {tbl}")

    # =========================================================
    # 1. Employees Table
    #    role: 'Engineer' | 'FieldManager' | 'AreaManager' | 'Admin'
    #    field_manager_id: FK to self (Engineer → their FM)
    #    area_manager_id: FK to self (FM → their AM)
    #    team_id: integer grouping per FM's team
    # =========================================================
    cursor.execute("""
    CREATE TABLE employees (
        employee_id       INTEGER PRIMARY KEY,
        name              TEXT    NOT NULL,
        email             TEXT    NOT NULL,
        manager           TEXT,
        department        TEXT,
        designation       TEXT,
        joining_date      TEXT,
        years_of_service  INTEGER DEFAULT 0,
        role              TEXT    NOT NULL DEFAULT 'Engineer',
        field_manager_id  INTEGER,
        area_manager_id   INTEGER,
        team_id           INTEGER DEFAULT 1,
        phone             TEXT,
        FOREIGN KEY(field_manager_id) REFERENCES employees(employee_id),
        FOREIGN KEY(area_manager_id)  REFERENCES employees(employee_id)
    )
    """)

    # =========================================================
    # 2. Leave Balance Table
    # =========================================================
    cursor.execute("""
    CREATE TABLE leave_balance (
        employee_id  INTEGER PRIMARY KEY,
        casual       INTEGER DEFAULT 12,
        sick         INTEGER DEFAULT 8,
        earned       INTEGER DEFAULT 15,
        FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # 3. Leave Requests Table (extended)
    #    absence_type: 'Sickness' | 'Holiday' | 'Emergency' | 'Funeral' | 'Casual' | 'Earned'
    #    exception_flag: 1 if this request needs Area Manager approval
    #    area_manager_id: AM to whom exception is routed
    #    area_manager_status: 'NotRequired' | 'Pending' | 'Approved' | 'Rejected'
    #    ifs_cloud_ref: IFS Cloud reference ID after sync
    #    ifs_sync_status: 'pending' | 'synced' | 'failed'
    #    sql_sync_status: 'pending' | 'synced' | 'failed'
    #    submitted_at: ISO timestamp when request was submitted
    # =========================================================
    cursor.execute("""
    CREATE TABLE leave_requests (
        request_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id         INTEGER NOT NULL,
        from_date           TEXT    NOT NULL,
        to_date             TEXT    NOT NULL,
        leave_type          TEXT    NOT NULL DEFAULT 'Casual',
        absence_type        TEXT    NOT NULL DEFAULT 'Casual',
        status              TEXT    NOT NULL DEFAULT 'Pending',
        exception_flag      INTEGER NOT NULL DEFAULT 0,
        area_manager_id     INTEGER,
        area_manager_status TEXT    NOT NULL DEFAULT 'NotRequired',
        ifs_cloud_ref       TEXT,
        ifs_sync_status     TEXT    NOT NULL DEFAULT 'pending',
        sql_sync_status     TEXT    NOT NULL DEFAULT 'pending',
        reason              TEXT,
        submitted_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(employee_id)     REFERENCES employees(employee_id) ON DELETE CASCADE,
        FOREIGN KEY(area_manager_id) REFERENCES employees(employee_id)
    )
    """)

    # =========================================================
    # 4. Team Coverage Table
    #    Tracks daily availability per Field Manager's team
    #    coverage_pct = (total_engineers - absent_count) / total_engineers
    # =========================================================
    cursor.execute("""
    CREATE TABLE team_coverage (
        coverage_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        field_manager_id  INTEGER NOT NULL,
        coverage_date     TEXT    NOT NULL,
        total_engineers   INTEGER NOT NULL DEFAULT 0,
        absent_count      INTEGER NOT NULL DEFAULT 0,
        coverage_pct      REAL    NOT NULL DEFAULT 1.0,
        below_threshold   INTEGER NOT NULL DEFAULT 0,
        recorded_at       TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(field_manager_id) REFERENCES employees(employee_id),
        UNIQUE(field_manager_id, coverage_date)
    )
    """)

    # =========================================================
    # 5. Exception Requests Table
    #    Routes exceptional absence requests to Area Manager
    # =========================================================
    cursor.execute("""
    CREATE TABLE exception_requests (
        exception_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        leave_request_id    INTEGER NOT NULL,
        employee_id         INTEGER NOT NULL,
        field_manager_id    INTEGER,
        area_manager_id     INTEGER NOT NULL,
        exception_reason    TEXT    NOT NULL,
        exception_type      TEXT    NOT NULL DEFAULT 'CoverageThreshold',
        status              TEXT    NOT NULL DEFAULT 'Pending',
        am_decision         TEXT,
        am_notes            TEXT,
        created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
        decided_at          TEXT,
        notified_employee   INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(leave_request_id) REFERENCES leave_requests(request_id) ON DELETE CASCADE,
        FOREIGN KEY(employee_id)      REFERENCES employees(employee_id),
        FOREIGN KEY(field_manager_id) REFERENCES employees(employee_id),
        FOREIGN KEY(area_manager_id)  REFERENCES employees(employee_id)
    )
    """)

    # =========================================================
    # 6. Roster Schedules Table
    #    Daily shift assignments per engineer
    # =========================================================
    cursor.execute("""
    CREATE TABLE roster_schedules (
        schedule_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id    INTEGER NOT NULL,
        schedule_date  TEXT    NOT NULL,
        shift_start    TEXT    DEFAULT '09:00',
        shift_end      TEXT    DEFAULT '17:00',
        status         TEXT    NOT NULL DEFAULT 'Scheduled',
        notes          TEXT,
        created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE,
        UNIQUE(employee_id, schedule_date)
    )
    """)

    # =========================================================
    # 7. Grievances Table
    # =========================================================
    cursor.execute("""
    CREATE TABLE grievances (
        grievance_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id   INTEGER NOT NULL,
        title         TEXT    NOT NULL,
        description   TEXT    NOT NULL,
        status        TEXT    NOT NULL DEFAULT 'Open',
        created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # 8. Calendar Events Table
    # =========================================================
    cursor.execute("""
    CREATE TABLE calendar_events (
        event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        title       TEXT    NOT NULL,
        event_date  TEXT    NOT NULL,
        event_time  TEXT,
        duration    INTEGER DEFAULT 30,
        location    TEXT,
        attendees   TEXT,
        status      TEXT    NOT NULL DEFAULT 'Scheduled',
        FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # 9. Policies Table
    # =========================================================
    cursor.execute("""
    CREATE TABLE policies (
        policy_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_name  TEXT    UNIQUE NOT NULL,
        category     TEXT,
        description  TEXT    NOT NULL
    )
    """)

    # =========================================================
    # 10. IFS Cloud Sync Log
    # =========================================================
    cursor.execute("""
    CREATE TABLE ifs_sync_log (
        log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        leave_request_id INTEGER NOT NULL,
        sync_status      TEXT    NOT NULL DEFAULT 'pending',
        ifs_cloud_ref    TEXT,
        error_message    TEXT,
        attempt_count    INTEGER NOT NULL DEFAULT 0,
        last_attempt_at  TEXT,
        synced_at        TEXT,
        FOREIGN KEY(leave_request_id) REFERENCES leave_requests(request_id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # 11. SQL Server Sync Log
    # =========================================================
    cursor.execute("""
    CREATE TABLE sql_server_sync_log (
        log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        leave_request_id INTEGER NOT NULL,
        sync_status      TEXT    NOT NULL DEFAULT 'pending',
        sql_record_id    TEXT,
        error_message    TEXT,
        attempt_count    INTEGER NOT NULL DEFAULT 0,
        last_attempt_at  TEXT,
        synced_at        TEXT,
        FOREIGN KEY(leave_request_id) REFERENCES leave_requests(request_id) ON DELETE CASCADE
    )
    """)

    # =========================================================
    # SEED DATA
    # =========================================================

    from datetime import datetime
    now = datetime.now()

    # ---------------------------------------------------------
    # Org hierarchy:
    #   Area Manager (AM): employee_id=2001 (James Wright)
    #   Field Managers (FM): 2002 (Sarah Collins, team 1), 2003 (Mike Patel, team 2)
    #   Engineers (team 1 under Sarah): 1001-1007 (7 engineers)
    #   Engineers (team 2 under Mike):  1008-1012 (5 engineers)
    #   Plus original HR/Finance staff: retained as Admin role
    # ---------------------------------------------------------

    def years_since(date_str):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return max(0, (now - d).days // 365)

    raw_employees = [
        # id,    name,               email,                        manager,       dept,        designation,          joining,       role,          fm_id,  am_id,  team, phone
        (2001, "James Wright",     "james@fieldops.com",     None,          "Operations", "Area Manager",       "2018-01-10",  "AreaManager",   None, None,   0,    "07700900001"),
        (2002, "Sarah Collins",    "sarah@fieldops.com",     "James Wright","Operations", "Field Manager",      "2019-03-15",  "FieldManager",  None, 2001,   1,    "07700900002"),
        (2003, "Mike Patel",       "mike@fieldops.com",      "James Wright","Operations", "Field Manager",      "2019-06-01",  "FieldManager",  None, 2001,   2,    "07700900003"),
        # Team 1 engineers (under Sarah Collins, team_id=1)
        (1001, "Ravi Kumar",       "ravi@fieldops.com",      "Sarah Collins","Field Ops", "Field Engineer",     "2022-03-15",  "Engineer",      2002, 2001,   1,    "07700900101"),
        (1002, "Priya Singh",      "priya@fieldops.com",     "Sarah Collins","Field Ops", "Field Engineer",     "2021-06-01",  "Engineer",      2002, 2001,   1,    "07700900102"),
        (1003, "Amit Patel",       "amit@fieldops.com",      "Sarah Collins","Field Ops", "Senior Engineer",    "2020-11-10",  "Engineer",      2002, 2001,   1,    "07700900103"),
        (1004, "Ananya Roy",       "ananya@fieldops.com",    "Sarah Collins","Field Ops", "Field Engineer",     "2023-01-20",  "Engineer",      2002, 2001,   1,    "07700900104"),
        (1005, "Rajesh Gupta",     "rajesh@fieldops.com",    "Sarah Collins","Field Ops", "Tech Lead",          "2020-09-05",  "Engineer",      2002, 2001,   1,    "07700900105"),
        (1006, "Sneha Reddy",      "sneha@fieldops.com",     "Sarah Collins","Field Ops", "Field Engineer",     "2022-02-14",  "Engineer",      2002, 2001,   1,    "07700900106"),
        (1007, "Vikram Malhotra",  "vikram@fieldops.com",    "Sarah Collins","Field Ops", "Field Engineer",     "2021-04-10",  "Engineer",      2002, 2001,   1,    "07700900107"),
        # Team 2 engineers (under Mike Patel, team_id=2)
        (1008, "Pooja Joshi",      "pooja@fieldops.com",     "Mike Patel",  "Field Ops", "Field Engineer",     "2022-01-10",  "Engineer",      2003, 2001,   2,    "07700900108"),
        (1009, "Suresh Nair",      "suresh@fieldops.com",    "Mike Patel",  "Field Ops", "Field Engineer",     "2023-08-15",  "Engineer",      2003, 2001,   2,    "07700900109"),
        (1010, "Meera Kapoor",     "meera@fieldops.com",     "Mike Patel",  "Field Ops", "Senior Engineer",    "2021-10-01",  "Engineer",      2003, 2001,   2,    "07700900110"),
        (1011, "Arjun Das",        "arjun@fieldops.com",     "Mike Patel",  "Field Ops", "Field Engineer",     "2022-07-05",  "Engineer",      2003, 2001,   2,    "07700900111"),
        (1012, "Kavya Sharma",     "kavya@fieldops.com",     "Mike Patel",  "Field Ops", "Field Engineer",     "2023-03-20",  "Engineer",      2003, 2001,   2,    "07700900112"),
    ]

    employees_data = []
    for emp in raw_employees:
        yrs = years_since(emp[6])
        employees_data.append((
            emp[0],  # employee_id
            emp[1],  # name
            emp[2],  # email
            emp[3],  # manager
            emp[4],  # department
            emp[5],  # designation
            emp[6],  # joining_date
            yrs,     # years_of_service
            emp[7],  # role
            emp[8],  # field_manager_id
            emp[9],  # area_manager_id
            emp[10], # team_id
            emp[11], # phone
        ))

    cursor.executemany("""
    INSERT OR REPLACE INTO employees
    (employee_id, name, email, manager, department, designation, joining_date,
     years_of_service, role, field_manager_id, area_manager_id, team_id, phone)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, employees_data)

    # Leave Balance for all employees
    leave_balance_data = [
        (2001, 12, 8, 15),
        (2002, 12, 8, 15),
        (2003, 12, 8, 15),
        (1001, 8,  5, 12),
        (1002, 10, 6, 15),
        (1003, 5,  3, 8),
        (1004, 12, 7, 14),
        (1005, 7,  4, 10),
        (1006, 11, 8, 18),
        (1007, 6,  2, 9),
        (1008, 9,  5, 11),
        (1009, 4,  6, 7),
        (1010, 12, 8, 16),
        (1011, 8,  5, 12),
        (1012, 10, 7, 14),
    ]
    cursor.executemany("""
    INSERT OR REPLACE INTO leave_balance (employee_id, casual, sick, earned)
    VALUES (?, ?, ?, ?)
    """, leave_balance_data)

    # Leave Requests with new columns
    # request_id, emp_id, from, to, leave_type, absence_type, status, exception_flag,
    # am_id, am_status, ifs_cloud_ref, ifs_sync_status, sql_sync_status, reason, submitted_at
    leave_requests_data = [
        (1, 1001, "2026-08-05", "2026-08-07", "Casual",  "Holiday",   "Approved",  0, None, "NotRequired", None, "synced", "synced", "Family holiday", "2026-08-01 09:00:00"),
        (2, 1002, "2026-07-10", "2026-07-12", "Sick",    "Sickness",  "Approved",  0, None, "NotRequired", None, "synced", "synced", "Flu",            "2026-07-10 07:30:00"),
        (3, 1003, "2026-08-15", "2026-08-18", "Casual",  "Holiday",   "Pending",   0, None, "NotRequired", None, "pending","pending","Annual leave",   "2026-08-10 10:00:00"),
        (4, 1004, "2026-09-01", "2026-09-05", "Earned",  "Holiday",   "Pending",   0, None, "NotRequired", None, "pending","pending","Wedding",        "2026-08-20 11:00:00"),
        (5, 1005, "2026-07-28", "2026-07-28", "Sick",    "Sickness",  "Approved",  0, None, "NotRequired", None, "synced", "synced", "Migraine",       "2026-07-28 06:45:00"),
        (6, 1006, "2026-08-04", "2026-08-04", "Casual",  "Emergency", "Approved",  1, 2001, "Approved",    None, "synced", "synced", "Family emergency","2026-08-01 08:00:00"),
        (7, 1007, "2026-08-11", "2026-08-11", "Casual",  "Funeral",   "Pending",   1, 2001, "Pending",     None, "pending","pending","Bereavement",    "2026-08-08 07:00:00"),
        (8, 1008, "2026-08-02", "2026-08-03", "Sick",    "Sickness",  "Approved",  0, None, "NotRequired", None, "synced", "synced", "Back injury",    "2026-08-02 07:00:00"),
        (9, 1009, "2026-08-18", "2026-08-22", "Earned",  "Holiday",   "Pending",   0, None, "NotRequired", None, "pending","pending","Summer holiday", "2026-08-10 09:30:00"),
        (10,1010, "2026-09-15", "2026-09-20", "Earned",  "Holiday",   "Pending",   0, None, "NotRequired", None, "pending","pending","Family trip",    "2026-09-01 10:00:00"),
    ]
    cursor.executemany("""
    INSERT OR REPLACE INTO leave_requests
    (request_id, employee_id, from_date, to_date, leave_type, absence_type, status,
     exception_flag, area_manager_id, area_manager_status, ifs_cloud_ref,
     ifs_sync_status, sql_sync_status, reason, submitted_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, leave_requests_data)

    # Exception Requests (2 examples)
    exception_data = [
        (1, 6, 1006, 2002, 2001, "Emergency leave submitted with <2 hours notice", "LastMinuteEmergency",
         "Approved", "Approved", "Genuine emergency confirmed by FM", "2026-08-01 08:00:00",
         "2026-08-01 10:30:00", 1),
        (2, 7, 1007, 2002, 2001, "Funeral leave requires Area Manager approval", "FuneralLeave",
         "Pending",  None,       None, "2026-08-08 07:00:00", None, 0),
    ]
    cursor.executemany("""
    INSERT OR REPLACE INTO exception_requests
    (exception_id, leave_request_id, employee_id, field_manager_id, area_manager_id,
     exception_reason, exception_type, status, am_decision, am_notes,
     created_at, decided_at, notified_employee)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, exception_data)

    # Team Coverage snapshot examples
    team_coverage_data = [
        (1, 2002, "2026-08-05", 7, 1, round(6/7, 4), 0),  # Sarah's team: 6/7 = 85.7%
        (2, 2002, "2026-08-06", 7, 1, round(6/7, 4), 0),
        (3, 2003, "2026-08-05", 5, 0, 1.0,            0),  # Mike's team: 5/5 = 100%
        (4, 2002, "2026-07-10", 7, 1, round(6/7, 4), 0),
    ]
    cursor.executemany("""
    INSERT OR REPLACE INTO team_coverage
    (coverage_id, field_manager_id, coverage_date, total_engineers,
     absent_count, coverage_pct, below_threshold)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, team_coverage_data)

    # Roster schedules for next 5 working days
    roster_data = []
    schedule_dates = [
        "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08",
    ]
    all_engineers = [1001,1002,1003,1004,1005,1006,1007,1008,1009,1010,1011,1012]
    for emp_id in all_engineers:
        for sd in schedule_dates:
            roster_data.append((emp_id, sd, "09:00", "17:00", "Scheduled", None))
    cursor.executemany("""
    INSERT OR IGNORE INTO roster_schedules
    (employee_id, schedule_date, shift_start, shift_end, status, notes)
    VALUES (?, ?, ?, ?, ?, ?)
    """, roster_data)

    # IFS Sync Log
    ifs_log_data = [
        (1, "synced",  "IFS-ABS-10001", None,      1, "2026-08-01 10:00:00", "2026-08-01 10:01:00"),
        (2, "synced",  "IFS-ABS-10002", None,      1, "2026-07-10 08:00:00", "2026-07-10 08:01:00"),
        (5, "synced",  "IFS-ABS-10003", None,      1, "2026-07-28 07:00:00", "2026-07-28 07:01:00"),
        (3, "pending", None,            None,      0, None,                   None),
        (4, "pending", None,            None,      0, None,                   None),
    ]
    cursor.executemany("""
    INSERT OR IGNORE INTO ifs_sync_log
    (leave_request_id, sync_status, ifs_cloud_ref, error_message, attempt_count,
     last_attempt_at, synced_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ifs_log_data)

    # SQL Server Sync Log
    sql_log_data = [
        (1, "synced",  "SQL-ATT-20001", None,     1, "2026-08-01 10:00:05", "2026-08-01 10:00:06"),
        (2, "synced",  "SQL-ATT-20002", None,     1, "2026-07-10 08:00:05", "2026-07-10 08:00:06"),
        (5, "synced",  "SQL-ATT-20003", None,     1, "2026-07-28 07:00:05", "2026-07-28 07:00:06"),
        (3, "pending", None,            None,     0, None,                   None),
        (4, "pending", None,            None,     0, None,                   None),
    ]
    cursor.executemany("""
    INSERT OR IGNORE INTO sql_server_sync_log
    (leave_request_id, sync_status, sql_record_id, error_message, attempt_count,
     last_attempt_at, synced_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, sql_log_data)

    # Grievances
    grievances_data = [
        (1001, "Overtime Dispute",    "Uncompensated weekend support hours", "Open",        "2026-07-18 16:45:00"),
        (1005, "Equipment Issue",     "Van battery failing on site visits",  "In Progress", "2026-07-20 09:00:00"),
        (1008, "Shift Allocation",    "Assigned consecutive night shifts",   "Open",        "2026-07-22 08:00:00"),
        (1002, "Safety Concern",      "PPE not provided for new job site",   "Resolved",    "2026-07-15 10:00:00"),
        (1009, "Target Dispute",      "KPI targets raised without notice",   "Open",        "2026-07-25 11:00:00"),
    ]
    cursor.executemany("""
    INSERT INTO grievances (employee_id, title, description, status, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, grievances_data)

    # Calendar Events
    calendar_events_data = [
        (2002, "Team Stand-up",           "2026-08-04", "09:00", 30,  "Site Office",    "Sarah, Team 1",        "Scheduled"),
        (2003, "Team Stand-up",           "2026-08-04", "09:00", 30,  "Site Office",    "Mike, Team 2",         "Scheduled"),
        (2001, "FM Weekly Review",        "2026-08-05", "14:00", 60,  "HQ Board Room",  "James, Sarah, Mike",   "Scheduled"),
        (1001, "Site Inspection",         "2026-08-06", "10:00", 120, "Site A",         "Ravi, Client",         "Scheduled"),
        (1005, "Tech Review",             "2026-08-07", "13:00", 60,  "Dev Room B",     "Rajesh, Pooja",        "Scheduled"),
        (2002, "Coverage Review",         "2026-08-08", "11:00", 45,  "Virtual",        "Sarah, James",         "Scheduled"),
    ]
    cursor.executemany("""
    INSERT INTO calendar_events (employee_id, title, event_date, event_time, duration, location, attendees, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, calendar_events_data)

    # Policies — extended with Attendance Threshold policy
    policies_data = [
        ("Leave Policy",             "Leave",     "Employees are eligible for 12 Casual Leaves, 8 Sick Leaves, and 15 Earned Leaves annually. Leave applications must be submitted at least 2 days in advance unless it is an emergency."),
        ("Attendance Threshold Policy","Attendance","Each Field Manager's team must maintain a minimum 80% attendance coverage at all times. If an absence request would drop coverage below 80%, it must be escalated to the Area Manager for approval before it can be granted."),
        ("Sickness Absence Policy",  "Attendance","Sickness absences can be reported on the same day by calling the HR Voice Bot before 08:00. The system will check team coverage and route to Area Manager if coverage would fall below 80%."),
        ("Exception Approval Policy","Attendance","The following absence types always require Area Manager approval: Monday absences submitted on the preceding Friday, last-minute emergency leave (< 2 hours notice), funeral/bereavement leave, and any leave that would breach the 80% coverage threshold."),
        ("Workforce Planning Policy","Operations","Field Managers are responsible for ensuring operational coverage. The AI system will automatically reassign roster schedules when an absence is approved to maintain service delivery targets."),
        ("Grievance Policy",         "Grievances","Employees can raise grievances regarding workplace issues, harassment, or conflicts. All reports are handled confidentially within 7 business days."),
        ("Maternity Leave Policy",   "Leave",     "Female employees with at least 1 year of continuous service are eligible for 26 weeks of fully paid maternity leave."),
        ("Paternity Leave Policy",   "Leave",     "Male employees are eligible for 2 weeks of paid paternity leave upon the birth or adoption of a child."),
        ("Work From Home Policy",    "Workplace", "Field engineers must be on-site for their assigned shift. Office-based employees may work remotely up to 2 days per week with prior manager approval."),
        ("Travel Policy",            "Finance",   "Business travel expenses including fuel, accommodation, and daily meals up to specified limits are reimbursable upon submitting valid receipts within 30 days."),
    ]
    cursor.executemany("""
    INSERT OR REPLACE INTO policies (policy_name, category, description)
    VALUES (?, ?, ?)
    """, policies_data)

    conn.commit()
    conn.close()

    print("✅ Database initialised successfully — HR + Workforce schema ready.")


if __name__ == "__main__":
    create_database()