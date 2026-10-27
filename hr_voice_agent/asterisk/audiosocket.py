"""
hr_voice_agent/asterisk/audiosocket.py

Asterisk AudioSocket Protocol Implementation
=============================================
RFC/Specification: Asterisk app_audiosocket / res_audiosocket / chan_audiosocket

Wire Format:
  [Type (1 byte)] [Payload Length (2 bytes, big-endian uint16)] [Payload (N bytes)]

Message Types:
  0x00 = Hangup (asterisk/client disconnect)
  0x01 = UUID (16-byte binary UUID or ASCII UUID string)
  0x02 = Silence (heartbeat)
  0x03 = Error
  0x10 = Linear 16-bit signed PCM audio
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Optional, Tuple

logger = logging.getLogger("asterisk.audiosocket")

# AudioSocket protocol byte types
TYPE_HANGUP   = 0x00
TYPE_UUID     = 0x01
TYPE_SILENCE  = 0x02
TYPE_ERROR    = 0x03
TYPE_AUDIO    = 0x10

HEADER_SIZE = 3               # 1 byte type + 2 bytes big-endian length
FRAME_DURATION_MS = 20        # 20ms audio frames standard in telephony
BYTES_PER_SAMPLE = 2          # 16-bit signed PCM = 2 bytes per sample


def samples_per_frame(sample_rate: int = 8000, duration_ms: int = FRAME_DURATION_MS) -> int:
    """Number of audio samples in one frame (e.g. 160 samples @ 8kHz / 20ms)."""
    return int(sample_rate * (duration_ms / 1000.0))


def bytes_per_frame(sample_rate: int = 8000, duration_ms: int = FRAME_DURATION_MS) -> int:
    """Byte length of one raw PCM frame (e.g. 320 bytes @ 8kHz / 20ms)."""
    return samples_per_frame(sample_rate, duration_ms) * BYTES_PER_SAMPLE


def encode_packet(msg_type: int, payload: bytes = b"") -> bytes:
    """Encodes an AudioSocket packet with 3-byte header."""
    return struct.pack(">BH", msg_type, len(payload)) + payload


def encode_audio_frame(pcm_data: bytes) -> bytes:
    """Encodes a TYPE_AUDIO (0x10) packet with PCM payload."""
    return encode_packet(TYPE_AUDIO, pcm_data)


def encode_hangup() -> bytes:
    """Encodes a TYPE_HANGUP (0x00) packet."""
    return encode_packet(TYPE_HANGUP, b"")


async def read_packet(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
    """
    Reads one complete AudioSocket packet from the stream.
    Returns (msg_type, payload).
    Raises asyncio.IncompleteReadError on socket EOF.
    """
    header = await reader.readexactly(HEADER_SIZE)
    msg_type, msg_len = struct.unpack(">BH", header)
    if msg_len > 0:
        payload = await reader.readexactly(msg_len)
    else:
        payload = b""
    return msg_type, payload


class AudioSocketWriterQueue:
    """
    Paced Audio Streamer for Asterisk AudioSocket.

    Maintains an asynchronous queue of 20ms audio frames and paces delivery
    at real-time rate (every ~20ms). Supports instant barge-in / interruption
    by purging all pending frames immediately.
    """

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        sample_rate: int = 8000,
        frame_duration_ms: int = FRAME_DURATION_MS,
    ) -> None:
        self.writer = writer
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = bytes_per_frame(sample_rate, frame_duration_ms)
        self.frame_interval = frame_duration_ms / 1000.0  # 0.02s

        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running = True
        self._is_playing = False
        self._playout_done_event = asyncio.Event()
        self._playout_done_event.set()
        self._worker_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Starts the background frame delivery loop."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._playback_loop(), name="AudioSocketPacer")

    @property
    def is_playing(self) -> bool:
        return self._is_playing or not self._queue.empty()

    def enqueue_pcm(self, pcm_data: bytes) -> None:
        """
        Slices arbitrary length PCM data into exact 20ms frames and enqueues them.
        """
        if not pcm_data or not self._running:
            return

        self._is_playing = True
        self._playout_done_event.clear()

        # Slice into exact frame_bytes chunks
        step = self.frame_bytes
        for offset in range(0, len(pcm_data), step):
            chunk = pcm_data[offset : offset + step]
            if len(chunk) < step:
                # Pad final short chunk with silence to maintain 20ms framing
                chunk = chunk + (b"\x00" * (step - len(chunk)))
            self._queue.put_nowait(chunk)

    def interrupt(self) -> None:
        """
        Barge-in: Instantly purges all queued audio frames and stops playback.
        """
        discarded = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                discarded += 1
            except asyncio.QueueEmpty:
                break

        self._is_playing = False
        self._playout_done_event.set()
        if discarded > 0:
            logger.info(f"⚡ [AudioSocket] Barge-in interruption: purged {discarded} audio frames ({discarded * 20}ms).")

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """
        Waits until all currently enqueued audio frames have been transmitted to Asterisk.
        """
        if self._queue.empty() and not self._is_playing:
            return True
        try:
            if timeout is not None:
                await asyncio.wait_for(self._playout_done_event.wait(), timeout=timeout)
            else:
                await self._playout_done_event.wait()
            return True
        except asyncio.TimeoutError:
            return False

    async def _playback_loop(self) -> None:
        """Continuously pops 20ms frames and sends them with precise timing."""
        next_deadline = time.monotonic()

        while self._running:
            try:
                # Wait for next frame (or idle timeout)
                try:
                    frame = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if self._queue.empty():
                        self._is_playing = False
                        self._playout_done_event.set()
                    continue

                if frame is None or not self._running:
                    break

                # Send packet over TCP
                packet = encode_audio_frame(frame)
                self.writer.write(packet)
                await self.writer.drain()
                self._queue.task_done()

                # Maintain 20ms cadence
                now = time.monotonic()
                if next_deadline < now:
                    next_deadline = now
                next_deadline += self.frame_interval
                sleep_duration = next_deadline - time.monotonic()
                if sleep_duration > 0.001:
                    await asyncio.sleep(sleep_duration)

            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                break
            except Exception as e:
                logger.warning(f"⚠️ AudioSocket writer error: {e}")
                break

        self._is_playing = False
        self._playout_done_event.set()

    async def close(self) -> None:
        """Stops the loop and writes hangup if socket is open."""
        self._running = False
        self.interrupt()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        try:
            if not self.writer.is_closing():
                self.writer.write(encode_hangup())
                await self.writer.drain()
                self.writer.close()
                await self.writer.wait_closed()
        except Exception:
            pass
