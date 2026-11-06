/**
 * hr_voice_agent/frontend/src/components/TranscriptFeed.jsx
 *
 * Real-time conversation transcript with bubbles, role labels, timestamps,
 * auto-scroll, and animated entry for each new turn.
 */

import { useEffect, useRef } from "react";

export default function TranscriptFeed({ transcript }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  return (
    <div
      className="transcript-feed"
      id="transcript-feed"
      aria-label="Conversation Transcript"
      aria-live="polite"
    >
      {/* Header */}
      <div className="transcript-feed__header">
        <h3 className="transcript-feed__title">
          💬 Live Transcript
          {transcript.length > 0 && (
            <span className="transcript-feed__count">{transcript.length}</span>
          )}
        </h3>
      </div>

      {/* Body */}
      {transcript.length === 0 ? (
        <div className="transcript-feed__empty">
          <span className="transcript-feed__empty-icon">🔇</span>
          <span>Start a call and speak to see the transcript here…</span>
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
