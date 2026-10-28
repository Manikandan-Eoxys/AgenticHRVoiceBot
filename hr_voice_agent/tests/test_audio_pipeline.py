"""
hr_voice_agent/tests/test_audio_pipeline.py

Unit tests for Phase 4: Audio Resampling, VAD, and AudioPacer.
"""

import asyncio
import time
import numpy as np
import pytest

from hr_voice_agent.audio.resample import resample_pcm, Resampler, slice_frames
from hr_voice_agent.audio.vad import VoiceActivityDetector, VadState, VadEvent
from hr_voice_agent.audio.pacer import AudioPacer


def test_resample_pcm():
    # 1 second of 440Hz sine wave @ 8000 Hz (8000 samples = 16000 bytes)
    t = np.linspace(0, 1.0, 8000, endpoint=False)
    sine_8k = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16).tobytes()
    assert len(sine_8k) == 16000

    # 1. 8kHz to 16kHz
    resampled_16k = resample_pcm(sine_8k, in_rate=8000, out_rate=16000)
    # 16000 samples @ 16-bit = 32000 bytes (+/- a few samples filter delay)
    assert abs(len(resampled_16k) - 32000) <= 64

    # 2. 16kHz to 8kHz
    resampled_8k = resample_pcm(resampled_16k, in_rate=16000, out_rate=8000)
    assert abs(len(resampled_8k) - 16000) <= 64

    # 3. Same rate identity
    assert resample_pcm(sine_8k, 8000, 8000) == sine_8k

    # 4. Empty buffer
    assert resample_pcm(b"", 8000, 16000) == b""


def test_slice_frames():
    data = b"\x01" * 800  # 800 bytes
    # Frame size = 320 bytes
    frames_no_pad = slice_frames(data, frame_size_bytes=320, pad=False)
    assert len(frames_no_pad) == 2  # 320, 320 (remaining 160 discarded)

    frames_pad = slice_frames(data, frame_size_bytes=320, pad=True)
    assert len(frames_pad) == 3  # 320, 320, 320 (padded)
    assert len(frames_pad[2]) == 320
    assert frames_pad[2][:160] == b"\x01" * 160
    assert frames_pad[2][160:] == b"\x00" * 160


def test_vad_detection():
    vad = VoiceActivityDetector(
        sample_rate=8000,
        frame_duration_ms=20,
        speech_start_ms=40,   # 2 frames
        silence_end_ms=60,    # 3 frames
        barge_in_ms=40,       # 2 frames
    )

    silence_frame = b"\x00" * 320  # Pure zero silence

    # Generate audible speech-like frame (loud sine wave)
    t = np.linspace(0, 0.02, 160, endpoint=False)
    voice_frame = (np.sin(2 * np.pi * 300 * t) * 12000).astype(np.int16).tobytes()

    # 1. Silence check
    assert vad.is_speech(silence_frame) is False
    assert vad.process_frame(silence_frame) == VadEvent.SILENCE

    # 2. First speech frame -> still waiting for start threshold (needs 2 frames)
    evt1 = vad.process_frame(voice_frame)
    assert evt1 == VadEvent.SILENCE

    # 3. Second speech frame -> triggers SPEECH_START
    evt2 = vad.process_frame(voice_frame)
    assert evt2 == VadEvent.SPEECH_START
    assert vad.state == VadState.SPEAKING

    # 4. Ongoing speech
    evt3 = vad.process_frame(voice_frame)
    assert evt3 == VadEvent.SPEECH_ONGOING

    # 5. Silence frames leading to SPEECH_END (needs 3 frames)
    assert vad.process_frame(silence_frame) == VadEvent.SPEECH_ONGOING  # silence 1
    assert vad.process_frame(silence_frame) == VadEvent.SPEECH_ONGOING  # silence 2
    assert vad.process_frame(silence_frame) == VadEvent.SPEECH_END      # silence 3: end of turn
    assert vad.state == VadState.LISTENING


def test_audio_pacer_pacing_and_barge_in():
    async def _test():
        delivered_frames = []

        async def mock_sink(frame: bytes):
            delivered_frames.append(frame)

        pacer = AudioPacer(
            sink=mock_sink,
            sample_rate=8000,
            frame_duration_ms=20,
        )
        pacer.start()

        # Enqueue 10 frames (200ms of audio = 3200 bytes)
        test_pcm = b"\x55" * 3200
        pacer.enqueue_pcm(test_pcm)
        assert pacer.is_playing is True

        # Let 2-3 frames play out
        await asyncio.sleep(0.06)
        assert len(delivered_frames) >= 2

        # Trigger barge-in interrupt
        purged = pacer.interrupt()
        assert purged > 0
        assert pacer.is_playing is False

        # Playout event should be set
        assert await pacer.wait_for_playout(timeout=0.2) is True

        await pacer.close()

    asyncio.run(_test())
