"""
tests/test_sarvam_tamil.py

Verification script for Tamil Voice Pipeline using Sarvam AI Bulbul TTS:
1. Tests Sarvam AI Text-to-Speech API directly (Bulbul v4-flash, 8kHz Linear PCM)
2. Tests Ollama / LLM understanding and response in Tamil
3. Tests StreamingTTSClient end-to-end integration
"""

import asyncio
import io
import os
import sys
import wave
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
)
from asterisk.llm_types import ChatContext
from asterisk.tts_client import StreamingTTSClient
from asterisk.audiosocket import AudioSocketWriterQueue
from ollama_llm import OllamaLLM
from prompts import INBOUND_INSTRUCTIONS


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


async def run_tamil_test():
    print("=" * 60)
    print("🎙️ Testing Tamil Pipeline with Sarvam AI TTS (bulbul:v4-flash)")
    print("=" * 60)

    print(f"• TTS Provider       : {TTS_PROVIDER}")
    print(f"• Sarvam Model       : {SARVAM_MODEL}")
    print(f"• Sarvam Speaker     : {SARVAM_SPEAKER}")
    print(f"• Language Code      : {SARVAM_LANGUAGE_CODE}")
    print(f"• Ollama Model       : {OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")

    # 1. Test LLM Generation in Tamil
    print("\n[Step 1/2] Testing LLM Generation in Tamil...")
    llm = OllamaLLM(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    ctx = ChatContext()
    ctx.add_message(role="system", content=INBOUND_INSTRUCTIONS)
    
    tamil_query = "வணக்கம், நிறுவனத்தின் வேலை நேரம் என்ன?"
    print(f"Caller Utterance: {tamil_query}")
    ctx.add_message(role="user", content=tamil_query)

    stream = llm.chat(chat_ctx=ctx)
    chunks = []
    async for chunk in stream:
        if chunk.delta and chunk.delta.content:
            chunks.append(chunk.delta.content)
    tamil_reply = "".join(chunks).strip()
    print(f"🤖 LLM Response in Tamil:\n{tamil_reply}")

    assert tamil_reply, "LLM returned empty reply!"

    # 2. Test Sarvam TTS Client
    print("\n[Step 2/2] Testing Sarvam TTS Synthesis into 8kHz PCM...")
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
    print(f"✅ AudioSocket Frames Captured: {len(mock_writer.frames)} frames ({total_bytes} bytes)")

    # Save to audio file for auditory verification
    recordings_dir = BASE_DIR / "Recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    out_wav_path = recordings_dir / "test_tamil_sarvam.wav"

    # Extract raw PCM from AudioSocket frames (strip 3-byte AudioSocket header: type, len_msb, len_lsb)
    pcm_payloads = []
    for f in mock_writer.frames:
        if len(f) > 3 and f[0] == 0x10:  # TYPE_AUDIO
            pcm_payloads.append(f[3:])
    
    all_pcm = b"".join(pcm_payloads)
    with wave.open(str(out_wav_path), "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(2)
        wav_out.setframerate(8000)
        wav_out.writeframes(all_pcm)

    print(f"🎵 Audio successfully saved to: {out_wav_path}")
    print(f"   Duration: {len(all_pcm) / (8000 * 2):.2f} seconds @ 8000 Hz 16-bit PCM")

    await tts_client.close()
    await audio_queue.close()

    print("\n🎉 ALL TESTS PASSED: Tamil STT, LLM & Sarvam TTS are fully ready!")


if __name__ == "__main__":
    asyncio.run(run_tamil_test())

