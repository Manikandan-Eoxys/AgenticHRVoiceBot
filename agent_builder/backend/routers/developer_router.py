"""
agent_builder/backend/routers/developer_router.py

Developer & Diagnostic Routes
=============================
FastAPI router for developer dashboard endpoints:
  GET  /api/developer/info     - Complete runtime diagnostics + developer profile
  GET  /api/developer/metrics  - Real-time CPU, memory, thread metrics
  GET  /api/developer/logs     - Filtered system logs
  GET  /api/developer/profile  - Stored developer contact and configuration
  POST /api/developer/profile  - Save/update developer profile persistently
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional

try:
    from controllers.developerController import (
        get_full_developer_info,
        get_runtime_metrics,
        get_system_logs,
        _get_stored_profile,
        save_developer_profile,
    )
except ImportError:
    from agent_builder.backend.controllers.developerController import (
        get_full_developer_info,
        get_runtime_metrics,
        get_system_logs,
        _get_stored_profile,
        save_developer_profile,
    )

router = APIRouter(prefix="/api/developer", tags=["Developer Diagnostics"])


class DeveloperProfileModel(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    slack_channel: Optional[str] = None
    environment: Optional[str] = None
    notes: Optional[str] = None


@router.get("/info")
async def info_endpoint():
    """Return aggregated developer and runtime diagnostics."""
    return get_full_developer_info()


@router.get("/metrics")
async def metrics_endpoint():
    """Return live system resource utilization."""
    return get_runtime_metrics()


@router.get("/logs")
async def logs_endpoint(level: str = "ALL"):
    """Return buffered system logs filtered by log level."""
    return get_system_logs(level)


@router.get("/profile")
async def get_profile_endpoint():
    """Return stored developer profile."""
    return {"status": "ok", "profile": _get_stored_profile()}


@router.post("/profile")
async def save_profile_endpoint(profile: DeveloperProfileModel):
    """Store/update developer profile persistently."""
    data = {k: v for k, v in profile.dict().items() if v is not None}
    saved = save_developer_profile(data)
    return {"status": "ok", "message": "Developer profile saved successfully", "profile": saved}
