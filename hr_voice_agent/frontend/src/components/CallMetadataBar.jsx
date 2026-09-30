/**
 * hr_voice_agent/frontend/src/components/CallMetadataBar.jsx
 *
 * Metadata bar displaying: call type badge (SIP / WebRTC), active LiveKit room ID,
 * and the currently active HR goal (e.g. "Leave Application").
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
        <span className="call-meta__value">{roomId || <em>—</em>}</span>
      </div>

      <div className="call-meta__item">
        <span className="call-meta__label">Active Goal</span>
        <span className="call-meta__value call-meta__value--goal">
          {activeGoal || <em>Idle</em>}
        </span>
      </div>
    </div>
  );
}
