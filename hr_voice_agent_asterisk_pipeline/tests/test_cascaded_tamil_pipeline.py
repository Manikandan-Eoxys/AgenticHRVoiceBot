"""
tests/test_cascaded_tamil_pipeline.py

Verification test for Approach 2: Cascaded Multilingual Tamil Pipeline
1. Simulates Tamil caller STT: 'வணக்கம், எனது விடுப்பு விவரங்களை கூறவும்.'
2. Translates Tamil -> English via Sarvam Mayura
3. Runs English LLM chat turn with HR prompt and tools
4. Translates English LLM output -> Tamil via Sarvam Mayura
5. Synthesizes Tamil audio via Sarvam Bulbul TTS into 8000 Hz Linear PCM
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import (
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
    SARVAM_API_KEY,
    SARVAM_SPEAKER,
    SARVAM_MODEL,
    SARVAM_LANGUAGE_CODE,
    TTS_PROVIDER,
    PRIMARY_LANGUAGE,
)
from asterisk.llm_types import ChatContext
from asterisk.tts_client import StreamingTTSClient
from asterisk.audiosocket import AudioSocketWriterQueue
from ollama_llm import OllamaLLM
from prompts import INBOUND_INSTRUCTIONS
from services.translator import SarvamTranslator


class MockAudioWriter:
    """Mock writer to capture frames sent by AudioSocketWriterQueue."""
    def __init__(self):
        self.frames = []
        self._closed = False

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    async def wait_closed(self) -> None:
        pass


async def test_cascaded_tamil_pipeline():
    print("=" * 70)
    print("🚀 TESTING APPROACH 2: CASCADED MULTILINGUAL PIPELINE (TAMIL)")
    print("=" * 70)
    print(f"• Active Language     : {PRIMARY_LANGUAGE} ({SARVAM_LANGUAGE_CODE})")
    print(f"• Sarvam Speaker      : {SARVAM_SPEAKER}")
    print(f"• TTS Provider        : {TTS_PROVIDER}")
    print(f"• Sarvam Model        : {SARVAM_MODEL}")

    translator = SarvamTranslator(
        api_key=SARVAM_API_KEY,
        target_lang="ta-IN",
        source_lang="en-IN",
        enabled=True,
    )

    tamil_utterance = "வணக்கம், எனது விடுப்பு விவரங்களை கூறவும்."
    print(f"\n[Step 1] Caller Speaks (Tamil STT): '{tamil_utterance}'")

    # Step 2: TA -> EN Translation
    english_query = await translator.to_english(tamil_utterance)
    print(f"[Step 2] Translated to English for LLM: '{english_query}'")
    assert english_query, "Translation to English failed!"

    # Step 3: English LLM Generation
    print(f"\n[Step 3] Processing in Core English LLM ({OLLAMA_MODEL})...")
    llm = OllamaLLM(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    ctx = ChatContext()
    ctx.add_message(role="system", content=INBOUND_INSTRUCTIONS)
    ctx.add_message(role="user", content=english_query)

    stream = llm.chat(chat_ctx=ctx)
    chunks = []
    async for chunk in stream:
        delta = getattr(chunk, "delta", None)
        if delta and getattr(delta, "content", None):
            chunks.append(delta.content)
    english_reply = "".join(chunks).strip()
    print(f"🤖 LLM English Output:\n'{english_reply}'")
    assert english_reply, "LLM returned empty reply!"

    # Step 4: EN -> TA Translation
    print(f"\n[Step 4] Translating English Reply back to Tamil...")
    tamil_reply = await translator.to_target(english_reply)
    print(f"🌐 Translated Tamil Output for TTS:\n'{tamil_reply}'")
    assert tamil_reply, "Translation to Tamil failed!"

    # Step 5: Sarvam Bulbul TTS Synthesis
    print(f"\n[Step 5] Synthesizing Tamil Speech via Sarvam Bulbul ({SARVAM_SPEAKER})...")
    mock_writer = MockAudioWriter()
    audio_queue = AudioSocketWriterQueue(mock_writer, sample_rate=8000)
    audio_queue.start()

    tts_client = StreamingTTSClient(
        api_key="",
        audio_queue=audio_queue,
        sample_rate=8000,
        provider="sarvam",
        sarvam_api_key=SARVAM_API_KEY,
        sarvam_speaker=SARVAM_SPEAKER,
        sarvam_model=SARVAM_MODEL,
        sarvam_language_code=SARVAM_LANGUAGE_CODE,
    )

    await tts_client.speak_text(tamil_reply)
    await tts_client.wait_for_playout()

    total_bytes = sum(len(f) for f in mock_writer.frames)
    print(f"✅ Generated {len(mock_writer.frames)} audio frames ({total_bytes} bytes @ 8kHz PCM)")
    print("\n🎉 ALL TAMIL PIPELINE STEPS COMPLETED SUCCESSFULLY!")

    await translator.close()
    await tts_client.close()


if __name__ == "__main__":
    asyncio.run(test_cascaded_tamil_pipeline())

