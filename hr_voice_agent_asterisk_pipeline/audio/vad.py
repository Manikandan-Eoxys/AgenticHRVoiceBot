"""
hr_voice_agent/audio/vad.py

Voice Activity Detection (VAD) and Turn State Tracker
=====================================================
Detects speech versus silence in 20ms audio frames using WebRTC VAD with RMS energy fallback.
Tracks speech start, ongoing speech, end-of-turn silence thresholds, and barge-in triggers.
"""

from __future__ import annotations

import enum
import logging
from typing import Optional

import numpy as np

try:
    import webrtcvad
    HAS_WEBRTC_VAD = True
except ImportError:
    HAS_WEBRTC_VAD = False

logger = logging.getLogger("audio.vad")


class VadState(enum.Enum):
    LISTENING = "listening"       # Caller is silent, waiting for speech
    SPEAKING  = "speaking"        # Caller is actively speaking


class VadEvent(enum.Enum):
    SILENCE        = "silence"        # Frame is silence, caller not speaking
    SPEECH_START   = "speech_start"   # Caller just started speaking
    SPEECH_ONGOING = "speech_ongoing" # Caller continues speaking
    SPEECH_END     = "speech_end"     # Caller finished speaking (silence threshold reached)


class VoiceActivityDetector:
    """
    Frame-by-frame Voice Activity Detector with conversational turn tracking.
    """

    def __init__(
        self,
        sample_rate: int = 8000,
        frame_duration_ms: int = 20,
        vad_mode: int = 2,  # 0=least aggressive, 3=most aggressive
        speech_start_ms: int = 80,       # Consecutive speech to declare start (e.g. 4 frames @ 20ms)
        silence_end_ms: int = 600,       # Consecutive silence to declare end of turn (e.g. 30 frames @ 20ms)
        barge_in_ms: int = 160,          # Consecutive speech to trigger barge-in interruption (e.g. 8 frames)
        energy_threshold: float = 350.0, # RMS energy fallback threshold
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * (frame_duration_ms / 1000.0) * 2)  # 320 bytes @ 8kHz/20ms
        self.vad_mode = vad_mode
        self.energy_threshold = energy_threshold

        self.speech_start_frames = max(1, speech_start_ms // frame_duration_ms)
        self.silence_end_frames  = max(1, silence_end_ms // frame_duration_ms)
        self.barge_in_frames     = max(1, barge_in_ms // frame_duration_ms)

        self._webrtc_vad: Optional[webrtcvad.Vad] = None
        if HAS_WEBRTC_VAD and sample_rate in (8000, 16000, 32000, 48000):
            try:
                self._webrtc_vad = webrtcvad.Vad(self.vad_mode)
            except Exception as e:
                logger.warning(f"Could not initialize webrtcvad: {e}")

        # State tracking
        self.state = VadState.LISTENING
        self._consecutive_speech = 0
        self._consecutive_silence = 0

    def is_speech(self, frame: bytes) -> bool:
        """
        Evaluates whether a single frame contains voice.
        Uses webrtcvad if available, with energy threshold verification.
        """
        if len(frame) != self.frame_bytes:
            return False

        webrtc_speech = False
        if self._webrtc_vad is not None:
            try:
                webrtc_speech = self._webrtc_vad.is_speech(frame, self.sample_rate)
            except Exception:
                webrtc_speech = False

        # RMS Energy calculation
        samples = np.frombuffer(frame, dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        energy_speech = rms > self.energy_threshold

        # If WebRTC VAD is available, require both or trust WebRTC when RMS is reasonable
        if self._webrtc_vad is not None:
            return webrtc_speech and (rms > (self.energy_threshold * 0.4))
        return energy_speech

    def process_frame(self, frame: bytes, is_bot_speaking: bool = False) -> VadEvent:
        """
        Processes one 20ms audio frame and transitions turn state.
        Returns the corresponding VadEvent.
        """
        has_speech = self.is_speech(frame)

        if has_speech:
            self._consecutive_speech += 1
            self._consecutive_silence = 0

            threshold = self.barge_in_frames if is_bot_speaking else self.speech_start_frames

            if self.state == VadState.LISTENING:
                if self._consecutive_speech >= threshold:
                    self.state = VadState.SPEAKING
                    return VadEvent.SPEECH_START
                return VadEvent.SILENCE
            else:
                return VadEvent.SPEECH_ONGOING

        else:
            self._consecutive_silence += 1
            self._consecutive_speech = 0

            if self.state == VadState.SPEAKING:
                if self._consecutive_silence >= self.silence_end_frames:
                    self.state = VadState.LISTENING
                    return VadEvent.SPEECH_END
                return VadEvent.SPEECH_ONGOING
            else:
                return VadEvent.SILENCE

    def reset(self) -> None:
        """Resets the state detector."""
        self.state = VadState.LISTENING
        self._consecutive_speech = 0
        self._consecutive_silence = 0
