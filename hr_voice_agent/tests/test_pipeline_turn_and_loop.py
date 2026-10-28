"""
hr_voice_agent/tests/test_pipeline_turn_and_loop.py

Unit tests for Phase 5: TurnManager, StreamingTTS, and ConversationPipeline.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock
import numpy as np
import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from telephony.base import BaseTelephonySession
from pipeline.turn_manager import TurnManager, TurnEvent
from pipeline.tts_stream import StreamingTTS
from pipeline.conversation_loop import ConversationPipeline


class MockTelephonySession(BaseTelephonySession):
    def __init__(self):
        super().__init__(call_id="call_test_1", caller_number="100")
        self.sent_audio = []
        self.interrupted = False
        self._inbound_q = asyncio.Queue()

    async def audio_frames(self):
        while self.is_active:
            f = await self._inbound_q.get()
            if f is None:
                break
            yield f

    def feed_inbound(self, frame: bytes):
        self._inbound_q.put_nowait(frame)

    def close_inbound(self):
        self._inbound_q.put_nowait(None)

    async def send_audio(self, pcm: bytes):
        self.sent_audio.append(pcm)

    def interrupt_speech(self):
        self.interrupted = True

    async def wait_for_playout(self, timeout=None):
        return True

    async def send_dtmf(self, digits: str):
        return True

    async def transfer_call(self, target: str):
        return True

    async def hangup(self):
        self._is_active = False


def test_turn_manager_aggregation_and_completion():
    completed_turns = []
    barge_ins = []

    tm = TurnManager(
        sample_rate=8000,
        frame_duration_ms=20,
        silence_end_ms=60,
        barge_in_ms=40,
        on_barge_in=lambda: barge_ins.append(True),
        on_turn_completed=lambda text: completed_turns.append(text),
    )

    # 1. Partial interim transcripts
    tm.on_stt_transcript("Hello", speech_final=False)
    tm.on_stt_transcript("I want leave", speech_final=False)
    assert len(completed_turns) == 0

    # 2. Final segment triggers completion
    tm.on_stt_transcript("tomorrow please.", speech_final=True)
    assert len(completed_turns) == 1
    assert completed_turns[0] == "Hello I want leave tomorrow please."


def test_turn_manager_barge_in():
    barge_ins = []
    tm = TurnManager(
        sample_rate=8000,
        frame_duration_ms=20,
        barge_in_ms=40,  # 2 frames @ 20ms
        on_barge_in=lambda: barge_ins.append(True),
    )

    # Bot starts speaking
    tm.set_bot_speaking(True)

    # Generate loud speech frame
    t = np.linspace(0, 0.02, 160, endpoint=False)
    voice_frame = (np.sin(2 * np.pi * 300 * t) * 12000).astype(np.int16).tobytes()

    # Frame 1: under threshold
    evt1 = tm.process_audio_frame(voice_frame)
    assert len(barge_ins) == 0

    # Frame 2: meets 40ms threshold -> barge-in!
    evt2 = tm.process_audio_frame(voice_frame)
    assert evt2 == TurnEvent.BARGE_IN
    assert len(barge_ins) == 1


def test_streaming_tts_speak_and_cancel():
    async def _test():
        session = MockTelephonySession()

        # Mock aiohttp session and response
        mock_http = MagicMock()
        mock_http.closed = False
        mock_resp = AsyncMock()
        mock_resp.status = 200

        class FakeContent:
            async def iter_chunked(self, size):
                yield b"pcm_chunk_1"
                yield b"pcm_chunk_2"

        mock_resp.content = FakeContent()
        mock_http.post.return_value.__aenter__.return_value = mock_resp

        tts = StreamingTTS(
            api_key="fake-key",
            session=session,
            sample_rate=8000,
            http_session=mock_http,
        )

        # 1. Speak text
        ok = await tts.speak_text("Hello there!")
        assert ok is True
        assert session.sent_audio == [b"pcm_chunk_1", b"pcm_chunk_2"]

        # 2. Test cancellation
        tts.cancel()
        assert session.interrupted is True

        await tts.close()

    asyncio.run(_test())


def test_conversation_pipeline_turn_and_barge_in():
    async def _test():
        session = MockTelephonySession()
        handled_turns = []

        async def mock_llm_handler(utterance: str):
            handled_turns.append(utterance)
            return "Got your request."

        mock_stt = MagicMock()
        mock_stt.start = AsyncMock(return_value=None)
        mock_stt.close = AsyncMock(return_value=None)
        mock_stt.send_audio = AsyncMock(return_value=None)

        mock_tts = MagicMock()
        mock_tts.speak_text = AsyncMock(return_value=True)
        mock_tts.speak_token_stream = AsyncMock(return_value=True)
        mock_tts.cancel = MagicMock()
        mock_tts.close = AsyncMock(return_value=None)

        pipeline = ConversationPipeline(
            session=session,
            deepgram_api_key="fake-key",
            llm_turn_handler=mock_llm_handler,
            sample_rate=8000,
            stt=mock_stt,
            tts=mock_tts,
        )

        await pipeline.start()

        # Simulate completed user turn from STT
        pipeline.turn_manager.on_stt_transcript("Check my leave balance", speech_final=True)

        # Wait briefly for async LLM task
        await asyncio.sleep(0.05)
        assert len(handled_turns) == 1
        assert handled_turns[0] == "Check my leave balance"
        mock_tts.speak_text.assert_called_once_with("Got your request.")

        # Test barge in
        pipeline.turn_manager.set_bot_speaking(True)
        pipeline._handle_barge_in()
        assert session.interrupted is True
        assert pipeline.turn_manager.is_bot_speaking is False
        mock_tts.cancel.assert_called_once()

        await pipeline.stop()

    asyncio.run(_test())
