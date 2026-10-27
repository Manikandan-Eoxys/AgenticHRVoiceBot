"""
hr_voice_agent/asterisk/deepgram_stt.py

Real-Time Deepgram Streaming Speech-to-Text (STT) Client
========================================================
Connects to Deepgram's streaming WebSocket API over raw PCM audio.
Supports real-time VAD events (instant barge-in detection) and
endpointing (end-of-turn detection).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Callable, Optional

import aiohttp
import numpy as np

logger = logging.getLogger("asterisk.deepgram_stt")


class DeepgramStreamingSTT:
    """
    Manages a real-time streaming WebSocket connection to Deepgram Nova-3.

    Incoming PCM audio from Asterisk AudioSocket is forwarded over WebSocket.
    Deepgram returns real-time JSON responses with:
      - SpeechStarted: VAD event for instantaneous barge-in
      - is_final: whether this segment is finalized
      - speech_final: whether the speaker finished talking (end-of-turn)
      - channel.alternatives[0].transcript: the recognized text
    """

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 8000,
        model: str = "nova-3",
        language: str = "en",
        endpointing_ms: int = 350,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_speech_final: Optional[Callable[[str], None]] = None,
        on_interim: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.api_key = api_key
        self.sample_rate = sample_rate
        self.model = model
        self.language = language
        self.endpointing_ms = endpointing_ms

        # Event callbacks
        self.on_speech_started = on_speech_started
        self.on_speech_final = on_speech_final
        self.on_interim = on_interim

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._current_utterance: list[str] = []

    def _build_ws_url(self) -> str:
        params = {
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "model": self.model,
            "language": self.language,
            "endpointing": str(self.endpointing_ms),
            "interim_results": "true",
            "smart_format": "true",
            "vad_events": "true",
            "no_delay": "true",
        }
        return f"wss://api.deepgram.com/v1/listen?{urllib.parse.urlencode(params)}"

    async def start(self) -> None:
        """Connects to Deepgram WebSocket and starts receiver task."""
        if self._running:
            return

        self._running = True
        ws_url = self._build_ws_url()
        headers = {
            "Authorization": f"Token {self.api_key}",
        }

        self._session = aiohttp.ClientSession()
        logger.info(f"🎙️ [Deepgram STT] Connecting to streaming API (model={self.model}, rate={self.sample_rate}Hz)...")
        try:
            self._ws = await self._session.ws_connect(ws_url, headers=headers)
            logger.info("✅ [Deepgram STT] WebSocket connected.")
            self._receive_task = asyncio.create_task(self._listen_loop(), name="DeepgramSTTListener")
        except Exception as e:
            logger.error(f"❌ [Deepgram STT] Connection error: {e}")
            await self.close()
            raise

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Sends raw PCM chunk to Deepgram WebSocket."""
        if not self._running or self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_bytes(pcm_bytes)
        except Exception as e:
            logger.debug(f"[Deepgram STT] Audio send failed: {e}")

    async def _listen_loop(self) -> None:
        """Receives and parses transcripts and VAD events from Deepgram."""
        if self._ws is None:
            return

        try:
            async for msg in self._ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    # 1. Instant Barge-In detection via Deepgram VAD event
                    if msg_type == "SpeechStarted":
                        logger.debug("🗣️ [Deepgram VAD] SpeechStarted detected!")
                        if self.on_speech_started:
                            try:
                                res = self.on_speech_started()
                                if asyncio.iscoroutine(res):
                                    asyncio.create_task(res)
                            except Exception as ex:
                                logger.warning(f"Error in on_speech_started callback: {ex}")

                    # 2. Transcription results
                    channel = data.get("channel", {})
                    alternatives = channel.get("alternatives", [])
                    if not alternatives:
                        continue

                    transcript = (alternatives[0].get("transcript") or "").strip()
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)

                    if transcript:
                        if not is_final:
                            if self.on_interim:
                                try:
                                    res = self.on_interim(transcript)
                                    if asyncio.iscoroutine(res):
                                        asyncio.create_task(res)
                                except Exception:
                                    pass
                        else:
                            self._current_utterance.append(transcript)
                            logger.info(f"📝 [STT Final Chunk]: '{transcript}' (speech_final={speech_final})")

                    # 3. End-of-turn detection
                    if speech_final:
                        full_transcript = " ".join(self._current_utterance).strip()
                        self._current_utterance.clear()
                        if full_transcript:
                            logger.info(f"🎯 [STT End-of-Turn]: '{full_transcript}'")
                            if self.on_speech_final:
                                try:
                                    res = self.on_speech_final(full_transcript)
                                    if asyncio.iscoroutine(res):
                                        asyncio.create_task(res)
                                except Exception as ex:
                                    logger.error(f"Error in on_speech_final callback: {ex}", exc_info=True)

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("⚠️ [Deepgram STT] WebSocket closed.")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ [Deepgram STT] Listener loop error: {e}")

    async def close(self) -> None:
        """Closes WebSocket and cleans up resources."""
        self._running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_bytes(b"")  # Deepgram close signal
                await self._ws.close()
            except Exception:
                pass
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        logger.info("🔌 [Deepgram STT] Stream closed cleanly.")
