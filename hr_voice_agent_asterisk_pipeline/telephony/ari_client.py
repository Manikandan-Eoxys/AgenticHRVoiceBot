"""
hr_voice_agent/telephony/ari_client.py

Asterisk REST Interface (ARI) Client
====================================
Provides async REST operations and WebSocket event streaming for Asterisk ARI.
Uses aiohttp with HTTP Basic Authentication and automatic reconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

import base64

logger = logging.getLogger("telephony.ari")


class AriClient:
    """
    High-level ARI REST API and WebSocket event streaming client.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8088/ari",
        ws_url: str = "ws://127.0.0.1:8088/ari/events",
        username: str = "asterisk",
        password: str = "asterisk",
        app_name: str = "hr_voicebot",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url
        self.username = username
        self.password = password
        self.app_name = app_name

        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {token}"}
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._running = False
        self._event_handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []

    async def connect(self) -> None:
        """Initializes internal HTTP session."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(headers=self._auth_headers)
        self._running = True

    async def close(self) -> None:
        """Gracefully closes HTTP session and WebSocket task."""
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        logger.info("🛑 ARI client disconnected.")

    def on_event(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Registers a callback for ARI WebSocket events."""
        if handler not in self._event_handlers:
            self._event_handlers.append(handler)

    def start_event_listener(self) -> asyncio.Task:
        """Spawns background task listening for ARI WebSocket events."""
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_event_loop(), name="AriWsListener")
        return self._ws_task

    async def _ws_event_loop(self) -> None:
        """Maintains persistent WebSocket connection to ARI events with auto-reconnect."""
        ws_endpoint = f"{self.ws_url}?app={self.app_name}"
        retry_delay = 2.0

        while self._running:
            try:
                if self._http_session is None or self._http_session.closed:
                    self._http_session = aiohttp.ClientSession(headers=self._auth_headers)

                logger.info(f"🔌 Connecting to ARI WebSocket: {ws_endpoint}")
                async with self._http_session.ws_connect(ws_endpoint) as ws:
                    logger.info(f"✅ Connected to ARI Stasis App '{self.app_name}'")
                    retry_delay = 2.0  # Reset on successful connection

                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = json.loads(msg.data)
                                asyncio.create_task(self._dispatch_event(event))
                            except Exception as parse_err:
                                logger.error(f"Failed to parse ARI event JSON: {parse_err}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("⚠️ ARI WebSocket closed or errored.")
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"⚠️ ARI WebSocket connection failed: {e}. Reconnecting in {retry_delay:.1f}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 15.0)

    async def _dispatch_event(self, event: Dict[str, Any]) -> None:
        for handler in self._event_handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Error in ARI event handler: {e}", exc_info=True)

    # -------------------------------------------------------------------------
    # REST API Methods
    # -------------------------------------------------------------------------

    async def answer_channel(self, channel_id: str) -> bool:
        """Answers an incoming channel."""
        if not self._http_session:
            return False
        url = f"{self.base_url}/channels/{channel_id}/answer"
        try:
            async with self._http_session.post(url) as resp:
                if resp.status in (200, 204):
                    logger.debug(f"Channel {channel_id} answered successfully.")
                    return True
                logger.error(f"Failed to answer channel {channel_id}: status {resp.status}")
                return False
        except Exception as e:
            logger.error(f"Exception answering channel {channel_id}: {e}")
            return False

    async def hangup_channel(self, channel_id: str, reason: str = "normal") -> bool:
        """Hangs up a channel."""
        if not self._http_session:
            return False
        url = f"{self.base_url}/channels/{channel_id}"
        params = {"reason": reason}
        try:
            async with self._http_session.delete(url, params=params) as resp:
                return resp.status in (200, 204, 404)
        except Exception as e:
            logger.debug(f"Notice on hangup channel {channel_id}: {e}")
            return False

    async def create_external_media(
        self,
        external_host: str,
        call_uuid: str,
        format_code: str = "slin",
        encapsulation: str = "audiosocket",
        transport: str = "tcp",
    ) -> Optional[Dict[str, Any]]:
        """
        Creates an externalMedia channel in Asterisk pointing to an AudioSocket host:port.
        """
        if not self._http_session:
            return None
        url = f"{self.base_url}/channels/externalMedia"
        payload = {
            "app": self.app_name,
            "external_host": external_host,
            "format": format_code,
            "encapsulation": encapsulation,
            "transport": transport,
            "connection_type": "client",
            "data": call_uuid,
        }
        try:
            async with self._http_session.post(url, json=payload) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                body = await resp.text()
                logger.error(f"Failed to create externalMedia channel: {resp.status} - {body}")
                return None
        except Exception as e:
            logger.error(f"Exception creating externalMedia: {e}")
            return None

    async def create_mixing_bridge(self, name: Optional[str] = None) -> Optional[str]:
        """Creates an ARI mixing bridge."""
        if not self._http_session:
            return None
        url = f"{self.base_url}/bridges"
        payload: Dict[str, Any] = {"type": "mixing"}
        if name:
            payload["name"] = name
        try:
            async with self._http_session.post(url, json=payload) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return data.get("id")
                logger.error(f"Failed to create mixing bridge: {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Exception creating bridge: {e}")
            return None

    async def add_channel_to_bridge(self, bridge_id: str, channel_ids: List[str] | str) -> bool:
        """Adds one or more channels to a bridge."""
        if not self._http_session:
            return False
        if isinstance(channel_ids, list):
            chan_str = ",".join(channel_ids)
        else:
            chan_str = channel_ids
        url = f"{self.base_url}/bridges/{bridge_id}/addChannel"
        params = {"channel": chan_str}
        try:
            async with self._http_session.post(url, params=params) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"Exception adding channels to bridge {bridge_id}: {e}")
            return False

    async def remove_channel_from_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """Removes a channel from a bridge."""
        if not self._http_session:
            return False
        url = f"{self.base_url}/bridges/{bridge_id}/removeChannel"
        params = {"channel": channel_id}
        try:
            async with self._http_session.post(url, params=params) as resp:
                return resp.status in (200, 204, 404)
        except Exception:
            return False

    async def destroy_bridge(self, bridge_id: str) -> bool:
        """Destroys a bridge."""
        if not self._http_session:
            return False
        url = f"{self.base_url}/bridges/{bridge_id}"
        try:
            async with self._http_session.delete(url) as resp:
                return resp.status in (200, 204, 404)
        except Exception as e:
            logger.debug(f"Notice destroying bridge {bridge_id}: {e}")
            return False

    async def send_dtmf(self, channel_id: str, dtmf: str) -> bool:
        """Sends DTMF tones on a channel."""
        if not self._http_session:
            return False
        url = f"{self.base_url}/channels/{channel_id}/dtmf"
        params = {"dtmf": dtmf}
        try:
            async with self._http_session.post(url, params=params) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"Exception sending DTMF on channel {channel_id}: {e}")
            return False

    async def transfer_channel(self, channel_id: str, endpoint: str) -> bool:
        """
        Transfers channel by originating to target endpoint and redirecting/bridging.
        Uses ARI /channels/{channel_id}/redirect or dial.
        """
        if not self._http_session:
            return False
        # Asterisk ARI channel redirect
        url = f"{self.base_url}/channels/{channel_id}/redirect"
        params = {"endpoint": endpoint}
        try:
            async with self._http_session.post(url, params=params) as resp:
                if resp.status in (200, 204):
                    logger.info(f"✅ Redirected channel {channel_id} to endpoint {endpoint}")
                    return True
                logger.warning(f"ARI redirect returned {resp.status} for endpoint {endpoint}")
                return False
        except Exception as e:
            logger.error(f"Exception transferring channel {channel_id}: {e}")
            return False

    async def play_media(self, channel_id: str, media_uri: str) -> Optional[str]:
        """Plays audio media to a channel (e.g. 'sound:demo-congrats')."""
        if not self._http_session:
            return None
        url = f"{self.base_url}/channels/{channel_id}/play"
        payload = {"media": media_uri}
        try:
            async with self._http_session.post(url, json=payload) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return data.get("id")
                return None
        except Exception as e:
            logger.error(f"Exception playing media on channel {channel_id}: {e}")
            return None
