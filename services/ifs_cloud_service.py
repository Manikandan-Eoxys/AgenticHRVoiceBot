"""
services/ifs_cloud_service.py

IFS Cloud Service — Service Layer Wrapper
Orchestrates IFS Cloud syncs and updates the local sync log.
"""

import logging
import sqlite3
from datetime import datetime
from config import Config
from integrations.ifs_cloud_client import ifs_client

logger = logging.getLogger(__name__)


class IFSCloudService:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_absence_to_ifs(self, leave_request_id: int) -> dict:
        """
        Syncs a specific leave request to IFS Cloud immediately.
        Updates leave_requests and ifs_sync_log on success/failure.
        """
        conn   = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT lr.*, e.name AS employee_name
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        WHERE lr.request_id = ?
        """, (leave_request_id,))
        row = cursor.fetchone()

        if row is None:
            conn.close()
            return {"success": False, "message": "Leave request not found."}

        row = dict(row)
        result = ifs_client.post_absence(
            employee_id  = row["employee_id"],
            absence_type = row["absence_type"],
            from_date    = row["from_date"],
            to_date      = row["to_date"],
            reason       = row["reason"] or "",
            request_id   = leave_request_id,
        )

        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        if result["success"]:
            ifs_ref = result.get("ifs_cloud_ref", "")
            cursor.execute("""
            UPDATE leave_requests
            SET ifs_sync_status='synced', ifs_cloud_ref=?, updated_at=datetime('now')
            WHERE request_id=?
            """, (ifs_ref, leave_request_id))
            cursor.execute("""
            INSERT INTO ifs_sync_log (leave_request_id, sync_status, ifs_cloud_ref, attempt_count, last_attempt_at, synced_at)
            VALUES (?, 'synced', ?, 1, ?, ?)
            ON CONFLICT DO NOTHING
            """, (leave_request_id, ifs_ref, now_str, now_str))
        else:
            cursor.execute("""
            UPDATE ifs_sync_log
            SET sync_status='failed', error_message=?, attempt_count=attempt_count+1, last_attempt_at=?
            WHERE leave_request_id=?
            """, (result.get("error", "Unknown"), now_str, leave_request_id))

        conn.commit()
        conn.close()

        return {**result, "leave_request_id": leave_request_id}

    def get_sync_status(self, leave_request_id: int) -> dict:
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ifs_sync_log WHERE leave_request_id=? ORDER BY log_id DESC LIMIT 1",
            (leave_request_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return {"success": False, "message": "No IFS sync log found for this request."}
        return {"success": True, "log": dict(row)}
