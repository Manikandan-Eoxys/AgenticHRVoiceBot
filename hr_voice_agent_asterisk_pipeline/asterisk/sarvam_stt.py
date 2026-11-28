"""
hr_voice_agent/asterisk/sarvam_stt.py

Real-Time Sarvam AI Streaming Speech-to-Text (STT) Client
=========================================================
Connects to Sarvam's streaming WebSocket API over raw 8kHz PCM audio (upsampled to 16kHz).
Supports client-side amplitude VAD for barge-in detection and streaming transcript updates.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
import urllib.parse
import wave
from typing import Callable, Optional

import aiohttp
import numpy as np

logger = logging.getLogger("asterisk.sarvam_stt")

LANG_CODE_MAP = {
    "ta": "ta-IN",
    "kn": "kn-IN",
    "hi": "hi-IN",
    "te": "te-IN",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "od": "od-IN",
    "en": "en-IN",
    "en-US": "en-IN",
}


def normalize_stt_lang(code: str) -> str:
    cleaned = (code or "ta-IN").strip()
    return LANG_CODE_MAP.get(cleaned, LANG_CODE_MAP.get(cleaned.lower(), cleaned))


class SarvamStreamingSTT:
    """
    Manages a real-time streaming WebSocket connection to Sarvam AI STT (Saaras v3).
    Provides drop-in compatibility with DeepgramStreamingSTT interface.
    """

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 8000,
        model: str = "saaras:v3",
        language: str = "ta-IN",
        endpointing_ms: int = 400,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_speech_final: Optional[Callable[[str], None]] = None,
        on_interim: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.api_key = api_key
        self.sample_rate = sample_rate
        self.model = model if model not in ("nova-3", "nova-2", "default") else "saaras:v3"
        self.language = normalize_stt_lang(language)
        self.endpointing_ms = endpointing_ms

        self.on_speech_started = on_speech_started
        self.on_speech_final = on_speech_final
        self.on_interim = on_interim

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False

        # Audio buffering & VAD
        self._audio_buffer = bytearray()
        self._is_speaking = False
        self._speech_frames_count = 0
        self._silence_frames_count = 0
        self._vad_threshold = 500  # Clean margin above ~350 RMS microphone baseline noise
        self._last_speech_time = 0.0
        self._speech_start_perf: float = 0.0
        self._last_flush_perf: float = 0.0
        self._last_dispatch_perf: float = 0.0
        self.last_stt_latency_ms: float = 0.0

    def _build_ws_url(self) -> str:
        params = {
            "language-code": self.language,
            "model": self.model,
        }
        return f"wss://api.sarvam.ai/speech-to-text/ws?{urllib.parse.urlencode(params)}"

    async def start(self) -> None:
        """Connects to Sarvam WebSocket and starts receiver task."""
        if self._running:
            return

        self._running = True
        ws_url = self._build_ws_url()
        headers = {"api-subscription-key": self.api_key}

        self._session = aiohttp.ClientSession()
        logger.info(f"🎙️ [Sarvam STT] Connecting to streaming WebSocket ({self.language}, model={self.model})...")
        try:
            self._ws = await self._session.ws_connect(ws_url, headers=headers)
            logger.info("✅ [Sarvam STT] WebSocket connected successfully.")
            self._receive_task = asyncio.create_task(self._listen_loop(), name="SarvamSTTListener")
        except Exception as e:
            logger.error(f"❌ [Sarvam STT] Connection error: {e}")
            await self.close()
            raise

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Processes 8kHz PCM audio frame from Asterisk, tracks VAD, and buffers for transcription."""
        if not self._running or self._ws is None or self._ws.closed:
            return

        # Calculate energy (RMS) of 16-bit PCM chunk
        if len(pcm_bytes) >= 2:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) > 0 else 0.0
        else:
            rms = 0.0

        now = time.monotonic()
        now_perf = time.perf_counter()

        if rms > self._vad_threshold:
            self._speech_frames_count += 1
            self._silence_frames_count = 0
            self._last_speech_time = now

            if not self._is_speaking and self._speech_frames_count >= 2:
                self._is_speaking = True
                self._speech_start_perf = now_perf
                logger.debug("🗣️ [Sarvam VAD] Speech started")
                if self.on_speech_started:
                    try:
                        res = self.on_speech_started()
                        if asyncio.iscoroutine(res):
                            asyncio.create_task(res)
                    except Exception as ex:
                        logger.warning(f"Error in on_speech_started callback: {ex}")
        else:
            if self._is_speaking:
                self._silence_frames_count += 1
            else:
                # Maintain a rolling 300ms pre-roll buffer during silence.
                # Crucial fix: prevents accumulating 30s of dead silence during agent greeting/speech
                # which previously caused the caller's first utterance to be cut in half or dropped!
                max_pre_roll = int(self.sample_rate * 2 * 0.3)  # 4800 bytes @ 8kHz 16-bit
                if len(self._audio_buffer) > max_pre_roll:
                    self._audio_buffer = bytearray(self._audio_buffer[-max_pre_roll:])

        # Always append current frame
        self._audio_buffer.extend(pcm_bytes)

        # Flush buffer if:
        # 1. User was speaking and has now paused for >= endpointing_ms, OR
        # 2. Buffer has grown beyond 2.5 seconds (~40,000 bytes @ 8kHz 16-bit)
        silence_duration_ms = self._silence_frames_count * 20  # ~20ms per Asterisk audio frame
        buffer_duration_sec = len(self._audio_buffer) / (self.sample_rate * 2)

        should_flush = (
            (self._is_speaking and silence_duration_ms >= self.endpointing_ms)
            or (buffer_duration_sec >= 2.5 and self._is_speaking)
        )

        if should_flush and len(self._audio_buffer) >= 3200:  # at least 200ms
            buf_to_send = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self._is_speaking = False
            self._speech_frames_count = 0
            self._silence_frames_count = 0
            self._last_flush_perf = now_perf
            asyncio.create_task(self._dispatch_audio_chunk(buf_to_send, dispatch_perf=now_perf))

    async def _dispatch_audio_chunk(self, pcm_8k: bytes, dispatch_perf: float = 0.0) -> None:
        """Upsamples 8kHz linear PCM to 16kHz WAV and sends to Sarvam WebSocket."""
        if not self._ws or self._ws.closed:
            return
        self._last_dispatch_perf = dispatch_perf or time.perf_counter()
        dur_s = len(pcm_8k) / 16000.0
        logger.info(f"🎙️ [Sarvam STT Dispatching] {len(pcm_8k)} bytes ({dur_s:.2f}s audio)...")

        try:
            arr_8k = np.frombuffer(pcm_8k, dtype=np.int16)
            arr_16k = np.repeat(arr_8k, 2)  # High-efficiency 2x upsampling to 16kHz

            bio = io.BytesIO()
            with wave.open(bio, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(arr_16k.tobytes())

            payload = {
                "audio": {
                    "data": base64.b64encode(bio.getvalue()).decode("ascii"),
                    "sample_rate": "16000",
                    "encoding": "audio/wav",
                }
            }
            await self._ws.send_str(json.dumps(payload))
        except Exception as e:
            logger.debug(f"[Sarvam STT] Audio dispatch failed: {e}")

    async def _listen_loop(self) -> None:
        """Receives transcripts from Sarvam AI WebSocket."""
        if self._ws is None:
            return

        try:
            async for msg in self._ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue

                    msg_type = data.get("type")
                    if msg_type == "data":
                        payload = data.get("data", {})
                        transcript = (payload.get("transcript") or "").strip()
                        if transcript:
                            latency_ms = (time.perf_counter() - (self._last_dispatch_perf or time.perf_counter())) * 1000
                            self.last_stt_latency_ms = latency_ms
                            logger.info(f"🎯 [Sarvam STT Final]: '{transcript}' (stt_latency={latency_ms:.1f}ms)")
                            if self.on_speech_final:
                                try:
                                    res = self.on_speech_final(transcript)
                                    if asyncio.iscoroutine(res):
                                        asyncio.create_task(res)
                                except Exception as ex:
                                    logger.error(f"Error in on_speech_final callback: {ex}", exc_info=True)
                    elif msg_type == "error":
                        logger.warning(f"⚠️ [Sarvam STT Server Error]: {data.get('data')}")

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("⚠️ [Sarvam STT] WebSocket connection closed.")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ [Sarvam STT] Listener loop error: {e}")

    async def close(self) -> None:
        """Closes WebSocket and cleans up resources."""
        self._running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        logger.info("🔌 [Sarvam STT] Stream closed cleanly.")
