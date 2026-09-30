"""
agentDashboardController.py

Real-time monitoring and status API for the HR Voice Agent.

Responsibilities:
  - Exposes a lightweight FastAPI sub-application that the Agent Builder dashboard
    can query to get live status of the running agent worker
  - Provides HTTP + WebSocket endpoints for:
      GET  /status         — worker health, active call count, uptime
      GET  /calls          — list of currently active calls
      GET  /logs/stream    — server-sent events (SSE) stream of recent log lines
      WS   /ws/monitor     — WebSocket push for real-time voice state changes
  - Consumes GlobalMonitorFSM and VoiceController state (read-only, no side-effects)
  - Designed to be mounted as a sub-app in hr_voice_agent/main.py or run standalone

Security:
  - Bearer token auth (AGENT_DASHBOARD_TOKEN env var).
    If not set, the endpoints are open (dev mode only — set the token in production).

Usage (mount in hr_voice_agent/main.py):
    from controllers.agentDashboardController import create_dashboard_app
    app.mount("/dashboard", create_dashboard_app())
"""

import asyncio
import json
import logging
import os
import time
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger("controllers.agent_dashboard")

_WORKER_START_TIME: float = time.monotonic()
_DASHBOARD_TOKEN: str = os.environ.get("AGENT_DASHBOARD_TOKEN", "")

# In-memory ring buffer of recent log lines (last 200)
_log_buffer: list[dict] = []
_log_lock = asyncio.Lock()
_MAX_LOG_BUFFER = 200

# Connected WebSocket clients
_ws_clients: list[WebSocket] = []


# ---------------------------------------------------------------------------
# Log buffer feeder (attach as a Python logging handler)
# ---------------------------------------------------------------------------

class _DashboardLogHandler(logging.Handler):
    """Appends log records to the in-memory ring buffer for SSE/WS streaming."""

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": time.time(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if len(_log_buffer) >= _MAX_LOG_BUFFER:
            _log_buffer.pop(0)
        _log_buffer.append(entry)


def install_dashboard_log_handler() -> None:
    """Call once at startup to wire the dashboard log buffer."""
    h = _DashboardLogHandler()
    h.setLevel(logging.INFO)
    logging.getLogger().addHandler(h)
    logger.info("📊 [Dashboard] Log handler installed.")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_token(authorization: str = "") -> None:
    if not _DASHBOARD_TOKEN:
        return  # dev mode — no auth
    token = authorization.replace("Bearer ", "").strip()
    if token != _DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard token.")


# ---------------------------------------------------------------------------
# State snapshot helpers
# ---------------------------------------------------------------------------

def _get_voice_state() -> dict:
    """Reads current state from VoiceController (import lazily to avoid circular deps)."""
    try:
        from controllers.voice_controller import VoiceController
        vc = VoiceController()
        state = getattr(vc, "_state", "unknown")
        active_rooms = getattr(vc, "_active_rooms", {})
        return {
            "state": str(state),
            "active_call_count": len(active_rooms),
            "active_rooms": list(active_rooms.keys()) if isinstance(active_rooms, dict) else [],
        }
    except Exception as e:
        return {"state": "unavailable", "error": str(e)}


def _get_global_fsm_state() -> dict:
    """Reads current GlobalMonitorFSM state."""
    try:
        from controllers.global_monitor_fsm import GlobalMonitorFSM
        fsm = GlobalMonitorFSM.instance()
        return {
            "fsm_state": str(getattr(fsm, "_state", "unknown")),
            "total_calls": getattr(fsm, "_total_calls", 0),
        }
    except Exception as e:
        return {"fsm_state": "unavailable", "error": str(e)}


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------

def create_dashboard_app() -> FastAPI:
    """
    Creates and returns the dashboard FastAPI sub-application.

    Mount this with:
        app.mount("/dashboard", create_dashboard_app())
    """
    dashboard = FastAPI(
        title="HR Voice Agent — Dashboard API",
        description="Real-time monitoring API for the HR Voice Agent worker.",
        version="1.0.0",
    )

    dashboard.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    install_dashboard_log_handler()

    # ------------------------------------------------------------------
    # GET /status
    # ------------------------------------------------------------------

    @dashboard.get("/status", tags=["monitoring"])
    async def get_status(authorization: str = ""):
        _check_token(authorization)
        uptime_s = time.monotonic() - _WORKER_START_TIME
        voice = _get_voice_state()
        fsm = _get_global_fsm_state()
        return {
            "status": "ok",
            "uptime_seconds": round(uptime_s, 1),
            "voice": voice,
            "fsm": fsm,
            "log_buffer_size": len(_log_buffer),
        }

    # ------------------------------------------------------------------
    # GET /calls
    # ------------------------------------------------------------------

    @dashboard.get("/calls", tags=["monitoring"])
    async def get_active_calls(authorization: str = ""):
        _check_token(authorization)
        voice = _get_voice_state()
        return {
            "active_call_count": voice.get("active_call_count", 0),
            "active_rooms": voice.get("active_rooms", []),
        }

    # ------------------------------------------------------------------
    # GET /logs/stream  (Server-Sent Events)
    # ------------------------------------------------------------------

    @dashboard.get("/logs/stream", tags=["monitoring"])
    async def stream_logs(authorization: str = ""):
        _check_token(authorization)

        async def event_generator() -> AsyncGenerator[str, None]:
            # Send buffered history first
            for entry in list(_log_buffer):
                yield f"data: {json.dumps(entry)}\n\n"

            # Then stream new entries as they arrive
            last_len = len(_log_buffer)
            while True:
                await asyncio.sleep(0.5)
                current = _log_buffer[last_len:]
                for entry in current:
                    yield f"data: {json.dumps(entry)}\n\n"
                last_len = len(_log_buffer)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # WS /ws/monitor  (WebSocket push)
    # ------------------------------------------------------------------

    @dashboard.websocket("/ws/monitor")
    async def ws_monitor(websocket: WebSocket):
        await websocket.accept()
        _ws_clients.append(websocket)
        logger.info(f"📡 [Dashboard WS] Client connected. Total: {len(_ws_clients)}")
        try:
            while True:
                # Send a heartbeat / state snapshot every 2 s
                snapshot = {
                    "type": "snapshot",
                    "ts": time.time(),
                    **_get_voice_state(),
                    **_get_global_fsm_state(),
                }
                await websocket.send_json(snapshot)
                await asyncio.sleep(2.0)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            if websocket in _ws_clients:
                _ws_clients.remove(websocket)
            logger.info(f"📡 [Dashboard WS] Client disconnected. Remaining: {len(_ws_clients)}")

    return dashboard


# ---------------------------------------------------------------------------
# Broadcast helper (call from agent code to push state changes immediately)
# ---------------------------------------------------------------------------

async def broadcast_state_change(event_type: str, payload: dict) -> None:
    """
    Pushes an event to all connected WebSocket dashboard clients immediately.

    Call this from agent FSM transitions, tool calls, etc:
        from controllers.agentDashboardController import broadcast_state_change
        await broadcast_state_change("agent_state", {"new_state": "SPEAKING"})
    """
    if not _ws_clients:
        return
    message = json.dumps({"type": event_type, "ts": time.time(), **payload})
    dead: list[WebSocket] = []
    for ws in list(_ws_clients):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
