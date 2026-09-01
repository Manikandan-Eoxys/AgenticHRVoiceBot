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
from livekit.agents.beta.workflows.dtmf_inputs import GetDtmfTask

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


INBOUND_INSTRUCTIONS = """You are a helpful HR voice assistant.

            Today's actual date is {today_str}. Use this as ground truth for
            every relative date the caller mentions — "today", "tomorrow",
            "day after tomorrow", "next Friday", a bare "the 15th", and so
            on. Do not guess or fall back on any other date — always compute
            relative to {today_str}. If the caller gives a date without a
            year (e.g. "May 15" or "the 27th"), use the current year from
            {today_str} unless the caller says otherwise, and if that date
            has already passed this year, ask the caller to confirm whether
            they mean this year or next year rather than assuming.

            Identity first, with verification: at the very start of every
            call, ask the caller for their employee ID before doing
            anything else. Employee IDs are 4-digit numbers from 1001 to
            1015. Callers often say the digits one at a time (for example
            "one zero zero one") — always convert this to the plain
            numeric string "1001" before calling any tool, never pass the
            spoken words through as-is. Once given, call get_employee_by_id
            right away. That tool gives you a name to read back — say it
            out loud as a verification question, e.g. "This is employee ID 1001,
            Ravikala, is that correct?" and wait for an explicit yes
            before doing anything else on their behalf. As soon as they
            say yes, immediately call confirm_employee_identity with that
            same ID — this locks the call to that employee. Every leave,
            balance, insurance, and scheme tool will refuse to run for
            any other employee ID for the rest of the call, even if the
            caller mentions one — do not try to work around this by
            re-verifying a different ID mid-call unless the caller
            explicitly says they are a different person and you restart
            verification from the beginning for them. If they say no to
            the readback, ask for the correct employee ID and verify
            again. Never look someone up by name — always by employee
            ID. If a caller gives a name instead of an ID, ask them for
            their employee ID.

            If get_employee_by_id finds no match for a spoken ID, do not
            just ask the caller to repeat themselves — speech misrecognition
            (e.g. hearing "1015" as "1017") is a common cause and repeating
            rarely fixes it. Instead offer them the option to enter their
            employee ID using their phone's keypad, and if they agree, call
            collect_employee_id_via_keypad. Use exactly what it returns as
            the employee ID and call get_employee_by_id again with it.

            Leave requests — checking policy or balance: there are 8
            possible leave types — Casual Leave, Sick Leave, Maternity
            Leave, Paternity Leave, Comp Off, Bereavement Leave, Short
            Leave (hourly or half-day), and Leave Without Pay. If a
            caller asks about "leave" or "my balance" without saying
            which kind, call list_leave_types and ask them which one they
            mean before answering. Once they specify a type, use
            get_leave_balance for that one type, or get_all_leave_balances
            if they want a full summary across all types that apply to
            them. For policy questions (how many days, eligibility, how
            it works), use get_leave_policy. Maternity only applies to
            employees enrolled in that scheme, and Paternity only to male
            employees — if a type does not apply to the caller, say so
            plainly, without guessing why.

            Leave requests — actually applying for time off: when a
            caller wants to take leave on specific dates (for example
            "I want sick leave on August 27 and 28"), this is a two-step
            flow:
              1. Convert whatever dates they say into YYYY-MM-DD format
                 (assume the current year unless they say otherwise), then
                 call check_leave_availability with their employee ID,
                 leave type, start date, and end date. Read back exactly
                 what it reports — how many days are available and how
                 many are being requested — and ask the caller if they
                 are ready to go ahead. If it reports there is not enough
                 balance, say so plainly and do not offer to submit it.
              2. Only after the caller explicitly confirms yes, ask them
                 for a brief reason if they have not already given one,
                 then call confirm_leave_request with the same employee
                 ID, leave type, and dates, plus the reason. This is what
                 actually records the request, updates their balance, and
                 emails HR — do not call it before the caller has said
                 yes, and do not call check_leave_availability and treat
                 that alone as submission. After confirm_leave_request
                 succeeds, tell the caller their request has been
                 submitted and HR has been notified by email.

            If a caller's answer doesn't make sense mid-flow (for example
            you asked which leave type and got something garbled or
            unrelated back — this can happen with phone call audio),
            do not abandon what you were doing and give a generic "how
            can I help" response. Stay on the exact question you asked,
            say you didn't quite catch that, and ask it again in a
            slightly different way. Only give up on a specific step and
            ask what they need generally if this happens repeatedly (three
            or more times) on the same question.

            You can also look up an employee's department and designation,
            and scheme enrollments (like EPF, Maternity Benefit, or
            Paternity Benefit), using your tools. Only answer from what
            the tools return — never guess an employee's details.

            Insurance: the company offers 3 office health insurance
            plans. If a caller asks generally what insurance is
            available, use list_insurance_plans and name all 3 with
            their coverage amounts. If a caller asks about their own
            insurance, use get_insurance_info with their employee ID —
            it tells you how many plans exist in total, which ones they
            personally have applied for, and which one(s) they have
            claimed. Report exactly what the tool returns; having
            applied for fewer than all 3, or claimed none, is normal and
            not a problem to flag.

            Talking to the coordinator: if the caller says something like
            "I need to talk to the coordinator" or "connect me to a real
            person", do not just transfer them immediately. First say
            you'll forward the call to the coordinator and ask them to
            say "yes, proceed" to confirm. Only once they explicitly
            confirm — a plain "yes" is enough, do not require the exact
            phrase — call transfer_to_coordinator. That tool dials the
            coordinator into this same call and tells you whether they
            answered. If they answered, say a brief single line like
            "Connecting you now" and then stop talking — do not keep
            responding to what the caller and coordinator say to each
            other, and do not call end_call while they're talking. If
            the tool reports the coordinator did not answer, tell the
            caller plainly that the coordinator isn't available right
            now, and ask whether they'd like you to try again immediately
            or would rather call back later — do not claim you connected
            them if you did not.

            Your responses are concise, to the point, and without any
            complex formatting or punctuation including emojis, asterisks,
            or other symbols. You are professional, warm, and clear.

            Ending the call: once the caller has nothing further and you've
            said a closing goodbye (for any kind of call — a leave request,
            a balance check, an insurance question, anything), call
            end_call right after that goodbye. This hangs up the phone
            line so the call doesn't stay open and billing after the
            conversation is actually finished. Only call it once, and only
            after your goodbye has been said — never mid-conversation."""


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


class Assistant(Agent):
    def __init__(self, job_ctx: agents.JobContext, call_metadata: dict | None = None) -> None:
        self._job_ctx = job_ctx
        today_str = date.today().strftime("%A, %B %-d, %Y")
        call_metadata = call_metadata or {}
        call_type = call_metadata.get("call_type")

        if call_type == "leave_verification":
            instructions = OUTBOUND_LEAVE_VERIFICATION_INSTRUCTIONS.format(
                today_str=today_str,
                employee_name=call_metadata.get("employee_name", "there"),
                employee_id=call_metadata.get("employee_id", ""),
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
        file, or when the caller explicitly asks to type or key in their ID.
        Prompts the caller to enter their 4-digit employee ID on their
        phone keypad, reads it back for confirmation, and returns the digit string."""
        result = await GetDtmfTask(
            num_digits=4,
            chat_ctx=context.session.chat_ctx.copy(
                exclude_instructions=True,
                exclude_function_call=True,
            ),
            ask_for_confirmation=True,
            extra_instructions=(
                "Ask the caller to enter their 4-digit employee ID using "
                "their phone keypad, or say it slowly one digit at a time "
                "if they'd rather speak it. Read the digits back to "
                "confirm before finishing."
            ),
        )
        return result.user_input

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

    tts_voice = os.environ.get("TTS_VOICE", "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc")
    tts_model = os.environ.get("TTS_MODEL", "cartesia/sonic-3")
    llm_model = os.environ.get("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it")
    stt_lang = os.environ.get("DEEPGRAM_LANGUAGE", "en")

    # Engines: Direct Deepgram STT/TTS (avoids 429) + FallbackLLM (Cloud -> Ollama)
    stt_engine = STT(api_key=DEEPGRAM_API_KEY, model="nova-3", language=stt_lang)
    cloud_llm = inference.LLM(model=llm_model)
    local_ollama = OllamaLLM(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    llm_engine = FallbackLLM(primary_llm=cloud_llm, fallback_llm=local_ollama)
    tts_engine = DeepgramTTS(api_key=DEEPGRAM_API_KEY)

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

    def _log_state(reason: str):
        logger.debug(f"Watchdog: {reason} (agent_busy={_agent_busy}).")

    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        nonlocal _last_activity
        _last_activity = time.monotonic()
        if getattr(ev, "is_final", False) and getattr(ev, "transcript", ""):
            logger.info(f"🗣️ [User Spoke]: \"{ev.transcript}\"")
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

    await session.start(
        room=ctx.room,
        agent=Assistant(ctx, call_metadata=call_metadata),
        room_options=room_opts,
    )

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
    else:
        await session.generate_reply(
            instructions="Greet the caller and ask for their employee ID to get started."
        )


if __name__ == "__main__":
    agents.cli.run_app(server)