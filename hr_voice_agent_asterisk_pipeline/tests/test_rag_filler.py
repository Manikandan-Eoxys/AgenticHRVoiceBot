"""
tests/test_rag_filler.py

Unit tests for services/rag_filler.py.
Covers load_clips (missing folder, empty folder, valid 16-bit PCM WAV)
and RagFiller playback (text mode, audio mode with clips, audio fallback to text,
llm warning fallback, duplicate turn prevention, and exception safety).
"""

import asyncio
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock

# Ensure hr_voice_agent root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.rag_retriever_service import FILLER_TEXTS, RagFiller, load_clips


def _create_wav_file(path: Path, sample_rate: int = 24000, sample_width: int = 2, duration_ms: int = 50) -> None:
    """Helper to generate a dummy mono PCM WAV file."""
    channels = 1
    num_frames = int(sample_rate * (duration_ms / 1000.0))
    raw_data = b"\x00" * (num_frames * channels * sample_width)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_data)


class TestLoadClips(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_missing_and_empty_folder(self):
        """Test missing folder and empty folder return empty list."""
        self.assertEqual(load_clips("/non/existent/path/xyz"), [])
        self.assertEqual(load_clips(""), [])
        self.assertEqual(load_clips(str(self.test_dir)), [])

    def test_valid_16bit_pcm_wav(self):
        """Test loading valid 16-bit PCM WAV creates correct transcript and 10ms frame stream."""
        wav_path = self.test_dir / "let_me_look_that_up.wav"
        _create_wav_file(wav_path, sample_rate=24000, sample_width=2, duration_ms=50)

        clips = load_clips(str(self.test_dir))
        self.assertEqual(len(clips), 1)

        transcript, factory = clips[0]
        self.assertEqual(transcript, "Let me look that up for you.")

        # Test streaming frames
        async def _test_frames():
            frames = []
            async for frame in factory():
                frames.append(frame)
            return frames

        frames = asyncio.run(_test_frames())
        # 50 ms at 24000 Hz = 5 frames of 10 ms (240 samples per channel)
        self.assertEqual(len(frames), 5)
        self.assertEqual(frames[0].samples_per_channel, 240)
        self.assertEqual(frames[0].sample_rate, 24000)

    def test_skips_invalid_sample_width(self):
        """Test WAV files that are not 16-bit PCM (e.g. 8-bit, sample width 1) are skipped."""
        invalid_wav = self.test_dir / "invalid_8bit.wav"
        _create_wav_file(invalid_wav, sample_rate=16000, sample_width=1, duration_ms=50)

        clips = load_clips(str(self.test_dir))
        self.assertEqual(clips, [])


class TestRagFiller(unittest.TestCase):
    def test_text_mode_playback(self):
        """Test text mode plays random phrase with allow_interruptions=True and add_to_chat_ctx=False."""
        session = MagicMock()
        filler = RagFiller(mode="text")

        filler.play(session)
        session.say.assert_called_once()
        args, kwargs = session.say.call_args
        self.assertIn(args[0], FILLER_TEXTS)
        self.assertTrue(kwargs.get("allow_interruptions"))
        self.assertFalse(kwargs.get("add_to_chat_ctx"))

    def test_prevent_two_fillers_in_same_turn(self):
        """Test that play() does not play twice within the same turn until reset_turn() is called."""
        session = MagicMock()
        filler = RagFiller(mode="text")

        filler.play(session)
        self.assertEqual(session.say.call_count, 1)

        # Second play in same turn -> ignored
        filler.play(session)
        self.assertEqual(session.say.call_count, 1)

        # After turn reset -> can play again
        filler.reset_turn()
        filler.play(session)
        self.assertEqual(session.say.call_count, 2)

    def test_audio_mode_with_clips(self):
        """Test audio mode plays audio stream with clip transcript."""
        session = MagicMock()
        dummy_factory = MagicMock()
        filler = RagFiller(mode="audio", clips=[("One moment while I check.", dummy_factory)])

        filler.play(session)
        session.say.assert_called_once()
        args, kwargs = session.say.call_args
        self.assertEqual(args[0], "One moment while I check.")
        self.assertTrue(kwargs.get("allow_interruptions"))
        self.assertFalse(kwargs.get("add_to_chat_ctx"))
        self.assertIn("audio", kwargs)

    def test_audio_mode_fallback_when_no_clips(self):
        """Test audio mode falls back to text when no clips are available."""
        session = MagicMock()
        filler = RagFiller(mode="audio", clips=[])

        filler.play(session)
        session.say.assert_called_once()
        args, kwargs = session.say.call_args
        self.assertIn(args[0], FILLER_TEXTS)
        self.assertNotIn("audio", kwargs)

    def test_llm_mode_fallback_to_text(self):
        """Test llm mode falls back to text mode."""
        session = MagicMock()
        filler = RagFiller(mode="llm")

        filler.play(session)
        session.say.assert_called_once()
        args, kwargs = session.say.call_args
        self.assertIn(args[0], FILLER_TEXTS)

    def test_never_raises_on_session_error(self):
        """Test play() absorbs any exception thrown by session without raising."""
        broken_session = MagicMock()
        broken_session.say.side_effect = RuntimeError("Session disconnected")

        filler = RagFiller(mode="text")
        # Should not raise exception
        filler.play(broken_session)


if __name__ == "__main__":
    unittest.main()
