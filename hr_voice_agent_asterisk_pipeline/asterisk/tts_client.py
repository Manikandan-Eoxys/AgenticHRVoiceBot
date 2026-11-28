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
import base64
import io
import logging
import re
import time
import urllib.parse
import wave
from typing import Optional, AsyncGenerator

import aiohttp

from asterisk.audiosocket import AudioSocketWriterQueue

logger = logging.getLogger("asterisk.tts")

# Sentence / clause splitting: break on punctuation (.!?;:,\n|।) to dispatch speech early
SENTENCE_SPLIT_REGEX = re.compile(r"([.!?;:\n|।]|,\s+)")


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
    t = re.sub(r"[ \t]+", " ", t).strip()

    # Reject text containing only punctuation or whitespace (e.g. ':', '...', '-')
    # Must contain at least one valid word character or Indic/CJK/Latin script character
    if not re.search(r"[\w\u0900-\u0DFF]", t):
        return ""

    return t


def chunk_text_for_sarvam(text: str, max_chars: int = 400) -> list[str]:
    """
    Splits long text into chunks <= max_chars respecting sentence boundaries.
    Sarvam AI Bulbul enforces a strict 500 character limit per input chunk.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    parts = re.split(r"([.!?;।\n]+)", text)
    current = ""
    for part in parts:
        if not part:
            continue
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current.strip():
                chunks.append(current.strip())
            if len(part) > max_chars:
                words = part.split(" ")
                sub = ""
                for w in words:
                    if len(sub) + len(w) + 1 <= max_chars:
                        sub = f"{sub} {w}".strip()
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = w
                current = sub
            else:
                current = part
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c.strip()]


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
        provider: str = "deepgram",
        sarvam_api_key: Optional[str] = None,
        sarvam_speaker: str = "ishita_en_customer",
        sarvam_model: str = "bulbul:v4-flash",
        sarvam_language_code: str = "ta-IN",
        sarvam_pace: Optional[float] = None,
        multi_tts_api_key: Optional[str] = None,
        multi_tts_speaker: Optional[str] = None,
        multi_tts_model: Optional[str] = None,
        multi_tts_language_code: Optional[str] = None,
        multi_tts_pace: Optional[float] = None,
    ) -> None:
        self.api_key = api_key
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.model = model
        self.provider = (provider or "deepgram").lower().strip()
        self.sarvam_api_key = multi_tts_api_key or sarvam_api_key
        self.sarvam_speaker = multi_tts_speaker or sarvam_speaker
        self.sarvam_model = multi_tts_model or sarvam_model
        self.sarvam_language_code = multi_tts_language_code or sarvam_language_code
        pace_val = multi_tts_pace if multi_tts_pace is not None else sarvam_pace
        self.sarvam_pace = pace_val if pace_val is not None else float(os.getenv("MULTI_TTS_PACE", "1.0"))
        self.last_tts_duration_ms: float = 0.0
        self.on_first_audio_chunk: Optional[Callable[[], None]] = None

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
        Delegates to Multilingual (Sarvam AI) or Deepgram based on configured provider.
        """
        cleaned = clean_tts_text(text)
        if not cleaned:
            return

        if self.provider in ("sarvam", "multi", "multilingual") and self.sarvam_api_key:
            await self._speak_sarvam(cleaned)
        else:
            await self._speak_deepgram(cleaned)

    async def _speak_sarvam(self, text: str) -> None:
        """Synthesizes text using Sarvam AI Bulbul TTS into 8000 Hz 16-bit PCM."""
        self._cancelled = False
        session = await self._get_session()
        url = "https://api.sarvam.ai/text-to-speech"
        headers = {
            "api-subscription-key": self.sarvam_api_key or "",
            "Content-Type": "application/json",
        }

        # Sarvam enforces max 500 chars per input item
        chunks = chunk_text_for_sarvam(text, max_chars=400)

        for chunk in chunks:
            if self._cancelled:
                break

            payload = {
                "inputs": [chunk],
                "target_language_code": self.sarvam_language_code,
                "speaker": self.sarvam_speaker,
                "speech_sample_rate": self.sample_rate,
                "model": self.sarvam_model,
                "pace": self.sarvam_pace,
            }

            try:
                t0_tts = time.perf_counter()
                logger.info(f"🔊 [Sarvam TTS Synthesizing ({self.sarvam_language_code}/{self.sarvam_speaker})]: '{chunk[:60]}'...")
                self._current_task = asyncio.current_task()
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        logger.error(f"❌ [Sarvam TTS Error] Status {resp.status}: {body}")
                        continue

                    res_json = await resp.json()
                    audios = res_json.get("audios", [])
                    if not audios:
                        logger.warning("⚠️ [Sarvam TTS] No audio returned.")
                        continue

                    wav_bytes = base64.b64decode(audios[0])
                    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
                        pcm_data = wav_file.readframes(wav_file.getnframes())

                    tts_elapsed_ms = (time.perf_counter() - t0_tts) * 1000
                    self.last_tts_duration_ms = tts_elapsed_ms
                    logger.info(f"🔊 [Sarvam TTS Synthesized in {tts_elapsed_ms:.1f}ms ({self.sarvam_language_code})]: {len(pcm_data)} bytes")

                    if self.on_first_audio_chunk is not None:
                        try:
                            self.on_first_audio_chunk()
                        except Exception:
                            pass

                    # Feed 20ms chunks (320 bytes @ 8kHz 16-bit) to audio queue
                    chunk_size = int(self.sample_rate * 2 * 0.02)  # 320 bytes
                    for i in range(0, len(pcm_data), chunk_size):
                        if self._cancelled:
                            break
                        self.audio_queue.enqueue_pcm(pcm_data[i : i + chunk_size])

                    if not self._cancelled:
                        self.audio_queue.flush()

            except asyncio.CancelledError:
                self._cancelled = True
                break
            except Exception as e:
                logger.error(f"❌ [Sarvam TTS Exception]: {e}")
            finally:
                self._current_task = None

    async def _speak_deepgram(self, cleaned: str) -> None:
        """Synthesizes text using Deepgram Aura API."""
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
            logger.info(f"🔊 [Deepgram TTS Synthesizing]: '{cleaned[:60]}'...")
            self._current_task = asyncio.current_task()
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"❌ [Deepgram TTS Error] Status {resp.status}: {body}")
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
            logger.error(f"❌ [Deepgram TTS Exception]: {e}")
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
