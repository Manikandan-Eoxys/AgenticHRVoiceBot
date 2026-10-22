"""
controllers/agentVoiceController.py

Voice Controller for HR Voice Assistant — Loop-Driven Match-Case State Machine.
"""

from __future__ import annotations

import os
import json
import logging
import time
import urllib.request
import atexit
import asyncio
import multiprocessing
from enum import Enum, auto
from typing import Any, Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from goals.base import GoalService

from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
)

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    DEEPGRAM_API_KEY,
    SYSTEM_PROMPT,
)
from integrations.sync_scheduler import start_scheduler, stop_scheduler
from controllers.global_monitor_fsm import report_agent_event
from controllers.agentToolsController import AgentToolsController

logger = logging.getLogger(__name__)


# -----------------------
# STATES
# -----------------------
class ControllerState(Enum):
    UNINITIALIZED = auto()  # 1. Booted
    INITIALIZING  = auto()  # 2. Engines building
    PREWARMING    = auto()  # 3. Model & DB prewarming
    READY         = auto()  # 4. System ready
    JOB_ACCEPTED  = auto()  # 5. Call job accepted
    CONNECTING    = auto()  # 6. Room connecting
    SESSION_ACTIVE= auto()  # 7. Call live
    LISTENING     = auto()  # 8. User speaking / STT listening
    THINKING      = auto()  # 9. LLM generating / executing tools
    SPEAKING      = auto()  # 10. TTS synthesizing / playing voice
    ENDING        = auto()  # 11. Disconnecting / tearing down
    ERROR         = auto()  # 12. Exception / error


_SUBSTATE_TARGET = {
    "LISTENING": ControllerState.LISTENING,
    "THINKING":  ControllerState.THINKING,
    "SPEAKING":  ControllerState.SPEAKING,
}

# States where an active call is in progress (used by the FSM guard and shutdown)
_IN_CALL = (
    ControllerState.CONNECTING,
    ControllerState.SESSION_ACTIVE,
    ControllerState.LISTENING,
    ControllerState.THINKING,
    ControllerState.SPEAKING,
)

_scheduler_started = False


def _prime_ollama_model(base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL) -> None:
    """Synchronous warm-up request so the first real turn isn't slow."""
    try:
        ollama_host = base_url.rstrip("/")
        if ollama_host.endswith("/v1"):
            ollama_host = ollama_host[:-3]

        payload = json.dumps({
            "model": (model or "qwen3:8b").strip(),
            "prompt": "Hello",  # Warm up model into memory quickly
            "stream": False,  # Non-streaming single response
            "keep_alive": -1,  # Keep model loaded indefinitely in GPU/RAM
            "think": False,  # Top-level parameter in Ollama to suppress chain-of-thought
            "options": {
                "num_predict": 1,  # Generate only 1 token for warm-up
                "num_thread": 8,  # Leverage CPU cores
            },
        }).encode()

        req = urllib.request.Request(
            f"{ollama_host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        logger.info(f"[FSM] Priming Ollama model '{model}'...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        logger.info("[FSM] Ollama model loaded and system prompt primed.")
    except Exception as exc:
        logger.warning(f"[FSM] Ollama warm-up skipped (non-fatal): {exc}")


def prewarm_worker_resources(startup_checks: Callable[[], None] | None = None) -> None:
    """Scheduler + Ollama priming + DB check. Main worker process only."""
    global _scheduler_started
    proc = multiprocessing.current_process()
    if proc.name != "MainProcess" or multiprocessing.parent_process() is not None:
        logger.info("⚡ Job process: worker prewarm is owned by the main process; skipping.")
        return
    if _scheduler_started:
        return

    start_scheduler()
    atexit.register(stop_scheduler)
    _scheduler_started = True
    logger.info("⏳ Background sync scheduler started.")
    _prime_ollama_model(OLLAMA_BASE_URL, OLLAMA_MODEL)
    if startup_checks:
        startup_checks()


class VoiceController:
    """
    Loop-driven FSM controller for the HR Voice Assistant.
    Coordinates STT, LLM, TTS construction and LiveKit session lifecycle
    through a single `match self.current_state:` dispatcher across 12 explicit states.
    """

    def __init__(
        self,
        ollama_model: str = OLLAMA_MODEL,
        ollama_base_url: str = OLLAMA_BASE_URL,
        deepgram_api_key: str | None = DEEPGRAM_API_KEY,
        system_prompt: str = SYSTEM_PROMPT,
        goal_classes: list[type[GoalService]] | None = None,
        call_type: str = "inbound",
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ):
        self.ollama_model = (ollama_model or "qwen3:8b").strip()
        self.ollama_base_url = ollama_base_url
        self.deepgram_api_key = deepgram_api_key
        self.system_prompt = system_prompt

        # Engines (populated by INITIALIZING)
        self.llm: Optional[Any] = None
        self.stt: Optional[Any] = None
        self.tts: Optional[Any] = None

        # FSM core — 12-state transition engine
        self.current_state: ControllerState = ControllerState.UNINITIALIZED
        self.next_state: ControllerState = ControllerState.UNINITIALIZED
        self._state_updated_at: float = time.time()
        self._previous_state: Optional[ControllerState] = None
        self._error_message: Optional[str] = None

        self._pending_proc: Optional[JobProcess] = None
        self._active_ctx: Optional[JobContext] = None
        self._active_agent: Optional[Any] = None
        self._active_session: Optional[AgentSession] = None
        self._active_participant_identity: str = ""
        self._shutdown_requested: bool = False

        # Facts written by event handlers, read (and consumed) by run_state_machine()
        self._call_requested: bool = False
        self._agent_substate: str = ""          # last LiveKit agent_state: listening/thinking/speaking/...
        self._error_requested: bool = False

        self._pid = os.getpid()
        self._room_name: str = (
            job_ctx.room.name if (job_ctx and getattr(job_ctx, "room", None)) else ""
        )
        self._agent_id = str(self._pid)
        self._last_beat = 0.0
        self._fsm_task: Optional[asyncio.Task] = None
        self._idle_watchdog_task: Optional[asyncio.Task] = None  # Cancelled on call end
        self._ready_event = asyncio.Event()      # set the first time READY is reached
        self._startup_checks: Optional[Callable[[], None]] = None
        self._call_start_times: dict[str, float] = {}

        # Watchdog timeouts & engine status
        self.llm_timeout: float = 30.0
        self.stt_timeout: float = 30.0
        self.tts_timeout: float = 20.0
        self.caller_connect_timeout: float = 45.0
        self.stt_status: str = "idle"
        self.llm_status: str = "idle"
        self.tts_status: str = "idle"
        self._scheduler_started: bool = False

        self.call_type: str = call_type
        self.job_ctx: Any = job_ctx
        self.collector: Any = collector
        self.call_metadata: dict = call_metadata or {}

        # Tools Controller manages all Domain Business Logic (Goal Services), DB tools & 3rd-party integrations
        self.tools_controller: AgentToolsController = AgentToolsController(
            goal_classes=goal_classes,
            call_type=self.call_type,
            job_ctx=self.job_ctx,
            collector=self.collector,
            call_metadata=self.call_metadata,
        )

        logger.info(f"[FSM] Controller initialized [pid={self._pid}] in state: {self.current_state.name}")
        # Register this agent with the global monitor
        report_agent_event(
            self._agent_id,
            self.current_state,
            room=self._room_name,
            pid=self._pid,
        )

    def _stuck_for(self, seconds: float) -> bool:
        """Returns True if the current state has been active longer than `seconds`."""
        return (time.time() - self._state_updated_at) > seconds


    def ensure_fsm_running(self) -> None:
        """Starts VoiceControllerFSM loop task if not already running."""
        if self._fsm_task is None or self._fsm_task.done():
            self._fsm_task = asyncio.create_task(self.run_loop(), name="VoiceControllerFSM")

    async def run_loop(self) -> None:
        """Embedded-style super-loop. The ONLY place the FSM executes. Never exits."""
        logger.info(f"🚀 Voice Controller 12-State FSM Started [pid={self._pid}]")

        while True:
            try:
                # ── 1. Evaluate state logic based on CURRENT state ────────────────
                match self.current_state:

                    case ControllerState.UNINITIALIZED:
                        self.next_state = ControllerState.INITIALIZING

                    case ControllerState.INITIALIZING:
                        self._build_engines()
                        self.next_state = ControllerState.PREWARMING

                    case ControllerState.PREWARMING:
                        await asyncio.to_thread(self.prewarm_resources_once)
                        self.next_state = ControllerState.READY

                    case ControllerState.READY:
                        self._shutdown_requested = False          # drop stale flags
                        self._error_requested = False
                        if self._call_requested:
                            self.next_state = ControllerState.JOB_ACCEPTED

                    case ControllerState.JOB_ACCEPTED:
                        self._call_requested = False
                        self.next_state = ControllerState.CONNECTING

                    case (ControllerState.CONNECTING | ControllerState.SESSION_ACTIVE
                        | ControllerState.LISTENING | ControllerState.THINKING
                        | ControllerState.SPEAKING):

                        # Faults and hangups take priority over everything else
                        if self._error_requested:
                            self._error_requested = False
                            self.next_state = ControllerState.ERROR

                        elif self._shutdown_requested:
                            self.next_state = ControllerState.ENDING

                        elif self.current_state is ControllerState.CONNECTING:
                            if self._active_participant_identity:
                                self.next_state = ControllerState.SESSION_ACTIVE
                            elif self._stuck_for(self.caller_connect_timeout):
                                logger.warning(f"⏱️ Timed out waiting for caller after {self.caller_connect_timeout}s.")
                                self._error_message = "Caller participant did not join in time"
                                self.next_state = ControllerState.ERROR

                        else:
                            # SESSION_ACTIVE / LISTENING / THINKING / SPEAKING:
                            # sub-state follows what LiveKit last reported
                            self.next_state = _SUBSTATE_TARGET.get(
                                self._agent_substate.upper(), ControllerState.SESSION_ACTIVE)

                            # Per-state stall watchdogs (log once per stuck period)
                            if self.current_state is ControllerState.LISTENING:
                                if self.stt_status == "idle":
                                    self.stt_status = "listening"
                                elif self.stt_status == "listening" and self._stuck_for(self.stt_timeout):
                                    self.stt_status = "stalled"
                                    logger.debug(f"[STT Watchdog] LISTENING stalled >{self.stt_timeout}s")
                            elif self.current_state is ControllerState.THINKING:
                                if self.llm_status == "idle":
                                    self.llm_status = "generating"
                                elif self.llm_status == "generating" and self._stuck_for(self.llm_timeout):
                                    self.llm_status = "slow"
                                    logger.warning(f"⚠️ [LLM Watchdog] THINKING stalled >{self.llm_timeout}s")
                            elif self.current_state is ControllerState.SPEAKING:
                                if self.tts_status == "idle":
                                    self.tts_status = "synthesizing"
                                elif self.tts_status == "synthesizing" and self._stuck_for(self.tts_timeout):
                                    self.tts_status = "stalled"
                                    logger.warning(f"⚠️ [TTS Watchdog] SPEAKING stalled >{self.tts_timeout}s")

                    case ControllerState.ENDING:
                        self._shutdown_requested = False
                        if self._active_session is not None:
                            try:
                                await asyncio.wait_for(self._active_session.aclose(), timeout=10.0)
                            except Exception as exc:
                                logger.debug(f"Session aclose exception (non-fatal): {exc}")
                            self._active_session = None
                        self._active_ctx = None
                        self._active_agent = None
                        self._active_participant_identity = ""
                        self._agent_substate = ""
                        # _call_requested is intentionally NOT cleared: a call that arrived
                        # during teardown is picked up by READY → JOB_ACCEPTED.
                        logger.info("🧹 Reset state for next call job")
                        self.next_state = ControllerState.READY

                    case ControllerState.ERROR:
                        logger.error(f"🛑 ControllerState.ERROR: {self._error_message}")
                        self.next_state = ControllerState.ENDING      # recover via teardown

                    case _:
                        # Defensive fallback trap
                        self._error_message = f"Unknown state: {self.current_state}"
                        self.next_state = ControllerState.ERROR

            except asyncio.CancelledError:
                raise                                  # process / event loop shutdown only
            except Exception as exc:
                logger.exception(f"Unhandled error in state {self.current_state.name}: {exc}")
                self._error_message = str(exc)
                # A fault inside ERROR/ENDING must not trap the loop there forever
                self.next_state = (
                    ControllerState.READY
                    if self.current_state in (ControllerState.ERROR, ControllerState.ENDING)
                    else ControllerState.ERROR
                )

            # ── 2. Synchronize states at the very end of the iteration ────────────
            if self.next_state is not self.current_state:
                old = self.current_state
                self._previous_state = old
                self._state_updated_at = time.time()
                self.stt_status = self.llm_status = self.tts_status = "idle"
                tag = f"pid={self._pid}" + (f" | room='{self._room_name}'" if self._room_name else "")
                logger.info(f"➡️ VC Transition [{tag}]: {old.name} → {self.next_state.name}")
                self.current_state = self.next_state            # currentState = nextState
                if self.current_state is ControllerState.READY:
                    self._ready_event.set()
                else:
                    self._ready_event.clear()
                self._report_to_global_fsm()

            # ── 2b. Heartbeat: proves this process's loop is alive ────────────────
            _now = time.time()
            if _now - self._last_beat >= 3.0:
                self._last_beat = _now
                self._report_to_global_fsm()

            # ── 3. System cadence delay (also yields to the asyncio event loop) ───
            await asyncio.sleep(0.05)

    async def wait_until_ready(self, timeout: float = 150.0) -> None:
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)

    def setup_call(
        self,
        call_type: str | None = None,
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ) -> None:
        """
        Configures this VoiceController instance for an active call session,
        delegating all domain goal services and tool setup to tools_controller.
        """
        self.call_type = call_type or "inbound"
        self.job_ctx = job_ctx
        self.collector = collector
        self.call_metadata = call_metadata or {}

        self.tools_controller.setup_call(
            call_type=self.call_type,
            job_ctx=job_ctx,
            collector=collector,
            call_metadata=self.call_metadata,
        )

        logger.info(
            f"🎯 [VoiceController] Configured for call_type='{self.call_type}' "
            f"with {len(self.active_goal_ids)} active goals via AgentToolsController."
        )

    def attach_session(
        self,
        session: AgentSession,
        assistant: Any = None,
        ctx: Any = None,
        idle_hangup_seconds: float = 60.0,
        on_blocked: Callable[[], None] | None = None,
        on_caller_disconnect: Callable[[], None] | None = None,
        on_coordinator_left: Callable[[], None] | None = None,
        is_call_blocked_fn: Callable[[Any], tuple[bool, str | None]] | None = None,
    ) -> None:
        """
        Attaches the active LiveKit AgentSession and Assistant to this VoiceController.
        Wires all session lifecycle events (participant connect/disconnect, agent state,
        idle watchdog, and shutdown hook) into the FSM — keeping agent.py free of
        controller-layer logic.

        Args:
            session: Active LiveKit AgentSession.
            assistant: The Assistant agent instance.
            ctx: LiveKit JobContext.
            idle_hangup_seconds: Seconds of silence before auto-hangup.
            on_blocked: Optional callback invoked when a blocked participant joins.
            on_caller_disconnect: Optional callback when caller hangs up (for recorder/DTMF cleanup).
            on_coordinator_left: Optional callback when a coordinator participant leaves.
            is_call_blocked_fn: Callable(participant) -> (bool, str). Used to check blocklist.
        """
        self._active_session = session
        if assistant is not None:
            self._active_agent = assistant
        if ctx is not None:
            self._active_ctx = ctx
            if ctx.room:
                self._room_name = ctx.room.name
                # Note: _agent_id is NOT changed here — the pid-keyed registry
                # entry stays consistent; room is reported separately via _report_to_global_fsm.

            # ── Shutdown hook: job cancellation → clean ENDING → READY ────────────
            ctx.add_shutdown_callback(self._on_job_shutdown)

        logger.info(f"🎙️ [VoiceController] LiveKit AgentSession attached to FSM [room='{self._room_name}'].")

        # ── Agent sub-state changes (LISTENING / THINKING / SPEAKING) ────────────
        # @session.on("agent_state_changed")
        # def _on_agent_state_changed(ev):
        #     self.on_agent_substate_changed(getattr(ev, "new_state", str(ev)))
        # ── Agent sub-state changes (LISTENING / THINKING / SPEAKING) ────────────
        @session.on("agent_state_changed")
        def _on_agent_state_changed(ev):
            self._agent_substate = str(getattr(ev, "new_state", ev))

        # ── Session close / error ─────────────────────────────────────────────────
        @session.on("close")
        def _on_session_closed(ev=None):
            # Session closed by LiveKit (timeout, error, or server restart).
            # Fire the caller-disconnect callback so recorder/DTMF are cleaned up.
            self.on_call_end(self._room_name)
            if on_caller_disconnect:              # Idempotent — _on_caller_disconnect_cb has its own guard
                on_caller_disconnect()

        @session.on("error")
        def _on_session_err(ev):
            err = getattr(ev, "error", ev)
            # Recoverable errors (e.g. Deepgram blip, primary LLM hiccup before FallbackLLM takes over)
            # must NOT end the call — only hard, non-recoverable failures route to on_error.
            if getattr(err, "recoverable", False):
                logger.warning(f"Recoverable session error — ignored by FSM: {err}")
                return
            self.on_error(err if isinstance(err, Exception) else Exception(str(err)))

        # ── Room participant events ────────────────────────────────────────────────
        if ctx is not None and ctx.room:
            @ctx.room.on("participant_connected")
            def _on_participant_connected(participant):
                identity = getattr(participant, "identity", "")
                # Check blocklist first
                if is_call_blocked_fn:
                    blocked, matched_id = is_call_blocked_fn(participant)
                    if blocked:
                        logger.warning(
                            f"🚫 [CALL REJECTED] Blocked participant '{matched_id}' joined room '{self._room_name}'. Hanging up."
                        )
                        if on_blocked:
                            on_blocked()
                        return
                self.on_participant_joined(identity)

            @ctx.room.on("participant_disconnected")
            def _on_participant_disconnected(participant):
                identity = getattr(participant, "identity", "")
                if identity.startswith("coordinator-"):
                    # Coordinator left — re-engage with caller (handled by caller-side callback)
                    if on_coordinator_left:
                        on_coordinator_left()
                else:
                    # Only end the call if all non-coordinator callers have left
                    remaining = [
                        p for p in ctx.room.remote_participants.values()
                        if p.identity != identity and not p.identity.startswith("coordinator-")
                    ]
                    if not remaining:
                        logger.info(f"Caller '{identity}' disconnected from room '{self._room_name}'.")   
                        self.on_call_end(self._room_name)
                        if on_caller_disconnect:
                            on_caller_disconnect()

        # ── Idle watchdog: auto-hangup after silence ──────────────────────────────
        # Runs as a controller task, cancelled in on_call_end.
        _last_activity = time.monotonic()
        _agent_busy_ref: list[bool] = [True]  # Mutable container for closure capture

        @session.on("user_input_transcribed")
        def _on_transcript(ev):
            nonlocal _last_activity
            _last_activity = time.monotonic()

        @session.on("user_state_changed")
        def _on_user_state(ev):
            nonlocal _last_activity
            _last_activity = time.monotonic()

        @session.on("agent_state_changed")
        def _on_agent_busy_track(ev):
            nonlocal _last_activity
            _agent_busy_ref[0] = getattr(ev, "new_state", "") in ("thinking", "speaking", "initializing")
            if _agent_busy_ref[0]:
                _last_activity = time.monotonic()

        @session.on("function_tools_executed")
        def _on_tools_done(ev):
            nonlocal _last_activity
            _last_activity = time.monotonic()

        @session.on("conversation_item_added")
        def _on_item_added(ev):
            nonlocal _last_activity
            _last_activity = time.monotonic()

        async def _idle_watchdog():
            nonlocal _last_activity
            while True:
                await asyncio.sleep(1.0)
                # Pause watchdog while coordinator call is active
                if getattr(session, "coordinator_connected", False):
                    continue
                # Reset if agent or user is busy
                if _agent_busy_ref[0] or getattr(session, "user_state", "") == "speaking":
                    _last_activity = time.monotonic()
                    continue
                idle_for = time.monotonic() - _last_activity
                if idle_for >= idle_hangup_seconds:
                    logger.info(
                        f"Caller silent and agent idle for {idle_for:.1f}s — auto-hangup."
                    )
                    self.on_call_end(self._room_name)
                    if on_caller_disconnect:
                        on_caller_disconnect()
                    return

        self._idle_watchdog_task = asyncio.create_task(_idle_watchdog(), name="IdleWatchdog")

    # ------------------------------------------------------------------
    # Goal & Service Introspection (Delegated to AgentToolsController)
    # ------------------------------------------------------------------
    @property
    def services(self) -> dict[str, Any]:
        """All GoalService instances keyed by goal ID."""
        return self.tools_controller.services

    @property
    def goal_ids(self) -> list[str]:
        """All registered goal IDs."""
        return self.tools_controller.goal_ids

    @property
    def active_goal_ids(self) -> list[str]:
        """IDs of currently enabled goals."""
        return self.tools_controller.active_goal_ids

    def get_service(self, goal_id: str) -> Optional[Any]:
        """Retrieve a GoalService instance by goal ID (e.g. 'g01_identity')."""
        return self.tools_controller.get_service(goal_id)

    def get_goal(self, goal_id: str) -> Optional[Any]:
        """Alias for get_service."""
        return self.tools_controller.get_goal(goal_id)

    @property
    def identity_service(self) -> Optional[Any]:
        return self.tools_controller.identity_service

    @property
    def leave_balance_service(self) -> Optional[Any]:
        return self.tools_controller.leave_balance_service

    @property
    def leave_application_service(self) -> Optional[Any]:
        return self.tools_controller.leave_application_service

    @property
    def leave_status_service(self) -> Optional[Any]:
        return self.tools_controller.leave_status_service

    @property
    def attendance_service(self) -> Optional[Any]:
        return self.tools_controller.attendance_service

    @property
    def wfh_service(self) -> Optional[Any]:
        return self.tools_controller.wfh_service

    @property
    def payroll_service(self) -> Optional[Any]:
        return self.tools_controller.payroll_service

    @property
    def hr_policy_service(self) -> Optional[Any]:
        return self.tools_controller.hr_policy_service

    @property
    def calendar_service(self) -> Optional[Any]:
        return self.tools_controller.calendar_service

    @property
    def reminders_service(self) -> Optional[Any]:
        return self.tools_controller.reminders_service

    @property
    def insurance_service(self) -> Optional[Any]:
        return self.tools_controller.insurance_service

    @property
    def coordinator_service(self) -> Optional[Any]:
        return self.tools_controller.coordinator_service

    @property
    def outbound_verification_service(self) -> Optional[Any]:
        return self.tools_controller.outbound_verification_service

    def disable_goal(self, goal_id: str) -> None:
        """Disables all tools for the specified goal at runtime."""
        self.tools_controller.disable_goal(goal_id)

    def enable_goal(self, goal_id: str) -> None:
        """Re-enables a previously disabled goal."""
        self.tools_controller.enable_goal(goal_id)

    def is_goal_enabled(self, goal_id: str) -> bool:
        """Checks if a specific goal is currently active/enabled."""
        return self.tools_controller.is_goal_enabled(goal_id)

    def session_goal_tools(self) -> list[Callable]:
        """Returns all @function_tool callables active in the current call session (from enabled GoalServices)."""
        return self.tools_controller.get_goal_tools()

    def session_tools_for_goals(self, *goal_ids: str) -> list[Callable]:
        """Returns tools from the specified goal IDs for the current call session."""
        return self.tools_controller.get_tools_for_goals(*goal_ids)

    def session_all_tools(self) -> list[Callable]:
        """
        Returns the full deduplicated tool list for the current call session via AgentToolsController.
        Combines GoalService action tools + Third-Party & DB tools with unique names.
        """
        return self.tools_controller.get_all_tools()

    def session_tool_names(self) -> list[str]:
        """Returns sorted list of all active tool function names for the current call session."""
        return self.tools_controller.get_tool_names()

    def session_tool_count(self) -> int:
        """Returns total count of active tools for the current call session."""
        return self.tools_controller.get_tool_count()

    def session_goal_summary(self) -> list[dict]:
        """Returns goal status summary for the current call session (for builder UI / monitoring)."""
        return self.tools_controller.get_goal_summary()

    def log_session_manifest(self) -> None:
        """Logs the full tool registry manifest for the current call session."""
        self.tools_controller.log_get_manifest()

    def apply_config(self, agent_config: Any) -> None:
        """Applies goals enablement from AgentConfig to tools_controller."""
        self.tools_controller.apply_config(agent_config)

    # -----------------------
    # EXTERNAL ENTRY POINTS
    # -----------------------
    def request_prewarm(self, proc: JobProcess) -> None:
        """Called once by the LiveKit worker's prewarm hook."""
        self._pending_proc = proc

    # -----------------------
    # DUAL-FSM EVENT OBSERVERS (LiveKit AgentServer Integration)
    # -----------------------
    def set_startup_checks(self, fn: Callable[[], None] | None) -> None:
        self._startup_checks = fn

    def prewarm_resources_once(self) -> None:
        """Scheduler + Ollama priming + DB check. Delegates to module-level prewarm_worker_resources."""
        prewarm_worker_resources(self._startup_checks)

    def on_call_start(self, room_name: str = "") -> None:
        if room_name:
            self._room_name = room_name
            self._call_start_times[room_name] = time.monotonic()
        self._call_requested = True                      # READY case picks this up

    def on_participant_joined(self, identity: str = "") -> None:
        if identity.startswith("coordinator-") or self._active_session is None:
            return
        self._active_participant_identity = identity     # CONNECTING case picks this up

    def on_session_started(self) -> None:
        room = self._active_ctx.room if self._active_ctx else None
        if not room or self._active_participant_identity:
            return
        for p in room.remote_participants.values():
            if not p.identity.startswith("coordinator-"):
                self.on_participant_joined(p.identity)
                break

    def on_call_end(self, room_name: str = "") -> None:
        if self.current_state is ControllerState.READY:
            # The call was cancelled before the FSM picked up _call_requested.
            # Clear the flag and the leaked start-time so READY stays idle.
            self._call_requested = False
            self._call_start_times.pop(room_name or self._room_name, None)
            return
        if self.current_state is ControllerState.ENDING:
            return
        target_room = room_name or self._room_name
        elapsed = 0.0
        if target_room in self._call_start_times:
            elapsed = time.monotonic() - self._call_start_times.pop(target_room)
        logger.info(f"📴 [VoiceController] Call ended (room='{target_room}', duration={elapsed:.1f}s).")
        if self._idle_watchdog_task and not self._idle_watchdog_task.done():
            self._idle_watchdog_task.cancel()
            self._idle_watchdog_task = None
        self._shutdown_requested = True                  # state machine goes → ENDING

    async def _on_job_shutdown(self, *_args) -> None:
        """Async shutdown callback registered with JobContext.add_shutdown_callback.
        Fires when LiveKit terminates the job (timeout, server restart, etc.).
        Ensures FSM reaches READY (and aclose runs) even if the process is about
        to exit before the loop ticks into ENDING on its own."""
        self.on_call_end(self._room_name)  # Idempotent — safe if already READY
        # Wait up to ~5 s for the FSM to finish tearing down so aclose() and
        # cleanup in the ENDING case run before the process exits.
        for _ in range(100):
            if self.current_state is ControllerState.READY:
                break
            await asyncio.sleep(0.05)

    def on_error(self, error: Exception) -> None:
        self._error_message = str(error)
        self._error_requested = True                     # state machine goes → ERROR
        logger.error(f"❌ [VoiceController] Error: {error}")

    # def _build_engines(self) -> None:
    #     """Verify engine configuration during controller startup.
    #     Actual session engines (STT, LLM, TTS) are constructed per-session
    #     in agent.py (_my_agent_impl).
    #     """
    #     # Read USE_CLOUD_LLM from .env; True only if it is "true" or "1" (case-insensitive), otherwise False
    #     use_cloud = os.getenv("USE_CLOUD_LLM", "false").lower() in ("true", "1")
    #     llm_model = os.getenv("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it")
    #     if use_cloud:
    #         logger.info(
    #             "[FSM] Engine configuration verified: Hybrid LLM (Primary: %s -> Fallback: Ollama %s).",
    #             llm_model,
    #             self.ollama_model,
    #         )
    #     else:
    #         logger.info(
    #             "[FSM] Engine configuration verified: Local Ollama (%s).",
    #             self.ollama_model,
    #         )
    def _build_engines(self) -> None:
        """Log the configured LLM setup at worker startup.

        Nothing is built here. The real STT, LLM and TTS engines are created
        per call in agent.py (_my_agent_impl). This method only prints which
        LLM mode will be used, so you can check it in the terminal.
        """

        # Read USE_CLOUD_LLM from .env; True only if it is "true" or "1"
        # (case-insensitive), otherwise False.
        use_cloud = os.getenv("USE_CLOUD_LLM", "false").lower() in ("true", "1")

        # Cloud model name from .env, with a default if the variable is not set.
        llm_model = os.getenv("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it")

        if use_cloud:
            # Cloud mode: the cloud LLM is tried first, Ollama is the backup.
            # %s placeholders are filled by the logger with the values after the string.
            logger.info(
                "[FSM] Engine configuration verified: Hybrid LLM (Primary: %s -> Fallback: Ollama %s).",
                llm_model,          # first %s  -> cloud model name
                self.ollama_model,  # second %s -> local Ollama model name
            )
        else:
            # Local-only mode: no cloud LLM, only Ollama is used.
            logger.info(
                "[FSM] Engine configuration verified: Local Ollama (%s).",
                self.ollama_model,
            )

    def _prime_ollama_model(self) -> None:
        """Synchronous warm-up request so the first real turn isn't slow.
        The outer prewarm_resources_once marker already gates this to run once per
        worker process tree, so no secondary /tmp marker is needed here."""
        try:
            ollama_host = self.ollama_base_url.rstrip("/")
            if ollama_host.endswith("/v1"):
                ollama_host = ollama_host[:-3]

            payload = json.dumps({
                "model": self.ollama_model,
                "prompt": "Hello",  # Warm up model into memory quickly
                "stream": False,  # Non-streaming single response
                "keep_alive": -1,  # Keep model loaded indefinitely in GPU/RAM
                "think": False,  # Top-level parameter in Ollama to suppress chain-of-thought
                "options": {
                    "num_predict": 1,  # Generate only 1 token for warm-up
                    "num_thread": 8,  # Leverage CPU cores
                },
            }).encode()

            # Prepare HTTP POST request to Ollama API
            req = urllib.request.Request(
                f"{ollama_host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            logger.info(f"[FSM] Priming Ollama model '{self.ollama_model}'...")
            # Execute warmup request with 120s timeout
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
            logger.info("[FSM] Ollama model loaded and system prompt primed.")
        except Exception as exc:
            # Non-fatal warning — model warmup skipped if Ollama is unreachable
            logger.warning(f"[FSM] Ollama warm-up skipped (non-fatal): {exc}")

    # ── Global FSM reporting helper ───────────────────────────────────────
    def _report_to_global_fsm(
        self,
        room: str = "",
        participant: str = "",
    ) -> None:
        report_agent_event(
            self._agent_id,
            self.current_state,
            room=room or self._room_name or "",
            participant=participant or self._active_participant_identity,
            pid=self._pid,
        )


# Alias for convenience and backwards compatibility
AgentVoiceController = VoiceController
__all__ = [
    "VoiceController",
    "AgentVoiceController",
    "ControllerState",
    "prewarm_worker_resources",
    "_prime_ollama_model",
]