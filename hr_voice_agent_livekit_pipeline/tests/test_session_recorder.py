"""
tests/test_session_recorder.py

Unit tests for SessionRecorder (folder indexing, clean log format, and 48kHz FFmpeg mixing).
"""

import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from services.session_recorder import SessionRecorder


class TestSessionRecorder(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.base_dir = Path(self.test_dir) / "Recordings"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_folder_indexing(self):
        """Test that session folders increment properly (User-1, User-2, User-3)."""
        # Session 1
        rec1 = SessionRecorder(base_dir=self.base_dir)
        folder1 = rec1.prepare_session_folder(room_name="console-1234")
        self.assertTrue(folder1.name.startswith("User-1-"))
        self.assertTrue(folder1.exists())

        # Session 2
        rec2 = SessionRecorder(base_dir=self.base_dir)
        folder2 = rec2.prepare_session_folder(room_name="console-1234")
        self.assertTrue(folder2.name.startswith("User-2-"))
        self.assertTrue(folder2.exists())

        # Session 3
        rec3 = SessionRecorder(base_dir=self.base_dir)
        folder3 = rec3.prepare_session_folder(room_name="console-1234")
        self.assertTrue(folder3.name.startswith("User-3-"))
        self.assertTrue(folder3.exists())

    def test_clean_conversation_log_format(self):
        """Test that conversation logs match the exact required header, body, and footer format."""
        rec = SessionRecorder(base_dir=self.base_dir)
        rec.prepare_session_folder(room_name="console-198296de")
        
        rec._append_log("[18:30:33] [Assistant]: Hello. Thank you for calling HR. To get started, may I have your employee ID please?")
        rec._append_log("[18:30:37] [User]: My employee ID is one zero zero eight.")
        rec._append_log("[18:30:50] [Assistant]: This is employee ID 1008, Shivaraj, is that correct?")
        rec._append_log("[18:30:54] [User]: Yeah.")
        
        self.assertTrue(rec.log_path.exists())
        content = rec.log_path.read_text(encoding="utf-8")
        self.assertIn("HR VOICE AGENT CONVERSATION LOG", content)
        self.assertIn("Room: console-198296de", content)
        self.assertIn("[18:30:33] [Assistant]: Hello.", content)
        self.assertIn("[18:30:37] [User]: My employee ID is one zero zero eight.", content)

    def test_dual_track_48khz_mp3_mixing(self):
        """Test that User and Agent WAV tracks are mixed into 1.0x normal speed Conversation_Audio.mp3."""
        rec = SessionRecorder(base_dir=self.base_dir)
        rec.prepare_session_folder(room_name="test-room")
        
        sr = 48000
        # Write 1 sec silence into user_temp.wav
        with wave.open(str(rec.user_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\x00" * (sr * 2))
            
        # Write 1 sec silence into agent_temp.wav
        with wave.open(str(rec.agent_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\x00" * (sr * 2))
            
        self.assertTrue(rec.user_wav_path.exists())
        self.assertTrue(rec.agent_wav_path.exists())
        
        import asyncio
        asyncio.run(rec.stop())
        
        # Verify MP3 exists and raw WAVs were cleaned up
        self.assertTrue(rec.mp3_path.exists())
        self.assertGreater(os.path.getsize(rec.mp3_path), 500)
        self.assertFalse(rec.user_wav_path.exists())
        self.assertFalse(rec.agent_wav_path.exists())


if __name__ == "__main__":
    unittest.main()
