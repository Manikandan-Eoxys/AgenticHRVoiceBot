"""
hr_voice_agent/asterisk/tts_client.py

Streaming Text-to-Speech (TTS) Client for Asterisk AudioSocket
=============================================================
Synthesizes speech using Deepgram Aura (or Cartesia) directly into
linear 16-bit PCM @ 8000 Hz, streaming frames into the AudioSocketWriterQueue.
Supports sentence-level pipelining and instant cancellation for barge-in.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import Optional, AsyncGenerator

import aiohttp

from asterisk.audiosocket import AudioSocketWriterQueue

logger = logging.getLogger("asterisk.tts")

# Sentence splitting pattern for natural speech cadence
SENTENCE_SPLIT_REGEX = re.compile(r"([.!?;:\n]+)")


class StreamingTTSClient:
    """
    Synthesizes text into 8000 Hz 16-bit linear PCM and feeds AudioSocketWriterQueue.
    """

    def __init__(
        self,
        api_key: str,
        audio_queue: AudioSocketWriterQueue,
        sample_rate: int = 8000,
        model: str = "aura-asteria-en",
    ) -> None:
        self.api_key = api_key
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.model = model

        self._session: Optional[aiohttp.ClientSession] = None
        self._current_task: Optional[asyncio.Task] = None
        self._cancelled = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def speak_text(self, text: str) -> None:
        """
        Synthesizes a complete text string and feeds audio frames to Asterisk.
        """
        cleaned = text.strip()
        if not cleaned:
            return

        self._cancelled = False
        session = await self._get_session()
        params = {
            "model": self.model,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
        }
        url = f"https://api.deepgram.com/v1/speak?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": cleaned}

        try:
            logger.info(f"🔊 [TTS Synthesizing]: '{cleaned[:60]}'...")
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"❌ [TTS Error] Status {resp.status}: {body}")
                    return

                # Stream audio chunks as they arrive from Deepgram
                async for chunk in resp.content.iter_chunked(1024):
                    if self._cancelled:
                        break
                    self.audio_queue.enqueue_pcm(chunk)

        except asyncio.CancelledError:
            self._cancelled = True
        except Exception as e:
            logger.error(f"❌ [TTS Exception]: {e}")

    async def speak_stream(self, token_generator: AsyncGenerator[str, None]) -> None:
        """
        Consumes streaming LLM tokens, groups into natural sentence chunks,
        and streams synthesized audio in real time.
        """
        self._cancelled = False
        buffer = ""

        async for token in token_generator:
            if self._cancelled:
                break
            buffer += token

            # Split on sentence boundaries
            parts = SENTENCE_SPLIT_REGEX.split(buffer)
            # If we have at least one complete sentence + delimiter
            if len(parts) >= 3:
                # parts: [sentence1, delimiter1, sentence2, delimiter2, ..., remainder]
                to_speak = "".join(parts[:2]).strip()
                buffer = "".join(parts[2:])
                if to_speak:
                    await self.speak_text(to_speak)

        # Synthesize any trailing text in buffer
        if buffer.strip() and not self._cancelled:
            await self.speak_text(buffer.strip())

    def interrupt(self) -> None:
        """
        Barge-in: Cancels ongoing synthesis and purges outbound audio queue immediately.
        """
        self._cancelled = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        self.audio_queue.interrupt()

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """Waits until all queued audio has finished playing to Asterisk."""
        return await self.audio_queue.wait_for_playout(timeout)

    async def close(self) -> None:
        """Cleans up session."""
        self.interrupt()
        if self._session and not self._session.closed:
            await self._session.close()
