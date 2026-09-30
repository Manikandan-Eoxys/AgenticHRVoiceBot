"""
agent_builder/backend/services/dbManagerService.py

Database Manager Service
=========================
Handles database introspection, query sanitization, execution timing,
and table statistics for the Agent Builder admin portal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List

import mysql.connector

logger = logging.getLogger("agent_builder.db_manager")


class DbManagerService:
    """Service layer for safe database introspection and query execution."""

    _WRITE_KEYWORDS = re.compile(
        r"^\s*(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|REVOKE)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        self.config = {
            "host": os.environ.get("MYSQL_HOST", "localhost"),
            "user": os.environ.get("MYSQL_USER", "hrbot"),
            "password": os.environ.get("MYSQL_PASSWORD", "Eoxys@110"),
            "database": os.environ.get("MYSQL_DATABASE", "hr_voicebot"),
        }

    def _get_connection(self):
        return mysql.connector.connect(**self.config)

    def _assert_readonly(self, sql: str) -> None:
        clean = sql.strip()
        if self._WRITE_KEYWORDS.match(clean):
            raise PermissionError(
                "Only SELECT statements are permitted via the Admin DB Manager."
            )
        if ";" in clean[:-1]:
            # Disallow multiple chained queries
            raise PermissionError("Multiple SQL statements in a single query are not permitted.")

    def _sync_list_tables(self) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            sql = """
                SELECT
                    TABLE_NAME AS table_name,
                    TABLE_ROWS AS row_count,
                    DATA_LENGTH AS data_bytes,
                    UPDATE_TIME AS last_updated
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME
            """
            cursor.execute(sql, (self.config["database"],))
            rows = cursor.fetchall()
            cursor.close()
            # If TABLE_ROWS is None or inaccurate, query actual COUNT(*) for each table
            result = []
            for r in rows:
                t_name = r["table_name"]
                cur_cnt = conn.cursor()
                try:
                    cur_cnt.execute(f"SELECT COUNT(*) FROM `{t_name}`")
                    exact_count = cur_cnt.fetchone()[0]
                except Exception:
                    exact_count = r["row_count"] or 0
                finally:
                    cur_cnt.close()
                result.append({
                    "table_name": t_name,
                    "row_count": exact_count,
                    "data_bytes": r["data_bytes"] or 0,
                    "last_updated": str(r["last_updated"]) if r["last_updated"] else None,
                })
            return result
        finally:
            conn.close()

    async def list_tables(self) -> List[Dict[str, Any]]:
        """Return all database tables with exact row counts."""
        return await asyncio.to_thread(self._sync_list_tables)

    def _sync_get_schema(self, table_name: str) -> List[Dict[str, Any]]:
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "", table_name)
        conn = self._get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            sql = """
                SELECT
                    COLUMN_NAME AS column_name,
                    COLUMN_TYPE AS column_type,
                    IS_NULLABLE AS nullable,
                    COLUMN_KEY AS key_type,
                    COLUMN_DEFAULT AS default_value,
                    EXTRA AS extra
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """
            cursor.execute(sql, (self.config["database"], safe_name))
            rows = cursor.fetchall()
            cursor.close()
            for r in rows:
                if r["default_value"] is not None:
                    r["default_value"] = str(r["default_value"])
            return rows
        finally:
            conn.close()

    async def get_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """Return column definitions and keys for a specific table."""
        return await asyncio.to_thread(self._sync_get_schema, table_name)

    def _sync_run_query(self, sql: str) -> Dict[str, Any]:
        self._assert_readonly(sql)
        conn = self._get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            t0 = time.perf_counter()
            cursor.execute(sql)
            rows = cursor.fetchall()
            exec_ms = round((time.perf_counter() - t0) * 1000, 2)
            cursor.close()

            # Format datetime / date / decimal objects to string for JSON serialization
            serialized_rows = []
            for r in rows:
                new_row = {}
                for k, v in r.items():
                    if hasattr(v, "isoformat"):
                        new_row[k] = v.isoformat()
                    elif hasattr(v, "__str__") and type(v).__name__ in ("Decimal", "timedelta", "bytes"):
                        new_row[k] = str(v)
                    else:
                        new_row[k] = v
                serialized_rows.append(new_row)

            columns = list(serialized_rows[0].keys()) if serialized_rows else []
            return {
                "rows": serialized_rows,
                "columns": columns,
                "row_count": len(serialized_rows),
                "exec_ms": exec_ms,
            }
        finally:
            conn.close()

    async def run_query(self, sql: str) -> Dict[str, Any]:
        """Execute a sanitized SELECT query and return rows + execution timing."""
        return await asyncio.to_thread(self._sync_run_query, sql)

    def _sync_pool_stats(self) -> Dict[str, Any]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION()")
            ver = cursor.fetchone()[0]
            cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads = cursor.fetchone()
            active_conn = int(threads[1]) if threads else 1
            cursor.execute("SHOW STATUS LIKE 'Uptime'")
            uptime_val = cursor.fetchone()
            uptime_sec = int(uptime_val[1]) if uptime_val else 0
            cursor.close()

            return {
                "status": "connected",
                "database": self.config["database"],
                "host": self.config["host"],
                "server_version": ver,
                "active_connections": active_conn,
                "uptime_seconds": uptime_sec,
            }
        finally:
            conn.close()

    async def pool_stats(self) -> Dict[str, Any]:
        """Return database connection status and server metrics."""
        return await asyncio.to_thread(self._sync_pool_stats)
