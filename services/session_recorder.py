"""
services/session_recorder.py

Automatic Local Call Recording and Clean Conversation Logging for LiveKit Voice Agent.

Supported Modes (configured via RECORDING_COORDINATOR_MODE in .env or config.py):
    - "record_all" (Approach 2, default): Full 3-way recording of Caller + Agent + Coordinator
      with dedicated per-participant audio streams and dynamic FFmpeg multi-channel mixing.
    - "cutoff" (Approach 1): Stops/excludes recording when the call is forwarded to the coordinator.

Directory Structure:
    Recordings/
      └── <DD-MM-YYYY>/
           └── User-<SessionIndex>-<HH.MM>/
                ├── Conversation_Audio.mp3
                └── Conversation_log
"""

import asyncio
import logging
import os
import re
import subprocess
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

from livekit import agents, rtc

logger = logging.getLogger("session_recorder")

DEFAULT_RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "Recordings"
FFMPEG_PATH = "/usr/local/bin/ffmpeg" if os.path.exists("/usr/local/bin/ffmpeg") else "ffmpeg"


class TrackRecorder:
    """Manages writing PCM audio for a single participant into a dedicated WAV file."""

    def __init__(self, path: Path, sample_rate: int = 48000, silence_pad_seconds: float = 0.0):
        self.path = path
        self.sample_rate = sample_rate
        self.lock = asyncio.Lock()
        self.wav_file: Optional[wave.Wave_write] = wave.open(str(path), "wb")
        self.wav_file.setnchannels(1)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(sample_rate)

        # Pad initial silence if this participant joined mid-call (e.g. coordinator)
        if 0.1 < silence_pad_seconds < 7200.0:
            remaining_samples = int(silence_pad_seconds * sample_rate)
            chunk_size = sample_rate  # 1 second chunk
            silence_chunk = b"\x00" * (chunk_size * 2)
            while remaining_samples > 0:
                to_write = min(remaining_samples, chunk_size)
                if to_write == chunk_size:
                    self.wav_file.writeframes(silence_chunk)
                else:
                    self.wav_file.writeframes(b"\x00" * (to_write * 2))
                remaining_samples -= to_write

    async def write(self, data: bytes) -> None:
        async with self.lock:
            if self.wav_file:
                self.wav_file.writeframes(data)

    async def close(self) -> None:
        async with self.lock:
            if self.wav_file:
                try:
                    self.wav_file.close()
                except Exception:
                    pass
                self.wav_file = None


class SessionRecorder:
    """
    Records call audio at 48000 Hz with multi-participant synchronization
    and writes clean conversation transcripts.
    """

    def __init__(
        self,
        base_dir: Path | str = DEFAULT_RECORDINGS_DIR,
        coordinator_mode: Optional[str] = None,
    ):
        self.base_dir = Path(base_dir)
        self.coordinator_mode = (
            coordinator_mode
            or os.environ.get("RECORDING_COORDINATOR_MODE", "record_all")
        ).strip().lower()

        self.session_dir: Optional[Path] = None
        self.mp3_path: Optional[Path] = None
        self.log_path: Optional[Path] = None

        self.sample_rate = 48000  # 48 kHz standard WebRTC audio rate
        self.num_channels = 1

        self._tracks: dict[str, TrackRecorder] = {}
        self._tracks_lock = asyncio.Lock()

        self._log_entries: list[str] = []
        self._start_time: float = time.time()
        self._start_time_str: str = ""
        self._session_name: str = ""
        self._room_name: str = ""
        self._is_closed = False

        self._audio_tasks: list[asyncio.Task] = []
        self._active_streams: list[rtc.AudioStream] = []

    def prepare_session_folder(self, room_name: str = "") -> Path:
        """
        Calculates the date and User-<Index>-<HH.MM> folder path and creates it.
        """
        now = datetime.now()
        date_str = now.strftime("%d-%m-%Y")  # e.g., 27-08-2026
        time_str = now.strftime("%H.%M")     # e.g., 18.30
        self._start_time_str = now.strftime("%d-%m-%Y %H:%M:%S")
        self._room_name = room_name

        date_folder = self.base_dir / date_str
        date_folder.mkdir(parents=True, exist_ok=True)

        # Determine next session number for today
        existing_folders = [
            f.name for f in date_folder.iterdir() if f.is_dir() and f.name.startswith("User-")
        ]

        max_index = 0
        for f_name in existing_folders:
            match = re.match(r"^User-(\d+)", f_name)
            if match:
                max_index = max(max_index, int(match.group(1)))

        session_index = max_index + 1
        self._session_name = f"User-{session_index}-{time_str}"
        self.session_dir = date_folder / self._session_name
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.mp3_path = self.session_dir / "Conversation_Audio.mp3"
        self.log_path = self.session_dir / "Conversation_log"

        # Write initial header in Conversation_log
        header = (
            f"================================================================================\n"
            f"HR VOICE AGENT CONVERSATION LOG\n"
            f"Session: {self._session_name} | Date & Time: {self._start_time_str}\n"
            f"Room: {self._room_name} | Coordinator Mode: {self.coordinator_mode}\n"
            f"================================================================================\n"
        )
        self._append_log(header)

        logger.info(
            f"📁 [Recorder] Created session folder: {self.session_dir} (mode={self.coordinator_mode})"
        )
        return self.session_dir

    async def _get_or_create_track(self, track_id: str, label: str) -> Optional[TrackRecorder]:
        """Creates or returns an existing TrackRecorder for a specific participant track."""
        async with self._tracks_lock:
            if self._is_closed:
                return None
            if track_id in self._tracks:
                return self._tracks[track_id]

            # In cutoff mode, do not record coordinator tracks
            if self.coordinator_mode == "cutoff" and label.startswith("coordinator"):
                logger.info(
                    f"⏭️ [Recorder] Mode is 'cutoff': skipping audio recording for coordinator track ({track_id})."
                )
                return None

            elapsed = max(0.0, time.time() - self._start_time)
            # Silence padding aligns late-joining coordinator audio with the timeline
            silence_pad = elapsed if label.startswith("coordinator") else 0.0

            wav_path = self.session_dir / f"{label}_{int(time.time())}.wav"
            track_rec = TrackRecorder(
                wav_path,
                sample_rate=self.sample_rate,
                silence_pad_seconds=silence_pad,
            )
            self._tracks[track_id] = track_rec
            logger.info(
                f"🎙️ [Recorder] Created separate track '{label}' at {wav_path.name} "
                f"(offset padding={silence_pad:.1f}s)"
            )
            return track_rec

    async def _record_audio_track(self, track: rtc.Track, track_id: str, label: str) -> None:
        """Captures 48kHz audio frames from a given track into its dedicated WAV file."""
        track_rec = await self._get_or_create_track(track_id, label)
        if not track_rec:
            return

        try:
            stream = rtc.AudioStream(
                track,
                sample_rate=self.sample_rate,
                num_channels=self.num_channels,
            )
            self._active_streams.append(stream)
            async for frame_event in stream:
                if self._is_closed:
                    break
                await track_rec.write(frame_event.frame.data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Audio stream for {label} ended: {e}")

    async def start(self, job_ctx: agents.JobContext, session: agents.AgentSession) -> None:
        """
        Attaches audio streams and conversation event listeners to the active session.
        """
        self._start_time = time.time()
        self.prepare_session_folder(room_name=job_ctx.room.name)

        # 1. Attach Remote Track Listeners (User & Coordinator)
        @job_ctx.room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.TrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                is_coord = participant.identity.startswith("coordinator-")
                label = "coordinator" if is_coord else "user"
                track_id = f"{label}_{participant.identity}_{track.sid}"
                logger.info(
                    f"🎙️ [Recorder] Remote track subscribed: {participant.identity} (label={label})"
                )
                t = asyncio.create_task(self._record_audio_track(track, track_id, label))
                self._audio_tasks.append(t)

        # 2. Attach Local Track Listener (Agent)
        @job_ctx.room.on("local_track_published")
        def _on_local_track_published(
            publication: rtc.LocalTrackPublication,
            track: rtc.LocalTrack,
        ):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                track_id = f"agent_local_{track.sid}"
                logger.info("🎙️ [Recorder] Local agent audio track published")
                t = asyncio.create_task(self._record_audio_track(track, track_id, "agent"))
                self._audio_tasks.append(t)

        # Check existing remote tracks
        for participant in job_ctx.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    is_coord = participant.identity.startswith("coordinator-")
                    label = "coordinator" if is_coord else "user"
                    track_id = f"{label}_{participant.identity}_{pub.track.sid}"
                    t = asyncio.create_task(self._record_audio_track(pub.track, track_id, label))
                    self._audio_tasks.append(t)

        # Check existing local tracks
        for pub in job_ctx.room.local_participant.track_publications.values():
            if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                track_id = f"agent_local_{pub.track.sid}"
                t = asyncio.create_task(self._record_audio_track(pub.track, track_id, "agent"))
                self._audio_tasks.append(t)

        # 3. Attach Clean Conversation Transcript Listeners
        @session.on("conversation_item_added")
        def _on_item_added(ev):
            role = getattr(ev.item, "role", "") if hasattr(ev, "item") else ""
            text = getattr(ev.item, "text", "") if hasattr(ev, "item") else ""
            if not text and hasattr(ev, "item") and hasattr(ev.item, "content"):
                text = str(ev.item.content)

            clean_text = text.strip()
            if not clean_text:
                return

            now_str = datetime.now().strftime("%H:%M:%S")
            if role == "user":
                self._append_log(f"[{now_str}] [User]: {clean_text}")
            elif role == "assistant":
                self._append_log(f"[{now_str}] [Assistant]: {clean_text}")

        # 4. Register Shutdown Callback
        job_ctx.add_shutdown_callback(self.stop)

    def _append_log(self, text: str) -> None:
        """Appends a log line to memory and flushes to Conversation_log."""
        self._log_entries.append(text)
        try:
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
        except Exception as e:
            logger.warning(f"Failed to write conversation log: {e}")

    async def stop(self, *args) -> None:
        """
        Finalizes recording, closes streams, and mixes all active tracks via FFmpeg at 48kHz 1.0x speed.
        """
        if self._is_closed:
            return
        self._is_closed = True

        logger.info(
            f"🛑 [Recorder] Stopping session recording for {self._session_name} "
            f"(mode={self.coordinator_mode})..."
        )

        # 1. Close audio streams and cancel background tasks
        for stream in self._active_streams:
            try:
                stream.close()
            except Exception:
                pass

        for t in self._audio_tasks:
            t.cancel()

        # 2. Close all track WAV files safely
        valid_wav_paths: list[Path] = []
        for track_rec in list(self._tracks.values()):
            await track_rec.close()
            if track_rec.path.exists() and os.path.getsize(track_rec.path) > 44:
                valid_wav_paths.append(track_rec.path)

        # 3. Mix all separate participant tracks using FFmpeg
        try:
            if len(valid_wav_paths) >= 2:
                cmd = [FFMPEG_PATH, "-y"]
                for p in valid_wav_paths:
                    cmd.extend(["-i", str(p)])
                cmd.extend([
                    "-filter_complex",
                    f"amix=inputs={len(valid_wav_paths)}:duration=longest:dropout_transition=2",
                    "-ar",
                    "48000",
                    "-codec:a",
                    "libmp3lame",
                    "-qscale:a",
                    "2",
                    str(self.mp3_path),
                ])
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                logger.info(
                    f"✅ [Recorder] Mixed {len(valid_wav_paths)} tracks cleanly to MP3: {self.mp3_path}"
                )
            elif len(valid_wav_paths) == 1:
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    str(valid_wav_paths[0]),
                    "-ar",
                    "48000",
                    "-codec:a",
                    "libmp3lame",
                    "-qscale:a",
                    "2",
                    str(self.mp3_path),
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                logger.info(f"✅ [Recorder] Converted single track to MP3: {self.mp3_path}")
            else:
                # Create empty silent 1s MP3
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=mono",
                    "-t",
                    "1",
                    "-codec:a",
                    "libmp3lame",
                    str(self.mp3_path),
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception as e:
            logger.warning(f"FFmpeg MP3 mixing failed: {e}")
        finally:
            # Clean up temporary raw wav files
            for track_rec in self._tracks.values():
                if track_rec.path.exists():
                    try:
                        track_rec.path.unlink()
                    except Exception:
                        pass

        # 4. Write Session Completion Summary in Conversation_log
        duration = int(time.time() - self._start_time)
        mins, secs = divmod(duration, 60)
        end_time_str = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        self._append_log(
            f"\n================================================================================\n"
            f"Session Completed: {end_time_str}\n"
            f"Total Call Duration: {mins}m {secs}s\n"
            f"Recording Mode: {self.coordinator_mode}\n"
            f"================================================================================\n"
        )
        logger.info(f"✅ [Recorder] Session artifacts saved in: {self.session_dir}")
