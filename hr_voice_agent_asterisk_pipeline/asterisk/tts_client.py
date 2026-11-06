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

# Sentence / clause splitting: break on punctuation (.!?;:,\n) to dispatch speech early
SENTENCE_SPLIT_REGEX = re.compile(r"([.!?;:\n]|,\s+)")


def clean_tts_text(text: str) -> str:
    """
    Strips markdown formatting, asterisks, bullet points, headers, and code tags
    so the speech engine synthesizes natural human sentences instead of reading
    symbols aloud (e.g. preventing 'star star bold star star').
    """
    if not text:
        return ""
    # Strip markdown bold & italic asterisks: **word** -> word, *word* -> word
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"__([^_]+)__", r"\1", t)
    t = re.sub(r"_([^_]+)_", r"\1", t)
    # Strip any remaining lone asterisks
    t = t.replace("*", "")
    # Strip markdown headers (e.g. ### Title -> Title)
    t = re.sub(r"^[#]+\s*", "", t)
    # Strip bullet point markers (- Item or • Item or + Item -> Item)
    t = re.sub(r"^[\-\•\+\>]\s*", "", t)
    # Strip markdown links: [Label](url) -> Label
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Strip backticks
    t = t.replace("`", "")
    # Collapse multiple whitespace
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


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
        cleaned = clean_tts_text(text)
        if not cleaned:
            return

        self._cancelled = False
        session = await self._get_session()
        params = {
            "model": self.model,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "container": "none",
        }
        url = f"https://api.deepgram.com/v1/speak?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": cleaned}

        try:
            logger.info(f"🔊 [TTS Synthesizing]: '{cleaned[:60]}'...")
            self._current_task = asyncio.current_task()
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"❌ [TTS Error] Status {resp.status}: {body}")
                    return

                # Stream audio chunks as they arrive from Deepgram
                is_first = True
                async for chunk in resp.content.iter_chunked(1024):
                    if self._cancelled:
                        break
                    # Defensively strip WAV RIFF container header if present
                    if is_first:
                        is_first = False
                        if chunk.startswith(b"RIFF") and len(chunk) >= 44:
                            chunk = chunk[44:]
                    if chunk:
                        self.audio_queue.enqueue_pcm(chunk)

                if not self._cancelled:
                    self.audio_queue.flush()

        except asyncio.CancelledError:
            self._cancelled = True
        except Exception as e:
            logger.error(f"❌ [TTS Exception]: {e}")
        finally:
            self._current_task = None

    async def speak_stream(self, token_generator: AsyncGenerator[str, None]) -> None:
        """
        Consumes streaming LLM tokens, groups into natural clause chunks,
        and streams synthesized audio in real time.
        """
        self._cancelled = False
        buffer = ""

        async for token in token_generator:
            if self._cancelled:
                break
            buffer += token

            # Split on sentence/clause boundaries (.!?;:\n)
            while True:
                parts = SENTENCE_SPLIT_REGEX.split(buffer, maxsplit=1)
                # If matched, parts is: [text_before, delimiter, text_after]
                if len(parts) >= 3:
                    to_speak = (parts[0] + parts[1]).strip()
                    buffer = parts[2]
                    if to_speak and not self._cancelled:
                        await self.speak_text(to_speak)
                else:
                    break

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
