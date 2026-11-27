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
from goals.base import GoalService, GoalMeta, function_tool, RunContext
from security.call_blocklist import is_number_blocked

logger = logging.getLogger("goals.g13_coordinator")

COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
COORDINATOR_DIAL_TIMEOUT_SECONDS = int(os.environ.get("COORDINATOR_DIAL_TIMEOUT_SECONDS", "30"))
SIP_CALLER_ID = os.environ.get("SIP_CALLER_ID", "+918035315803")



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

        # Check recording mode: if cutoff, stop recording before dialing coordinator
        rec_mode = os.environ.get("RECORDING_COORDINATOR_MODE", "record_all").strip().lower()
        if rec_mode == "cutoff" and getattr(context.session, "recorder", None):
            logger.info("🛑 [Transfer] Mode is 'cutoff': stopping recorder before coordinator pick.")
            await context.session.recorder.stop()

        telephony_session = getattr(context, "session", None)
        if telephony_session is None or not hasattr(telephony_session, "transfer_call"):
            logger.warning("⚠️ [Transfer] No active telephony session found for transfer.")
            return "Call transfer service is currently unavailable."

        if getattr(telephony_session, "_transfer_attempted", False) or getattr(self, "_transfer_attempted", False):
            logger.warning("⚠️ [Transfer] Transfer already attempted in this call session. Preventing duplicate dial loop.")
            return (
                "A transfer was already attempted and the coordinator could not be reached. "
                "Inform the caller that the coordinator is currently unavailable, and offer to raise an HR ticket for a callback."
            )
        telephony_session._transfer_attempted = True
        self._transfer_attempted = True
        # Block barge-in and new STT turns BEFORE speaking the hold notice so a late
        # "Yes" / echo cannot cancel this task before transfer_call() even dials.
        if hasattr(telephony_session, "is_transferring"):
            telephony_session.is_transferring = True

        dial_started = False
        try:
            # Tell the caller what is happening BEFORE dialing, and WAIT until it has been spoken completely
            # (the ring tone starts right after; the transfer must not cut this sentence off).
            tts = getattr(telephony_session, "tts", None)
            try:
                if tts is not None:
                    await tts.speak_text("Please hold for a moment while I connect you to the HR coordinator.")
                    await tts.wait_for_playout(timeout=8.0)
                else:
                    handle = telephony_session.say("Please hold for a moment while I connect you to the HR coordinator.", allow_interruptions=False)
                    await handle.wait_for_playout()
            except asyncio.CancelledError:
                raise
            except Exception as se:
                logger.debug(f"[Transfer] Hold notice: {se}")

            logger.info(f"🔀 [Transfer] Dialing coordinator {coord_num}...")
            dial_started = True
            ok = await telephony_session.transfer_call(coord_num)
        except BaseException:
            if hasattr(telephony_session, "is_transferring"):
                telephony_session.is_transferring = False
            if not dial_started:
                telephony_session._transfer_attempted = False
                self._transfer_attempted = False
            raise

        if not ok:
            if hasattr(telephony_session, "is_transferring"):
                telephony_session.is_transferring = False
            logger.warning(f"⚠️ [Transfer] Transfer to {coord_num} failed.")
            return (
                "The coordinator could not be reached right now. "
                "Apologise to the caller and say: 'The HR coordinator is currently unavailable. "
                "I can raise an HR ticket for your query so they can call you back, or I can help you with anything else. "
                "Which would you prefer?'"
            )

        logger.info(f"✅ [Transfer] Coordinator {coord_num} is now connected to the caller.")
        return (
            "Coordinator connected. The caller is now talking to the coordinator and this AI session is ending. "
            "Do not say anything else."
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
