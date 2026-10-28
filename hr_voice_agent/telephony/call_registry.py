"""
hr_voice_agent/telephony/call_registry.py

Call Session Registry & Metadata Mapping
========================================
Maintains bidirectional mappings between:
  - Asterisk ARI Channel ID
  - AudioSocket Call UUID (RFC 4122)
  - Active Telephony Session

Ensures isolation between concurrent calls and guarantees atomic session lookup/cleanup.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time

from hr_voice_agent.telephony.base import BaseTelephonySession

logger = logging.getLogger("telephony.registry")


@dataclass
class CallRecord:
    """Metadata tracking a call through pending and active states."""
    call_uuid: str
    channel_id: str
    caller_number: str
    created_at: float = field(default_factory=time.time)
    bridge_id: Optional[str] = None
    external_channel_id: Optional[str] = None
    session: Optional[BaseTelephonySession] = None


class CallRegistry:
    """
    Thread-safe / asyncio-safe registry for active and pending telephone calls.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Mappings
        self._by_uuid: Dict[str, CallRecord] = {}
        self._by_channel_id: Dict[str, CallRecord] = {}
        self._sessions: Dict[str, BaseTelephonySession] = {}  # call_id -> session

    async def register_pending(
        self,
        call_uuid: str,
        channel_id: str,
        caller_number: str,
    ) -> CallRecord:
        """
        Registers an incoming ARI channel awaiting AudioSocket TCP connection.
        """
        async with self._lock:
            record = CallRecord(
                call_uuid=call_uuid,
                channel_id=channel_id,
                caller_number=caller_number,
            )
            self._by_uuid[call_uuid] = record
            self._by_channel_id[channel_id] = record
            logger.debug(
                f"📝 Registered pending call: UUID={call_uuid}, Channel={channel_id}, Caller={caller_number}"
            )
            return record

    async def associate_bridge_and_media(
        self,
        channel_id: str,
        bridge_id: str,
        external_channel_id: str,
    ) -> Optional[CallRecord]:
        """Attaches bridge_id and external_channel_id to the pending call record."""
        async with self._lock:
            record = self._by_channel_id.get(channel_id)
            if record:
                record.bridge_id = bridge_id
                record.external_channel_id = external_channel_id
            return record

    async def resolve_audiosocket(self, call_uuid: str) -> Optional[CallRecord]:
        """
        Resolves an incoming AudioSocket connection by UUID.
        Falls back to the oldest pending record if UUID is not found directly.
        """
        async with self._lock:
            if call_uuid in self._by_uuid:
                return self._by_uuid[call_uuid]
            # Fallback: if single pending record exists, match it
            if len(self._by_uuid) == 1:
                return next(iter(self._by_uuid.values()))
            return None

    async def register_session(self, session: BaseTelephonySession) -> None:
        """Registers an active session."""
        async with self._lock:
            self._sessions[session.call_id] = session
            record = self._by_channel_id.get(session.call_id)
            if record:
                record.session = session
            logger.info(
                f"📞 Active sessions count: {len(self._sessions)} (added {session.call_id})"
            )

    async def get_session(self, call_id: str) -> Optional[BaseTelephonySession]:
        """Gets active session by call ID or channel ID."""
        async with self._lock:
            return self._sessions.get(call_id)

    async def get_record_by_channel(self, channel_id: str) -> Optional[CallRecord]:
        """Gets call record by channel ID."""
        async with self._lock:
            return self._by_channel_id.get(channel_id)

    async def remove_call(self, channel_id: str) -> Optional[CallRecord]:
        """
        Removes call and associated session from registry on hangup/termination.
        """
        async with self._lock:
            record = self._by_channel_id.pop(channel_id, None)
            if record:
                self._by_uuid.pop(record.call_uuid, None)
                self._sessions.pop(channel_id, None)
                self._sessions.pop(record.call_uuid, None)
                logger.info(
                    f"🧹 Cleaned up call: Channel={channel_id}, UUID={record.call_uuid}. "
                    f"Remaining sessions: {len(self._sessions)}"
                )
            else:
                self._sessions.pop(channel_id, None)
            return record

    async def all_sessions(self) -> List[BaseTelephonySession]:
        """Returns all currently active telephony sessions."""
        async with self._lock:
            return list(self._sessions.values())

    async def active_count(self) -> int:
        """Returns the number of currently active sessions."""
        async with self._lock:
            return len(self._sessions)

    async def clear(self) -> None:
        """Clears all records."""
        async with self._lock:
            self._by_uuid.clear( )
            self._by_channel_id.clear()
            self._sessions.clear()
