/**
 * hr_voice_agent/frontend/src/services/livekitClient.js
 *
 * WebRTC room connection, audio track publish/subscribe, and DTMF forwarding.
 * Connects to a LiveKit room using a JWT token from token_server.py.
 */

import { Room, RoomEvent, Track } from "livekit-client";
import { fetchToken } from "./tokenClient.js";

/** Global room reference */
let _room = null;

/**
 * Connect to a LiveKit room and set up audio + transcript listeners.
 *
 * @param {Object} opts
 * @param {Function} opts.onTranscript  - Called with {role, text, time} on each transcript turn
 * @param {Function} opts.onMeta        - Called with call metadata updates {roomId, activeGoal}
 * @returns {Promise<{room: Room, token: string}>}
 */
export async function connectToRoom({ onTranscript, onMeta } = {}) {
  const name = `WebUser-${Math.floor(Math.random() * 9000) + 1000}`;
  const { token, url } = await fetchToken(name);

  const room = new Room({
    audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
  });

  // ------------------------------------------------------------------
  // Listen for agent data channel messages (transcript, metadata)
  // ------------------------------------------------------------------
  room.on(RoomEvent.DataReceived, (payload, participant) => {
    try {
      const msg = JSON.parse(new TextDecoder().decode(payload));

      if (msg.type === "transcript" && onTranscript) {
        onTranscript({
          role: msg.role || "agent",
          text: msg.text,
          time: new Date().toLocaleTimeString(),
        });
      }

      if (msg.type === "metadata" && onMeta) {
        onMeta({ activeGoal: msg.activeGoal });
      }
    } catch (_) {}
  });

  // ------------------------------------------------------------------
  // Listen for LiveKit transcription events (speech-to-text)
  // ------------------------------------------------------------------
  room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
    if (!onTranscript) return;
    for (const seg of segments) {
      if (seg.text && (seg.final || seg.isFinal)) {
        const isAgent = participant?.isAgent || (participant?.identity && participant.identity.includes("agent"));
        onTranscript({
          role: isAgent ? "agent" : "user",
          text: seg.text,
          time: new Date().toLocaleTimeString(),
        });
      }
    }
  });

  // ------------------------------------------------------------------
  // Subscribe to agent audio track and play audio
  // ------------------------------------------------------------------
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    if (track.kind === Track.Kind.Audio) {
      const audioEl = track.attach();
      audioEl.style.display = "none";
      document.body.appendChild(audioEl);
    }
  });

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    try {
      track.detach().forEach((el) => el.remove());
    } catch (_) {}
  });

  // ------------------------------------------------------------------
  // Connect and publish microphone
  // ------------------------------------------------------------------
  console.log(`[LiveKit] Connecting to ${url}...`);
  await room.connect(url, token);
  console.log(`[LiveKit] Connected to room: ${room.name}`);

  try {
    await room.localParticipant.setMicrophoneEnabled(true);
    console.log("[LiveKit] Microphone enabled");
  } catch (micErr) {
    console.warn("[LiveKit] Could not enable microphone:", micErr);
  }

  _room = room;
  if (onMeta) onMeta({ roomId: room.name });

  return { room, token };
}

/**
 * Disconnect from the LiveKit room and clean up resources.
 * @param {Room} room
 */
export async function disconnectFromRoom(room) {
  const r = room || _room;
  if (!r) return;
  await r.localParticipant.setMicrophoneEnabled(false);
  r.disconnect();
  _room = null;
}

/**
 * Send a DTMF digit to the agent via WebRTC Data Channel.
 * The agent's DtmfCollector listens for: {"digit": "7"}
 *
 * @param {Room} room
 * @param {string} digit  - Single digit character (0-9, *, #)
 */
export function sendDtmfDigit(room, digit) {
  if (!room) return;
  const payload = JSON.stringify({ digit });
  const encoder = new TextEncoder();
  room.localParticipant.publishData(encoder.encode(payload), { reliable: true });
}
