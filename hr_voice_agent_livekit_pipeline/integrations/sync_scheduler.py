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
from datetime import datetime
from config import Config
from integrations.ifs_cloud_client import ifs_client
from integrations.sql_server_client import sql_client
from database.db import get_db_connection

logger = logging.getLogger(__name__)

SYNC_INTERVAL   = int(getattr(Config, "SYNC_INTERVAL_MINUTES", 15))
MAX_ATTEMPTS    = 5

_scheduler = None


# ──────────────────────────────────────────────────────────────────────────────
# Ensure sync columns exist (auto-migration)
# ──────────────────────────────────────────────────────────────────────────────
def _ensure_sync_columns():
    """Add sync-related columns and tables if they don't already exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Add sync status columns to leave_requests if missing
    for col, default in [
        ("ifs_sync_status", "'pending'"),
        ("ifs_cloud_ref", "NULL"),
        ("sql_sync_status", "'pending'"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE leave_requests ADD COLUMN {col} VARCHAR(20) DEFAULT {default}")
            logger.info("[SyncScheduler] Added column leave_requests.%s", col)
        except Exception:
            pass  # Column already exists

    # Create ifs_sync_log if missing
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ifs_sync_log (
        log_id              INT AUTO_INCREMENT PRIMARY KEY,
        leave_request_id    INT NOT NULL,
        sync_status         VARCHAR(20) NOT NULL DEFAULT 'pending',
        ifs_cloud_ref       VARCHAR(100),
        attempt_count       INT NOT NULL DEFAULT 0,
        last_attempt_at     TIMESTAMP NULL,
        synced_at           TIMESTAMP NULL,
        error_message       TEXT NULL,
        UNIQUE KEY uq_ifs_leave (leave_request_id)
    )
    """)

    # Create sql_server_sync_log if missing
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sql_server_sync_log (
        log_id              INT AUTO_INCREMENT PRIMARY KEY,
        leave_request_id    INT NOT NULL,
        sync_status         VARCHAR(20) NOT NULL DEFAULT 'pending',
        sql_record_id       VARCHAR(100),
        attempt_count       INT NOT NULL DEFAULT 0,
        last_attempt_at     TIMESTAMP NULL,
        synced_at           TIMESTAMP NULL,
        error_message       TEXT NULL,
        UNIQUE KEY uq_sql_leave (leave_request_id)
    )
    """)

    conn.commit()
    conn.close()
    logger.info("[SyncScheduler] Sync schema verified / migrated.")


# ──────────────────────────────────────────────────────────────────────────────
# Sync Job: IFS Cloud
# ──────────────────────────────────────────────────────────────────────────────
def _sync_to_ifs():
    """
    Picks up leave_requests with ifs_sync_status='pending' and syncs them to IFS Cloud.
    """
    conn    = get_db_connection()
    cursor  = conn.cursor(dictionary=True)

    cursor.execute("""
    SELECT lr.*, e.full_name AS employee_name
    FROM   leave_requests lr
    JOIN   employees e ON lr.employee_id = e.employee_id
    WHERE  lr.ifs_sync_status = 'pending'
      AND  lr.status IN ('approved', 'submitted')
    ORDER  BY lr.requested_at ASC
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
            "SELECT attempt_count FROM ifs_sync_log WHERE leave_request_id=%s ORDER BY log_id DESC LIMIT 1",
            (row["request_id"],),
        )
        log_row = cursor.fetchone()
        attempts = log_row["attempt_count"] if log_row else 0

        if attempts >= MAX_ATTEMPTS:
            # Mark as permanently failed
            cursor.execute(
                "UPDATE leave_requests SET ifs_sync_status='failed' WHERE request_id=%s",
                (row["request_id"],),
            )
            cursor.execute("""
            UPDATE ifs_sync_log
            SET sync_status='failed', last_attempt_at=NOW()
            WHERE leave_request_id=%s
            """, (row["request_id"],))
            conn.commit()
            logger.warning("[SyncScheduler] IFS: request %d failed after %d attempts", row["request_id"], attempts)
            continue

        result = ifs_client.post_absence(
            employee_id  = row["employee_id"],
            absence_type = row["leave_code"],
            from_date    = row["start_date"],
            to_date      = row["end_date"],
            reason       = row["reason"] or "",
            request_id   = row["request_id"],
        )

        now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

        if result["success"]:
            ifs_ref = result.get("ifs_cloud_ref", "")
            cursor.execute("""
            UPDATE leave_requests
            SET ifs_sync_status='synced', ifs_cloud_ref=%s
            WHERE request_id=%s
            """, (ifs_ref, row["request_id"]))

            # Upsert into ifs_sync_log
            cursor.execute("""
            INSERT INTO ifs_sync_log (leave_request_id, sync_status, ifs_cloud_ref, attempt_count, last_attempt_at, synced_at)
            VALUES (%s, 'synced', %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sync_status='synced', ifs_cloud_ref=%s, attempt_count=%s, last_attempt_at=%s, synced_at=%s
            """, (row["request_id"], ifs_ref, attempts + 1, now_str, now_str,
                  ifs_ref, attempts + 1, now_str, now_str))
            logger.info("[SyncScheduler] IFS: synced request %d → ref=%s", row["request_id"], ifs_ref)
        else:
            # Upsert failure into ifs_sync_log
            error_msg = result.get("error", "Unknown error")
            cursor.execute("""
            INSERT INTO ifs_sync_log (leave_request_id, attempt_count, last_attempt_at, error_message)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                attempt_count=%s, last_attempt_at=%s, error_message=%s
            """, (row["request_id"], attempts + 1, now_str, error_msg,
                  attempts + 1, now_str, error_msg))
            logger.warning("[SyncScheduler] IFS: request %d failed (attempt %d): %s",
                           row["request_id"], attempts + 1, error_msg)

        conn.commit()

    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Sync Job: SQL Server
# ──────────────────────────────────────────────────────────────────────────────
def _sync_to_sql():
    """
    Picks up leave_requests with sql_sync_status='pending' and syncs them to SQL Server.
    """
    conn    = get_db_connection()
    cursor  = conn.cursor(dictionary=True)

    cursor.execute("""
    SELECT lr.*
    FROM   leave_requests lr
    WHERE  lr.sql_sync_status = 'pending'
      AND  lr.status IN ('approved', 'submitted')
    ORDER  BY lr.requested_at ASC
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
            "SELECT attempt_count FROM sql_server_sync_log WHERE leave_request_id=%s ORDER BY log_id DESC LIMIT 1",
            (row["request_id"],),
        )
        log_row  = cursor.fetchone()
        attempts = log_row["attempt_count"] if log_row else 0

        if attempts >= MAX_ATTEMPTS:
            cursor.execute(
                "UPDATE leave_requests SET sql_sync_status='failed' WHERE request_id=%s",
                (row["request_id"],),
            )
            cursor.execute("""
            UPDATE sql_server_sync_log
            SET sync_status='failed', last_attempt_at=NOW()
            WHERE leave_request_id=%s
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
            SET sql_sync_status='synced'
            WHERE request_id=%s
            """, (row["request_id"],))

            cursor.execute("""
            INSERT INTO sql_server_sync_log (leave_request_id, sync_status, sql_record_id, attempt_count, last_attempt_at, synced_at)
            VALUES (%s, 'synced', %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sync_status='synced', sql_record_id=%s, attempt_count=%s, last_attempt_at=%s, synced_at=%s
            """, (row["request_id"], sql_ref, attempts + 1, now_str, now_str,
                  sql_ref, attempts + 1, now_str, now_str))
            logger.info("[SyncScheduler] SQL: synced request %d → ref=%s", row["request_id"], sql_ref)
        else:
            error_msg = result.get("error", "Unknown")
            cursor.execute("""
            INSERT INTO sql_server_sync_log (leave_request_id, attempt_count, last_attempt_at, error_message)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                attempt_count=%s, last_attempt_at=%s, error_message=%s
            """, (row["request_id"], attempts + 1, now_str, error_msg,
                  attempts + 1, now_str, error_msg))

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

    # Ensure sync columns & tables exist before starting jobs
    try:
        _ensure_sync_columns()
    except Exception as e:
        logger.error("[SyncScheduler] Failed to ensure sync schema: %s", e)

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
