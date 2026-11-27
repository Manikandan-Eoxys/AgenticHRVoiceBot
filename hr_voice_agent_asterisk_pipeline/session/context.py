"""
hr_voice_agent/session/context.py

CallContext & Asterisk Context Adapters
=======================================
Holds all per-call metadata, verification state, channel identifiers,
telephony session references, and compatibility context adapters
(`AsteriskJobContext`, `AsteriskRunContext`, `PlayoutHandle`, etc.) for
tools, goals, and the VoiceController FSM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from session.base import BaseTelephonySession

logger = logging.getLogger("session.context")


class PlayoutHandle:
    """Awaitable handle returned by session.say() to wait for speech playout."""

    def __init__(self, wait_fn: Callable[[], Any]) -> None:
        self._wait_fn = wait_fn

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        res = self._wait_fn()
        if asyncio.iscoroutine(res):
            return await res
        return True

    async def wait(self, timeout: Optional[float] = None) -> bool:
        """Alias for wait_for_playout."""
        return await self.wait_for_playout(timeout=timeout)


class AsteriskParticipant:
    """Participant representation for caller, agent, or coordinator."""

    def __init__(self, identity: str, name: str = "") -> None:
        self.identity = identity
        self.name = name or identity
        self.attributes: Dict[str, str] = {}

    async def disconnect(self) -> None:
        pass


class AsteriskRoom:
    """Room representation for an Asterisk call session."""

    def __init__(self, name: str, caller_id: str = "asterisk_caller") -> None:
        self.name = name
        self.local_participant = AsteriskParticipant(identity="agent-worker", name="HRVoiceAgent")
        caller_p = AsteriskParticipant(identity=f"sip_{caller_id}", name=caller_id)
        self.remote_participants: Dict[str, AsteriskParticipant] = {caller_p.identity: caller_p}
        self._events: Dict[str, list[Callable]] = {}

    def on(self, event_name: str, handler: Callable) -> None:
        self._events.setdefault(event_name, []).append(handler)

    def emit(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        for handler in self._events.get(event_name, []):
            try:
                res = handler(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.warning(f"Error in room event handler for '{event_name}': {e}")


class AsteriskJobContext:
    """Asterisk telephony JobContext tracking session and hangup hooks."""

    def __init__(self, room_name: str, caller_id: str, on_disconnect: Optional[Callable[[], Any]] = None) -> None:
        self.room = AsteriskRoom(name=room_name, caller_id=caller_id)
        self.caller_id = caller_id
        self._on_disconnect = on_disconnect
        self._shutdown_callbacks: list[Callable] = []

    def add_shutdown_callback(self, cb: Callable) -> None:
        self._shutdown_callbacks.append(cb)

    async def disconnect(self) -> None:
        logger.info(f"📴 [AsteriskJobContext] Disconnecting call {self.room.name}...")
        for cb in self._shutdown_callbacks:
            try:
                res = cb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug(f"Error in shutdown callback: {e}")
        if self._on_disconnect:
            res = self._on_disconnect()
            if asyncio.iscoroutine(res):
                await res


class AsteriskRunContext:
    """
    Asterisk RunContext passed into @function_tool actions.
    Provides context.session, context.wait_for_playout(), and context.agent.
    """

    def __init__(self, session: Any, agent: Any = None) -> None:
        self.session = session
        self.agent = agent

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        if hasattr(self.session, "wait_for_playout"):
            return await self.session.wait_for_playout(timeout)
        return True


# Backwards-compatible aliases
DuckParticipant = AsteriskParticipant
DuckRoom = AsteriskRoom
DuckJobContext = AsteriskJobContext
DuckRunContext = AsteriskRunContext


@dataclass
class CallContext:
    """
    Call-specific state context isolating resources across concurrent calls.
    """
    call_id: str
    caller_number: str
    channel_id: str
    external_channel_id: Optional[str] = None
    bridge_id: Optional[str] = None
    room_name: str = ""
    created_at: float = field(default_factory=time.time)

    # Verification state (Identity Verification goal)
    employee_verified: bool = False
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None
    employee_data: Dict[str, Any] = field(default_factory=dict)

    # Telephony session and pipeline references
    session: Optional[BaseTelephonySession] = None
    pipeline: Optional[Any] = None
    voice_controller: Optional[Any] = None
    recorder: Optional[Any] = None

    # DTMF digit accumulation buffer
    dtmf_buffer: List[str] = field(default_factory=list)
    last_dtmf_time: float = 0.0

    # Custom call metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.room_name:
            self.room_name = f"call_{self.caller_number}_{self.call_id[:8]}"
        self.job_ctx = DuckJobContext(
            room_name=self.room_name,
            caller_id=self.caller_number,
            on_disconnect=self._on_disconnect_callback,
        )
        self.run_ctx = DuckRunContext(session=self)

    def _on_disconnect_callback(self) -> None:
        if self.session and self.session.is_active:
            asyncio.create_task(self.session.hangup())

    @property
    def verified_employee_id(self) -> Optional[str]:
        return self.employee_id

    @verified_employee_id.setter
    def verified_employee_id(self, val: Optional[str]) -> None:
        self.employee_id = val

    async def transfer_call(self, target: str) -> bool:
        """Transfers the underlying telephony session."""
        if self.session and hasattr(self.session, "transfer_call"):
            return await self.session.transfer_call(target)
        return False

    async def say(self, text: str) -> bool:
        """Helper to speak text via the pipeline TTS."""
        if self.pipeline and hasattr(self.pipeline, "tts"):
            return await self.pipeline.tts.speak_text(text)
        return False

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        if self.session:
            return await self.session.wait_for_playout(timeout=timeout)
        return True

    def mark_verified(self, emp_id: str, emp_name: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Marks employee as authenticated."""
        self.employee_verified = True
        self.employee_id = emp_id
        self.employee_name = emp_name
        if data:
            self.employee_data = data
        self.metadata["verified_employee_id"] = emp_id
        self.metadata["verified_employee_name"] = emp_name
        logger.info(f"🔒 [CallContext {self.call_id}] Employee identity verified: {emp_name} (ID: {emp_id})")
