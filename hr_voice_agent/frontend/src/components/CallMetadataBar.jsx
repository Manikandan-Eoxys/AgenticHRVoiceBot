/**
 * hr_voice_agent/frontend/src/components/CallMetadataBar.jsx
 *
 * Three-column metadata strip: call type badge, room ID, active HR goal.
 */

export default function CallMetadataBar({ callType, roomId, activeGoal }) {
  return (
    <div className="call-meta" id="call-metadata-bar" aria-label="Call Metadata">
      <div className="call-meta__item">
        <span className="call-meta__label">Call Type</span>
        <span className={`call-meta__badge call-meta__badge--${callType === "SIP" ? "sip" : "webrtc"}`}>
          {callType || "WebRTC"}
        </span>
      </div>

      <div className="call-meta__item">
        <span className="call-meta__label">Room</span>
        <span className="call-meta__value">
          {roomId ? roomId : <em style={{ color: "var(--text-3)", fontStyle: "normal" }}>—</em>}
        </span>
      </div>

      <div className="call-meta__item">
        <span className="call-meta__label">Active Goal</span>
        <span className="call-meta__value call-meta__value--goal">
          {activeGoal || <em style={{ color: "var(--text-3)", fontStyle: "normal" }}>Idle</em>}
        </span>
      </div>
    </div>
  );
}
