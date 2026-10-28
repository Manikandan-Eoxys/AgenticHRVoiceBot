"""
hr_voice_agent/pipeline/turn_manager.py

Turn Manager & Conversational Turn State Controller
===================================================
Coordinates local VAD, Deepgram STT transcripts, and endpointing to emit clean,
single-utterance conversational user turns.
Detects barge-in speech during bot playback and triggers interruption callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, List, Optional

from hr_voice_agent.audio.vad import VoiceActivityDetector, VadEvent

logger = logging.getLogger("pipeline.turn_manager")


class TurnEvent:
    """Event container for completed turns or turn lifecycle signals."""
    SPEECH_STARTED   = "speech_started"
    BARGE_IN         = "barge_in"
    TURN_COMPLETED   = "turn_completed"


class TurnManager:
    """
    Manages conversational turn boundaries, transcript aggregation, and barge-in detection.
    """

    def __init__(
        self,
        sample_rate: int = 8000,
        frame_duration_ms: int = 20,
        speech_start_ms: int = 80,
        silence_end_ms: int = 600,   # Configurable 500-700ms
        barge_in_ms: int = 180,      # Configurable 150-250ms
        on_barge_in: Optional[Callable[[], None]] = None,
        on_turn_completed: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_end_ms = silence_end_ms
        self.barge_in_ms = barge_in_ms

        self.on_barge_in = on_barge_in
        self.on_turn_completed = on_turn_completed

        # Internal VAD
        self.vad = VoiceActivityDetector(
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            speech_start_ms=speech_start_ms,
            silence_end_ms=silence_end_ms,
            barge_in_ms=barge_in_ms,
        )

        self.is_bot_speaking = False
        self._utterance_segments: List[str] = []
        self._last_speech_time = 0.0
        self._turn_in_progress = False
        self._lock = asyncio.Lock()
        self._silence_timer_task: Optional[asyncio.Task] = None

    def set_bot_speaking(self, is_speaking: bool) -> None:
        """Notifies the turn manager whether the bot is currently playing speech."""
        self.is_bot_speaking = is_speaking

    def process_audio_frame(self, frame: bytes) -> Optional[str]:
        """
        Feeds an incoming 20ms audio frame to the VAD.
        Returns TurnEvent signal if an event was triggered.
        """
        vad_event = self.vad.process_frame(frame, is_bot_speaking=self.is_bot_speaking)

        if vad_event == VadEvent.SPEECH_START:
            self._turn_in_progress = True
            self._last_speech_time = time.monotonic()

            if self.is_bot_speaking:
                logger.info(f"⚡ [TurnManager] Barge-in detected ({self.barge_in_ms}ms speech threshold)!")
                if self.on_barge_in:
                    self.on_barge_in()
                return TurnEvent.BARGE_IN
            return TurnEvent.SPEECH_STARTED

        elif vad_event == VadEvent.SPEECH_ONGOING:
            self._last_speech_time = time.monotonic()

        elif vad_event == VadEvent.SPEECH_END:
            # Silence threshold met locally
            if self._turn_in_progress and self._utterance_segments:
                self._trigger_turn_complete()
                return TurnEvent.TURN_COMPLETED

        return None

    def on_stt_transcript(self, transcript: str, speech_final: bool) -> None:
        """
        Handles incoming finalized transcript segments from Deepgram.
        """
        text = transcript.strip()
        if not text:
            return

        self._turn_in_progress = True
        self._utterance_segments.append(text)
        self._last_speech_time = time.monotonic()
        logger.debug(f"🎙️ [TurnManager] Appended segment: '{text}' (speech_final={speech_final})")

        if speech_final:
            self._trigger_turn_complete()

    def _trigger_turn_complete(self) -> None:
        """Emits completed turn with aggregated user utterance."""
        if not self._utterance_segments:
            return

        full_utterance = " ".join(self._utterance_segments).strip()
        self._utterance_segments.clear()
        self._turn_in_progress = False
        self.vad.reset()

        if full_utterance:
            logger.info(f"🗣️ [TurnManager] Completed User Turn: \"{full_utterance}\"")
            if self.on_turn_completed:
                self.on_turn_completed(full_utterance)

    def flush(self) -> None:
        """Clears pending segments without emitting."""
        self._utterance_segments.clear()
        self._turn_in_progress = False
        self.vad.reset()
