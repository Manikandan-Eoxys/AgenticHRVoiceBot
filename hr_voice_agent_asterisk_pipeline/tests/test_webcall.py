"""Offline tests for the web call edge (no aiortc/Asterisk needed)."""
import asyncio
import unittest

from webcall.audio_io import FRAME_BYTES, FrameQueue, FrameSlicer
from webcall.settings import WebCallSettings


class SlicerQueueTests(unittest.TestCase):
    def test_slicer_cuts_exact_frames(self):
        s = FrameSlicer()
        self.assertEqual(s.push(b"a" * 100), [])
        frames = s.push(b"b" * (FRAME_BYTES * 2))
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(f) == FRAME_BYTES for f in frames))

    def test_queue_prebuffers_then_plays_and_caps_latency(self):
        q = FrameQueue(max_frames=3, prebuffer=2)
        q.push(b"1"); self.assertIsNone(q.pop())
        q.push(b"2"); self.assertEqual(q.pop(), b"1")
        for i in range(10):
            q.push(bytes([i]))
        self.assertEqual(len(q), 3)
        self.assertGreater(q.dropped, 0)


class SettingsTests(unittest.TestCase):
    def test_ice_servers_parsed_and_bad_json_ignored(self):
        import os
        os.environ["WEBCALL_ICE_SERVERS"] = '[{"urls":"stun:x"},{"nope":1}]'
        self.assertEqual(WebCallSettings.from_env().ice_servers, ({"urls": "stun:x"},))
        os.environ["WEBCALL_ICE_SERVERS"] = "not json"
        self.assertEqual(WebCallSettings.from_env().ice_servers, ())
        del os.environ["WEBCALL_ICE_SERVERS"]


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_is_idempotent_and_unreachable_pipeline_reported(self):
        from webcall.bridge import WebCallBridge
        b = WebCallBridge("c1", "web-c1", WebCallSettings(audiosocket_port=1))
        self.assertEqual(await b.run(lambda: asyncio.sleep(0, b"")), "pipeline_unreachable")
        self.assertEqual(await b.close("again"), "pipeline_unreachable")

    def test_digit_validation(self):
        from webcall.bridge import WebCallBridge
        b = WebCallBridge("c1", "web-c1", WebCallSettings())
        self.assertFalse(b.send_digit("5"))      # no server attached
        self.assertFalse(b.send_digit("x"))


if __name__ == "__main__":
    unittest.main()
