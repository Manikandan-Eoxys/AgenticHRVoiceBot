"""
hr_voice_agent/audio/resample.py

Audio Resampling and Framing Utilities
======================================
Converts linear 16-bit mono PCM between different sample rates (e.g. 8kHz Asterisk <-> 16kHz STT/TTS <-> 48kHz WebRTC)
using high-performance numpy and soxr libraries.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

try:
    import soxr
    HAS_SOXR = True
except ImportError:
    HAS_SOXR = False

logger = logging.getLogger("audio.resample")


def resample_pcm(
    pcm_bytes: bytes,
    in_rate: int,
    out_rate: int,
    quality: str = "HQ",
) -> bytes:
    """
    Resamples raw linear 16-bit mono PCM bytes from in_rate to out_rate.
    Returns resampled 16-bit mono PCM bytes.
    """
    if not pcm_bytes or in_rate == out_rate:
        return pcm_bytes

    # Convert to signed 16-bit numpy array
    signal = np.frombuffer(pcm_bytes, dtype=np.int16)

    if HAS_SOXR:
        # Use soxr for high quality, low-latency sinc resampling
        resampled = soxr.resample(signal, in_rate, out_rate, quality=quality)
        # Clip to int16 boundaries to avoid integer wrap-around distortion
        clipped = np.clip(resampled, -32768, 32767).astype(np.int16)
        return clipped.tobytes()
    else:
        # Fallback linear interpolation
        num_output_samples = int(round(len(signal) * float(out_rate) / float(in_rate)))
        orig_indices = np.linspace(0, len(signal) - 1, len(signal))
        new_indices = np.linspace(0, len(signal) - 1, num_output_samples)
        resampled = np.interp(new_indices, orig_indices, signal)
        clipped = np.clip(resampled, -32768, 32767).astype(np.int16)
        return clipped.tobytes()


class Resampler:
    """
    Stateful resampler for streaming audio buffers.
    """

    def __init__(self, in_rate: int, out_rate: int, quality: str = "HQ") -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.quality = quality

    def resample(self, pcm_bytes: bytes) -> bytes:
        return resample_pcm(pcm_bytes, self.in_rate, self.out_rate, self.quality)


def slice_frames(
    pcm_bytes: bytes,
    frame_size_bytes: int = 320,
    pad: bool = False,
) -> List[bytes]:
    """
    Slices raw PCM bytes into fixed-length frames (e.g. 320 bytes = 20ms @ 8kHz 16-bit).
    If pad=True, the final chunk will be padded with zeros (silence) to frame_size_bytes.
    """
    if not pcm_bytes:
        return []

    frames = []
    for offset in range(0, len(pcm_bytes), frame_size_bytes):
        chunk = pcm_bytes[offset : offset + frame_size_bytes]
        if len(chunk) < frame_size_bytes:
            if pad:
                chunk = chunk + (b"\x00" * (frame_size_bytes - len(chunk)))
                frames.append(chunk)
            break
        frames.append(chunk)
    return frames
