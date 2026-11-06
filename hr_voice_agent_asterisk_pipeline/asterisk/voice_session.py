"""
hr_voice_agent/asterisk/voice_session.py

Active Call Session Orchestrator for Asterisk Telephony
======================================================
Manages full lifecycle of one live phone call:
  - AudioSocket bidirectional streaming (8kHz PCM)
  - Deepgram streaming STT + VAD barge-in detection
  - Streaming TTS with sentence pipelining
  - VoiceController 12-state FSM transitions & GlobalMonitor telemetry
  - Domain Goal execution (g01-g13), Employee Verification, DTMF fallback
  - External RAG retrieval + latency filler audio
  - Fallback LLM / local Ollama chat stream
  - Local call recording & clean session teardown
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import wave
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

# Asterisk LLM types used by tools and FallbackLLM
from asterisk.llm_types import ChatContext, ChatMessage

from config import (
    DEEPGRAM_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
    RAG_TIMEOUT,
    RAG_FILLER_DELAY,
    RAG_MAX_CONTEXT_CHARS,
    RAG_FILLER_MODE,
    ARI_URL,
)
from prompts import INBOUND_INSTRUCTIONS
DEEPGRAM_LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "en")
COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
from controllers.asteriskVoiceController import AsteriskVoiceController, ControllerState
from controllers.global_monitor_fsm import report_agent_event
from ollama_llm import OllamaLLM, FallbackLLM
from services.rag_retriever_service import (
    RagConfig,
    HttpRetriever,
    validate_rag_url,
    load_knowledge_base_config,
    needs_rag,
    clean_snippets,
    build_context_message,
    RagFiller,
    load_clips,
)
from session.call_session import CallSession
from asterisk.audiosocket import (
    AudioSocketWriterQueue,
    read_packet,
    TYPE_AUDIO,
    TYPE_HANGUP,
    TYPE_UUID,
)
from asterisk.deepgram_stt import DeepgramStreamingSTT
from asterisk.tts_client import StreamingTTSClient
from asterisk.context import (
    AsteriskJobContext,
    AsteriskRunContext,
    PlayoutHandle,
)
from telephony.base import BaseTelephonySession
from security.tool_guardrails import default_tool_guardrail

logger = logging.getLogger("asterisk.voice_session")

# DTMF frequencies for in-band detector
DTMF_FREQS_ROW = [697, 770, 852, 941]
DTMF_FREQS_COL = [1209, 1336, 1477, 1633]
DTMF_GRID = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"],
]


class InBandDtmfDetector8k:
    """Goertzel algorithm DTMF detector tuned for 8kHz telephony PCM."""

    def __init__(self, sample_rate: int = 8000, block_size: int = 160):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.all_freqs = DTMF_FREQS_ROW + DTMF_FREQS_COL
        self.coeffs = {}
        for f in self.all_freqs:
            k = int(0.5 + (block_size * f) / sample_rate)
            omega = (2.0 * np.pi * k) / block_size
            self.coeffs[f] = 2.0 * np.cos(omega)
        self.last_detected: str | None = None
        self.hit_count = 0
        self.silence_count = 0

    def process_block(self, samples: np.ndarray) -> str | None:
        if len(samples) < self.block_size:
            return None
        s = samples.astype(np.float32)
        rms = np.sqrt(np.mean(s**2))

        # Gate 1: RMS floor.  Real DTMF tones are loud full-power sinusoids.
        if rms < 1800:
            self.silence_count += 1
            if self.silence_count >= 2:
                self.last_detected = None
                self.hit_count = 0
            return None
        self.silence_count = 0

        # Compute Goertzel power for each of the 8 DTMF frequencies.
        powers = {}
        for f in self.all_freqs:
            coeff = self.coeffs[f]
            sp, sp2 = 0.0, 0.0
            for val in s:
                sc = val + coeff * sp - sp2
                sp2, sp = sp, sc
            powers[f] = sp2 * sp2 + sp * sp - coeff * sp * sp2

        best_row_f = max(DTMF_FREQS_ROW, key=lambda f: powers[f])
        best_col_f = max(DTMF_FREQS_COL, key=lambda f: powers[f])
        p_row = powers[best_row_f]
        p_col = powers[best_col_f]

        # Gate 2: row/col balance — both tones must be comparably strong.
        if not (0.15 < p_row / (p_col + 1e-6) < 6.5):
            self.hit_count = 0
            return None

        # Gate 3: adjacent-frequency suppression.
        # DTMF tones are nearly pure sinusoids; speech harmonics spread power across
        # neighbouring bins.  Raised from 2.5x → 8x to reject speech false-positives.
        other_row_powers = [powers[f] for f in DTMF_FREQS_ROW if f != best_row_f]
        other_col_powers = [powers[f] for f in DTMF_FREQS_COL if f != best_col_f]
        if other_row_powers and p_row < 8.0 * max(other_row_powers):
            self.hit_count = 0
            return None
        if other_col_powers and p_col < 8.0 * max(other_col_powers):
            self.hit_count = 0
            return None

        # Gate 4: spectral purity.
        # At least 70% of the total power across all 8 DTMF frequencies must be
        # concentrated in exactly the two winning frequencies.  Speech energy is
        # spread across many frequencies so this check reliably rejects it.
        total_power = sum(powers.values()) + 1e-6
        if (p_row + p_col) / total_power < 0.70:
            self.hit_count = 0
            return None

        r_idx = DTMF_FREQS_ROW.index(best_row_f)
        c_idx = DTMF_FREQS_COL.index(best_col_f)
        digit = DTMF_GRID[r_idx][c_idx]

        if digit == self.last_detected:
            return None

        self.hit_count += 1
        # Require 4 consecutive blocks (≈80ms) of the same digit to fire.
        # This prevents speech transients that briefly look like DTMF from registering.
        if self.hit_count >= 4:
            self.last_detected = digit
            self.hit_count = 0
            return digit
        return None


class AsteriskDtmfCollector:
    """Buffers DTMF digits and triggers completion on 4-digit employee IDs."""

    def __init__(self, on_id_complete: Optional[Callable[[str], Any]] = None):
        self.buffer: list[str] = []
        self.on_id_complete = on_id_complete
        self._completion_event = asyncio.Event()
        self._collected_id: Optional[str] = None

    def on_digit(self, digit: str) -> None:
        if digit in "0123456789":
            self.buffer.append(digit)
            logger.info(f"🔢 [DTMF Collector] Digit '{digit}' recorded. Buffer: {''.join(self.buffer)}")
            if len(self.buffer) == 4:
                emp_id = "".join(self.buffer)
                self._collected_id = emp_id
                self._completion_event.set()
                if self.on_id_complete:
                    res = self.on_id_complete(emp_id)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
        elif digit == "*":
            self.clear()
            logger.info("🔢 [DTMF Collector] Buffer cleared via '*'")

    async def wait_for_id(self, timeout: float = 25.0) -> Optional[str]:
        """Awaits collection of 4-digit employee ID from keypad."""
        if len(self.buffer) == 4:
            return "".join(self.buffer)
        self._completion_event.clear()
        try:
            await asyncio.wait_for(self._completion_event.wait(), timeout=timeout)
            return self._collected_id
        except asyncio.TimeoutError:
            return None

    def clear(self) -> None:
        self.buffer.clear()
        self._completion_event.clear()
        self._collected_id = None


class AsteriskVoiceSession(BaseTelephonySession):
    """
    Manages one active telephone call bridged via Asterisk AudioSocket and ARI.
    Inherits from BaseTelephonySession so it satisfies the CallContext.session type.
    """

    def __init__(
        self,
        call_uuid: str,
        channel_id: str,
        caller_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        sample_rate: int = 8000,
        ari_client_session: Optional[Any] = None,
    ) -> None:
        # BaseTelephonySession requires call_id and caller_number in __init__
        super().__init__(call_id=call_uuid, caller_number=caller_id or "unknown")

        self.call_uuid = call_uuid
        self.channel_id = channel_id
        self.caller_id = caller_id or "unknown"
        self.reader = reader
        self.writer = writer
        self.sample_rate = sample_rate
        self.ari_http = ari_client_session

        self.room_name = f"ast-{self.call_uuid[:8]}"
        self.running = True
        self.agent_state = "initializing"

        # Session attributes required by tools & helpers
        self.verified_employee_id: Optional[str] = None
        self.verified_employee_name: Optional[str] = None
        self.coordinator_connected: bool = False
        self.call_session = CallSession(room_name=self.room_name, call_type="inbound")

        # Telephony / Audio queues
        self.audio_queue = AudioSocketWriterQueue(writer, sample_rate=self.sample_rate)
        self._agent_audio_buffer: deque[bytes] = deque(maxlen=500)
        self.audio_queue.on_frame_sent = self._on_agent_frame_sent
        self._speaking_start_time: float = 0.0
        self.tts = StreamingTTSClient(api_key=DEEPGRAM_API_KEY or "", audio_queue=self.audio_queue, sample_rate=self.sample_rate)
        self.stt: Optional[DeepgramStreamingSTT] = None
        self.dtmf_detector = InBandDtmfDetector8k(sample_rate=self.sample_rate)
        self.dtmf_collector = AsteriskDtmfCollector(on_id_complete=self._on_dtmf_id_collected)
        self._turn_lock = asyncio.Lock()
        self._current_turn_task: Optional[asyncio.Task] = None

        # STT debounce: prevent duplicate processing when Deepgram fires
        # multiple speech_final events for the same utterance
        self._last_stt_text: str = ""
        self._last_stt_time: float = 0.0
        self._stt_debounce_window: float = 2.0  # seconds

        # Context compatibility layers
        self.job_ctx = AsteriskJobContext(room_name=self.room_name, caller_id=self.caller_id, on_disconnect=self.hangup)
        self.run_ctx = AsteriskRunContext(session=self)

        # Domain Voice Controller & FSM (Dedicated Asterisk pipeline)
        self.voice_controller = AsteriskVoiceController(
            caller_id=self.caller_id,
            channel_id=self.channel_id,
            call_uuid=self.call_uuid,
            room_name=self.room_name,
            call_type="inbound",
            job_ctx=self.job_ctx,
            collector=self.dtmf_collector,
            call_metadata={"caller_id": self.caller_id, "channel_id": self.channel_id},
        )
        self.all_tools = self.voice_controller.session_all_tools()
        self.active_tools = self.all_tools
        self.verified_employee_id: Optional[str] = None

        # Call-specific isolated context
        try:
            from telephony.context import CallContext
        except ImportError:
            from hr_voice_agent.telephony.context import CallContext
        self.call_context = CallContext(
            call_id=self.call_uuid,
            caller_number=self.caller_id,
            channel_id=self.channel_id,
            room_name=self.room_name,
            session=self,
            voice_controller=self.voice_controller,
        )

        # Fallback LLM / Ollama configuration
        self._setup_llm()

        # Conversation History
        today_str = date.today().strftime("%A, %B %-d, %Y")
        prompt = INBOUND_INSTRUCTIONS.format(today_str=today_str)
        self.chat_ctx = ChatContext()
        self.chat_ctx.add_message(role="system", content=prompt)

        # RAG Pipeline initialization
        self._retriever: Optional[HttpRetriever] = None
        self._rag_filler: Optional[RagFiller] = None
        self._rag_config = RagConfig()
        self._init_rag()

        # Call Audio Recording
        self._recordings_dir = Path(__file__).resolve().parent.parent / "Recordings"
        self._wav_writer: Optional[wave.Wave_write] = None
        self._recording_wav_path: Optional[Path] = None
        self._log_path: Optional[Path] = None
        self._init_recording()

        # Background tasks
        self._stt_send_task: Optional[asyncio.Task] = None
        self._inbound_audio_task: Optional[asyncio.Task] = None

    def _setup_llm(self) -> None:
        """
        Sets up the LLM for Asterisk calls:
        - Primary: OpenRouter (Qwen 2.5 72B) or Groq
        - Fallback: Local Ollama (if cloud provider has a network outage)
        - Zero LiveKit gateway involvement.
        """
        local_ollama = OllamaLLM(
            model=OLLAMA_MODEL.strip(),
            base_url=OLLAMA_BASE_URL,
            think=False,
        )
        
        # 1. OpenRouter (Primary)
        openrouter_key = (OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")).strip()
        openrouter_model = (OPENROUTER_MODEL or os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")).strip()

        if openrouter_key:
            from asterisk.llm_types import OpenAICompatibleLLM
            openrouter_llm = OpenAICompatibleLLM(
                model=openrouter_model,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
            )
            logger.info(f"🚀 [LLM Setup] Primary: OpenRouter ({openrouter_model}) | Fallback: Local Ollama ({OLLAMA_MODEL})")
            self.llm = FallbackLLM(primary_llm=openrouter_llm, fallback_llm=local_ollama)
            return

        # 2. Groq (Secondary)
        groq_key = (GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")).strip()
        groq_model = (GROQ_MODEL or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")).strip()

        if groq_key:
            from asterisk.llm_types import OpenAICompatibleLLM
            groq_llm = OpenAICompatibleLLM(
                model=groq_model,
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
            )
            logger.info(f"🚀 [LLM Setup] Primary: Groq ({groq_model}) | Fallback: Local Ollama ({OLLAMA_MODEL})")
            self.llm = FallbackLLM(primary_llm=groq_llm, fallback_llm=local_ollama)
        else:
            logger.info(f"🚀 [LLM Setup] Cloud LLM key not provided; using Local Ollama ({OLLAMA_MODEL})")
            self.llm = local_ollama

    def _init_rag(self) -> None:
        try:
            self._rag_filler = RagFiller(RAG_FILLER_MODE, load_clips("rag_fillers"))
            kb = load_knowledge_base_config({"caller_id": self.caller_id})
            self._rag_config = RagConfig.from_knowledge_base(kb)
            if self._rag_config.enabled and self._rag_config.url:
                validate_rag_url(self._rag_config.url)
                self._retriever = HttpRetriever(config=self._rag_config, timeout=RAG_TIMEOUT)
                logger.info(f"📚 [RAG] Initialized with endpoint: {self._rag_config.url}")
        except Exception as e:
            logger.warning(f"⚠️ RAG initialization skipped: {e}")
            self._retriever = None

    def _init_recording(self) -> None:
        try:
            today_folder = self._recordings_dir / datetime.now().strftime("%d-%m-%Y")
            time_folder = today_folder / f"User-{self.room_name}-{datetime.now().strftime('%H.%M')}"
            time_folder.mkdir(parents=True, exist_ok=True)
            self._recording_wav_path = time_folder / "Conversation_Audio.wav"
            self._log_path = time_folder / "Conversation_log"
            self._wav_writer = wave.open(str(self._recording_wav_path), "wb")
            self._wav_writer.setnchannels(1)
            self._wav_writer.setsampwidth(2)
            self._wav_writer.setframerate(self.sample_rate)
            logger.info(f"🎙️ [Recording] Local audio recording started at: {self._recording_wav_path}")
        except Exception as e:
            logger.warning(f"Could not initialize local audio recording: {e}")

    def _append_transcript_log(self, text: str) -> None:
        """Appends a turn line to Conversation_log in the recordings folder."""
        if self._log_path:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(text.strip() + "\n")
            except Exception:
                pass

    async def start(self) -> None:
        """Starts call session, audio streams, and greeting."""
        logger.info(f"🚀 [Asterisk Session {self.call_uuid}] Starting active call session for caller {self.caller_id}")

        # Start Asterisk FSM loop and attach telephony session
        self.voice_controller.ensure_fsm_running()
        self.voice_controller.attach_session(self, on_hangup=self.hangup)
        self.voice_controller.on_call_start(
            room_name=self.room_name,
            caller_id=self.caller_id,
            channel_id=self.channel_id,
        )

        # Start AudioSocket paced playback
        self.audio_queue.start()

        # Start Deepgram streaming STT
        self.stt = DeepgramStreamingSTT(
            api_key=DEEPGRAM_API_KEY or "",
            sample_rate=self.sample_rate,
            language=DEEPGRAM_LANGUAGE,
            on_speech_started=self._on_user_speech_started,
            on_interim=self._on_user_interim_transcript,
            on_speech_final=self._on_user_speech_final,
        )
        await self.stt.start()

        # Start background inbound audio loop (Asterisk AudioSocket -> STT + DTMF)
        self._inbound_audio_task = asyncio.create_task(self._inbound_audio_loop(), name="AsteriskInboundAudio")

        # Play initial agent greeting
        asyncio.create_task(self._play_greeting())

    async def _play_greeting(self) -> None:
        """Plays the opening HR assistant greeting."""
        # Wait long enough for the ARI mixing bridge to be fully established
        # before streaming audio — 0.3s was too short; Asterisk would drop frames
        # that arrived before the bridge channels were joined.
        await asyncio.sleep(1.5)
        greeting = (
            "Hello! Welcome to Eoxys HR voice assistant. "
            "Please say or enter your employee ID to get started."
        )
        self.chat_ctx.add_message(role="assistant", content=greeting)
        self._append_transcript_log(f"[{datetime.now().strftime('%H:%M:%S')}] [Assistant]: {greeting}")
        await self.tts.speak_text(greeting)
        await self.tts.wait_for_playout()
        if self.agent_state == "speaking":
            self._update_state("listening")

    def say(self, text: str, allow_interruptions: bool = True) -> PlayoutHandle:
        """Speaks a text announcement into the phone line with playout handle."""
        self._update_state("speaking")

        async def _speak_and_wait():
            await self.tts.speak_text(text)
            await self.tts.wait_for_playout()
            if self.agent_state == "speaking":
                self._update_state("listening")
            return True

        task = asyncio.create_task(_speak_and_wait())
        return PlayoutHandle(wait_fn=lambda: task)

    def interrupt(self) -> None:
        """Barge-in: Immediately stops agent speech and clears audio buffers."""
        if self.agent_state == "speaking":
            logger.info("⚡ [Barge-In] Interrupting agent speech.")
            self.tts.interrupt()
            if self._current_turn_task and not self._current_turn_task.done():
                self._current_turn_task.cancel()
            self._update_state("listening")

    def _on_agent_frame_sent(self, frame: bytes) -> None:
        """Buffers 20ms outbound agent audio frame for synchronized call recording."""
        if hasattr(self, "_agent_audio_buffer"):
            self._agent_audio_buffer.append(frame)

    def _update_state(self, new_state: str) -> None:
        self.agent_state = new_state
        if new_state == "speaking":
            self._speaking_start_time = time.monotonic()
        self.voice_controller.on_agent_substate_changed(new_state)

    # ── BaseTelephonySession abstract method implementations ─────────────────

    async def audio_frames(self):  # type: ignore[override]
        """Yields inbound PCM audio frames from the AudioSocket stream."""
        while self.running:
            try:
                msg_type, payload = await read_packet(self.reader)
                if msg_type == TYPE_HANGUP:
                    break
                if msg_type == TYPE_AUDIO and payload:
                    yield payload
            except Exception:
                break

    async def send_audio(self, pcm: bytes) -> None:
        """Enqueues outbound PCM audio to the Asterisk AudioSocket writer."""
        self.audio_queue.enqueue_pcm(pcm)

    def interrupt_speech(self) -> None:
        """Barge-in: instantly purges the outbound audio queue."""
        self.interrupt()

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """Waits until all queued audio frames have been sent to Asterisk."""
        return await self.tts.wait_for_playout(timeout=timeout)

    async def send_dtmf(self, digits: str) -> bool:
        """Asterisk AudioSocket does not support outbound DTMF — always False."""
        logger.debug(f"send_dtmf({digits!r}) not supported on AudioSocket transport.")
        return False

    async def transfer_call(self, target: str) -> bool:  # type: ignore[override]
        """Transfers the caller channel via ARI redirect (delegates to existing impl)."""
        endpoint = target if "/" in target else f"PJSIP/{target}"
        self.interrupt()
        if not self.ari_http or not self.channel_id:
            return False
        try:
            url = f"{ARI_URL}/channels/{self.channel_id}/redirect"
            async with self.ari_http.post(url, params={"endpoint": endpoint}) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"ARI transfer exception: {e}")
            return False

    def _on_user_speech_started(self) -> None:
        """
        Fired by Deepgram VAD when sound activity is detected.
        We do NOT instantly dump seconds of buffered audio on raw VAD trigger alone
        because speaker echo / acoustic reflection into mic easily trips Deepgram VAD.
        Instead, we note speech activity and only interrupt if:
        1. Caller explicitly speaks confirmed words (_on_user_interim_transcript), or
        2. High audio amplitude occurs consistently.
        """
        if self.agent_state == "speaking":
            elapsed_speaking = time.monotonic() - getattr(self, "_speaking_start_time", 0.0)
            if elapsed_speaking < 1.0:
                logger.debug(f"⚡ [Barge-In] Ignored onset acoustic reflection ({elapsed_speaking:.2f}s)")
                return
            # Mark candidate interruption time; will trigger interrupt if confirmed by words or DTMF
            self._vad_candidate_at = time.monotonic()

    def _on_user_interim_transcript(self, interim_text: str) -> None:
        """
        Fired when Deepgram transcribes interim words while caller is talking.
        This provides verified speech verification for barge-in interruptions.
        """
        words = [w for w in interim_text.strip().split() if w]
        if self.agent_state == "speaking" and words:
            # Genuine words spoken by user while agent is talking -> trigger instant barge-in!
            logger.info(f"⚡ [Barge-In] Caller interrupted with words: '{interim_text}' -> interrupting agent speech.")
            self.interrupt()

    def on_dtmf_digit(self, digit: str) -> None:
        """Called by ARI ChannelDtmfReceived event."""
        logger.info(f"🔢 [ARI DTMF Event] Digit received: {digit}")
        if self.agent_state == "speaking":
            self.interrupt()
        self.dtmf_collector.on_digit(digit)

    async def _on_dtmf_id_collected(self, emp_id: str) -> None:
        """Handles 4 digits collected from phone keypad."""
        logger.info(f"🔐 [DTMF Keypad] 4-digit employee ID entered: {emp_id}")
        self.say(f"Thank you, I received employee ID {emp_id}. Let me verify your details.", allow_interruptions=False)
        # Execute turn with synthesized text
        await self._process_user_turn(f"My employee ID is {emp_id}")

    async def _inbound_audio_loop(self) -> None:
        """Reads PCM audio frames from Asterisk AudioSocket TCP stream."""
        logger.info("🔄 [Asterisk AudioSocket] Inbound audio loop started.")
        while self.running:
            try:
                msg_type, payload = await read_packet(self.reader)

                if msg_type == TYPE_HANGUP:
                    logger.info("📴 [Asterisk AudioSocket] Received HANGUP packet from Asterisk.")
                    await self.hangup()
                    break

                elif msg_type == TYPE_AUDIO and payload:
                    # 1. Forward raw PCM to Deepgram STT
                    if self.stt:
                        await self.stt.send_audio(payload)

                    # 2. In-band Goertzel DTMF detection & audio level diagnostics
                    samples = np.frombuffer(payload, dtype=np.int16)
                    self._audio_frames_received = getattr(self, "_audio_frames_received", 0) + 1

                    # Track last time we saw genuine speech (RMS > 30 ≈ background noise level).
                    # Asterisk's DTX/comfort-noise sends near-zero silence frames interspersed
                    # with voice — sample every 100 frames but only warn after ≥5s of true silence.
                    frame_rms = float(np.sqrt(np.mean(samples.astype(np.float32)**2)))
                    if frame_rms > 30.0:
                        self._last_speech_at = getattr(self, "_last_speech_at", 0.0)
                        import time as _time
                        self._last_speech_at = _time.monotonic()

                    if self._audio_frames_received % 100 == 0:  # Every ~2 seconds
                        max_val = int(np.max(np.abs(samples)))
                        import time as _time
                        silence_for = _time.monotonic() - getattr(self, "_last_speech_at", _time.monotonic())
                        if frame_rms < 10.0 and silence_for >= 5.0:
                            # Only warn if we have been in true silence for ≥5 consecutive seconds
                            logger.warning(
                                f"⚠️ [Caller Audio] No voice detected for {silence_for:.0f}s "
                                f"(RMS={frame_rms:.1f}, Peak={max_val}). "
                                "Verify MicroSIP microphone is not muted."
                            )
                        elif frame_rms >= 30.0:
                            logger.info(f"🎙️ [Caller Audio] Sound detected (RMS={frame_rms:.1f}, Peak={max_val}).")

                    # Gate DTMF in-band detection: skip when agent is speaking to prevent
                    # TTS audio echoing through the microphone from registering false digits.
                    if self.agent_state != "speaking":
                        digit = self.dtmf_detector.process_block(samples)
                        if digit:
                            logger.info(f"🎶 [In-Band DTMF Audio] Detected digit '{digit}' from audio stream")
                            self.on_dtmf_digit(digit)

                    # 3. Write to local WAV recording file (clean multi-track mix)
                    if self._wav_writer:
                        try:
                            if self._agent_audio_buffer:
                                agent_frame = self._agent_audio_buffer.popleft()
                                if len(agent_frame) == len(payload):
                                    # If agent is speaking: write agent speech directly to avoid acoustic echo comb-filtering.
                                    # If caller speaks simultaneously (barge-in overlap, frame_rms > 1200), mix both with headroom.
                                    if self.agent_state == "speaking" and frame_rms <= 1200.0:
                                        self._wav_writer.writeframes(agent_frame)
                                    else:
                                        u = np.frombuffer(payload, dtype=np.int16).astype(np.float32)
                                        a = np.frombuffer(agent_frame, dtype=np.int16).astype(np.float32)
                                        mixed = np.clip(0.7 * u + 0.7 * a, -32768, 32767).astype(np.int16).tobytes()
                                        self._wav_writer.writeframes(mixed)
                                else:
                                    self._wav_writer.writeframes(payload)
                            else:
                                self._wav_writer.writeframes(payload)
                        except Exception:
                            pass

            except asyncio.IncompleteReadError:
                logger.info("🔌 [Asterisk AudioSocket] Socket closed by peer.")
                await self.hangup()
                break
            except Exception as e:
                logger.warning(f"⚠️ [Asterisk AudioSocket] Audio read error: {e}")
                break

    def _on_user_speech_final(self, user_transcript: str) -> None:
        """Fired by Deepgram STT when caller finishes speaking (end-of-turn)."""
        text = user_transcript.strip()
        if not text or not self.running:
            return

        # ── STT Debounce ────────────────────────────────────────────────
        # Deepgram sometimes fires two speech_final events for a single
        # utterance (echo, VAD re-trigger, etc.).  If the identical text
        # arrives within the debounce window, skip the duplicate.
        now = time.time()
        if (
            text == self._last_stt_text
            and (now - self._last_stt_time) < self._stt_debounce_window
        ):
            logger.info(
                f"⏩ [STT Debounce] Skipping duplicate speech_final '{text}' "
                f"({now - self._last_stt_time:.2f}s since last)"
            )
            return
        self._last_stt_text = text
        self._last_stt_time = now

        # Cancel any previous turn task still processing to avoid duplicate overlapping responses
        if self._current_turn_task and not self._current_turn_task.done():
            logger.info(f"⚡ [Turn Supervisor] Cancelling prior turn task for new speech input: '{text}'")
            self._current_turn_task.cancel()

        self._current_turn_task = asyncio.create_task(self._safe_process_user_turn(text))

    async def _safe_process_user_turn(self, user_text: str) -> None:
        async with self._turn_lock:
            try:
                await self._process_user_turn(user_text)
            except asyncio.CancelledError:
                logger.debug(f"Turn '{user_text[:30]}' was cancelled.")
            except Exception as e:
                logger.error(f"❌ Error during conversation turn: {e}", exc_info=True)

    async def _process_user_turn(self, user_text: str) -> None:
        """Orchestrates RAG check, LLM generation, tool execution, and TTS."""
        logger.info(f"🗣️ [Caller Turn]: '{user_text}'")
        self._update_state("thinking")

        # 1. Check RAG Retrieval
        rag_context_msg = await self._handle_rag(user_text)

        # 2. Append User Message
        self.chat_ctx.add_message(role="user", content=user_text)
        self._append_transcript_log(f"[{datetime.now().strftime('%H:%M:%S')}] [User]: {user_text}")
        if rag_context_msg:
            self.chat_ctx.add_message(role="assistant", content=rag_context_msg)

        # 3. Dynamic Goal-Based Tool Filtering (Reduces prompt token count from ~5,500 to ~400)
        turn_tools = self._get_relevant_tools_for_turn(user_text)

        # 4. Call LLM with Pruned Tool Set
        try:
            stream = self.llm.chat(chat_ctx=self.chat_ctx, tools=turn_tools)
            self._update_state("speaking")

            reply_chunks: list[str] = []
            tool_calls_to_run: list[Any] = []

            async def _token_generator():
                async for chunk in stream:
                    if not self.running:
                        break
                    # LiveKit ChatChunk exposes .delta directly (not .choices[0].delta)
                    delta = getattr(chunk, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        reply_chunks.append(content)
                        yield content
                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        tool_calls_to_run.extend(tool_calls)

            # Stream speech directly to Asterisk
            await self.tts.speak_stream(_token_generator())
            await self.tts.wait_for_playout()

            full_reply_text = "".join(reply_chunks).strip()
            # Record assistant message into history
            if full_reply_text:
                self.chat_ctx.add_message(role="assistant", content=full_reply_text)
                self._append_transcript_log(f"[{datetime.now().strftime('%H:%M:%S')}] [Assistant]: {full_reply_text}")

            # 4. Execute any requested Function Tools
            if tool_calls_to_run:
                for tc in tool_calls_to_run:
                    fn_name = ""
                    raw_args: Any = {}

                    # Safely extract function name and arguments regardless of whether
                    # tc is LiveKit FunctionToolCall, OpenAI tool call, or a dictionary.
                    func = getattr(tc, "function", None) if not isinstance(tc, dict) else tc.get("function")
                    if func is not None:
                        if isinstance(func, dict):
                            fn_name = func.get("name", "")
                            raw_args = func.get("arguments", {})
                        else:
                            fn_name = getattr(func, "name", "")
                            raw_args = getattr(func, "arguments", {})
                    elif isinstance(tc, dict):
                        fn_name = tc.get("name", "")
                        raw_args = tc.get("arguments", {})
                    else:
                        fn_name = getattr(tc, "name", "")
                        raw_args = getattr(tc, "arguments", {})

                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args) if raw_args.strip() else {}
                        except Exception as e:
                            logger.warning(f"Failed to parse tool call arguments '{raw_args}': {e}")
                            args = {}
                    elif isinstance(raw_args, dict):
                        args = raw_args
                    else:
                        args = {}

                    logger.info(f"🛠️ [Tool Invoking] {fn_name}({args})")

                    # Premature Hangup Guard: Prevent model from calling end_call when caller just declined a proposal
                    if fn_name == "end_call":
                        # Strip punctuation so "No. Thank you." matches "no thank you"
                        caller_words = re.sub(r'[^\w\s]', '', (user_text or "").lower())
                        explicit_end = any(
                            k in caller_words
                            for k in (
                                "bye", "goodbye", "end call", "hang up", "disconnect",
                                "that's all", "thats all", "no thank you", "no thanks",
                                "nothing else", "i'm done", "im done", "no more questions",
                                "that is all"
                            )
                        )
                        if not explicit_end:
                            logger.info(f"🛡️ [Guardrail] Blocked unprompted end_call (caller said '{user_text}'). Guiding agent to offer further help.")
                            self.chat_ctx.add_message(
                                role="user",
                                content=(
                                    "[System Instruction]: The caller declined an option but did NOT ask to end the call or say goodbye. "
                                    "Do NOT hang up and do NOT say goodbye. Instead say: 'Understood. Is there anything else I can assist you with today?' "
                                    "and wait for the caller's response."
                                ),
                            )
                            followup_stream = self.llm.chat(chat_ctx=self.chat_ctx, tools=[])
                            followup_chunks: list[str] = []
                            in_think_block = False

                            async def _followup_end_guard_gen():
                                nonlocal in_think_block
                                async for chunk in followup_stream:
                                    if not self.running:
                                        break
                                    delta = getattr(chunk, "delta", None)
                                    if delta is None:
                                        continue
                                    c = getattr(delta, "content", None)
                                    if not c:
                                        continue
                                    followup_chunks.append(c)
                                    if "<think>" in c:
                                        in_think_block = True
                                        c = c.split("<think>")[0]
                                    if "</think>" in c:
                                        in_think_block = False
                                        c = c.split("</think>")[-1]
                                    if not in_think_block and c:
                                        if not c.startswith("[System"):
                                            yield c

                            await self.tts.speak_stream(_followup_end_guard_gen())
                            await self.tts.wait_for_playout()
                            followup_reply = "".join(followup_chunks).strip()
                            if followup_reply:
                                self.chat_ctx.add_message(role="assistant", content=followup_reply)
                                self._append_transcript_log(f"[{datetime.now().strftime('%H:%M:%S')}] [Assistant]: {followup_reply}")
                            continue

                    tool_fn = self._find_tool_by_name(fn_name)
                    if tool_fn:
                        try:
                            # 1 & 2 & 4. Execute through ToolExecutionGuardrail (least-privilege, timeout, breaker, sanitization)
                            verified_id = self.verified_employee_id or getattr(self.run_ctx.session, "verified_employee_id", None)
                            exec_result = await default_tool_guardrail.execute_tool(
                                tool_callable=tool_fn,
                                tool_name=fn_name,
                                tool_args=args,
                                caller_employee_id=verified_id,
                                context=self.run_ctx,
                                timeout_seconds=3.5,
                            )

                            # Sync verified employee ID if identity tool just succeeded
                            new_verified = getattr(self.run_ctx.session, "verified_employee_id", None)
                            if new_verified and new_verified != self.verified_employee_id:
                                self.verified_employee_id = new_verified
                                logger.info(f"🔒 [Identity Locked] Session verified for employee {self.verified_employee_id}")

                            if exec_result["success"]:
                                res_content = exec_result["data"]
                                logger.info(f"✅ [Tool Result] {fn_name} -> {str(res_content)[:100]}")
                                # Inject as user role instruction so the LLM immediately speaks the result to caller
                                self.chat_ctx.add_message(
                                    role="user",
                                    content=(
                                        f"[System Note]: Action '{fn_name}' completed. Result: {res_content}. "
                                        "Deliver your concise, natural spoken reply to the caller now without any markdown, asterisks, or internal labels."
                                    ),
                                )
                            else:
                                err_msg = exec_result["message"]
                                logger.warning(f"⚠️ [Tool Blocked/Failed] {fn_name} ({exec_result.get('error_code')}): {err_msg}")
                                self.chat_ctx.add_message(
                                    role="user",
                                    content=(
                                        f"[System Note]: Action '{fn_name}' failed: {err_msg}. "
                                        "Inform the caller calmly in 1-2 spoken sentences."
                                    ),
                                )

                            # Trigger follow-up reply from model explaining result (or error)
                            followup_stream = self.llm.chat(chat_ctx=self.chat_ctx, tools=[])
                            followup_chunks: list[str] = []
                            in_think_block = False

                            async def _followup_gen():
                                nonlocal in_think_block
                                async for chunk in followup_stream:
                                    if not self.running:
                                        break
                                    delta = getattr(chunk, "delta", None)
                                    if delta is None:
                                        continue
                                    c = getattr(delta, "content", None)
                                    if not c:
                                        continue
                                    followup_chunks.append(c)

                                    # Filter out internal <think>...</think> reasoning tags
                                    if "<think>" in c:
                                        in_think_block = True
                                        c = c.split("<think>")[0]
                                    if "</think>" in c:
                                        in_think_block = False
                                        c = c.split("</think>")[-1]

                                    if not in_think_block and c:
                                        # Filter out accidental tool result / system echoes
                                        if not c.startswith("[Tool Result:") and not c.startswith("[System Note"):
                                            yield c

                            await self.tts.speak_stream(_followup_gen())
                            await self.tts.wait_for_playout()

                            followup_reply = "".join(followup_chunks).strip()
                            if followup_reply:
                                self.chat_ctx.add_message(role="assistant", content=followup_reply)
                                self._append_transcript_log(f"[{datetime.now().strftime('%H:%M:%S')}] [Assistant]: {followup_reply}")

                        except Exception as e:
                            logger.error(f"❌ Tool execution failed for {fn_name}: {e}", exc_info=True)
                    else:
                        logger.warning(f"⚠️ Tool '{fn_name}' requested by LLM but not found in active tools.")

        except Exception as exc:
            logger.error(f"❌ Error during conversation turn: {exc}", exc_info=True)

        finally:
            if self.running:
                self._update_state("listening")

    def _get_relevant_tools_for_turn(self, user_text: str) -> list[Callable]:
        """
        Dynamically selects only the relevant tools for this specific conversation turn.
        Prevents exhausting LLM rate limits (e.g. Groq 7,000 ITPM) by pruning
        the prompt tool schema from 44 tools (~5,400 tokens) down to 2-5 tools (~400 tokens).
        """
        # 1. Verification Guard:
        # Check if caller has already confirmed employee identity
        is_verified = bool(self.verified_employee_id)
        if not is_verified:
            msgs = self.chat_ctx.messages() if callable(self.chat_ctx.messages) else self.chat_ctx.messages
            for msg in msgs:
                content = str(getattr(msg, "content", "") or "")
                if "Identity locked to employee" in content or "Your identity is confirmed" in content:
                    is_verified = True
                    break

        # 2. Dynamic Tool Discovery & Pruning via VoiceController
        matched_tools = self.voice_controller.session_relevant_tools(user_text, is_verified=is_verified)
        logger.info(f"🎯 [Dynamic Tools] Intent-routed {len(matched_tools)} tools for turn (verified={is_verified}, saving ~5,000 tokens)")
        return matched_tools

    def _find_tool_by_name(self, name: str) -> Optional[Callable]:
        """Matches tool name to tool in full registry."""
        tools_pool = getattr(self, "all_tools", self.active_tools)
        for tool in tools_pool:
            info = getattr(tool, "info", None)
            tool_name = (
                getattr(info, "name", None)
                or getattr(tool, "__name__", "")
                or getattr(getattr(tool, "_func", None), "__name__", "")
            )
            if tool_name == name:
                return tool
        return None

    async def _handle_rag(self, text: str) -> Optional[str]:
        """Performs RAG search and returns context grounding message if needed."""
        if self._retriever is None or not needs_rag(text):
            return None

        self.voice_controller.rag_status = "retrieving"
        try:
            logger.info(f"📚 [RAG Triggered] Searching Knowledge Base for query: '{text}'")
            search_task = asyncio.create_task(self._retriever.search(text, self._rag_config.top_k))
            done, _ = await asyncio.wait({search_task}, timeout=RAG_FILLER_DELAY)
            filler_handle = None
            if not done:
                # Play latency filler audio clip so user is not left in dead silence
                filler_handle = self.say("Let me check that policy for you real quick...", allow_interruptions=False)

            result = await asyncio.wait_for(asyncio.shield(search_task), timeout=RAG_TIMEOUT)
            if filler_handle:
                try:
                    await filler_handle.wait_for_playout(timeout=3.0)
                except Exception:
                    pass
            snippets = clean_snippets(result, top_k=self._rag_config.top_k, max_chars=RAG_MAX_CONTEXT_CHARS)
            msg = build_context_message(snippets, mode=self._rag_config.grounding_mode)
            if msg:
                logger.info(f"📚 [RAG Grounded] Injected {len(snippets)} knowledge snippet(s) into context")
            return msg
        except Exception as e:
            logger.warning(f"RAG search error: {e}")
            return None
        finally:
            self.voice_controller.rag_status = "idle"

    async def transfer_call(self, target: str) -> bool:
        """Transfers the caller channel to destination endpoint via ARI redirect."""
        endpoint = target if "/" in target else f"PJSIP/{target}"
        logger.info(f"🔀 [Asterisk Session {self.call_uuid}] Transferring channel {self.channel_id} to {endpoint}...")
        self.interrupt()
        if not self.ari_http or not self.channel_id:
            return False
        try:
            url = f"{ARI_URL}/channels/{self.channel_id}/redirect"
            params = {"endpoint": endpoint}
            async with self.ari_http.post(url, params=params) as resp:
                if resp.status in (200, 204):
                    logger.info(f"✅ [Asterisk Session {self.call_uuid}] Redirected channel {self.channel_id} to {endpoint}")
                    return True
                logger.warning(f"ARI redirect returned {resp.status} for endpoint {endpoint}")
                return False
        except Exception as e:
            logger.error(f"Exception during ARI transfer: {e}")
            return False

    async def hangup(self) -> None:
        """Ends the call cleanly across Asterisk ARI and AudioSocket."""
        if not self.running:
            return
        self.running = False
        logger.info(f"📴 [Session {self.call_uuid}] Hanging up call...")

        # Update FSM state to ENDING -> READY
        self.voice_controller.on_call_end(self.room_name)

        # Close STT & TTS
        if self.stt:
            await self.stt.close()
        await self.tts.close()

        # Close AudioSocket writer
        await self.audio_queue.close()

        # Flush any remaining agent speech into recording
        if self._wav_writer and hasattr(self, "_agent_audio_buffer"):
            while self._agent_audio_buffer:
                try:
                    self._wav_writer.writeframes(self._agent_audio_buffer.popleft())
                except Exception:
                    break

        # Close local WAV recording
        if self._wav_writer:
            try:
                self._wav_writer.close()
                self._wav_writer = None
                if self._recording_wav_path:
                    logger.info(f"💾 [Recording] Audio recording saved: {self._recording_wav_path}")
            except Exception:
                pass

        # Close Asterisk ARI channel if active
        if self.ari_http and self.channel_id:
            try:
                del_url = f"{ARI_URL}/channels/{self.channel_id}"
                async with self.ari_http.delete(del_url) as resp:
                    logger.info(f"📴 [ARI] Channel {self.channel_id} hangup requested: {resp.status}")
            except Exception as e:
                logger.debug(f"ARI hangup notice: {e}")

        logger.info(f"✅ [Session {self.call_uuid}] Call session closed cleanly.")
