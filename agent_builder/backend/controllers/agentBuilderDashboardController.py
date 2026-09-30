"""
agentBuilderDashboardController.py

Builder dashboard data controller.

Responsibilities:
  - Aggregates live data from connected HR Voice Agent workers
  - Provides the data layer for the dashboard SPA:
      - Agent list (configured agents from DB/config)
      - Per-agent live status (proxied from agentDashboardController endpoints)
      - System health summary
      - Recent call logs (from SessionRecorder log files)
  - Caches remote agent status to avoid hammering agent workers

Architecture note:
  The builder backend READS from agent workers over HTTP (REST proxy).
  It NEVER writes to or restarts agent processes directly — operations
  like deploying or restarting agents are done via the agentBuilderPagesController
  which generates shell commands / service configs.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from controllers.agentBuilderLoginController import login_controller

logger = logging.getLogger("agent_builder.dashboard")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# URL of the running HR Voice Agent dashboard API
_AGENT_DASHBOARD_URL: str = os.environ.get(
    "AGENT_DASHBOARD_URL", "http://localhost:8570/dashboard"
)
_AGENT_DASHBOARD_TOKEN: str = os.environ.get("AGENT_DASHBOARD_TOKEN", "")

# Local logs directory (for call log browsing)
_LOGS_DIR: Path = Path(
    os.environ.get(
        "AGENT_LOGS_DIR",
        str(Path(__file__).resolve().parents[3] / "hr_voice_agent" / "logs"),
    )
)

# Status cache TTL (seconds)
_CACHE_TTL: float = 5.0
_status_cache: dict = {}
_cache_ts: float = 0.0


# ---------------------------------------------------------------------------
# Remote agent status proxy
# ---------------------------------------------------------------------------

async def _fetch_agent_status() -> dict:
    """Fetches live status from the running HR Voice Agent dashboard API."""
    global _status_cache, _cache_ts
    now = time.monotonic()
    if now - _cache_ts < _CACHE_TTL and _status_cache:
        return _status_cache

    headers = {}
    if _AGENT_DASHBOARD_TOKEN:
        headers["Authorization"] = f"Bearer {_AGENT_DASHBOARD_TOKEN}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_AGENT_DASHBOARD_URL}/status",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _status_cache = data
                    _cache_ts = now
                    return data
                return {"status": "error", "detail": f"HTTP {resp.status}"}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


async def _fetch_active_calls() -> dict:
    """Fetches active call list from the running HR Voice Agent."""
    headers = {}
    if _AGENT_DASHBOARD_TOKEN:
        headers["Authorization"] = f"Bearer {_AGENT_DASHBOARD_TOKEN}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_AGENT_DASHBOARD_URL}/calls",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"active_call_count": 0, "active_rooms": []}
    except Exception:
        return {"active_call_count": 0, "active_rooms": [], "error": "unreachable"}


# ---------------------------------------------------------------------------
# Call log browser
# ---------------------------------------------------------------------------

def _list_recent_call_logs(limit: int = 20) -> list[dict]:
    """
    Scans the agent logs directory for session log files and returns metadata
    for the most recent `limit` sessions.
    """
    if not _LOGS_DIR.exists():
        return []

    log_files = sorted(
        _LOGS_DIR.glob("*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    results = []
    for lf in log_files:
        stat = lf.stat()
        results.append({
            "filename": lf.name,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "path": str(lf),
        })
    return results


def _read_call_log(filename: str) -> str:
    """Reads the content of a specific call log file."""
    safe_name = Path(filename).name  # strip any path traversal
    log_path = _LOGS_DIR / safe_name
    if not log_path.exists() or not log_path.is_file():
        raise FileNotFoundError(f"Log file not found: {safe_name}")
    return log_path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Dashboard Router Factory
# ---------------------------------------------------------------------------

class AgentBuilderDashboardController:
    """
    Provides the FastAPI router for builder dashboard data endpoints.

    All routes require builder admin authentication.
    """

    def create_router(self) -> APIRouter:
        router = APIRouter(
            prefix="/dashboard",
            tags=["dashboard"],
            dependencies=[Depends(login_controller.require_admin)],
        )

        # GET /dashboard/overview
        @router.get("/overview")
        async def overview():
            """System-wide health and call summary."""
            agent_status = await _fetch_agent_status()
            calls = await _fetch_active_calls()
            return {
                "agent": agent_status,
                "calls": calls,
                "builder_version": "1.0.0",
                "timestamp": time.time(),
            }

        # GET /dashboard/status
        @router.get("/status")
        async def status():
            """Proxied live status from the HR Voice Agent worker."""
            return await _fetch_agent_status()

        # GET /dashboard/calls
        @router.get("/calls")
        async def active_calls():
            """Proxied active call list from the HR Voice Agent worker."""
            return await _fetch_active_calls()

        # GET /dashboard/logs
        @router.get("/logs")
        async def list_logs(limit: int = 20):
            """Lists recent call session log files."""
            logs = _list_recent_call_logs(limit=min(limit, 100))
            return {"logs": logs, "count": len(logs)}

        # GET /dashboard/logs/{filename}
        @router.get("/logs/{filename}")
        async def get_log(filename: str):
            """Returns the content of a specific call session log."""
            try:
                content = _read_call_log(filename)
                return {"filename": filename, "content": content}
            except FileNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

        return router


# Module-level singleton
dashboard_controller = AgentBuilderDashboardController()
