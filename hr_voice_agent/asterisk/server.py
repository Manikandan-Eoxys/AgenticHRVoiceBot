"""
hr_voice_agent/asterisk/server.py

Asterisk ARI + AudioSocket Telephony Daemon
===========================================
Entry point for Asterisk-based telephony mode in AgenticVoiceBot.
Manages:
  1. AudioSocket TCP Server for bidirectional audio transport.
  2. Asterisk ARI WebSocket connection for Stasis call control.
  3. ExternalMedia channel provisioning and Mixing Bridge lifecycle.
  4. Multiple concurrent incoming calls with completely isolated voice sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import sys
import uuid
import time
from typing import Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv

from config import (
    AST_BRIDGE_HOST,
    AST_BRIDGE_ADVERTISE_HOST,
    AST_BRIDGE_PORT,
    AST_SAMPLE_RATE,
    ARI_URL,
    ARI_WS_URL,
    ARI_USER,
    ARI_PASSWORD,
    ARI_APP_NAME,
)
COORDINATOR_PHONE_NUMBER = os.environ.get("COORDINATOR_PHONE_NUMBER", "+918925355704")
from controllers.agentVoiceController import prewarm_worker_resources
from controllers.global_monitor_fsm import start_global_monitor
from database.db import run_startup_check
from security.call_blocklist import is_number_blocked
from services.monitoring_handler import install_monitoring_handler
from goals.email_approval import start_email_approval_daemon
from asterisk.audiosocket import (
    read_packet,
    HEADER_SIZE,
    TYPE_UUID,
)
from asterisk.voice_session import AsteriskVoiceSession

logger = logging.getLogger("asterisk.server")


class AsteriskServer:
    """
    Main Asterisk Telephony Daemon coordinating ARI Stasis events and AudioSocket media.
    """

    def __init__(self) -> None:
        self.auth = aiohttp.BasicAuth(ARI_USER, ARI_PASSWORD)
        self.active_sessions: Dict[str, AsteriskVoiceSession] = {}   # channel_id -> session
        self.active_bridges: Dict[str, str] = {}                     # channel_id -> bridge_id
        self.pending_call_uuids: Dict[str, str] = {}                 # call_uuid -> channel_id
        self.pending_channels: Dict[str, str] = {}                   # channel_id -> call_uuid
        self.channel_callers: Dict[str, str] = {}                    # channel_id -> caller_num
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._server: Optional[asyncio.Server] = None
        self._running = True

    async def start(self) -> None:
        """Starts the AudioSocket TCP server and ARI WebSocket listener."""
        logger.info(f"🚀 Initializing Asterisk Telephony Daemon (App: {ARI_APP_NAME})...")

        # Start Global Monitor FSM thread & background scheduler
        start_global_monitor()
        try:
            prewarm_worker_resources(run_startup_check)
        except Exception:
            logger.exception("Worker prewarm completed with warnings (continuing).")

        # Start email approval daemon for manager leave workflows
        try:
            start_email_approval_daemon(poll_interval=15)
        except Exception as e:
            logger.warning(f"Could not start email approval daemon: {e}")

        self.http_session = aiohttp.ClientSession(auth=self.auth)

        # 1. Start AudioSocket TCP server
        self._server = await asyncio.start_server(
            self._handle_audiosocket_client,
            AST_BRIDGE_HOST,
            AST_BRIDGE_PORT,
        )
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        logger.info(f"🌐 [AudioSocket] TCP server listening on {addrs} (Advertised: {AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT})")

        # 2. Run AudioSocket server and ARI WebSocket event listener concurrently
        await asyncio.gather(
            self._server.serve_forever(),
            self._ari_event_loop(),
        )

    async def _ari_event_loop(self) -> None:
        """Connects to Asterisk ARI WebSocket and handles incoming call events."""
        ws_url = f"{ARI_WS_URL}?app={ARI_APP_NAME}"

        while self._running:
            try:
                logger.info(f"🔌 Connecting to Asterisk ARI WebSocket: {ws_url}")
                async with self.http_session.ws_connect(ws_url) as ws:
                    logger.info(f"✅ Connected to Asterisk ARI Stasis Application '{ARI_APP_NAME}'")
                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            event_type = event.get("type")
                            logger.debug(f"📩 ARI Event: {event_type}")
                            asyncio.create_task(self._handle_ari_event(event))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("⚠️ ARI WebSocket connection closed. Reconnecting in 3s...")
                            break
            except Exception as e:
                logger.warning(f"⚠️ ARI connection error: {e}. Retrying in 4 seconds...")
                await asyncio.sleep(4)

    async def _handle_ari_event(self, event: Dict[str, Any]) -> None:
        """Dispatches ARI events: StasisStart, StasisEnd, ChannelDtmfReceived."""
        event_type = event.get("type")

        if event_type == "StasisStart":
            channel = event.get("channel", {})
            channel_id = channel.get("id")
            channel_name = channel.get("name", "")
            caller_num = channel.get("caller", {}).get("number", "unknown")

            # Ignore internal channels created by Python itself
            if "AudioSocket" in channel_name or "UnicastRTP" in channel_name or channel.get("dialplan", {}).get("app_data") == "externalMedia":
                return

            logger.info(f"📞 [ARI] Incoming call on channel {channel_id} from '{caller_num}'")

            # Check security blocklist before answering
            if is_number_blocked(caller_num):
                logger.warning(f"🚫 [CALL REJECTED] Caller '{caller_num}' is blocked. Hanging up channel {channel_id}.")
                await self._hangup_channel(channel_id)
                return

            self.channel_callers[channel_id] = caller_num
            await self._setup_incoming_call(channel_id, caller_num)

        elif event_type == "StasisEnd":
            channel_id = event.get("channel", {}).get("id")
            logger.info(f"📴 [ARI] Call ended / left Stasis: Channel {channel_id}")
            await self._cleanup_call(channel_id)

        elif event_type == "ChannelDtmfReceived":
            channel_id = event.get("channel", {}).get("id")
            digit = event.get("digit")
            session = self.active_sessions.get(channel_id)
            if session:
                session.on_dtmf_digit(digit)

    async def _setup_incoming_call(self, channel_id: str, caller_num: str) -> None:
        """Answers channel, creates externalMedia channel pointing to AudioSocket, and mixes them."""
        try:
            # 1. Answer channel
            ans_url = f"{ARI_URL}/channels/{channel_id}/answer"
            async with self.http_session.post(ans_url) as resp:
                if resp.status not in (200, 204):
                    logger.error(f"Failed to answer channel {channel_id}: {resp.status}")
                    return

            # 2. Register standard RFC 4122 UUID (required by Asterisk res_audiosocket)
            call_uuid = str(uuid.uuid4())
            self.pending_call_uuids[call_uuid] = channel_id
            self.pending_channels[channel_id] = call_uuid

            # 3. Create externalMedia channel in Asterisk pointing to Python AudioSocket TCP port
            ext_url = f"{ARI_URL}/channels/externalMedia"
            payload = {
                "app": ARI_APP_NAME,
                "external_host": f"{AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT}",
                "format": "slin",  # 8000 Hz 16-bit linear PCM
                "encapsulation": "audiosocket",
                "transport": "tcp",
                "connection_type": "client",
                "data": call_uuid,
            }
            logger.info(f"🛠️ Creating Asterisk externalMedia channel for UUID: {call_uuid}")
            async with self.http_session.post(ext_url, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"Failed to create externalMedia channel: {resp.status} - {body}")
                    return
                ext_chan = await resp.json()
                ext_channel_id = ext_chan.get("id")

            # 4. Create Mixing Bridge
            bridge_url = f"{ARI_URL}/bridges"
            bridge_payload = {"type": "mixing", "name": f"bridge_{call_uuid}"}
            async with self.http_session.post(bridge_url, json=bridge_payload) as resp:
                if resp.status not in (200, 201):
                    logger.error(f"Failed to create bridge: {resp.status}")
                    return
                bridge = await resp.json()
                bridge_id = bridge.get("id")
                self.active_bridges[channel_id] = bridge_id

            # 5. Add caller and externalMedia to bridge
            add_url = f"{ARI_URL}/bridges/{bridge_id}/addChannel"
            params = {"channel": f"{channel_id},{ext_channel_id}"}
            async with self.http_session.post(add_url, params=params) as resp:
                if resp.status in (200, 204):
                    logger.info(f"✅ [ARI Bridge {bridge_id}] Caller {channel_id} ↔ AudioSocket {ext_channel_id} bridged successfully.")
                else:
                    logger.error(f"Failed to bridge channels: {resp.status}")

        except Exception as e:
            logger.error(f"❌ Error setting up call {channel_id}: {e}", exc_info=True)

    async def _handle_audiosocket_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handles TCP connection from Asterisk externalMedia."""
        peer = writer.get_extra_info("peername")
        logger.info(f"🔌 AudioSocket client connected from {peer}")

        try:
            # 1. Read first packet (expected TYPE_UUID = 0x01)
            msg_type, payload = await read_packet(reader)
            call_uuid = ""
            if msg_type == TYPE_UUID and payload:
                if len(payload) == 16:
                    try:
                        call_uuid = str(uuid.UUID(bytes=payload))
                    except Exception:
                        call_uuid = payload.hex()
                else:
                    call_uuid = payload.decode("utf-8", errors="ignore").strip()

            if not call_uuid:
                call_uuid = next(iter(self.pending_call_uuids.keys()), str(uuid.uuid4()))

            logger.info(f"🆔 AudioSocket Handshake OK — Call UUID: {call_uuid}")

            channel_id = self.pending_call_uuids.pop(call_uuid, call_uuid)
            self.pending_channels.pop(channel_id, None)
            caller_num = self.channel_callers.get(channel_id, "unknown")

            # 2. Instantiate and launch isolated voice session for this call
            session = AsteriskVoiceSession(
                call_uuid=call_uuid,
                channel_id=channel_id,
                caller_id=caller_num,
                reader=reader,
                writer=writer,
                sample_rate=AST_SAMPLE_RATE,
                ari_client_session=self.http_session,
            )
            self.active_sessions[channel_id] = session
            await session.start()

        except Exception as e:
            logger.error(f"❌ AudioSocket client handler error: {e}", exc_info=True)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _cleanup_call(self, channel_id: str) -> None:
        """Destroys ARI bridge and active session."""
        bridge_id = self.active_bridges.pop(channel_id, None)
        if bridge_id and self.http_session:
            try:
                del_url = f"{ARI_URL}/bridges/{bridge_id}"
                async with self.http_session.delete(del_url) as resp:
                    logger.info(f"🧹 Destroyed ARI bridge {bridge_id}: {resp.status}")
            except Exception as e:
                logger.debug(f"Bridge cleanup notice: {e}")

        session = self.active_sessions.pop(channel_id, None)
        if session:
            await session.hangup()

        self.channel_callers.pop(channel_id, None)
        pending_uuid = self.pending_channels.pop(channel_id, None)
        if pending_uuid:
            self.pending_call_uuids.pop(pending_uuid, None)

    async def _hangup_channel(self, channel_id: str) -> None:
        if self.http_session:
            try:
                del_url = f"{ARI_URL}/channels/{channel_id}"
                await self.http_session.delete(del_url)
            except Exception:
                pass


def run_asterisk_server() -> None:
    """Entry point function to run the Asterisk server."""
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s %(name)s - %(message)s",
        force=True,
    )
    server = AsteriskServer()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Asterisk server stopped by user.")


if __name__ == "__main__":
    run_asterisk_server()
