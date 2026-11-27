"""Outbound calling: number handling, safety checks, ARI originate request, and the answered-call path."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import pipeline.outbound as ob
import pipeline.server as srv
from pipeline.outbound import OutboundDialer, OutboundError, OutboundSettings, build_api_app, normalize_number

SETTINGS = OutboundSettings(caller_id="918031451001", api_key="k" * 24)


@pytest.mark.parametrize("raw,expected", [
    ("+91 89253 55704", "918925355704"),
    ("08925355704", "918925355704"),
    ("8925355704", "918925355704"),
    ("918925355704", "918925355704"),
    ("0091-89253-55704", "918925355704"),
    ("+1 (415) 555-0123", "14155550123"),
])
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "123"])
def test_normalize_number_rejects_garbage(raw):
    with pytest.raises(OutboundError):
        normalize_number(raw)


class FakeAri:
    """Minimal ARI: records what the Python side asks Asterisk to do."""

    def __init__(self):
        self.originated, self.answered, self.added, self.gone = [], [], [], set()
        app = web.Application()
        app.router.add_post("/ari/channels", self.originate)
        app.router.add_get("/ari/channels/{cid}", self.get_channel)
        app.router.add_delete("/ari/channels/{cid}", self.delete_channel)
        app.router.add_post("/ari/channels/externalMedia", self.external_media)
        app.router.add_post("/ari/channels/{cid}/answer", self.answer)
        app.router.add_post("/ari/bridges", self.bridge)
        app.router.add_post("/ari/bridges/{bid}/addChannel", self.add_channel)
        self.app = app

    async def originate(self, r):
        self.originated.append(dict(r.query))
        return web.json_response({"id": r.query["channelId"]})

    async def get_channel(self, r):
        return web.json_response({}, status=404 if r.match_info["cid"] in self.gone else 200)

    async def delete_channel(self, r):
        self.gone.add(r.match_info["cid"])
        return web.Response(status=204)

    async def external_media(self, r):
        return web.json_response({"id": "ext-1"})

    async def answer(self, r):
        self.answered.append(r.match_info["cid"])
        return web.Response(status=204)

    async def bridge(self, r):
        return web.json_response({"id": "bridge-1"})

    async def add_channel(self, r):
        self.added.append(r.query["channel"])
        return web.Response(status=204)


async def _with_fake_ari(monkeypatch, body, settings=SETTINGS):
    fake = FakeAri()
    async with TestServer(fake.app) as ari_srv:
        base = f"http://127.0.0.1:{ari_srv.port}/ari"
        monkeypatch.setattr(ob, "ARI_URL", base)
        monkeypatch.setattr(srv, "ARI_URL", base)
        monkeypatch.setattr(ob, "POLL_SECONDS", 0.01)
        server = srv.AsteriskServer()
        dialer = OutboundDialer(server, settings)
        try:
            await body(fake, server, dialer)
        finally:
            await server.close()


def test_dial_sends_correct_originate_request(monkeypatch):
    async def body(fake, server, dialer):
        result = await dialer.dial("+91 89253 55704", "assistant")
        sent = fake.originated[0]
        assert sent["endpoint"] == "PJSIP/918925355704@plivo-outbound"
        assert sent["callerId"] == "918031451001"
        assert sent["app"] == ob.ARI_APP_NAME and sent["appArgs"] == "outbound"
        assert sent["channelId"] == result["channelId"]
        assert server.outbound_calls[result["channelId"]]["to_number"] == "+918925355704"
    asyncio.run(_with_fake_ari(monkeypatch, body))


def test_dial_rejects_unsafe_requests(monkeypatch):
    async def body(fake, server, dialer):
        with pytest.raises(OutboundError) as e:
            await dialer.dial("+14155550123")                       # country not allowed
        assert e.value.status == 403
        with pytest.raises(OutboundError) as e:
            await dialer.dial("+918925355704", "leave_verification")  # needs employeeId
        assert e.value.status == 400
        with pytest.raises(OutboundError):
            await dialer.dial("+918925355704", "not_a_type")
        monkeypatch.setattr("security.call_blocklist.is_number_blocked", lambda n: True)
        with pytest.raises(OutboundError) as e:
            await dialer.dial("+918925355704")
        assert e.value.status == 403
        assert fake.originated == []                                # nothing was dialled
    asyncio.run(_with_fake_ari(monkeypatch, body))


def test_dial_enforces_line_limit(monkeypatch):
    async def body(fake, server, dialer):
        await dialer.dial("+918925355704")
        await dialer.dial("+918925355705")
        with pytest.raises(OutboundError) as e:
            await dialer.dial("+918925355706")
        assert e.value.status == 503
    asyncio.run(_with_fake_ari(monkeypatch, body, OutboundSettings(caller_id="918031451001", max_calls=2)))


def test_unanswered_call_frees_the_line(monkeypatch):
    async def body(fake, server, dialer):
        result = await dialer.dial("+918925355704")
        fake.gone.add(result["channelId"])                          # callee never picked up
        await asyncio.sleep(0.2)
        assert result["channelId"] not in server.outbound_calls
    asyncio.run(_with_fake_ari(monkeypatch, body))


def test_answered_outbound_call_is_bridged_without_answering_again(monkeypatch):
    async def body(fake, server, dialer):
        result = await dialer.dial("+918925355704", "assistant")
        cid = result["channelId"]
        await server._handle_ari_event({"type": "StasisStart",
                                        "channel": {"id": cid, "name": "PJSIP/plivo-outbound-00000001",
                                                    "caller": {"number": "918031451001"}}})
        assert fake.answered == []                                  # already answered by the callee
        assert fake.added == [f"{cid},ext-1"]                       # bridged to AudioSocket
        assert server.channel_callers[cid] == "+918925355704"       # session caller = the person we called
        assert server.outbound_calls[cid]["status"] == "answered"
        await server._cleanup_call(cid)
        assert cid not in server.outbound_calls
    asyncio.run(_with_fake_ari(monkeypatch, body))


def test_inbound_call_is_still_answered(monkeypatch):
    async def body(fake, server, dialer):
        await server._handle_ari_event({"type": "StasisStart",
                                        "channel": {"id": "in-1", "name": "PJSIP/plivo-inbound-00000002",
                                                    "caller": {"number": "+919000000000"}}})
        assert fake.answered == ["in-1"]
    asyncio.run(_with_fake_ari(monkeypatch, body))


def test_http_api_requires_key_and_places_call(monkeypatch):
    async def body(fake, server, dialer):
        async with TestClient(TestServer(build_api_app(dialer))) as api:
            assert (await api.post("/api/outbound/call", json={"to": "+918925355704"})).status == 401
            bad = await api.post("/api/outbound/call", json={"to": "+14155550123"}, headers={"X-API-Key": SETTINGS.api_key})
            assert bad.status == 403
            ok = await api.post("/api/outbound/call", json={"to": "+918925355704"}, headers={"X-API-Key": SETTINGS.api_key})
            assert ok.status == 202 and (await ok.json())["status"] == "dialing"
    asyncio.run(_with_fake_ari(monkeypatch, body))


class _Stub:
    def __init__(self, call_type, **meta):
        self.call_type, self.call_metadata = call_type, meta


def test_session_prompt_and_greeting_follow_call_type():
    from pipeline.voice_session import AsteriskVoiceSession as S
    day = "Monday, November 23, 2026"
    leave = _Stub("leave_verification", employee_name="Ravi", employee_id="1007")
    assert "OUTBOUND call to Ravi" in S._build_system_prompt(leave, day)
    assert "Ravi" in S._opening_line(leave) and "leave request" in S._opening_line(leave)
    rem = _Stub("scheduled_reminder", employee_name="Ravi", employee_id="1007", reminder_prompt="Submit timesheet")
    assert "Submit timesheet" in S._build_system_prompt(rem, day)
    assert "OUTBOUND" not in S._build_system_prompt(_Stub("inbound"), day)
    assert "Welcome" in S._opening_line(_Stub("inbound"))
    assert "calling" in S._opening_line(_Stub("assistant"))
