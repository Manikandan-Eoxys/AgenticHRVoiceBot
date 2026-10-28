"""
agent.py

HR Voice Agent
LiveKit Agents v1.6.10

Matches the exact conversation flow and tools:
- Identity verification with readback and identity locking
- Full leave management (balance inquiry, policy lookup, two-step availability & submission)
- MySQL balance updates and automated email notification
- Call transfer to coordinator with ringback audio and audio muting
- Keypad DTMF fallback if spoken ID fails
- End-of-call disconnect with hangup tone
"""

import asyncio
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional, cast

import re
import aiohttp
import numpy as np
from dotenv import load_dotenv

from livekit import agents, api, rtc
from livekit.protocol import egress as egress_proto
from livekit.agents import (
    AgentServer, AgentSession, Agent, inference, room_io,
    TurnHandlingOptions, RunContext,
)

# Try importing ai_coustics plugin if installed
try:
    from livekit.plugins import ai_coustics
    HAS_AI_COUSTICS = True
except ImportError:
    HAS_AI_COUSTICS = False

from database.db import run_startup_check
from security.call_blocklist import is_call_blocked
from config import (
    DEEPGRAM_API_KEY, OLLAMA_MODEL, OLLAMA_BASE_URL,
    WORKER_AGENT_NAME,
    RAG_FILLER_MODE, RAG_TIMEOUT, RAG_FILLER_DELAY, RAG_MAX_CONTEXT_CHARS,
)
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
from services.session_recorder import SessionRecorder
from services.monitoring_handler import install_monitoring_handler
from controllers.agentVoiceController import (
    VoiceController,
    ControllerState,
    prewarm_worker_resources,
)
from controllers.global_monitor_fsm import (
    start_global_monitor,
    global_monitor_running,
    report_agent_event,
)
from ollama_llm import OllamaLLM, FallbackLLM
from livekit.plugins.deepgram import STT, TTS as DeepgramTTS

try:
    install_monitoring_handler()
except Exception:
    pass

# Load environment variables
_env_path = Path(__file__).resolve().parent / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent / ".env.local"
load_dotenv(_env_path)

# Ensure prewarm logs (INITIALIZING → PREWARMING → READY) are visible in the terminal during startup.
# MonitoringHandler (installed above) ships logs to HTTP only — not to the terminal.
# A temporary stderr StreamHandler is added here for startup and removed once LiveKit's CLI takes over.

import sys as _sys
_root = logging.getLogger()
_root.setLevel(logging.DEBUG)   # unconditional: root defaults to WARNING, not NOTSET
_has_stream = any(
    isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (_sys.stderr, _sys.stdout)
    for h in _root.handlers
)
_startup_sh: logging.StreamHandler | None = None
if not _has_stream:
    _startup_sh = logging.StreamHandler(_sys.stderr)
    _startup_sh.setLevel(logging.DEBUG)
    _startup_sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s %(name)s - %(message)s"))
    _root.addHandler(_startup_sh)

logger = logging.getLogger("agent")

_hung_up_rooms: set[str] = set()


def _tone_samples(sample_rate: int, freq: float, duration_s: float, amplitude: int = 6000) -> np.ndarray:
    """Generates one pure sine tone as int16 PCM samples with fade in/out."""
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    wave = amplitude * np.sin(2 * np.pi * freq * t)
    fade = min(n // 10, 480)
    if fade > 0:
        ramp = np.linspace(0, 1, fade)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave.astype(np.int16)


async def _play_hangup_tone(job_ctx: agents.JobContext) -> None:
    """Plays a short two-tone 'call ended' beep into the room right before hanging up."""
    try:
        sample_rate = 48000
        source = rtc.AudioSource(sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("hangup-tone", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        publication = await job_ctx.room.local_participant.publish_track(track, options)

        samples = np.concatenate([
            _tone_samples(sample_rate, 480, 0.2),
            np.zeros(int(sample_rate * 0.05), dtype=np.int16),
            _tone_samples(sample_rate, 620, 0.2),
        ])

        frame = rtc.AudioFrame.create(sample_rate, 1, len(samples))
        np.copyto(np.frombuffer(frame.data, dtype=np.int16), samples)
        await source.capture_frame(frame)

        await asyncio.sleep(len(samples) / sample_rate + 0.15)
        await job_ctx.room.local_participant.unpublish_track(publication.sid)
    except Exception:
        logger.exception("Failed to play hangup tone (continuing to hang up anyway).")


async def _ringback_loop(job_ctx: agents.JobContext, stop_event: asyncio.Event) -> None:
    """Plays a repeating ring tone into the room while an outbound call (e.g. to the coordinator) is dialing."""
    publication = None
    try:
        sample_rate = 48000
        source = rtc.AudioSource(sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("ringback-tone", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        publication = await job_ctx.room.local_participant.publish_track(track, options)

        ring_cycle = np.concatenate([
            _tone_samples(sample_rate, 440, 1.0, amplitude=4500),
            np.zeros(int(sample_rate * 3.0), dtype=np.int16),
        ])
        frame = rtc.AudioFrame.create(sample_rate, 1, len(ring_cycle))

        while not stop_event.is_set():
            np.copyto(np.frombuffer(frame.data, dtype=np.int16), ring_cycle)
            await source.capture_frame(frame)
            for _ in range(40):
                if stop_event.is_set():
                    break
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Ringback tone failed (continuing without it).")
    finally:
        if publication is not None:
            try:
                await job_ctx.room.local_participant.unpublish_track(publication.sid)
            except Exception:
                logger.exception("Failed to unpublish ringback track (non-fatal).")


# ── Standard ITU-T Q.23 Dual-Tone Multi-Frequency (DTMF) Frequencies ───────────
# Covers ALL telephone digits 0 through 9, plus *, #, and A-D.
DTMF_FREQUENCIES: dict[str, tuple[float, float]] = {
    "1": (697.0, 1209.0),
    "2": (697.0, 1336.0),
    "3": (697.0, 1477.0),
    "A": (697.0, 1633.0),
    "4": (770.0, 1209.0),
    "5": (770.0, 1336.0),
    "6": (770.0, 1477.0),
    "B": (770.0, 1633.0),
    "7": (852.0, 1209.0),
    "8": (852.0, 1336.0),
    "9": (852.0, 1477.0),
    "C": (852.0, 1633.0),
    "*": (941.0, 1209.0),
    "0": (941.0, 1336.0),
    "#": (941.0, 1477.0),
    "D": (941.0, 1633.0),
}


def _dtmf_samples(sample_rate: int, digit: str, duration_s: float = 0.14, amplitude: int = 5500) -> np.ndarray:
    """Generates authentic ITU-T dual-frequency sine waves for telephone DTMF keys with fade-in/out."""
    freqs = DTMF_FREQUENCIES.get(digit.upper())
    if not freqs:
        return np.zeros(int(sample_rate * duration_s), dtype=np.int16)
    f1, f2 = freqs
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    wave = (amplitude / 2.0) * (np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t))
    fade = min(int(sample_rate * 0.008), n // 4)
    if fade > 0:
        ramp = np.linspace(0, 1, fade)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave.astype(np.int16)


class DtmfPlayer:
    """Publishes a dedicated audio track into the LiveKit room to play authentic
    dual-tone telephone key sounds directly to the caller whenever keys are pressed."""

    def __init__(self, room: rtc.Room, sample_rate: int = 48000):
        self.room = room
        self.sample_rate = sample_rate
        self.source: rtc.AudioSource | None = None
        self.track: rtc.LocalAudioTrack | None = None
        self.publication: rtc.LocalTrackPublication | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            self.source = rtc.AudioSource(self.sample_rate, 1)
            self.track = rtc.LocalAudioTrack.create_audio_track("dtmf-tones", self.source)
            opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            self.publication = await self.room.local_participant.publish_track(self.track, opts)
            self._worker_task = asyncio.create_task(self._play_worker())
            logger.info("🎵 [DTMF Player] Audio track published and ready.")
        except Exception as e:
            logger.warning(f"Failed to publish DTMF audio track: {e}")

    def play_tone(self, digit: str) -> None:
        """Enqueues digit for immediate dual-tone audio playback."""
        if digit.upper() in DTMF_FREQUENCIES:
            self._queue.put_nowait(digit)

    async def _play_worker(self) -> None:
        while True:
            try:
                digit = await self._queue.get()
                if not self.source:
                    continue
                samples = np.concatenate([
                    _dtmf_samples(self.sample_rate, digit, duration_s=0.14, amplitude=5500),
                    np.zeros(int(self.sample_rate * 0.03), dtype=np.int16),
                ])
                frame = rtc.AudioFrame.create(self.sample_rate, 1, len(samples))
                np.copyto(np.frombuffer(frame.data, dtype=np.int16), samples)
                await self.source.capture_frame(frame)
                await asyncio.sleep(len(samples) / self.sample_rate)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"DTMF play worker error: {e}")
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        if self.publication and self.room.local_participant:
            try:
                await self.room.local_participant.unpublish_track(self.publication.sid)
            except Exception:
                pass


DTMF_FREQS_ROW = [697, 770, 852, 941]
DTMF_FREQS_COL = [1209, 1336, 1477, 1633]
DTMF_GRID = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"],
]


class InBandDtmfDetector:
    """Detects in-band telephone DTMF dual-tones from raw 16kHz PCM audio frames using the Goertzel algorithm."""

    def __init__(self, sample_rate: int = 16000, block_size: int = 320):
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
        if rms < 300:
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

        # Twist check: row and col powers must be reasonably balanced (< 8 dB)
        ratio = p_row / (p_col + 1e-6)
        if not (0.15 < ratio < 6.5):
            return None

        # Threshold check against non-selected DTMF frequencies
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
        if self.hit_count >= 2:  # 2 consecutive blocks (~40ms) confirms genuine keypress
            self.last_detected = digit
            self.silence_count = 0
            self.hit_count = 0
            return digit
        return None


async def _listen_inband_dtmf(track: rtc.Track, collector: "DtmfCollector") -> None:
    """Consumes 16kHz PCM audio frames from the caller track and detects in-band DTMF keypresses."""
    detector = InBandDtmfDetector(sample_rate=16000, block_size=320)
    try:
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        async for frame_event in stream:
            frame = frame_event.frame
            samples = np.frombuffer(frame.data, dtype=np.int16)
            for i in range(0, len(samples), 320):
                block = samples[i : i + 320]
                if len(block) == 320:
                    digit = detector.process_block(block)
                    if digit:
                        logger.info(f"🎶 [In-Band DTMF Audio] Detected digit '{digit}' from caller audio track")
                        await collector.on_dtmf(digit)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"In-band DTMF listener ended for track: {e}")


class DtmfCollector:
    """Collects 4-digit employee ID from keypad DTMF tones (0-9, *), data channel, or spoken voice."""

    def __init__(self, player: DtmfPlayer | None = None, session: AgentSession | None = None):
        self.player = player
        self.session = session
        self._buffer: list[str] = []
        self._event = asyncio.Event()
        self._collecting = False
        self.last_captured_id: str | None = None
        self._last_id_time: float = 0.0

    def is_collecting(self) -> bool:
        return self._collecting

    async def on_dtmf(self, digit: str) -> None:
        d = digit.strip().upper()
        if not d:
            return

        # 1. Play authentic telephone dual-tone sound immediately into the room
        if self.player:
            self.player.play_tone(d)

        # 2. Keypad input handling
        if d == "*":
            self._buffer.clear()
            logger.info("🔢 [Keypad DTMF] Star (*) pressed: buffer cleared.")
            return

        if d == "#":
            curr = "".join(self._buffer)
            logger.info(f"🔢 [Keypad DTMF] Pound (#) pressed. Current buffer: '{curr}'")
            if len(self._buffer) >= 4:
                cand = "".join(self._buffer[:4])
                self.last_captured_id = cand
                self._last_id_time = time.monotonic()
                self._event.set()
            return

        if d in "0123456789":
            self._buffer.append(d)
            curr = "".join(self._buffer)
            logger.info(f"🔢 [Keypad DTMF] Received '{d}'. Buffer: {curr} (collecting={self._collecting})")
            if len(self._buffer) >= 4:
                cand = "".join(self._buffer[:4])
                self.last_captured_id = cand
                self._last_id_time = time.monotonic()
                self._event.set()

                # If caller typed 4 digits while NOT in keypad collection tool (e.g. during initial greeting)
                if not self._collecting and self.session and not getattr(self.session, "verified_employee_id", None):
                    self._buffer.clear()
                    logger.info(f"🔢 [Keypad DTMF] Caller typed full ID '{cand}' at prompt. Triggering verification.")
                    self.session.generate_reply(
                        user_input=f"The caller entered employee ID {cand} on their phone keypad."
                    )

    def on_spoken_text(self, text: str) -> None:
        """Captures spoken digits or words when waiting for employee ID."""
        if not text:
            return

        # Check for 4 consecutive digits (e.g. "1002", "1007")
        m = re.search(r"\b(10\d{2}|1\d{3})\b", text)
        if m:
            extracted = m.group(1)
            logger.info(f"🗣️ [Voice Collector] Captured 4-digit ID from speech: '{extracted}'")
            self._buffer = list(extracted)
            self.last_captured_id = extracted
            self._last_id_time = time.monotonic()
            self._event.set()
            return

        # Check spelled out words: "one zero zero seven"
        words = text.lower().replace("-", " ").split()
        word_map = {
            "zero": "0", "oh": "0", "o": "0",
            "one": "1", "won": "1",
            "two": "2", "to": "2", "too": "2",
            "three": "3",
            "four": "4", "for": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8", "ate": "8",
            "nine": "9",
        }
        digits = []
        for w in words:
            if w in word_map:
                digits.append(word_map[w])
            elif w.isdigit():
                digits.extend(list(w))

        if len(digits) >= 4:
            cand = "".join(digits[:4])
            if cand.startswith("1"):
                logger.info(f"🗣️ [Voice Collector] Captured spelled digits: '{cand}'")
                self._buffer = list(cand)
                self.last_captured_id = cand
                self._last_id_time = time.monotonic()
                self._event.set()

    async def wait_for_id(self, timeout: float = 25.0) -> str:
        self._collecting = True
        self._event.clear()

        # If already buffered 4 digits
        if len(self._buffer) >= 4:
            res = "".join(self._buffer[:4])
            self._buffer.clear()
            self._collecting = False
            return res

        # If 4 digits were recently captured within the last 15 seconds
        if self.last_captured_id and (time.monotonic() - self._last_id_time) < 15.0:
            res = self.last_captured_id
            self.last_captured_id = None
            self._collecting = False
            return res

        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            if len(self._buffer) >= 4:
                res = "".join(self._buffer[:4])
                self._buffer.clear()
                return res
            if self.last_captured_id:
                res = self.last_captured_id
                self.last_captured_id = None
                return res
        except asyncio.TimeoutError:
            pass
        finally:
            self._collecting = False

        if len(self._buffer) >= 4:
            res = "".join(self._buffer[:4])
            self._buffer.clear()
            return res

        return ""


async def _hangup(job_ctx: agents.JobContext) -> None:
    """Ends the call by deleting the room."""
    room_name = job_ctx.room.name
    if room_name in _hung_up_rooms:
        return
    _hung_up_rooms.add(room_name)
    await _play_hangup_tone(job_ctx)
    try:
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=room_name)
        )
        logger.info(f"Call ended: room {room_name} deleted.")
    except Exception as e:
        already_gone = isinstance(e, aiohttp.ClientError) or "disconnected" in str(e).lower()
        if already_gone:
            logger.info(f"Room {room_name} already ended (hangup was redundant): {e}")
        else:
            logger.exception(f"FAILED to hang up call for room {room_name}: {e}")


async def _start_call_recording(job_ctx: agents.JobContext) -> str | None:
    """Starts recording the room's audio via LiveKit Egress."""
    if not RECORDINGS_S3_BUCKET:
        logger.info("RECORDINGS_S3_BUCKET not set — skipping call recording.")
        return None
    room_name = job_ctx.room.name
    s3_key = f"recordings/{room_name}-{int(time.time())}.ogg"
    try:
        s3_upload = egress_proto.S3Upload(
            bucket=RECORDINGS_S3_BUCKET,
            region=RECORDINGS_S3_REGION,
            access_key=RECORDINGS_S3_ACCESS_KEY,
            secret=RECORDINGS_S3_SECRET_KEY,
        )
        if RECORDINGS_S3_ENDPOINT:
            s3_upload.endpoint = RECORDINGS_S3_ENDPOINT
            s3_upload.force_path_style = True
        info = await job_ctx.api.egress.start_room_composite_egress(
            egress_proto.RoomCompositeEgressRequest(
                room_name=room_name,
                audio_only=True,
                file_outputs=[
                    egress_proto.EncodedFileOutput(
                        file_type=egress_proto.EncodedFileType.OGG,
                        filepath=s3_key,
                        s3=s3_upload,
                    )
                ],
            )
        )
        logger.info(f"Recording started: egress_id={info.egress_id}, s3_key={s3_key}")
        return info.egress_id
    except Exception:
        logger.exception("Failed to start call recording (continuing without it).")
        return None


async def _stop_call_recording(job_ctx: agents.JobContext, egress_id: str) -> None:
    """Stops an in-progress recording and kicks off download-and-cleanup."""
    try:
        await job_ctx.api.egress.stop_egress(
            egress_proto.StopEgressRequest(egress_id=egress_id)
        )
        logger.info(f"Recording stopped: egress_id={egress_id}")
    except Exception:
        logger.exception(f"Failed to stop egress {egress_id} (may still be running).")

    download_task = asyncio.create_task(_download_and_cleanup_recording(job_ctx, egress_id))

    async def _wait_for_recording_download(*_args) -> None:
        try:
            await download_task
        except Exception:
            logger.exception(f"Recording download task for {egress_id} failed during shutdown wait.")

    job_ctx.add_shutdown_callback(_wait_for_recording_download)


async def _download_and_cleanup_recording(job_ctx: agents.JobContext, egress_id: str) -> None:
    """Polls until egress finishes, downloads from S3 into RECORDINGS_LOCAL_DIR, then deletes S3 copy."""
    try:
        import boto3  # type: ignore
    except ImportError:
        logger.warning("boto3 not installed — skipping recording download.")
        return

    s3_key = None
    for _ in range(60):
        await asyncio.sleep(2.0)
        try:
            result = await job_ctx.api.egress.list_egress(
                egress_proto.ListEgressRequest(egress_id=egress_id)
            )
        except Exception:
            logger.exception(f"Failed to poll egress {egress_id} status.")
            return
        if not result.items:
            continue
        info = result.items[0]
        if info.status == egress_proto.EgressStatus.EGRESS_COMPLETE:
            if info.file_results:
                s3_key = info.file_results[0].filename
            break
        if info.status in (egress_proto.EgressStatus.EGRESS_FAILED, egress_proto.EgressStatus.EGRESS_ABORTED):
            logger.error(f"Egress {egress_id} ended with status {info.status}: {info.error}")
            return

    if not s3_key:
        logger.error(f"Egress {egress_id} never reached COMPLETE within timeout.")
        return

    try:
        os.makedirs(RECORDINGS_LOCAL_DIR, exist_ok=True)
        local_path = os.path.join(RECORDINGS_LOCAL_DIR, os.path.basename(s3_key))
        s3 = boto3.client(
            "s3",
            region_name=RECORDINGS_S3_REGION,
            endpoint_url=RECORDINGS_S3_ENDPOINT or None,
            aws_access_key_id=RECORDINGS_S3_ACCESS_KEY,
            aws_secret_access_key=RECORDINGS_S3_SECRET_KEY,
        )
        await asyncio.to_thread(s3.download_file, RECORDINGS_S3_BUCKET, s3_key, local_path)
        await asyncio.to_thread(s3.delete_object, Bucket=RECORDINGS_S3_BUCKET, Key=s3_key)
        logger.info(f"Recording saved to {local_path} and removed from S3.")
    except Exception:
        logger.exception(f"Failed to download/cleanup recording {s3_key} from S3.")


COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
OUTBOUND_TRUNK_ID = os.environ.get("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_ZoFAnNLxoPce")
SIP_CALLER_ID = os.environ.get("SIP_CALLER_ID", "+918031451001")
COORDINATOR_DIAL_TIMEOUT_SECONDS = 30

RECORDINGS_S3_BUCKET = os.environ.get("RECORDINGS_S3_BUCKET", "")
RECORDINGS_S3_REGION = os.environ.get("RECORDINGS_S3_REGION", "us-east-1")
RECORDINGS_S3_ENDPOINT = os.environ.get("RECORDINGS_S3_ENDPOINT", "")
RECORDINGS_S3_ACCESS_KEY = os.environ.get("RECORDINGS_S3_ACCESS_KEY", "")
RECORDINGS_S3_SECRET_KEY = os.environ.get("RECORDINGS_S3_SECRET_KEY", "")
RECORDINGS_LOCAL_DIR = os.environ.get(
    "RECORDINGS_LOCAL_DIR",
    str(Path(__file__).resolve().parent / "Recordings"),
)


INBOUND_INSTRUCTIONS = """You are a helpful HR voice assistant for company employees.

Today's actual date is {today_str}. Use this as ground truth for every relative date the caller mentions ("today", "tomorrow", "yesterday", "next Friday").

════════════════════════════════════════════════════════════════
⚡ CONVERSATIONAL STYLE & LOW LATENCY
════════════════════════════════════════════════════════════════
- Always keep responses SHORT and CRISP: 1 to 2 sentences maximum for natural phone conversation.
- Never recite long lists of policies, leave types, or explanations unless directly asked.
- Speak warmly and professionally; never output emojis, bullet points, or markdown formatting.

════════════════════════════════════════════════════════════════
🔒 1. IDENTITY VERIFICATION GATE
════════════════════════════════════════════════════════════════
- At the start of the call, greet the caller and ask for their 4-digit employee ID (1001 to 1015).
- When the caller provides an employee ID:
  * You MUST IMMEDIATELY call the tool `get_employee_by_id(employee_id=...)` to look up the caller's actual name in the database.
  * NEVER invent, guess, or hallucinate employee names. You do NOT have names in memory; you must get them from `get_employee_by_id`.
  * Read back the exact name returned by the tool: "This is employee ID [ID], [Name], is that correct?"
- When the caller confirms YES: call `confirm_employee_identity(employee_id=...)` to lock the session immediately before performing any HR action.
- If verification fails (wrong ID, denied name, or unclear speech): say ONCE:
  "Please enter your 4-digit employee ID using your phone keypad or keyboard, or say it one digit at a time."
  and invoke the keypad collection tool in that same turn.

════════════════════════════════════════════════════════════════
🏢 2. COMPANY POLICY GROUND TRUTH (General Knowledge)
════════════════════════════════════════════════════════════════
- Working Hours: 9:00 AM to 6:00 PM (8 work hours + 1 hr lunch). Morning grace period until 9:15 AM. 3 late marks per month incur a half-day deduction.
- Dress Code: Business casual Monday through Thursday, smart casual on Friday.
- Work From Home: Up to 2 days per week or 8 days per month with manager approval (post-probation employees).
- Payroll Schedule: Salary is credited on the last working day of each month. Payslips available on the 1st of every month.

IMPORTANT — Policy Tool Usage:
- When the caller asks about ANY leave policy (Casual Leave, Sick Leave, Maternity, Paternity, Comp Off, Bereavement, Short Leave, LWP/LOP): you MUST call the tool `get_leave_policy(leave_type=...)` to fetch the exact DB answer. NEVER say "no policy available" — the policy data is in the database.
- When the caller asks about attendance policy, WFH policy, payroll policy, or code of conduct details: call `get_hr_policy(topic=...)` to look up the matching policy record.
- When the caller asks about code of conduct, dress code details, anti-harassment, VPN rules: call `get_code_of_conduct_policy(topic=...)`.
- You MUST call the appropriate policy tool before responding — never invent or skip policy answers.

════════════════════════════════════════════════════════════════
🔄 3. WORKFLOW RULES
════════════════════════════════════════════════════════════════
- Applying for Leave: Always check availability first. Read back requested vs available days and ask for confirmation before submitting.
- Sensitive Complaints: Treat grievances and harassment reports with maximum empathy and confidentiality; log a confidential ticket immediately.
- Unresolved Issues: If an issue cannot be resolved by policy rules, create an HR ticket and read the reference number to the caller.
- Coordinator Transfer: If caller asks for a live human coordinator, ask them to confirm "yes, proceed" before transferring.
- Ending the Call: When the caller says "no thank you", "goodbye", "that's all", or clearly indicates they are done:
  1. Say your brief polite goodbye (e.g. "You're welcome! Have a great day. Goodbye.").
  2. Then call the tool end_call() — the tool will wait for your goodbye to finish playing and then allow a short pause before disconnecting, in case the caller adds a final word.
  Never hang up mid-sentence or before the caller has had a chance to respond.
"""


OUTBOUND_LEAVE_VERIFICATION_INSTRUCTIONS = """You are an HR voice assistant making an OUTBOUND call to {employee_name} (Employee ID: {employee_id}).

Today's actual date is {today_str}.

════════════════════════════════════════════════════════════════
⚡ VOICE STYLE & CONVERSATIONAL RULES
════════════════════════════════════════════════════════════════
- Keep answers SHORT and NATURAL: 1 to 2 sentences maximum.
- Speak warmly and professionally; never output emojis, asterisks, or markdown formatting.

════════════════════════════════════════════════════════════════
🔒 1. IDENTITY VERIFICATION GATE
════════════════════════════════════════════════════════════════
- Greet {employee_name} and ask them to confirm identity: "Hello, is this {employee_name}? I'm calling from HR regarding your leave request."
- If YES: lock the session with employee ID "{employee_id}" immediately before calling any leave tools.
- If NO (wrong person reached): apologize, do not disclose any personal details, and end the call immediately.

════════════════════════════════════════════════════════════════
📋 2. LEAVE VERIFICATION WORKFLOW
════════════════════════════════════════════════════════════════
- Once identity is confirmed, check pending leave requests for employee "{employee_id}".
- State the pending dates clearly and ask: "Could you share a brief reason for this leave to help your manager review it?"
- Record the reason given (or empty if declined). Remind them this records the note for HR/manager review and does not auto-approve the leave.
- If no pending leave is found: inform them politely, apologize for the confusion, and end the call.

════════════════════════════════════════════════════════════════
📞 3. COORDINATOR TRANSFER & ENDING
════════════════════════════════════════════════════════════════
- If they ask for a live coordinator: ask for confirmation ("yes, proceed") before transferring.
- Ending the Call: Say your brief polite goodbye, then call the tool end_call() to disconnect. The tool waits for TTS to finish and gives a short grace window before hanging up.
"""


OUTBOUND_REMINDER_INSTRUCTIONS = """You are an HR voice assistant making an OUTBOUND reminder or notification call to {employee_name} (Employee ID: {employee_id}).

Today's actual date is {today_str}.

════════════════════════════════════════════════════════════════
⚡ VOICE STYLE & CONVERSATIONAL RULES
════════════════════════════════════════════════════════════════
- Keep answers SHORT and CRISP: 1 to 2 sentences maximum.
- Speak warmly and professionally; never output emojis, bullet points, or markdown formatting.

════════════════════════════════════════════════════════════════
🔒 1. IDENTITY VERIFICATION GATE
════════════════════════════════════════════════════════════════
- Greet {employee_name} and verify identity: "Hello {employee_name}, I'm calling from HR with a scheduled notification."
- Once confirmed, lock the session to employee ID "{employee_id}".
- If wrong person reached: apologize politely and end the call immediately.

════════════════════════════════════════════════════════════════
📢 2. NOTIFICATION OBJECTIVE
════════════════════════════════════════════════════════════════
Reason for call:
{reminder_prompt}

- Deliver the notification clearly and ask for the employee's acknowledgement or status.
- Once the employee confirms (e.g. acknowledged policy, submitted form, downloaded payslip), record their acknowledgement and thank them.

════════════════════════════════════════════════════════════════
📞 3. COORDINATOR & ENDING
════════════════════════════════════════════════════════════════
- If they request a human coordinator: ask for confirmation ("yes, proceed") before transferring.
- Ending the Call: When acknowledgement is recorded and the employee has no further questions, say a brief polite goodbye, then call the tool end_call() to disconnect. The tool waits for TTS to finish and allows a short pause before hanging up.
"""


class Assistant(Agent):
    def __init__(
        self,
        job_ctx: agents.JobContext,
        call_metadata: dict | None = None,              #It expects either a Python dictionary or None.  
        collector: DtmfCollector | None = None,
    ) -> None:
        self._job_ctx = job_ctx
        self.collector = collector
        today_str = date.today().strftime("%A, %B %-d, %Y")
        call_metadata = call_metadata or {}             #if call_metadata was passed as None (or empty), replace it with an empty dictionary {}.
        call_type = call_metadata.get("call_type")

        # RAG Pipeline initialization (external endpoint only)
        self._rag_config: RagConfig = RagConfig()
        self._retriever: Optional[HttpRetriever] = None
        self._rag_filler: Optional[RagFiller] = None
        try:
            self._rag_filler = RagFiller(RAG_FILLER_MODE, load_clips("rag_fillers"))
            kb = load_knowledge_base_config(call_metadata)
            self._rag_config = RagConfig.from_knowledge_base(kb)

            if self._rag_config.enabled and self._rag_config.url:
                validate_rag_url(self._rag_config.url)   # reject unsafe URLs up front
                self._retriever = HttpRetriever(config=self._rag_config, timeout=RAG_TIMEOUT)
        except Exception as exc:
            logger.warning("Failed to initialize RAG pipeline: %s", type(exc).__name__)
            self._retriever = None

        if call_type == "leave_verification":
            instructions = OUTBOUND_LEAVE_VERIFICATION_INSTRUCTIONS.format(
                today_str=today_str,
                employee_name=call_metadata.get("employee_name", "there"),
                employee_id=call_metadata.get("employee_id", ""),
            )
        elif call_type in ("scheduled_reminder", "policy_acknowledgement"):
            instructions = OUTBOUND_REMINDER_INSTRUCTIONS.format(
                today_str=today_str,
                employee_name=call_metadata.get("employee_name", "there"),
                employee_id=call_metadata.get("employee_id", ""),
                reminder_prompt=call_metadata.get("reminder_prompt", "Deliver the scheduled HR notification and record employee acknowledgement."),
            )
        else:
            instructions = INBOUND_INSTRUCTIONS.format(today_str=today_str)

        # Fresh VoiceController instance per call session
        self.voice_controller = VoiceController(
            call_type=call_type or "inbound",
            job_ctx=job_ctx,
            collector=collector,
            call_metadata=call_metadata,
        )
        self.tools_controller = self.voice_controller.tools_controller
        active_tools = self.voice_controller.session_all_tools()

        super().__init__(
            instructions=instructions,
            tools=cast(list[Any], active_tools),         # ← All @function_tool methods from ALL GoalServices
        )

    async def end_call(self, context: RunContext) -> None:
        """Delegate end_call to CoordinatorTransferService."""
        if self.voice_controller.coordinator_service:
            await self.voice_controller.coordinator_service.end_call(context)
        else:
            await context.wait_for_playout()
            await _hangup(self._job_ctx)

    async def collect_employee_id_via_keypad(self, context: RunContext) -> str:
        """Delegate keypad DTMF collection to IdentityVerificationService."""
        if self.voice_controller.identity_service:
            return await self.voice_controller.identity_service.collect_employee_id_via_keypad(context)
        return "Keypad collector not initialized."

    async def transfer_to_coordinator(self, context: RunContext) -> str:
        """Delegate human transfer to CoordinatorTransferService."""
        if self.voice_controller.coordinator_service:
            return await self.voice_controller.coordinator_service.transfer_to_coordinator(context)
        return "Coordinator transfer unavailable."

    # This method is automatically called by the framework immediately after the user finishes speaking (end of their turn).
    # It serves as the primary hook to trigger the RAG pipeline.
    async def on_user_turn_completed(
        self, turn_ctx: Any, new_message: Any
    ) -> None:
        """
        RAG retrieval hook triggered when user finishes speaking.

        Inspects utterance via needs_rag. If RAG is required:
          a. Returns if retriever is None or needs_rag(text) is False.
          b. Sets voice_controller.rag_status = 'retrieving' (resets to 'idle' in finally).
          c. Starts search as an asyncio task: search(text, top_k).
          d. Waits with asyncio.wait for RAG_FILLER_DELAY. If not done, plays filler.
          e. Awaits search up to remaining RAG_TIMEOUT. On error/timeout, cancels task and uses [].
          f. Cleans snippets and builds grounding context message.
          g. Adds message to turn_ctx if not None.
          h. Logs latency, snippet count, filler status, and error type.
        """
        # Safely extracts the transcribed text string from LiveKit's message payload.
        raw_text = getattr(new_message, "text_content", None)
        if not raw_text:
            content = getattr(new_message, "content", "")
            raw_text = " ".join(content) if isinstance(content, list) else str(content or "")
        text = (raw_text or "").strip()
        
        if self._retriever is None or not needs_rag(text):
            return
        
        # Resets the filler state so one latency filler is allowed to play on this turn if retrieval takes too long.
        if self._rag_filler is not None:
            self._rag_filler.reset_turn()

        self.voice_controller.rag_status = "retrieving"
        start_time = time.monotonic()
        filler_played = False
        error_type: Optional[str] = None
        snippets: list[str] = []
        search_task: Optional[asyncio.Task] = None

        try:
            #  Launches HTTP retrieval asynchronously as an asyncio task in the background without blocking the voice loop.
            search_task = asyncio.create_task(
                self._retriever.search(text, self._rag_config.top_k)
            )
            # Waits up to RAG_FILLER_DELAY (default 0.7s). If retrieval doesn't return in time, plays a filler TTS clip.
            done, _ = await asyncio.wait({search_task}, timeout=RAG_FILLER_DELAY)
            if not done:
                session = getattr(self, "session", None)
                if self._rag_filler is not None and session is not None:
                    self._rag_filler.play(session)
                    filler_played = True

            # Calculates how much time is left before reaching the hard cutoff RAG_TIMEOUT (e.g., 2.0s - 0.45s = 1.55s left).
            elapsed = time.monotonic() - start_time
            remaining_time = max(0.0, RAG_TIMEOUT - elapsed)

            result: list[str] = []
            try:
                # If the task completed before the delay, retrieve result immediately; otherwise wait the remaining time.
                if search_task.done():
                    result = search_task.result()
                else:
                    result = await asyncio.wait_for(asyncio.shield(search_task), timeout=remaining_time)
            
            # Catches retrieval errors (network, timeout, parsing) and falls back to empty snippets.
            except Exception as exc:
                if search_task is not None and not search_task.done():
                    search_task.cancel()
                error_type = type(exc).__name__
                logger.warning("RAG retrieval failed: %s", error_type)
                result = []

            # Ensures that the returned snippets do not exceed a character limit, and builds a context message in the required format.
            snippets = clean_snippets(
                result,
                top_k=self._rag_config.top_k,
                max_chars=RAG_MAX_CONTEXT_CHARS,
            )

            # Ensures that the returned snippets do not exceed a character limit, and builds a context message in the required format.
            msg = build_context_message(
                snippets,
                mode=self._rag_config.grounding_mode,
            )
            # Injects the formatted grounding prompt directly into LiveKit's turn context before the LLM generates speech for this turn.
            if msg is not None:
                turn_ctx.add_message(role="assistant", content=msg)

            latency_ms = round((time.monotonic() - start_time) * 1000, 2)
            logger.info(
                "RAG turn: gate=True, latency_ms=%.2f, snippet_count=%d, filler_played=%s, error_type=%s",
                latency_ms,
                len(snippets),
                filler_played,
                error_type,
            )
        except asyncio.CancelledError:
            if search_task is not None and not search_task.done():
                search_task.cancel()
            raise
        finally:
            self.voice_controller.rag_status = "idle"



server = AgentServer()

# Main process only: starts THE ONE monitor thread. No-op in job processes and
# when a monitor is already running.
start_global_monitor()

try:
    prewarm_worker_resources(run_startup_check)
except Exception:
    logger.exception("Worker prewarm failed (continuing).")
finally:
    # Remove temporary startup handler so LiveKit's CLI can manage console logging exclusively.
    if _startup_sh is not None and _startup_sh in _root.handlers:
        _root.removeHandler(_startup_sh)

agent_worker_name = WORKER_AGENT_NAME

def _worker_setup(proc: agents.JobProcess) -> None:
    for h in list(logging.getLogger().handlers):          # existing handler cleanup
        if isinstance(h, logging.StreamHandler):
            logging.getLogger().removeHandler(h)
    report_agent_event(str(os.getpid()), "READY")          # "process started"

server.setup_fnc = _worker_setup


async def on_job_request(job_req: agents.JobRequest) -> None:
    """Evaluates incoming job requests and immediately rejects blocked numbers/callers."""
    blocked, matched_id = is_call_blocked(job_req)
    if blocked:
        logger.warning(
            f"🚫 [CALL REJECTED] Job request {job_req.id} rejected. Blocked number/caller: '{matched_id}'."
        )
        await job_req.reject()
        return

    logger.info(f"Accepted job request {job_req.id} for room {job_req.job.room.name} [pid={os.getpid()}].")
    await job_req.accept()


async def _my_agent_impl(ctx: agents.JobContext):
    
    if not await asyncio.to_thread(global_monitor_running):
        logger.warning("[GlobalFSM] Monitor not reachable — this call will not be monitored.")

    # Recorder reference initialized up-front to prevent UnboundLocalError in disconnect closures
    recorder: SessionRecorder | None = None

    # ------------------------------------------------------------------
    # 1. Immediate Call Blocklist Check upon Room Entry
    # ------------------------------------------------------------------
    blocked, matched_id = is_call_blocked(ctx)
    if blocked:
        logger.warning(
            f"🚫 [CALL REJECTED] Rejecting call in room {ctx.room.name}. "
            f"Caller/participant '{matched_id}' is in the blocked list. Hanging up immediately."
        )
        await _hangup(ctx)
        return

    tts_voice = os.environ.get("TTS_VOICE", "ec1e269e-9ca0-402f-8a18-58e0e022355a")  # Cartesia "Ariana"
    tts_model = os.environ.get("TTS_MODEL", "cartesia/sonic-3")
    tts_provider = os.environ.get("TTS_PROVIDER", "cartesia").strip().lower()
    llm_model = os.environ.get("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it")
    stt_lang = os.environ.get("DEEPGRAM_LANGUAGE", "en")

    # Engines: Direct Deepgram STT + FallbackLLM (Cloud -> Ollama) + Configurable TTS (Cartesia via LiveKit Inference)
    # stt_engine = STT(api_key=DEEPGRAM_API_KEY or "", model="nova-3", language=stt_lang)
    # local_ollama = OllamaLLM(model=OLLAMA_MODEL.strip(), base_url=OLLAMA_BASE_URL, think=False)
    
    # ── Build the speech and language engines for this call ──────────────────────
    # STT (speech -> text): Deepgram Nova-3, called directly with our own API key.
    # TTS (text -> speech) and the final LLM choice are set up in the lines below.

    endpointing_min = float(os.environ.get("ENDPOINTING_MIN_DELAY", "0.16"))
    endpointing_max = float(os.environ.get("ENDPOINTING_MAX_DELAY", "0.45"))

    # Speech-to-text engine.
    stt_engine = STT(
        api_key=DEEPGRAM_API_KEY or "",  # key from config.py; "" if missing (avoids passing None)
        model="nova-3",                  # Deepgram's Nova-3 recognition model
        language=stt_lang,               # language code from .env (DEEPGRAM_LANGUAGE), default "en"
    )

    # Local LLM served by Ollama on this machine.
    # Always created: it is the main LLM when USE_CLOUD_LLM is off,
    # and the backup LLM when USE_CLOUD_LLM is on.
    local_ollama = OllamaLLM(
        model=OLLAMA_MODEL.strip(),  # model name from config.py, trailing spaces removed
        base_url=OLLAMA_BASE_URL,    # Ollama server address (no /v1 suffix)
        think=False,                 # skip the reasoning chain for fast replies
    )
    # Read USE_CLOUD_LLM from .env; True only if it is "true" or "1"
    # (case-insensitive), otherwise False.
    use_cloud = os.getenv("USE_CLOUD_LLM", "false").lower() in ("true", "1")
    if use_cloud:
        cloud_llm = inference.LLM(model=llm_model)
        llm_engine = FallbackLLM(primary_llm=cloud_llm, fallback_llm=local_ollama)
    else:
        llm_engine = local_ollama

    if tts_provider == "deepgram":
        tts_engine = DeepgramTTS(api_key=DEEPGRAM_API_KEY or "")
    else:
        tts_engine = inference.TTS(model=tts_model, voice=tts_voice)

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={
                "min_delay": 0.25,
                "max_delay": 0.75,
            },
            preemptive_generation={
                "enabled": True,
                "preemptive_tts": True,
            },
        ),
    )

    # ── Keypad DTMF Player & Digits Collector ─────────────────────────────────
    dtmf_player = DtmfPlayer(ctx.room)
    collector = DtmfCollector(player=dtmf_player, session=session)
    assistant: Optional[Assistant] = None

    def _on_sip_dtmf(ev: rtc.SipDTMF):
        digit = (ev.digit).strip()
        logger.info(f"📞 [SIP DTMF Event] Received digit '{digit}' from participant {getattr(ev.participant, 'identity', 'unknown')}")
        # Keypad barge-in: stop speech if agent is currently speaking
        if session.agent_state == "speaking":
            session.interrupt()
        asyncio.create_task(collector.on_dtmf(digit))

    ctx.room.on("sip_dtmf_received", _on_sip_dtmf)

    # ── DataPacket Listener: Keyboard input from web console, chat, or Asterisk bridge ──
    def _on_data_received(dp: rtc.DataPacket):
        try:
            raw_text = dp.data.decode("utf-8").strip()
            if raw_text.startswith("{") and raw_text.endswith("}"):
                obj = json.loads(raw_text)
                if "digit" in obj:
                    raw_text = str(obj["digit"]).strip()
                elif "text" in obj:
                    raw_text = str(obj["text"]).strip()
            for ch in raw_text:
                if ch in "0123456789*#":
                    logger.info(f"💻 [DataChannel Keypress] Received '{ch}'")
                    if session.agent_state == "speaking":
                        session.interrupt()
                    asyncio.create_task(collector.on_dtmf(ch))
        except Exception as e:
            logger.debug(f"Data packet parsing error: {e}")

    ctx.room.on("data_received", _on_data_received)

    # ── In-Band Audio DTMF Detection on Caller Audio Track ────────────────────
    def _attach_audio_dtmf_detector(track: rtc.Track, p_identity: str):
        if track.kind == rtc.TrackKind.KIND_AUDIO and not p_identity.startswith("coordinator"):
            logger.info(f"🎧 [DTMF Detector] Attached in-band audio DTMF listener for '{p_identity}'")
            asyncio.create_task(_listen_inband_dtmf(track, collector))

    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        _attach_audio_dtmf_detector(track, getattr(participant, "identity", "unknown"))

    # Also attach to any remote audio tracks already subscribed
    for p in ctx.room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.track:
                _attach_audio_dtmf_detector(pub.track, getattr(p, "identity", "unknown"))

    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        if getattr(ev, "is_final", False) and getattr(ev, "transcript", ""):
            logger.info(f"🗣️ [User Spoke]: \"{ev.transcript}\"")
            collector.on_spoken_text(ev.transcript)

    @session.on("function_tools_executed")
    def _on_tools_executed(ev):
        logger.debug("tool call completed")

    @session.on("conversation_item_added")
    def _on_item_added(ev):
        logger.debug("conversation item added")
        item = getattr(ev, "item", None)
        if item and getattr(item, "role", "") == "assistant":
            text = getattr(item, "text", "") or getattr(item, "formatted_text", "") or ""
            if not text and hasattr(item, "content"):
                parts = getattr(item, "content", [])
                text = " ".join(str(p).strip() for p in parts if str(p).strip()) if isinstance(parts, list) else str(parts)
            clean_text = text.strip()
            if clean_text and not clean_text.startswith("[GROUNDING CONTEXT]"):
                logger.info(f"🤖 [Agent Spoke]: \"{clean_text}\"")

            if re.search(r"\b(goodbye|have a (great|nice|good) day|bye now)\b", text, re.IGNORECASE):
                logger.info(f"👋 [Agent] Goodbye detected in assistant response: '{text.strip()}'. Scheduling auto-hangup.")
                async def _auto_hangup_after_goodbye():
                    await asyncio.sleep(3.5)  # Wait for TTS audio to reach caller
                    if assistant is not None and assistant.voice_controller is not None:
                        if assistant.voice_controller.current_state not in (ControllerState.ENDING, ControllerState.READY):
                            logger.info("📴 [Agent] Executing auto-hangup following goodbye.")
                            assistant.voice_controller.on_call_end(ctx.room.name)
                            await _hangup(ctx)
                    else:
                        await _hangup(ctx)
                asyncio.create_task(_auto_hangup_after_goodbye())

    async def _on_coordinator_left_cb():
        """Called by VoiceController when a coordinator participant leaves."""
        # Guard: only react if a coordinator call was actually active — prevents
        # spurious replies when a coordinator participant leaves without ever connecting.
        if not getattr(session, "coordinator_connected", False):
            return
        logger.info("Coordinator left the call — re-engaging with the caller.")
        setattr(session, "coordinator_connected", False)
        session.input.set_audio_enabled(True)
        session.output.set_audio_enabled(True)
        egress_id = getattr(session, "recording_egress_id", None)
        if egress_id:
            setattr(session, "recording_egress_id", None)
            await _stop_call_recording(ctx, egress_id)
        await session.generate_reply(
            instructions=(
                "The call with the coordinator has just ended. Tell the "
                "caller the coordinator call is complete, then ask if "
                "there's anything else you can help with, or if they'd "
                "like to end the call now."
            )
        )

    _cleaned_up = False
    def _on_caller_disconnect_cb():
        """Called by VoiceController when the caller hangs up. Cleans up recorder & DTMF.
        Idempotent — may fire from participant_disconnected, session close, or job shutdown."""
        nonlocal _cleaned_up
        if _cleaned_up:   # Already ran — don't double-stop the recorder or re-hangup
            return
        _cleaned_up = True
        async def _cleanup():
            if assistant is not None and getattr(assistant, "_retriever", None):
                try:
                    await assistant._retriever.aclose()
                except Exception as err:
                    logger.warning(f"Error closing RAG retriever: {err}")
            try:
                await dtmf_player.close()
            except Exception:
                pass
            if recorder is not None:
                try:
                    await recorder.stop()
                except Exception as err:
                    logger.warning(f"Error finalizing recorder: {err}")
            egress_id = getattr(session, "recording_egress_id", None)
            if egress_id:
                setattr(session, "recording_egress_id", None)
                await _stop_call_recording(ctx, egress_id)
            await _hangup(ctx)
        asyncio.create_task(_cleanup())

    def _on_blocked_cb():
        """Called by VoiceController when a blocked participant joins."""
        asyncio.create_task(_hangup(ctx))

    #1.Read JSON string from LiveKit job metadata
    try:
        call_metadata = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Could not parse job metadata as JSON: {ctx.job.metadata!r}")
        call_metadata = {}

    audio_input_opts = None
    if HAS_AI_COUSTICS:
        try:
            audio_input_opts = room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_L,
                ),
            )
        except Exception as e:
            logger.warning(f"Could not initialize ai_coustics: {e}")
            audio_input_opts = None

    room_opts = room_io.RoomOptions(
        close_on_disconnect=False,
        audio_input=audio_input_opts if audio_input_opts else room_io.AudioInputOptions(),
    )
    # 2. Pass call_metadata into the Assistant (instantiates fresh VoiceController per call)
    assistant = Assistant(ctx, call_metadata=call_metadata, collector=collector)

    # Start this call's VoiceController FSM loop and wait until ready
    assistant.voice_controller.ensure_fsm_running()
    await assistant.voice_controller.wait_until_ready()
    assistant.voice_controller.on_call_start(ctx.room.name)

    # Wire all session lifecycle events into the FSM via attach_session.
    # Participant events, agent state, idle watchdog, and shutdown hook are all
    # handled inside the controller — agent.py only keeps recorder/DTMF concerns.
    IDLE_HANGUP_SECONDS = float(os.environ.get("IDLE_HANGUP_SECONDS", "60.0"))

    def _handle_coordinator_left() -> None:
        asyncio.create_task(_on_coordinator_left_cb())

    assistant.voice_controller.attach_session(
        session,
        assistant=assistant,
        ctx=ctx,
        idle_hangup_seconds=IDLE_HANGUP_SECONDS,
        on_blocked=_on_blocked_cb,
        on_caller_disconnect=_on_caller_disconnect_cb,
        on_coordinator_left=_handle_coordinator_left,
        is_call_blocked_fn=is_call_blocked,
    )

    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_opts,
    )

    # After session.start(): advance FSM to SESSION_ACTIVE if caller was already in room
    # (inbound SIP — no participant_connected event fires for pre-existing participants).
    assistant.voice_controller.on_session_started()

    # Publish DTMF dual-tone audio track into room
    await dtmf_player.start()

    # Start local session recording (MP3 audio and Conversation_log)
    recorder = SessionRecorder()
    setattr(session, "recorder", recorder)
    await recorder.start(ctx, session)

    # Allow SIP media stream 1s to settle before speaking initial greeting
    is_sip = any(p.identity.startswith("sip_") for p in ctx.room.remote_participants.values())
    if is_sip:
        await asyncio.sleep(1.0)

    if call_metadata.get("call_type") == "leave_verification":
        employee_name = call_metadata.get("employee_name", "there")
        await session.say(
            f"Hello {employee_name}, this is the HR voice assistant calling regarding your leave request. Could you please confirm your identity to proceed?",
            allow_interruptions=False,
        )
    elif call_metadata.get("call_type") in ("scheduled_reminder", "policy_acknowledgement"):
        employee_name = call_metadata.get("employee_name", "there")
        await session.say(
            f"Hello {employee_name}, this is the HR voice assistant with a scheduled notification. Could you please confirm your identity to get started?",
            allow_interruptions=False,
        )
    else:
        await session.say(
            "Hello! Welcome to Eoxys HR voice assistant. Please say or enter your employee ID to get started.",
            allow_interruptions=False,
        )


@server.rtc_session(agent_name=agent_worker_name, on_request=on_job_request)
async def my_agent(ctx: agents.JobContext):
    try:
        await _my_agent_impl(ctx)
    except BaseException as e:
        import traceback, sys
        err = traceback.format_exc()
        try:
            with open("/tmp/my_agent_crash.log", "w") as f:
                f.write(f"Exception: {type(e)} {e}\n{err}\n")
        except Exception:
            pass
        logger.error(f"💥💥💥 MY_AGENT CRASHED: {type(e)} {e}\n{err}")
        sys.stderr.write(f"\n💥💥💥 MY_AGENT CRASHED: {type(e)} {e}\n{err}\n")
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    agents.cli.run_app(server)