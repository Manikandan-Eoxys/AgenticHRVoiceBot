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
from datetime import date, timedelta
from pathlib import Path

import re
from typing import Optional, Dict, Any, List, Tuple
import aiohttp
import numpy as np
from dotenv import load_dotenv

from livekit import agents, api, rtc
from livekit.protocol import egress as egress_proto
from livekit.agents import (
    AgentServer, AgentSession, Agent, inference, room_io,
    TurnHandlingOptions, function_tool, RunContext,
)

# Try importing ai_coustics plugin if installed
try:
    from livekit.plugins import ai_coustics
    HAS_AI_COUSTICS = True
except ImportError:
    HAS_AI_COUSTICS = False

import hr_tools
from security.call_blocklist import is_call_blocked, is_number_blocked
from config import (
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
    DEEPGRAM_API_KEY, OLLAMA_MODEL, OLLAMA_BASE_URL,
)
from services.session_recorder import SessionRecorder
from services.monitoring_handler import install_monitoring_handler
from controllers.voice_controller import VoiceController
from controllers.global_monitor_fsm import GlobalMonitorFSM
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
    freqs = DTMF_FREQUENCIES.get(str(digit).upper())
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
        if str(digit).upper() in DTMF_FREQUENCIES:
            self._queue.put_nowait(str(digit))

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


class DtmfCollector:
    """Collects 4-digit employee ID from keypad DTMF tones (0-9) or spoken voice."""

    def __init__(self, player: DtmfPlayer | None = None, session: AgentSession | None = None):
        self.player = player
        self.session = session
        self._buffer: list[str] = []
        self._event = asyncio.Event()
        self._collecting = False

    def is_collecting(self) -> bool:
        return self._collecting

    async def on_dtmf(self, digit: str) -> None:
        d = str(digit).strip().upper()
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

        if d in "0123456789":
            self._buffer.append(d)
            curr = "".join(self._buffer)
            logger.info(f"🔢 [Keypad DTMF] Received '{d}'. Buffer: {curr} (collecting={self._collecting})")
            if len(self._buffer) >= 4:
                self._event.set()
                # If caller typed 4 digits while NOT in keypad collection tool (e.g. during initial greeting)
                if not self._collecting and self.session and not getattr(self.session, "verified_employee_id", None):
                    cand = "".join(self._buffer[:4])
                    self._buffer.clear()
                    logger.info(f"🔢 [Keypad DTMF] Caller typed full ID '{cand}' at prompt. Triggering verification.")
                    asyncio.create_task(
                        self.session.generate_reply(
                            user_input=f"The caller entered employee ID {cand} on their phone keypad."
                        )
                    )

    def on_spoken_text(self, text: str) -> None:
        """Captures spoken digits when waiting for employee ID."""
        if not self._collecting or not text:
            return

        # Check for 4 consecutive digits (e.g. "1002")
        m = re.search(r"\b(1\d{3})\b", text)
        if m:
            extracted = m.group(1)
            logger.info(f"🗣️ [Voice Collector] Captured 4-digit ID from speech: '{extracted}'")
            self._buffer = list(extracted)
            self._event.set()
            return

        # Check spelled out words: "one zero zero two"
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
                self._event.set()

    async def wait_for_id(self, timeout: float = 15.0) -> str:
        self._collecting = True
        self._event.clear()

        # If already buffered 4 digits
        if len(self._buffer) >= 4:
            res = "".join(self._buffer[:4])
            self._buffer.clear()
            self._collecting = False
            return res

        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            if len(self._buffer) >= 4:
                res = "".join(self._buffer[:4])
                self._buffer.clear()
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
        import boto3
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
OUTBOUND_TRUNK_ID = os.environ.get("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_48TbGFJbEJvV")
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

            Today's actual date is {today_str}. Use this as ground truth for
            every relative date the caller mentions — "today", "tomorrow",
            "yesterday", "next Friday", a bare "the 15th", and so on. Always
            compute dates relative to {today_str}.

            ════════════════════════════════════════════════════════════════
            1. IDENTITY VERIFICATION & KEYPAD (DTMF) FALLBACK
            ════════════════════════════════════════════════════════════════
            At the start of every call, greet the caller and ask for their
            4-digit employee ID (1001 to 1015). Callers often speak digits
            one by one (e.g. "one zero zero one") — convert to numeric string
            "1001" and immediately call get_employee_by_id.

            Once found, read the name back for confirmation:
            "This is employee ID 1001, Ravikala, is that correct?"
            - If they confirm YES: immediately call confirm_employee_identity
              with that ID to lock the session to this employee.
            - IF VERIFICATION FAILS (either of two failure triggers):
              Trigger 1: Spoken ID was not found in the system or out of range.
              Trigger 2: Caller says "No" to the name readback (agent mistook ID).
              ACTION ON FAILURE: Do not keep asking repeatedly via voice.
              Say this sentence ONCE and invoke collect_employee_id_via_keypad in that same turn:
              "I couldn't verify that ID. Please enter your 4-digit employee ID
              using your phone keypad, or say it slowly one digit at a time."
              Do not speak again until the tool returns.
              The caller can either press digits 0-9 on their keypad or speak them;
              both work. When the tool returns the ID, immediately look it up with
              get_employee_by_id and read back the name to verify.

            ════════════════════════════════════════════════════════════════
            2. THE 5 CORE HR POLICIES & PERMITTED ACTIONS
            ════════════════════════════════════════════════════════════════

            [POLICY 1: LEAVE & HOLIDAY POLICY]
            - 8 leave types: Casual Leave (CL), Sick Leave (SL), Maternity,
              Paternity, Comp Off, Bereavement, Short Leave (hourly/half-day),
              and Leave Without Pay (LWP).
            - Checking balance: Use get_leave_balance for a single type or
              get_all_leave_balances for a full summary.
            - Holiday inquiries: Use check_company_holiday (e.g. "Is Monday a
              holiday?", "Is Diwali a holiday?") or list_upcoming_company_holidays.
            - Policy questions: Use get_leave_policy or get_hr_policy (e.g.
              carry-forward rules: earned leave carries forward up to 30 days;
              casual leave expires at year end).
            - Applying for leave (Two-step flow):
              Step 1: Check availability with check_leave_availability(employee_id,
              leave_type, start_date, end_date). Read back available vs requested
              days and ask if ready to submit.
              Step 2: When caller says YES, ask for a brief reason and call
              confirm_leave_request. This records the request with status
              'submitted' and emails HR.
            - Cancelling leave: If caller asks to cancel leave, call cancel_leave_request.
            - Communicating Leave Status & Reminders:
              * Caller asks "What's the status of my leave?" -> call get_leave_request_status.
              * If Approved: Tell them their leave was approved by their manager.
              * If Rejected: Read back the manager's reason (e.g. "Team coverage
                is required on that date").
              * If Pending: Tell them it is still awaiting manager approval, and
                offer to send a reminder. If they say yes, call send_manager_leave_reminder.

            [POLICY 2: ATTENDANCE & REGULARIZATION POLICY]
            - Office hours: 9:00 AM to 6:00 PM (8 work hours + 1 hr lunch).
              Morning grace period is 15 minutes (until 9:15 AM). Arrival between
              9:16 AM and 10:00 AM is logged as a Late mark.
              3 late marks in a month incur a half-day salary or leave deduction.
            - "Why is yesterday showing as absent?" or "Did my punch register?":
              Call check_attendance_status(date_str="yesterday", employee_id=...).
              Explain what the system recorded.
            - "How many late marks do I have?":
              Call get_late_marks(employee_id). Explain the count and 3-late-marks rule.
            - Action — Attendance Regularization:
              If caller forgot to punch in/out, had biometric issues, or wants
              to regularize attendance: call submit_attendance_regularization(
              date_str, punch_type, actual_time, reason, employee_id).

            [POLICY 3: WORK FROM HOME / HYBRID WORK POLICY]
            - Eligibility: Confirmed employees post probation.
              Quota: Up to 2 days per week or 8 days per month with manager approval.
              Core hours: 9:30 AM to 5:30 PM.
            - Inquiries ("Can I work from home tomorrow?", "How many WFH days left?"):
              Call check_wfh_eligibility and get_wfh_quota_balance.
            - Action — Apply WFH:
              Call apply_wfh_request(start_date, end_date, reason, employee_id).
              Informs caller that WFH request has been submitted pending manager approval.
            - Check WFH status: Call get_wfh_request_status.

            [POLICY 4: PAYROLL & SALARY POLICY]
            - Salary credit schedule: Credited on the last working day of each month.
              Payslips available on the 1st of every month. Call get_salary_credit_date.
            - Basic salary inquiries: Call get_basic_salary_info(employee_id).
            - Deductions breakdown: Call get_salary_deductions_info(month_year, employee_id)
              to explain PF (12%), Professional Tax, TDS, and unpaid leaves.
            - Payslip inquiries: Call check_payslip_status(month_year, employee_id).
            - Payroll discrepancies: If an employee disputes deductions or reports
              a salary difference ("My salary is 5000 less than expected"), explain
              that this requires payroll review and create a ticket using
              raise_hr_ticket(category="Payroll & Salary Issues",
              ticket_type="PAYROLL_SALARY_DISCREPANCY", ...).

            [POLICY 5: CODE OF CONDUCT & GRIEVANCES]
            - Normal workplace questions: Call get_code_of_conduct_policy(topic)
              for dress code (business casual Mon-Thu, smart casual Fri),
              company laptops/VPN rules, and conflict of interest guidelines.
            - SENSITIVE COMPLAINTS (Harassment, Misconduct, Manager Grievance):
              Treat with maximum empathy and confidentiality.
              Respond: "This is a sensitive matter. I take this very seriously and
              will raise this with the appropriate HR team for confidential handling."
              Call report_confidential_grievance to log the issue confidentially
              and generate a high-priority ticket for senior HR.

            ════════════════════════════════════════════════════════════════
            3. HR TICKET CREATION (HUMAN INTERVENTION REQUIRED)
            ════════════════════════════════════════════════════════════════
            Whenever an issue cannot be resolved automatically by policy rules,
            create an HR ticket using raise_hr_ticket:
            Categories & Types:
            1. 'Payroll & Salary Issues': PAYROLL_SALARY_DISCREPANCY, SALARY_NOT_CREDITED,
               SALARY_DEDUCTION_QUERY, PAYSLIP_NOT_AVAILABLE, PAYSLIP_CORRECTION,
               BONUS_INCENTIVE_DISCREPANCY, TAX_DEDUCTION_QUERY.
            2. 'Attendance Issues': ATTENDANCE_REGULARIZATION, ATTENDANCE_STATUS_CORRECTION,
               ATTENDANCE_CORRECTION, BIOMETRIC_ISSUE, ATTENDANCE_SYSTEM_ISSUE, LATE_MARK_DISPUTE.
            3. 'Leave Issues': LEAVE_REQUEST_ISSUE, LEAVE_BALANCE_DISCREPANCY,
               LEAVE_APPROVAL_DELAY, LEAVE_CANCELLATION_ISSUE, LEAVE_EXCEPTION_REQUEST,
               EMERGENCY_LEAVE_REQUEST.
            4. 'HRMS / Employee Profile Issues': EMPLOYEE_DATA_CORRECTION,
               PERSONAL_DETAILS_CORRECTION, ADDRESS_UPDATE_ISSUE, BANK_DETAILS_UPDATE,
               EMERGENCY_CONTACT_UPDATE, EMPLOYEE_ID_ISSUE.
            5. 'HR Documents': EXPERIENCE_LETTER_REQUEST, EMPLOYMENT_CERTIFICATE_REQUEST,
               SALARY_CERTIFICATE_REQUEST, RELIEVING_LETTER_REQUEST, HR_DOCUMENT_CORRECTION.
            6. 'Work From Home / Hybrid Work': WFH_EXCEPTION_REQUEST, WFH_APPROVAL_DELAY,
               WFH_SYSTEM_ISSUE, HYBRID_WORK_ISSUE.

            Always read the created ticket reference number (e.g. TICK-1042) to the caller
            and assure them HR will follow up. To check existing tickets, use check_my_hr_tickets.

            ════════════════════════════════════════════════════════════════
            4. MANDATORY POLICY ACKNOWLEDGEMENTS & SCHEDULED REMINDERS
            ════════════════════════════════════════════════════════════════

            [USE CASE 1: MANDATORY HR POLICY ACKNOWLEDGEMENT]
            - Enterprise scenario: HR releases a mandatory policy (e.g. Work From Home policy)
              with an acknowledgement deadline (e.g. before Friday).
            - When caller asks "Do I have any pending policies to acknowledge?" or checks notices:
              Call check_pending_policy_acknowledgements(employee_id).
              Explain: "A new Work From Home policy has been released. Please review the policy and confirm whether you acknowledge it."
            - When caller states "Yes, I acknowledge the policy" or "I acknowledge the Work From Home policy":
              Call acknowledge_hr_policy(policy_name="Work From Home Policy", employee_id=...).
              Say: "Thank you. Your acknowledgement has been recorded."

            [USE CASE 2: DOCUMENT / FORM SUBMISSION REMINDER]
            - When caller asks to schedule a reminder:
              "I need to submit my PF nomination form. Can you remind me tomorrow at 10 AM?"
              Call schedule_employee_reminder(reminder_topic="PF nomination form", scheduled_time="tomorrow at 10 AM", employee_id=...).
              Say: "Sure. I'll remind you tomorrow at 10 AM."
            - Checking scheduled messages:
              If caller asks "Do I have any scheduled reminders?" or "Are there any messages scheduled for me?":
              Call check_my_scheduled_reminders(employee_id).
            - Acknowledging reminder completion:
              When checking or delivering the reminder:
              "Good morning. This is a reminder to submit your PF nomination form. Have you completed it?"
              When caller answers "Yes, I've submitted it" or "Yes, I completed it":
              Call acknowledge_scheduled_reminder(reminder_topic_or_id="PF nomination form", response_text="Yes, submitted", employee_id=...).
              Say: "Thank you. I've recorded your acknowledgement."

            [USE CASE 3: SALARY / PAYSLIP NOTIFICATION & SCHEDULED REMINDER]
            - When caller asks about their salary slip or payslip status:
              Call check_payslip_status(month_year="September 2026", employee_id=...).
              Say: "Your September salary slip is now available in the employee portal. Would you like me to remind you later to download it?"
            - When caller replies "Yes, remind me tomorrow at 10 AM":
              Call schedule_employee_reminder(reminder_topic="September salary slip download", scheduled_time="tomorrow at 10 AM", employee_id=...).
              Say: "Sure. I'll remind you tomorrow at 10 AM."
            - When caller confirms downloading the payslip:
              "Your salary slip is available. Have you downloaded it?"
              When caller says "Yes" or "I downloaded it":
              Call acknowledge_scheduled_reminder(reminder_topic_or_id="September salary slip", response_text="Yes, downloaded", employee_id=...).
              Say: "Thank you. Your acknowledgement has been recorded."

            ════════════════════════════════════════════════════════════════
            5. TALKING TO COORDINATOR & ENDING THE CALL
            ════════════════════════════════════════════════════════════════
            - If caller asks for a live human coordinator, ask them to confirm
              "yes, proceed", then call transfer_to_coordinator.
            - When caller has finished and you've given a polite closing goodbye,
              call end_call immediately after your goodbye to hang up the line.
            - Keep voice replies natural, concise, and professional without emojis
              or special symbols.
"""


OUTBOUND_LEAVE_VERIFICATION_INSTRUCTIONS = """You are an HR voice assistant making an OUTBOUND call — you called
            {employee_name}, employee ID {employee_id}, they did not call you.

            Today's actual date is {today_str}. Use this as ground truth for
            any relative date you need to reason about.

            Start of call — verify you reached the right person: greet them
            by name and identify yourself, then ask them to confirm their
            identity, for example: "Hello, is this {employee_name}? I'm
            calling from HR, I wanted to verify a few details with you." If
            they say yes, immediately call confirm_employee_identity with
            employee_id "{employee_id}" — this must happen before any leave
            tool. If they say this isn't {employee_name}, apologize for the
            wrong number, do not discuss any leave or personal details, and
            call end_call.

            Once identity is confirmed, call get_pending_leave_request with
            employee_id "{employee_id}" to find out which request you're
            calling about. Read back the dates it returns and tell them
            plainly, for example: "I'm calling because your leave request
            for August 27 to August 29 is currently pending approval." —
            using the actual dates the tool gave you, not a placeholder.
            Then ask: "Can you give me a brief
            reason for this leave, to help get it approved?" Once they give
            a reason (or explicitly say they'd rather not), call
            record_leave_verification_reason with the request_id from the
            lookup and their reason (empty string if they declined). Make
            clear this records their reason for HR's review — it does not
            itself approve the leave.

            If get_pending_leave_request finds nothing pending, tell them
            plainly there's no pending leave request on file right now,
            apologize for the confusion, and move to ending the call.

            Talking to the coordinator: if they ask to talk to the
            coordinator directly instead of you, follow the same
            confirm-then-transfer flow as inbound calls — ask them to say
            "yes, proceed", then call transfer_to_coordinator.

            Your responses are concise, natural for a phone call, and
            without any complex formatting, emojis, or asterisks. You are
            professional, warm, and clear.

            Ending the call: once you've covered the reason for the call
            and the person has nothing further, say a brief closing
            goodbye and then call end_call right after it. Only call it
            once, and only after the goodbye has actually been said."""


OUTBOUND_REMINDER_INSTRUCTIONS = """You are an HR voice assistant making an OUTBOUND notification or reminder call to
            {employee_name}, employee ID {employee_id}.

            Today's actual date is {today_str}. Use this as ground truth for any dates mentioned.

            Start of call: Greet {employee_name} and verify identity:
            "Hello {employee_name}. I'm calling from HR."
            Once confirmed, call confirm_employee_identity with employee_id "{employee_id}".

            Reason for call:
            {reminder_prompt}

            If this is a Mandatory Policy Acknowledgement:
            Say: "Hello {employee_name}. A new Work From Home policy has been released. Please review the policy and confirm whether you acknowledge it."
            When employee replies "Yes, I acknowledge the policy" (or similar):
            Call acknowledge_hr_policy and say: "Thank you. Your acknowledgement has been recorded."

            If this is a Document / Form Submission Reminder:
            Say: "Good morning. This is a reminder to submit your PF nomination form. Have you completed it?"
            When employee replies "Yes, I've submitted it":
            Call acknowledge_scheduled_reminder and say: "Thank you. I've recorded your acknowledgement."

            If this is a Salary / Payslip Notification or Reminder:
            Say: "Your salary slip is available. Have you downloaded it?"
            When employee replies "Yes":
            Call acknowledge_scheduled_reminder and say: "Thank you. Your acknowledgement has been recorded."

            Ending the call: once acknowledgement is recorded and employee has no further questions,
            say a polite closing goodbye and call end_call immediately.
"""


class Assistant(Agent):
    def __init__(
        self,
        job_ctx: agents.JobContext,
        call_metadata: dict | None = None,
        collector: DtmfCollector | None = None,
    ) -> None:
        self._job_ctx = job_ctx
        self.collector = collector
        today_str = date.today().strftime("%A, %B %-d, %Y")
        call_metadata = call_metadata or {}
        call_type = call_metadata.get("call_type")

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

        super().__init__(
            instructions=instructions,
            tools=hr_tools.ALL_TOOLS,
        )

    @function_tool()
    async def end_call(self, context: RunContext) -> None:
        """Call this exactly once, as the very last action of the call,
        immediately after you've said your closing goodbye and the caller
        has nothing further to ask. Hangs up the phone line."""
        await context.wait_for_playout()
        await _hangup(self._job_ctx)

    @function_tool()
    async def collect_employee_id_via_keypad(self, context: RunContext) -> str:
        """Use this when a spoken employee ID didn't match any employee on
        file, or when the caller denied the name verification question, or asks
        to key in their ID. Prompts caller to enter their 4-digit employee ID
        on their phone keypad or speak it one digit at a time."""
        if not self.collector:
            return "Keypad collector not initialized. Please ask the caller to speak their 4-digit employee ID clearly."

        logger.info("⏳ [DTMF Fallback] Listening for 4-digit employee ID from keypad or voice...")
        digits = await self.collector.wait_for_id(timeout=15.0)
        if digits:
            logger.info(f"✅ [DTMF Fallback] Captured 4-digit ID: {digits}")
            return (
                f"Caller entered employee ID {digits}. "
                f"Immediately call get_employee_by_id with employee_id '{digits}' and verify their name with the caller."
            )
        else:
            logger.warning("⚠️ [DTMF Fallback] No digits received before timeout.")
            return (
                "No digits were received from keypad or voice within the timeout. "
                "Ask the caller once more: 'I didn't receive your employee ID. "
                "Could you please enter your 4-digit ID on the keypad, or say it slowly one digit at a time?'"
            )

    @function_tool()
    async def transfer_to_coordinator(self, context: RunContext) -> str:
        """Call this ONLY after the caller has explicitly confirmed
        (e.g. said 'yes, proceed') that they want to be connected to
        the coordinator. Dials the coordinator's phone number into
        this same call with a ring tone while dialing."""
        if is_number_blocked(COORDINATOR_PHONE_NUMBER):
            logger.warning(f"🚫 Coordinator transfer blocked: {COORDINATOR_PHONE_NUMBER} is in blocklist.")
            return "The transfer cannot be completed because the coordinator's number is currently blocked."

        room = self._job_ctx.room
        identity = f"coordinator-{int(time.time())}"
        # ── Check recording mode: if cutoff, stop recording before dialing coordinator ──
        rec_mode = os.environ.get("RECORDING_COORDINATOR_MODE", "record_all").strip().lower()
        if rec_mode == "cutoff" and getattr(context.session, "recorder", None):
            logger.info("🛑 [Transfer] Mode is 'cutoff': stopping recorder before coordinator pick.")
            await context.session.recorder.stop()

        # ── Start Ringback / Dialtone Beep into the Room ───────────────────────
        stop_ringback = asyncio.Event()
        ringback_task = asyncio.create_task(_ringback_loop(self._job_ctx, stop_ringback))

        try:
            async with api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lkapi:
                await lkapi.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=OUTBOUND_TRUNK_ID,
                        sip_call_to=COORDINATOR_PHONE_NUMBER,
                        room_name=room.name,
                        participant_identity=identity,
                        participant_name="Coordinator",
                        wait_until_answered=True,
                        ringing_timeout=timedelta(seconds=COORDINATOR_DIAL_TIMEOUT_SECONDS),
                    )
                )
        except Exception as e:
            stop_ringback.set()
            await ringback_task
            logger.warning(f"⚠️ [Transfer] Coordinator at {COORDINATOR_PHONE_NUMBER} did not answer or failed: {e}")
            return (
                "The coordinator did not answer, or the call could not "
                "connect. Tell the caller this plainly and ask if they'd "
                "like you to try again now, or would rather call back "
                "later. Do not say they were connected."
            )

        stop_ringback.set()
        await ringback_task
        logger.info(f"✅ [Transfer] Coordinator {identity} answered and joined room {room.name}.")

        context.session.coordinator_connected = True

        handle = context.session.say(
            "Connecting you to the coordinator now.",
            allow_interruptions=False,
        )
        await handle.wait_for_playout()
        context.session.input.set_audio_enabled(False)
        context.session.output.set_audio_enabled(False)

        egress_id = await _start_call_recording(self._job_ctx)
        context.session.recording_egress_id = egress_id

        return (
            "Coordinator connected and the handoff line has already been "
            "spoken by this tool. The agent is now muted at the code "
            "level -- do not attempt to say anything else."
        )


server = AgentServer()

# Instantiate global voice_controller and run worker startup prewarm
voice_controller = VoiceController()
voice_controller.on_worker_start()

# Run DB startup check once at worker startup
hr_tools.run_startup_check()

agent_worker_name = os.environ.get("WORKER_AGENT_NAME", "my-agent")

# Global Monitor FSM — will be started as asyncio task on first call
_global_fsm_task: Optional[asyncio.Task] = None


def _ensure_global_fsm_running() -> None:
    """Lazily starts the GlobalMonitorFSM run_loop() asyncio task once."""
    global _global_fsm_task
    if _global_fsm_task is None or _global_fsm_task.done():
        _global_fsm_task = asyncio.create_task(
            GlobalMonitorFSM.instance().run_loop(),
            name="GlobalMonitorFSM",
        )
        logger.info("🌐 [GlobalFSM] run_loop() task started.")


async def on_job_request(job_req: agents.JobRequest) -> None:
    """Evaluates incoming job requests and immediately rejects blocked numbers/callers."""
    blocked, matched_id = is_call_blocked(job_req)
    if blocked:
        logger.warning(
            f"🚫 [CALL REJECTED] Job request {job_req.id} rejected. Blocked number/caller: '{matched_id}'."
        )
        await job_req.reject()
        return

    logger.info(f"Accepted job request {job_req.id} for room {job_req.job.room.name}.")
    await job_req.accept()
    voice_controller.on_job_accepted(job_req.job.room.name)


@server.rtc_session(agent_name=agent_worker_name, on_request=on_job_request)
async def my_agent(ctx: agents.JobContext):
    # Start global FSM loop (idempotent — only creates task once)
    _ensure_global_fsm_running()

    # Trigger VoiceController FSM: READY → CONNECTING
    voice_controller.on_call_start(ctx.room.name)

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
    stt_engine = STT(api_key=DEEPGRAM_API_KEY, model="nova-3", language=stt_lang)
    cloud_llm = inference.LLM(model=llm_model)
    local_ollama = OllamaLLM(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    llm_engine = FallbackLLM(primary_llm=cloud_llm, fallback_llm=local_ollama)

    if tts_provider == "deepgram":
        tts_engine = DeepgramTTS(api_key=DEEPGRAM_API_KEY)
    else:
        tts_engine = inference.TTS(model=tts_model, voice=tts_voice)

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"min_delay": 0.5, "max_delay": 1.8},
        ),
    )

    IDLE_HANGUP_SECONDS = float(os.environ.get("IDLE_HANGUP_SECONDS", "30.0"))
    _agent_busy = True
    _last_activity = time.monotonic()

    # ── Keypad DTMF Player & Digits Collector ─────────────────────────────────
    dtmf_player = DtmfPlayer(ctx.room)
    collector = DtmfCollector(player=dtmf_player, session=session)

    def _on_sip_dtmf(ev: rtc.SipDTMF):
        digit = str(ev.digit).strip()
        logger.info(f"📞 [SIP DTMF Event] Received digit '{digit}' from participant {getattr(ev.participant, 'identity', 'unknown')}")
        # Keypad barge-in: stop speech if agent is currently speaking
        if session.agent_state == "speaking":
            session.interrupt()
        asyncio.create_task(collector.on_dtmf(digit))

    ctx.room.on("sip_dtmf_received", _on_sip_dtmf)

    def _log_state(reason: str):
        logger.debug(f"Watchdog: {reason} (agent_busy={_agent_busy}).")

    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        nonlocal _last_activity
        _last_activity = time.monotonic()
        if getattr(ev, "is_final", False) and getattr(ev, "transcript", ""):
            logger.info(f"🗣️ [User Spoke]: \"{ev.transcript}\"")
            collector.on_spoken_text(ev.transcript)
        _log_state("caller spoke (transcription)")

    @session.on("user_state_changed")
    def _on_user_state(ev):
        nonlocal _last_activity
        _last_activity = time.monotonic()
        _log_state(f"user state -> {ev.new_state}")

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        nonlocal _agent_busy, _last_activity
        _agent_busy = ev.new_state in ("thinking", "speaking", "initializing")
        _last_activity = time.monotonic()
        voice_controller.on_agent_substate_changed(ev.new_state)
        _log_state(f"agent state -> {ev.new_state}")

    @session.on("function_tools_executed")
    def _on_tools_executed(ev):
        nonlocal _last_activity
        _last_activity = time.monotonic()
        _log_state("tool call completed")

    @session.on("conversation_item_added")
    def _on_item_added(ev):
        nonlocal _last_activity
        _last_activity = time.monotonic()
        _log_state("conversation item added")

    async def _on_coordinator_left():
        nonlocal _agent_busy, _last_activity
        logger.info("Coordinator left the call — re-engaging with the caller.")
        session.coordinator_connected = False
        session.input.set_audio_enabled(True)
        session.output.set_audio_enabled(True)
        egress_id = getattr(session, "recording_egress_id", None)
        if egress_id:
            session.recording_egress_id = None
            await _stop_call_recording(ctx, egress_id)
        _agent_busy = True
        _last_activity = time.monotonic()
        await session.generate_reply(
            instructions=(
                "The call with the coordinator has just ended. Tell the "
                "caller the coordinator call is complete, then ask if "
                "there's anything else you can help with, or if they'd "
                "like to end the call now."
            )
        )

    def _on_participant_disconnected(participant: rtc.RemoteParticipant):
        if participant.identity.startswith("coordinator-"):
            asyncio.create_task(_on_coordinator_left())
        else:
            # Only hang up if all remote callers have left
            remaining = [p for p in ctx.room.remote_participants.values() if p.identity != participant.identity and not p.identity.startswith("coordinator-")]
            if not remaining:
                logger.info(f"Caller {participant.identity} disconnected — ending the call.")
                voice_controller.on_call_end()

                async def _stop_recording_then_hangup():
                    try:
                        await dtmf_player.close()
                    except Exception:
                        pass
                    try:
                        await recorder.stop()
                    except Exception as err:
                        logger.warning(f"Error finalizing recorder: {err}")
                    egress_id = getattr(session, "recording_egress_id", None)
                    if egress_id:
                        session.recording_egress_id = None
                        await _stop_call_recording(ctx, egress_id)
                    await _hangup(ctx)

                asyncio.create_task(_stop_recording_then_hangup())

    ctx.room.on("participant_disconnected", _on_participant_disconnected)

    def _on_participant_connected(participant: rtc.RemoteParticipant):
        blocked, matched_id = is_call_blocked(participant)
        if blocked:
            logger.warning(
                f"🚫 [CALL REJECTED] Blocked participant joined room {ctx.room.name}: '{matched_id}'. Hanging up."
            )
            asyncio.create_task(_hangup(ctx))
        else:
            voice_controller.on_participant_joined(participant.identity)

    ctx.room.on("participant_connected", _on_participant_connected)

    async def _idle_watchdog_loop():
        nonlocal _agent_busy
        while True:
            await asyncio.sleep(1.0)
            if getattr(session, "coordinator_connected", False):
                continue
            # If agent is busy (generating, speaking, initializing) or user is speaking, reset timer
            if _agent_busy or session.agent_state in ("thinking", "speaking", "initializing") or session.user_state == "speaking":
                _last_activity = time.monotonic()
                continue
            idle_for = time.monotonic() - _last_activity
            if idle_for >= IDLE_HANGUP_SECONDS:
                logger.info(
                    f"Caller silent and agent idle for {idle_for:.1f}s — hanging up automatically."
                )
                await _hangup(ctx)
                return

    asyncio.create_task(_idle_watchdog_loop())

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

    assistant = Assistant(ctx, call_metadata=call_metadata, collector=collector)
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_opts,
    )

    # Publish DTMF dual-tone audio track into room
    await dtmf_player.start()

    # Start local session recording (MP3 audio and Conversation_log)
    recorder = SessionRecorder()
    session.recorder = recorder
    await recorder.start(ctx, session)

    # Allow SIP media stream 1s to settle before speaking initial greeting
    is_sip = any(p.identity.startswith("sip_") for p in ctx.room.remote_participants.values())
    if is_sip:
        await asyncio.sleep(1.0)

    if call_metadata.get("call_type") == "leave_verification":
        employee_name = call_metadata.get("employee_name", "there")
        await session.generate_reply(
            instructions=(
                f"Greet {employee_name} and ask them to confirm their "
                f"identity before discussing anything, per your instructions."
            )
        )
    elif call_metadata.get("call_type") in ("scheduled_reminder", "policy_acknowledgement"):
        employee_name = call_metadata.get("employee_name", "there")
        await session.generate_reply(
            instructions=(
                f"Greet {employee_name} and confirm their identity before delivering the scheduled HR notification."
            )
        )
    else:
        await session.generate_reply(
            instructions="Greet the caller and ask for their employee ID to get started."
        )


if __name__ == "__main__":
    agents.cli.run_app(server)