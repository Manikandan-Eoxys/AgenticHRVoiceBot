/**
 * hr_voice_agent/frontend/src/App.jsx
 *
 * Web caller interface shell — orchestrates the HR Voice Bot test client.
 * Renders CallConsole, DialPad, LiveWaveform, TranscriptFeed, and CallMetadataBar.
 */

import { useState } from "react";
import CallConsole      from "./components/CallConsole.jsx";
import DialPad          from "./components/DialPad.jsx";
import LiveWaveform     from "./components/LiveWaveform.jsx";
import TranscriptFeed   from "./components/TranscriptFeed.jsx";
import CallMetadataBar  from "./components/CallMetadataBar.jsx";
import { connectToRoom, disconnectFromRoom, sendDtmfDigit } from "./services/livekitClient.js";

export default function App() {
  const [connected, setConnected]     = useState(false);
  const [room, setRoom]               = useState(null);
  const [transcript, setTranscript]   = useState([]);
  const [callMeta, setCallMeta]       = useState({ callType: "WebRTC", roomId: null, activeGoal: null });
  const [duration, setDuration]       = useState(0);
  const [timerRef, setTimerRef]       = useState(null);

  async function handleStartCall() {
    const { room: newRoom, token } = await connectToRoom({
      onTranscript: (turn) => setTranscript((prev) => [...prev, turn]),
      onMeta:       (meta) => setCallMeta((prev) => ({ ...prev, ...meta })),
    });
    setRoom(newRoom);
    setConnected(true);
    setCallMeta((prev) => ({ ...prev, roomId: newRoom.name }));

    // Start duration timer
    let secs = 0;
    const t = setInterval(() => setDuration(++secs), 1000);
    setTimerRef(t);
  }

  async function handleEndCall() {
    if (timerRef) clearInterval(timerRef);
    await disconnectFromRoom(room);
    setRoom(null);
    setConnected(false);
    setDuration(0);
    setCallMeta({ callType: "WebRTC", roomId: null, activeGoal: null });
    setTranscript([]);
  }

  function handleDigit(digit) {
    if (room) sendDtmfDigit(room, digit);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">HR Voice Bot — Test Client</h1>
        <span className="app-subtitle">Interactive WebRTC Caller Simulator</span>
      </header>

      <main className="app-main">
        <div className="app-left">
          <CallConsole
            connected={connected}
            duration={duration}
            onStart={handleStartCall}
            onEnd={handleEndCall}
          />
          <LiveWaveform room={room} connected={connected} />
          <CallMetadataBar {...callMeta} />
        </div>

        <div className="app-right">
          <TranscriptFeed transcript={transcript} />
          <DialPad onDigit={handleDigit} disabled={!connected} />
        </div>
      </main>
    </div>
  );
}
