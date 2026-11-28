"""
services/translator.py

Real-Time Asynchronous Translation Service using Sarvam AI Mayura
=================================================================
Enables Cascaded Multilingual Telephony Pipeline (Approach 2):
  1. Kannada Caller Audio -> Kannada STT
  2. Kannada STT Text -> Sarvam Mayura -> English Text
  3. English Text -> Core LLM (Zero Hallucination, Tool-Calling, RAG)
  4. English LLM Reply -> Sarvam Mayura -> Kannada Text
  5. Kannada Text -> Sarvam Bulbul TTS (Native Kannada Speech)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("services.translator")

# Mapping short language codes to Sarvam BCP-47 standard codes
LANG_CODE_MAP = {
    "kn": "kn-IN",
    "ta": "ta-IN",
    "hi": "hi-IN",
    "te": "te-IN",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "od": "od-IN",
    "en": "en-IN",
}


def normalize_lang_code(code: str) -> str:
    """Normalizes 'kn' to 'kn-IN', etc."""
    code_clean = (code or "en-IN").strip()
    return LANG_CODE_MAP.get(code_clean.lower(), code_clean)


class SarvamTranslator:
    """
    High-performance async translator using Sarvam AI Mayura API.
    Reuses an aiohttp.ClientSession for connection pooling (<150ms roundtrip).
    """

    TRANSLATE_URL = "https://api.sarvam.ai/translate"

    def __init__(
        self,
        api_key: Optional[str] = None,
        target_lang: str = "kn-IN",
        source_lang: str = "en-IN",
        enabled: bool = True,
        speaker_gender: str = "Female",
        mode: str = "modern-colloquial",
        numerals_format: str = "international",
        model: str = "mayura:v1",
        timeout_seconds: float = 3.0,
    ) -> None:
        import os
        self.api_key = (
            api_key
            or os.getenv("TRANSLATOR_API_KEY")
            or os.getenv("SARVAM_TRANSLATOR_API_KEY")
            or os.getenv("MULTI_TTS_API_KEY")
            or os.getenv("SARVAM_API_KEY")
        )
        self.target_lang = normalize_lang_code(target_lang)
        self.source_lang = normalize_lang_code(source_lang)
        self.enabled = enabled
        self.speaker_gender = speaker_gender
        self.mode = os.getenv("TRANSLATION_MODE", mode)
        self.numerals_format = os.getenv("TRANSLATION_NUMERALS_FORMAT", numerals_format)
        self.model = os.getenv("TRANSLATOR_MODEL", model)
        self.timeout_seconds = timeout_seconds
        self.last_duration_ms: float = 0.0

        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def is_active(self) -> bool:
        """True if translation is enabled, API key is set, and languages differ."""
        return bool(self.enabled and self.api_key and self.target_lang != self.source_lang)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def translate(
        self,
        text: str,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> str:
        """
        Translates text between source and target language.
        Returns original text immediately if disabled, empty, or languages match.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        src = normalize_lang_code(source_lang or self.source_lang)
        tgt = normalize_lang_code(target_lang or self.target_lang)

        if src == tgt or not self.enabled or not self.api_key:
            return cleaned

        payload = {
            "input": cleaned,
            "source_language_code": src,
            "target_language_code": tgt,
            "speaker_gender": self.speaker_gender,
            "mode": self.mode,
            "model": self.model,
            "numerals_format": self.numerals_format,
        }
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
        }

        t0 = time.time()
        try:
            session = await self._get_session()
            async with session.post(self.TRANSLATE_URL, json=payload, headers=headers) as resp:
                elapsed_ms = (time.time() - t0) * 1000
                self.last_duration_ms = elapsed_ms
                if resp.status == 200:
                    data = await resp.json()
                    translated = (data.get("translated_text") or "").strip()
                    logger.info(
                        f"🌐 [Translate {elapsed_ms:.1f}ms] ({src} -> {tgt}): "
                        f"'{cleaned[:50]}' => '{translated[:50]}'"
                    )
                    return translated or cleaned
                else:
                    err_body = await resp.text()
                    logger.warning(
                        f"⚠️ [Sarvam Translate HTTP {resp.status} in {elapsed_ms:.1f}ms]: {err_body}"
                    )
                    return cleaned
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ [Sarvam Translate Timeout] ({src} -> {tgt}) timed out after {self.timeout_seconds}s")
            return cleaned
        except Exception as e:
            logger.error(f"❌ [Sarvam Translate Error]: {e}")
            return cleaned

    async def to_english(self, user_text: str, source_lang: Optional[str] = None) -> str:
        """Translates non-English caller text (e.g. Kannada) into English for the LLM."""
        src = source_lang or self.target_lang
        return await self.translate(user_text, source_lang=src, target_lang="en-IN")

    async def to_target(self, agent_reply_en: str, target_lang: Optional[str] = None) -> str:
        """Translates English LLM output back to target language (e.g. Kannada) for TTS."""
        tgt = target_lang or self.target_lang
        return await self.translate(agent_reply_en, source_lang="en-IN", target_lang=tgt)

    async def close(self) -> None:
        """Closes client session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

