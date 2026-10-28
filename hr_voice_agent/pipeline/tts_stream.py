"""
hr_voice_agent/pipeline/tts_stream.py

Streaming Text-to-Speech (TTS) Synthesizer
==========================================
Synthesizes speech using Deepgram Aura API directly into linear 16-bit 8000 Hz PCM.
Supports sentence-level pipelining of streaming LLM tokens and instant barge-in cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import AsyncIterator, Callable, Optional, Union

import aiohttp

from hr_voice_agent.telephony.base import BaseTelephonySession

logger = logging.getLogger("pipeline.tts")

SENTENCE_SPLIT_REGEX = re.compile(r"([.!?;:\n]+)")


class StreamingTTS:
    """
    Pipelined TTS Client synthesizing text or streaming LLM tokens into 8kHz PCM.
    """

    def __init__(
        self,
        api_key: str,
        session: BaseTelephonySession,
        sample_rate: int = 8000,
        model: str = "aura-asteria-en",
        http_session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.sample_rate = sample_rate
        self.model = model

        self._http_session = http_session
        self._owned_session = http_session is None
        self._cancelled = False
        self._active_task: Optional[asyncio.Task] = None

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or bool(getattr(self._http_session, "closed", False)):
            self._http_session = aiohttp.ClientSession()
            self._owned_session = True
        return self._http_session

    def cancel(self) -> None:
        """Instantly cancels speech synthesis and purges queued audio."""
        self._cancelled = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self.session.interrupt_speech()
        logger.info("⚡ [TTS] Synthesis cancelled.")

    async def speak_text(self, text: str) -> bool:
        """
        Synthesizes a complete text utterance and streams audio into telephony session.
        Returns True if synthesis completed without cancellation.
        """
        cleaned = text.strip()
        if not cleaned:
            return True

        self._cancelled = False
        http = await self._get_http_session()

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
            logger.info(f"🔊 [TTS] Synthesizing: '{cleaned[:60]}'...")
            async with http.post(url, headers=headers, json=payload) as resp:
                if resp.status not in (200, 201):
                    err_msg = await resp.text()
                    logger.error(f"❌ [TTS Error] Status {resp.status}: {err_msg}")
                    return False

                async for chunk in resp.content.iter_chunked(1024):
                    if self._cancelled:
                        return False
                    await self.session.send_audio(chunk)

            return not self._cancelled

        except asyncio.CancelledError:
            self._cancelled = True
            return False
        except Exception as e:
            logger.error(f"❌ [TTS Exception]: {e}")
            return False

    async def speak_token_stream(self, token_stream: AsyncIterator[str]) -> bool:
        """
        Consumes streaming LLM tokens, groups into natural sentences,
        and overlaps synthesis with LLM generation for lowest latency.
        """
        self._cancelled = False
        buffer = ""

        try:
            async for token in token_stream:
                if self._cancelled:
                    return False

                buffer += token
                # Check for sentence end boundary
                match = SENTENCE_SPLIT_REGEX.search(buffer)
                if match:
                    split_idx = match.end()
                    sentence = buffer[:split_idx].strip()
                    buffer = buffer[split_idx:]

                    if sentence and len(sentence) > 3:
                        ok = await self.speak_text(sentence)
                        if not ok or self._cancelled:
                            return False

            # Synthesize any remaining trailing buffer
            trailing = buffer.strip()
            if trailing and not self._cancelled:
                await self.speak_text(trailing)

            return not self._cancelled

        except asyncio.CancelledError:
            self._cancelled = True
            return False

    async def close(self) -> None:
        """Closes HTTP session if owned."""
        self.cancel()
        if self._owned_session and self._http_session and not self._http_session.closed:
            await self._http_session.close()
