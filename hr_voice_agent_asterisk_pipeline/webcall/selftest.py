"""webcall/selftest.py - end-to-end check without Asterisk, Deepgram or an LLM.

A fake AudioSocket "pipeline" (echoes a 440 Hz tone back when it hears audio) stands in for
AsteriskServer; an aiortc client stands in for the browser. Verifies:
  browser mic -> WebRTC -> bridge -> AudioSocket frames   (pipeline receives audio)
  AudioSocket tone -> bridge -> WebRTC -> browser         (browser receives non-silent audio)
Run:  python -m webcall.selftest
"""
from __future__ import annotations

import asyncio
import fractions
import sys
import uuid
from pathlib import Path

import numpy as np
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
import av

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audio.audiosocket import TYPE_AUDIO, TYPE_HANGUP, TYPE_UUID, encode_audio_frame, read_packet  # noqa: E402
from webcall.server import WebCallServer  # noqa: E402
from webcall.settings import WebCallSettings  # noqa: E402

stats = {"uuid": None, "frames_in": 0}


async def fake_pipeline(reader, writer):
    t, payload = await read_packet(reader)
    if t == TYPE_UUID:
        stats["uuid"] = str(uuid.UUID(bytes=payload))
    phase = 0
    try:
        while True:
            t, payload = await read_packet(reader)
            if t == TYPE_AUDIO:
                stats["frames_in"] += 1
                n = np.arange(160) + phase
                phase += 160
                tone = (np.sin(2 * np.pi * 440 * n / 8000) * 12000).astype(np.int16).tobytes()
                writer.write(encode_audio_frame(tone))
                await writer.drain()
            elif t == TYPE_HANGUP:
                break
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    writer.close()


class MicTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._ts = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        n = np.arange(960) + self._ts
        pcm = (np.sin(2 * np.pi * 300 * n / 48000) * 8000).astype(np.int16)
        f = av.AudioFrame(format="s16", layout="mono", samples=960)
        f.planes[0].update(pcm.tobytes())
        f.pts, f.sample_rate, f.time_base = self._ts, 48000, fractions.Fraction(1, 48000)
        self._ts += 960
        return f


async def main() -> int:
    pipe = await asyncio.start_server(fake_pipeline, "127.0.0.1", 0)
    pipe_port = pipe.sockets[0].getsockname()[1]
    settings = WebCallSettings(host="127.0.0.1", port=0, audiosocket_host="127.0.0.1",
                               audiosocket_port=pipe_port, max_seconds=30)
    srv = WebCallServer(settings)
    from aiohttp import web
    runner = web.AppRunner(srv.build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    pc = RTCPeerConnection()
    pc.addTrack(MicTrack())
    pc.createDataChannel("control")
    got = {"rms": 0.0, "frames": 0}
    remote_done = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        async def drain():
            while got["frames"] < 150:
                fr = await track.recv()
                got["frames"] += 1
                arr = fr.to_ndarray().astype(np.float32)
                got["rms"] = max(got["rms"], float(np.sqrt((arr ** 2).mean())))
            remote_done.set()
        asyncio.ensure_future(drain())

    await pc.setLocalDescription(await pc.createOffer())
    async with ClientSession() as http:
        r = await http.post(f"{base}/api/webcall/offer", json={"type": "offer", "sdp": pc.localDescription.sdp})
        body = await r.json()
        assert r.status == 200, body
        await pc.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type=body["type"]))
        try:
            await asyncio.wait_for(remote_done.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass
        await http.post(f"{base}/api/webcall/hangup", json={"callId": body["callId"]})
    await asyncio.sleep(0.5)
    await pc.close()
    await runner.cleanup()
    pipe.close()

    ok_up = stats["frames_in"] > 50
    ok_down = got["rms"] > 500
    ok_id = stats["uuid"] is not None
    print(f"handshake uuid          : {'OK' if ok_id else 'FAIL'} {stats['uuid']}")
    print(f"browser -> pipeline     : {'OK' if ok_up else 'FAIL'} ({stats['frames_in']} frames)")
    print(f"pipeline -> browser     : {'OK' if ok_down else 'FAIL'} (peak RMS {got['rms']:.0f}, {got['frames']} frames)")
    print(f"call slot released      : {'OK' if not srv.calls else 'FAIL'}")
    return 0 if (ok_up and ok_down and ok_id and not srv.calls) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
