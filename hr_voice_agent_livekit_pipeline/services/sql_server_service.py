"""
services/sql_server_service.py

SQL Server Service — Service Layer Wrapper
Orchestrates SQL Server syncs and updates the local sync log.
"""

import logging
from datetime import datetime
from config import Config
from integrations.sql_server_client import sql_client

from database.db import get_db_connection

logger = logging.getLogger(__name__)


class SQLServerService:

    def _connect(self):
        return get_db_connection()

    def sync_attendance_record(self, leave_request_id: int) -> dict:
        """
        Syncs a specific leave request to SQL Server immediately.
        """
        conn   = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM leave_requests WHERE request_id = %s",
            (leave_request_id,),
        )
        row = cursor.fetchone()

        if row is None:
            cursor.close()
            conn.close()
            return {"success": False, "message": "Leave request not found."}

        row    = dict(row)
        result = sql_client.upsert_attendance(row)
        cursor.close()
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
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM leave_requests WHERE request_id = %s",
            (leave_request_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row is None:
            return {"success": False, "message": "No sync log found for this request."}
        return {"success": True, "log": dict(row)}
