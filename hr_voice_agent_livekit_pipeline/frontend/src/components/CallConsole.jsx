/**
 * hr_voice_agent/frontend/src/components/CallConsole.jsx
 *
 * Premium call controls: orb avatar with pulse rings, status dot,
 * animated timer, and Start / End buttons.
 */

export default function CallConsole({ connected, connecting, duration, onStart, onEnd, error }) {
  const minutes = String(Math.floor(duration / 60)).padStart(2, "0");
  const seconds = String(duration % 60).padStart(2, "0");

  return (
    <div
      id="call-console"
      className={`call-console${connected ? " call-console--connected" : ""}`}
      aria-label="Call Console"
    >
      {/* Orb */}
      <div className="call-console__orb-wrap">
        <span className="call-console__ring" />
        <span className="call-console__ring" />
        <span className="call-console__ring" />
        <div className="call-console__orb" aria-hidden="true">
          {connected ? "🔊" : "🎙️"}
        </div>
      </div>

      {/* Status */}
      <div className={`call-console__status call-console__status--${connected ? "connected" : "idle"}`}>
        <span className="call-console__status-dot" />
        {connecting ? "Connecting to Agent..." : connected ? "Call in Progress" : "Ready to Call"}
      </div>

      {/* Timer */}
      {connected && (
        <div className="call-console__timer" aria-label="Call duration">
          {minutes}:{seconds}
        </div>
      )}

      {/* Error Message */}
      {error && (
        <div style={{ color: "#f87171", fontSize: "0.85rem", padding: "6px 12px", background: "rgba(239, 68, 68, 0.15)", borderRadius: "8px", margin: "6px 0", textAlign: "center" }}>
          ⚠️ {error}
        </div>
      )}

      {/* Action button */}
      <button
        id="call-toggle-btn"
        className={`call-console__btn call-console__btn--${connected ? "end" : "start"}`}
        onClick={connected ? onEnd : onStart}
        disabled={connecting}
        aria-label={connected ? "End Call" : "Start Call"}
      >
        {connecting ? "⏳ Connecting..." : connected ? "📵  End Call" : "📞  Start Call"}
      </button>
    </div>
  );
}
