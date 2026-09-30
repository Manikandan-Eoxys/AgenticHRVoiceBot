"""
controllers/global_monitor_fsm.py

Global Multi-Agent Monitoring FSM  —  Embedded Loop Style
==========================================================
Mirrors the exact `run_state_machine()` / `match self.current_state:` pattern
of `VoiceController`.  The global FSM:

  - Has its own `current_state` / `next_state` pair.
  - One `match self.current_state:` block; each `case` reads the agent
    registry inline and decides `self.next_state`.
  - `run_state_machine()` is an async method you drive with a `while True:`
    loop — identical to how VoiceController is driven.
  - Start it once from `agent.py` (or `main.py`) as an asyncio background task:

        asyncio.create_task(GlobalMonitorFSM.instance().run_loop())

  - Individual VoiceControllers still call `on_agent_event()` to write into
    the agent registry (thread-safe dict write, non-blocking).
  - The FSM loop reads the registry every tick and drives its own state
    transitions, logging them as:
        "➡️ GlobalFSM Transition: IDLE → PARTIAL_LOAD"

Global States
-------------
  STARTING      System booting — no agents registered yet
  IDLE          All agents READY, 0 active calls
  PARTIAL_LOAD  Some (not all) agents are handling calls
  FULL_LOAD     Every registered agent slot is occupied
  DEGRADED      ≥ 1 agent in ERROR state
  SHUTDOWN      Shutdown requested — loop exits

Design Notes
------------
- `on_agent_event()` is a plain synchronous method (thread-safe lock).
  It only updates the registry; the FSM loop decides state transitions.
- `_push_monitoring()` is fire-and-forget via a daemon thread — the async
  event loop is never blocked by a slow monitoring server.
- Heartbeat push every `GLOBAL_FSM_HEARTBEAT_SECONDS` (default 10) even
  when global state has not changed.
- Immediate push on global state transitions (same hybrid strategy as
  VoiceController event hooks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent call states — an agent in one of these is "busy"
# ---------------------------------------------------------------------------
_ACTIVE_CALL_STATES = {
    "JOB_ACCEPTED",
    "CONNECTING",
    "SESSION_ACTIVE",
    "LISTENING",
    "THINKING",
    "SPEAKING",
    "ENDING",
}


# ---------------------------------------------------------------------------
# Global FSM States
# ---------------------------------------------------------------------------
class GlobalFSMState(Enum):
    STARTING     = auto()   # 1. No agents registered yet
    IDLE         = auto()   # 2. All agents READY, 0 active calls
    PARTIAL_LOAD = auto()   # 3. Some agents busy
    FULL_LOAD    = auto()   # 4. All agent slots occupied
    DEGRADED     = auto()   # 5. ≥ 1 agent in ERROR
    SHUTDOWN     = auto()   # 6. Shutdown requested


# ---------------------------------------------------------------------------
# Per-agent snapshot (updated by on_agent_event, read by run_state_machine)
# ---------------------------------------------------------------------------
@dataclass
class AgentSnapshot:
    agent_id: str           # str(pid) — unique per worker process
    state: str              # ControllerState.name e.g. "LISTENING"
    room: str = ""          # Active LiveKit room name
    participant: str = ""   # Caller identity
    state_since: float = field(default_factory=time.time)
    call_count: int = 0     # Total completed calls on this agent
    error_count: int = 0    # Total errors on this agent
    registered_at: float = field(default_factory=time.time)

    def is_active(self) -> bool:
        return self.state in _ACTIVE_CALL_STATES

    def to_api_dict(self) -> dict:
        ts = datetime.fromtimestamp(self.state_since, tz=timezone.utc).isoformat()
        return {
            "agent_id": self.agent_id,
            "state": self.state,
            "room": self.room,
            "participant": self.participant,
            "state_since": ts,
            "call_count": self.call_count,
            "error_count": self.error_count,
        }


# ---------------------------------------------------------------------------
# Global Monitor FSM — singleton, embedded loop style
# ---------------------------------------------------------------------------
class GlobalMonitorFSM:
    """
    Process-level singleton.  Mirrors VoiceController's run_state_machine() style.

    Startup
    -------
        # In agent.py, after creating the VoiceController:
        asyncio.create_task(GlobalMonitorFSM.instance().run_loop())

    Per-agent reporting
    -------------------
        # In VoiceController._report_to_global_fsm() (already wired):
        GlobalMonitorFSM.instance().on_agent_event(agent_id, state, room, participant)

    REST endpoint
    -------------
        # In token_server.py (already mounted):
        GET /monitor/agents         → GlobalMonitorFSM.instance().get_snapshot()
        GET /monitor/global-state   → lightweight state + counts
        GET /monitor/health         → 200 / 503
    """

    _instance: Optional["GlobalMonitorFSM"] = None
    _init_lock = threading.Lock()

    # ── Singleton factory ────────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "GlobalMonitorFSM":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Constructor ──────────────────────────────────────────────────────────
    def __init__(self) -> None:
        # FSM core — identical pattern to VoiceController
        self.current_state: GlobalFSMState = GlobalFSMState.STARTING
        self.next_state: GlobalFSMState = GlobalFSMState.STARTING
        self._previous_state: Optional[GlobalFSMState] = None
        self._state_updated_at: float = time.time()

        # Agent registry (written by on_agent_event, read by run_state_machine)
        self._lock = threading.Lock()
        self._agents: Dict[str, AgentSnapshot] = {}
        self._total_calls: int = 0       # across all agents, all time
        self._shutdown_requested: bool = False
        self._started_at: float = time.time()

        # Monitoring config
        self._monitoring_url: str = os.getenv(
            "MONITORING_LOG_URL", "http://192.168.0.230:8570/api/logs"
        )
        self._service_name: str = os.getenv(
            "MONITORING_SERVICE_NAME", "AgenticHRVoiceBot"
        )
        self._heartbeat_interval: float = float(
            os.getenv("GLOBAL_FSM_HEARTBEAT_SECONDS", "10")
        )
        self._last_heartbeat: float = 0.0

        logger.info("[GlobalFSM] Controller initialized in state: STARTING")

    # =========================================================================
    # 6-STATE GLOBAL FSM DISPATCHER  (mirrors VoiceController.run_state_machine)
    # =========================================================================
    async def run_state_machine(self) -> None:
        """
        One tick of the global FSM.  Call in a `while True:` loop.
        Each `case` reads the agent registry inline and sets self.next_state.
        Transitions are logged as:
            ➡️ GlobalFSM Transition [global]: IDLE → PARTIAL_LOAD
        """

        # ── 0. Stale Session Watchdog Check ────────────────────────────────────
        self._check_stale_sessions()

        match self.current_state:

            case GlobalFSMState.STARTING:
                # Wait until at least one agent has registered
                with self._lock:
                    agent_count = len(self._agents)

                if agent_count > 0:
                    self.next_state = GlobalFSMState.IDLE
                else:
                    self.next_state = GlobalFSMState.STARTING

            case GlobalFSMState.IDLE:
                with self._lock:
                    error_count  = sum(1 for s in self._agents.values() if s.state == "ERROR")
                    active_count = sum(1 for s in self._agents.values() if s.is_active())
                    total        = len(self._agents)

                if self._shutdown_requested:
                    self.next_state = GlobalFSMState.SHUTDOWN
                elif error_count > 0:
                    self.next_state = GlobalFSMState.DEGRADED
                elif total == 0:
                    self.next_state = GlobalFSMState.STARTING
                elif active_count == total and total > 0:
                    self.next_state = GlobalFSMState.FULL_LOAD
                elif active_count > 0:
                    self.next_state = GlobalFSMState.PARTIAL_LOAD
                else:
                    self.next_state = GlobalFSMState.IDLE

            case GlobalFSMState.PARTIAL_LOAD:
                with self._lock:
                    error_count  = sum(1 for s in self._agents.values() if s.state == "ERROR")
                    active_count = sum(1 for s in self._agents.values() if s.is_active())
                    total        = len(self._agents)

                if self._shutdown_requested:
                    self.next_state = GlobalFSMState.SHUTDOWN
                elif error_count > 0:
                    self.next_state = GlobalFSMState.DEGRADED
                elif active_count == 0:
                    self.next_state = GlobalFSMState.IDLE
                elif active_count == total and total > 0:
                    self.next_state = GlobalFSMState.FULL_LOAD
                else:
                    self.next_state = GlobalFSMState.PARTIAL_LOAD

            case GlobalFSMState.FULL_LOAD:
                with self._lock:
                    error_count  = sum(1 for s in self._agents.values() if s.state == "ERROR")
                    active_count = sum(1 for s in self._agents.values() if s.is_active())
                    total        = len(self._agents)

                if self._shutdown_requested:
                    self.next_state = GlobalFSMState.SHUTDOWN
                elif error_count > 0:
                    self.next_state = GlobalFSMState.DEGRADED
                elif active_count == 0:
                    self.next_state = GlobalFSMState.IDLE
                elif active_count < total:
                    self.next_state = GlobalFSMState.PARTIAL_LOAD
                else:
                    self.next_state = GlobalFSMState.FULL_LOAD

            case GlobalFSMState.DEGRADED:
                with self._lock:
                    error_count  = sum(1 for s in self._agents.values() if s.state == "ERROR")
                    active_count = sum(1 for s in self._agents.values() if s.is_active())
                    total        = len(self._agents)

                if self._shutdown_requested:
                    self.next_state = GlobalFSMState.SHUTDOWN
                elif error_count == 0:
                    # All errors cleared — recover
                    if active_count == 0:
                        self.next_state = GlobalFSMState.IDLE
                    elif active_count == total:
                        self.next_state = GlobalFSMState.FULL_LOAD
                    else:
                        self.next_state = GlobalFSMState.PARTIAL_LOAD
                else:
                    self.next_state = GlobalFSMState.DEGRADED

            case GlobalFSMState.SHUTDOWN:
                # Terminal — loop exits in run_loop()
                self.next_state = GlobalFSMState.SHUTDOWN

        # ── Apply transition (identical to VoiceController) ──────────────────
        if self.current_state != self.next_state:
            logger.info(
                f"➡️ GlobalFSM Transition [global]: "
                f"{self.current_state.name} → {self.next_state.name}"
            )
            self._previous_state = self.current_state
            self._state_updated_at = time.time()
            self.current_state = self.next_state
            # Immediate push on every global state transition
            self._push_monitoring_async()

        # ── Periodic heartbeat push (even when state unchanged) ───────────────
        if time.time() - self._last_heartbeat >= self._heartbeat_interval:
            self._last_heartbeat = time.time()
            self._push_monitoring_async()

        await asyncio.sleep(0.1)   # same rhythm as VoiceController (50–100ms tick)

    # ── Drive loop (start as asyncio.create_task) ────────────────────────────
    async def run_loop(self) -> None:
        """
        Runs the global FSM continuously until SHUTDOWN.
        Start from agent.py:
            asyncio.create_task(GlobalMonitorFSM.instance().run_loop())
        """
        logger.info("🌐 Global Monitor FSM loop started.")
        while self.current_state != GlobalFSMState.SHUTDOWN:
            try:
                await self.run_state_machine()
            except Exception as exc:
                logger.error(f"[GlobalFSM] Unhandled error in run_state_machine: {exc}")
                await asyncio.sleep(1.0)
        logger.info("🌐 Global Monitor FSM loop exited (SHUTDOWN).")

    # ── Public API called by VoiceController (thread-safe) ───────────────────
    def on_agent_event(
        self,
        agent_id: str,
        state,                  # ControllerState enum value or object with .name
        room: str = "",
        participant: str = "",
    ) -> None:
        """
        Called by each VoiceController on every state transition.
        Only updates the agent registry — the FSM loop decides transitions.
        Non-blocking: dict write under a threading.Lock.
        """
        state_name: str = state.name if hasattr(state, "name") else str(state)

        with self._lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = AgentSnapshot(
                    agent_id=agent_id,
                    state=state_name,
                    room=room,
                    participant=participant,
                )
                logger.info(
                    f"[GlobalFSM] Agent '{agent_id}' registered → {state_name}"
                )
            else:
                snap = self._agents[agent_id]
                old_state = snap.state

                # Count completed calls (ENDING → READY = one call done)
                if old_state == "ENDING" and state_name == "READY":
                    snap.call_count += 1
                    self._total_calls += 1

                # Count errors
                if state_name == "ERROR":
                    snap.error_count += 1

                snap.state = state_name
                snap.room = room or snap.room
                snap.participant = participant or snap.participant
                snap.state_since = time.time()

        logger.debug(
            f"[GlobalFSM] Registry update: agent='{agent_id}' "
            f"state={state_name} room='{room}' participant='{participant}'"
        )

    def on_agent_shutdown(self, agent_id: str) -> None:
        """Called when a worker process is exiting. Removes from registry."""
        with self._lock:
            removed = self._agents.pop(agent_id, None)
        if removed:
            logger.info(f"[GlobalFSM] Agent '{agent_id}' deregistered (shutdown).")

    def _check_stale_sessions(self) -> None:
        """
        Watchdog: Checks if any agent has been stuck in an active call state
        (CONNECTING, LISTENING, THINKING, SPEAKING, ENDING) beyond STALE_TIMEOUT.
        If stuck/hung, automatically marks the agent as ERROR to trigger alert/recovery.
        """
        now = time.time()
        stale_threshold = float(os.getenv("GLOBAL_FSM_STALE_TIMEOUT_SECONDS", "300"))  # Default 5 minutes

        with self._lock:
            for agent_id, snap in list(self._agents.items()):
                if snap.is_active() and (now - snap.state_since) > stale_threshold:
                    logger.warning(
                        f"⚠️ [GlobalFSM Watchdog] Agent '{agent_id}' stuck in '{snap.state}' "
                        f"for {now - snap.state_since:.1f}s (room: '{snap.room}', participant: '{snap.participant}'). "
                        f"Marking as ERROR to handle hung session."
                    )
                    snap.state = "ERROR"
                    snap.error_count += 1
                    snap.state_since = now

    def request_shutdown(self) -> None:
        """Signal the FSM loop to exit cleanly."""
        self._shutdown_requested = True
        logger.info("[GlobalFSM] Shutdown requested — FSM will exit after current tick.")

    # ── REST / snapshot API ───────────────────────────────────────────────────
    def get_global_state(self) -> GlobalFSMState:
        return self.current_state

    def get_snapshot(self) -> dict:
        """Returns the full JSON-serializable snapshot for the REST endpoint."""
        return self._build_payload()

    # ── Payload builder ───────────────────────────────────────────────────────
    def _build_payload(self) -> dict:
        uptime = int(time.time() - self._started_at)
        ts = datetime.now(tz=timezone.utc).isoformat()

        with self._lock:
            global_state = self.current_state.name
            active_calls = sum(1 for s in self._agents.values() if s.is_active())
            ready_agents = sum(1 for s in self._agents.values() if s.state == "READY")
            error_agents = sum(1 for s in self._agents.values() if s.state == "ERROR")
            total_agents = len(self._agents)
            total_calls  = self._total_calls
            agent_list   = [s.to_api_dict() for s in self._agents.values()]

        return {
            "message": (
                f"[GlobalFSM] {global_state} | "
                f"Active: {active_calls}/{total_agents} | "
                f"Total calls: {total_calls}"
            ),
            "service": self._service_name,
            "applicationName": self._service_name,
            "environment": "development",
            "level": "INFO",
            "timestamp": ts,
            "global_fsm": {
                "global_state": global_state,
                "previous_state": self._previous_state.name if self._previous_state else None,
                "active_calls": active_calls,
                "total_agents": total_agents,
                "ready_agents": ready_agents,
                "error_agents": error_agents,
                "total_calls_served": total_calls,
                "uptime_seconds": uptime,
                "agents": agent_list,
            },
        }

    # ── Monitoring push (fire-and-forget daemon thread) ───────────────────────
    def _push_monitoring_async(self) -> None:
        """Kick off a short-lived daemon thread to POST to the monitoring API."""
        payload = self._build_payload()
        t = threading.Thread(
            target=self._push_monitoring_sync,
            args=(payload,),
            daemon=True,
        )
        t.start()

    def _push_monitoring_sync(self, payload: dict) -> None:
        """Synchronous HTTP POST. Silently drops all errors."""
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._monitoring_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AgenticHRVoiceBot/GlobalFSM",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except urllib.error.URLError:
            pass
        except Exception:
            pass
