"""
services/asterisk_bridge.py

Asterisk REST Interface (ARI) to LiveKit Audio Bridge Service
==============================================================
Bridges live audio bidirectionally between Asterisk PBX (via ARI Stasis + External Media)
and LiveKit Cloud (as an RTC participant) without requiring LiveKit SIP trunking.

Protocol: Asterisk REST Interface (ARI) + AudioSocket (TCP / External Media)
-----------------------------------------------------------------------------
1. Asterisk Dialplan routes incoming call to `Stasis(hr_voicebot)`.
2. Python ARI Service receives `StasisStart` event over ARI WebSocket.
3. Python ARI Service calls ARI REST API to:
   - Answer incoming caller channel.
   - Create an `externalMedia` audio channel pointing to Python's TCP AudioSocket port.
   - Create an ARI Mixing Bridge and add both channels to it.
4. Bidirectional audio flows between Asterisk externalMedia TCP socket and LiveKit RTC room.
   - Inbound audio: 8kHz PCM (Asterisk) → Upsampled to 48kHz PCM → LiveKit AudioSource.
   - Outbound audio: 48kHz PCM (LiveKit) → Downsampled to 8kHz PCM → Asterisk TCP socket.
5. On hangup (`StasisEnd`), ARI bridge and LiveKit room session are destroyed cleanly.
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
from typing import Optional, Dict, Any, Tuple

import aiohttp
import numpy as np
from dotenv import load_dotenv

# LiveKit Server API & WebRTC RTC SDK
from livekit import api, rtc

# Local configuration
from config import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    WORKER_AGENT_NAME,
    AST_BRIDGE_HOST,
    AST_BRIDGE_ADVERTISE_HOST,
    AST_BRIDGE_PORT,
    AST_SAMPLE_RATE,
    AST_ROOM_PREFIX,
    ARI_URL,
    ARI_WS_URL,
    ARI_USER,
    ARI_PASSWORD,
    ARI_APP_NAME,
)

logger = logging.getLogger("asterisk_ari_bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Signal processing resampler check
try:
    from scipy.signal import resample_poly
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    logger.warning(
        "⚠️ scipy is not installed. Falling back to basic numpy interpolation (may cause audio aliasing). "
        "Install scipy (`pip install scipy`) for optimal STT and audio quality."
    )

# ---------------------------------------------------------------------------
# AudioSocket Protocol Constants
# ---------------------------------------------------------------------------
TYPE_HANGUP = 0x00
TYPE_UUID   = 0x01
TYPE_AUDIO  = 0x10

HEADER_SIZE = 3
FRAME_DURATION_MS = 20  # 20ms audio frames

INBOUND_SAMPLE_RATE  = AST_SAMPLE_RATE  # 8000 Hz from Asterisk slin
OUTBOUND_SAMPLE_RATE = 48000             # 48000 Hz required by LiveKit RTC

SAMPLES_PER_FRAME_8K  = int(INBOUND_SAMPLE_RATE * (FRAME_DURATION_MS / 1000))   # 160 samples
SAMPLES_PER_FRAME_48K = int(OUTBOUND_SAMPLE_RATE * (FRAME_DURATION_MS / 1000))  # 960 samples


# ---------------------------------------------------------------------------
# Audio Resampler (Polyphase Anti-Aliasing Resampling: 8kHz ↔ 48kHz)
# ---------------------------------------------------------------------------
class AudioResampler:
    """Polyphase audio resampler with scipy anti-aliasing filter or numpy fallback."""

    @staticmethod
    def resample_8k_to_48k(pcm_8k: bytes) -> bytes:
        """Upsamples 8kHz 16-bit signed PCM to 48kHz PCM."""
        if not pcm_8k:
            return b""
        audio_8k = np.frombuffer(pcm_8k, dtype=np.int16)
        if len(audio_8k) == 0:
            return b""

        if HAS_SCIPY:
            # Polyphase upsampling 1 -> 6 (8000 Hz * 6 = 48000 Hz) with low-pass filter
            audio_48k = resample_poly(audio_8k, 6, 1).astype(np.int16)
        else:
            # 6x linear interpolation (fallback)
            x_old = np.arange(len(audio_8k))
            x_new = np.linspace(0, len(audio_8k) - 1, len(audio_8k) * 6)
            audio_48k = np.interp(x_new, x_old, audio_8k).astype(np.int16)

        return audio_48k.tobytes()

    @staticmethod
    def resample_48k_to_8k(pcm_48k: bytes) -> bytes:
        """Downsamples 48kHz 16-bit signed PCM to 8kHz PCM."""
        if not pcm_48k:
            return b""
        audio_48k = np.frombuffer(pcm_48k, dtype=np.int16)
        if len(audio_48k) == 0:
            return b""

        if HAS_SCIPY:
            # Polyphase decimation 6 -> 1 (48000 Hz / 6 = 8000 Hz) with low-pass anti-aliasing filter
            audio_8k = resample_poly(audio_48k, 1, 6).astype(np.int16)
        else:
            # Decimate 6:1 (fallback: take every 6th sample)
            audio_8k = audio_48k[::6].astype(np.int16)

        return audio_8k.tobytes()


# ---------------------------------------------------------------------------
# LiveKit Room Bridge Participant
# ---------------------------------------------------------------------------
class LiveKitBridgeSession:
    """
    Manages one active Asterisk ARI call session:
    1. Creates a LiveKit room & dispatches the agent worker.
    2. Connects as a participant to the room.
    3. Bridges audio: Asterisk (TCP Socket) ↔ LiveKit (RTC AudioTrack).
    """

    def __init__(
        self,
        call_uuid: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        caller_id: str = "asterisk_caller",
    ) -> None:
        self.call_uuid = call_uuid
        self.reader = reader
        self.writer = writer
        self.caller_id = caller_id

        self.room_name = f"{AST_ROOM_PREFIX}-{uuid.uuid4().hex[:8]}"
        self.lk_room: Optional[rtc.Room] = None
        self.audio_source: Optional[rtc.AudioSource] = None
        self.running = True

    async def start(self) -> None:
        """Main lifecycle of the audio session."""
        logger.info(f"📞 [Session {self.call_uuid}] Starting ARI LiveKit bridge session for room: {self.room_name}")
        try:
            # 1. Create Room & Dispatch Agent via LiveKit API
            await self._dispatch_agent()

            # 2. Join Room as Participant
            await self._connect_livekit_room()

            # 3. Start bidirectional audio loops concurrently
            await asyncio.gather(
                self._asterisk_to_livekit_loop(),
                self._livekit_to_asterisk_loop(),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.error(f"❌ [Session {self.call_uuid}] Bridge session error: {exc}", exc_info=True)
        finally:
            await self.close()

    async def _dispatch_agent(self) -> None:
        """Uses LiveKit Server API to create room and dispatch the agent worker."""
        logger.info(f"🚀 [Session {self.call_uuid}] Dispatching agent '{WORKER_AGENT_NAME}' to room '{self.room_name}'")
        lk_api = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        try:
            dispatch_req = api.CreateAgentDispatchRequest(
                agent_name=WORKER_AGENT_NAME,
                room=self.room_name,
                metadata=json.dumps({"caller_id": self.caller_id, "source": "asterisk_ari"}),
            )
            await lk_api.agent_dispatch.create_dispatch(dispatch_req)
            logger.info(f"✅ [Session {self.call_uuid}] Agent dispatch request sent successfully.")
        finally:
            await lk_api.aclose()

    async def _connect_livekit_room(self) -> None:
        """Connects to LiveKit room as an RTC Participant."""
        token = (
            api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(f"sip_{self.caller_id}")
            .with_name(f"Caller {self.caller_id}")
            .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
            .to_jwt()
        )

        self.lk_room = rtc.Room()
        await self.lk_room.connect(LIVEKIT_URL, token)
        logger.info(f"✅ [Session {self.call_uuid}] Connected to LiveKit room '{self.room_name}' as participant.")

        # Create AudioSource for publishing Asterisk microphone audio (48kHz Mono)
        self.audio_source = rtc.AudioSource(OUTBOUND_SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("asterisk_mic", self.audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.lk_room.local_participant.publish_track(track, options)
        logger.info(f"🎙️ [Session {self.call_uuid}] Asterisk audio track published to room.")

    async def _asterisk_to_livekit_loop(self) -> None:
        """Reads 8kHz PCM frames from Asterisk External Media socket, resamples to 48kHz, pushes to LiveKit."""
        logger.info(f"🔄 [Session {self.call_uuid}] Asterisk → LiveKit audio loop active.")
        while self.running:
            try:
                # Read AudioSocket 3-byte header or raw PCM frame
                header = await self.reader.readexactly(HEADER_SIZE)
                msg_type, msg_len = struct.unpack(">BH", header)

                if msg_type == TYPE_HANGUP or msg_len == 0:
                    logger.info(f"📴 [Session {self.call_uuid}] Asterisk sent HANGUP signal.")
                    break

                # Read payload
                payload = await self.reader.readexactly(msg_len)

                if msg_type == TYPE_AUDIO and self.audio_source:
                    # Upsample 8kHz -> 48kHz PCM
                    pcm_48k = AudioResampler.resample_8k_to_48k(payload)

                    # Capture frame into LiveKit AudioSource
                    frame = rtc.AudioFrame(
                        data=pcm_48k,
                        sample_rate=OUTBOUND_SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=SAMPLES_PER_FRAME_48K,
                    )
                    await self.audio_source.capture_frame(frame)

            except asyncio.IncompleteReadError:
                logger.info(f"🔌 [Session {self.call_uuid}] External media socket closed.")
                break
            except Exception as exc:
                logger.warning(f"⚠️ [Session {self.call_uuid}] Audio read error: {exc}")
                break

        self.running = False

    async def _livekit_to_asterisk_loop(self) -> None:
        """Listens for Agent audio track in LiveKit room, downsamples 48kHz -> 8kHz, writes to Asterisk socket."""
        logger.info(f"🔄 [Session {self.call_uuid}] LiveKit → Asterisk audio loop active.")

        # Wait for agent participant audio track
        agent_track: Optional[rtc.RemoteAudioTrack] = None
        while self.running and not agent_track:
            for participant in self.lk_room.remote_participants.values():
                for pub in participant.track_publications.values():
                    if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                        agent_track = pub.track
                        logger.info(f"🎧 [Session {self.call_uuid}] Subscribed to agent audio track: {pub.track.sid}")
                        break
            if not agent_track:
                await asyncio.sleep(0.1)

        if not agent_track or not self.running:
            return

        audio_stream = rtc.AudioStream(agent_track)
        async for event in audio_stream:
            if not self.running:
                break
            try:
                # Downsample 48kHz -> 8kHz
                pcm_8k = AudioResampler.resample_48k_to_8k(event.frame.data.tobytes())

                if pcm_8k:
                    # Construct AudioSocket header (TYPE_AUDIO = 0x10)
                    header = struct.pack(">BH", TYPE_AUDIO, len(pcm_8k))
                    self.writer.write(header + pcm_8k)
                    await self.writer.drain()
            except Exception as exc:
                logger.warning(f"⚠️ [Session {self.call_uuid}] Audio write error: {exc}")
                break

    async def close(self) -> None:
        """Cleans up TCP writer and disconnects from LiveKit room."""
        if not self.running:
            return
        self.running = False
        logger.info(f"🧹 [Session {self.call_uuid}] Cleaning up bridge session...")

        try:
            if self.writer and not self.writer.is_closing():
                hangup_header = struct.pack(">BH", TYPE_HANGUP, 0)
                self.writer.write(hangup_header)
                await self.writer.drain()
                self.writer.close()
                await self.writer.wait_closed()
        except Exception:
            pass

        if self.lk_room:
            try:
                await self.lk_room.disconnect()
            except Exception:
                pass
        logger.info(f"✅ [Session {self.call_uuid}] Session closed cleanly.")


# ---------------------------------------------------------------------------
# Asterisk ARI Controller & Media Manager
# ---------------------------------------------------------------------------
class AsteriskARIBridgeManager:
    """
    Manages Asterisk ARI (REST API & WebSocket Stasis events):
    1. Listens for `StasisStart` over ARI WebSocket.
    2. Answers caller channel via ARI REST API.
    3. Spawns an `externalMedia` channel in Asterisk.
    4. Creates an ARI mixing bridge connecting caller channel and media channel.
    5. Manages audio socket streams for active calls.
    """

    def __init__(self) -> None:
        self.auth = aiohttp.BasicAuth(ARI_USER, ARI_PASSWORD)
        self.active_sessions: Dict[str, LiveKitBridgeSession] = {}
        self.active_bridges: Dict[str, str] = {}         # channel_id -> bridge_id
        self.pending_call_uuids: Dict[str, str] = {}     # socket_call_uuid -> channel_id
        self.pending_channels: Dict[str, str] = {}       # channel_id -> socket_call_uuid

    async def start(self) -> None:
        """Starts the external media audio socket server and ARI WebSocket listener."""
        # 1. Start TCP socket server to accept externalMedia connections from Asterisk
        audio_server = await asyncio.start_server(
            self._handle_audio_client, AST_BRIDGE_HOST, AST_BRIDGE_PORT
        )
        addrs = ", ".join(str(sock.getsockname()) for sock in audio_server.sockets)
        logger.info(f"🌐 [Asterisk ARI Media Server] Audio socket listening on {addrs} (Advertised host: {AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT})")

        # 2. Start ARI WebSocket event loop
        asyncio.create_task(audio_server.serve_forever())
        await self._ari_event_loop()

    async def _ari_event_loop(self) -> None:
        """Connects to Asterisk ARI WebSocket and handles Stasis application events."""
        # Scoped strictly to ARI_APP_NAME (removed subscribeAll=true to minimize event noise)
        ws_url = f"{ARI_WS_URL}?app={ARI_APP_NAME}"

        while True:
            try:
                logger.info(f"🔌 Connecting to Asterisk ARI WebSocket: {ARI_WS_URL} (App: {ARI_APP_NAME})")
                async with aiohttp.ClientSession(auth=self.auth) as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info(f"✅ Connected to Asterisk ARI Stasis App '{ARI_APP_NAME}'")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                event = json.loads(msg.data)
                                event_type = event.get("type")
                                logger.debug(f"📩 ARI Event: {event_type}")
                                asyncio.create_task(self._handle_ari_event(session, event))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("⚠️ ARI WebSocket closed. Reconnecting in 3s...")
                                break
            except Exception as exc:
                logger.warning(f"⚠️ ARI Connection error: {exc}. Retrying in 5 seconds...")
                await asyncio.sleep(5)

    async def _handle_ari_event(self, http_session: aiohttp.ClientSession, event: Dict[str, Any]) -> None:
        """Processes ARI events (`StasisStart`, `StasisEnd`, `ChannelDtmfReceived`, etc.)."""
        event_type = event.get("type")

        if event_type == "StasisStart":
            channel = event.get("channel", {})
            channel_id = channel.get("id")
            channel_name = channel.get("name", "")
            caller_num = channel.get("caller", {}).get("number", "unknown")

            # Ignore externalMedia channels created by Python itself
            if "UnicastRTP" in channel_name or "AudioSocket" in channel_name or channel.get("dialplan", {}).get("app_data") == "externalMedia":
                logger.info(f"ℹ️ [ARI Channel {channel_id}] Internal externalMedia channel joined Stasis.")
                return

            logger.info(f"📞 [ARI] New incoming call channel {channel_id} from '{caller_num}'")
            await self._setup_ari_call(http_session, channel_id, caller_num)

        elif event_type == "StasisEnd":
            channel_id = event.get("channel", {}).get("id")
            logger.info(f"📴 [ARI] Call ended / left Stasis: Channel {channel_id}")
            await self._cleanup_ari_call(http_session, channel_id)

        elif event_type == "ChannelDtmfReceived":
            digit = event.get("digit")
            channel_id = event.get("channel", {}).get("id")
            logger.info(f"🔢 [ARI Channel {channel_id}] DTMF Keypress received: {digit}")
            session = self.active_sessions.get(channel_id)
            if session and session.lk_room:
                asyncio.create_task(
                    session.lk_room.local_participant.publish_data(
                        json.dumps({"type": "dtmf", "digit": digit}).encode("utf-8"),
                        reliable=True,
                    )
                )

    async def _setup_ari_call(self, http_session: aiohttp.ClientSession, channel_id: str, caller_num: str) -> None:
        """Answers incoming channel, creates externalMedia channel, and bridges them via ARI REST."""
        try:
            # 1. Answer incoming caller channel
            answer_url = f"{ARI_URL}/channels/{channel_id}/answer"
            async with http_session.post(answer_url) as resp:
                if resp.status not in (200, 204):
                    logger.error(f"Failed to answer channel {channel_id}: {resp.status}")

            # 2. Trigger optional ARI channel recording right after answering
            rec_url = f"{ARI_URL}/channels/{channel_id}/record"
            rec_payload = {
                "name": f"rec_{channel_id}_{int(time.time())}",
                "format": "wav",
                "maxDurationSeconds": 0,
                "maxSilenceSeconds": 0,
                "ifExists": "overwrite",
            }
            try:
                async with http_session.post(rec_url, json=rec_payload) as rec_resp:
                    if rec_resp.status in (200, 201):
                        logger.info(f"🎙️ [ARI Channel {channel_id}] ARI call recording started successfully.")
                    else:
                        logger.warning(f"⚠️ [ARI Channel {channel_id}] Recording response status: {rec_resp.status}")
            except Exception as rec_exc:
                logger.warning(f"⚠️ [ARI Channel {channel_id}] Could not start ARI recording: {rec_exc}")

            # 3. Create External Media channel in Asterisk pointing to Python TCP socket
            ext_media_url = f"{ARI_URL}/channels/externalMedia"
            call_uuid = f"ari_{uuid.uuid4().hex[:8]}"
            self.pending_call_uuids[call_uuid] = channel_id
            self.pending_channels[channel_id] = call_uuid

            payload = {
                "app": ARI_APP_NAME,
                "external_host": f"{AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT}",
                "format": "slin",
                "encapsulation": "audiosocket",
                "transport": "tcp",
                "connection_type": "client",
                "data": call_uuid,
            }

            logger.info(f"🛠️ Creating ARI externalMedia channel for call UUID: {call_uuid} (Host: {AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT})")
            async with http_session.post(ext_media_url, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"Failed to create externalMedia channel: {resp.status} - {body}")
                    return
                ext_channel = await resp.json()
                ext_channel_id = ext_channel.get("id")

            # 4. Create ARI Mixing Bridge & Add both channels
            bridge_url = f"{ARI_URL}/bridges"
            bridge_payload = {"type": "mixing", "name": f"bridge_{call_uuid}"}
            async with http_session.post(bridge_url, json=bridge_payload) as resp:
                if resp.status not in (200, 201):
                    logger.error(f"Failed to create ARI bridge: {resp.status}")
                    return
                bridge = await resp.json()
                bridge_id = bridge.get("id")
                self.active_bridges[channel_id] = bridge_id

            # Add caller channel and externalMedia channel to bridge
            add_chan_url = f"{ARI_URL}/bridges/{bridge_id}/addChannel"
            params = {"channel": f"{channel_id},{ext_channel_id}"}
            async with http_session.post(add_chan_url, params=params) as resp:
                if resp.status not in (200, 204):
                    logger.error(f"Failed to add channels to bridge {bridge_id}: {resp.status}")
                else:
                    logger.info(f"✅ ARI Bridge {bridge_id} active: Channel {channel_id} ↔ External Media {ext_channel_id}")

        except Exception as exc:
            logger.error(f"❌ Error setting up ARI call for channel {channel_id}: {exc}", exc_info=True)

    async def _cleanup_ari_call(self, http_session: aiohttp.ClientSession, channel_id: str) -> None:
        """Cleans up ARI mixing bridge and active LiveKit session on hangup (handles fast hangup gracefully)."""
        # Cleanup pending mappings to avoid orphaned sessions on fast hangup
        pending_uuid = self.pending_channels.pop(channel_id, None)
        if pending_uuid:
            self.pending_call_uuids.pop(pending_uuid, None)
            logger.info(f"🧹 Removed pending call UUID {pending_uuid} for channel {channel_id}")

        bridge_id = self.active_bridges.pop(channel_id, None)
        if bridge_id:
            try:
                delete_bridge_url = f"{ARI_URL}/bridges/{bridge_id}"
                async with http_session.delete(delete_bridge_url) as resp:
                    logger.info(f"🧹 Destroyed ARI mixing bridge {bridge_id} (Status: {resp.status})")
            except Exception as exc:
                logger.warning(f"Error deleting bridge {bridge_id}: {exc}")

        # Close associated LiveKit bridge session if present (by channel_id or call_uuid)
        session = self.active_sessions.pop(channel_id, None)
        if not session and pending_uuid:
            session = self.active_sessions.pop(pending_uuid, None)

        if session:
            await session.close()
        else:
            logger.info(f"ℹ️ No active bridge session found for channel {channel_id} (already closed or fast hangup)")

    async def _handle_audio_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handles incoming TCP socket connection from Asterisk externalMedia."""
        peer = writer.get_extra_info("peername")
        logger.info(f"🔌 Incoming audio socket connection from {peer}")

        try:
            # Read first frame — expected to be TYPE_UUID (0x01)
            header = await reader.readexactly(HEADER_SIZE)
            msg_type, msg_len = struct.unpack(">BH", header)

            call_uuid = f"ari_{uuid.uuid4().hex[:6]}"
            if msg_type == TYPE_UUID and msg_len > 0:
                uuid_bytes = await reader.readexactly(msg_len)
                call_uuid = uuid_bytes.decode("utf-8", errors="ignore").strip()

            logger.info(f"🆔 Audio Socket Handshake OK — Call UUID: {call_uuid}")

            # Lookup channel_id and clean pending maps
            channel_id = self.pending_call_uuids.pop(call_uuid, call_uuid)
            self.pending_channels.pop(channel_id, None)

            # Instantiate and start bridge session
            session = LiveKitBridgeSession(
                call_uuid=call_uuid,
                reader=reader,
                writer=writer,
                caller_id=call_uuid[:8],
            )
            self.active_sessions[channel_id] = session
            await session.start()

        except Exception as exc:
            logger.error(f"❌ Handshake failed for client {peer}: {exc}")
            writer.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    load_dotenv()
    logger.info("Starting Asterisk ARI to LiveKit Audio Bridge Service...")
    manager = AsteriskARIBridgeManager()
    try:
        asyncio.run(manager.start())
    except KeyboardInterrupt:
        logger.info("Asterisk ARI Bridge stopped by user.")
