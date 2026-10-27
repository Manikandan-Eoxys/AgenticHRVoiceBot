"""
hr_voice_agent/asterisk/context.py

Duck-Typed Context Abstractions for Asterisk Telephony
======================================================
Provides seamless compatibility with LiveKit's RunContext, JobContext,
and Room abstractions so all domain goals (g01 to g13), VoiceController,
and security checks run without modification.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict

logger = logging.getLogger("asterisk.context")


class PlayoutHandle:
    """Awaitable handle returned by session.say() to wait for speech playout."""

    def __init__(self, wait_fn: Callable[[], Any]) -> None:
        self._wait_fn = wait_fn

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        res = self._wait_fn()
        if asyncio.iscoroutine(res):
            return await res
        return True


class AsteriskParticipant:
    """Mock participant representing caller, agent, or coordinator."""

    def __init__(self, identity: str, name: str = "") -> None:
        self.identity = identity
        self.name = name or identity
        self.attributes: Dict[str, str] = {}

    async def disconnect(self) -> None:
        pass


class AsteriskRoom:
    """Mock room representation for an Asterisk call session."""

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
    """
    Duck-typed replacement for LiveKit JobContext.
    Used by VoiceController, security blocklist, and session recorder.
    """

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
    Duck-typed replacement for LiveKit RunContext passed into @function_tool actions.
    Provides context.session, context.wait_for_playout(), and context.agent.
    """

    def __init__(self, session: Any, agent: Any = None) -> None:
        self.session = session
        self.agent = agent

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        if hasattr(self.session, "wait_for_playout"):
            return await self.session.wait_for_playout(timeout)
        return True
