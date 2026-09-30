/**
 * hr_voice_agent/frontend/src/components/CallConsole.jsx
 *
 * Call button, connection status indicator, and call duration timer.
 * Provides "Start Call" / "End Call" controls with visual status feedback.
 */

export default function CallConsole({ connected, duration, onStart, onEnd }) {
  const minutes = String(Math.floor(duration / 60)).padStart(2, "0");
  const seconds = String(duration % 60).padStart(2, "0");

  return (
    <div className="call-console" id="call-console">
      <div className={`call-console__status call-console__status--${connected ? "connected" : "idle"}`}>
        <span className="call-console__status-dot" />
        {connected ? "Connected" : "Ready to Call"}
      </div>

      {connected && (
        <div className="call-console__timer" aria-label="Call duration">
          {minutes}:{seconds}
        </div>
      )}

      <button
        id="call-toggle-btn"
        className={`call-console__btn call-console__btn--${connected ? "end" : "start"}`}
        onClick={connected ? onEnd : onStart}
        aria-label={connected ? "End Call" : "Start Call"}
      >
        {connected ? "📵 End Call" : "📞 Start Call"}
      </button>
    </div>
  );
}
