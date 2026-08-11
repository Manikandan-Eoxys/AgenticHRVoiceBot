"""
controllers/voice_controller.py

Voice Controller for HR Voice Assistant with Embedded-Style Switch-Case (Match-Case) State Machine.
Uses explicit (current_state, next_state) tuple matching to drive state transitions
and execute state actions for STT, LLM, TTS, and LiveKit session lifecycles.
"""

import json
import logging
import time
import urllib.request
import atexit
from enum import Enum
from typing import Dict, Any, Optional

from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
)
from livekit.plugins.deepgram import STT, TTS as DeepgramTTS
from ollama_llm import OllamaLLM

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    DEEPGRAM_API_KEY,
    SYSTEM_PROMPT,
)
from integrations.sync_scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


class ControllerState(str, Enum):
    """Finite State Machine states for VoiceController lifecycle."""
    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    PREWARMING = "PREWARMING"
    READY = "READY"
    CONNECTING = "CONNECTING"
    SESSION_ACTIVE = "SESSION_ACTIVE"
    ENDING = "ENDING"
    ERROR = "ERROR"


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted in the FSM."""
    pass


class VoiceController:
    """
    Embedded-Style Switch-Case State Machine Controller for HR Voice Assistant.
    Coordinates STT, LLM, TTS, and LiveKit session lifecycles using explicit
    (current_state, next_state) match-case state dispatching.
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

        self.llm: Optional[OllamaLLM] = None
        self.stt: Optional[STT] = None
        self.tts: Optional[DeepgramTTS] = None

        # State Machine Initialization
        self._state: ControllerState = ControllerState.UNINITIALIZED
        self._previous_state: Optional[ControllerState] = None
        self._state_updated_at: float = time.time()
        self._error_message: Optional[str] = None

        logger.info(f"[Embedded FSM] Controller initialized in state: {self._state.value}")

    @property
    def state(self) -> ControllerState:
        return self._state

    def _set_state(self, new_state: ControllerState, reason: str = "") -> None:
        """Internal helper to log and assign state."""
        old_state = self._state
        self._previous_state = old_state
        self._state = new_state
        self._state_updated_at = time.time()
        reason_str = f" [{reason}]" if reason else ""
        logger.info(f"[Embedded FSM] State Transition: {old_state.value} ➔ {new_state.value}{reason_str}")

    # =========================================================================
    # EMBEDDED SWITCH-CASE (MATCH-CASE) STATE MACHINE DISPATCHER
    # =========================================================================
    def transition(self, next_state: ControllerState, **kwargs: Any) -> Any:
        """
        Embedded-style State Dispatcher matching (current_state, next_state).
        Executes enter/exit actions and returns transition result.
        """
        current_state = self._state

        match (current_state, next_state):
            # 1. UNINITIALIZED ➔ INITIALIZING
            case (ControllerState.UNINITIALIZED, ControllerState.INITIALIZING):
                return self._handle_uninitialized_to_initializing(**kwargs)

            # 2. INITIALIZING ➔ PREWARMING
            case (ControllerState.INITIALIZING, ControllerState.PREWARMING):
                return self._handle_initializing_to_prewarming(**kwargs)

            # 3. PREWARMING ➔ READY
            case (ControllerState.PREWARMING, ControllerState.READY):
                return self._handle_prewarming_to_ready(**kwargs)

            # 4. READY ➔ CONNECTING
            case (ControllerState.READY, ControllerState.CONNECTING):
                return self._handle_ready_to_connecting(**kwargs)

            # 5. CONNECTING ➔ SESSION_ACTIVE
            case (ControllerState.CONNECTING, ControllerState.SESSION_ACTIVE):
                return self._handle_connecting_to_active(**kwargs)

            # 6. SESSION_ACTIVE ➔ ENDING
            case (ControllerState.SESSION_ACTIVE, ControllerState.ENDING):
                return self._handle_active_to_ending(**kwargs)

            # 7. ENDING ➔ READY
            case (ControllerState.ENDING, ControllerState.READY):
                return self._handle_ending_to_ready(**kwargs)

            # 8. ANY STATE ➔ ERROR
            case (_, ControllerState.ERROR):
                return self._handle_to_error(**kwargs)

            # INVALID TRANSITION
            case _:
                err_msg = f"Invalid state transition: {current_state.value} ➔ {next_state.value}"
                logger.error(f"[Embedded FSM] {err_msg}")
                raise InvalidStateTransitionError(err_msg)

    # =========================================================================
    # STATE TRANSITION ACTION HANDLERS
    # =========================================================================

    def _handle_uninitialized_to_initializing(self, **kwargs: Any) -> Dict[str, Any]:
        """Action handler: Construct STT, LLM, and TTS plugin instances."""
        self._set_state(ControllerState.INITIALIZING, reason="Constructing STT, LLM, TTS engines")
        try:
            # 1. LLM Initialization (Ollama native /api/chat wrapper)
            self.llm = OllamaLLM(
                model=self.ollama_model,
                base_url=self.ollama_base_url,
                think=False,
                timeout=60.0,
            )
            logger.info("[Embedded FSM] LLM (OllamaLLM) constructed.")

            # 2. STT Initialization (Deepgram Nova-3)
            self.stt = STT(
                api_key=self.deepgram_api_key,
                model="nova-3",
            )
            logger.info("[Embedded FSM] STT (Deepgram Nova-3) constructed.")

            # 3. TTS Initialization (Deepgram)
            self.tts = DeepgramTTS(
                api_key=self.deepgram_api_key,
            )
            logger.info("[Embedded FSM] TTS (DeepgramTTS) constructed.")

            return {"stt": self.stt, "llm": self.llm, "tts": self.tts}
        except Exception as exc:
            self.transition(ControllerState.ERROR, error=exc)
            raise

    def _handle_initializing_to_prewarming(self, proc: JobProcess, **kwargs: Any) -> None:
        """Action handler: Start sync scheduler & prime Ollama system prompt."""
        self._set_state(ControllerState.PREWARMING, reason="Starting sync scheduler & priming Ollama model")
        try:
            proc.userdata["llm"] = self.llm
            proc.userdata["stt"] = self.stt
            proc.userdata["tts"] = self.tts

            # Start background sync scheduler
            start_scheduler()
            atexit.register(stop_scheduler)
            logger.info("[Embedded FSM] Background sync scheduler started.")

            # Prime Ollama model & system prompt
            self._prime_ollama_model()
        except Exception as exc:
            self.transition(ControllerState.ERROR, error=exc)
            raise

    def _handle_prewarming_to_ready(self, **kwargs: Any) -> None:
        """Action handler: Set state to READY for incoming call jobs."""
        self._set_state(ControllerState.READY, reason="Worker process prewarm complete")

    def _handle_ready_to_connecting(self, ctx: JobContext, **kwargs: Any) -> None:
        """Action handler: Mark room connection in progress."""
        room_name = ctx.room.name if ctx.room else "pending"
        self._set_state(ControllerState.CONNECTING, reason=f"Connecting to room '{room_name}'")

    def _handle_connecting_to_active(self, participant_identity: str = "", **kwargs: Any) -> None:
        """Action handler: Mark call session active."""
        self._set_state(ControllerState.SESSION_ACTIVE, reason=f"Participant '{participant_identity}' connected")

    def _handle_active_to_ending(self, **kwargs: Any) -> None:
        """Action handler: Mark call session tearing down."""
        self._set_state(ControllerState.ENDING, reason="Call session disconnect requested")

    def _handle_ending_to_ready(self, **kwargs: Any) -> None:
        """Action handler: Reset state back to READY for next call."""
        self._set_state(ControllerState.READY, reason="Reset state for next call job")

    def _handle_to_error(self, error: Optional[Exception] = None, **kwargs: Any) -> None:
        """Action handler: Record error state."""
        self._error_message = str(error) if error else "Unknown error"
        self._set_state(ControllerState.ERROR, reason=f"Error encountered: {self._error_message}")

    # =========================================================================
    # PUBLIC HIGH-LEVEL PIPELINE METHODS
    # =========================================================================

    def initialize_pipeline(self) -> Dict[str, Any]:
        """Convenience caller for UNINITIALIZED ➔ INITIALIZING transition."""
        return self.transition(ControllerState.INITIALIZING)

    def prewarm_pipeline(self, proc: JobProcess) -> None:
        """
        Executes worker prewarm pipeline:
        1. UNINITIALIZED ➔ INITIALIZING
        2. INITIALIZING ➔ PREWARMING
        3. PREWARMING ➔ READY
        """
        self.transition(ControllerState.INITIALIZING)
        self.transition(ControllerState.PREWARMING, proc=proc)
        self.transition(ControllerState.READY)

    def _prime_ollama_model(self) -> None:
        """Send synchronous request to Ollama to load model and prime system prompt."""
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

            logger.info(f"[Embedded FSM] Priming Ollama model '{self.ollama_model}'...")
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
            logger.info("[Embedded FSM] Ollama model loaded and system prompt primed.")
        except Exception as exc:
            logger.warning(f"[Embedded FSM] Ollama warm-up skipped (non-fatal): {exc}")

    async def start_session(self, ctx: JobContext, agent_instance: Any) -> AgentSession:
        """
        Connects to LiveKit room and manages session startup through FSM transitions:
        READY ➔ CONNECTING ➔ SESSION_ACTIVE ➔ (ENDING ➔ READY on shutdown)
        """
        self.transition(ControllerState.CONNECTING, ctx=ctx)
        try:
            await ctx.connect()
            logger.info("[Embedded FSM] Connected to LiveKit room.")

            participant = await ctx.wait_for_participant()
            logger.info(f"[Embedded FSM] Participant joined: {participant.identity}")

            # Retrieve pre-warmed engines from proc.userdata if available
            llm = ctx.proc.userdata.get("llm", self.llm)
            stt = ctx.proc.userdata.get("stt", self.stt)
            tts = ctx.proc.userdata.get("tts", self.tts)

            if not all([llm, stt, tts]):
                logger.info("[Embedded FSM] Pipeline engines not found in proc.userdata, initializing fresh.")
                pipeline = self.initialize_pipeline()
                llm, stt, tts = pipeline["llm"], pipeline["stt"], pipeline["tts"]

            session = AgentSession(
                stt=stt,
                llm=llm,
                tts=tts,
            )

            self.transition(ControllerState.SESSION_ACTIVE, participant_identity=participant.identity)

            # Register shutdown callback to transition ENDING ➔ READY when call ends
            def on_close():
                self.transition(ControllerState.ENDING)
                self.transition(ControllerState.READY)

            ctx.add_shutdown_callback(on_close)

            await session.start(
                room=ctx.room,
                agent=agent_instance,
            )

            logger.info("[Embedded FSM] Agent voice session running.")
            return session

        except Exception as exc:
            self.transition(ControllerState.ERROR, error=exc)
            raise

    def health_check(self) -> Dict[str, Any]:
        """Returns status report and state machine metrics."""
        return {
            "state": self._state.value,
            "previous_state": self._previous_state.value if self._previous_state else None,
            "state_duration_seconds": round(time.time() - self._state_updated_at, 2),
            "ollama_model": self.ollama_model,
            "deepgram_key_configured": bool(self.deepgram_api_key),
            "stt_active": self.stt is not None,
            "llm_active": self.llm is not None,
            "tts_active": self.tts is not None,
            "error_message": self._error_message,
        }
