"""
services/workforce_planning_service.py

Workforce Planning & Roster Scheduling Service
Handles:
  1. Auto-reallocating roster when an absence is approved
  2. Listing available engineers for redeployment
  3. Updating individual schedule entries
  4. Generating coverage summaries across all teams
"""

import sqlite3
from datetime import datetime, timedelta
from config import Config


class WorkforcePlanningService:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    # ----------------------------------------------------------
    # 1. Auto-Reallocate Roster after an Absence is Approved
    # ----------------------------------------------------------
    def auto_reallocate_roster(
        self,
        field_manager_id: int,
        absent_employee_id: int,
        absence_dates: list,  # list of 'YYYY-MM-DD' strings
    ) -> dict:
        """
        When an engineer goes absent, marks their roster entries as 'OnLeave'
        and reassigns their slot to an available cover engineer where possible.

        For each absence date:
          1. Mark the absent engineer's schedule as 'OnLeave'.
          2. Find the first available (non-absent) engineer.
          3. Update that engineer's schedule to 'Reallocated' with a note.
          4. If no cover is found, log the gap (FM should be alerted separately).

        Returns a reallocation summary.
        """
        from services.notification_service import NotificationService
        notif = NotificationService()

        conn = self._connect()
        cursor = conn.cursor()

        updated_entries    = []
        reallocation_notes = []
        no_cover_dates     = []

        for absence_date in absence_dates:
            # ── Step 1: Mark the absent engineer's schedule ──────────────────
            cursor.execute("""
            UPDATE roster_schedules
            SET    status     = 'OnLeave',
                   notes      = 'Absent — auto-updated by AI',
                   updated_at = datetime('now')
            WHERE  employee_id   = ?
              AND  schedule_date = ?
            """, (absent_employee_id, absence_date))

            if cursor.rowcount == 0:
                # No existing schedule entry — insert one
                cursor.execute("""
                INSERT OR IGNORE INTO roster_schedules
                (employee_id, schedule_date, shift_start, shift_end, status, notes)
                VALUES (?, ?, '09:00', '17:00', 'OnLeave', 'Absent — auto-inserted by AI')
                """, (absent_employee_id, absence_date))

            updated_entries.append(absence_date)

            # ── Step 2: Find the first available cover engineer ──────────────
            cursor.execute("""
            SELECT e.employee_id, e.name, rs.shift_start, rs.shift_end
            FROM   employees e
            LEFT JOIN roster_schedules rs
                ON  rs.employee_id   = e.employee_id
                AND rs.schedule_date = ?
            LEFT JOIN leave_requests lr
                ON  lr.employee_id  = e.employee_id
                AND lr.from_date   <= ?
                AND lr.to_date     >= ?
                AND lr.status IN ('Approved', 'Pending')
            WHERE e.field_manager_id = ?
              AND e.employee_id     != ?
              AND e.role            = 'Engineer'
              AND lr.request_id IS NULL
            ORDER BY e.employee_id
            LIMIT 1
            """, (absence_date, absence_date, absence_date, field_manager_id, absent_employee_id))

            cover_eng = cursor.fetchone()

            if cover_eng:
                # ── Step 3: Update cover engineer's roster to 'Reallocated' ─
                cursor.execute("""
                INSERT INTO roster_schedules
                    (employee_id, schedule_date, shift_start, shift_end, status, notes)
                VALUES (?, ?, ?, ?, 'Reallocated', 'Cover for absent colleague — auto-assigned by AI')
                ON CONFLICT(employee_id, schedule_date)
                DO UPDATE SET
                    status     = 'Reallocated',
                    notes      = 'Cover for absent colleague — auto-assigned by AI',
                    updated_at = datetime('now')
                """, (
                    cover_eng["employee_id"],
                    absence_date,
                    cover_eng["shift_start"] or "09:00",
                    cover_eng["shift_end"]   or "17:00",
                ))
                reallocation_notes.append(
                    f"{absence_date}: {cover_eng['name']} (ID {cover_eng['employee_id']}) "
                    f"assigned as cover — roster updated to 'Reallocated'."
                )
            else:
                # ── Step 4: No cover found — log for FM alert ────────────────
                reallocation_notes.append(
                    f"{absence_date}: No cover engineer available — Field Manager alerted."
                )
                no_cover_dates.append(absence_date)

        conn.commit()
        conn.close()

        # ── Notify FM for any dates with no cover ────────────────────────────
        if no_cover_dates:
            # Fetch current coverage % for the first no-cover date to pass to alert
            from services.attendance_service import AttendanceService
            att = AttendanceService()
            cov = att.get_team_coverage(field_manager_id, no_cover_dates[0])
            coverage_pct = cov.get("coverage_pct", 0.0)
            notif.notify_field_manager_coverage_alert(
                field_manager_id = field_manager_id,
                coverage_pct     = coverage_pct,
                coverage_date    = no_cover_dates[0],
            )

        return {
            "success":             True,
            "field_manager_id":    field_manager_id,
            "absent_employee_id":  absent_employee_id,
            "dates_updated":       updated_entries,
            "reallocation_notes":  reallocation_notes,
            "no_cover_dates":      no_cover_dates,
            "message": (
                f"Roster updated for {len(updated_entries)} day(s). "
                f"{len(updated_entries) - len(no_cover_dates)} day(s) covered. "
                + (f"{len(no_cover_dates)} day(s) with no cover — Field Manager notified." if no_cover_dates else "All days covered.")
            ),
        }

    # ----------------------------------------------------------
    # 2. Get Available Engineers for a Date
    # ----------------------------------------------------------
    def get_available_engineers(
        self, field_manager_id: int, target_date: str
    ) -> dict:
        """
        Returns engineers who are NOT on approved/pending leave on target_date.
        """
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT e.employee_id, e.name, e.designation,
               rs.shift_start, rs.shift_end, rs.status AS shift_status
        FROM   employees e
        LEFT JOIN roster_schedules rs
            ON  rs.employee_id   = e.employee_id
            AND rs.schedule_date = ?
        LEFT JOIN leave_requests lr
            ON  lr.employee_id  = e.employee_id
            AND lr.from_date   <= ?
            AND lr.to_date     >= ?
            AND lr.status IN ('Approved', 'Pending')
        WHERE e.field_manager_id = ?
          AND e.role            = 'Engineer'
          AND lr.request_id IS NULL
        ORDER BY e.name
        """, (target_date, target_date, target_date, field_manager_id))

        available = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return {
            "success":          True,
            "field_manager_id": field_manager_id,
            "target_date":      target_date,
            "count":            len(available),
            "engineers":        available,
        }

    # ----------------------------------------------------------
    # 3. Update a Single Schedule Entry
    # ----------------------------------------------------------
    def update_schedule(
        self,
        employee_id: int,
        schedule_date: str,
        shift_status: str,
        shift_start: str = None,
        shift_end: str   = None,
        notes: str       = None,
    ) -> dict:
        """
        Update or insert a roster schedule entry for an engineer.
        shift_status: 'Scheduled' | 'OnLeave' | 'Reallocated' | 'Cancelled'
        """
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO roster_schedules (employee_id, schedule_date, shift_start, shift_end, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(employee_id, schedule_date)
        DO UPDATE SET
            status     = excluded.status,
            shift_start= COALESCE(excluded.shift_start, shift_start),
            shift_end  = COALESCE(excluded.shift_end,   shift_end),
            notes      = COALESCE(excluded.notes,       notes),
            updated_at = datetime('now')
        """, (
            employee_id, schedule_date,
            shift_start or "09:00",
            shift_end   or "17:00",
            shift_status,
            notes,
        ))

        conn.commit()
        conn.close()

        return {
            "success":       True,
            "employee_id":   employee_id,
            "schedule_date": schedule_date,
            "shift_status":  shift_status,
            "message":       f"Schedule updated to '{shift_status}' for {schedule_date}.",
        }

    # ----------------------------------------------------------
    # 4. Get Coverage Summary Across All Teams
    # ----------------------------------------------------------
    def get_all_teams_coverage_summary(self, target_date: str) -> dict:
        """
        Returns a summary of coverage across all FM teams on a date.
        Useful for Area Manager / operations dashboard.
        """
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT employee_id, name FROM employees WHERE role='FieldManager'"
        )
        fms = cursor.fetchall()

        summaries = []
        for fm in fms:
            fm_id = fm["employee_id"]

            cursor.execute(
                "SELECT COUNT(*) as cnt FROM employees WHERE field_manager_id=? AND role='Engineer'",
                (fm_id,),
            )
            total = cursor.fetchone()["cnt"]

            cursor.execute("""
            SELECT COUNT(DISTINCT lr.employee_id) as absent
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.employee_id
            WHERE e.field_manager_id = ?
              AND lr.from_date <= ?
              AND lr.to_date   >= ?
              AND lr.status IN ('Approved', 'Pending')
            """, (fm_id, target_date, target_date))
            absent = cursor.fetchone()["absent"]

            available   = total - absent
            coverage    = round(available / total, 4) if total else 1.0
            threshold   = float(getattr(Config, "COVERAGE_THRESHOLD", 0.80))
            below       = coverage < threshold

            summaries.append({
                "field_manager_id":   fm_id,
                "field_manager_name": fm["name"],
                "total_engineers":    total,
                "available":          available,
                "absent":             absent,
                "coverage_pct":       coverage,
                "coverage_pct_str":   f"{round(coverage * 100, 1)}%",
                "below_threshold":    below,
                "alert":              "⚠️ BELOW THRESHOLD" if below else "✅ OK",
            })

        conn.close()

        return {
            "success":     True,
            "target_date": target_date,
            "teams":       summaries,
            "total_teams": len(summaries),
            "teams_at_risk": sum(1 for s in summaries if s["below_threshold"]),
        }

    # ----------------------------------------------------------
    # 5. Generate Absence Date List between two dates
    # ----------------------------------------------------------
    @staticmethod
    def date_range(from_date: str, to_date: str) -> list:
        """Returns a list of 'YYYY-MM-DD' strings from from_date to to_date inclusive."""
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end   = datetime.strptime(to_date,   "%Y-%m-%d")
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates
