"""
agent_builder/backend/routers/pages_router.py

Configuration Routes
=====================
FastAPI router for agent configuration management endpoints.
  GET  /api/pages/config           - Retrieve current agent_config.json
  PUT  /api/pages/config           - Update agent_config.json
  GET  /api/pages/goals            - List available goals
  POST /api/pages/config/reload    - Hot-reload config in running agent
"""

from fastapi import APIRouter, HTTPException
from typing import Any, Dict

from agent_builder.backend.controllers.agentBuilderPagesController import (
    get_agent_config,
    update_agent_config,
    list_goals,
)

router = APIRouter(prefix="/api/pages", tags=["Configuration"])


@router.get("/config")
async def get_config():
    """Return the current agent_config.json contents."""
    result = await get_agent_config()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
    return result


@router.put("/config")
async def put_config(body: Dict[str, Any]):
    """Write updated agent configuration to agent_config.json."""
    result = await update_agent_config(body)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.get("/goals")
async def get_goals():
    """List all available goal modules with metadata."""
    result = await list_goals()
    return result
