"""
hr_voice_agent/tests/test_telephony_base.py

Unit tests for BaseTelephonySession abstraction and interface compliance.
"""

import asyncio
from typing import AsyncIterator, Optional
import pytest

from hr_voice_agent.telephony.base import BaseTelephonySession


class MockTelephonySession(BaseTelephonySession):
    def __init__(self, call_id: str = "mock-1", caller_number: str = "100") -> None:
        super().__init__(call_id=call_id, caller_number=caller_number)
        self.sent_audio: list[bytes] = []
        self.dtmf_sent: list[str] = []
        self.interrupted = False
        self.transferred_to: Optional[str] = None
        self.hungup = False
        self._inbound_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

    def feed_inbound(self, pcm: bytes) -> None:
        self._inbound_queue.put_nowait(pcm)

    def close_inbound(self) -> None:
        self._inbound_queue.put_nowait(None)

    async def audio_frames(self) -> AsyncIterator[bytes]:
        while self.is_active:
            frame = await self._inbound_queue.get()
            if frame is None:
                break
            yield frame

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    def interrupt_speech(self) -> None:
        self.interrupted = True

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        return True

    async def send_dtmf(self, digits: str) -> bool:
        self.dtmf_sent.append(digits)
        return True

    async def transfer_call(self, target: str) -> bool:
        self.transferred_to = target
        return True

    async def hangup(self) -> None:
        self._is_active = False
        self.hungup = True


def test_base_telephony_session_interface():
    async def _test():
        session = MockTelephonySession(call_id="call_abc", caller_number="9876543210")
        assert session.is_active is True
        assert session.call_id == "call_abc"
        assert session.caller_number == "9876543210"

        # DTMF callback dispatch
        received = []
        session.on_dtmf(lambda d: received.append(d))
        session.dispatch_dtmf("9")
        assert received == ["9"]

        # Send audio
        await session.send_audio(b"sample_outbound_pcm")
        assert session.sent_audio == [b"sample_outbound_pcm"]

        # Interrupt
        session.interrupt_speech()
        assert session.interrupted is True

        # Send DTMF
        assert await session.send_dtmf("123") is True
        assert session.dtmf_sent == ["123"]

        # Transfer
        assert await session.transfer_call("PJSIP/coordinator") is True
        assert session.transferred_to == "PJSIP/coordinator"

        # Inbound audio stream
        session.feed_inbound(b"frame_1")
        session.feed_inbound(b"frame_2")
        session.close_inbound()

        collected = []
        async for f in session.audio_frames():
            collected.append(f)
        assert collected == [b"frame_1", b"frame_2"]

        # Hangup
        await session.hangup()
        assert session.is_active is False
        assert session.hungup is True

    asyncio.run(_test())
