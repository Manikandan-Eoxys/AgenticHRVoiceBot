"""
controllers/monitor_router.py

FastAPI router — Global Multi-Agent Monitor REST API
=====================================================

Endpoints
---------
GET /monitor/agents
    Full snapshot of all registered agent FSMs + global state.
    Returns JSON matching the GlobalMonitorFSM payload format.

GET /monitor/global-state
    Lightweight — just the global state string and active call count.
    Useful for health checks and dashboard polling.

GET /monitor/health
    Simple liveness check. Returns 200 OK if the system is running.

Usage
-----
Mount into token_server.py:

    from controllers.monitor_router import router as monitor_router
    app.include_router(monitor_router)
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from controllers.global_monitor_fsm import GlobalMonitorFSM

router = APIRouter(prefix="/monitor", tags=["Monitor"])


@router.get("/agents")
async def get_agents() -> JSONResponse:
    """
    Returns the full global FSM snapshot:
    - global_state
    - active_calls, total_agents, ready_agents, error_agents
    - total_calls_served, uptime_seconds
    - agents[]: per-agent state, room, participant, call_count, error_count
    """
    snapshot = GlobalMonitorFSM.instance().get_snapshot()
    return JSONResponse(content=snapshot)


@router.get("/global-state")
async def get_global_state() -> JSONResponse:
    """
    Lightweight endpoint — just global state + call count.
    Suitable for high-frequency dashboard polling.
    """
    gfsm = GlobalMonitorFSM.instance()
    snapshot = gfsm.get_snapshot()
    fsm_data = snapshot.get("global_fsm", {})
    return JSONResponse(content={
        "global_state": fsm_data.get("global_state", "UNKNOWN"),
        "active_calls": fsm_data.get("active_calls", 0),
        "total_agents": fsm_data.get("total_agents", 0),
        "total_calls_served": fsm_data.get("total_calls_served", 0),
        "uptime_seconds": fsm_data.get("uptime_seconds", 0),
    })


@router.get("/health")
async def health_check() -> JSONResponse:
    """
    Simple liveness check.
    Returns 200 OK + current global state.
    """
    gfsm = GlobalMonitorFSM.instance()
    global_state = gfsm.get_global_state().name
    healthy = global_state not in ("DEGRADED", "SHUTDOWN")
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "global_state": global_state,
        },
    )


@router.get("/active-assistants")
async def get_active_assistants_endpoint() -> JSONResponse:
    """
    Returns only the list of currently active call assistants:
    PID, agent_id, state, room, participant, state_duration, call_duration.
    """
    gfsm = GlobalMonitorFSM.instance()
    active_list = gfsm.get_active_assistants()
    return JSONResponse(content={
        "status": "ok",
        "active_count": len(active_list),
        "assistants": active_list,
    })


@router.get("/kpis")
async def get_kpis_endpoint() -> JSONResponse:
    """
    Extracts real-time operational KPIs:
    - Concurrency (active calls, peak concurrent calls, utilization)
    - Reliability (health status, error rate, hung/stale count)
    - Performance (average call duration, max active duration)
    - State distribution breakdown
    """
    gfsm = GlobalMonitorFSM.instance()
    kpis = gfsm.get_kpis()
    return JSONResponse(content={"status": "ok", "kpis": kpis})


@router.post("/assistants/{pid}/close")
async def close_assistant_endpoint(pid: int, force: bool = False) -> JSONResponse:
    """
    Closes / terminates an assistant worker process using its PID.
    Pass ?force=true to send SIGKILL instead of SIGTERM.
    """
    gfsm = GlobalMonitorFSM.instance()
    result = gfsm.close_assistant_by_pid(pid, force=force)
    status_code = 200 if result.get("success") else 400
    return JSONResponse(status_code=status_code, content=result)


@router.post("/assistants/close-all")
async def close_all_assistants_endpoint(force: bool = False) -> JSONResponse:
    """
    Closes / terminates all currently active assistant worker processes.
    Pass ?force=true to send SIGKILL instead of SIGTERM.
    """
    gfsm = GlobalMonitorFSM.instance()
    result = gfsm.close_all_active_assistants(force=force)
    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)
