"""
hr_voice_agent/audio/__init__.py

Audio processing, resampling, voice activity detection (VAD), and real-time pacing.
"""

try:
    from .resample import resample_pcm, Resampler, slice_frames
    from .vad import VoiceActivityDetector, VadState, VadEvent
    from .pacer import AudioPacer
except ImportError:
    from audio.resample import resample_pcm, Resampler, slice_frames
    from audio.vad import VoiceActivityDetector, VadState, VadEvent
    from audio.pacer import AudioPacer

__all__ = [
    "resample_pcm",
    "Resampler",
    "slice_frames",
    "VoiceActivityDetector",
    "VadState",
    "VadEvent",
    "AudioPacer",
]
