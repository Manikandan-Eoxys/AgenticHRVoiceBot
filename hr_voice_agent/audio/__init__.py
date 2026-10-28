"""
hr_voice_agent/audio/__init__.py

Audio processing, resampling, voice activity detection (VAD), and real-time pacing.
"""

from hr_voice_agent.audio.resample import resample_pcm, Resampler, slice_frames
from hr_voice_agent.audio.vad import VoiceActivityDetector, VadState, VadEvent
from hr_voice_agent.audio.pacer import AudioPacer

__all__ = [
    "resample_pcm",
    "Resampler",
    "slice_frames",
    "VoiceActivityDetector",
    "VadState",
    "VadEvent",
    "AudioPacer",
]
