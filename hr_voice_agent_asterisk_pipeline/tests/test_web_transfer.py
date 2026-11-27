"""Web call -> coordinator: real WebCallBridge + real AudioSocket handler; the test plays Asterisk and the phone."""
import asyncio
import uuid

from aiohttp import web
from aiohttp.test_utils import TestServer

import pipeline.server as srv
import pipeline.transfer as tr
from audio.audiosocket import (TYPE_AUDIO, TYPE_HANGUP, TYPE_UUID, encode_audio_frame, encode_hangup,
                               encode_packet, read_packet)
from webcall.bridge import WebCallBridge
from webcall.settings import WebCallSettings

FRAME = bytes([7]) * 320


class FakeAri:
    def __init__(self):
        self.originated, self.external, self.added, self.deleted_channels, self.deleted_bridges = [], [], [], [], []
        app = web.Application()
        app.router.add_post("/ari/channels", self.originate)
        app.router.add_post("/ari/channels/externalMedia", self.external_media)
        app.router.add_delete("/ari/channels/{cid}", self.delete_channel)
        app.router.add_post("/ari/bridges", self.make_bridge)
        app.router.add_post("/ari/bridges/{bid}/addChannel", self.add_channel)
        app.router.add_delete("/ari/bridges/{bid}", self.delete_bridge)
        self.app = app

    async def originate(self, r):
        self.originated.append(dict(r.query))
        return web.json_response({"id": r.query["channelId"]})

    async def external_media(self, r):
        self.external.append(await r.json())
        return web.json_response({"id": "relay-chan"})

    async def make_bridge(self, r):
        return web.json_response({"id": "web-bridge"})

    async def add_channel(self, r):
        self.added.append((r.match_info["bid"], r.query["channel"]))
        return web.Response(status=204)

    async def delete_channel(self, r):
        self.deleted_channels.append(r.match_info["cid"])
        return web.Response(status=204)

    async def delete_bridge(self, r):
        self.deleted_bridges.append(r.match_info["bid"])
        return web.Response(status=204)


async def _wait_for(cond, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "condition not met in time"
        await asyncio.sleep(0.02)


def test_browser_is_connected_to_the_coordinator_and_call_ends_when_they_hang_up(monkeypatch):
    async def main():
        monkeypatch.setenv("OUTBOUND_CALLER_ID", "918031451001")
        monkeypatch.setenv("COORDINATOR_DIAL_TIMEOUT_SECONDS", "5")
        fake = FakeAri()
        async with TestServer(fake.app) as ari:
            base = f"http://127.0.0.1:{ari.port}/ari"
            monkeypatch.setattr(tr, "ARI_URL", base)
            monkeypatch.setattr(srv, "ARI_URL", base)
            server = srv.AsteriskServer()

            # "the AI pipeline": accepts the bridge's first AudioSocket connection and records what it hears
            agent = {"hangup": False, "frames": 0}

            async def fake_agent(reader, writer):
                try:
                    while True:
                        t, _ = await read_packet(reader)
                        agent["frames"] += t == TYPE_AUDIO
                        if t == TYPE_HANGUP:
                            agent["hangup"] = True
                            break
                except Exception:
                    pass
                writer.close()

            agent_srv = await asyncio.start_server(fake_agent, "127.0.0.1", 0)
            # "Asterisk's AudioSocket port": the REAL handler, which hands relay connections to the transfer manager
            asterisk_port_srv = await asyncio.start_server(server._handle_audiosocket_client, "127.0.0.1", 0)

            bridge = WebCallBridge("c1", "web-c1", WebCallSettings(audiosocket_port=agent_srv.sockets[0].getsockname()[1]), server)
            events = []
            bridge.on_event = events.append
            mic = asyncio.Queue()

            async def read_pcm():
                return await mic.get()

            run_task = asyncio.create_task(bridge.run(read_pcm))
            await _wait_for(lambda: bridge.call_uuid in server.web_bridges)

            transfer = asyncio.create_task(server.transfers.transfer(bridge.call_uuid, "+91 89253 55704"))
            await _wait_for(lambda: fake.originated)
            coord = fake.originated[0]
            assert coord["endpoint"] == "PJSIP/918925355704@plivo-outbound" and coord["callerId"] == "918031451001"

            # coordinator answers -> manager builds the Asterisk bridge and asks Asterisk for the relay connection
            await server._handle_ari_event({"type": "StasisStart", "channel": {"id": coord["channelId"], "name": "PJSIP/plivo-outbound-1"}})
            await _wait_for(lambda: fake.external)
            relay_uuid = fake.external[0]["data"]

            # the test now plays Asterisk: connect to the AudioSocket port and send the relay UUID
            ast_r, ast_w = await asyncio.open_connection("127.0.0.1", asterisk_port_srv.sockets[0].getsockname()[1])
            ast_w.write(encode_packet(TYPE_UUID, uuid.UUID(relay_uuid).bytes))
            await ast_w.drain()
            assert await asyncio.wait_for(transfer, 5) is True

            assert ("web-bridge", coord["channelId"]) in fake.added and ("web-bridge", "relay-chan") in fake.added
            assert events == ["call_transferred"] and bridge.relaying
            await _wait_for(lambda: agent["hangup"])                      # the AI was disconnected

            # browser microphone -> coordinator (arrives on the Asterisk side of the relay)
            await mic.put(FRAME)
            t, payload = await asyncio.wait_for(read_packet(ast_r), 3)
            assert t == TYPE_AUDIO and payload == FRAME
            # coordinator voice -> browser
            ast_w.write(encode_audio_frame(bytes([9]) * 320))
            await ast_w.drain()
            await _wait_for(lambda: len(bridge.outbound) > 0)

            # coordinator hangs up: Asterisk tells us, the call ends and the bridge is destroyed
            await server._handle_ari_event({"type": "StasisEnd", "channel": {"id": coord["channelId"]}})
            reason = await asyncio.wait_for(run_task, 5)
            assert reason == "coordinator_hangup"
            assert "relay-chan" in fake.deleted_channels and fake.deleted_bridges == ["web-bridge"]
            assert not server.transfers.is_transferred(bridge.call_uuid)

            ast_w.close()
            agent_srv.close()
            asterisk_port_srv.close()
            await server.close()
    asyncio.run(main())


def test_browser_hanging_up_drops_the_coordinator(monkeypatch):
    async def main():
        monkeypatch.setenv("OUTBOUND_CALLER_ID", "918031451001")
        fake = FakeAri()
        async with TestServer(fake.app) as ari:
            base = f"http://127.0.0.1:{ari.port}/ari"
            monkeypatch.setattr(tr, "ARI_URL", base)
            monkeypatch.setattr(srv, "ARI_URL", base)
            server = srv.AsteriskServer()
            agent_srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
            port_srv = await asyncio.start_server(server._handle_audiosocket_client, "127.0.0.1", 0)
            bridge = WebCallBridge("c2", "web-c2", WebCallSettings(audiosocket_port=agent_srv.sockets[0].getsockname()[1]), server)
            mic = asyncio.Queue()
            run_task = asyncio.create_task(bridge.run(mic.get))
            await _wait_for(lambda: bridge.call_uuid in server.web_bridges)
            transfer = asyncio.create_task(server.transfers.transfer(bridge.call_uuid, "+918925355704"))
            await _wait_for(lambda: fake.originated)
            coord = fake.originated[0]["channelId"]
            await server._handle_ari_event({"type": "StasisStart", "channel": {"id": coord, "name": "PJSIP/x"}})
            await _wait_for(lambda: fake.external)
            r, w = await asyncio.open_connection("127.0.0.1", port_srv.sockets[0].getsockname()[1])
            w.write(encode_packet(TYPE_UUID, uuid.UUID(fake.external[0]["data"]).bytes))
            await w.drain()
            assert await asyncio.wait_for(transfer, 5) is True

            bridge.request_end("browser_hangup")                           # user presses End call
            assert await asyncio.wait_for(run_task, 5) == "browser_hangup"
            assert coord in fake.deleted_channels and fake.deleted_bridges == ["web-bridge"]
            w.close(); agent_srv.close(); port_srv.close()
            await server.close()
    asyncio.run(main())
