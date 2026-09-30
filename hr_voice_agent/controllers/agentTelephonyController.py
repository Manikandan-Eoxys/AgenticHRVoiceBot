"""
agentTelephonyController.py

SIP trunk, outbound dialing, and call forwarding management for the HR Voice Agent.

Responsibilities:
  - Initiating outbound SIP calls (coordinator transfer, leave verification calls,
    outbound reminder calls)
  - Managing ringback tone lifecycle during dialing
  - Blocking calls to numbers in the blocklist before dialing
  - Encapsulating all LiveKit SIP API calls (create_sip_participant,
    create_sip_trunk, etc.) so agent.py is decoupled from the raw API

Environment variables consumed (all from config.py / .env):
  COORDINATOR_PHONE_NUMBER    — default coordinator to transfer to
  LIVEKIT_OUTBOUND_TRUNK_ID   — SIP trunk ID for outbound
  SIP_CALLER_ID               — caller ID presented on outbound calls
  LIVEKIT_URL / API_KEY / API_SECRET
"""

import asyncio
import logging
import os
import time
from datetime import timedelta
from typing import Optional

from livekit import api, rtc

from config import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
)
from security.call_blocklist import is_number_blocked

logger = logging.getLogger("controllers.agent_telephony")


# ---------------------------------------------------------------------------
# Module-level env defaults (overridden by .env)
# ---------------------------------------------------------------------------

COORDINATOR_PHONE_NUMBER: str = os.environ.get(
    "COORDINATOR_PHONE_NUMBER", "+918925355704"
)
OUTBOUND_TRUNK_ID: str = os.environ.get(
    "LIVEKIT_OUTBOUND_TRUNK_ID", "ST_ZoFAnNLxoPce"
)
SIP_CALLER_ID: str = os.environ.get("SIP_CALLER_ID", "+918031451001")
COORDINATOR_DIAL_TIMEOUT: int = int(
    os.environ.get("COORDINATOR_DIAL_TIMEOUT_SECONDS", "30")
)


# ---------------------------------------------------------------------------
# Ringback tone helper (delegated to here from agent.py)
# ---------------------------------------------------------------------------

async def _ringback_loop(
    room: rtc.Room, stop_event: asyncio.Event, sample_rate: int = 48000
) -> None:
    """Plays a repeating ring tone into the room while an outbound call is dialing."""
    import numpy as np

    publication = None
    try:
        source = rtc.AudioSource(sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("ringback-tone", source)
        opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        publication = await room.local_participant.publish_track(track, opts)

        n_ring = int(sample_rate * 1.0)
        n_silence = int(sample_rate * 3.0)
        t_ring = np.arange(n_ring) / sample_rate
        ring = (4500 * np.sin(2 * 3.14159265 * 440 * t_ring)).astype(np.int16)
        silence = np.zeros(n_silence, dtype=np.int16)
        cycle = np.concatenate([ring, silence])

        frame = rtc.AudioFrame.create(sample_rate, 1, len(cycle))
        while not stop_event.is_set():
            np.copyto(np.frombuffer(frame.data, dtype=np.int16), cycle)
            await source.capture_frame(frame)
            for _ in range(40):
                if stop_event.is_set():
                    break
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Ringback tone failed (non-fatal).")
    finally:
        if publication is not None:
            try:
                await room.local_participant.unpublish_track(publication.sid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AgentTelephonyController
# ---------------------------------------------------------------------------

class AgentTelephonyController:
    """
    Manages all telephony operations for the HR Voice Agent.

    One instance is sufficient for the entire worker process — it holds no
    per-call state.  All methods that need a room/session accept them as args.

    Typical usage:
        tc = AgentTelephonyController()

        # Transfer caller to coordinator:
        result = await tc.transfer_to_coordinator(room, session)

        # Dial a specific phone number into a room:
        ok = await tc.dial_into_room(
            room=room,
            phone_number="+919876543210",
            participant_name="Employee",
            trunk_id=OUTBOUND_TRUNK_ID,
        )
    """

    def __init__(
        self,
        outbound_trunk_id: str = OUTBOUND_TRUNK_ID,
        coordinator_phone: str = COORDINATOR_PHONE_NUMBER,
        caller_id: str = SIP_CALLER_ID,
        dial_timeout: int = COORDINATOR_DIAL_TIMEOUT,
    ) -> None:
        self.outbound_trunk_id = outbound_trunk_id
        self.coordinator_phone = coordinator_phone
        self.caller_id = caller_id
        self.dial_timeout = dial_timeout

    # ------------------------------------------------------------------
    # Transfer to Coordinator
    # ------------------------------------------------------------------

    async def transfer_to_coordinator(
        self,
        room: rtc.Room,
        session=None,  # AgentSession (typed loosely to avoid circular import)
    ) -> tuple[bool, str]:
        """
        Dials the coordinator into the active call room with ringback audio.

        Returns:
            (True, identity)  — coordinator answered and is now in the room
            (False, reason)   — call failed / timed out / blocked
        """
        if is_number_blocked(self.coordinator_phone):
            reason = (
                f"Transfer blocked — coordinator number {self.coordinator_phone} "
                "is in the call blocklist."
            )
            logger.warning(f"🚫 [Telephony] {reason}")
            return False, reason

        # Stop recording if in 'cutoff' mode before dialing coordinator
        rec_mode = os.environ.get("RECORDING_COORDINATOR_MODE", "record_all").lower()
        if rec_mode == "cutoff" and session and getattr(session, "recorder", None):
            logger.info("🛑 [Telephony] Mode=cutoff: stopping recorder before coordinator pickup.")
            await session.recorder.stop()

        identity = f"coordinator-{int(time.time())}"
        stop_ringback = asyncio.Event()
        ringback_task = asyncio.create_task(
            _ringback_loop(room, stop_ringback)
        )

        try:
            async with api.LiveKitAPI(
                LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
            ) as lkapi:
                await lkapi.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=self.outbound_trunk_id,
                        sip_call_to=self.coordinator_phone,
                        sip_number=self.caller_id,
                        room_name=room.name,
                        participant_identity=identity,
                        participant_name="Coordinator",
                        wait_until_answered=True,
                        ringing_timeout=timedelta(seconds=self.dial_timeout),
                    )
                )
        except Exception as e:
            stop_ringback.set()
            await ringback_task
            reason = f"Coordinator did not answer or call failed: {e}"
            logger.warning(f"⚠️ [Telephony] {reason}")
            return False, reason

        stop_ringback.set()
        await ringback_task
        logger.info(
            f"✅ [Telephony] Coordinator '{identity}' answered and joined room '{room.name}'."
        )
        return True, identity

    # ------------------------------------------------------------------
    # Generic outbound dial
    # ------------------------------------------------------------------

    async def dial_into_room(
        self,
        room: rtc.Room,
        phone_number: str,
        participant_name: str = "Participant",
        trunk_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> tuple[bool, str]:
        """
        Dials an arbitrary phone number into an active LiveKit room.

        Returns:
            (True, identity)  — participant answered
            (False, reason)   — failed or blocked
        """
        if is_number_blocked(phone_number):
            reason = f"Dial blocked — number {phone_number} is in the blocklist."
            logger.warning(f"🚫 [Telephony] {reason}")
            return False, reason

        trunk = trunk_id or self.outbound_trunk_id
        identity = f"outbound-{int(time.time())}"
        try:
            async with api.LiveKitAPI(
                LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
            ) as lkapi:
                await lkapi.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk,
                        sip_call_to=phone_number,
                        sip_number=self.caller_id,
                        room_name=room.name,
                        participant_identity=identity,
                        participant_name=participant_name,
                        wait_until_answered=True,
                        ringing_timeout=timedelta(seconds=timeout_seconds),
                    )
                )
            logger.info(
                f"✅ [Telephony] Outbound call to {phone_number} answered "
                f"(identity='{identity}', room='{room.name}')."
            )
            return True, identity
        except Exception as e:
            reason = f"Outbound call to {phone_number} failed: {e}"
            logger.warning(f"⚠️ [Telephony] {reason}")
            return False, reason

    # ------------------------------------------------------------------
    # SIP Trunk Info (read-only helpers)
    # ------------------------------------------------------------------

    def trunk_info(self) -> dict:
        """Returns current telephony configuration as a plain dict."""
        return {
            "outbound_trunk_id": self.outbound_trunk_id,
            "coordinator_phone": self.coordinator_phone,
            "caller_id": self.caller_id,
            "dial_timeout_seconds": self.dial_timeout,
        }
