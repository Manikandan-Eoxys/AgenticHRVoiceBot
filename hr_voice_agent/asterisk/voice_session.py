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
import time
import wave
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

# LiveKit LLM types used by existing tools and FallbackLLM
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents import inference

from config import (
    DEEPGRAM_API_KEY,
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
    SYSTEM_PROMPT,
    RAG_TIMEOUT,
    RAG_FILLER_DELAY,
    RAG_MAX_CONTEXT_CHARS,
    RAG_FILLER_MODE,
    ARI_URL,
)
DEEPGRAM_LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "en")
COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
from controllers.agentVoiceController import VoiceController, ControllerState
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
        if rms < 350:
            self.silence_count += 1
            if self.silence_count >= 2:
                self.last_detected = None
            return None

        powers = {}
        for f in self.all_freqs:
            coeff = self.coeffs[f]
            s_prev = 0.0
            s_prev2 = 0.0
            for val in s:
                s_curr = val + coeff * s_prev - s_prev2
                s_prev2 = s_prev
                s_prev = s_curr
            powers[f] = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2

        best_row_f = max(DTMF_FREQS_ROW, key=lambda f: powers[f])
        best_col_f = max(DTMF_FREQS_COL, key=lambda f: powers[f])
        p_row = powers[best_row_f]
        p_col = powers[best_col_f]

        if not (0.15 < p_row / (p_col + 1e-6) < 6.5):
            return None

        other_row_powers = [powers[f] for f in DTMF_FREQS_ROW if f != best_row_f]
        other_col_powers = [powers[f] for f in DTMF_FREQS_COL if f != best_col_f]
        if other_row_powers and p_row < 2.5 * max(other_row_powers):
            return None
        if other_col_powers and p_col < 2.5 * max(other_col_powers):
            return None

        r_idx = DTMF_FREQS_ROW.index(best_row_f)
        c_idx = DTMF_FREQS_COL.index(best_col_f)
        digit = DTMF_GRID[r_idx][c_idx]

        if digit == self.last_detected:
            return None

        self.hit_count += 1
        if self.hit_count >= 2:
            self.last_detected = digit
            self.silence_count = 0
            self.hit_count = 0
            return digit
        return None


class AsteriskDtmfCollector:
    """Buffers DTMF digits and triggers completion on 4-digit employee IDs."""

    def __init__(self, on_id_complete: Optional[Callable[[str], Any]] = None):
        self.buffer: list[str] = []
        self.on_id_complete = on_id_complete

    def on_digit(self, digit: str) -> None:
        if digit in "0123456789":
            self.buffer.append(digit)
            logger.info(f"🔢 [DTMF Collector] Digit '{digit}' recorded. Buffer: {''.join(self.buffer)}")
            if len(self.buffer) == 4 and self.on_id_complete:
                emp_id = "".join(self.buffer)
                res = self.on_id_complete(emp_id)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
        elif digit == "*":
            self.buffer.clear()
            logger.info("🔢 [DTMF Collector] Buffer cleared via '*'")


class AsteriskVoiceSession:
    """
    Manages one active telephone call bridged via Asterisk AudioSocket and ARI.
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
        self.tts = StreamingTTSClient(api_key=DEEPGRAM_API_KEY or "", audio_queue=self.audio_queue, sample_rate=self.sample_rate)
        self.stt: Optional[DeepgramStreamingSTT] = None
        self.dtmf_detector = InBandDtmfDetector8k(sample_rate=self.sample_rate)
        self.dtmf_collector = AsteriskDtmfCollector(on_id_complete=self._on_dtmf_id_collected)

        # Context compatibility layers
        self.job_ctx = AsteriskJobContext(room_name=self.room_name, caller_id=self.caller_id, on_disconnect=self.hangup)
        self.run_ctx = AsteriskRunContext(session=self)

        # Domain Voice Controller & FSM
        self.voice_controller = VoiceController(
            call_type="inbound",
            job_ctx=self.job_ctx,
            collector=self.dtmf_collector,
            call_metadata={"caller_id": self.caller_id, "channel_id": self.channel_id},
        )
        self.active_tools = self.voice_controller.session_all_tools()

        # Call-specific isolated context
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
        prompt = SYSTEM_PROMPT.format(today_str=today_str) if "{today_str}" in SYSTEM_PROMPT else SYSTEM_PROMPT
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
        self._init_recording()

        # Background tasks
        self._stt_send_task: Optional[asyncio.Task] = None
        self._inbound_audio_task: Optional[asyncio.Task] = None

    def _setup_llm(self) -> None:
        local_ollama = OllamaLLM(
            model=OLLAMA_MODEL.strip(),
            base_url=OLLAMA_BASE_URL,
            think=False,
        )
        use_cloud = os.getenv("USE_CLOUD_LLM", "false").lower() in ("true", "1")
        llm_model = os.getenv("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it")
        if use_cloud:
            cloud_llm = inference.LLM(model=llm_model)
            self.llm = FallbackLLM(primary_llm=cloud_llm, fallback_llm=local_ollama)
        else:
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
            wav_path = time_folder / "Conversation_Audio.wav"
            self._wav_writer = wave.open(str(wav_path), "wb")
            self._wav_writer.setnchannels(1)
            self._wav_writer.setsampwidth(2)
            self._wav_writer.setframerate(self.sample_rate)
            logger.info(f"🎙️ [Recording] Local audio recording started at: {wav_path}")
        except Exception as e:
            logger.warning(f"Could not initialize local audio recording: {e}")

    async def start(self) -> None:
        """Starts call session, audio streams, and greeting."""
        logger.info(f"🚀 [Asterisk Session {self.call_uuid}] Starting active call session for caller {self.caller_id}")

        # Start FSM loop and report CONNECTED
        self.voice_controller.ensure_fsm_running()
        self.voice_controller.on_call_start(self.room_name)
        self.voice_controller.on_participant_joined(f"sip_{self.caller_id}")

        # Start AudioSocket paced playback
        self.audio_queue.start()

        # Start Deepgram streaming STT
        self.stt = DeepgramStreamingSTT(
            api_key=DEEPGRAM_API_KEY or "",
            sample_rate=self.sample_rate,
            language=DEEPGRAM_LANGUAGE,
            on_speech_started=self._on_user_speech_started,
            on_speech_final=self._on_user_speech_final,
        )
        await self.stt.start()

        # Start background inbound audio loop (Asterisk AudioSocket -> STT + DTMF)
        self._inbound_audio_task = asyncio.create_task(self._inbound_audio_loop(), name="AsteriskInboundAudio")

        # Play initial agent greeting
        asyncio.create_task(self._play_greeting())

    async def _play_greeting(self) -> None:
        """Plays the opening HR assistant greeting."""
        await asyncio.sleep(0.3)
        greeting = (
            "Hello! Thank you for calling HR Support. "
            "To get started, please tell me your 4-digit employee ID or enter it on your phone keypad."
        )
        self.say(greeting, allow_interruptions=True)

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
            self._update_state("listening")

    def _update_state(self, new_state: str) -> None:
        self.agent_state = new_state
        self.voice_controller.on_agent_substate_changed(new_state)

    def _on_user_speech_started(self) -> None:
        """Fired by Deepgram VAD the millisecond the caller starts talking."""
        if self.agent_state == "speaking":
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
                    if self._audio_frames_received % 100 == 0:  # Every ~2 seconds
                        rms = float(np.sqrt(np.mean(samples.astype(np.float32)**2)))
                        max_val = int(np.max(np.abs(samples)))
                        if rms < 10.0:
                            logger.warning(f"⚠️ [Caller Audio] Received pure silence (RMS={rms:.1f}, Peak={max_val}). Verify MicroSIP microphone is not muted and the correct input device is selected.")
                        else:
                            logger.info(f"🎙️ [Caller Audio] Sound detected (RMS={rms:.1f}, Peak={max_val}).")

                    digit = self.dtmf_detector.process_block(samples)
                    if digit:
                        logger.info(f"🎶 [In-Band DTMF Audio] Detected digit '{digit}' from audio stream")
                        self.on_dtmf_digit(digit)

                    # 3. Write to local WAV recording file
                    if self._wav_writer:
                        try:
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
        if not user_transcript.strip() or not self.running:
            return
        asyncio.create_task(self._process_user_turn(user_transcript))

    async def _process_user_turn(self, user_text: str) -> None:
        """Orchestrates RAG check, LLM generation, tool execution, and TTS."""
        logger.info(f"🗣️ [Caller Turn]: '{user_text}'")
        self._update_state("thinking")

        # 1. Check RAG Retrieval
        rag_context_msg = await self._handle_rag(user_text)

        # 2. Append User Message
        self.chat_ctx.add_message(role="user", content=user_text)
        if rag_context_msg:
            self.chat_ctx.add_message(role="assistant", content=rag_context_msg)

        # 3. Call LLM with Tool Calling Support
        try:
            stream = self.llm.chat(chat_ctx=self.chat_ctx, tools=self.active_tools)
            self._update_state("speaking")

            full_reply_text = ""
            tool_calls_to_run: list[Any] = []

            async def _token_generator():
                nonlocal full_reply_text, tool_calls_to_run
                async for chunk in stream:
                    if not self.running:
                        break
                    choices = getattr(chunk, "choices", [])
                    if not choices:
                        continue
                    delta = choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        full_reply_text += content
                        yield content
                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        tool_calls_to_run.extend(tool_calls)

            # Stream speech directly to Asterisk
            await self.tts.speak_stream(_token_generator())
            await self.tts.wait_for_playout()

            # Record assistant message into history
            if full_reply_text:
                self.chat_ctx.add_message(role="assistant", content=full_reply_text)

            # 4. Execute any requested Function Tools
            if tool_calls_to_run:
                for tc in tool_calls_to_run:
                    fn_name = getattr(tc.function, "name", None) or getattr(tc, "name", "")
                    raw_args = getattr(tc.function, "arguments", {}) or getattr(tc, "arguments", {})
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})

                    logger.info(f"🛠️ [Tool Invoking] {fn_name}({args})")
                    tool_fn = self._find_tool_by_name(fn_name)
                    if tool_fn:
                        try:
                            # Pass AsteriskRunContext if accepted
                            import inspect
                            sig = inspect.signature(tool_fn)
                            if "context" in sig.parameters:
                                res = await tool_fn(**args, context=self.run_ctx)
                            else:
                                res = await tool_fn(**args)

                            logger.info(f"✅ [Tool Result] {fn_name} -> {str(res)[:100]}")
                            self.chat_ctx.add_message(role="tool", content=str(res))

                            # Trigger follow-up reply from model explaining result
                            followup_stream = self.llm.chat(chat_ctx=self.chat_ctx, tools=[])
                            async def _followup_gen():
                                async for chunk in followup_stream:
                                    if not self.running:
                                        break
                                    delta = chunk.choices[0].delta
                                    c = getattr(delta, "content", None)
                                    if c:
                                        yield c
                            await self.tts.speak_stream(_followup_gen())
                            await self.tts.wait_for_playout()

                        except Exception as e:
                            logger.error(f"❌ Tool execution failed for {fn_name}: {e}", exc_info=True)

        except Exception as exc:
            logger.error(f"❌ Error during conversation turn: {exc}", exc_info=True)

        finally:
            if self.running and self.agent_state != "speaking":
                self._update_state("listening")

    def _find_tool_by_name(self, name: str) -> Optional[Callable]:
        """Matches tool name to active tool in registry."""
        for tool in self.active_tools:
            info = getattr(tool, "info", None)
            tool_name = getattr(info, "name", None) or getattr(tool, "__name__", "")
            if tool_name == name:
                return tool
        return None

    async def _handle_rag(self, text: str) -> Optional[str]:
        """Performs RAG search and returns context grounding message if needed."""
        if self._retriever is None or not needs_rag(text):
            return None

        self.voice_controller.rag_status = "retrieving"
        try:
            search_task = asyncio.create_task(self._retriever.search(text, self._rag_config.top_k))
            done, _ = await asyncio.wait({search_task}, timeout=RAG_FILLER_DELAY)
            if not done:
                # Play latency filler audio clip
                self.say("Let me check that policy for you real quick...", allow_interruptions=False)

            result = await asyncio.wait_for(asyncio.shield(search_task), timeout=RAG_TIMEOUT)
            snippets = clean_snippets(result, top_k=self._rag_config.top_k, max_chars=RAG_MAX_CONTEXT_CHARS)
            return build_context_message(snippets, mode=self._rag_config.grounding_mode)
        except Exception as e:
            logger.warning(f"RAG search error: {e}")
            return None
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

        # Close local WAV recording
        if self._wav_writer:
            try:
                self._wav_writer.close()
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
