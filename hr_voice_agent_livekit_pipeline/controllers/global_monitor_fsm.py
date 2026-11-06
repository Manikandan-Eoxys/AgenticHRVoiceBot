"""
controllers/global_monitor_fsm.py

ONE Global Monitor FSM per machine.
  - Lives in the MAIN worker process only, in ONE dedicated thread (embedded super-loop).
  - Job processes (one agent / one pid per call) never run it. They only send
    state + heartbeat datagrams to it over local UDP.
"""
from __future__ import annotations

import json
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_HOST = "127.0.0.1"
_PORT = int(os.getenv("GLOBAL_FSM_PORT", "48999"))
_TICK_S = 0.1

_ACTIVE_CALL_STATES = {
    "JOB_ACCEPTED", "CONNECTING", "SESSION_ACTIVE",
    "LISTENING", "THINKING", "SPEAKING", "ENDING",
}


class GlobalFSMState(Enum):
    STARTING     = auto()   # thread up, socket not yet serving
    IDLE         = auto()   # no active calls
    PARTIAL_LOAD = auto()   # some calls active
    FULL_LOAD    = auto()   # active calls >= GLOBAL_FSM_MAX_CALLS (only when set)
    DEGRADED     = auto()   # any agent ERROR / hung / stuck
    SHUTDOWN     = auto()


# ═══════════════ client side — usable from ANY process, never starts anything ═══════════════
_client_sock: Optional[socket.socket] = None
_client_lock = threading.Lock()


def _is_main_process() -> bool:
    return (multiprocessing.current_process().name == "MainProcess"
            and multiprocessing.parent_process() is None)


def _send(msg: dict) -> None:
    """Fire-and-forget UDP datagram to the monitor. Never blocks, never raises."""
    global _client_sock
    try:
        with _client_lock:
            if _client_sock is None:
                _client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                _client_sock.setblocking(False)
        _client_sock.sendto(json.dumps(msg).encode(), (_HOST, _PORT))
    except Exception:
        pass


def report_agent_event(agent_id: str, state, room: str = "",
                       participant: str = "", pid: int = 0) -> None:
    if not global_monitor_running():
        start_global_monitor()
    _send({"type": "event", "agent_id": agent_id, "pid": pid or os.getpid(),
           "state": state.name if hasattr(state, "name") else str(state),
           "room": room, "participant": participant})


def report_agent_shutdown(agent_id: str) -> None:
    _send({"type": "shutdown", "agent_id": agent_id})


def _request(msg: dict, timeout: float = 0.5) -> Optional[dict]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(json.dumps(msg).encode(), (_HOST, _PORT))
        data, _ = s.recvfrom(65535)
        return json.loads(data.decode())
    except Exception:
        return None
    finally:
        s.close()


def global_monitor_running() -> bool:
    """Only CHECKS whether the monitor answers. Starts nothing."""
    reply = _request({"type": "ping"})
    return bool(reply and reply.get("type") == "pong")


def start_global_monitor() -> bool:
    """Starts the monitor if not already running."""
    if global_monitor_running():
        return False
    return GlobalMonitorFSM.instance().start_background()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                   # zombie = exited but not yet reaped
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except Exception:
        return True


# ═══════════════════════════════ registry entry ═══════════════════════════════
@dataclass
class AgentSnapshot:
    agent_id: str
    pid: int
    state: str
    room: str = ""
    participant: str = ""
    state_since: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    hung: bool = False          # active call but no heartbeat for GLOBAL_FSM_HANG_SECONDS
    stale: bool = False         # stuck in one active state too long
    call_count: int = 0
    error_count: int = 0
    registered_at: float = field(default_factory=time.time)
    call_started_at: Optional[float] = None

    def is_active(self) -> bool:
        return self.state in _ACTIVE_CALL_STATES

    def is_unhealthy(self) -> bool:
        return self.hung or self.stale or self.state == "ERROR"

    def to_api_dict(self) -> dict:
        now = time.time()
        call_dur = round(now - self.call_started_at, 1) if (self.call_started_at and self.is_active()) else 0.0
        return {
            "agent_id": self.agent_id,
            "pid": self.pid,
            "state": self.state,
            "is_active": self.is_active(),
            "room": self.room,
            "participant": self.participant,
            "state_since": datetime.fromtimestamp(self.state_since, tz=timezone.utc).isoformat(),
            "state_duration_seconds": round(now - self.state_since, 1),
            "call_duration_seconds": call_dur,
            "last_seen_age_s": round(now - self.last_seen, 1),
            "hung": self.hung,
            "stale": self.stale,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "registered_at": datetime.fromtimestamp(self.registered_at, tz=timezone.utc).isoformat(),
        }


# ═════════════════════════════════ the monitor ════════════════════════════════
class GlobalMonitorFSM:
    _instance: Optional["GlobalMonitorFSM"] = None
    _init_lock = threading.Lock()
    _start_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "GlobalMonitorFSM":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:                      # inert: no thread, no socket
        self.current_state = GlobalFSMState.STARTING
        self.next_state = GlobalFSMState.STARTING
        self._previous_state: Optional[GlobalFSMState] = None
        self._state_updated_at = time.time()

        self._lock = threading.Lock()
        self._agents: Dict[str, AgentSnapshot] = {}
        self._total_calls = 0
        self._shutdown_requested = False
        self._started_at = time.time()

        # Cumulative KPI metrics
        self._peak_concurrent_calls: int = 0
        self._total_errors: int = 0
        self._total_call_duration_seconds: float = 0.0
        self._completed_call_count: int = 0

        self._owner = False                          # True only in the process running the thread
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

        self._max_calls = int(os.getenv("GLOBAL_FSM_MAX_CALLS", "0"))          # 0 = unlimited
        self._hang_after = float(os.getenv("GLOBAL_FSM_HANG_SECONDS", "15"))
        self._stale_after = float(os.getenv("GLOBAL_FSM_STALE_TIMEOUT_SECONDS", "1800"))
        self._push_interval = float(os.getenv("GLOBAL_FSM_HEARTBEAT_SECONDS", "10"))
        self._monitoring_url = os.getenv("MONITORING_LOG_URL", "http://192.168.0.230:8570/api/logs")
        self._service_name = os.getenv("MONITORING_SERVICE_NAME", "AgenticHRVoiceBot")
        self._last_push = 0.0
        self._last_watchdog = 0.0
        self._last_kpi_log = 0.0
        self._kpi_log_interval = float(os.getenv("GLOBAL_FSM_KPI_LOG_SECONDS", "60"))

    # ── start exactly once per machine ──────────────────────────────────────
    def start_background(self) -> bool:
        with GlobalMonitorFSM._start_lock:
            if self._owner:
                return False
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass

            bound = False
            for attempt in range(5):
                try:
                    sock.bind((_HOST, _PORT))
                    bound = True
                    break
                except OSError:
                    if global_monitor_running():
                        sock.close()
                        return False
                    time.sleep(0.2)

            if not bound:
                sock.close()
                logger.warning(f"[GlobalFSM] Port {_PORT} is busy after retries. Set GLOBAL_FSM_PORT to a free port.")
                return False

            sock.setblocking(False)
            self._sock, self._owner = sock, True
        self._thread = threading.Thread(target=self._run, name="GlobalMonitorFSM", daemon=True)
        self._thread.start()
        logger.info(f"[GlobalFSM] Monitor listening on udp://{_HOST}:{_PORT}")
        return True

    # ── registry mutations ──────────────────────────────────────────────────
    def _apply_event(self, agent_id: str, state_name: str, room: str,
                     participant: str, pid: int) -> None:
        now = time.time()
        with self._lock:
            s = self._agents.get(agent_id)
            if s is None:
                call_started = now if state_name in _ACTIVE_CALL_STATES else None
                s = AgentSnapshot(
                    agent_id=agent_id, pid=pid, state=state_name,
                    room=room, participant=participant,
                    call_started_at=call_started)
                self._agents[agent_id] = s
                logger.info(f"[GlobalFSM] Process started: agent '{agent_id}' (pid={pid}) → {state_name}")
                self._update_kpi_counters_locked()
                return
            if s.hung:
                s.hung = False
                logger.info(f"[GlobalFSM] Agent '{agent_id}' (pid={s.pid}) recovered from hang.")
            call_completed = False
            call_dur = 0.0
            if state_name != s.state:                 # heartbeats do not reset state_since
                # If entering an active call
                if state_name in _ACTIVE_CALL_STATES and s.state not in _ACTIVE_CALL_STATES:
                    s.call_started_at = now

                # If leaving an active call
                if s.state in _ACTIVE_CALL_STATES and state_name not in _ACTIVE_CALL_STATES:
                    if s.call_started_at:
                        dur = now - s.call_started_at
                        self._total_call_duration_seconds += dur
                        self._completed_call_count += 1
                        call_dur = dur
                        s.call_started_at = None

                if s.state == "ENDING" and state_name == "READY":
                    s.call_count += 1
                    self._total_calls += 1
                    call_completed = True
                if state_name == "ERROR":
                    s.error_count += 1
                    self._total_errors += 1
                s.state, s.state_since, s.stale = state_name, now, False
            s.room = room or s.room
            s.participant = participant or s.participant
            s.pid = pid or s.pid
            s.last_seen = now
            self._update_kpi_counters_locked()

        if call_completed:
            self.log_kpis(trigger=f"Call Completed - Agent '{agent_id}' ({call_dur:.1f}s)")

    def _update_kpi_counters_locked(self) -> None:
        active = sum(1 for s in self._agents.values() if s.is_active())
        if active > self._peak_concurrent_calls:
            self._peak_concurrent_calls = active

    def _remove_agent(self, agent_id: str, reason: str) -> None:
        with self._lock:
            s = self._agents.pop(agent_id, None)
        if s:
            logger.info(f"[GlobalFSM] Process ended: agent '{agent_id}' (pid={s.pid}, "
                        f"last state={s.state}) — {reason}")

    # Public API kept for old call sites. Inside the owner it applies directly;
    # anywhere else it just forwards to the one real monitor.
    def on_agent_event(self, agent_id: str, state, room: str = "",
                       participant: str = "", pid: int = 0) -> None:
        if self._owner:
            self._apply_event(agent_id, state.name if hasattr(state, "name") else str(state),
                              room, participant, pid or os.getpid())
        else:
            report_agent_event(agent_id, state, room, participant, pid)

    def on_agent_shutdown(self, agent_id: str) -> None:
        if self._owner:
            self._remove_agent(agent_id, "deregistered")
        else:
            report_agent_shutdown(agent_id)

    def request_shutdown(self) -> None:
        self._shutdown_requested = True

    # ── socket intake (runs inside the monitor thread) ──────────────────────
    def _drain_socket(self) -> None:
        if self._sock is None:
            return
        while True:
            try:
                data, addr = self._sock.recvfrom(65535)
            except OSError:                           # nothing waiting (BlockingIOError) or socket error
                return
            try:
                msg = json.loads(data.decode())
                kind = msg.get("type")
                if kind == "event":
                    self._apply_event(msg["agent_id"], str(msg["state"]), msg.get("room", ""),
                                      msg.get("participant", ""), int(msg.get("pid", 0)))
                elif kind == "shutdown":
                    self._remove_agent(msg["agent_id"], "reported shutdown")
                elif kind == "ping":
                    self._reply(addr, {"type": "pong", "state": self.current_state.name,
                                       "agents": self._counts()[0]})
                elif kind == "snapshot":
                    self._reply(addr, self._build_payload())
                elif kind == "close_pid":
                    res = self._terminate_local_pid(int(msg.get("pid", 0)), bool(msg.get("force", False)))
                    self._reply(addr, {"type": "close_pid_reply", "result": res})
                elif kind == "close_all":
                    res = self.close_all_active_assistants(bool(msg.get("force", False)))
                    self._reply(addr, {"type": "close_all_reply", "result": res})
                elif kind == "get_active":
                    active_list = [s.to_api_dict() for s in self._agents.values() if s.is_active()]
                    self._reply(addr, {"type": "active_reply", "assistants": active_list})
                elif kind == "get_kpis":
                    self._reply(addr, {"type": "kpis_reply", "kpis": self.get_kpis()})
            except Exception as exc:
                logger.debug(f"[GlobalFSM] bad datagram ignored: {exc}")

    def _reply(self, addr, obj: dict) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(json.dumps(obj).encode(), addr)
        except OSError:
            pass

    # ── watchdog: dead, hung and stuck processes (monitor thread) ───────────
    def _watchdog(self) -> None:
        now = time.time()
        if now - self._last_watchdog < 1.0:
            return
        self._last_watchdog = now
        dead = []
        with self._lock:
            for aid, s in self._agents.items():
                if s.pid and not _pid_alive(s.pid):
                    dead.append(aid)
                    continue
                if s.is_active() and not s.hung and now - s.last_seen > self._hang_after:
                    s.hung = True
                    logger.warning(f"⚠️ [GlobalFSM] Agent '{aid}' (pid={s.pid}) HUNG — no heartbeat for "
                                   f"{now - s.last_seen:.0f}s (state={s.state}, room='{s.room}')")
                if s.is_active() and not s.hung and not s.stale and now - s.state_since > self._stale_after:
                    s.stale = True
                    logger.warning(f"⚠️ [GlobalFSM] Agent '{aid}' (pid={s.pid}) stuck in {s.state} "
                                   f"for {now - s.state_since:.0f}s (room='{s.room}')")
        for aid in dead:
            self._remove_agent(aid, "process no longer exists")

    def _counts(self):
        with self._lock:
            agents = list(self._agents.values())
        return (len(agents),
                sum(1 for s in agents if s.is_active()),
                sum(1 for s in agents if s.is_unhealthy()))

    def _load_state(self, active: int) -> GlobalFSMState:
        if self._max_calls and active >= self._max_calls:
            return GlobalFSMState.FULL_LOAD
        return GlobalFSMState.PARTIAL_LOAD

    # ════════════ embedded-style super-loop — the ONLY place the FSM executes ════════════
    def _run(self) -> None:
        logger.info(f"🌐 Global Monitor FSM thread started [pid={os.getpid()}].")

        while True:
            agents, active, unhealthy = 0, 0, 0
            try:
                self._drain_socket()
                self._watchdog()
                agents, active, unhealthy = self._counts()

                # ── 1. Evaluate logic for the CURRENT state ─────────────────
                match self.current_state:

                    case GlobalFSMState.STARTING:
                        self.next_state = GlobalFSMState.IDLE

                    case GlobalFSMState.IDLE:
                        if self._shutdown_requested:
                            self.next_state = GlobalFSMState.SHUTDOWN
                        elif unhealthy:
                            self.next_state = GlobalFSMState.DEGRADED
                        elif active:
                            self.next_state = self._load_state(active)

                    case GlobalFSMState.PARTIAL_LOAD:
                        if self._shutdown_requested:
                            self.next_state = GlobalFSMState.SHUTDOWN
                        elif unhealthy:
                            self.next_state = GlobalFSMState.DEGRADED
                        elif active == 0:
                            self.next_state = GlobalFSMState.IDLE
                        else:
                            self.next_state = self._load_state(active)

                    case GlobalFSMState.FULL_LOAD:
                        if self._shutdown_requested:
                            self.next_state = GlobalFSMState.SHUTDOWN
                        elif unhealthy:
                            self.next_state = GlobalFSMState.DEGRADED
                        elif active == 0:
                            self.next_state = GlobalFSMState.IDLE
                        else:
                            self.next_state = self._load_state(active)

                    case GlobalFSMState.DEGRADED:
                        if self._shutdown_requested:
                            self.next_state = GlobalFSMState.SHUTDOWN
                        elif unhealthy == 0:
                            self.next_state = (GlobalFSMState.IDLE if active == 0
                                               else self._load_state(active))

                    case GlobalFSMState.SHUTDOWN:
                        logger.info("🌐 Global Monitor FSM thread exiting (SHUTDOWN) — terminating all active call assistants.")
                        self.close_all_active_assistants(force=False)
                        break

                    case _:                              # defensive trap
                        self.next_state = GlobalFSMState.DEGRADED

            except Exception as exc:
                logger.error(f"[GlobalFSM] loop error in {self.current_state.name}: {exc}")
                self.next_state = self.current_state

            # ── 2. Synchronize states at the very end of the iteration ──────
            if self.next_state is not self.current_state:
                old = self.current_state
                self._previous_state = old
                self._state_updated_at = time.time()
                logger.info(f"➡️ GlobalFSM Transition [global]: {old.name} → {self.next_state.name} "
                            f"(active={active}, agents={agents}, unhealthy={unhealthy})")
                self.current_state = self.next_state
                self.log_kpis(trigger=f"{old.name} → {self.next_state.name}")
                self._push_monitoring_async()

            now_tick = time.time()
            if now_tick - self._last_push >= self._push_interval:
                self._last_push = now_tick
                self._push_monitoring_async()

            # Only log periodic KPI heartbeat if there are active calls being handled
            if self._kpi_log_interval > 0 and (now_tick - self._last_kpi_log >= self._kpi_log_interval):
                self._last_kpi_log = now_tick
                if active > 0:
                    self.log_kpis(trigger="Periodic")

            # ── 3. Cadence ──────────────────────────────────────────────────
            time.sleep(_TICK_S)

    # ── Active assistants management ─────────────────────────────────────────
    def get_active_assistants(self) -> list[dict]:
        """Returns the list of all currently active call assistants."""
        if not self._owner:
            reply = _request({"type": "get_active"})
            if reply and isinstance(reply.get("assistants"), list):
                return reply["assistants"]
        with self._lock:
            return [s.to_api_dict() for s in self._agents.values() if s.is_active()]

    # ── KPI and metrics extraction ──────────────────────────────────────────
    def get_kpis(self) -> dict:
        """Extracts comprehensive real-time KPIs and historical metrics."""
        if not self._owner:
            reply = _request({"type": "get_kpis"})
            if reply and isinstance(reply.get("kpis"), dict):
                return reply["kpis"]
        return self._compute_kpis()

    def _compute_kpis(self) -> dict:
        now = time.time()
        with self._lock:
            agents = list(self._agents.values())
            total_calls = self._total_calls
            peak_calls = self._peak_concurrent_calls
            total_errors = self._total_errors
            total_dur = self._total_call_duration_seconds
            completed_calls = self._completed_call_count
            started_at = self._started_at

        total_agents = len(agents)
        active_agents = [s for s in agents if s.is_active()]
        active_count = len(active_agents)
        ready_count = sum(1 for s in agents if s.state == "READY" and not s.hung)
        hung_count = sum(1 for s in agents if s.hung)
        stale_count = sum(1 for s in agents if s.stale)
        error_count = sum(1 for s in agents if s.state == "ERROR")
        unhealthy_count = sum(1 for s in agents if s.is_unhealthy())

        state_breakdown: dict[str, int] = {}
        for s in agents:
            state_breakdown[s.state] = state_breakdown.get(s.state, 0) + 1

        max_active_dur = 0.0
        for s in active_agents:
            if s.call_started_at:
                dur = now - s.call_started_at
                if dur > max_active_dur:
                    max_active_dur = dur

        if self._max_calls > 0:
            utilization = round((active_count / self._max_calls) * 100, 1)
        elif total_agents > 0:
            utilization = round((active_count / total_agents) * 100, 1)
        else:
            utilization = 0.0

        all_calls_attempted = max(1, total_calls + active_count)
        error_rate = round((total_errors / all_calls_attempted) * 100, 2)
        avg_dur = round(total_dur / max(1, completed_calls), 1)

        health_status = "HEALTHY"
        if error_count > 0 or hung_count > 0 or stale_count > 0:
            health_status = "DEGRADED" if unhealthy_count < max(1, total_agents) else "CRITICAL"

        return {
            "concurrency": {
                "active_calls": active_count,
                "peak_concurrent_calls": max(peak_calls, active_count),
                "total_agents": total_agents,
                "ready_agents": ready_count,
                "capacity_utilization_pct": utilization,
                "max_configured_calls": self._max_calls if self._max_calls > 0 else "unlimited",
            },
            "reliability": {
                "health_status": health_status,
                "total_calls_served": total_calls,
                "total_errors": total_errors,
                "error_rate_pct": error_rate,
                "hung_agents": hung_count,
                "stale_agents": stale_count,
                "unhealthy_agents": unhealthy_count,
            },
            "performance": {
                "avg_call_duration_seconds": avg_dur,
                "max_active_call_duration_seconds": round(max_active_dur, 1),
                "completed_calls_count": completed_calls,
                "system_uptime_seconds": int(now - started_at),
            },
            "state_breakdown": state_breakdown,
        }

    def log_kpis(self, trigger: str = "") -> None:
        """Logs a formatted real-time KPI summary line to logger.info."""
        try:
            kpis = self._compute_kpis() if self._owner else self.get_kpis()
            con = kpis.get("concurrency", {})
            rel = kpis.get("reliability", {})
            perf = kpis.get("performance", {})

            tag = f" [{trigger}]" if trigger else ""
            logger.info(
                f"📊 [GlobalFSM KPI{tag}] "
                f"Calls Served: {rel.get('total_calls_served', 0)} | "
                f"Active: {con.get('active_calls', 0)} | "
                f"Peak: {con.get('peak_concurrent_calls', 0)} | "
                f"Avg Duration: {perf.get('avg_call_duration_seconds', 0.0)}s | "
                f"Errors: {rel.get('total_errors', 0)} ({rel.get('error_rate_pct', 0.0)}%) | "
                f"Health: {rel.get('health_status', 'HEALTHY')}"
            )
        except Exception as exc:
            logger.debug(f"[GlobalFSM] Failed to log KPIs: {exc}")

    # ── PID-based termination / process close ────────────────────────────────
    def close_assistant_by_pid(self, pid: int, force: bool = False) -> dict:
        """
        Terminates the worker process of an assistant by PID.
        Sends SIGTERM (or SIGKILL if force=True).
        """
        if not self._owner:
            reply = _request({"type": "close_pid", "pid": pid, "force": force})
            if reply and isinstance(reply.get("result"), dict):
                return reply["result"]
            return self._terminate_local_pid(pid, force)

        return self._terminate_local_pid(pid, force)

    def _terminate_local_pid(self, pid: int, force: bool = False) -> dict:
        if pid <= 1:
            return {"success": False, "pid": pid, "message": f"Invalid PID {pid}."}
        if pid == os.getpid():
            return {"success": False, "pid": pid, "message": "Cannot terminate monitor main process."}

        target_aid = None
        target_room = ""
        with self._lock:
            for aid, s in self._agents.items():
                if s.pid == pid:
                    target_aid = aid
                    target_room = s.room
                    break

        sig = signal.SIGKILL if force else signal.SIGTERM
        sig_name = "SIGKILL" if force else "SIGTERM"

        if not _pid_alive(pid):
            if target_aid:
                self._remove_agent(target_aid, f"PID {pid} already dead")
            return {
                "success": True,
                "pid": pid,
                "agent_id": target_aid or str(pid),
                "message": f"Process PID {pid} was already terminated."
            }

        try:
            os.kill(pid, sig)
            logger.info(f"🛑 [GlobalFSM] Sent {sig_name} to assistant PID {pid} (agent '{target_aid}', room='{target_room}').")
            if target_aid:
                self._remove_agent(target_aid, f"closed via {sig_name} request")
            return {
                "success": True,
                "pid": pid,
                "agent_id": target_aid or str(pid),
                "room": target_room,
                "signal": sig_name,
                "message": f"Successfully sent {sig_name} to assistant PID {pid}."
            }
        except ProcessLookupError:
            if target_aid:
                self._remove_agent(target_aid, f"PID {pid} no longer found")
            return {
                "success": True,
                "pid": pid,
                "agent_id": target_aid or str(pid),
                "message": f"Process PID {pid} no longer exists."
            }
        except PermissionError as exc:
            logger.error(f"❌ [GlobalFSM] Permission denied sending {sig_name} to PID {pid}: {exc}")
            return {
                "success": False,
                "pid": pid,
                "message": f"Permission denied attempting to terminate PID {pid}: {exc}"
            }
        except Exception as exc:
            logger.error(f"❌ [GlobalFSM] Failed to terminate PID {pid}: {exc}")
            return {
                "success": False,
                "pid": pid,
                "message": f"Failed to terminate PID {pid}: {exc}"
            }

    def close_all_active_assistants(self, force: bool = False) -> dict:
        """Terminates all worker processes currently handling active calls."""
        if not self._owner:
            reply = _request({"type": "close_all", "force": force})
            if reply and isinstance(reply.get("result"), dict):
                return reply["result"]

        with self._lock:
            active_pids = [s.pid for s in self._agents.values() if s.is_active() and s.pid]

        results = []
        for pid in active_pids:
            res = self._terminate_local_pid(pid, force=force)
            results.append(res)

        closed = [r for r in results if r.get("success")]
        failed = [r for r in results if not r.get("success")]
        return {
            "success": len(failed) == 0,
            "total_targeted": len(active_pids),
            "closed_count": len(closed),
            "failed_count": len(failed),
            "closed": closed,
            "failed": failed,
        }

    # ── snapshot / REST (works from any process) ────────────────────────────
    def get_snapshot(self) -> dict:
        if self._owner:
            return self._build_payload()
        return _request({"type": "snapshot"}) or self._build_payload()

    def get_global_state(self) -> GlobalFSMState:
        if self._owner:
            return self.current_state
        reply = _request({"type": "ping"})
        name = reply.get("state") if reply else None
        return GlobalFSMState[name] if (isinstance(name, str) and name in GlobalFSMState.__members__) else self.current_state

    @property
    def _state(self) -> GlobalFSMState:                # alias used by the dashboard controller
        return self.get_global_state()

    async def run_loop(self) -> None:                  # kept so old callers don't break; no task
        return

    async def run_state_machine(self) -> None:
        return

    def _build_payload(self) -> dict:
        with self._lock:
            agents = list(self._agents.values())
            total_calls = self._total_calls
        active = sum(1 for s in agents if s.is_active())
        hung = sum(1 for s in agents if s.hung)
        gs = self.current_state.name
        kpis = self._compute_kpis()
        active_list = [s.to_api_dict() for s in agents if s.is_active()]
        return {
            "message": f"[GlobalFSM] {gs} | Active: {active}/{len(agents)} | Hung: {hung} | Total calls: {total_calls}",
            "service": self._service_name, "applicationName": self._service_name,
            "environment": "development", "level": "INFO",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "kpis": kpis,
            "active_assistants": active_list,
            "global_fsm": {
                "global_state": gs,
                "previous_state": self._previous_state.name if self._previous_state else None,
                "active_calls": active, "total_agents": len(agents),
                "ready_agents": sum(1 for s in agents if s.state == "READY" and not s.hung),
                "unhealthy_agents": sum(1 for s in agents if s.is_unhealthy()),
                "hung_agents": hung,
                "total_calls_served": total_calls,
                "uptime_seconds": int(time.time() - self._started_at),
                "agents": [s.to_api_dict() for s in agents],
            },
        }

    def _push_monitoring_async(self) -> None:
        threading.Thread(target=self._push_monitoring_sync,
                         args=(self._build_payload(),), daemon=True).start()

    def _push_monitoring_sync(self, payload: dict) -> None:
        try:
            req = urllib.request.Request(
                self._monitoring_url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "User-Agent": "AgenticHRVoiceBot/GlobalFSM"},
                method="POST")
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass


# ═══════════════ Public Convenience Functions ═══════════════
def get_active_assistants() -> list[dict]:
    """Returns the list of currently active call assistants."""
    return GlobalMonitorFSM.instance().get_active_assistants()


def get_kpis() -> dict:
    """Extracts real-time KPIs and metrics from GlobalMonitorFSM."""
    return GlobalMonitorFSM.instance().get_kpis()


def log_kpis(trigger: str = "") -> None:
    """Logs real-time KPIs and metrics from GlobalMonitorFSM."""
    GlobalMonitorFSM.instance().log_kpis(trigger=trigger)


def close_assistant_by_pid(pid: int, force: bool = False) -> dict:
    """Closes / terminates an assistant worker process using its PID."""
    return GlobalMonitorFSM.instance().close_assistant_by_pid(pid, force=force)


def close_all_active_assistants(force: bool = False) -> dict:
    """Closes / terminates all worker processes handling active calls."""
    return GlobalMonitorFSM.instance().close_all_active_assistants(force=force)


__all__ = [
    "GlobalMonitorFSM", "GlobalFSMState", "AgentSnapshot", "start_global_monitor",
    "global_monitor_running", "report_agent_event", "report_agent_shutdown",
    "get_active_assistants", "get_kpis", "log_kpis", "close_assistant_by_pid", "close_all_active_assistants"
]
