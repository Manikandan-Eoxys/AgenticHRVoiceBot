"""
hr_voice_agent/pipeline/stt_deepgram.py

Real-Time Deepgram Streaming Speech-to-Text (STT) Client
========================================================
Connects to Deepgram's streaming WebSocket API to process linear 16-bit PCM.
Emits real-time interim results, final speech segments, and server-side VAD events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger("pipeline.stt")


class DeepgramStreamingSTT:
    """
    Asynchronous streaming STT client for Deepgram Nova-3.
    """

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 8000,
        model: str = "nova-3",
        language: str = "en",
        endpointing_ms: int = 400,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_interim: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str, bool], None]] = None,  # (transcript, speech_final)
    ) -> None:
        self.api_key = api_key
        self.sample_rate = sample_rate
        self.model = model
        self.language = language
        self.endpointing_ms = endpointing_ms

        self.on_speech_started = on_speech_started
        self.on_interim = on_interim
        self.on_final = on_final

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._connected_event = asyncio.Event()

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
        """Connects to Deepgram streaming WebSocket."""
        if self._running:
            return

        self._running = True
        ws_url = self._build_ws_url()
        headers = {"Authorization": f"Token {self.api_key}"}

        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(ws_url, headers=headers)
            self._connected_event.set()
            self._receive_task = asyncio.create_task(self._listen_loop(), name="DeepgramListener")
            logger.info(f"🎙️ Deepgram streaming STT connected (model={self.model}, rate={self.sample_rate}Hz)")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Deepgram STT: {e}")
            await self.close()
            raise

    async def send_audio(self, pcm_data: bytes) -> None:
        """Sends a chunk of raw linear 16-bit PCM bytes to Deepgram."""
        if not self._running or self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_bytes(pcm_data)
        except Exception as e:
            logger.warning(f"⚠️ Error sending audio to Deepgram: {e}")

    async def _listen_loop(self) -> None:
        """Receives and processes JSON messages from Deepgram."""
        if not self._ws:
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
                    logger.info("Deepgram WebSocket closed.")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Exception in Deepgram listener loop: {e}")

    def _handle_message(self, data: dict) -> None:
        msg_type = data.get("type")

        # 1. Server-side VAD event: speech started
        if msg_type == "SpeechStarted":
            logger.debug("⚡ [STT] Deepgram SpeechStarted VAD event")
            if self.on_speech_started:
                self.on_speech_started()
            return

        # 2. Transcription results
        if msg_type == "Results" or "channel" in data:
            channel = data.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "").strip()
            if not transcript:
                return

            is_final = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if is_final:
                logger.debug(f"📝 [STT Final]: '{transcript}' (speech_final={speech_final})")
                if self.on_final:
                    self.on_final(transcript, speech_final)
            else:
                if self.on_interim:
                    self.on_interim(transcript)

    async def close(self) -> None:
        """Closes the WebSocket and session cleanly."""
        self._running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            try:
                # Send close stream message if socket is open
                await self._ws.send_json({"type": "CloseStream"})
                await self._ws.close()
            except Exception:
                pass

        if self._session and not self._session.closed:
            await self._session.close()
