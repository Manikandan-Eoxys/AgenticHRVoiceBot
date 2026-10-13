"""
goals/g13_coordinator_transfer.py  —  Goal 13: Coordinator Transfer & Call Control

Actions:
  ├── transfer_to_coordinator()  dials human HR coordinator into the call
  └── end_call()                 hangs up the phone line cleanly after closing
"""

import asyncio
import logging
import os
import time
from datetime import timedelta
from typing import Any
from livekit.agents import function_tool, RunContext
from livekit import api

from goals.base import GoalService, GoalMeta
from manage_blocked_numbers import is_number_blocked

logger = logging.getLogger("goals.g13_coordinator")

COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
COORDINATOR_DIAL_TIMEOUT_SECONDS = int(os.environ.get("COORDINATOR_DIAL_TIMEOUT_SECONDS", "30"))
SIP_CALLER_ID = os.environ.get("SIP_CALLER_ID", "+918035315803")
OUTBOUND_TRUNK_ID = os.environ.get("SIP_OUTBOUND_TRUNK_ID", "ST_L4jT3fWb7Zk5")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "http://localhost:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")


class CoordinatorTransferService(GoalService):
    """
    Goal 13 — Coordinator Transfer & Call Control

    Actions:
      ├── transfer_to_coordinator()   dials human HR coordinator into current room
      └── end_call()                  hangs up phone line after goodbye
    """

    meta = GoalMeta(
        id="g13_coordinator",
        name="Coordinator Transfer & Call Flow",
        description="Transfers calls to human HR coordinator and cleanly terminates calls.",
        trigger_phrases=[
            "talk to coordinator",
            "transfer me",
            "speak to human",
            "connect to hr",
            "coordinator",
            "representative",
            "agent",
            "bye",
            "goodbye",
            "end call",
        ],
    )

    def __init__(self, job_ctx: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.job_ctx = job_ctx

    @function_tool
    async def transfer_to_coordinator(self, context: RunContext) -> str:
        """Transfer the call to a human HR coordinator after the caller confirms they wish to proceed."""
        self.log_action("transfer_to_coordinator")
        coord_num = os.environ.get("COORDINATOR_PHONE_NUMBER", COORDINATOR_PHONE_NUMBER)

        if is_number_blocked(coord_num):
            logger.warning(f"🚫 Coordinator transfer blocked: {coord_num} is in blocklist.")
            return "The transfer cannot be completed because the coordinator's number is currently blocked."

        if not self.job_ctx or not getattr(self.job_ctx, "room", None):
            return "Call context is unavailable. Unable to complete live transfer at this moment."

        room = self.job_ctx.room
        identity = f"coordinator-{int(time.time())}"

        # Check recording mode: if cutoff, stop recording before dialing coordinator
        rec_mode = os.environ.get("RECORDING_COORDINATOR_MODE", "record_all").strip().lower()
        if rec_mode == "cutoff" and getattr(context.session, "recorder", None):
            logger.info("🛑 [Transfer] Mode is 'cutoff': stopping recorder before coordinator pick.")
            await context.session.recorder.stop()

        timeout_sec = int(os.environ.get("COORDINATOR_DIAL_TIMEOUT_SECONDS", str(COORDINATOR_DIAL_TIMEOUT_SECONDS)))
        caller_id = os.environ.get("SIP_CALLER_ID", SIP_CALLER_ID)
        trunk_id = os.environ.get("SIP_OUTBOUND_TRUNK_ID", OUTBOUND_TRUNK_ID)

        try:
            async with api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lkapi:
                await lkapi.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk_id,
                        sip_call_to=coord_num,
                        sip_number=caller_id,
                        room_name=room.name,
                        participant_identity=identity,
                        participant_name="Coordinator",
                        wait_until_answered=True,
                        ringing_timeout=timedelta(seconds=timeout_sec),
                    )
                )
        except Exception as e:
            logger.warning(f"⚠️ [Transfer] Coordinator at {coord_num} did not answer or failed: {e}")
            return (
                f"The coordinator did not answer the call within {timeout_sec} seconds. "
                "Apologise to the caller and say: 'The HR coordinator is currently unavailable. "
                "I can raise an HR ticket for your query so they can call you back, or I can help you with anything else. "
                "Which would you prefer?'"
            )

        logger.info(f"✅ [Transfer] Coordinator answered: {coord_num} joined room {room.name}")
        if hasattr(context, "session") and context.session:
            context.session.coordinator_connected = True
            try:
                handle = context.session.say(
                    "Connecting you to the coordinator now.",
                    allow_interruptions=False,
                )
                await handle.wait_for_playout()
                if hasattr(context.session, "input"):
                    context.session.input.set_audio_enabled(False)
                if hasattr(context.session, "output"):
                    context.session.output.set_audio_enabled(False)
            except Exception as se:
                logger.debug(f"[Transfer] Session handoff notice: {se}")

        return (
            "Coordinator connected and the handoff line has already been "
            "spoken by this tool. The agent is now muted at the code "
            "level -- do not attempt to say anything else."
        )

    @function_tool
    async def end_call(self, context: RunContext) -> None:
        """Terminates and hangs up the phone call after saying goodbye."""
        self.log_action("end_call")
        await context.wait_for_playout()
        if self.job_ctx and getattr(self.job_ctx, "room", None):
            try:
                for p in list(self.job_ctx.room.remote_participants.values()):
                    if p.identity.startswith("sip_") or "participant" in p.identity.lower():
                        logger.info(f"📴 [Hangup] Disconnecting participant {p.identity}")
                        await self.job_ctx.room.local_participant.disconnect()
                        break
                await self.job_ctx.disconnect()
            except Exception as e:
                logger.debug(f"[Hangup] Cleanup error (safe to ignore): {e}")
