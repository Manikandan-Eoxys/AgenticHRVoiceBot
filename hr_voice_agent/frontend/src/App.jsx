/**
 * hr_voice_agent/frontend/src/App.jsx
 *
 * Premium HR Voice Bot UI — orchestrates all panels.
 */

import { useState } from "react";
import CallConsole      from "./components/CallConsole.jsx";
import DialPad          from "./components/DialPad.jsx";
import LiveWaveform     from "./components/LiveWaveform.jsx";
import TranscriptFeed   from "./components/TranscriptFeed.jsx";
import CallMetadataBar  from "./components/CallMetadataBar.jsx";
import GoalsPanel       from "./components/GoalsPanel.jsx";
import { connectToRoom, disconnectFromRoom, sendDtmfDigit } from "./services/livekitClient.js";

export default function App() {
  const [connecting, setConnecting] = useState(false);
  const [error, setError]           = useState(null);
  const [connected, setConnected]   = useState(false);
  const [room, setRoom]             = useState(null);
  const [transcript, setTranscript] = useState([]);
  const [callMeta, setCallMeta]     = useState({ callType: "WebRTC", roomId: null, activeGoal: null });
  const [duration, setDuration]     = useState(0);
  const [timerRef, setTimerRef]     = useState(null);
  const [dtmfBuffer, setDtmfBuffer] = useState("");

  async function handleStartCall() {
    try {
      setError(null);
      setConnecting(true);
      const { room: newRoom } = await connectToRoom({
        onTranscript: (turn) => setTranscript((prev) => [...prev, turn]),
        onMeta:       (meta) => setCallMeta((prev) => ({ ...prev, ...meta })),
      });
      setRoom(newRoom);
      setConnected(true);
      setCallMeta((prev) => ({ ...prev, roomId: newRoom.name }));

      let secs = 0;
      const t = setInterval(() => setDuration(++secs), 1000);
      setTimerRef(t);
    } catch (err) {
      console.error("Failed to connect call:", err);
      setError(err?.message || "Could not connect to LiveKit room. Check browser mic permissions.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleEndCall() {
    if (timerRef) clearInterval(timerRef);
    await disconnectFromRoom(room);
    setRoom(null);
    setConnected(false);
    setDuration(0);
    setDtmfBuffer("");
    setCallMeta({ callType: "WebRTC", roomId: null, activeGoal: null });
    setTranscript([]);
    setError(null);
  }

  function handleDigit(digit) {
    if (room) sendDtmfDigit(room, digit);
    setDtmfBuffer((prev) => (prev + digit).slice(-8));
  }

  return (
    <div className="app">
      {/* ── Header ── */}
      <header className="app-header">
        <div className="app-header__brand">
          <div className="app-header__logo">🎙️</div>
          <div>
            <h1 className="app-title">HR Voice Agent</h1>
            <span className="app-subtitle">AI-Powered HR Assistant</span>
          </div>
        </div>

        <div className="app-header__pills">
          <span className="pill">LiveKit · WebRTC</span>
          <span className="pill">Deepgram STT/TTS</span>
          <span className="pill">Ollama LLM</span>
          {connected && (
            <span className="pill pill--live">
              <span className="pill__dot" />
              Live
            </span>
          )}
        </div>
      </header>

      {/* ── Main ── */}
      <main className="app-main">
        {/* Left column */}
        <div className="app-left">
          <CallConsole
            connected={connected}
            connecting={connecting}
            duration={duration}
            onStart={handleStartCall}
            onEnd={handleEndCall}
            error={error}
          />
          <LiveWaveform room={room} connected={connected} />
          <CallMetadataBar {...callMeta} />
          <GoalsPanel activeGoal={callMeta.activeGoal} />
        </div>

        {/* Right column */}
        <div className="app-right">
          <TranscriptFeed transcript={transcript} />
          <DialPad onDigit={handleDigit} disabled={!connected} dtmfBuffer={dtmfBuffer} />
        </div>
      </main>
    </div>
  );
}
