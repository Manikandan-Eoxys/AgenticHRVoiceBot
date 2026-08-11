"""
integrations/sync_scheduler.py

Background Sync Scheduler
Uses APScheduler to periodically sync pending records to:
  - IFS Cloud (via ifs_cloud_client)
  - Microsoft SQL Server (via sql_server_client)

Schedule: Every SYNC_INTERVAL_MINUTES (default 15 minutes).

Usage:
  from integrations.sync_scheduler import start_scheduler, stop_scheduler
  start_scheduler()   # call this in main.py after agent is ready
  stop_scheduler()    # call on shutdown
"""

import logging
import sqlite3
from datetime import datetime
from config import Config
from integrations.ifs_cloud_client import ifs_client
from integrations.sql_server_client import sql_client

logger = logging.getLogger(__name__)

SYNC_INTERVAL   = int(getattr(Config, "SYNC_INTERVAL_MINUTES", 15))
MAX_ATTEMPTS    = 5

_scheduler = None


# ──────────────────────────────────────────────────────────────────────────────
# Sync Job: IFS Cloud
# ──────────────────────────────────────────────────────────────────────────────
def _sync_to_ifs():
    """
    Picks up leave_requests with ifs_sync_status='pending' and syncs them to IFS Cloud.
    """
    db_path = Config.DATABASE_PATH
    conn    = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor  = conn.cursor()

    cursor.execute("""
    SELECT lr.*, e.name AS employee_name
    FROM   leave_requests lr
    JOIN   employees e ON lr.employee_id = e.employee_id
    WHERE  lr.ifs_sync_status = 'pending'
      AND  lr.status IN ('Approved', 'Pending')
    ORDER  BY lr.submitted_at ASC
    LIMIT  50
    """)
    pending = cursor.fetchall()

    if not pending:
        conn.close()
        return

    logger.info("[SyncScheduler] IFS sync: %d pending records", len(pending))

    for row in pending:
        row = dict(row)
        # Check attempt count from ifs_sync_log
        cursor.execute(
            "SELECT attempt_count FROM ifs_sync_log WHERE leave_request_id=? ORDER BY log_id DESC LIMIT 1",
            (row["request_id"],),
        )
        log_row = cursor.fetchone()
        attempts = log_row["attempt_count"] if log_row else 0

        if attempts >= MAX_ATTEMPTS:
            # Mark as permanently failed
            cursor.execute(
                "UPDATE leave_requests SET ifs_sync_status='failed' WHERE request_id=?",
                (row["request_id"],),
            )
            cursor.execute("""
            UPDATE ifs_sync_log
            SET sync_status='failed', last_attempt_at=datetime('now')
            WHERE leave_request_id=?
            """, (row["request_id"],))
            conn.commit()
            logger.warning("[SyncScheduler] IFS: request %d failed after %d attempts", row["request_id"], attempts)
            continue

        result = ifs_client.post_absence(
            employee_id  = row["employee_id"],
            absence_type = row["absence_type"],
            from_date    = row["from_date"],
            to_date      = row["to_date"],
            reason       = row["reason"] or "",
            request_id   = row["request_id"],
        )

        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        if result["success"]:
            ifs_ref = result.get("ifs_cloud_ref", "")
            cursor.execute("""
            UPDATE leave_requests
            SET ifs_sync_status='synced', ifs_cloud_ref=?, updated_at=datetime('now')
            WHERE request_id=?
            """, (ifs_ref, row["request_id"]))
            cursor.execute("""
            UPDATE ifs_sync_log
            SET sync_status='synced', ifs_cloud_ref=?, attempt_count=?, last_attempt_at=?, synced_at=?
            WHERE leave_request_id=?
            """, (ifs_ref, attempts + 1, now_str, now_str, row["request_id"]))
            logger.info("[SyncScheduler] IFS: synced request %d → ref=%s", row["request_id"], ifs_ref)
        else:
            cursor.execute("""
            UPDATE ifs_sync_log
            SET attempt_count=?, last_attempt_at=?, error_message=?
            WHERE leave_request_id=?
            """, (attempts + 1, now_str, result.get("error", "Unknown error"), row["request_id"]))
            logger.warning("[SyncScheduler] IFS: request %d failed (attempt %d): %s",
                           row["request_id"], attempts + 1, result.get("error"))

        conn.commit()

    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Sync Job: SQL Server
# ──────────────────────────────────────────────────────────────────────────────
def _sync_to_sql():
    """
    Picks up leave_requests with sql_sync_status='pending' and syncs them to SQL Server.
    """
    db_path = Config.DATABASE_PATH
    conn    = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor  = conn.cursor()

    cursor.execute("""
    SELECT lr.*
    FROM   leave_requests lr
    WHERE  lr.sql_sync_status = 'pending'
      AND  lr.status IN ('Approved', 'Pending')
    ORDER  BY lr.submitted_at ASC
    LIMIT  50
    """)
    pending = cursor.fetchall()

    if not pending:
        conn.close()
        return

    logger.info("[SyncScheduler] SQL sync: %d pending records", len(pending))

    for row in pending:
        row = dict(row)
        cursor.execute(
            "SELECT attempt_count FROM sql_server_sync_log WHERE leave_request_id=? ORDER BY log_id DESC LIMIT 1",
            (row["request_id"],),
        )
        log_row  = cursor.fetchone()
        attempts = log_row["attempt_count"] if log_row else 0

        if attempts >= MAX_ATTEMPTS:
            cursor.execute(
                "UPDATE leave_requests SET sql_sync_status='failed' WHERE request_id=?",
                (row["request_id"],),
            )
            cursor.execute("""
            UPDATE sql_server_sync_log
            SET sync_status='failed', last_attempt_at=datetime('now')
            WHERE leave_request_id=?
            """, (row["request_id"],))
            conn.commit()
            logger.warning("[SyncScheduler] SQL: request %d permanently failed", row["request_id"])
            continue

        result = sql_client.upsert_attendance(row)
        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        if result["success"]:
            sql_ref = result.get("sql_record_id", "")
            cursor.execute("""
            UPDATE leave_requests
            SET sql_sync_status='synced', updated_at=datetime('now')
            WHERE request_id=?
            """, (row["request_id"],))
            cursor.execute("""
            UPDATE sql_server_sync_log
            SET sync_status='synced', sql_record_id=?, attempt_count=?, last_attempt_at=?, synced_at=?
            WHERE leave_request_id=?
            """, (sql_ref, attempts + 1, now_str, now_str, row["request_id"]))
            logger.info("[SyncScheduler] SQL: synced request %d → ref=%s", row["request_id"], sql_ref)
        else:
            cursor.execute("""
            UPDATE sql_server_sync_log
            SET attempt_count=?, last_attempt_at=?, error_message=?
            WHERE leave_request_id=?
            """, (attempts + 1, now_str, result.get("error", "Unknown"), row["request_id"]))

        conn.commit()

    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Lifecycle
# ──────────────────────────────────────────────────────────────────────────────
def start_scheduler():
    """Start the APScheduler background sync jobs."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("[SyncScheduler] apscheduler not installed — sync scheduler disabled.")
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _sync_to_ifs,
        trigger  = "interval",
        minutes  = SYNC_INTERVAL,
        id       = "ifs_sync",
        name     = "IFS Cloud Sync",
        max_instances = 1,
    )
    _scheduler.add_job(
        _sync_to_sql,
        trigger  = "interval",
        minutes  = SYNC_INTERVAL,
        id       = "sql_sync",
        name     = "SQL Server Sync",
        max_instances = 1,
    )
    _scheduler.start()
    logger.info(
        "[SyncScheduler] Started — IFS Cloud & SQL Server sync every %d min(s).",
        SYNC_INTERVAL,
    )


def stop_scheduler():
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[SyncScheduler] Stopped.")
        _scheduler = None


def run_immediate_sync():
    """Trigger an immediate sync cycle (useful for testing or manual triggers)."""
    logger.info("[SyncScheduler] Running immediate sync cycle …")
    _sync_to_ifs()
    _sync_to_sql()
    logger.info("[SyncScheduler] Immediate sync cycle complete.")
