"""
tools/attendance_tools.py

Attendance Management Tool Functions
Used by LiveKit/LLM Function Calling.

Provides:
  1. submit_absence_request   — Employee submits sickness/holiday/emergency/funeral
  2. check_team_coverage      — Field Manager checks team availability for a date
  3. get_team_roster          — Field Manager views full roster for a date
  4. list_pending_exceptions  — Area Manager lists exceptions awaiting approval
  5. approve_exception        — Area Manager approves or rejects an exception
  6. get_all_teams_coverage   — Area Manager / admin sees all-teams coverage summary
  7. get_exception_detail     — Retrieve detail on a specific exception request
"""

from datetime import datetime
from config import Config
from database.db import get_db_connection
from services.attendance_service import AttendanceService
from services.exception_service import ExceptionService
from services.workforce_planning_service import WorkforcePlanningService
from services.notification_service import NotificationService
from services.ifs_cloud_service import IFSCloudService
from services.sql_server_service import SQLServerService


class AttendanceTools:

    def __init__(self):
        self.attendance_service    = AttendanceService()
        self.exception_service     = ExceptionService()
        self.workforce_service     = WorkforcePlanningService()
        self.notification_service  = NotificationService()
        self.ifs_service           = IFSCloudService()
        self.sql_service           = SQLServerService()

    def _connect(self):
        return get_db_connection()

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Submit Absence Request
    # ──────────────────────────────────────────────────────────────────────────
    def submit_absence_request(
        self,
        employee_id: int,
        absence_type: str,
        from_date: str,
        to_date: str,
        reason: str = "",
    ) -> dict:
        """
        Submit an absence request on behalf of a field engineer.

        absence_type options:
          'Sickness'  — same-day sickness call
          'Holiday'   — planned holiday
          'Emergency' — emergency leave (short notice)
          'Funeral'   — bereavement/funeral leave
          'Casual'    — casual day off
          'Earned'    — earned / annual leave

        Automatically:
          - Checks team coverage threshold (80%)
          - Detects if exception approval is required
          - Routes to Area Manager if needed
          - Creates sync entries for IFS Cloud and SQL Server
        """
        submitted_at = datetime.now().isoformat(sep=" ", timespec="seconds")

        result = self.attendance_service.submit_absence_request(
            employee_id  = employee_id,
            absence_type = absence_type,
            from_date    = from_date,
            to_date      = to_date,
            reason       = reason,
            submitted_at = submitted_at,
        )

        if not result.get("success"):
            return result

        # ── Always notify HR inbox for every submitted leave request ──────────
        emp = self._connect()
        try:
            c = emp.cursor(dictionary=True)
            c.execute(
                "SELECT full_name FROM employees WHERE employee_id = %s",
                (str(employee_id),)
            )
            emp_row = c.fetchone()
            c.close()
            emp.close()
            employee_name = emp_row["full_name"] if emp_row else f"Employee {employee_id}"
        except Exception:
            employee_name = f"Employee {employee_id}"

        try:
            days_requested = result.get("days", 1)
            self.notification_service.notify_hr_leave_request(
                employee_id   = employee_id,
                employee_name = employee_name,
                leave_type    = absence_type,
                from_date     = from_date,
                to_date       = to_date,
                days          = days_requested,
                request_id    = result.get("request_id", 0),
                reason        = reason,
            )
            result["hr_email_sent"] = True
        except Exception as exc:
            result["hr_email_sent"] = False
            result["hr_email_error"] = str(exc)

        # ── Notify Area Manager if this is an exception request ──────────────
        if result.get("exception_flag") and result.get("area_manager_id") and result.get("exception_id"):
            self.notification_service.notify_area_manager(
                area_manager_id = result["area_manager_id"],
                exception_id    = result["exception_id"],
                employee_id     = employee_id,
                exception_type  = result.get("exception_type", "Exception"),
                from_date       = from_date,
                to_date         = to_date,
                reason          = reason,
            )

        # ── IFS Cloud + SQL Server sync for non-exception approved requests ───
        if not result.get("exception_flag") and result.get("request_id"):
            self.ifs_service.sync_absence_to_ifs(result["request_id"])
            self.sql_service.sync_attendance_record(result["request_id"])

        return result


    # ──────────────────────────────────────────────────────────────────────────
    # 2. Check Team Coverage
    # ──────────────────────────────────────────────────────────────────────────
    def check_team_coverage(
        self,
        field_manager_id: int,
        coverage_date: str,
    ) -> dict:
        """
        Check the attendance coverage percentage for a Field Manager's team on a given date.
        Returns available engineers, absent engineers, and whether coverage is above threshold.
        """
        return self.attendance_service.get_team_coverage(field_manager_id, coverage_date)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Get Team Roster
    # ──────────────────────────────────────────────────────────────────────────
    def get_team_roster(
        self,
        field_manager_id: int,
        roster_date: str,
    ) -> dict:
        """
        Retrieve the full roster for a Field Manager's team on a specific date.
        Shows each engineer's shift status, leave type if absent, and availability.
        """
        return self.attendance_service.get_team_roster(field_manager_id, roster_date)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. List Pending Exceptions (Area Manager)
    # ──────────────────────────────────────────────────────────────────────────
    def list_pending_exceptions(self, area_manager_id: int) -> dict:
        """
        Retrieve all exception absence requests that are pending the Area Manager's approval.
        Returns exception ID, employee name, dates, exception type, and reason for each.
        """
        return self.exception_service.get_pending_exceptions(area_manager_id)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Approve or Reject Exception (Area Manager)
    # ──────────────────────────────────────────────────────────────────────────
    def approve_exception(
        self,
        exception_id: int,
        area_manager_id: int,
        decision: str,
        notes: str = "",
    ) -> dict:
        """
        Area Manager approves or rejects an exception absence request.

        decision: 'Approved' | 'Rejected'
        notes: Optional reason / comments from the Area Manager.

        After decision:
          - Updates leave_requests status
          - Notifies the employee
          - If approved: triggers IFS Cloud and SQL Server sync
          - If rejected: restores employee leave balance
        """
        result = self.exception_service.process_exception_decision(
            exception_id    = exception_id,
            area_manager_id = area_manager_id,
            decision        = decision,
            notes           = notes,
        )

        if not result.get("success"):
            return result

        # Notify employee of the decision
        # Fetch leave request details for notification
        conn    = self._connect()
        cursor  = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT start_date AS from_date, end_date AS to_date FROM leave_requests WHERE request_id=%s",
            (result.get("leave_request_id"),),
        )
        lr = cursor.fetchone()
        cursor.close()
        conn.close()

        if lr:
            self.notification_service.notify_employee(
                employee_id = result["employee_id"],
                decision    = decision,
                request_id  = result["leave_request_id"],
                from_date   = str(lr["from_date"]),
                to_date     = str(lr["to_date"]),
                am_notes    = notes,
            )
            self.exception_service.mark_employee_notified(exception_id)

        # If approved: sync to IFS & SQL
        if decision == "Approved" and result.get("leave_request_id"):
            self.ifs_service.sync_absence_to_ifs(result["leave_request_id"])
            self.sql_service.sync_attendance_record(result["leave_request_id"])

            # Trigger roster reallocation
            conn    = self._connect()
            cursor  = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT start_date AS from_date, end_date AS to_date, employee_id FROM leave_requests WHERE request_id=%s",
                (result["leave_request_id"],),
            )
            lr2 = cursor.fetchone()
            cursor.execute(
                "SELECT field_manager_id FROM employees WHERE employee_id=%s",
                (str(result["employee_id"]),),
            )
            emp_row = cursor.fetchone()
            cursor.close()
            conn.close()

            if lr2 and emp_row and emp_row.get("field_manager_id"):
                dates = self.workforce_service.date_range(str(lr2["from_date"]), str(lr2["to_date"]))
                self.workforce_service.auto_reallocate_roster(
                    field_manager_id   = emp_row["field_manager_id"],
                    absent_employee_id = result["employee_id"],
                    absence_dates      = dates,
                )

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Get All Teams Coverage Summary (Area Manager / Admin)
    # ──────────────────────────────────────────────────────────────────────────
    def get_all_teams_coverage(self, target_date: str) -> dict:
        """
        Returns a coverage summary across all Field Manager teams for a given date.
        Useful for Area Managers and operations staff to spot teams at risk.
        """
        return self.workforce_service.get_all_teams_coverage_summary(target_date)

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Get Exception Detail
    # ──────────────────────────────────────────────────────────────────────────
    def get_exception_detail(self, exception_id: int) -> dict:
        """
        Retrieve the full detail of a specific exception request.
        """
        return self.exception_service.get_exception(exception_id)
