"""
main.py

Entry point for HR Voice Agent
LiveKit Agents v1.6.6
"""

import json
import logging
import urllib.request

from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)

from livekit.plugins.deepgram import STT, TTS as DeepgramTTS
from ollama_llm import OllamaLLM  # native /api/chat wrapper — truly suppresses think mode
# from livekit.plugins.openai import LLM  # OpenAI-compat endpoint ignores think:false
# from livekit.plugins.google import LLM

from agent import HRAgent

from config import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    DEEPGRAM_API_KEY,
    SYSTEM_PROMPT,   # needed so prewarm can send the REAL prompt, not "hi"
)

# ── Sync scheduler (IFS Cloud + SQL Server background sync) ──────────────────
from integrations.sync_scheduler import start_scheduler, stop_scheduler
import atexit

import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


from controllers import VoiceController

# Instantiate global controller instance
voice_controller = VoiceController()


# ------------------------------------------------------------
# Prewarm — runs ONCE when the worker process starts.
# Delegates initialization & model prewarming to VoiceController.
# ------------------------------------------------------------

def prewarm(proc: JobProcess):
    """
    Called once per worker process before any job is accepted.
    Uses VoiceController to prewarm STT, LLM, TTS, and cache prompts.
    """
    logger.info("[prewarm] Delegating process prewarm to VoiceController...")
    voice_controller.prewarm_pipeline(proc)


# ------------------------------------------------------------
# Entrypoint — runs for EACH new room/session.
# Uses VoiceController to start the session with prewarmed engines.
# ------------------------------------------------------------

async def entrypoint(ctx: JobContext):
    """
    Called when a participant joins a room.
    Delegates room connection, engine setup, and session startup to VoiceController.
    """
    logger.info("[entrypoint] Starting call session via VoiceController...")
    agent_instance = HRAgent()
    await voice_controller.start_session(ctx, agent_instance)
    logger.info("Agent session running")


    # REMOVED: the extra `await session.generate_reply(instructions="Greet...")`
    # call that used to be here. HRAgent.on_enter() already greets the user
    # with a fixed session.say() — no LLM call needed for that. Having BOTH
    # meant every session fired a second, redundant Ollama completion right
    # at startup, competing with the model for no benefit (the fixed greeting
    # is what the user actually heard first anyway).


# ------------------------------------------------------------
# Worker
# ------------------------------------------------------------

async def request_fnc(req):
    logger.info(f"RECEIVED JOB REQUEST: {req.job.id}  Room: {req.job.room.name}")
    await req.accept()


if __name__ == "__main__":

    cli.run_app(

        WorkerOptions(

            entrypoint_fnc=entrypoint,

            prewarm_fnc=prewarm,       # ← NEW: one-time warm-up per process

            # NEW: LiveKit's default (~10s) isn't enough time for prewarm to
            # finish sending the full SYSTEM_PROMPT to Ollama — that alone
            # can take 10-60s depending on model/hardware. Without raising
            # this, LiveKit kills the process mid-warmup (seen as
            # "process exited with non-zero exit code -10" / TimeoutError
            # in supervised_proc.py). 120s gives ample headroom; tune down
            # once you've confirmed how long your actual warm-up takes.
            initialize_process_timeout=120,

            request_fnc=request_fnc,

            ws_url=LIVEKIT_URL,

            api_key=LIVEKIT_API_KEY,

            api_secret=LIVEKIT_API_SECRET,

            agent_name="hr-voice-agent",
        )

    )

