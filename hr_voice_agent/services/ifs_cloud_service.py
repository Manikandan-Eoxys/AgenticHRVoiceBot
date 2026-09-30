"""
services/ifs_cloud_service.py

IFS Cloud Service — Service Layer Wrapper
Orchestrates IFS Cloud syncs and updates the local sync log.
"""

import logging
from datetime import datetime
from config import Config
from integrations.ifs_cloud_client import ifs_client

from database.db import get_db_connection

logger = logging.getLogger(__name__)


class IFSCloudService:

    def _connect(self):
        return get_db_connection()

    def sync_absence_to_ifs(self, leave_request_id: int) -> dict:
        """
        Syncs a specific leave request to IFS Cloud immediately.
        Updates leave_requests and ifs_sync_log on success/failure.
        """
        conn   = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
        SELECT lr.*, e.full_name AS employee_name
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        WHERE lr.request_id = %s
        """, (leave_request_id,))
        row = cursor.fetchone()

        if row is None:
            cursor.close()
            conn.close()
            return {"success": False, "message": "Leave request not found."}

        result = ifs_client.post_absence(
            employee_id  = row["employee_id"],
            absence_type = row.get("leave_code", "CL"),
            from_date    = str(row.get("start_date")),
            to_date      = str(row.get("end_date")),
            reason       = row.get("reason", "") or "",
            request_id   = leave_request_id,
        )

        cursor.close()
        conn.close()
        return {**result, "leave_request_id": leave_request_id}

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
            return {"leave_request_id": leave_request_id, "status": "not_found"}
        return dict(row)
