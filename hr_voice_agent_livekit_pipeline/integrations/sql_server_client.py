"""
integrations/sql_server_client.py

Microsoft SQL Server Integration Client
Handles:
  - Syncing attendance/absence records to SQL Server
  - Syncing team coverage snapshots
  - Reading roster data from SQL Server (bidirectional)

STUB MODE: Operates in stub mode if SQL_SERVER_CONN_STR is not configured.
When real credentials are available, set SQL_SERVER_CONN_STR in .env.

Connection string examples:
  On-premises MSSQL (Windows Auth):
    DRIVER={ODBC Driver 17 for SQL Server};SERVER=myserver;DATABASE=hrdb;Trusted_Connection=yes;
  SQL Server with username/password:
    DRIVER={ODBC Driver 17 for SQL Server};SERVER=myserver;DATABASE=hrdb;UID=user;PWD=pass;
  Azure SQL:
    DRIVER={ODBC Driver 17 for SQL Server};SERVER=myserver.database.windows.net;
    DATABASE=hrdb;UID=user@myserver;PWD=pass;Encrypt=yes;TrustServerCertificate=no;
"""

import logging
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)

SQL_CONN_STR = getattr(Config, "SQL_SERVER_CONN_STR", None)
STUB_MODE    = not bool(SQL_CONN_STR)


def _get_connection():
    """Return a pyodbc connection."""
    if STUB_MODE:
        raise RuntimeError("SQL Server stub mode: no connection string configured.")
    import pyodbc
    return pyodbc.connect(SQL_CONN_STR, timeout=10)


class SQLServerClient:
    """
    SQL Server integration client.
    All methods return {success, data/error, stub_mode}.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Upsert Attendance Record
    # ──────────────────────────────────────────────────────────────────────────
    def upsert_attendance(self, record: dict) -> dict:
        """
        Inserts or updates an attendance/absence record in SQL Server.

        Expected record keys:
          request_id, employee_id, from_date, to_date, absence_type,
          leave_type, status, reason, submitted_at
        """
        if STUB_MODE:
            sql_ref = f"SQL-ATT-STUB-{record.get('request_id', '0')}"
            logger.info(
                "[SQLClient STUB] upsert_attendance: request_id=%s employee=%s ref=%s",
                record.get("request_id"), record.get("employee_id"), sql_ref,
            )
            return {
                "success":      True,
                "sql_record_id":sql_ref,
                "stub_mode":    True,
                "record":       record,
            }

        sql = """
        MERGE INTO HR_Attendance AS target
        USING (SELECT
            :request_id  AS RequestId,
            :employee_id AS EmployeeId,
            :from_date   AS FromDate,
            :to_date     AS ToDate,
            :absence_type AS AbsenceType,
            :leave_type  AS LeaveType,
            :status      AS Status,
            :reason      AS Reason,
            :submitted_at AS SubmittedAt
        ) AS source ON target.RequestId = source.RequestId
        WHEN MATCHED THEN
            UPDATE SET
                Status      = source.Status,
                UpdatedAt   = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (RequestId, EmployeeId, FromDate, ToDate, AbsenceType, LeaveType, Status, Reason, SubmittedAt, CreatedAt)
            VALUES (source.RequestId, source.EmployeeId, source.FromDate, source.ToDate,
                    source.AbsenceType, source.LeaveType, source.Status, source.Reason,
                    source.SubmittedAt, GETDATE());
        """
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, record)
            conn.commit()
            conn.close()
            sql_ref = f"SQL-ATT-{record.get('request_id')}"
            logger.info("[SQLClient] Attendance upserted: %s", sql_ref)
            return {"success": True, "sql_record_id": sql_ref, "stub_mode": False}
        except Exception as exc:
            logger.error("[SQLClient] upsert_attendance failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Upsert Coverage Snapshot
    # ──────────────────────────────────────────────────────────────────────────
    def upsert_coverage_snapshot(
        self,
        field_manager_id: int,
        coverage_date: str,
        total_engineers: int,
        absent_count: int,
        coverage_pct: float,
    ) -> dict:
        """
        Pushes a daily coverage snapshot to SQL Server for reporting.
        """
        if STUB_MODE:
            logger.info(
                "[SQLClient STUB] upsert_coverage_snapshot: fm=%s date=%s pct=%.1f%%",
                field_manager_id, coverage_date, coverage_pct * 100,
            )
            return {
                "success":   True,
                "stub_mode": True,
                "coverage_pct": coverage_pct,
            }

        sql = """
        MERGE INTO HR_TeamCoverage AS target
        USING (SELECT
            :field_manager_id AS FieldManagerId,
            :coverage_date    AS CoverageDate
        ) AS source ON (target.FieldManagerId = source.FieldManagerId
                    AND target.CoverageDate   = source.CoverageDate)
        WHEN MATCHED THEN
            UPDATE SET
                TotalEngineers = :total_engineers,
                AbsentCount    = :absent_count,
                CoveragePct    = :coverage_pct,
                UpdatedAt      = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (FieldManagerId, CoverageDate, TotalEngineers, AbsentCount, CoveragePct, CreatedAt)
            VALUES (:field_manager_id, :coverage_date, :total_engineers, :absent_count, :coverage_pct, GETDATE());
        """
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, {
                "field_manager_id": field_manager_id,
                "coverage_date":    coverage_date,
                "total_engineers":  total_engineers,
                "absent_count":     absent_count,
                "coverage_pct":     coverage_pct,
            })
            conn.commit()
            conn.close()
            logger.info("[SQLClient] Coverage snapshot upserted: fm=%s date=%s", field_manager_id, coverage_date)
            return {"success": True, "stub_mode": False}
        except Exception as exc:
            logger.error("[SQLClient] upsert_coverage_snapshot failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Read Roster from SQL Server
    # ──────────────────────────────────────────────────────────────────────────
    def read_roster(self, team_id: int, target_date: str) -> dict:
        """
        Reads roster assignments for a team from SQL Server.
        Used for bidirectional sync.
        """
        if STUB_MODE:
            logger.info(
                "[SQLClient STUB] read_roster: team=%s date=%s",
                team_id, target_date,
            )
            return {
                "success":   True,
                "stub_mode": True,
                "team_id":   team_id,
                "date":      target_date,
                "roster":    [],
            }

        sql = """
        SELECT EmployeeId, EmployeeName, ShiftStart, ShiftEnd, ShiftStatus
        FROM   HR_RosterSchedules
        WHERE  TeamId       = ?
          AND  ScheduleDate = ?
        ORDER BY EmployeeName
        """
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, (team_id, target_date))
            rows = [
                {
                    "employee_id":   r[0],
                    "employee_name": r[1],
                    "shift_start":   str(r[2]),
                    "shift_end":     str(r[3]),
                    "shift_status":  r[4],
                }
                for r in cursor.fetchall()
            ]
            conn.close()
            return {"success": True, "stub_mode": False, "roster": rows}
        except Exception as exc:
            logger.error("[SQLClient] read_roster failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}


# Module-level singleton
sql_client = SQLServerClient()
