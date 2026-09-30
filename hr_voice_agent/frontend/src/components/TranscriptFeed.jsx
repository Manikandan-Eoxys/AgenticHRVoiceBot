/**
 * hr_voice_agent/frontend/src/components/TranscriptFeed.jsx
 *
 * Real-time transcribed conversation feed showing user speech and agent replies.
 * Each turn is displayed with role label, timestamp, and auto-scrolls to latest.
 */

import { useEffect, useRef } from "react";

export default function TranscriptFeed({ transcript }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  return (
    <div className="transcript-feed" id="transcript-feed" aria-label="Conversation Transcript">
      <h3 className="transcript-feed__title">Live Transcript</h3>

      {transcript.length === 0 ? (
        <div className="transcript-feed__empty">
          Start a call and speak to see the transcript here…
        </div>
      ) : (
        <div className="transcript-feed__messages">
          {transcript.map((turn, idx) => (
            <div
              key={idx}
              className={`transcript-feed__turn transcript-feed__turn--${turn.role}`}
            >
              <span className="transcript-feed__role">
                {turn.role === "user" ? "🎤 You" : "🤖 Agent"}
              </span>
              <span className="transcript-feed__text">{turn.text}</span>
              {turn.time && (
                <span className="transcript-feed__time">{turn.time}</span>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}
