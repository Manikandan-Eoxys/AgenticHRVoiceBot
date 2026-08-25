# """
# controllers/voice_controller.py

# Voice Controller for HR Voice Assistant with Embedded-Style Switch-Case (Match-Case) State Machine.
# Uses explicit (current_state, next_state) tuple matching to drive state transitions
# and execute state actions for STT, LLM, TTS, and LiveKit session lifecycles.
# """

# import json
# import logging
# import time
# import urllib.request
# import atexit
# from enum import Enum
# from typing import Dict, Any, Optional

# from livekit.agents import (
#     AgentSession,
#     JobContext,
#     JobProcess,
# )
# from livekit.plugins.deepgram import STT, TTS as DeepgramTTS
# from ollama_llm import OllamaLLM

# from config import (
#     OLLAMA_BASE_URL,
#     OLLAMA_MODEL,
#     DEEPGRAM_API_KEY,
#     SYSTEM_PROMPT,
# )
# from integrations.sync_scheduler import start_scheduler, stop_scheduler

# logger = logging.getLogger(__name__)


# class ControllerState(str, Enum):
#     """Finite State Machine states for VoiceController lifecycle."""
#     UNINITIALIZED = "UNINITIALIZED"
#     INITIALIZING = "INITIALIZING"
#     PREWARMING = "PREWARMING"
#     READY = "READY"
#     CONNECTING = "CONNECTING"
#     SESSION_ACTIVE = "SESSION_ACTIVE"
#     ENDING = "ENDING"
#     ERROR = "ERROR"


# class InvalidStateTransitionError(Exception):
#     """Raised when an invalid state transition is attempted in the FSM."""
#     pass


# class VoiceController:
#     """
#     Embedded-Style Switch-Case State Machine Controller for HR Voice Assistant.
#     Coordinates STT, LLM, TTS, and LiveKit session lifecycles using explicit
#     (current_state, next_state) match-case state dispatching.
#     """

#     def __init__(
#         self,
#         ollama_model: str = OLLAMA_MODEL,
#         ollama_base_url: str = OLLAMA_BASE_URL,
#         deepgram_api_key: str = DEEPGRAM_API_KEY,
#         system_prompt: str = SYSTEM_PROMPT,
#     ):
#         self.ollama_model = ollama_model
#         self.ollama_base_url = ollama_base_url
#         self.deepgram_api_key = deepgram_api_key
#         self.system_prompt = system_prompt

#         self.llm: Optional[OllamaLLM] = None
#         self.stt: Optional[STT] = None
#         self.tts: Optional[DeepgramTTS] = None

#         # State Machine Initialization
#         self._state: ControllerState = ControllerState.UNINITIALIZED
#         self._previous_state: Optional[ControllerState] = None
#         self._state_updated_at: float = time.time()
#         self._error_message: Optional[str] = None

#         logger.info(f"[Embedded FSM] Controller initialized in state: {self._state.value}")

#     @property
#     def state(self) -> ControllerState:
#         return self._state

#     def _set_state(self, new_state: ControllerState, reason: str = "") -> None:
#         """Internal helper to log and assign state."""
#         old_state = self._state
#         self._previous_state = old_state
#         self._state = new_state
#         self._state_updated_at = time.time()
#         reason_str = f" [{reason}]" if reason else ""
#         logger.info(f"[Embedded FSM] State Transition: {old_state.value} ➔ {new_state.value}{reason_str}")

#     # =========================================================================
#     # EMBEDDED SWITCH-CASE (MATCH-CASE) STATE MACHINE DISPATCHER
#     # =========================================================================
#     def transition(self, next_state: ControllerState, **kwargs: Any) -> Any:
#         """
#         Embedded-style State Dispatcher matching (current_state, next_state).
#         Executes enter/exit actions and returns transition result.
#         """
#         current_state = self._state

#         match (current_state, next_state):
#             # 1. UNINITIALIZED ➔ INITIALIZING
#             case (ControllerState.UNINITIALIZED, ControllerState.INITIALIZING):
#                 return self._handle_uninitialized_to_initializing(**kwargs)

#             # 2. INITIALIZING ➔ PREWARMING
#             case (ControllerState.INITIALIZING, ControllerState.PREWARMING):
#                 return self._handle_initializing_to_prewarming(**kwargs)

#             # 3. PREWARMING ➔ READY
#             case (ControllerState.PREWARMING, ControllerState.READY):
#                 return self._handle_prewarming_to_ready(**kwargs)

#             # 4. READY ➔ CONNECTING
#             case (ControllerState.READY, ControllerState.CONNECTING):
#                 return self._handle_ready_to_connecting(**kwargs)

#             # 5. CONNECTING ➔ SESSION_ACTIVE
#             case (ControllerState.CONNECTING, ControllerState.SESSION_ACTIVE):
#                 return self._handle_connecting_to_active(**kwargs)

#             # 6. SESSION_ACTIVE ➔ ENDING
#             case (ControllerState.SESSION_ACTIVE, ControllerState.ENDING):
#                 return self._handle_active_to_ending(**kwargs)

#             # 7. ENDING ➔ READY
#             case (ControllerState.ENDING, ControllerState.READY):
#                 return self._handle_ending_to_ready(**kwargs)

#             # 8. ANY STATE ➔ ERROR
#             case (_, ControllerState.ERROR):
#                 return self._handle_to_error(**kwargs)

#             # INVALID TRANSITION
#             case _:
#                 err_msg = f"Invalid state transition: {current_state.value} ➔ {next_state.value}"
#                 logger.error(f"[Embedded FSM] {err_msg}")
#                 raise InvalidStateTransitionError(err_msg)

#     # =========================================================================
#     # STATE TRANSITION ACTION HANDLERS
#     # =========================================================================

#     def _handle_uninitialized_to_initializing(self, **kwargs: Any) -> Dict[str, Any]:
#         """Action handler: Construct STT, LLM, and TTS plugin instances."""
#         self._set_state(ControllerState.INITIALIZING, reason="Constructing STT, LLM, TTS engines")
#         try:
#             # 1. LLM Initialization (Ollama native /api/chat wrapper)
#             self.llm = OllamaLLM(
#                 model=self.ollama_model,
#                 base_url=self.ollama_base_url,
#                 think=False,
#                 timeout=60.0,
#             )
#             logger.info("[Embedded FSM] LLM (OllamaLLM) constructed.")

#             # 2. STT Initialization (Deepgram Nova-3)
#             self.stt = STT(
#                 api_key=self.deepgram_api_key,
#                 model="nova-3",
#             )
#             logger.info("[Embedded FSM] STT (Deepgram Nova-3) constructed.")

#             # 3. TTS Initialization (Deepgram)
#             self.tts = DeepgramTTS(
#                 api_key=self.deepgram_api_key,
#             )
#             logger.info("[Embedded FSM] TTS (DeepgramTTS) constructed.")

#             return {"stt": self.stt, "llm": self.llm, "tts": self.tts}
#         except Exception as exc:
#             self.transition(ControllerState.ERROR, error=exc)
#             raise

#     def _handle_initializing_to_prewarming(self, proc: JobProcess, **kwargs: Any) -> None:
#         """Action handler: Start sync scheduler & prime Ollama system prompt."""
#         self._set_state(ControllerState.PREWARMING, reason="Starting sync scheduler & priming Ollama model")
#         try:
#             proc.userdata["llm"] = self.llm
#             proc.userdata["stt"] = self.stt
#             proc.userdata["tts"] = self.tts

#             # Start background sync scheduler
#             start_scheduler()
#             atexit.register(stop_scheduler)
#             logger.info("[Embedded FSM] Background sync scheduler started.")

#             # Prime Ollama model & system prompt
#             self._prime_ollama_model()
#         except Exception as exc:
#             self.transition(ControllerState.ERROR, error=exc)
#             raise

#     def _handle_prewarming_to_ready(self, **kwargs: Any) -> None:
#         """Action handler: Set state to READY for incoming call jobs."""
#         self._set_state(ControllerState.READY, reason="Worker process prewarm complete")

#     def _handle_ready_to_connecting(self, ctx: JobContext, **kwargs: Any) -> None:
#         """Action handler: Mark room connection in progress."""
#         room_name = ctx.room.name if ctx.room else "pending"
#         self._set_state(ControllerState.CONNECTING, reason=f"Connecting to room '{room_name}'")

#     def _handle_connecting_to_active(self, participant_identity: str = "", **kwargs: Any) -> None:
#         """Action handler: Mark call session active."""
#         self._set_state(ControllerState.SESSION_ACTIVE, reason=f"Participant '{participant_identity}' connected")

#     def _handle_active_to_ending(self, **kwargs: Any) -> None:
#         """Action handler: Mark call session tearing down."""
#         self._set_state(ControllerState.ENDING, reason="Call session disconnect requested")

#     def _handle_ending_to_ready(self, **kwargs: Any) -> None:
#         """Action handler: Reset state back to READY for next call."""
#         self._set_state(ControllerState.READY, reason="Reset state for next call job")

#     def _handle_to_error(self, error: Optional[Exception] = None, **kwargs: Any) -> None:
#         """Action handler: Record error state."""
#         self._error_message = str(error) if error else "Unknown error"
#         self._set_state(ControllerState.ERROR, reason=f"Error encountered: {self._error_message}")

#     # =========================================================================
#     # PUBLIC HIGH-LEVEL PIPELINE METHODS
#     # =========================================================================

#     def initialize_pipeline(self) -> Dict[str, Any]:
#         """Convenience caller for UNINITIALIZED ➔ INITIALIZING transition."""
#         return self.transition(ControllerState.INITIALIZING)

#     def prewarm_pipeline(self, proc: JobProcess) -> None:
#         """
#         Executes worker prewarm pipeline:
#         1. UNINITIALIZED ➔ INITIALIZING
#         2. INITIALIZING ➔ PREWARMING
#         3. PREWARMING ➔ READY
#         """
#         self.transition(ControllerState.INITIALIZING)
#         self.transition(ControllerState.PREWARMING, proc=proc)
#         self.transition(ControllerState.READY)

#     def _prime_ollama_model(self) -> None:
#         """Send synchronous request to Ollama to load model and prime system prompt."""
#         try:
#             ollama_host = self.ollama_base_url.rstrip("/")
#             if ollama_host.endswith("/v1"):
#                 ollama_host = ollama_host[:-3]

#             payload = json.dumps({
#                 "model": self.ollama_model,
#                 "prompt": self.system_prompt,
#                 "stream": False,
#                 "keep_alive": -1,
#                 "options": {
#                     "num_predict": 1,
#                     "think": False,
#                 },
#             }).encode()

#             req = urllib.request.Request(
#                 f"{ollama_host}/api/generate",
#                 data=payload,
#                 headers={"Content-Type": "application/json"},
#                 method="POST",
#             )

#             logger.info(f"[Embedded FSM] Priming Ollama model '{self.ollama_model}'...")
#             with urllib.request.urlopen(req, timeout=120) as resp:
#                 resp.read()
#             logger.info("[Embedded FSM] Ollama model loaded and system prompt primed.")
#         except Exception as exc:
#             logger.warning(f"[Embedded FSM] Ollama warm-up skipped (non-fatal): {exc}")

#     async def start_session(self, ctx: JobContext, agent_instance: Any) -> AgentSession:
#         """
#         Connects to LiveKit room and manages session startup through FSM transitions:
#         READY ➔ CONNECTING ➔ SESSION_ACTIVE ➔ (ENDING ➔ READY on shutdown)
#         """
#         self.transition(ControllerState.CONNECTING, ctx=ctx)
#         try:
#             await ctx.connect()
#             logger.info("[Embedded FSM] Connected to LiveKit room.")

#             participant = await ctx.wait_for_participant()
#             logger.info(f"[Embedded FSM] Participant joined: {participant.identity}")

#             # Retrieve pre-warmed engines from proc.userdata if available
#             llm = ctx.proc.userdata.get("llm", self.llm)
#             stt = ctx.proc.userdata.get("stt", self.stt)
#             tts = ctx.proc.userdata.get("tts", self.tts)

#             if not all([llm, stt, tts]):
#                 logger.info("[Embedded FSM] Pipeline engines not found in proc.userdata, initializing fresh.")
#                 pipeline = self.initialize_pipeline()
#                 llm, stt, tts = pipeline["llm"], pipeline["stt"], pipeline["tts"]

#             session = AgentSession(
#                 stt=stt,
#                 llm=llm,
#                 tts=tts,
#             )

#             self.transition(ControllerState.SESSION_ACTIVE, participant_identity=participant.identity)

#             # Register shutdown callback to transition ENDING ➔ READY when call ends
#             def on_close():
#                 self.transition(ControllerState.ENDING)
#                 self.transition(ControllerState.READY)

#             ctx.add_shutdown_callback(on_close)

#             await session.start(
#                 room=ctx.room,
#                 agent=agent_instance,
#             )

#             logger.info("[Embedded FSM] Agent voice session running.")
#             return session

#         except Exception as exc:
#             self.transition(ControllerState.ERROR, error=exc)
#             raise

#     def health_check(self) -> Dict[str, Any]:
#         """Returns status report and state machine metrics."""
#         return {
#             "state": self._state.value,
#             "previous_state": self._previous_state.value if self._previous_state else None,
#             "state_duration_seconds": round(time.time() - self._state_updated_at, 2),
#             "ollama_model": self.ollama_model,
#             "deepgram_key_configured": bool(self.deepgram_api_key),
#             "stt_active": self.stt is not None,
#             "llm_active": self.llm is not None,
#             "tts_active": self.tts is not None,
#             "error_message": self._error_message,
#         }
"""
controllers/voice_controller.py

Voice Controller for HR Voice Assistant — Loop-Driven Match-Case State Machine.

This mirrors the style of `safety_island_engine.py`:
  - A single `self.current_state` / `self.next_state` pair (no tuple-keyed
    dispatch table).
  - One `match self.current_state:` block. Each `case` does its own work
    inline and then decides `self.next_state`.
  - Incoming work arrives through a queue (`self.job_queue`), the same way
    the safety island reads CAN events off `can_to_si_queue` in its
    READ_EVENTS state.
  - `run_state_machine()` is an async loop you drive with
    `await controller.run_state_machine()` in a `while True:` (or a single
    call per tick, if you're embedding it in someone else's loop) — the
    transition itself is logged right before `self.current_state` is
    updated, exactly like the safety island's
    "➡️ SI Transition: X → Y" line.
"""

import json
import logging
import time
import urllib.request
import atexit
import asyncio
from enum import Enum, auto
from queue import Empty
from typing import Dict, Any, Optional, Tuple

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

logger = logging.getLogger(__name__)


# -----------------------
# STATES
# -----------------------
class ControllerState(Enum):
    UNINITIALIZED = auto()
    INITIALIZING = auto()
    PREWARMING = auto()
    READY = auto()
    CONNECTING = auto()
    SESSION_ACTIVE = auto()
    ENDING = auto()
    ERROR = auto()


class VoiceController:
    """
    Loop-driven FSM controller for the HR Voice Assistant.
    Coordinates STT, LLM, TTS construction and LiveKit session lifecycle
    through a single `match self.current_state:` dispatcher, the same
    pattern used by SafetyIslandEngine.run_state_machine().
    """

    def __init__(
        self,
        ollama_model: str = OLLAMA_MODEL,
        ollama_base_url: str = OLLAMA_BASE_URL,
        deepgram_api_key: str = DEEPGRAM_API_KEY,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url
        self.deepgram_api_key = deepgram_api_key
        self.system_prompt = system_prompt

        # Engines (populated by INITIALIZING)
        self.llm: Optional[Any] = None
        self.stt: Optional[Any] = None
        self.tts: Optional[Any] = None

        # FSM core — mirrors SafetyIslandEngine
        self.current_state: ControllerState = ControllerState.UNINITIALIZED
        self.next_state: ControllerState = ControllerState.UNINITIALIZED
        self._state_updated_at: float = time.time()
        self._previous_state: Optional[ControllerState] = None
        self._error_message: Optional[str] = None

        # Inbound work queue — same role as `can_to_si_queue` in the
        # safety island engine. Each item is (ctx, agent_instance, proc).
        self.job_queue: "asyncio.Queue[Tuple[JobContext, Any, Optional[JobProcess]]]" = asyncio.Queue()

        # Data carried between states, analogous to `self.can_req` /
        # `self.target_state` in the safety island engine.
        self._pending_proc: Optional[JobProcess] = None
        self._active_ctx: Optional[JobContext] = None
        self._active_agent: Optional[Any] = None
        self._active_session: Optional[AgentSession] = None
        self._active_participant_identity: str = ""
        self._shutdown_requested: bool = False

        # NEW: current pid, included in transition prints so overlapping
        # worker-process logs (e.g. one process serving a live call while
        # another idle-pool process is independently prewarming) can be
        # told apart at a glance instead of looking like one FSM jumping
        # backward.
        import os
        self._pid = os.getpid()

        logger.info(f"[FSM] Controller initialized in state: {self.current_state.name}")

    # -----------------------
    # EXTERNAL ENTRY POINTS
    # -----------------------
    def request_prewarm(self, proc: JobProcess) -> None:
        """Called once by the LiveKit worker's prewarm hook."""
        self._pending_proc = proc

    def request_job(self, ctx: JobContext, agent_instance: Any) -> None:
        """Called whenever a new call/room job arrives — enqueues it."""
        self.job_queue.put_nowait((ctx, agent_instance, None))

    # FIX: this MUST be `async def`. LiveKit's job-shutdown machinery
    # (job_proc_lazy_main.py) collects every callback registered via
    # ctx.add_shutdown_callback(...) and runs them as
    # `await asyncio.gather(*[callback() for callback in shutdown_callbacks])`
    # — i.e. it always does `await callback()`. A plain `def` callback
    # still executes fine (the body runs, self._shutdown_requested does
    # get set to True), but its return value is `None`, and `await None`
    # raises:
    #     TypeError: object NoneType can't be used in 'await' expression
    # That exception happens *inside* LiveKit's own shutdown gather, which
    # disrupts the shutdown sequence before this controller's background
    # _drive_until_ready() task gets its next event-loop tick to observe
    # _shutdown_requested and print the ENDING transition — which is why
    # "➡️ VC Transition: SESSION_ACTIVE → ENDING" never showed up in the
    # log even though the room actually did disconnect.
    async def request_shutdown(self) -> None:
        """Called from the LiveKit shutdown callback to end the active session.

        FIX: this used to only set a flag and rely on _drive_until_ready()'s
        next loop tick to notice it and print SESSION_ACTIVE -> ENDING. But
        once room.disconnect() is called, LiveKit's job-shutdown sequence
        starts tearing the whole process down almost immediately — there's
        no guarantee the background polling task gets scheduled again
        before "process exiting" happens. That's a race, and the log
        showed it being lost (session closed cleanly, but no ENDING print).
        Since this callback already knows for certain the session is
        ending, do the SESSION_ACTIVE -> ENDING -> READY transition and
        cleanup right here, synchronously with the callback, instead of
        deferring it to a task that may never run again.
        """
        self._shutdown_requested = True

        if self.current_state == ControllerState.SESSION_ACTIVE:
            logger.info("📴 Call session disconnect requested")
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.SESSION_ACTIVE.name} → {ControllerState.ENDING.name}")
            self._previous_state = ControllerState.SESSION_ACTIVE
            self.current_state = ControllerState.ENDING
            self._state_updated_at = time.time()

            self._active_ctx = None
            self._active_agent = None
            self._active_session = None
            self._active_participant_identity = ""
            logger.info("🧹 Reset state for next call job")

            self.next_state = ControllerState.READY
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.ENDING.name} → {ControllerState.READY.name}")
            self.current_state = ControllerState.READY
            self._previous_state = ControllerState.ENDING
            self._state_updated_at = time.time()
            self._shutdown_requested = False

    # -----------------------
    # COMPATIBILITY WRAPPERS
    # -----------------------
    # main.py calls these two methods the same way it always did:
    # `voice_controller.prewarm_pipeline(proc)` (sync, NOT awaited — called
    # from LiveKit's prewarm_fnc, which runs in a worker thread outside the
    # event loop) once per worker process, and
    # `await voice_controller.start_session(ctx, agent)` (async, awaited
    # from the async entrypoint) once per job. Neither call site in
    # main.py needs to change.
    def prewarm_pipeline(self, proc: JobProcess) -> None:
        """
        Synchronous — LiveKit calls `prewarm_fnc` as a plain function run in
        a worker thread, NOT the asyncio event loop. `main.py`'s wrapper
        (`def prewarm(proc): voice_controller.prewarm_pipeline(proc)`) is
        also not `async`/`await`ed — so this method CANNOT be `async def`,
        or calling it just creates a coroutine object that's silently
        discarded and never runs (no engines built, no logs — which is
        exactly what the last run showed). Runs UNINITIALIZED →
        INITIALIZING → PREWARMING → READY directly, without going through
        the async run_state_machine() loop.
        """
        self.request_prewarm(proc)

        self.current_state = ControllerState.INITIALIZING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.UNINITIALIZED.name} → {self.current_state.name}")
        try:
            self._build_engines()
        except Exception as exc:
            logger.error(f"❌ Engine construction failed: {exc}")
            self._error_message = str(exc)
            self.current_state = ControllerState.ERROR
            raise

        self.current_state = ControllerState.PREWARMING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.INITIALIZING.name} → {self.current_state.name}")
        try:
            proc.userdata["llm"] = self.llm
            proc.userdata["stt"] = self.stt
            proc.userdata["tts"] = self.tts

            start_scheduler()
            atexit.register(stop_scheduler)
            logger.info("⏳ Background sync scheduler started.")
        except Exception as exc:
            logger.error(f"❌ Prewarm failed: {exc}")
            self._error_message = str(exc)
            self.current_state = ControllerState.ERROR
            raise

        self.current_state = ControllerState.READY
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.PREWARMING.name} → {self.current_state.name}")
        self._previous_state = ControllerState.PREWARMING
        self._state_updated_at = time.time()

    async def start_session(self, ctx: JobContext, agent_instance: Any) -> AgentSession:
        """Runs READY → CONNECTING → SESSION_ACTIVE and returns the active AgentSession.

        Also spawns a background task that keeps driving the loop through
        SESSION_ACTIVE → ENDING → READY once `request_shutdown()` fires —
        without it, nothing would advance the FSM after this call returns.
        """
        self.request_job(ctx, agent_instance)
        while self.current_state != ControllerState.SESSION_ACTIVE:
            await self.run_state_machine()
            if self.current_state == ControllerState.ERROR:
                raise RuntimeError(self._error_message or "session start failed")

        asyncio.create_task(self._drive_until_ready())
        return self._active_session

    async def _drive_until_ready(self) -> None:
        """Background pump: keeps the FSM alive through SESSION_ACTIVE → ENDING → READY."""
        while self.current_state not in (ControllerState.READY, ControllerState.ERROR):
            await self.run_state_machine()

    # -----------------------
    # HELPERS (called from inside match cases, same role as
    # send_illuminator() being called from SIState.EXECUTE)
    # -----------------------
    def _build_engines(self) -> None:
        self.llm = inference.LLM(model="openai/gpt-4o-mini")
        logger.info("[FSM] LLM (LiveKit Inference) constructed.")

        self.stt = inference.STT(model="deepgram/nova-3")
        logger.info("[FSM] STT (LiveKit Inference) constructed.")

        self.tts = inference.TTS(
            model="deepgram/aura-2",
            voice="andromeda",
            language="en",
        )
        logger.info("[FSM] TTS (LiveKit Inference) constructed.")

    def _prime_ollama_model(self) -> None:
        """Synchronous warm-up request so the first real turn isn't slow."""
        try:
            ollama_host = self.ollama_base_url.rstrip("/")
            if ollama_host.endswith("/v1"):
                ollama_host = ollama_host[:-3]

            payload = json.dumps({
                "model": self.ollama_model,
                "prompt": self.system_prompt,
                "stream": False,
                "keep_alive": -1,
                "options": {
                    "num_predict": 1,
                    "think": False,
                },
            }).encode()

            req = urllib.request.Request(
                f"{ollama_host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            logger.info(f"[FSM] Priming Ollama model '{self.ollama_model}'...")
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
            logger.info("[FSM] Ollama model loaded and system prompt primed.")
        except Exception as exc:
            # Non-fatal — worth logging but shouldn't push us to ERROR.
            logger.warning(f"[FSM] Ollama warm-up skipped (non-fatal): {exc}")

    def health_check(self) -> Dict[str, Any]:
        """Returns status report and state machine metrics."""
        return {
            "state": self.current_state.name,
            "previous_state": self._previous_state.name if self._previous_state else None,
            "state_duration_seconds": round(time.time() - self._state_updated_at, 2),
            "ollama_model": self.ollama_model,
            "deepgram_key_configured": bool(self.deepgram_api_key),
            "stt_active": self.stt is not None,
            "llm_active": self.llm is not None,
            "tts_active": self.tts is not None,
            "error_message": self._error_message,
        }

    # =========================================================================
    # FSM LOOP — mirrors SafetyIslandEngine.run_state_machine()
    # =========================================================================
    async def run_state_machine(self):

        match self.current_state:

            # -----------------------
            # UNINITIALIZED
            # -----------------------
            case ControllerState.UNINITIALIZED:
                logger.info("🚀 Voice Controller FSM Started")
                logger.info("🔄 ControllerState.UNINITIALIZED")
                self.next_state = ControllerState.INITIALIZING

            # -----------------------
            # INITIALIZING — construct STT / LLM / TTS
            # -----------------------
            case ControllerState.INITIALIZING:
                try:
                    self._build_engines()
                    self.next_state = ControllerState.PREWARMING
                except Exception as exc:
                    logger.error(f"❌ Engine construction failed: {exc}")
                    self._error_message = str(exc)
                    self.next_state = ControllerState.ERROR

            # -----------------------
            # PREWARMING — scheduler + Ollama warm-up
            # -----------------------
            case ControllerState.PREWARMING:
                try:
                    if self._pending_proc is not None:
                        self._pending_proc.userdata["llm"] = self.llm
                        self._pending_proc.userdata["stt"] = self.stt
                        self._pending_proc.userdata["tts"] = self.tts

                    start_scheduler()
                    atexit.register(stop_scheduler)
                    logger.info("⏳ Background sync scheduler started.")

                    self._prime_ollama_model()
                    self.next_state = ControllerState.READY
                except Exception as exc:
                    logger.error(f"❌ Prewarm failed: {exc}")
                    self._error_message = str(exc)
                    self.next_state = ControllerState.ERROR

            # -----------------------
            # READY — wait for an incoming call job
            # -----------------------
            case ControllerState.READY:

                job = None
                try:
                    if not self.job_queue.empty():
                        job = self.job_queue.get_nowait()
                except Empty:
                    pass

                if job:
                    ctx, agent_instance, proc = job
                    self._active_ctx = ctx
                    self._active_agent = agent_instance
                    logger.info(f"📞 Job received for room '{ctx.room.name if ctx.room else 'pending'}'")
                    self.next_state = ControllerState.CONNECTING
                else:
                    self.next_state = ControllerState.READY

            # -----------------------
            # CONNECTING — join the LiveKit room, wait for participant
            # -----------------------
            case ControllerState.CONNECTING:
                try:
                    ctx = self._active_ctx
                    await ctx.connect()
                    logger.info("🔗 Connected to LiveKit room.")

                    participant = await ctx.wait_for_participant()
                    self._active_participant_identity = participant.identity
                    logger.info(f"🙋 Participant joined: {participant.identity}")

                    llm = ctx.proc.userdata.get("llm", self.llm) if ctx.proc else self.llm
                    stt = ctx.proc.userdata.get("stt", self.stt) if ctx.proc else self.stt
                    tts = ctx.proc.userdata.get("tts", self.tts) if ctx.proc else self.tts

                    if not all([llm, stt, tts]):
                        logger.warning("⚠️ Pipeline engines missing from proc.userdata — building fresh.")
                        self._build_engines()
                        llm, stt, tts = self.llm, self.stt, self.tts

                    self._active_session = AgentSession(stt=stt, llm=llm, tts=tts)

                    # FIX: request_shutdown is now `async def` — see the
                    # detailed note on its definition above. LiveKit awaits
                    # every registered shutdown callback directly, so a
                    # sync function here previously caused
                    # `TypeError: object NoneType can't be used in 'await'
                    # expression` during job teardown and prevented this
                    # controller's own ENDING transition from being logged.
                    ctx.add_shutdown_callback(self.request_shutdown)

                    await self._active_session.start(
                        room=ctx.room,
                        agent=self._active_agent,
                    )
                    logger.info("🎙️ Agent voice session running.")

                    self.next_state = ControllerState.SESSION_ACTIVE
                except Exception as exc:
                    logger.error(f"❌ Connect/session start failed: {exc}")
                    self._error_message = str(exc)
                    self.next_state = ControllerState.ERROR

            # -----------------------
            # SESSION_ACTIVE — running call, wait for shutdown signal
            # -----------------------
            case ControllerState.SESSION_ACTIVE:
                if self._shutdown_requested:
                    logger.info("📴 Call session disconnect requested")
                    self.next_state = ControllerState.ENDING
                else:
                    self.next_state = ControllerState.SESSION_ACTIVE

            # -----------------------
            # ENDING — tear down and reset for the next call
            # -----------------------
            case ControllerState.ENDING:
                self._shutdown_requested = False
                self._active_ctx = None
                self._active_agent = None
                self._active_session = None
                self._active_participant_identity = ""
                logger.info("🧹 Reset state for next call job")
                self.next_state = ControllerState.READY

            # -----------------------
            # ERROR — terminal-ish; log and idle here
            # -----------------------
            case ControllerState.ERROR:
                logger.error(f"🛑 ControllerState.ERROR: {self._error_message}")
                self.next_state = ControllerState.ERROR

        # -----------------------
        # TRANSITION
        # -----------------------
        if self.current_state != self.next_state:
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {self.current_state.name} → {self.next_state.name}")
            self._previous_state = self.current_state
            self._state_updated_at = time.time()

        self.current_state = self.next_state

        await asyncio.sleep(0.05)