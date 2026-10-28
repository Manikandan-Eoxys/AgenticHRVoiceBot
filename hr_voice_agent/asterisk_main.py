"""
hr_voice_agent/asterisk_main.py

Asterisk ARI + AudioSocket Telephony Daemon Entrypoint
======================================================
Dedicated service daemon for Asterisk-based telephony backend.
Supports:
  1. Configuration validation on startup
  2. Single-instance initialization of GlobalMonitorFSM, DB prewarming, and background daemons
  3. Real-time bidirectional audio transport via AudioSocket TCP server
  4. ARI Stasis event listener and call orchestration (bridge + externalMedia)
  5. Multi-call isolation with CallContext
  6. DTMF keypad collection, employee verification, RAG, and coordinator transfer
  7. Graceful shutdown on SIGINT and SIGTERM
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Ensure hr_voice_agent directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Load .env
env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file, override=True)
else:
    load_dotenv(override=True)

from config import (
    ARI_URL,
    ARI_WS_URL,
    ARI_USER,
    ARI_PASSWORD,
    ARI_APP_NAME,
    AST_BRIDGE_HOST,
    AST_BRIDGE_ADVERTISE_HOST,
    AST_BRIDGE_PORT,
    AST_SAMPLE_RATE,
    DEEPGRAM_API_KEY,
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
)
from controllers.agentVoiceController import prewarm_worker_resources
from controllers.global_monitor_fsm import start_global_monitor, global_monitor_running
from database.db import run_startup_check
from goals.email_approval import start_email_approval_daemon
from services.monitoring_handler import install_monitoring_handler
from asterisk.server import AsteriskServer

logger = logging.getLogger("asterisk_main")


def validate_config() -> bool:
    """Validates required telephony and model settings before starting."""
    missing = []
    if not ARI_URL:
        missing.append("ARI_URL")
    if not ARI_USER:
        missing.append("ARI_USER")
    if not ARI_PASSWORD:
        missing.append("ARI_PASSWORD")
    if not DEEPGRAM_API_KEY:
        missing.append("DEEPGRAM_API_KEY")

    if missing:
        logger.error(f"❌ Missing required environment configuration: {', '.join(missing)}")
        return False

    logger.info("⚙️ Telephony Configuration Validated:")
    logger.info(f"   • ARI URL:        {ARI_URL} (App: {ARI_APP_NAME})")
    logger.info(f"   • AudioSocket:    {AST_BRIDGE_HOST}:{AST_BRIDGE_PORT} (Advertised: {AST_BRIDGE_ADVERTISE_HOST})")
    logger.info(f"   • Audio Format:   8000 Hz Linear 16-bit PCM (slin)")
    logger.info(f"   • Local LLM:      {OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")
    return True


async def main() -> None:
    # 1. Logging and Monitoring
    try:
        install_monitoring_handler()
    except Exception:
        pass

    logger.info("=" * 70)
    logger.info("🤖 Starting Agentic HR VoiceBot (Asterisk ARI + AudioSocket Telephony)")
    logger.info("=" * 70)

    # 2. Validate configuration
    if not validate_config():
        logger.warning("Continuing with default configuration values.")

    # 3. Start Global Monitor FSM once (shared across all concurrent calls)
    if not global_monitor_running():
        start_global_monitor()
        logger.info("📊 GlobalMonitorFSM started in background thread.")

    # 4. Prewarm shared worker resources (DB and local LLM)
    try:
        prewarm_worker_resources(run_startup_check)
    except Exception as e:
        logger.warning(f"Startup prewarm notice: {e}")

    # 5. Start email approval polling daemon
    try:
        start_email_approval_daemon(poll_interval=15)
        logger.info("📧 Email approval daemon running (interval=15s).")
    except Exception as e:
        logger.warning(f"Email approval daemon notice: {e}")

    # 6. Initialize and run Asterisk Telephony Server
    server = AsteriskServer()

    # 7. Setup signal handling for graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _sig_handler():
        logger.info("🛑 Received termination signal (SIGINT/SIGTERM). Initiating shutdown...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass

    server_task = asyncio.create_task(server.start(), name="AsteriskTelephonyServer")

    # Wait until shutdown signal or server error
    try:
        done, pending = await asyncio.wait(
            [server_task, asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_event.is_set():
            logger.info("🛑 Gracefully shutting down active telephony sessions...")
            server._running = False

            # Hangup all active sessions
            for chan_id, session in list(server.active_sessions.items()):
                try:
                    await session.hangup()
                except Exception as he:
                    logger.debug(f"Hangup notice for {chan_id}: {he}")

            # Cancel main server task
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"❌ Server error: {e}", exc_info=True)
    finally:
        if server.http_session and not server.http_session.closed:
            await server.http_session.close()
        logger.info("✅ Asterisk Telephony Daemon stopped cleanly.")


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        force=True,
    )
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Server exiting.")


if __name__ == "__main__":
    run()
