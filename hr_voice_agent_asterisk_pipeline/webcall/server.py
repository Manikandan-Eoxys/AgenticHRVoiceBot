"""webcall/server.py - HTTPS front door: serves the landing page and the WebRTC offer endpoint.

  GET  /                      landing page (web/index.html)
  GET  /static/...            web/webcall.js etc.
  GET  /api/webcall/config    ICE servers + limits for the widget
  POST /api/webcall/offer     {type:"offer", sdp} -> {type, sdp, callId}
  POST /api/webcall/hangup    {callId}
Runs inside agent.py (WEBCALL_ENABLED=1) or standalone: python -m webcall.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import ssl
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Optional

from aiohttp import web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from .audio_io import InboundConverter, OutboundAudioTrack
from .bridge import WebCallBridge
from .settings import WebCallSettings

logger = logging.getLogger("webcall.server")


def _err(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


@dataclass
class ActiveCall:
    call_id: str
    ip: str
    pc: RTCPeerConnection
    bridge: WebCallBridge
    inbound: Any = None
    channel: Any = None
    task: Optional[asyncio.Task] = None
    ended: bool = False


class WebCallServer:
    def __init__(self, settings: Optional[WebCallSettings] = None, asterisk_server: Optional[object] = None) -> None:
        self.settings = settings or WebCallSettings.from_env()
        self.asterisk_server = asterisk_server
        self.calls: dict = {}
        self._offers: dict = defaultdict(deque)
        self._runner: Optional[web.AppRunner] = None
        self._stop = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def build_app(self) -> web.Application:
        s = self.settings

        @web.middleware
        async def cors(request, handler):
            origin = request.headers.get("Origin", "")
            resp = web.Response(status=204) if request.method == "OPTIONS" else await handler(request)
            if origin and origin in s.allowed_origins:
                resp.headers.update({
                    "Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                })
            return resp

        app = web.Application(middlewares=[cors], client_max_size=64 * 1024)
        app.router.add_get("/", self._index)
        app.router.add_get("/healthz", self._health)
        app.router.add_get("/api/webcall/config", self._config)
        app.router.add_post("/api/webcall/offer", self._offer)
        app.router.add_post("/api/webcall/hangup", self._hangup)
        app.router.add_static("/static", s.static_dir)
        return app

    async def start(self) -> None:
        s = self.settings
        ssl_ctx = None
        if s.cert_file and s.key_file:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(s.cert_file, s.key_file)
        try:
            self._runner = web.AppRunner(self.build_app())
            await self._runner.setup()
            await web.TCPSite(self._runner, s.host, s.port, ssl_context=ssl_ctx).start()
        except Exception:
            logger.exception("❌ [WebCall] could not start on %s:%s (phone calls are unaffected)", s.host, s.port)
            return
        logger.info("🌐 [WebCall] %s://%s:%s  max %s calls x %ss  → AudioSocket %s:%s",
                    "https" if ssl_ctx else "http", s.host, s.port, s.max_calls, s.max_seconds,
                    s.audiosocket_host, s.audiosocket_port)
        if not ssl_ctx:
            logger.warning("⚠️ [WebCall] no TLS cert: browsers allow the microphone only on https:// or http://localhost")
        await self._stop.wait()

    async def stop(self) -> None:
        for call in list(self.calls.values()):
            await self._finish(call, "server_shutdown")
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()
        self._stop.set()

    # ── HTTP handlers ────────────────────────────────────────────────────────
    async def _index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self.settings.static_dir / "index.html", headers={"Cache-Control": "no-store"})

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "activeCalls": len(self.calls)})

    async def _config(self, request: web.Request) -> web.Response:
        s = self.settings
        return web.json_response({"iceServers": list(s.ice_servers), "maxSeconds": s.max_seconds,
                                  "activeCalls": len(self.calls), "maxCalls": s.max_calls})

    def _client_ip(self, request: web.Request) -> str:
        if self.settings.trust_proxy:
            fwd = request.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.remote or "unknown"

    def _allow_offer(self, ip: str) -> bool:
        now, window = time.monotonic(), self._offers[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.settings.offers_per_minute:
            return False
        window.append(now)
        return True

    async def _offer(self, request: web.Request) -> web.Response:
        s, ip = self.settings, self._client_ip(request)
        if not self._allow_offer(ip):
            return _err(429, "rate_limited")
        if len(self.calls) >= s.max_calls:
            return _err(503, "busy")
        if sum(c.ip == ip for c in self.calls.values()) >= s.per_ip_max:
            return _err(429, "too_many_calls")
        try:
            body = await request.json()
            if body.get("type") != "offer":
                raise ValueError("not an offer")
            offer = RTCSessionDescription(sdp=str(body["sdp"]), type="offer")
        except Exception:
            return _err(400, "bad_offer")

        call_id = secrets.token_urlsafe(9)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**i) for i in s.ice_servers]))
        bridge = WebCallBridge(call_id, f"web-{call_id[:6]}", s, self.asterisk_server)
        call = self.calls[call_id] = ActiveCall(call_id, ip, pc, bridge)
        self._wire(call)
        try:
            await pc.setRemoteDescription(offer)
            await pc.setLocalDescription(await pc.createAnswer())
        except Exception:
            logger.exception("❌ [WebCall %s] negotiation failed", call_id)
            await self._finish(call, "negotiation_failed")
            return _err(400, "negotiation_failed")
        # free the slot if the browser never completes ICE
        asyncio.get_running_loop().call_later(
            s.connect_timeout,
            lambda: pc.connectionState != "connected" and asyncio.ensure_future(self._finish(call, "connect_timeout")))
        logger.info("📲 [WebCall %s] offer accepted from %s", call_id, ip)
        return web.json_response({"type": pc.localDescription.type, "sdp": pc.localDescription.sdp, "callId": call_id})

    async def _hangup(self, request: web.Request) -> web.Response:
        try:
            call = self.calls.get(json.loads(await request.text()).get("callId", ""))
        except Exception:
            call = None
        if call is not None:
            await self._finish(call, "browser_hangup")
        return web.Response(status=204)

    # ── call plumbing ────────────────────────────────────────────────────────
    def _wire(self, call: ActiveCall) -> None:
        pc = call.pc

        @pc.on("track")
        def on_track(track):
            if track.kind != "audio" or call.inbound is not None:
                return
            call.inbound = track
            pc.addTrack(OutboundAudioTrack(call.bridge.outbound))
            self._maybe_start(call)

        @pc.on("datachannel")
        def on_datachannel(channel):
            call.channel = channel

            @channel.on("message")
            def on_message(message):
                try:
                    call.bridge.send_digit(json.loads(message).get("digit"))
                except Exception:
                    pass

        @pc.on("connectionstatechange")
        async def on_state():
            state = pc.connectionState
            if state == "connected":
                self._maybe_start(call)
            elif state in ("failed", "closed"):
                await self._finish(call, f"peer_{state}")
            elif state == "disconnected":
                asyncio.get_running_loop().call_later(
                    10, lambda: pc.connectionState == "disconnected" and
                    asyncio.ensure_future(self._finish(call, "peer_disconnected")))

    def _maybe_start(self, call: ActiveCall) -> None:
        # Start the pipeline only once the browser is connected, so the greeting is not lost.
        if call.task or call.inbound is None or call.pc.connectionState != "connected":
            return
        converter = InboundConverter()

        async def read_pcm() -> bytes:
            while True:
                data = converter.convert(await call.inbound.recv())
                if data:
                    return data

        async def run() -> None:
            await self._finish(call, await call.bridge.run(read_pcm))

        call.task = asyncio.create_task(run(), name=f"webcall-{call.call_id}")
        self._notify(call, "call_started")

    def _notify(self, call: ActiveCall, event: str, reason: Optional[str] = None) -> None:
        ch = call.channel
        if ch is not None and ch.readyState == "open":
            ch.send(json.dumps({"type": "event", "event": event, "reason": reason}))

    async def _finish(self, call: ActiveCall, reason: str) -> None:
        if call.ended:
            return
        call.ended = True
        self.calls.pop(call.call_id, None)
        logger.info("📴 [WebCall %s] ended (%s)", call.call_id, reason)
        self._notify(call, "call_ended", reason)
        await asyncio.sleep(0.15)
        await call.bridge.close(reason)
        if call.task is not None and call.task is not asyncio.current_task():
            call.task.cancel()
        try:
            await asyncio.wait_for(call.pc.close(), timeout=3)
        except Exception:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    try:
        asyncio.run(WebCallServer().start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
