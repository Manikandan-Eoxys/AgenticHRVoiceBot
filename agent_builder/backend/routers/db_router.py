"""
agent_builder/backend/routers/db_router.py

Database Management Routes
===========================
FastAPI router for database administration endpoints.
  GET  /api/db/tables               - List all tables with row counts
  GET  /api/db/schema/{table_name}  - Get schema/columns for a table
  POST /api/db/query                - Execute a read-only SQL query
  GET  /api/db/pool                 - Get connection and server statistics
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

try:
    from controllers.dbManagementController import (
        get_table_list,
        get_table_schema,
        execute_query,
        get_pool_status,
    )
except ImportError:
    from agent_builder.backend.controllers.dbManagementController import (
        get_table_list,
        get_table_schema,
        execute_query,
        get_pool_status,
    )

router = APIRouter(prefix="/api/db", tags=["Database Management"])


class QueryRequest(BaseModel):
    sql: str


@router.get("/tables")
async def tables_endpoint():
    """List all tables in the database with row counts."""
    return await get_table_list()


@router.get("/schema/{table_name}")
async def schema_endpoint(table_name: str):
    """Return column definitions and key info for a specific table."""
    result = await get_table_schema(table_name)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/query")
async def query_endpoint(body: QueryRequest):
    """Execute a sanitized read-only SQL query and return results with timing."""
    result = await execute_query(body.sql)
    if result.get("status") == "error":
        status_code = 403 if "permitted" in result.get("message", "") else 500
        raise HTTPException(status_code=status_code, detail=result.get("message"))
    return result


@router.get("/pool")
async def pool_endpoint():
    """Return MySQL connection and server statistics."""
    return await get_pool_status()
