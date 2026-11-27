"""asterisk/outbound.py - place outbound calls (the HR bot phones someone) through the Plivo SIP trunk.

Flow
  1. dial() asks Asterisk (ARI  POST /channels) to call  PJSIP/<number>@plivo-outbound  with our caller ID,
     and registers the call in AsteriskServer.outbound_calls under a channel id we choose.
  2. The callee answers -> Asterisk sends StasisStart for that channel -> AsteriskServer sees the id in
     outbound_calls, skips "answer" (already answered) and bridges the call to AudioSocket.
  3. From there it is the SAME AsteriskVoiceSession as an inbound call (STT -> LLM/goals -> TTS).

Safety: only allowed country prefixes, blocklist check, concurrency cap, and the HTTP trigger listens on
127.0.0.1 and needs an API key (an open dial API is a toll-fraud risk).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web

from config import ARI_URL, ARI_APP_NAME

logger = logging.getLogger("asterisk.outbound")

CALL_TYPES = ("assistant", "leave_verification", "scheduled_reminder", "policy_acknowledgement")
NEEDS_EMPLOYEE = ("leave_verification", "scheduled_reminder", "policy_acknowledgement")


POLL_SECONDS = 2.0     # how often an unanswered call is checked


class OutboundError(Exception):
    """A dial request that cannot be placed (bad number, blocked, busy line, ARI error)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def outbound_enabled() -> bool:
    return _flag("OUTBOUND_ENABLED")


@dataclass(frozen=True)
class OutboundSettings:
    trunk_endpoint: str = "plivo-outbound"      # [plivo-outbound] in pjsip.conf
    caller_id: str = ""                          # your Plivo number, digits only (e.g. 918031451001)
    country_code: str = "91"
    allowed_prefixes: tuple = ("91",)
    max_calls: int = 2
    ring_timeout: int = 30
    dial_plus: bool = False                      # send +91... instead of 91... to the trunk
    api_host: str = "127.0.0.1"
    api_port: int = 8090
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "OutboundSettings":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        prefixes = tuple(p.strip().lstrip("+") for p in os.getenv("OUTBOUND_ALLOWED_PREFIXES", "91").split(",") if p.strip())
        return cls(
            trunk_endpoint=os.getenv("OUTBOUND_TRUNK_ENDPOINT", "plivo-outbound").strip(),
            caller_id=re.sub(r"\D", "", os.getenv("OUTBOUND_CALLER_ID", "")),
            country_code=re.sub(r"\D", "", os.getenv("OUTBOUND_DEFAULT_COUNTRY_CODE", "91")) or "91",
            allowed_prefixes=prefixes or ("91",),
            max_calls=_int("OUTBOUND_MAX_CALLS", 2),
            ring_timeout=_int("OUTBOUND_RING_TIMEOUT", 30),
            dial_plus=_flag("OUTBOUND_DIAL_PLUS"),
            api_host=os.getenv("OUTBOUND_API_HOST", "127.0.0.1"),
            api_port=_int("OUTBOUND_API_PORT", 8090),
            api_key=os.getenv("OUTBOUND_API_KEY", "").strip(),
        )


def normalize_number(raw: str, country_code: str = "91") -> str:
    """'+91 89253 55704', '08925355704', '8925355704', '918925355704' -> '918925355704' (digits only)."""
    text = re.sub(r"[\s\-().]", "", str(raw or ""))
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise OutboundError("A phone number is required.")
    if text.startswith("+"):
        pass                                    # already international
    elif digits.startswith("00"):
        digits = digits[2:]                     # 0091...
    elif digits.startswith("0"):
        digits = country_code + digits[1:]      # national trunk prefix: 08925355704
    elif len(digits) == 10:
        digits = country_code + digits          # bare national number
    if not 8 <= len(digits) <= 15:
        raise OutboundError(f"'{raw}' is not a valid phone number.")
    return digits


class OutboundDialer:
    def __init__(self, server: Any, settings: Optional[OutboundSettings] = None) -> None:
        self.server = server                     # AsteriskServer (owns outbound_calls + the ARI HTTP session)
        self.settings = settings or OutboundSettings.from_env()

    # ── public API ───────────────────────────────────────────────────────────
    async def dial(self, to_number: str = "", call_type: str = "assistant", employee_id: str = "",
                   employee_name: str = "", reminder_prompt: str = "") -> Dict[str, Any]:
        s = self.settings
        call_type = (call_type or "assistant").strip()
        if call_type not in CALL_TYPES:
            raise OutboundError(f"callType must be one of: {', '.join(CALL_TYPES)}")
        if not s.caller_id:
            raise OutboundError("OUTBOUND_CALLER_ID is not configured.", 500)

        employee_id = re.sub(r"\D", "", str(employee_id or ""))
        if call_type in NEEDS_EMPLOYEE:
            if not employee_id:
                raise OutboundError(f"employeeId is required for callType '{call_type}'.")
            to_number, employee_name = self._resolve_employee(employee_id, to_number, employee_name)
        digits = normalize_number(to_number, s.country_code)

        if not any(digits.startswith(p) for p in s.allowed_prefixes):
            raise OutboundError(f"Calls to +{digits} are not allowed (allowed prefixes: {', '.join(s.allowed_prefixes)}).", 403)
        from security.call_blocklist import is_number_blocked
        if is_number_blocked("+" + digits):
            raise OutboundError("That number is on the blocklist.", 403)
        if len(self.server.outbound_calls) >= s.max_calls:
            raise OutboundError("All outbound lines are busy. Try again shortly.", 503)

        channel_id = str(uuid.uuid4())
        self.server.outbound_calls[channel_id] = {
            "status": "dialing", "to_number": "+" + digits, "call_type": call_type,
            "employee_id": employee_id, "employee_name": employee_name or "", "reminder_prompt": reminder_prompt or "",
        }
        dial_number = ("+" if s.dial_plus else "") + digits
        params = {
            "endpoint": f"PJSIP/{dial_number}@{s.trunk_endpoint}",
            "app": ARI_APP_NAME,
            "appArgs": "outbound",
            "callerId": s.caller_id,
            "timeout": str(s.ring_timeout),
            "channelId": channel_id,
        }
        try:
            http = await self.server._get_http_session()
            async with http.post(f"{ARI_URL}/channels", params=params) as resp:
                if resp.status not in (200, 201):
                    body = (await resp.text())[:300]
                    raise OutboundError(f"Asterisk refused the call ({resp.status}): {body}", 502)
        except OutboundError:
            self.server.outbound_calls.pop(channel_id, None)
            raise
        except aiohttp.ClientError as exc:
            self.server.outbound_calls.pop(channel_id, None)
            raise OutboundError(f"Could not reach Asterisk ARI: {exc}", 502) from exc

        logger.info("📤 [Outbound %s] dialing +%s as %s via %s (%s)", channel_id[:8], digits, s.caller_id, s.trunk_endpoint, call_type)
        asyncio.create_task(self._watch_unanswered(channel_id), name=f"outbound-watch-{channel_id[:8]}")
        return {"channelId": channel_id, "to": "+" + digits, "callType": call_type, "status": "dialing"}

    def active_calls(self) -> list:
        return [{"channelId": cid, **{k: v for k, v in rec.items() if k != "reminder_prompt"}}
                for cid, rec in self.server.outbound_calls.items()]

    # ── helpers ──────────────────────────────────────────────────────────────
    def _resolve_employee(self, employee_id: str, to_number: str, employee_name: str):
        """Fill in the employee's name (and phone, if none given) from the HR database."""
        if to_number and employee_name:
            return to_number, employee_name
        try:
            from services.employee_service import EmployeeService
            contact = EmployeeService().get_employee_contact(employee_id)
        except Exception as exc:
            raise OutboundError(f"Employee lookup failed: {exc}", 500) from exc
        if not contact:
            raise OutboundError(f"Employee {employee_id} was not found or has no phone number.", 404)
        return to_number or contact["phone_number"], employee_name or contact["full_name"]

    async def _watch_unanswered(self, channel_id: str) -> None:
        """Free the line if the callee never answers (busy / rejected / no answer / bad number)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.ring_timeout + 10
        try:
            while loop.time() < deadline:
                await asyncio.sleep(POLL_SECONDS)
                rec = self.server.outbound_calls.get(channel_id)
                if rec is None or rec.get("status") != "dialing":
                    return                                   # answered, or already cleaned up
                http = await self.server._get_http_session()
                async with http.get(f"{ARI_URL}/channels/{channel_id}") as resp:
                    if resp.status == 404:                   # channel is gone, never answered
                        logger.info("📵 [Outbound %s] not answered (busy / rejected / no answer / invalid number)", channel_id[:8])
                        self.server.outbound_calls.pop(channel_id, None)
                        return
            rec = self.server.outbound_calls.pop(channel_id, None)
            if rec is not None:
                logger.info("📵 [Outbound %s] ring timeout, hanging up", channel_id[:8])
                await self.server._hangup_channel(channel_id)
        except Exception as exc:
            logger.debug("Outbound watcher notice: %s", exc)
            self.server.outbound_calls.pop(channel_id, None)


# ── local HTTP trigger (127.0.0.1 + API key) ─────────────────────────────────
def build_api_app(dialer: OutboundDialer) -> web.Application:
    key = dialer.settings.api_key

    @web.middleware
    async def auth(request: web.Request, handler):
        supplied = request.headers.get("X-API-Key", "")
        if not key or not hmac.compare_digest(supplied, key):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def call(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        try:
            result = await dialer.dial(
                to_number=str(body.get("to", "")),
                call_type=str(body.get("callType", "assistant")),
                employee_id=str(body.get("employeeId", "")),
                employee_name=str(body.get("employeeName", "")),
                reminder_prompt=str(body.get("reminderPrompt", "")),
            )
        except OutboundError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        return web.json_response(result, status=202)

    async def calls(request: web.Request) -> web.Response:
        return web.json_response({"calls": dialer.active_calls()})

    app = web.Application(middlewares=[auth], client_max_size=16 * 1024)
    app.router.add_post("/api/outbound/call", call)
    app.router.add_get("/api/outbound/calls", calls)
    return app


async def start_outbound_api(server: Any) -> Optional[web.AppRunner]:
    """Start the local trigger. Returns the runner (call .cleanup() on shutdown) or None if not started."""
    dialer = OutboundDialer(server)
    s = dialer.settings
    if not s.api_key or len(s.api_key) < 16:
        logger.error("❌ [Outbound] OUTBOUND_API_KEY missing or shorter than 16 chars - outbound API NOT started")
        return None
    if not s.caller_id:
        logger.error("❌ [Outbound] OUTBOUND_CALLER_ID missing - outbound API NOT started")
        return None
    runner = web.AppRunner(build_api_app(dialer))
    await runner.setup()
    await web.TCPSite(runner, s.api_host, s.api_port).start()
    logger.info("📤 [Outbound] API on http://%s:%s  caller ID %s via %s  prefixes %s  max %s calls",
                s.api_host, s.api_port, s.caller_id, s.trunk_endpoint, ",".join(s.allowed_prefixes), s.max_calls)
    return runner
