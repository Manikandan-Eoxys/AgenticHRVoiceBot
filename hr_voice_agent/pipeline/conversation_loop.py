"""
hr_voice_agent/pipeline/conversation_loop.py

Call Conversation Pipeline Orchestrator
=======================================
Coordinates bidirectional audio streaming, STT transcription, VAD turn detection,
LLM inference/tool dispatch, streaming TTS playback, and barge-in interruption
for an isolated telephony call session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional

from hr_voice_agent.telephony.base import BaseTelephonySession
from hr_voice_agent.pipeline.stt_deepgram import DeepgramStreamingSTT
from hr_voice_agent.pipeline.turn_manager import TurnManager, TurnEvent
from hr_voice_agent.pipeline.tts_stream import StreamingTTS

logger = logging.getLogger("pipeline.conversation")


class ConversationPipeline:
    """
    Per-call conversation pipeline driving STT -> LLM/FSM -> TTS with barge-in support.
    """

    def __init__(
        self,
        session: BaseTelephonySession,
        deepgram_api_key: str = "",
        llm_turn_handler: Optional[Callable[[str], AsyncIterator[str] | Awaitable[Optional[str]]]] = None,
        sample_rate: int = 8000,
        silence_end_ms: int = 600,
        barge_in_ms: int = 180,
        tts_model: str = "aura-asteria-en",
        stt: Optional[DeepgramStreamingSTT] = None,
        tts: Optional[StreamingTTS] = None,
        turn_manager: Optional[TurnManager] = None,
    ) -> None:
        self.session = session
        self.deepgram_api_key = deepgram_api_key
        self.llm_turn_handler = llm_turn_handler
        self.sample_rate = sample_rate

        # 1. Turn manager with local VAD
        self.turn_manager = turn_manager or TurnManager(
            sample_rate=sample_rate,
            silence_end_ms=silence_end_ms,
            barge_in_ms=barge_in_ms,
            on_barge_in=self._handle_barge_in,
            on_turn_completed=self._handle_completed_turn,
        )

        # 2. Deepgram STT
        self.stt = stt or DeepgramStreamingSTT(
            api_key=deepgram_api_key,
            sample_rate=sample_rate,
            endpointing_ms=silence_end_ms // 2,
            on_speech_started=self._handle_speech_started,
            on_final=self.turn_manager.on_stt_transcript,
        )

        # 3. Streaming TTS
        self.tts = tts or StreamingTTS(
            api_key=deepgram_api_key,
            session=session,
            sample_rate=sample_rate,
            model=tts_model,
        )

        self._inbound_task: Optional[asyncio.Task] = None
        self._llm_tts_task: Optional[asyncio.Task] = None
        self._running = False

    def _handle_speech_started(self) -> None:
        """Called when Deepgram server VAD detects speech."""
        if self.turn_manager.is_bot_speaking:
            logger.info("⚡ [Pipeline] Deepgram speech start triggered barge-in.")
            self._handle_barge_in()

    def _handle_barge_in(self) -> None:
        """Halts TTS and speech output immediately upon user interruption."""
        logger.info(f"⚡ [Call {self.session.call_id}] Barge-in interruption triggered.")
        self.turn_manager.set_bot_speaking(False)
        self.tts.cancel()
        if self._llm_tts_task and not self._llm_tts_task.done():
            self._llm_tts_task.cancel()
        self.session.interrupt_speech()

    def _handle_completed_turn(self, utterance: str) -> None:
        """Dispatches completed user turn to LLM and streams response into TTS."""
        if not self._running or not utterance.strip():
            return

        # Cancel any lingering prior response task
        if self._llm_tts_task and not self._llm_tts_task.done():
            self._llm_tts_task.cancel()

        self._llm_tts_task = asyncio.create_task(
            self._process_turn_and_respond(utterance),
            name=f"LLM_TTS_Turn_{self.session.call_id}",
        )

    async def _process_turn_and_respond(self, utterance: str) -> None:
        """Runs the LLM turn handler and pipes tokens to TTS."""
        try:
            self.turn_manager.set_bot_speaking(True)
            logger.info(f"🤖 [Call {self.session.call_id}] Invoking LLM handler for: \"{utterance}\"")

            result = self.llm_turn_handler(utterance)

            # Support either streaming AsyncIterator[str] or coroutine returning str
            if hasattr(result, "__aiter__"):
                await self.tts.speak_token_stream(result)
            elif asyncio.iscoroutine(result):
                text = await result
                if text:
                    await self.tts.speak_text(text)

            # Wait for queued audio playout to finish
            await self.session.wait_for_playout()

        except asyncio.CancelledError:
            logger.info("Turn response processing cancelled (barge-in or hangup).")
        except Exception as e:
            logger.error(f"❌ Error during turn processing: {e}", exc_info=True)
        finally:
            self.turn_manager.set_bot_speaking(False)

    async def start(self) -> None:
        """Starts STT connection and audio consumption loop."""
        if self._running:
            return
        self._running = True

        # Connect STT WebSocket
        try:
            await self.stt.start()
        except Exception as e:
            logger.warning(f"⚠️ STT start failed (continuing in mock/offline mode): {e}")

        # Start listening to telephony session inbound frames
        self._inbound_task = asyncio.create_task(
            self._audio_inbound_loop(),
            name=f"InboundAudio_{self.session.call_id}",
        )
        logger.info(f"🚀 [Call {self.session.call_id}] Conversation pipeline active.")

    async def _audio_inbound_loop(self) -> None:
        """Consumes 20ms frames from telephony session, feeding VAD and STT."""
        try:
            async for frame in self.session.audio_frames():
                if not self._running:
                    break

                # 1. Feed VAD turn manager
                self.turn_manager.process_audio_frame(frame)

                # 2. Feed Deepgram streaming STT
                await self.stt.send_audio(frame)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Inbound audio loop exception: {e}")
        finally:
            logger.info(f"🛑 [Call {self.session.call_id}] Inbound audio loop ended.")

    async def stop(self) -> None:
        """Gracefully shuts down pipeline."""
        self._running = False
        if self._llm_tts_task and not self._llm_tts_task.done():
            self._llm_tts_task.cancel()
        if self._inbound_task and not self._inbound_task.done():
            self._inbound_task.cancel()

        await self.stt.close()
        await self.tts.close()
        self.turn_manager.flush()
        logger.info(f"✅ [Call {self.session.call_id}] Conversation pipeline stopped.")
