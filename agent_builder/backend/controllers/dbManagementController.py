"""
agent_builder/backend/controllers/dbManagementController.py

Database Management Controller
================================
Allows admin users to inspect MySQL tables, review schemas,
monitor connection pool stats, and execute read-only queries
via the Agent Builder Dashboard.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from services.dbManagerService import DbManagerService
except ImportError:
    from agent_builder.backend.services.dbManagerService import DbManagerService

_db_service = DbManagerService()


async def get_table_list() -> Dict[str, Any]:
    """Return all tables with row counts from the database."""
    try:
        tables = await _db_service.list_tables()
        return {"status": "ok", "tables": tables}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "tables": []}


async def get_table_schema(table_name: str) -> Dict[str, Any]:
    """Return column definitions and indexes for a given table."""
    try:
        schema = await _db_service.get_schema(table_name)
        return {"status": "ok", "table": table_name, "schema": schema}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "schema": []}


async def execute_query(sql: str) -> Dict[str, Any]:
    """
    Execute a read-only SQL query and return results with timing info.
    Only SELECT statements are permitted; write operations are blocked.
    """
    try:
        result = await _db_service.run_query(sql)
        return {"status": "ok", **result}
    except PermissionError as exc:
        return {"status": "error", "message": str(exc), "rows": [], "columns": []}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "rows": [], "columns": []}


async def get_pool_status() -> Dict[str, Any]:
    """Return current MySQL connection and server statistics."""
    try:
        stats = await _db_service.pool_stats()
        return {"status": "ok", **stats}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
