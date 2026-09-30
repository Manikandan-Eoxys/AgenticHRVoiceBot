"""
agentVoiceController.py

Voice pipeline lifecycle manager for the HR Voice Agent.

Responsibilities:
  - Manages the per-call FSM: UNINITIALIZED → READY → CONNECTING → LISTENING
    → THINKING → SPEAKING → ENDING
  - Delegates to the lower-level VoiceController (controllers/voice_controller.py)
    for per-room state tracking and the GlobalMonitorFSM for cross-call monitoring
  - Provides a single object agents/assistant code can call for all pipeline events
    without importing the legacy controllers directly

Thin wrapper principle: all *real* state lives in VoiceController and
GlobalMonitorFSM; this controller simply fans out lifecycle events to the
right sub-systems and adds structured logging.
"""

import asyncio
import logging
import os
import time
from typing import Optional

from controllers.voice_controller import VoiceController
from controllers.global_monitor_fsm import GlobalMonitorFSM

logger = logging.getLogger("controllers.agent_voice")


class AgentVoiceController:
    """
    Facade that manages the voice pipeline lifecycle for one worker process.

    One instance per worker (not per call). Multiple concurrent calls are
    tracked by room name inside the underlying VoiceController.

    Usage (in agent.py):
        avc = AgentVoiceController()
        avc.on_worker_start()           # called once at worker init
        avc.on_call_start(room_name)    # called when a call arrives
        avc.on_agent_state(new_state)   # called on agent_state_changed
        avc.on_participant_joined(identity)
        avc.on_call_end()               # called when caller disconnects
    """

    def __init__(self) -> None:
        self._vc: VoiceController = VoiceController()
        self._fsm_task: Optional[asyncio.Task] = None
        self._call_start_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Worker-level lifecycle
    # ------------------------------------------------------------------

    def on_worker_start(self) -> None:
        """Must be called once when the worker process starts (before any calls)."""
        self._vc.on_worker_start()
        logger.info("🚀 [AgentVoiceController] Worker started — voice pipeline ready.")

    def ensure_fsm_running(self) -> None:
        """Lazily starts the GlobalMonitorFSM asyncio task (idempotent)."""
        if self._fsm_task is None or self._fsm_task.done():
            self._fsm_task = asyncio.create_task(
                GlobalMonitorFSM.instance().run_loop(),
                name="GlobalMonitorFSM",
            )
            logger.info("🌐 [AgentVoiceController] GlobalMonitorFSM task started.")

    # ------------------------------------------------------------------
    # Call-level lifecycle
    # ------------------------------------------------------------------

    def on_call_start(self, room_name: str) -> None:
        """Called when a new call arrives (READY → CONNECTING transition)."""
        self._call_start_times[room_name] = time.monotonic()
        self._vc.on_call_start(room_name)
        logger.info(f"📞 [AgentVoiceController] Call started in room '{room_name}'.")

    def on_agent_state(self, new_state: str) -> None:
        """
        Called on every agent_state_changed event.
        Forwards to VoiceController for per-room FSM advancement.
        """
        self._vc.on_agent_substate_changed(new_state)

    def on_participant_joined(self, participant_identity: str) -> None:
        """Called when a caller participant joins the room."""
        self._vc.on_participant_joined(participant_identity)
        logger.debug(
            f"👤 [AgentVoiceController] Participant joined: '{participant_identity}'."
        )

    def on_call_end(self, room_name: str = "") -> None:
        """Called when the caller disconnects or the call is ended."""
        self._vc.on_call_end()
        elapsed = 0.0
        if room_name and room_name in self._call_start_times:
            elapsed = time.monotonic() - self._call_start_times.pop(room_name)
        logger.info(
            f"📴 [AgentVoiceController] Call ended"
            + (f" (room='{room_name}', duration={elapsed:.1f}s)." if room_name else ".")
        )

    # ------------------------------------------------------------------
    # Pass-through properties (for code that needs the raw vc)
    # ------------------------------------------------------------------

    @property
    def voice_controller(self) -> VoiceController:
        return self._vc

    @property
    def global_fsm(self) -> GlobalMonitorFSM:
        return GlobalMonitorFSM.instance()
