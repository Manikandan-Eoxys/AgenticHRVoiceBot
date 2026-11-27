"""asterisk/transfer.py - hand a live phone call to a human (the HR coordinator) through the Plivo trunk.

Two kinds of caller are supported:
  * phone calls  - the caller is an Asterisk channel in a bridge with the AI's AudioSocket channel;
  * web calls    - the caller is a browser (WebRTC), reachable only through the WebCallBridge. For these, Asterisk
                   opens a second AudioSocket connection to us; the bridge relays browser audio over it.

Phone-call flow (TRANSFER_MODE=bridge, the default)  -  the caller NEVER leaves the Stasis app
  1. A ring tone is streamed to the caller (over the AI's AudioSocket) while we dial.
  2. ARI originate  PJSIP/<digits>@plivo-outbound  (caller ID = your Plivo number).
  3. The coordinator answers -> Asterisk sends StasisStart for that leg.
  4. The session is flagged "transferred" FIRST (so nothing that follows can hang up the caller), then the
     coordinator is added to the caller's bridge and the AI's AudioSocket channel is removed + hung up.
  5. Python keeps supervising both legs: whoever hangs up, the other leg and the bridge are torn down.
  6. No answer / busy / rejected -> the caller simply stays with the AI (it offers an HR ticket).

Fallback (TRANSFER_MODE=dialplan): the caller is `continue`d into [hr-voicebot-outbound] and Asterisk runs
Dial(PJSIP/<number>@plivo-outbound). Use only to compare behaviour; it has no ring tone and no fallback to the AI.

Every channel we hang up is logged with a reason ("we hung up X because ..."). If a call drops and there is NO such
line, the far end (Plivo / the coordinator's carrier) sent the BYE - see docs/COORDINATOR_TRANSFER.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

import aiohttp
import numpy as np

from config import ARI_URL, ARI_APP_NAME, AST_BRIDGE_ADVERTISE_HOST, AST_BRIDGE_PORT
from pipeline.outbound import OutboundError, OutboundSettings, normalize_number

logger = logging.getLogger("asterisk.transfer")

# ── Ring tone played to the caller while the coordinator's phone rings (440 Hz, 1 s on / 3 s off) ──
RING_SAMPLE_RATE = 8000
RING_FREQ_HZ = 440
RING_ON_SECONDS = 1.0
RING_OFF_SECONDS = 3.0
RING_AMPLITUDE = 4500


def build_ringback_cycle(sample_rate: int = RING_SAMPLE_RATE) -> bytes:
    """One ring cycle (tone + silence) as 16-bit mono PCM."""
    n_on = int(sample_rate * RING_ON_SECONDS)
    t = np.arange(n_on, dtype=np.float32) / sample_rate
    tone = (RING_AMPLITUDE * np.sin(2 * np.pi * RING_FREQ_HZ * t)).astype(np.int16)
    fade = int(sample_rate * 0.02)                       # 20 ms fade in/out: no clicks
    if fade > 0 and n_on > 2 * fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        tone[:fade] = (tone[:fade] * ramp).astype(np.int16)
        tone[-fade:] = (tone[-fade:] * ramp[::-1]).astype(np.int16)
    silence = np.zeros(int(sample_rate * RING_OFF_SECONDS), dtype=np.int16)
    return np.concatenate([tone, silence]).tobytes()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def transfer_mode() -> str:
    mode = os.environ.get("TRANSFER_MODE", "bridge").strip().lower()
    return mode if mode in ("bridge", "dialplan") else "bridge"


class RelayEntry:
    """One AudioSocket connection that Asterisk will open for the coordinator leg of a WEB call."""

    def __init__(self) -> None:
        loop = asyncio.get_running_loop()
        self.connected: asyncio.Future = loop.create_future()   # -> (reader, writer) when Asterisk connects
        self.closed = asyncio.Event()                           # set when the relay is finished


class TransferManager:
    def __init__(self, server: Any) -> None:
        self.server = server                         # AsteriskServer (ARI HTTP session, bridges, channels)
        self.pending: Dict[str, Dict[str, Any]] = {}  # coordinator channel id -> record (still ringing)
        self.active: Dict[str, Dict[str, Any]] = {}   # caller channel id      -> record (coordinator joined)
        self.by_coord: Dict[str, str] = {}            # coordinator channel id -> caller channel id
        self.by_ext: Dict[str, str] = {}              # relay (externalMedia) channel id -> caller id (web calls)

    # ── used by the voice session (through AsteriskServer) ──────────────────
    async def transfer(self, caller_channel_id: str, target: str, session: Any = None) -> bool:
        s = OutboundSettings.from_env()
        try:
            digits = normalize_number(target, s.country_code)
        except OutboundError as exc:
            logger.warning("🔀 [Transfer] bad target %r: %s", target, exc)
            return False
        if not any(digits.startswith(p) for p in s.allowed_prefixes):
            logger.warning("🔀 [Transfer] +%s is outside OUTBOUND_ALLOWED_PREFIXES (%s)", digits, ",".join(s.allowed_prefixes))
            return False
        if not s.caller_id:
            logger.error("🔀 [Transfer] OUTBOUND_CALLER_ID is not set in .env - cannot dial the coordinator")
            return False

        bridge_id = self.server.active_bridges.get(caller_channel_id)
        ext_id = self.server.external_channels.get(caller_channel_id)
        web = self.server.web_bridges.get(caller_channel_id)      # the browser leg, if this is a web call
        if not bridge_id and web is None:
            logger.warning("🔀 [Transfer] %s is neither a phone call in a bridge nor an active web call", caller_channel_id)
            return False
        if caller_channel_id in self.active:
            logger.warning("🔀 [Transfer] a transfer is already active for %s", caller_channel_id)
            return False

        timeout = int(os.environ.get("COORDINATOR_DIAL_TIMEOUT_SECONDS", "30"))

        # ── 1. WebCall (Browser WebRTC) Transfer ──────────────────────────────
        if web is not None:
            return await self._transfer_webcall(caller_channel_id, digits, s, web, ext_id, timeout)

        # ── 2. Phone call ────────────────────────────────────────────────────
        if transfer_mode() == "dialplan":
            return await self._transfer_dialplan(caller_channel_id, digits, bridge_id, ext_id, session)
        return await self._transfer_bridge(caller_channel_id, digits, s, bridge_id, ext_id, timeout, session)

    # ── phone call, supervised bridge (default) ───────────────────────────────
    async def _transfer_bridge(self, caller_id: str, digits: str, s: OutboundSettings, bridge_id: str,
                               ext_id: Optional[str], timeout: int, session: Any) -> bool:
        coord_id = f"coord-{uuid.uuid4().hex[:12]}"
        rec = {"caller": caller_id, "bridge": bridge_id, "ext": ext_id, "to": "+" + digits, "web": None,
               "future": asyncio.get_running_loop().create_future(), "state": "dialing"}
        self.pending[coord_id] = rec                      # registered BEFORE originate: never miss the first event
        name = os.environ.get("OUTBOUND_CALLER_NAME", "HR Voicebot").strip() or "HR Voicebot"
        params = {
            "endpoint": f"PJSIP/{('+' if s.dial_plus else '') + digits}@{s.trunk_endpoint}",
            "app": ARI_APP_NAME, "appArgs": f"transfer,{caller_id}",
            "callerId": f'"{name}" <{s.caller_id}>',
            "timeout": str(timeout), "channelId": coord_id,
        }
        stop_ring = asyncio.Event()
        ring_task: Optional[asyncio.Task] = None
        connected = False
        try:
            if session is not None and _env_flag("TRANSFER_RINGBACK", True):
                ring_task = asyncio.create_task(self._ringback_loop(session, stop_ring), name="TransferRingback")
            http = await self.server._get_http_session()
            async with http.post(f"{ARI_URL}/channels", params=params) as resp:
                if resp.status not in (200, 201):
                    logger.error("🔀 [Transfer] Asterisk refused the coordinator call (%s): %s", resp.status, (await resp.text())[:300])
                    return False
            logger.info("🔀 [Transfer] dialing coordinator +%s via PJSIP/%s (ring timeout %ss, channel %s)",
                        digits, s.trunk_endpoint, timeout, coord_id)
            answered = await asyncio.wait_for(rec["future"], timeout=timeout + 5)
            await self._stop_ringback(session, stop_ring, ring_task)
            ring_task = None
            if not answered:
                logger.warning("🔀 [Transfer] coordinator did not connect (%s) - caller stays with the AI", rec.get("fail", "no answer"))
                return False
            logger.info("📞 [Transfer] coordinator answered on %s - joining the call now", coord_id)

            # Flag the session BEFORE touching the AI leg: closing the AudioSocket must never hang up the caller.
            if session is not None:
                session.transferred = True
                session.coordinator_connected = True
            if not await self._join_phone(coord_id, rec):
                if session is not None:
                    session.transferred = False
                    session.coordinator_connected = False
                return False
            connected = True
            return True
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("🔀 [Transfer] coordinator did not pick up (%s)", type(exc).__name__)
            return False
        except BaseException as exc:
            logger.warning("🔀 [Transfer] transfer interrupted or cancelled: %s", exc)
            raise
        finally:
            await self._stop_ringback(session, stop_ring, ring_task)
            if not connected:
                self.pending.pop(coord_id, None)
                await self.server._hangup_channel(coord_id, "transfer failed / not answered")

    async def _join_phone(self, coord_id: str, rec: Dict[str, Any]) -> bool:
        """Coordinator answered: put them in the caller's bridge, then release the AI's AudioSocket leg."""
        http = await self.server._get_http_session()
        bridge, caller, ext = rec["bridge"], rec["caller"], rec.get("ext")
        try:
            async with http.post(f"{ARI_URL}/bridges/{bridge}/addChannel", params={"channel": coord_id}) as resp:
                if resp.status not in (200, 204):
                    raise RuntimeError(f"addChannel(coordinator) returned {resp.status}: {(await resp.text())[:200]}")
            # Register supervision before removing the AI leg so a fast hang-up is never missed.
            self.pending.pop(coord_id, None)
            self.active[caller] = rec
            self.by_coord[coord_id] = caller
            if ext:
                async with http.post(f"{ARI_URL}/bridges/{bridge}/removeChannel", params={"channel": ext}) as resp:
                    if resp.status not in (200, 204, 404):
                        logger.warning("🔀 [Transfer] could not remove the AI leg from the bridge (%s)", resp.status)
                await self.server._hangup_channel(ext, "AI leg released after hand-off to coordinator")
        except Exception as exc:
            logger.error("🔀 [Transfer] could not join the coordinator to the call: %s", exc)
            self.active.pop(caller, None)
            self.by_coord.pop(coord_id, None)
            return False
        logger.info("✅ [Transfer] coordinator %s joined - caller %s is now talking to a human (bridge %s)", rec["to"], caller, bridge)
        return True

    # ── phone call, native dialplan (TRANSFER_MODE=dialplan) ─────────────────
    async def _transfer_dialplan(self, caller_id: str, digits: str, bridge_id: str, ext_id: Optional[str], session: Any) -> bool:
        logger.info("🔀 [Transfer] (dialplan mode) handing %s to Dial(PJSIP/%s@%s)", caller_id, digits, OutboundSettings.from_env().trunk_endpoint)
        http = await self.server._get_http_session()
        # Everything that could hang up the caller must already know about the hand-off BEFORE `continue`:
        # the StasisEnd event can reach us before the HTTP reply does.
        rec = {"to": "+" + digits, "caller": caller_id, "bridge": bridge_id, "dialplan": True}
        self.active[caller_id] = rec
        if session is not None:
            session.transferred = True
            session.coordinator_connected = True
        params = {"context": "hr-voicebot-outbound", "extension": digits, "priority": "1"}
        try:
            if ext_id:
                async with http.post(f"{ARI_URL}/bridges/{bridge_id}/removeChannel", params={"channel": ext_id}):
                    pass
            async with http.post(f"{ARI_URL}/channels/{caller_id}/continue", params=params) as resp:
                if resp.status not in (200, 204):
                    raise RuntimeError(f"continue returned {resp.status}: {(await resp.text())[:200]}")
        except Exception as exc:
            logger.error("🔀 [Transfer] dialplan hand-off failed: %s", exc)
            self.active.pop(caller_id, None)
            if session is not None:
                session.transferred = False
                session.coordinator_connected = False
            return False
        return True

    # ── ring tone ────────────────────────────────────────────────────────────
    async def _ringback_loop(self, session: Any, stop: asyncio.Event) -> None:
        cycle = build_ringback_cycle(getattr(session, "sample_rate", RING_SAMPLE_RATE) or RING_SAMPLE_RATE)
        period = RING_ON_SECONDS + RING_OFF_SECONDS
        try:
            logger.info("🔔 [Transfer] ring tone started for the caller")
            while not stop.is_set():
                session.audio_queue.enqueue_pcm(cycle)
                waited = 0.0
                while waited < period and not stop.is_set():
                    await asyncio.sleep(0.1)
                    waited += 0.1
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("🔔 [Transfer] ring tone failed (continuing without it): %s", exc)

    async def _stop_ringback(self, session: Any, stop: asyncio.Event, task: Optional[asyncio.Task]) -> None:
        stop.set()
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except Exception:
            task.cancel()
        try:
            session.audio_queue.interrupt()               # purge ring audio still queued
        except Exception:
            pass

    async def _transfer_webcall(self, caller_channel_id: str, digits: str, s: OutboundSettings, web: Any, ext_id: Optional[str], timeout: int) -> bool:
        coord_id = str(uuid.uuid4())
        rec = {"caller": caller_channel_id, "bridge": None, "ext": ext_id, "to": "+" + digits, "web": web,
               "future": asyncio.get_running_loop().create_future()}
        self.pending[coord_id] = rec
        params = {
            "endpoint": f"PJSIP/{('+' if s.dial_plus else '') + digits}@{s.trunk_endpoint}",
            "app": ARI_APP_NAME, "appArgs": "transfer", "callerId": s.caller_id,
            "timeout": str(timeout), "channelId": coord_id,
        }
        joined = False
        try:
            http = await self.server._get_http_session()
            async with http.post(f"{ARI_URL}/channels", params=params) as resp:
                if resp.status not in (200, 201):
                    logger.error("🔀 [Transfer] Asterisk refused the call to the coordinator (%s): %s",
                                 resp.status, (await resp.text())[:200])
                    return False
            logger.info("🔀 [Transfer] dialing coordinator +%s via %s (ring timeout %ss)", digits, s.trunk_endpoint, timeout)
            joined = await asyncio.wait_for(rec["future"], timeout=timeout + 5)
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("🔀 [Transfer] coordinator did not pick up (%s)", type(exc).__name__)
            joined = False
        except BaseException as exc:
            logger.warning("🔀 [Transfer] transfer interrupted or cancelled: %s", exc)
            joined = False
            raise
        finally:
            if not joined:
                self.pending.pop(coord_id, None)
                try:
                    await self.server._hangup_channel(coord_id)
                except Exception:
                    pass
        return bool(joined)

    def is_transferred(self, caller_channel_id: str) -> bool:
        return caller_channel_id in self.active

    # ── ARI events (called from AsteriskServer._handle_ari_event) ───────────
    async def on_stasis_start(self, channel_id: str) -> bool:
        """The coordinator answered: put them in the caller's bridge and take the AI out."""
        rec = self.pending.get(channel_id)
        if rec is None:
            return False
        if rec.get("web") is not None:
            # Waits for Asterisk to open the relay connection, so run it as its own task: never block the ARI event loop.
            asyncio.create_task(self._join_web(channel_id, rec), name=f"transfer-join-{channel_id[:8]}")
            return True
        # Phone call: only signal "answered". transfer() performs the join itself, in order, after flagging the session.
        rec["state"] = "answered"
        if not rec["future"].done():
            rec["future"].set_result(True)
        return True

    async def _join_web(self, coord_id: str, rec: Dict[str, Any]) -> bool:
        """Coordinator answered a web-call transfer: build a bridge [coordinator + relay] and switch the browser to it."""
        http = await self.server._get_http_session()
        web, caller = rec["web"], rec["caller"]
        relay_uuid = str(uuid.uuid4())
        entry = RelayEntry()
        self.server.relay_calls[relay_uuid] = entry
        bridge_id = ext_id = None
        try:
            async with http.post(f"{ARI_URL}/bridges", json={"type": "mixing", "name": f"transfer_{relay_uuid}"}) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"create bridge returned {resp.status}")
                bridge_id = (await resp.json()).get("id")
            async with http.post(f"{ARI_URL}/bridges/{bridge_id}/addChannel", params={"channel": coord_id}) as resp:
                if resp.status not in (200, 204):
                    raise RuntimeError(f"adding the coordinator returned {resp.status}")
            payload = {
                "app": ARI_APP_NAME, "external_host": f"{AST_BRIDGE_ADVERTISE_HOST}:{AST_BRIDGE_PORT}",
                "format": "slin", "encapsulation": "audiosocket", "transport": "tcp",
                "connection_type": "client", "data": relay_uuid,
            }
            async with http.post(f"{ARI_URL}/channels/externalMedia", json=payload) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"externalMedia returned {resp.status}")
                ext_id = (await resp.json()).get("id")
            async with http.post(f"{ARI_URL}/bridges/{bridge_id}/addChannel", params={"channel": ext_id}) as resp:
                if resp.status not in (200, 204):
                    raise RuntimeError(f"adding the relay channel returned {resp.status}")
            rec.update(bridge=bridge_id, ext=ext_id, relay_entry=entry)
            self.pending.pop(coord_id, None)
            self.active[caller] = rec
            self.by_coord[coord_id] = caller
            self.by_ext[ext_id] = caller
            reader, writer = await asyncio.wait_for(entry.connected, timeout=8)     # Asterisk opens the connection
            await web.attach_relay(reader, writer, on_closed=entry.closed.set)
        except Exception as exc:
            logger.error("🔀 [Transfer] could not connect the browser to the coordinator: %s", exc)
            self.pending.pop(coord_id, None)
            self.active.pop(caller, None)
            self.by_coord.pop(coord_id, None)
            if ext_id:
                self.by_ext.pop(ext_id, None)
            if relay_uuid:
                self.server.relay_calls.pop(relay_uuid, None)
            entry.closed.set()
            for chan in (ext_id, coord_id):
                if chan:
                    await self.server._hangup_channel(chan)
            if bridge_id:
                try:
                    async with http.delete(f"{ARI_URL}/bridges/{bridge_id}"):
                        pass
                except Exception:
                    pass
            if not rec["future"].done():
                rec["future"].set_result(False)
            return True
        logger.info("✅ [Transfer] coordinator %s joined - the browser caller is now talking to a human", rec["to"])
        if not rec["future"].done():
            rec["future"].set_result(True)
        return True

    async def on_channel_destroyed(self, channel_id: str, cause: str = "", cause_code: Any = None) -> None:
        rec = self.pending.get(channel_id)
        if rec is not None:
            rec["fail"] = f"{cause or 'unknown'} (cause {cause_code})"
            if not rec["future"].done():
                logger.info("📵 [Transfer] coordinator leg ended before answering: %s (cause %s)", cause or "unknown", cause_code)
                rec["future"].set_result(False)
        # Dialplan mode: the caller channel is gone -> forget the hand-off and clean the leftovers.
        drec = self.active.get(channel_id)
        if drec is not None and drec.get("dialplan"):
            logger.info("📴 [Transfer] (dialplan) call %s ended: %s (cause %s)", channel_id, cause or "unknown", cause_code)
            self.active.pop(channel_id, None)
            self.server.active_bridges.pop(channel_id, None)
            self.server.external_channels.pop(channel_id, None)
            await self._delete_bridge(drec.get("bridge"))
        # If the caller channel was destroyed while waiting for the coordinator:
        for coord_id, p_rec in list(self.pending.items()):
            if p_rec.get("caller") == channel_id:
                logger.info("📵 [Transfer] caller %s destroyed while waiting for coordinator - aborting coordinator leg %s", channel_id, coord_id)
                if not p_rec["future"].done():
                    p_rec["future"].set_result(False)
                self.pending.pop(coord_id, None)
                await self.server._hangup_channel(coord_id, "caller hung up while the coordinator was ringing")

    async def on_stasis_end(self, channel_id: str) -> None:
        """Either person hung up: end the other leg and the bridge."""
        # If caller hung up while a transfer was pending:
        for coord_id, p_rec in list(self.pending.items()):
            if p_rec.get("caller") == channel_id:
                logger.info("📵 [Transfer] caller %s hung up before coordinator answered - cancelling coordinator leg %s", channel_id, coord_id)
                if not p_rec["future"].done():
                    p_rec["future"].set_result(False)
                self.pending.pop(coord_id, None)
                await self.server._hangup_channel(coord_id, "caller hung up while the coordinator was ringing")

        rec = self.active.get(channel_id)
        if rec is not None and rec.get("dialplan"):
            # Dialplan mode: leaving Stasis is the hand-off itself, NOT a hang-up. Do nothing here;
            # on_channel_destroyed() cleans up when the call really ends.
            logger.info("↪️ [Transfer] (dialplan) %s left Stasis for the dialplan - call continues", channel_id)
            return

        if channel_id in self.by_coord:                       # coordinator hung up -> drop the caller
            logger.info("📴 [Transfer] coordinator leg %s hung up - ending the caller leg", channel_id)
            await self.end(self.by_coord[channel_id], hang_up="caller")
        elif channel_id in self.by_ext:                       # web call: relay channel closed -> drop the coordinator
            await self.end(self.by_ext[channel_id], hang_up="coordinator")
        elif channel_id in self.active:                       # caller hung up -> drop the coordinator
            logger.info("📴 [Transfer] caller %s hung up - ending the coordinator leg", channel_id)
            await self.end(channel_id, hang_up="coordinator")

    async def _delete_bridge(self, bridge_id: Optional[str]) -> None:
        if not bridge_id:
            return
        try:
            http = await self.server._get_http_session()
            async with http.delete(f"{ARI_URL}/bridges/{bridge_id}") as resp:
                logger.info("🧹 [Transfer] call finished, bridge %s destroyed (%s)", bridge_id, resp.status)
        except Exception as exc:
            logger.debug("Transfer bridge cleanup notice: %s", exc)

    async def end(self, caller_channel_id: str, hang_up: str = "both") -> None:
        rec = self.active.pop(caller_channel_id, None)
        if rec is None:
            return
        coord_id = next((c for c, caller in self.by_coord.items() if caller == caller_channel_id), None)
        if coord_id:
            self.by_coord.pop(coord_id, None)
        web = rec.get("web")
        if hang_up in ("caller", "both"):
            if web is not None:
                web.request_end("coordinator_hangup")         # tells the browser the call is over
            else:
                await self.server._hangup_channel(caller_channel_id, "coordinator hung up")
        if hang_up in ("coordinator", "both") and coord_id:
            await self.server._hangup_channel(coord_id, "caller hung up")
        if web is not None and rec.get("ext"):                # web call: also close the relay channel
            self.by_ext.pop(rec["ext"], None)
            await self.server._hangup_channel(rec["ext"], "web call ended")
            entry = rec.get("relay_entry")
            if entry is not None:
                entry.closed.set()
        else:
            self.server.active_bridges.pop(caller_channel_id, None)
        await self._delete_bridge(rec.get("bridge"))
