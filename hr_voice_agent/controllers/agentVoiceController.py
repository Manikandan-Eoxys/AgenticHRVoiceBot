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
from enum import Enum, auto
from typing import Dict, Any, Optional, Tuple, Callable

from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
    inference,
)

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    DEEPGRAM_API_KEY,
    SYSTEM_PROMPT,
)
from integrations.sync_scheduler import start_scheduler, stop_scheduler
from controllers.global_monitor_fsm import GlobalMonitorFSM
from controllers.agentToolsController import AgentToolsController
from goals import (
    GoalService,
    ALL_GOAL_CLASSES,
    INBOUND_GOALS,
    OUTBOUND_LEAVE_VERIFICATION_GOALS,
    OUTBOUND_REMINDER_GOALS,
    IdentityVerificationService,
    LeaveBalanceService,
    LeaveApplicationService,
    LeaveStatusService,
    AttendanceService,
    WorkFromHomeService,
    PayrollService,
    HRPolicyService,
    CalendarService,
    RemindersService,
    EmployeeBenefitsService,
    LeaveVerificationOutboundService,
    CoordinatorTransferService,
)

logger = logging.getLogger(__name__)

# Marker file that prevents re-running worker startup (Ollama priming, DB check) on each
# LiveKit job process fork/spawn. Refreshed every _STARTUP_TTL_S seconds (default: 1 hour).
_STARTUP_MARKER = "/tmp/hrbot_worker_startup_done"
_STARTUP_TTL_S = 3600


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
        deepgram_api_key: str = DEEPGRAM_API_KEY,
        system_prompt: str = SYSTEM_PROMPT,
        goal_classes: list[type[GoalService]] | None = None,
        call_type: str = "inbound",
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ):
        self.ollama_model = ollama_model
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

        self._pid = os.getpid()
        self._room_name: str = ""
        self._agent_id: str = str(self._pid)
        self._fsm_task: Optional[asyncio.Task] = None
        self._global_fsm_task: Optional[asyncio.Task] = None
        self._idle_watchdog_task: Optional[asyncio.Task] = None  # Cancelled on call end
        self._stopped: bool = False
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

        # 1. Tools Controller manages strictly Third-Party & DB tools
        self.tools_controller: AgentToolsController = AgentToolsController()

        # 2. Voice Controller manages all Goals and GoalServices
        self.goal_classes: list[type[GoalService]] = goal_classes or ALL_GOAL_CLASSES
        self._services: dict[str, GoalService] = {}
        self._init_goals(
            goal_classes=self.goal_classes,
            job_ctx=self.job_ctx,
            collector=self.collector,
            call_metadata=self.call_metadata,
        )

        logger.info(f"[FSM] Controller initialized [pid={self._pid}] in state: {self.current_state.name}")
        # Register this agent with the global monitor
        GlobalMonitorFSM.instance().on_agent_event(
            agent_id=self._agent_id,
            state=self.current_state,
        )

    def _stuck_for(self, seconds: float) -> bool:
        """Returns True if the current state has been active longer than `seconds`."""
        return (time.time() - self._state_updated_at) > seconds

    def _set_state(self, new_state: ControllerState) -> None:
        """Single canonical place for event-driven state transitions.
        Logs the change, resets the watchdog timer, and reports to GlobalMonitorFSM.
        No-ops if the state is unchanged (idempotent)."""
        if self.current_state == new_state:
            return
        old = self.current_state
        self.current_state = new_state
        self.next_state = new_state
        self._previous_state = old
        self._state_updated_at = time.time()  # Reset watchdog timer for new state
        self.stt_status = self.llm_status = self.tts_status = "idle"
        tag = f"pid={self._pid}" + (f" | room='{self._room_name}'" if self._room_name else "")
        logger.info(f"➡️ VC Transition [{tag}]: {old.name} → {new_state.name}")
        self._report_to_global_fsm()

    def _instantiate_goal(
        self,
        goal_cls: type[GoalService],
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ) -> GoalService:
        """Instantiate a GoalService with call runtime context."""
        if goal_cls is IdentityVerificationService:
            return goal_cls(collector=collector)
        if goal_cls is CoordinatorTransferService:
            return goal_cls(job_ctx=job_ctx)
        return goal_cls()

    def _init_goals(
        self,
        goal_classes: list[type[GoalService]],
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ) -> None:
        """Initializes the GoalService map for this session."""
        self._services = {
            svc.meta.id: svc
            for cls in goal_classes
            for svc in [self._instantiate_goal(cls, job_ctx=job_ctx, collector=collector, call_metadata=call_metadata)]
        }

    def ensure_fsm_running(self) -> None:
        """Lazily starts GlobalMonitorFSM task and VoiceController 12-state FSM loop."""
        if self._global_fsm_task is None or self._global_fsm_task.done():
            self._global_fsm_task = asyncio.create_task(
                GlobalMonitorFSM.instance().run_loop(),
                name="GlobalMonitorFSM",
            )
            logger.info("🌐 [VoiceController] GlobalMonitorFSM task started.")
        if self._fsm_task is None or self._fsm_task.done():
            self._stopped = False
            self._fsm_task = asyncio.create_task(
                self.run_loop(),
                name="VoiceControllerFSM",
            )
            logger.info("🤖 [VoiceController] VoiceControllerFSM task started.")

    async def run_loop(self) -> None:
        """Continuously runs the 12-state FSM state machine loop until stopped."""
        logger.info(f"🔄 [VoiceController] FSM run loop started [pid={self._pid}].")
        while not self._stopped:
            try:
                await self.run_state_machine()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Unhandled error in VoiceController FSM loop: {exc}")
                await asyncio.sleep(0.1)

    def stop_fsm(self) -> None:
        """Stops the FSM loop."""
        self._stopped = True
        if self._fsm_task and not self._fsm_task.done():
            self._fsm_task.cancel()

    def setup_call(
        self,
        call_type: str | None = None,
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ) -> None:
        """
        Configures this VoiceController instance for an active call session,
        initializing goals, services, and tools for the specified call type.
        This allows reusing a single VoiceController instance across the application.
        """
        mapping = {
            "inbound": INBOUND_GOALS,
            "leave_verification": OUTBOUND_LEAVE_VERIFICATION_GOALS,
            "scheduled_reminder": OUTBOUND_REMINDER_GOALS,
            "policy_acknowledgement": OUTBOUND_REMINDER_GOALS,
        }
        self.call_type = call_type or "inbound"
        self.job_ctx = job_ctx
        self.collector = collector
        self.call_metadata = call_metadata or {}
        self.goal_classes = mapping.get(self.call_type, INBOUND_GOALS)

        self._init_goals(
            goal_classes=self.goal_classes,
            job_ctx=job_ctx,
            collector=collector,
            call_metadata=self.call_metadata,
        )

        try:
            from controllers.agentConfigController import AgentConfigController
            cfg = AgentConfigController.instance().get_agent_config()
            self.apply_config(cfg)
        except Exception as err:
            logger.debug(f"[VoiceController] Defaulting to code goal defaults: {err}")

        logger.info(
            f"🎯 [VoiceController] Configured for call_type='{self.call_type}' "
            f"with {len(self.active_goal_ids)} active goals."
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
        is_call_blocked_fn: Callable[[Any], tuple[bool, str]] | None = None,
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
                self._agent_id = f"{self._pid}:{ctx.room.name}"

            # ── Shutdown hook: job cancellation → clean ENDING → READY ────────────
            ctx.add_shutdown_callback(self._on_job_shutdown)

        logger.info(f"🎙️ [VoiceController] LiveKit AgentSession attached to FSM [room='{self._room_name}'].")

        # ── Agent sub-state changes (LISTENING / THINKING / SPEAKING) ────────────
        @session.on("agent_state_changed")
        def _on_agent_state_changed(ev):
            self.on_agent_substate_changed(getattr(ev, "new_state", str(ev)))

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

    @property
    def global_fsm(self) -> GlobalMonitorFSM:
        return GlobalMonitorFSM.instance()

    # ------------------------------------------------------------------
    # Goal & Service Introspection and Accessors
    # ------------------------------------------------------------------
    @property
    def services(self) -> dict[str, GoalService]:
        """All GoalService instances keyed by goal ID."""
        return self._services

    @property
    def goal_ids(self) -> list[str]:
        """All registered goal IDs."""
        return list(self._services.keys())

    @property
    def active_goal_ids(self) -> list[str]:
        """IDs of currently enabled goals."""
        return [gid for gid, svc in self._services.items() if svc.is_enabled()]

    def get_service(self, goal_id: str) -> Optional[GoalService]:
        """Retrieve a GoalService instance by goal ID (e.g. 'g01_identity')."""
        return self._services.get(goal_id)

    def get_goal(self, goal_id: str) -> Optional[GoalService]:
        """Alias for get_service."""
        return self.get_service(goal_id)

    @property
    def identity_service(self) -> Optional[IdentityVerificationService]:
        svc = self._services.get("g01_identity")
        return svc if isinstance(svc, IdentityVerificationService) else None

    @property
    def leave_balance_service(self) -> Optional[LeaveBalanceService]:
        svc = self._services.get("g02_leave_balance")
        return svc if isinstance(svc, LeaveBalanceService) else None

    @property
    def leave_application_service(self) -> Optional[LeaveApplicationService]:
        svc = self._services.get("g03_leave_application")
        return svc if isinstance(svc, LeaveApplicationService) else None

    @property
    def leave_status_service(self) -> Optional[LeaveStatusService]:
        svc = self._services.get("g04_leave_status")
        return svc if isinstance(svc, LeaveStatusService) else None

    @property
    def attendance_service(self) -> Optional[AttendanceService]:
        svc = self._services.get("g05_attendance")
        return svc if isinstance(svc, AttendanceService) else None

    @property
    def wfh_service(self) -> Optional[WorkFromHomeService]:
        svc = self._services.get("g06_wfh")
        return svc if isinstance(svc, WorkFromHomeService) else None

    @property
    def payroll_service(self) -> Optional[PayrollService]:
        svc = self._services.get("g07_payroll")
        return svc if isinstance(svc, PayrollService) else None

    @property
    def hr_policy_service(self) -> Optional[HRPolicyService]:
        svc = self._services.get("g08_hr_policy")
        return svc if isinstance(svc, HRPolicyService) else None

    @property
    def calendar_service(self) -> Optional[CalendarService]:
        svc = self._services.get("g09_calendar")
        return svc if isinstance(svc, CalendarService) else None

    @property
    def reminders_service(self) -> Optional[RemindersService]:
        svc = self._services.get("g10_reminders")
        return svc if isinstance(svc, RemindersService) else None

    @property
    def insurance_service(self) -> Optional[EmployeeBenefitsService]:
        svc = self._services.get("g11_benefits")
        return svc if isinstance(svc, EmployeeBenefitsService) else None

    @property
    def coordinator_service(self) -> Optional[CoordinatorTransferService]:
        svc = self._services.get("g13_coordinator")
        return svc if isinstance(svc, CoordinatorTransferService) else None

    @property
    def outbound_verification_service(self) -> Optional[LeaveVerificationOutboundService]:
        svc = self._services.get("g12_leave_verification_outbound")
        return svc if isinstance(svc, LeaveVerificationOutboundService) else None

    # ------------------------------------------------------------------
    # Goal Management & Tool Compilation
    # ------------------------------------------------------------------
    def disable_goal(self, goal_id: str) -> None:
        """Disables all tools for the specified goal at runtime."""
        svc = self._services.get(goal_id)
        if svc:
            svc.disable()
            logger.info(f"🔕 [AgentVoiceController] Goal '{goal_id}' disabled.")
        else:
            logger.warning(f"⚠️ [AgentVoiceController] Cannot disable unknown goal '{goal_id}'.")

    def enable_goal(self, goal_id: str) -> None:
        """Re-enables a previously disabled goal."""
        svc = self._services.get(goal_id)
        if svc:
            svc.enable()
            logger.info(f"🔔 [AgentVoiceController] Goal '{goal_id}' enabled.")
        else:
            logger.warning(f"⚠️ [AgentVoiceController] Cannot enable unknown goal '{goal_id}'.")

    def is_goal_enabled(self, goal_id: str) -> bool:
        """Checks if a specific goal is currently active/enabled."""
        svc = self._services.get(goal_id)
        return bool(svc and svc.is_enabled())

    def get_goal_tools(self) -> list[Callable]:
        """Returns flat list of all @function_tool callables from active GoalServices."""
        tools: list[Callable] = []
        for svc in self._services.values():
            if svc.is_enabled():
                tools.extend(svc.tools())
        return tools

    def get_tools_for_goals(self, *goal_ids: str) -> list[Callable]:
        """Returns tools only from the specified goal IDs."""
        tools: list[Callable] = []
        for gid in goal_ids:
            svc = self._services.get(gid)
            if svc and svc.is_enabled():
                tools.extend(svc.tools())
        return tools

    def get_all_tools(self) -> list[Callable]:
        """
        Returns all tools for the voice agent session:
        Combines GoalService action tools + Third-Party & DB tools.
        Guarantees unique function names to prevent LiveKit duplicate tool registration errors.
        """
        raw_tools: list[Callable] = self.get_goal_tools()
        if self.tools_controller:
            raw_tools.extend(self.tools_controller.get_all_tools())

        seen_names = set()
        unique_tools = []
        for t in raw_tools:
            name = getattr(getattr(t, "info", None), "name", None) or getattr(t, "__name__", None) or str(t)
            if name not in seen_names:
                seen_names.add(name)
                unique_tools.append(t)
            else:
                logger.warning(f"[VoiceController] Skipping duplicate tool '{name}'")
        return unique_tools

    def get_tool_names(self) -> list[str]:
        """Returns sorted list of all enabled tool function names across goals and tools controller."""
        names = []
        for svc in self._services.values():
            if svc.is_enabled():
                names.extend(svc.tool_names())
        if self.tools_controller:
            for t in self.tools_controller.get_all_tools():
                names.append(getattr(t, "__name__", str(t)))
        return sorted(list(set(names)))

    def tool_count(self) -> int:
        """Returns total count of enabled tools."""
        return len(self.get_all_tools())

    def goal_summary(self) -> list[dict]:
        """Returns a list of dicts for builder UI / monitoring."""
        return [
            {
                "id": svc.meta.id,
                "name": svc.meta.name,
                "enabled": svc.is_enabled(),
                "tool_count": len(svc.tools()),
                "tools": svc.tool_names(),
            }
            for svc in self._services.values()
        ]

    def log_manifest(self) -> None:
        """Logs the full tool manifest grouped by goal, plus third-party & DB tools."""
        logger.info("📋 [AgentVoiceController] Goal → Tool manifest:")
        logger.info(f"  {'GOAL ID':<35} {'STATUS':<10} TOOLS")
        logger.info("  " + "-" * 75)
        for gid, svc in self._services.items():
            status = "enabled " if svc.is_enabled() else "DISABLED"
            names = ", ".join(svc.tool_names()) if svc.is_enabled() else "-"
            logger.info(f"  {gid:<35} {status:<10} {names}")
        extra_tools = self.tools_controller.get_all_tools() if self.tools_controller else []
        if extra_tools:
            ext_names = ", ".join(getattr(t, "__name__", str(t)) for t in extra_tools)
            logger.info(f"  {'third_party_and_db':<35} {'enabled ':<10} {ext_names}")
        logger.info(
            f"  Total: {self.tool_count()} tools across {len(self.active_goal_ids)} active goals."
        )

    def apply_config(self, agent_config: Any) -> None:
        """
        Applies goals enablement from AgentConfig to GoalServices,
        and forwards configuration to the Third-Party & DB tools controller.
        """
        if not agent_config:
            return

        # 1. Sync goal enabled states
        goal_states = {g.id: g.enabled for g in getattr(agent_config, "goals", [])}
        for gid, svc in self._services.items():
            if gid in goal_states:
                if goal_states[gid]:
                    svc.enable()
                else:
                    svc.disable()

        # 2. Sync third-party & DB declarative actions
        if self.tools_controller:
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
    def on_worker_start(self) -> None:
        """Called at worker startup to prewarm Ollama & DB."""
        self._set_state(ControllerState.INITIALIZING)
        self._set_state(ControllerState.PREWARMING)
        self._prime_ollama_model()  # Warm up Ollama model (guarded by marker file)
        self._set_state(ControllerState.READY)
        logger.info(f"✅ [VoiceController] Worker startup complete — READY for call jobs.")

    def run_worker_startup_once(self, extra_checks: Callable[[], None] | None = None) -> None:
        """Prewarm Ollama and run optional extra checks (e.g. DB health) at most once
        per worker process tree. The marker file stores the worker's PID, so job
        processes (children) skip warmup but a fresh restart (new PID) re-runs it fully."""
        try:
            with open(_STARTUP_MARKER) as f:
                # Marker contains the parent/worker PID. Child processes share the same
                # parent PID, so they skip startup; a new worker process has a different PID.
                recent = int(f.read().strip()) == os.getppid()
        except Exception:
            recent = False  # No marker, or unreadable — run full startup

        if recent:
            logger.info("⚡ Worker startup already done by parent process; marking READY.")
            self._set_state(ControllerState.READY)  # Skip prewarm — go straight to READY
            return

        self.on_worker_start()  # Full prewarm: INITIALIZING → PREWARMING → READY
        if extra_checks:
            extra_checks()  # e.g. hr_tools.run_startup_check()
        try:
            with open(_STARTUP_MARKER, "w") as f:
                f.write(str(os.getpid()))  # Store this worker's PID so child processes skip warmup
        except Exception:
            pass

    def on_job_accepted(self, room_name: str = "") -> None:
        """Called when an incoming call job request is accepted."""
        if room_name:
            self._room_name = room_name
            self._agent_id = f"{self._pid}:{room_name}"
        self._set_state(ControllerState.JOB_ACCEPTED)  # Resets watchdog timer via _set_state
        self._report_to_global_fsm(room=room_name)

    def on_call_start(self, room_name: str = "") -> None:
        """Called when a call enters the room (READY → CONNECTING)."""
        if room_name:
            self._room_name = room_name
            self._agent_id = f"{self._pid}:{room_name}"
            self._call_start_times[room_name] = time.monotonic()  # Record call start time
        self._set_state(ControllerState.CONNECTING)  # Resets watchdog timer via _set_state
        self._report_to_global_fsm(room=room_name)

    def on_participant_joined(self, identity: str = "") -> None:
        """Called when caller audio stream is active (CONNECTING → SESSION_ACTIVE).
        Ignores coordinator participants and late events arriving after call teardown."""
        # Coordinators joining mid-call must not overwrite the active caller identity
        # or force SESSION_ACTIVE when we've already torn down.
        if identity.startswith("coordinator-"):
            return
        # Guard against stale participant_connected events arriving after on_call_end.
        if self._active_session is None:
            logger.debug(f"on_participant_joined: no active session; ignoring identity='{identity}'")
            return
        self._active_participant_identity = identity  # Track active caller identity
        self._set_state(ControllerState.SESSION_ACTIVE)  # Resets watchdog timer via _set_state
        self._report_to_global_fsm(participant=identity)

    def on_session_started(self) -> None:
        """Call right after session.start(). For inbound SIP calls the caller is already
        in the room before any participant_connected event fires — this catches that case."""
        if self.current_state != ControllerState.CONNECTING:
            return  # Only relevant when we are still waiting for a participant
        room = self._active_ctx.room if self._active_ctx else None
        if not room:
            return
        for p in room.remote_participants.values():
            if not p.identity.startswith("coordinator-"):
                self.on_participant_joined(p.identity)  # Advance FSM: CONNECTING → SESSION_ACTIVE
                break

    def on_agent_state(self, new_state: str) -> None:
        """Alias for on_agent_substate_changed."""
        self.on_agent_substate_changed(new_state)

    def on_agent_substate_changed(self, substate: str) -> None:
        """Tracks active turn sub-states: LISTENING, THINKING, SPEAKING.
        Uses _set_state() so the watchdog timer resets on every LLM/STT/TTS transition.
        Ignores events that arrive after the call has ended (no active session)."""
        # Guard: trailing agent_state_changed("idle") arrives after on_call_end and
        # would push the FSM back to SESSION_ACTIVE with no active call.
        if self._active_session is None or self.current_state in (ControllerState.ENDING, ControllerState.ERROR):
            return
        sub_upper = str(substate).upper()
        target_map = {
            "LISTENING": ControllerState.LISTENING,   # STT phase — user is speaking
            "THINKING": ControllerState.THINKING,     # LLM phase — generating reply or calling tools
            "SPEAKING": ControllerState.SPEAKING,     # TTS phase — audio is being synthesized/played
            "INITIALIZING": ControllerState.SESSION_ACTIVE,  # Session warmup before first turn
        }
        target = target_map.get(sub_upper, ControllerState.SESSION_ACTIVE)
        self._set_state(target)  # Resets watchdog timer; logs the transition

    def on_call_end(self, room_name: str = "") -> None:
        """Called when call ends or participant disconnects.
        Idempotent — safe to call multiple times (caller disconnect + job shutdown)."""
        # Guard: already back to READY with no active context → nothing to do
        if self.current_state == ControllerState.READY and not self._active_ctx:
            return

        elapsed = 0.0
        target_room = room_name or self._room_name
        if target_room and target_room in self._call_start_times:
            elapsed = time.monotonic() - self._call_start_times.pop(target_room)
        logger.info(
            f"📴 [VoiceController] Call ended"
            + (f" (room='{target_room}', duration={elapsed:.1f}s)." if target_room else ".")
        )

        # Cancel idle watchdog task now that call is over
        if self._idle_watchdog_task and not self._idle_watchdog_task.done():
            self._idle_watchdog_task.cancel()
            self._idle_watchdog_task = None

        self._shutdown_requested = False
        # Directly transition through ENDING and land at READY
        self.current_state = ControllerState.ENDING
        self.next_state = ControllerState.READY
        self._active_ctx = None          # Release JobContext reference
        self._active_agent = None        # Release assistant agent reference
        self._active_session = None      # Release voice session reference
        self._active_participant_identity = ""  # Clear caller identity
        self.stt_status = "idle"         # Reset STT engine status
        self.llm_status = "idle"         # Reset LLM engine status
        self.tts_status = "idle"         # Reset TTS engine status
        logger.info(f"➡️ VC Transition [pid={self._pid}]: ENDING → READY")
        self.current_state = ControllerState.READY
        self._state_updated_at = time.time()  # Reset watchdog timer to avoid stale READY stuck detection
        self._report_to_global_fsm(room=target_room)

    async def _on_job_shutdown(self, *_args) -> None:
        """Async shutdown callback registered with JobContext.add_shutdown_callback.
        Fires when LiveKit terminates the job (timeout, server restart, etc.).
        Ensures FSM reaches READY even if caller disconnect event was missed."""
        self.on_call_end(self._room_name)  # Idempotent — safe if already READY

    def on_error(self, error: Exception) -> None:
        """Called when an unhandled error occurs. Logs and transitions to ERROR state."""
        self._error_message = str(error)
        self._set_state(ControllerState.ERROR)  # Resets watchdog timer; logs the transition
        logger.error(f"❌ [VoiceController] Error: {error}")

    def _build_engines(self) -> None:
        from livekit.plugins.deepgram import STT, TTS as DeepgramTTS
        from livekit.agents import inference
        from ollama_llm import OllamaLLM, FallbackLLM

        local_llm = OllamaLLM(
            model=self.ollama_model,
            base_url=self.ollama_base_url,
            think=False,
            timeout=60.0,
        )

        use_cloud = os.getenv("USE_CLOUD_LLM", "true").lower() in ("true", "1")
        if use_cloud:
            try:
                cloud_llm = inference.LLM(model="openai/gpt-4o-mini")
                self.llm = FallbackLLM(primary_llm=cloud_llm, fallback_llm=local_llm)
                logger.info("[FSM] Hybrid LLM configured: Primary (LiveKit Cloud gpt-4o-mini) → Fallback (Local Ollama %s).", self.ollama_model)
            except Exception as exc:
                logger.warning("[FSM] LiveKit Cloud LLM setup failed (%s). Falling back to local Ollama.", exc)
                self.llm = local_llm
        else:
            self.llm = local_llm
            logger.info("[FSM] Local OllamaLLM (%s) constructed.", self.ollama_model)

        self.stt = STT(
            api_key=self.deepgram_api_key,
            model="nova-3",
        )
        logger.info("[FSM] STT (Deepgram Nova-3 Direct) constructed.")

        self.tts = DeepgramTTS(
            api_key=self.deepgram_api_key,
        )
        logger.info("[FSM] TTS (DeepgramTTS Direct) constructed.")

    def _prime_ollama_model(self) -> None:
        """Synchronous warm-up request so the first real turn isn't slow.
        The outer run_worker_startup_once marker already gates this to run once per
        worker process tree, so no secondary /tmp marker is needed here."""
        try:
            ollama_host = self.ollama_base_url.rstrip("/")
            if ollama_host.endswith("/v1"):
                ollama_host = ollama_host[:-3]

            payload = json.dumps({
                "model": self.ollama_model,
                "prompt": self.system_prompt,  # Warm up with system prompt
                "stream": False,  # Non-streaming single response
                "keep_alive": -1,  # Keep model loaded indefinitely in GPU/RAM
                "options": {
                    "num_predict": 1,  # Generate only 1 token for warm-up
                    "think": False,  # Disable chain-of-thought for speed
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

    # =========================================================================
    # 12-STATE FSM DISPATCHER
    # Executes one tick of the state machine loop, transitioning states cleanly.
    # =========================================================================
    async def run_state_machine(self):
        # Default next_state to current_state to preserve event-driven transitions
        self.next_state = self.current_state  # Prevents stale next_state overwriting current_state

        match self.current_state:

            # -----------------------------------------------------------------
            # STATE 1: UNINITIALIZED - Initial controller boot state
            # -----------------------------------------------------------------
            case ControllerState.UNINITIALIZED:
                logger.info("🚀 Voice Controller 12-State FSM Started")
                self.next_state = ControllerState.INITIALIZING  # Advance to building engines

            # -----------------------------------------------------------------
            # STATE 2: INITIALIZING - Construct STT, LLM, and TTS engine instances
            # -----------------------------------------------------------------
            case ControllerState.INITIALIZING:
                try:
                    self._build_engines()  # Instantiate STT, LLM, and TTS models
                    self.next_state = ControllerState.PREWARMING  # Proceed to prewarm phase
                except Exception as exc:
                    logger.error(f"❌ Engine construction failed: {exc}")
                    self._error_message = str(exc)  # Capture failure reason
                    self.next_state = ControllerState.ERROR  # Route to error handler

            # -----------------------------------------------------------------
            # STATE 3: PREWARMING - Preload LLM models, DB, and sync scheduler
            # -----------------------------------------------------------------
            case ControllerState.PREWARMING:
                try:
                    # Cache engines in LiveKit process userdata for session reuse
                    if self._pending_proc is not None:
                        self._pending_proc.userdata["llm"] = self.llm
                        self._pending_proc.userdata["stt"] = self.stt
                        self._pending_proc.userdata["tts"] = self.tts

                    # Start background sync jobs for IFS Cloud and SQL Server (once only)
                    if not self._scheduler_started:
                        start_scheduler()  # Start APScheduler periodic jobs
                        atexit.register(stop_scheduler)  # Stop scheduler cleanly on process exit
                        self._scheduler_started = True  # Flag scheduler as running
                        logger.info("⏳ Background sync scheduler started.")

                    # Prime the Ollama LLM in threadpool so the async event loop never freezes
                    await asyncio.to_thread(self._prime_ollama_model)
                    self.next_state = ControllerState.READY  # System is warm and ready for calls
                except Exception as exc:
                    logger.error(f"❌ Prewarm failed: {exc}")
                    self._error_message = str(exc)  # Save error details
                    self.next_state = ControllerState.ERROR  # Route to error handler

            # -----------------------------------------------------------------
            # STATE 4: READY - Idle and waiting for an incoming call job request
            # -----------------------------------------------------------------
            case ControllerState.READY:
                # READY is a stable idle state — the FSM sleeps briefly between ticks.
                # State transitions out of READY are purely event-driven via _set_state()
                # (on_call_start, run_worker_startup_once). No job-queue polling needed.
                pass

            # -----------------------------------------------------------------
            # STATE 5: JOB_ACCEPTED - Job acknowledged (event-driven, passes through)
            # -----------------------------------------------------------------
            case ControllerState.JOB_ACCEPTED:
                # on_call_start() is always called immediately after in my_agent,
                # so this state is transient. Pass through; event will drive CONNECTING.
                pass

            # -----------------------------------------------------------------
            # STATE 6: CONNECTING - Waiting for caller participant to join the room
            # -----------------------------------------------------------------
            case ControllerState.CONNECTING:
                # Session and participant arrive via attach_session() / on_session_started() / participant_connected.
                # Advance only when a participant is present so that on_session_started() can detect
                # pre-existing callers and the 45s connect timeout can fire if nobody joins.
                if self._active_participant_identity:
                    self.next_state = ControllerState.SESSION_ACTIVE
                elif self._shutdown_requested:
                    self.next_state = ControllerState.ENDING
                elif self._stuck_for(self.caller_connect_timeout):
                    logger.warning(f"⏱️ Timed out waiting for caller participant after {self.caller_connect_timeout}s.")
                    self._error_message = f"Caller participant did not join within {self.caller_connect_timeout}s"
                    self.next_state = ControllerState.ERROR

            # -----------------------------------------------------------------
            # STATE 7: SESSION_ACTIVE - Call is connected and active
            # -----------------------------------------------------------------
            case ControllerState.SESSION_ACTIVE:
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING  # Disconnect requested, tear down call

            # -----------------------------------------------------------------
            # STATE 8: LISTENING - STT is active, listening to caller speech
            # -----------------------------------------------------------------
            case ControllerState.LISTENING:  # STT phase
                if self.stt_status == "idle":
                    self.stt_status = "listening"  # Mark STT engine active once
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING  # Transition to ending if call hung up
                elif self._stuck_for(self.stt_timeout) and self.stt_status == "listening":
                    # One-shot warning: LISTENING can be long when caller is just quiet —
                    # the idle watchdog at 60 s handles the hangup. Log at debug only.
                    self.stt_status = "stalled"  # Latch flag so this only fires once per stuck period
                    logger.debug(f"[STT Watchdog] LISTENING stalled for >{self.stt_timeout}s (no transcript)")

            # -----------------------------------------------------------------
            # STATE 9: THINKING - LLM generating reply or executing tool calls
            # -----------------------------------------------------------------
            case ControllerState.THINKING:  # LLM + tools phase
                if self.llm_status == "idle":
                    self.llm_status = "generating"  # Mark LLM engine active once
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING  # Transition to ending if call hung up
                elif self._stuck_for(self.llm_timeout) and self.llm_status == "generating":
                    # One-shot warning: log only once per stuck LLM period (FallbackLLM may recover)
                    self.llm_status = "slow"  # Latch flag so this only fires once
                    logger.warning(f"⚠️ [LLM Watchdog] THINKING stalled >{self.llm_timeout}s (LLM slow; FallbackLLM should take over).")

            # -----------------------------------------------------------------
            # STATE 10: SPEAKING - TTS is synthesizing and streaming audio output
            # -----------------------------------------------------------------
            case ControllerState.SPEAKING:  # TTS playback phase
                if self.tts_status == "idle":
                    self.tts_status = "synthesizing"  # Mark TTS engine active once
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING  # Transition to ending if call hung up
                elif self._stuck_for(self.tts_timeout) and self.tts_status == "synthesizing":
                    # One-shot warning: log only once per stuck TTS period
                    self.tts_status = "stalled"  # Latch flag so this only fires once
                    logger.warning(f"⚠️ [TTS Watchdog] SPEAKING stalled >{self.tts_timeout}s.")

            # -----------------------------------------------------------------
            # STATE 11: ENDING - Disconnecting call and releasing session resources
            # -----------------------------------------------------------------
            case ControllerState.ENDING:
                self._shutdown_requested = False  # Reset shutdown flag
                # Gracefully close active LiveKit voice session
                if self._active_session is not None:
                    try:
                        await self._active_session.aclose()  # Close async session tracks
                        logger.info("🛑 Active voice session closed.")
                    except Exception as exc:
                        logger.debug(f"Session aclose exception (non-fatal): {exc}")
                    self._active_session = None  # Clear session reference

                # Reset all session-scoped variables and engine status
                self._active_ctx = None  # Release job context
                self._active_agent = None  # Release agent instance
                self._active_participant_identity = ""  # Reset caller identity
                self.stt_status = "idle"  # Reset STT engine status
                self.llm_status = "idle"  # Reset LLM engine status
                self.tts_status = "idle"  # Reset TTS engine status
                logger.info("🧹 Reset state for next call job")
                self.next_state = ControllerState.READY  # Ready for the next phone call

            # -----------------------------------------------------------------
            # STATE 12: ERROR - Handle errors and recover cleanly to READY
            # -----------------------------------------------------------------
            case ControllerState.ERROR:
                logger.error(f"🛑 ControllerState.ERROR: {self._error_message}")
                # Recovery path: route through ENDING to clean up resources, then return to READY
                self.next_state = ControllerState.ENDING

        # Log state transition if state has changed
        if self.current_state != self.next_state:
            tag = f"pid={self._pid}" + (f" | room='{self._room_name}'" if self._room_name else "")
            logger.info(f"➡️ VC Transition [{tag}]: {self.current_state.name} → {self.next_state.name}")
            self._previous_state = self.current_state  # Store previous state
            self._state_updated_at = time.time()  # Reset watchdog timer for new state
            self.current_state = self.next_state  # Advance current state to next state
            self._report_to_global_fsm()  # Notify global monitoring FSM of state change
        await asyncio.sleep(0.05)  # Yield control to event loop for 50ms

    # ── Global FSM reporting helper ───────────────────────────────────────
    def _report_to_global_fsm(
        self,
        room: str = "",
        participant: str = "",
    ) -> None:
        """
        Reports the current state of this VoiceController to the
        process-level GlobalMonitorFSM.  Called after every state
        transition.  Non-blocking (dict write under a lock).
        """
        try:
            GlobalMonitorFSM.instance().on_agent_event(
                agent_id=getattr(self, "_agent_id", str(self._pid)),
                state=self.current_state,
                room=room or getattr(self, "_room_name", "") or "",
                participant=participant or getattr(self, "_active_participant_identity", ""),
            )
        except Exception as exc:
            # Global FSM reporting must never crash the voice agent
            logger.debug(f"[GlobalFSM] report skipped: {exc}")


# Alias for convenience and backwards compatibility
AgentVoiceController = VoiceController
__all__ = ["VoiceController", "AgentVoiceController", "ControllerState"]