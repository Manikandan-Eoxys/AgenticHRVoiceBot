"""
hr_voice_agent/pipeline/stt_client.py

Real-Time Streaming Speech-to-Text (STT) Client
========================================================
Connects to Deepgram's streaming WebSocket API over raw linear 16-bit PCM.
Supports real-time VAD events (instant barge-in detection), interim results,
per-chunk final callbacks (`on_final`), and end-of-turn utterance callbacks
(`on_speech_final`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Any, Callable, Optional

import aiohttp

logger = logging.getLogger("pipeline.stt")


class StreamingSTTClient:
    """
    Asynchronous streaming STT client for Deepgram Nova-3.

    Supports both:
      - `on_final(transcript, speech_final)`: called on every `is_final` chunk
      - `on_speech_final(full_transcript)`: called once per utterance on `speech_final`
    """

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 8000,
        model: str = "nova-3",
        language: str = "en",
        endpointing_ms: int = 240,
        on_speech_started: Optional[Callable[[], Any]] = None,
        on_interim: Optional[Callable[[str], Any]] = None,
        on_final: Optional[Callable[[str, bool], Any]] = None,
        on_speech_final: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.api_key = api_key
        self.sample_rate = sample_rate
        self.model = model
        self.language = language
        self.endpointing_ms = endpointing_ms

        # Event callbacks
        self.on_speech_started = on_speech_started
        self.on_interim = on_interim
        self.on_final = on_final
        self.on_speech_final = on_speech_final

        self._connector: Optional[aiohttp.TCPConnector] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._connected_event = asyncio.Event()
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

    def _dispatch(self, callback: Optional[Callable[..., Any]], *args: Any, label: str = "") -> None:
        if not callback:
            return
        try:
            res = callback(*args)
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception as exc:
            logger.warning(f"Error in {label or 'STT'} callback: {exc}")

    async def start(self) -> None:
        """Connects to Deepgram streaming WebSocket and starts receiver task."""
        if self._running:
            return

        self._running = True
        ws_url = self._build_ws_url()
        headers = {"Authorization": f"Token {self.api_key}"}

        # Hold an explicit TCPConnector reference so Python 3.12 GC finalizers
        # on prior sessions cannot close a shared connector pool prematurely.
        self._connector = aiohttp.TCPConnector(force_close=True)
        self._session = aiohttp.ClientSession(connector=self._connector)
        logger.info(f"🎙️ [STT] Connecting to streaming API (model={self.model}, rate={self.sample_rate}Hz)...")
        try:
            self._ws = await self._session.ws_connect(ws_url, headers=headers)
            self._connected_event.set()
            logger.info("✅ [STT] WebSocket connected.")
            self._receive_task = asyncio.create_task(self._listen_loop(), name="DeepgramSTTListener")
        except Exception as e:
            logger.error(f"❌ [STT] Connection error: {e}")
            await self.close()
            raise

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Sends a chunk of raw linear 16-bit PCM bytes to Deepgram."""
        if not self._running or self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_bytes(pcm_bytes)
        except Exception as e:
            logger.debug(f"[STT] Audio send failed: {e}")

    async def _listen_loop(self) -> None:
        """Receives and parses transcripts and VAD events from Deepgram."""
        if self._ws is None:
            return

        try:
            async for msg in self._ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        self._handle_message(data)
                    except Exception as parse_err:
                        logger.warning(f"Failed to parse Deepgram message: {parse_err}")

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("⚠️ [STT] WebSocket closed.")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ [STT] Listener loop error: {e}")

    def _handle_message(self, data: dict) -> None:
        msg_type = data.get("type")

        # 1. Server-side VAD event: speech started (instant barge-in)
        if msg_type == "SpeechStarted":
            logger.debug("🗣️ [Deepgram VAD] SpeechStarted detected!")
            self._dispatch(self.on_speech_started, label="on_speech_started")
            return

        # 2. Transcription results (guard non-dict 'channel' in metadata/VAD events)
        channel = data.get("channel", {})
        if not isinstance(channel, dict):
            return
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return

        transcript = (alternatives[0].get("transcript") or "").strip()
        is_final = data.get("is_final", False)
        speech_final = data.get("speech_final", False)

        if transcript:
            if not is_final:
                self._dispatch(self.on_interim, transcript, label="on_interim")
            else:
                self._current_utterance.append(transcript)
                logger.info(f"📝 [STT Final Chunk]: '{transcript}' (speech_final={speech_final})")
                self._dispatch(self.on_final, transcript, speech_final, label="on_final")

        # 3. End-of-turn detection
        if speech_final:
            full_transcript = " ".join(self._current_utterance).strip()
            self._current_utterance.clear()
            if full_transcript:
                logger.info(f"🎯 [STT End-of-Turn]: '{full_transcript}'")
                self._dispatch(self.on_speech_final, full_transcript, label="on_speech_final")

    async def close(self) -> None:
        """Closes WebSocket, session, and connector cleanly."""
        self._running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_bytes(b"")
                await self._ws.close()
            except Exception:
                pass

        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

        if self._connector and not self._connector.closed:
            try:
                await self._connector.close()
            except Exception:
                pass
            self._connector = None
        logger.info("🔌 [STT] Stream closed cleanly.")
