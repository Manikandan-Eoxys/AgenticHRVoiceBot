"""webcall/bridge.py - one browser call <-> one AudioSocket connection into the existing pipeline.

The bridge pretends to be Asterisk: it opens a TCP connection to AsteriskServer's AudioSocket
port, sends the UUID handshake, then exchanges 20 ms 8 kHz PCM packets. AsteriskVoiceSession
(STT, LLM, goals/FSM, RAG, TTS, recording) runs exactly as it does for a phone call.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Optional

from audio.audiosocket import (
    TYPE_AUDIO,
    TYPE_ERROR,
    TYPE_HANGUP,
    TYPE_UUID,
    encode_audio_frame,
    encode_hangup,
    encode_packet,
    read_packet,
)

from .audio_io import FrameQueue, FrameSlicer
from .settings import WebCallSettings

logger = logging.getLogger("webcall.bridge")
TYPE_ERROR_FF = 0xFF
DTMF_KEYS = frozenset("0123456789*#")


class WebCallBridge:
    def __init__(self, call_id: str, caller_label: str, settings: WebCallSettings,
                 asterisk_server: Optional[object] = None) -> None:
        self.call_id = call_id
        self.caller_label = caller_label
        self.settings = settings
        self.server = asterisk_server
        self.outbound = FrameQueue()
        self.call_uuid = str(uuid.uuid4())
        self.reason = ""
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._tasks: list = []
        self._closed = False

    async def run(self, read_pcm: Callable[[], Awaitable[bytes]]) -> str:
        s = self.settings
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(s.audiosocket_host, s.audiosocket_port), timeout=5)
        except (OSError, asyncio.TimeoutError) as exc:
            logger.error("❌ [WebCall %s] AudioSocket %s:%s unreachable: %s",
                         self.call_id, s.audiosocket_host, s.audiosocket_port, exc)
            return await self.close("pipeline_unreachable")

        if self.server is not None:   # AsteriskServer reads channel_callers[channel_id] on handshake (channel_id == uuid)
            self.server.channel_callers[self.call_uuid] = self.caller_label
        self._writer.write(encode_packet(TYPE_UUID, uuid.UUID(self.call_uuid).bytes))
        await self._writer.drain()
        logger.info("📞 [WebCall %s] connected to pipeline as %s (uuid %s)",
                    self.call_id, self.caller_label, self.call_uuid)

        self._tasks = [
            asyncio.create_task(self._pump_in(read_pcm), name=f"webcall-in-{self.call_id}"),
            asyncio.create_task(self._pump_out(), name=f"webcall-out-{self.call_id}"),
            asyncio.create_task(self._watchdog(), name=f"webcall-limit-{self.call_id}"),
        ]
        done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        first = next(iter(done))
        if first.cancelled():
            reason = "cancelled"
        elif first.exception() is not None:
            reason = f"error:{type(first.exception()).__name__}"
        else:
            reason = first.result()
        return await self.close(reason)

    async def _pump_in(self, read_pcm) -> str:
        slicer = FrameSlicer()
        while True:
            try:
                pcm = await read_pcm()
            except Exception:
                return "browser_closed"
            for frame in slicer.push(pcm):
                self._writer.write(encode_audio_frame(frame))
            await self._writer.drain()

    async def _pump_out(self) -> str:
        slicer = FrameSlicer()
        while True:
            try:
                msg_type, payload = await read_packet(self._reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                return "agent_closed"
            if msg_type == TYPE_AUDIO:
                for frame in slicer.push(payload):
                    self.outbound.push(frame)
            elif msg_type == TYPE_HANGUP:
                return "agent_hangup"
            elif msg_type in (TYPE_ERROR, TYPE_ERROR_FF):
                return "agent_error"

    async def _watchdog(self) -> str:
        await asyncio.sleep(self.settings.max_seconds)
        return "time_limit"

    def send_digit(self, digit: str) -> bool:
        """Keypad digit from the browser -> same handler Asterisk's ARI DTMF event calls."""
        if not (isinstance(digit, str) and len(digit) == 1 and digit in DTMF_KEYS) or self.server is None:
            return False
        session = self.server.active_sessions.get(self.call_uuid)
        if session is None:
            return False
        session.on_dtmf_digit(digit)
        return True

    async def close(self, reason: str = "ended") -> str:
        if self._closed:
            return self.reason
        self._closed, self.reason = True, reason
        current = asyncio.current_task()
        for t in self._tasks:
            if t is not current and not t.done():
                t.cancel()
        if self._writer is not None:
            try:
                if not self._writer.is_closing():
                    self._writer.write(encode_hangup())
                    await self._writer.drain()
                self._writer.close()
            except Exception:
                pass
        if self.server is not None:
            session = self.server.active_sessions.pop(self.call_uuid, None)
            self.server.channel_callers.pop(self.call_uuid, None)
            if session is not None and getattr(session, "running", False):
                try:
                    await asyncio.wait_for(session.hangup(), timeout=3)
                except Exception:
                    pass
        self.outbound.clear()
        logger.info("📴 [WebCall %s] bridge closed (%s)", self.call_id, reason)
        return reason
