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
        self.relaying = False                      # True once the browser is talking to a human instead of the AI
        self.on_event: Optional[Callable[[str], None]] = None   # e.g. "call_transferred" -> sent to the browser
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._relay_closed_cb: Optional[Callable[[], None]] = None
        self._done: Optional[asyncio.Future] = None
        self._out_task: Optional[asyncio.Task] = None
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
            self.server.web_bridges[self.call_uuid] = self     # lets the coordinator transfer find this call
        self._writer.write(encode_packet(TYPE_UUID, uuid.UUID(self.call_uuid).bytes))
        await self._writer.drain()
        logger.info("📞 [WebCall %s] connected to pipeline as %s (uuid %s)",
                    self.call_id, self.caller_label, self.call_uuid)

        self._done = asyncio.get_running_loop().create_future()
        self._out_task = asyncio.create_task(self._guard(self._pump_out()), name=f"webcall-out-{self.call_id}")
        self._tasks = [
            asyncio.create_task(self._guard(self._pump_in(read_pcm)), name=f"webcall-in-{self.call_id}"),
            self._out_task,
            asyncio.create_task(self._guard(self._watchdog()), name=f"webcall-limit-{self.call_id}"),
        ]
        return await self.close(await self._done)

    def request_end(self, reason: str) -> None:
        """Ask run() to finish the call (safe to call from anywhere, any number of times)."""
        if self._done is not None and not self._done.done():
            self._done.set_result(reason)

    async def _guard(self, coro) -> None:
        """Run one pump; whatever ends it also ends the call."""
        try:
            reason = await coro
        except asyncio.CancelledError:
            return
        except Exception as exc:
            reason = f"error:{type(exc).__name__}"
        if self.relaying and reason in ("agent_closed", "agent_hangup", "agent_error"):
            logger.info("ℹ️ [WebCall %s] AI agent leg disconnected after transfer (expected)", self.call_id)
            return
        self.request_end(reason)

    async def _pump_in(self, read_pcm) -> str:
        """Browser microphone (8 kHz PCM) -> the AI agent, or (after a transfer) the coordinator."""
        slicer = FrameSlicer()
        while True:
            try:
                pcm = await read_pcm()
            except asyncio.CancelledError:
                raise
            except Exception:
                return "browser_closed"
            try:
                writer = self._writer
                if writer is not None and not writer.is_closing():
                    for frame in slicer.push(pcm):
                        writer.write(encode_audio_frame(frame))
                    await writer.drain()
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError):
                if self.relaying:
                    return "coordinator_closed"
                await asyncio.sleep(0.05)

    async def _pump_out(self) -> str:
        """The AI agent (or coordinator) audio -> jitter buffer -> browser track."""
        slicer = FrameSlicer()
        who = "coordinator" if self.relaying else "agent"
        while True:
            try:
                msg_type, payload = await read_packet(self._reader)
            except asyncio.CancelledError:
                raise
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return f"{who}_closed"
            if msg_type == TYPE_AUDIO:
                for frame in slicer.push(payload):
                    self.outbound.push(frame)
            elif msg_type == TYPE_HANGUP:
                return f"{who}_hangup"
            elif msg_type in (TYPE_ERROR, TYPE_ERROR_FF):
                return f"{who}_error"

    async def attach_relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                           on_closed: Optional[Callable[[], None]] = None) -> None:
        """Hand the browser over to a human: mic -> `writer`, `reader` -> browser; the AI agent is disconnected."""
        old_writer, old_task = self._writer, self._out_task
        self._relay_closed_cb = on_closed
        self.relaying = True
        self._reader, self._writer = reader, writer
        self.outbound.clear()
        if old_task is not None:
            old_task.cancel()
            self._tasks = [t for t in self._tasks if t is not old_task]
        self._out_task = asyncio.create_task(self._guard(self._pump_out()), name=f"webcall-relay-{self.call_id}")
        self._tasks.append(self._out_task)
        try:                                           # end the AI session's connection politely
            old_writer.write(encode_hangup())
            await old_writer.drain()
            old_writer.close()
        except Exception:
            pass
        logger.info("🔀 [WebCall %s] browser is now connected to the coordinator", self.call_id)
        if self.on_event:
            try:
                self.on_event("call_transferred")
            except Exception:
                pass

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
        if self._relay_closed_cb is not None:
            try:
                self._relay_closed_cb()
            except Exception:
                pass
        if self.server is not None:
            self.server.web_bridges.pop(self.call_uuid, None)
            transfers = getattr(self.server, "transfers", None)
            if transfers is not None:                      # browser left while talking to the coordinator: drop that leg
                try:
                    await transfers.end(self.call_uuid, hang_up="coordinator")
                except Exception:
                    pass
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
