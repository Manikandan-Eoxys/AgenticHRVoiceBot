"""
controllers/agentVoiceController.py

Voice Controller for Asterisk Telephony Pipeline — Standalone Loop-Driven Match-Case State Machine.
Dedicated exclusively to Asterisk AudioSocket, ARI Channel, SIP Caller ID,
telephony turn management, and telephony-native lifecycle management.
Zero dependency on LiveKit packages.

Exports:
  - AsteriskVoiceController      (primary class)
  - VoiceController              (alias for AsteriskVoiceController)
  - AgentVoiceController         (alias for AsteriskVoiceController)
  - ControllerState
  - prewarm_worker_resources
  - _prime_ollama_model
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

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    SYSTEM_PROMPT,
)
from integrations.sync_scheduler import start_scheduler, stop_scheduler
from controllers.global_monitor_fsm import report_agent_event
from controllers.agentToolsController import AgentToolsController

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# STATES
# ---------------------------------------------------------------------------
class ControllerState(Enum):
    UNINITIALIZED = auto()  # 1. Booted
    INITIALIZING  = auto()  # 2. Engines building
    PREWARMING    = auto()  # 3. Model & DB prewarming
    READY         = auto()  # 4. System ready
    JOB_ACCEPTED  = auto()  # 5. Call job accepted
    CONNECTING    = auto()  # 6. AudioSocket & ARI bridge connecting
    SESSION_ACTIVE= auto()  # 7. Call live
    LISTENING     = auto()  # 8. Caller speaking / STT listening
    THINKING      = auto()  # 9. LLM generating / executing tools
    SPEAKING      = auto()  # 10. TTS synthesizing / AudioSocket playing voice
    ENDING        = auto()  # 11. Asterisk channel hangup / tearing down
    ERROR         = auto()  # 12. Exception / error


_SUBSTATE_TARGET = {
    "LISTENING": ControllerState.LISTENING,
    "THINKING":  ControllerState.THINKING,
    "SPEAKING":  ControllerState.SPEAKING,
}

_IN_CALL = (
    ControllerState.CONNECTING,
    ControllerState.SESSION_ACTIVE,
    ControllerState.LISTENING,
    ControllerState.THINKING,
    ControllerState.SPEAKING,
)

_scheduler_started = False


# ---------------------------------------------------------------------------
# MODULE-LEVEL HELPERS
# ---------------------------------------------------------------------------
def _prime_ollama_model(base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL) -> None:
    """Synchronous warm-up request so the first real turn isn't slow."""
    try:
        ollama_host = base_url.rstrip("/")
        if ollama_host.endswith("/v1"):
            ollama_host = ollama_host[:-3]

        payload = json.dumps({
            "model": (model or "qwen3:8b").strip(),
            "prompt": "Hello",
            "stream": False,
            "keep_alive": -1,
            "think": False,
            "options": {
                "num_predict": 1,
                "num_thread": 8,
            },
        }).encode()

        req = urllib.request.Request(
            f"{ollama_host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        logger.info(f"[Asterisk FSM] Priming Ollama model '{model}'...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        logger.info("[Asterisk FSM] Ollama model loaded and system prompt primed.")
    except Exception as exc:
        logger.warning(f"[Asterisk FSM] Ollama warm-up skipped (non-fatal): {exc}")


def prewarm_worker_resources(startup_checks: Callable[[], None] | None = None) -> None:
    """Scheduler + Ollama priming + DB check. Main worker process only."""
    global _scheduler_started
    proc = multiprocessing.current_process()
    if proc.name != "MainProcess" or multiprocessing.parent_process() is not None:
        logger.info("⚡ Asterisk Job process: worker prewarm is owned by main process; skipping.")
        return
    if _scheduler_started:
        return

    start_scheduler()
    atexit.register(stop_scheduler)
    _scheduler_started = True
    logger.info("⏳ Asterisk Background sync scheduler started.")
    _prime_ollama_model(OLLAMA_BASE_URL, OLLAMA_MODEL)
    if startup_checks:
        startup_checks()


# ---------------------------------------------------------------------------
# CONTROLLER
# ---------------------------------------------------------------------------
class AsteriskVoiceController:
    """
    Standalone loop-driven FSM controller for the Asterisk Telephony pipeline.
    Manages AudioSocket & ARI channel lifecycle, caller identity, telephony turn states,
    tool execution via AgentToolsController, and reports telemetry to GlobalMonitorFSM.
    Zero dependency on LiveKit packages.
    """

    def __init__(
        self,
        caller_id: str = "",
        channel_id: str = "",
        call_uuid: str = "",
        room_name: str = "",
        ollama_model: str = OLLAMA_MODEL,
        ollama_base_url: str = OLLAMA_BASE_URL,
        system_prompt: str = SYSTEM_PROMPT,
        goal_classes: list[type[GoalService]] | None = None,
        call_type: str = "inbound",
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ):
        self.pipeline_name: str = "asterisk"
        self.caller_id: str = caller_id or (call_metadata.get("caller_id", "") if call_metadata else "")
        self.channel_id: str = channel_id or (call_metadata.get("channel_id", "") if call_metadata else "")
        self.call_uuid: str = call_uuid
        self._room_name: str = room_name or (f"asterisk_{self.channel_id}" if self.channel_id else "")
        self._caller_identity: str = f"sip_{self.caller_id}" if self.caller_id else ""

        self.ollama_model = (ollama_model or "qwen3:8b").strip()
        self.ollama_base_url = ollama_base_url
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

        # Telephony session handles
        self._telephony_session: Optional[Any] = None
        self._shutdown_requested: bool = False

        # Facts written by event handlers, read and consumed by run_state_machine()
        self._call_requested: bool = False
        self._agent_substate: str = ""          # listening / thinking / speaking / idle
        self._error_requested: bool = False

        self._pid = os.getpid()
        self._agent_id = str(self._pid)
        self._last_beat = 0.0
        self._fsm_task: Optional[asyncio.Task] = None
        self._idle_watchdog_task: Optional[asyncio.Task] = None
        self._ready_event = asyncio.Event()
        self._startup_checks: Optional[Callable[[], None]] = None
        self._call_start_times: dict[str, float] = {}

        # Watchdog timeouts & engine status
        self.llm_timeout: float = 30.0
        self.stt_timeout: float = 30.0
        self.tts_timeout: float = 20.0
        self.caller_connect_timeout: float = 30.0
        self.stt_status: str = "idle"
        self.llm_status: str = "idle"
        self.tts_status: str = "idle"
        self.rag_status: str = "idle"

        self.call_type: str = call_type
        self.job_ctx: Any = job_ctx
        self.collector: Any = collector
        self.call_metadata: dict = call_metadata or {}

        # Tools Controller manages domain goal services & tools
        self.tools_controller: AgentToolsController = AgentToolsController(
            goal_classes=goal_classes,
            call_type=self.call_type,
            job_ctx=self.job_ctx,
            collector=self.collector,
            call_metadata=self.call_metadata,
        )

        logger.info(f"[Asterisk FSM] Controller initialized [pid={self._pid}] in state: {self.current_state.name}")
        report_agent_event(
            self._agent_id,
            self.current_state,
            room=self._room_name,
            pid=self._pid,
        )

    # ------------------------------------------------------------------
    # FSM INTERNALS
    # ------------------------------------------------------------------
    def _stuck_for(self, seconds: float) -> bool:
        """Returns True if the current state has been active longer than `seconds`."""
        return (time.time() - self._state_updated_at) > seconds

    def ensure_fsm_running(self) -> None:
        """Starts the FSM loop task if not already running."""
        if self._fsm_task is None or self._fsm_task.done():
            self._fsm_task = asyncio.create_task(self.run_loop(), name="AsteriskVoiceControllerFSM")

    async def run_state_machine(self) -> None:
        """
        Executes a single step / tick of the Asterisk 12-state FSM.
        Evaluates current state logic, determines next state, applies transitions,
        and synchronizes telemetry.
        """
        try:
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
                    self._shutdown_requested = False
                    self._error_requested = False
                    if self._call_requested:
                        self.next_state = ControllerState.JOB_ACCEPTED

                case ControllerState.JOB_ACCEPTED:
                    self._call_requested = False
                    self.next_state = ControllerState.CONNECTING

                case (ControllerState.CONNECTING | ControllerState.SESSION_ACTIVE
                    | ControllerState.LISTENING | ControllerState.THINKING
                    | ControllerState.SPEAKING):

                    if self._error_requested:
                        self._error_requested = False
                        self.next_state = ControllerState.ERROR

                    elif self._shutdown_requested:
                        self.next_state = ControllerState.ENDING

                    elif self.current_state is ControllerState.CONNECTING:
                        # Caller is connected once caller_id / identity is set
                        if self._caller_identity or self.caller_id:
                            self.next_state = ControllerState.SESSION_ACTIVE
                        elif self._stuck_for(self.caller_connect_timeout):
                            logger.warning(f"⏱️ [Asterisk] Timed out waiting for channel connect after {self.caller_connect_timeout}s.")
                            self._error_message = "Asterisk channel did not connect in time"
                            self.next_state = ControllerState.ERROR

                    else:
                        # Sub-state transitions: LISTENING / THINKING / SPEAKING
                        self.next_state = _SUBSTATE_TARGET.get(
                            self._agent_substate.upper(), ControllerState.SESSION_ACTIVE
                        )

                        # Per-state stall watchdogs
                        if self.current_state is ControllerState.LISTENING:
                            if self.stt_status == "idle":
                                self.stt_status = "listening"
                            elif self.stt_status == "listening" and self._stuck_for(self.stt_timeout):
                                self.stt_status = "stalled"
                                logger.debug(f"[Asterisk STT Watchdog] LISTENING stalled >{self.stt_timeout}s")
                        elif self.current_state is ControllerState.THINKING:
                            if self.llm_status == "idle":
                                self.llm_status = "generating"
                            elif self.llm_status == "generating" and self._stuck_for(self.llm_timeout):
                                self.llm_status = "slow"
                                logger.warning(f"⚠️ [Asterisk LLM Watchdog] THINKING stalled >{self.llm_timeout}s")
                        elif self.current_state is ControllerState.SPEAKING:
                            if self.tts_status == "idle":
                                self.tts_status = "synthesizing"
                            elif self.tts_status == "synthesizing" and self._stuck_for(self.tts_timeout):
                                self.tts_status = "stalled"
                                logger.warning(f"⚠️ [Asterisk TTS Watchdog] SPEAKING stalled >{self.tts_timeout}s")

                case ControllerState.ENDING:
                    self._shutdown_requested = False
                    self._telephony_session = None
                    self._caller_identity = ""
                    self._agent_substate = ""
                    logger.info("🧹 [Asterisk] Teardown complete. Reset state for next telephony call")
                    self.next_state = ControllerState.READY

                case ControllerState.ERROR:
                    logger.error(f"🛑 [Asterisk] ControllerState.ERROR: {self._error_message}")
                    self.next_state = ControllerState.ENDING

                case _:
                    self._error_message = f"Unknown state: {self.current_state}"
                    self.next_state = ControllerState.ERROR

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"Unhandled error in Asterisk state {self.current_state.name}: {exc}")
            self._error_message = str(exc)
            self.next_state = (
                ControllerState.READY
                if self.current_state in (ControllerState.ERROR, ControllerState.ENDING)
                else ControllerState.ERROR
            )

        # ── Synchronize states at the end of each tick ───────────────────────────
        if self.next_state is not self.current_state:
            old = self.current_state
            self._previous_state = old
            self._state_updated_at = time.time()
            self.stt_status = self.llm_status = self.tts_status = "idle"
            tag = f"pid={self._pid}" + (f" | caller='{self.caller_id}'" if self.caller_id else "")
            logger.info(f"➡️ [Asterisk FSM] Transition [{tag}]: {old.name} → {self.next_state.name}")
            self.current_state = self.next_state
            if self.current_state is ControllerState.READY:
                self._ready_event.set()
            else:
                self._ready_event.clear()
            self._report_to_global_fsm()

    async def run_loop(self) -> None:
        """Embedded super-loop driving the Asterisk FSM ticks."""
        logger.info(f"🚀 Asterisk Voice Controller 12-State FSM Started [pid={self._pid}]")
        while True:
            await self.run_state_machine()

            _now = time.time()
            if _now - self._last_beat >= 3.0:
                self._last_beat = _now
                self._report_to_global_fsm()

            await asyncio.sleep(0.05)

    async def wait_until_ready(self, timeout: float = 150.0) -> None:
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)

    # ------------------------------------------------------------------
    # CALL SETUP & SESSION ATTACHMENT
    # ------------------------------------------------------------------
    def setup_call(
        self,
        call_type: str | None = None,
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: dict | None = None,
    ) -> None:
        self.call_type = call_type or "inbound"
        self.job_ctx = job_ctx
        self.collector = collector
        self.call_metadata = call_metadata or {}

        if self.call_metadata.get("caller_id"):
            self.caller_id = self.call_metadata["caller_id"]
            self._caller_identity = f"sip_{self.caller_id}"
        if self.call_metadata.get("channel_id"):
            self.channel_id = self.call_metadata["channel_id"]
            if not self._room_name:
                self._room_name = f"asterisk_{self.channel_id}"

        self.tools_controller.setup_call(
            call_type=self.call_type,
            job_ctx=job_ctx,
            collector=collector,
            call_metadata=self.call_metadata,
        )
        logger.info(
            f"🎯 [AsteriskVoiceController] Configured for call_type='{self.call_type}' "
            f"with {len(self.active_goal_ids)} active goals."
        )

    def attach_session(
        self,
        telephony_session: Any,
        idle_hangup_seconds: float = 60.0,
        on_hangup: Callable[[], Any] | None = None,
    ) -> None:
        """Attaches active AsteriskVoiceSession and starts telephony idle watchdog."""
        self._telephony_session = telephony_session
        if hasattr(telephony_session, "caller_id") and telephony_session.caller_id:
            self.caller_id = str(telephony_session.caller_id)
            self._caller_identity = f"sip_{self.caller_id}"
        if hasattr(telephony_session, "channel_id") and telephony_session.channel_id:
            self.channel_id = str(telephony_session.channel_id)
            self._room_name = f"asterisk_{self.channel_id}"

        logger.info(f"🎙️ [AsteriskVoiceController] Telephony session attached [caller='{self.caller_id}'].")

        # Telephony idle watchdog: monitors caller silence
        _last_activity = time.monotonic()

        def mark_activity():
            nonlocal _last_activity
            _last_activity = time.monotonic()

        self.mark_activity = mark_activity  # Allow caller to reset watchdog directly

        async def _idle_watchdog():
            nonlocal _last_activity
            while True:
                await asyncio.sleep(1.0)
                if not getattr(telephony_session, "running", True):
                    return
                # If agent is speaking or thinking, reset activity
                if getattr(telephony_session, "agent_state", "") in ("thinking", "speaking"):
                    _last_activity = time.monotonic()
                    continue
                idle_for = time.monotonic() - _last_activity
                if idle_for >= idle_hangup_seconds:
                    logger.info(f"⏱️ [Asterisk] Telephony caller silent for {idle_for:.1f}s — auto-hangup.")
                    self.on_call_end(self._room_name)
                    if on_hangup:
                        if asyncio.iscoroutinefunction(on_hangup):
                            await on_hangup()
                        else:
                            on_hangup()
                    return

        self._idle_watchdog_task = asyncio.create_task(_idle_watchdog(), name="AsteriskIdleWatchdog")

    # ------------------------------------------------------------------
    # GOAL & SERVICE INTROSPECTION (Delegated to AgentToolsController)
    # ------------------------------------------------------------------
    @property
    def services(self) -> dict[str, Any]:
        return self.tools_controller.services

    @property
    def goal_ids(self) -> list[str]:
        return self.tools_controller.goal_ids

    @property
    def active_goal_ids(self) -> list[str]:
        return self.tools_controller.active_goal_ids

    def get_service(self, goal_id: str) -> Optional[Any]:
        return self.tools_controller.get_service(goal_id)

    def get_goal(self, goal_id: str) -> Optional[Any]:
        return self.tools_controller.get_goal(goal_id)

    def get_all_tools(self) -> list[Callable]:
        return self.tools_controller.get_all_tools()

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
        self.tools_controller.disable_goal(goal_id)

    def enable_goal(self, goal_id: str) -> None:
        self.tools_controller.enable_goal(goal_id)

    def is_goal_enabled(self, goal_id: str) -> bool:
        return self.tools_controller.is_goal_enabled(goal_id)

    def session_goal_tools(self) -> list[Callable]:
        return self.tools_controller.get_goal_tools()

    def session_tools_for_goals(self, *goal_ids: str) -> list[Callable]:
        return self.tools_controller.get_tools_for_goals(*goal_ids)

    def session_relevant_tools(self, user_text: str, is_verified: bool = False) -> list[Callable]:
        return self.tools_controller.get_relevant_tools(user_text, is_verified)

    def session_all_tools(self) -> list[Callable]:
        return self.tools_controller.get_all_tools()

    def session_tool_names(self) -> list[str]:
        return self.tools_controller.get_tool_names()

    def session_tool_count(self) -> int:
        return self.tools_controller.get_tool_count()

    def session_goal_summary(self) -> list[dict]:
        return self.tools_controller.get_goal_summary()

    def log_session_manifest(self) -> None:
        self.tools_controller.log_get_manifest()

    def apply_config(self, agent_config: Any) -> None:
        self.tools_controller.apply_config(agent_config)

    # ------------------------------------------------------------------
    # EXTERNAL ENTRY POINTS
    # ------------------------------------------------------------------
    def set_startup_checks(self, fn: Callable[[], None] | None) -> None:
        self._startup_checks = fn

    def prewarm_resources_once(self) -> None:
        prewarm_worker_resources(self._startup_checks)

    def on_call_start(
        self,
        room_name: str = "",
        caller_id: str = "",
        channel_id: str = "",
    ) -> None:
        """Invoked when Asterisk channel / AudioSocket session connects."""
        if caller_id:
            self.caller_id = caller_id
            self._caller_identity = f"sip_{caller_id}"
        if channel_id:
            self.channel_id = channel_id
        target_room = room_name or (f"asterisk_{self.channel_id}" if self.channel_id else self._room_name)
        if target_room:
            self._room_name = target_room
            self._call_start_times[target_room] = time.monotonic()
        self._call_requested = True

    def on_agent_substate_changed(self, new_state: str) -> None:
        """Updates telephony agent substate: listening / thinking / speaking / idle."""
        self._agent_substate = new_state
        if hasattr(self, "mark_activity"):
            self.mark_activity()

    def on_call_end(self, room_name: str = "") -> None:
        """Invoked when Asterisk call hangs up or is terminated."""
        if self.current_state is ControllerState.READY:
            self._call_requested = False
            self._call_start_times.pop(room_name or self._room_name, None)
            return
        if self.current_state is ControllerState.ENDING:
            return
        target_room = room_name or self._room_name
        elapsed = 0.0
        if target_room in self._call_start_times:
            elapsed = time.monotonic() - self._call_start_times.pop(target_room)
        logger.info(f"📴 [AsteriskVoiceController] Call ended (room='{target_room}', duration={elapsed:.1f}s).")
        if self._idle_watchdog_task and not self._idle_watchdog_task.done():
            self._idle_watchdog_task.cancel()
            self._idle_watchdog_task = None
        self._shutdown_requested = True

    def on_error(self, error: Exception) -> None:
        self._error_message = str(error)
        self._error_requested = True
        logger.error(f"❌ [AsteriskVoiceController] Error: {error}")

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------
    def _build_engines(self) -> None:
        use_cloud = os.getenv("USE_CLOUD_LLM", "false").lower() in ("true", "1")
        llm_model = os.getenv("ASTERISK_LLM_MODEL", os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct"))
        if use_cloud:
            logger.info(
                "[Asterisk FSM] Engine config: Hybrid LLM (Primary: %s -> Fallback: Ollama %s).",
                llm_model,
                self.ollama_model,
            )
        else:
            logger.info(
                "[Asterisk FSM] Engine config: Local Ollama (%s).",
                self.ollama_model,
            )

    def _prime_ollama_model(self) -> None:
        _prime_ollama_model(self.ollama_base_url, self.ollama_model)

    def _report_to_global_fsm(
        self,
        room: str = "",
        participant: str = "",
    ) -> None:
        report_agent_event(
            self._agent_id,
            self.current_state,
            room=room or self._room_name or "",
            participant=participant or self._caller_identity or self.caller_id,
            pid=self._pid,
        )


# ---------------------------------------------------------------------------
# ALIASES (backward compatibility)
# ---------------------------------------------------------------------------
VoiceController = AsteriskVoiceController
AgentVoiceController = AsteriskVoiceController


__all__ = [
    "AsteriskVoiceController",
    "VoiceController",
    "AgentVoiceController",
    "ControllerState",
    "prewarm_worker_resources",
    "_prime_ollama_model",
]
