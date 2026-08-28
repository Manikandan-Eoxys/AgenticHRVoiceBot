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

        self.job_queue: "asyncio.Queue[Tuple[JobContext, Any, Optional[JobProcess]]]" = asyncio.Queue()

        self._pending_proc: Optional[JobProcess] = None
        self._active_ctx: Optional[JobContext] = None
        self._active_agent: Optional[Any] = None
        self._active_session: Optional[AgentSession] = None
        self._active_participant_identity: str = ""
        self._shutdown_requested: bool = False

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

    async def request_shutdown(self) -> None:
        """Called from LiveKit shutdown callback to end active session."""
        self._shutdown_requested = True

        if self.current_state in (ControllerState.SESSION_ACTIVE, ControllerState.LISTENING, ControllerState.THINKING, ControllerState.SPEAKING):
            logger.info("📴 Call session disconnect requested")
            old = self.current_state
            self.current_state = ControllerState.ENDING
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → ENDING")
            self._state_updated_at = time.time()

            self._active_ctx = None
            self._active_agent = None
            self._active_session = None
            self._active_participant_identity = ""
            logger.info("🧹 Reset state for next call job")

            self.next_state = ControllerState.READY
            logger.info(f"➡️ VC Transition [pid={self._pid}]: ENDING → READY")
            self.current_state = ControllerState.READY
            self._previous_state = ControllerState.ENDING
            self._state_updated_at = time.time()
            self._shutdown_requested = False

    # -----------------------
    # COMPATIBILITY WRAPPERS & PREWARM
    # -----------------------
    def prewarm_pipeline(self, proc: JobProcess) -> None:
        """Runs UNINITIALIZED → INITIALIZING → PREWARMING → READY directly."""
        self.request_prewarm(proc)

        self.current_state = ControllerState.INITIALIZING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {ControllerState.UNINITIALIZED.name} → INITIALIZING")
        try:
            self._build_engines()
        except Exception as exc:
            logger.error(f"❌ Engine construction failed: {exc}")
            self._error_message = str(exc)
            self.current_state = ControllerState.ERROR
            raise

        self.current_state = ControllerState.PREWARMING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: INITIALIZING → PREWARMING")
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
        logger.info(f"➡️ VC Transition [pid={self._pid}]: PREWARMING → READY")
        self._previous_state = ControllerState.PREWARMING
        self._state_updated_at = time.time()

    # -----------------------
    # DUAL-FSM EVENT OBSERVERS (LiveKit AgentServer Integration)
    # -----------------------
    def on_worker_start(self) -> None:
        """Called at worker startup to prewarm Ollama & DB."""
        old = self.current_state
        self.current_state = ControllerState.INITIALIZING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → INITIALIZING")

        self.current_state = ControllerState.PREWARMING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: INITIALIZING → PREWARMING")
        self._prime_ollama_model()

        self.current_state = ControllerState.READY
        logger.info(f"➡️ VC Transition [pid={self._pid}]: PREWARMING → READY [System ready for call jobs]")

    def on_job_accepted(self, room_name: str = "") -> None:
        """Called when an incoming call job request is accepted."""
        old = self.current_state
        self.current_state = ControllerState.JOB_ACCEPTED
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → JOB_ACCEPTED (Room: '{room_name}')")

    def on_call_start(self, room_name: str = "") -> None:
        """Called when a call enters the room."""
        old = self.current_state
        self.current_state = ControllerState.CONNECTING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → CONNECTING (Room: '{room_name}')")

    def on_participant_joined(self, identity: str = "") -> None:
        """Called when caller audio stream is active."""
        old = self.current_state
        self.current_state = ControllerState.SESSION_ACTIVE
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → SESSION_ACTIVE (Participant: '{identity}')")

    def on_agent_substate_changed(self, substate: str) -> None:
        """Tracks active turn sub-states: LISTENING, THINKING, SPEAKING."""
        sub_upper = substate.upper()
        target_map = {
            "LISTENING": ControllerState.LISTENING,
            "THINKING": ControllerState.THINKING,
            "SPEAKING": ControllerState.SPEAKING,
            "INITIALIZING": ControllerState.SESSION_ACTIVE,
        }
        target = target_map.get(sub_upper, ControllerState.SESSION_ACTIVE)
        if self.current_state != target:
            old = self.current_state
            self.current_state = target
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → {target.name}")

    def on_call_end(self) -> None:
        """Called when call ends or participant disconnects."""
        old = self.current_state
        self.current_state = ControllerState.ENDING
        logger.info(f"➡️ VC Transition [pid={self._pid}]: {old.name} → ENDING")
        self.current_state = ControllerState.READY
        logger.info(f"➡️ VC Transition [pid={self._pid}]: ENDING → READY [Reset for next call]")

    def on_error(self, error: Exception) -> None:
        """Called when an unhandled error occurs."""
        self._error_message = str(error)
        old = self.current_state
        self.current_state = ControllerState.ERROR
        logger.error(f"➡️ VC Transition [pid={self._pid}]: {old.name} → ERROR ({error})")

    async def start_session(self, ctx: JobContext, agent_instance: Any) -> AgentSession:
        """Runs READY → CONNECTING → SESSION_ACTIVE and returns active AgentSession."""
        self.request_job(ctx, agent_instance)
        while self.current_state not in (ControllerState.SESSION_ACTIVE, ControllerState.LISTENING):
            await self.run_state_machine()
            if self.current_state == ControllerState.ERROR:
                raise RuntimeError(self._error_message or "session start failed")

        asyncio.create_task(self._drive_until_ready())
        return self._active_session

    async def _drive_until_ready(self) -> None:
        """Background pump: keeps FSM alive through active call → ENDING → READY."""
        while self.current_state not in (ControllerState.READY, ControllerState.ERROR):
            await self.run_state_machine()

    def _build_engines(self) -> None:
        import os
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

    # =========================================================================
    # 12-STATE FSM DISPATCHER
    # =========================================================================
    async def run_state_machine(self):

        match self.current_state:

            case ControllerState.UNINITIALIZED:
                logger.info("🚀 Voice Controller 12-State FSM Started")
                self.next_state = ControllerState.INITIALIZING

            case ControllerState.INITIALIZING:
                try:
                    self._build_engines()
                    self.next_state = ControllerState.PREWARMING
                except Exception as exc:
                    logger.error(f"❌ Engine construction failed: {exc}")
                    self._error_message = str(exc)
                    self.next_state = ControllerState.ERROR

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

            case ControllerState.JOB_ACCEPTED:
                self.next_state = ControllerState.CONNECTING

            case ControllerState.CONNECTING:
                try:
                    ctx = self._active_ctx
                    if ctx:
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

            case ControllerState.SESSION_ACTIVE:
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING

            case ControllerState.LISTENING:
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING

            case ControllerState.THINKING:
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING

            case ControllerState.SPEAKING:
                if self._shutdown_requested:
                    self.next_state = ControllerState.ENDING

            case ControllerState.ENDING:
                self._shutdown_requested = False
                self._active_ctx = None
                self._active_agent = None
                self._active_session = None
                self._active_participant_identity = ""
                logger.info("🧹 Reset state for next call job")
                self.next_state = ControllerState.READY

            case ControllerState.ERROR:
                logger.error(f"🛑 ControllerState.ERROR: {self._error_message}")
                self.next_state = ControllerState.ERROR

        if self.current_state != self.next_state:
            logger.info(f"➡️ VC Transition [pid={self._pid}]: {self.current_state.name} → {self.next_state.name}")
            self._previous_state = self.current_state
            self._state_updated_at = time.time()

        self.current_state = self.next_state
        await asyncio.sleep(0.05)