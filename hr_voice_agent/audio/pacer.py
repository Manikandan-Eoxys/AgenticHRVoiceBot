"""
hr_voice_agent/audio/pacer.py

Real-Time Audio Frame Pacer
===========================
Paces outbound audio frames at strict real-time cadence (every 20ms).
Features:
  - Bounded asyncio queue to prevent latency buildup
  - Drift-free monotonic clock timing
  - Instant barge-in / queue flushing
  - Playout completion events
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, Union

logger = logging.getLogger("audio.pacer")

DEFAULT_SAMPLE_RATE = 8000
DEFAULT_FRAME_DURATION_MS = 20
BYTES_PER_SAMPLE = 2


class AudioPacer:
    """
    Paced Audio Streamer.
    Delivers exactly one frame every `frame_duration_ms` (default 20ms / 320 bytes @ 8kHz).
    """

    def __init__(
        self,
        sink: Union[asyncio.StreamWriter, Callable[[bytes], Awaitable[None]]],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_duration_ms: int = DEFAULT_FRAME_DURATION_MS,
        max_buffered_ms: int = 5000,  # Max 5 seconds in buffer
    ) -> None:
        self.sink = sink
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * (frame_duration_ms / 1000.0) * BYTES_PER_SAMPLE)
        self.frame_interval = frame_duration_ms / 1000.0

        max_queue_items = max(10, max_buffered_ms // frame_duration_ms)
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=max_queue_items)

        self._running = True
        self._is_playing = False
        self._playout_done_event = asyncio.Event()
        self._playout_done_event.set()
        self._worker_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Starts background frame delivery loop."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._pacing_loop(), name="AudioPacer")

    @property
    def is_playing(self) -> bool:
        return self._is_playing or not self._queue.empty()

    def enqueue_pcm(self, pcm_data: bytes) -> None:
        """
        Slices raw linear PCM data into exact 20ms frames and enqueues them.
        """
        if not pcm_data or not self._running:
            return

        self._is_playing = True
        self._playout_done_event.clear()

        step = self.frame_bytes
        for offset in range(0, len(pcm_data), step):
            chunk = pcm_data[offset : offset + step]
            if len(chunk) < step:
                chunk = chunk + (b"\x00" * (step - len(chunk)))
            try:
                self._queue.put_nowait(chunk)
            except asyncio.QueueFull:
                logger.warning("⚠️ AudioPacer queue full; dropping frame to avoid audio delay.")
                break

    def interrupt(self) -> int:
        """
        Barge-in: Instantly flushes all pending audio frames.
        Returns the count of purged frames.
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
            logger.info(
                f"⚡ [AudioPacer] Flushed {discarded} frames ({discarded * self.frame_duration_ms}ms) for barge-in."
            )
        return discarded

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """Waits until all queued frames have been transmitted."""
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

    async def _send_frame(self, frame: bytes) -> None:
        if isinstance(self.sink, asyncio.StreamWriter):
            # Encode as AudioSocket audio frame if writing to stream
            from hr_voice_agent.telephony.audiosocket import encode_audio_frame
            self.sink.write(encode_audio_frame(frame))
            await self.sink.drain()
        elif callable(self.sink):
            res = self.sink(frame)
            if asyncio.iscoroutine(res):
                await res

    async def _pacing_loop(self) -> None:
        """Paces frame delivery with precise monotonic deadline timing."""
        next_deadline = time.monotonic()

        while self._running:
            try:
                try:
                    frame = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if self._queue.empty():
                        self._is_playing = False
                        self._playout_done_event.set()
                    continue

                if frame is None or not self._running:
                    break

                await self._send_frame(frame)
                self._queue.task_done()

                # Maintain exact cadence without clock drift
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
                logger.warning(f"⚠️ AudioPacer send error: {e}")
                break

        self._is_playing = False
        self._playout_done_event.set()

    async def close(self) -> None:
        """Stops worker task and flushes queue."""
        self._running = False
        self.interrupt()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
