"""webcall/audio_io.py - audio helpers for the browser <-> AudioSocket bridge.

Pipeline speaks 8 kHz mono s16 PCM in 20 ms frames (320 bytes). WebRTC/Opus is 48 kHz,
so conversion happens here at the edge and the pipeline stays untouched.
"""
from __future__ import annotations

import asyncio
import fractions
import time
from collections import deque
from typing import List, Optional

import numpy as np

try:
    import av
    from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
except ImportError:  # pragma: no cover - unit tests run without aiortc
    av = None
    MediaStreamError = Exception
    MediaStreamTrack = object

RATE = 8000
FRAME_SAMPLES = 160
FRAME_BYTES = FRAME_SAMPLES * 2
SILENCE = bytes(FRAME_BYTES)


class FrameSlicer:
    """Re-cuts arbitrary PCM chunks into exact 20 ms frames."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> List[bytes]:
        self._buf.extend(data)
        out = []
        while len(self._buf) >= self.frame_bytes:
            out.append(bytes(self._buf[: self.frame_bytes]))
            del self._buf[: self.frame_bytes]
        return out


class FrameQueue:
    """Small jitter buffer between the agent's paced frames and the WebRTC 20 ms tick."""

    def __init__(self, max_frames: int = 25, prebuffer: int = 2) -> None:
        self.max_frames, self.prebuffer = max_frames, prebuffer
        self._q: deque = deque()
        self._playing = False
        self.dropped = 0

    def push(self, frame: bytes) -> None:
        if len(self._q) >= self.max_frames:
            self._q.popleft()
            self.dropped += 1
        self._q.append(frame)

    def pop(self) -> Optional[bytes]:
        if not self._playing:
            if len(self._q) < self.prebuffer:
                return None
            self._playing = True
        if not self._q:
            self._playing = False
            return None
        return self._q.popleft()

    def clear(self) -> None:
        self._q.clear()
        self._playing = False

    def __len__(self) -> int:
        return len(self._q)


class InboundConverter:
    """Streaming av.AudioFrame (48 kHz, any layout) -> 8 kHz mono s16 PCM bytes."""

    def __init__(self, rate: int = RATE) -> None:
        self._rate, self._sig, self._rs = rate, None, None

    def convert(self, frame) -> bytes:
        sig = (frame.format.name, frame.layout.name, frame.sample_rate)
        if sig != self._sig:
            self._rs = av.AudioResampler(format="s16", layout="mono", rate=self._rate)
            self._sig = sig
        out = self._rs.resample(frame)
        frames = out if isinstance(out, (list, tuple)) else ([out] if out is not None else [])
        return b"".join(
            np.ascontiguousarray(f.to_ndarray()).reshape(-1)[: f.samples].astype(np.int16).tobytes()
            for f in frames
        )


class OutboundAudioTrack(MediaStreamTrack):
    """Plays the agent's 8 kHz frames to the browser, 20 ms at a time (silence when idle)."""

    kind = "audio"

    def __init__(self, queue: FrameQueue) -> None:
        super().__init__()
        self._queue = queue
        self._start: Optional[float] = None
        self._ts = 0

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if self._start is None:
            self._start = time.monotonic()
        else:
            self._ts += FRAME_SAMPLES
            wait = self._start + self._ts / RATE - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        frame.planes[0].update(self._queue.pop() or SILENCE)
        frame.pts = self._ts
        frame.sample_rate = RATE
        frame.time_base = fractions.Fraction(1, RATE)
        return frame
