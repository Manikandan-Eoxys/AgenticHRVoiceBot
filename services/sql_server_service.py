"""
services/sql_server_service.py

SQL Server Service — Service Layer Wrapper
Orchestrates SQL Server syncs and updates the local sync log.
"""

import logging
import sqlite3
from datetime import datetime
from config import Config
from integrations.sql_server_client import sql_client

logger = logging.getLogger(__name__)


class SQLServerService:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_attendance_record(self, leave_request_id: int) -> dict:
        """
        Syncs a specific leave request to SQL Server immediately.
        """
        conn   = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM leave_requests WHERE request_id=?",
            (leave_request_id,),
        )
        row = cursor.fetchone()

        if row is None:
            conn.close()
            return {"success": False, "message": "Leave request not found."}

        row    = dict(row)
        result = sql_client.upsert_attendance(row)
        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        if result["success"]:
            sql_ref = result.get("sql_record_id", "")
            cursor.execute("""
            UPDATE leave_requests
            SET sql_sync_status='synced', updated_at=datetime('now')
            WHERE request_id=?
            """, (leave_request_id,))
            cursor.execute("""
            INSERT INTO sql_server_sync_log (leave_request_id, sync_status, sql_record_id, attempt_count, last_attempt_at, synced_at)
            VALUES (?, 'synced', ?, 1, ?, ?)
            ON CONFLICT DO NOTHING
            """, (leave_request_id, sql_ref, now_str, now_str))
        else:
            cursor.execute("""
            UPDATE sql_server_sync_log
            SET sync_status='failed', error_message=?, attempt_count=attempt_count+1, last_attempt_at=?
            WHERE leave_request_id=?
            """, (result.get("error", "Unknown"), now_str, leave_request_id))

        conn.commit()
        conn.close()

        return {**result, "leave_request_id": leave_request_id}

    def sync_coverage_snapshot(
        self,
        field_manager_id: int,
        coverage_date: str,
        total_engineers: int,
        absent_count: int,
        coverage_pct: float,
    ) -> dict:
        """Pushes a team coverage snapshot to SQL Server."""
        return sql_client.upsert_coverage_snapshot(
            field_manager_id, coverage_date, total_engineers, absent_count, coverage_pct
        )

    def get_sync_status(self, leave_request_id: int) -> dict:
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM sql_server_sync_log WHERE leave_request_id=? ORDER BY log_id DESC LIMIT 1",
            (leave_request_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return {"success": False, "message": "No SQL sync log found for this request."}
        return {"success": True, "log": dict(row)}
