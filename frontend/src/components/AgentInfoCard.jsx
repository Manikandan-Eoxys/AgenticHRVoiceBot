import "./AgentInfoCard.css";

/**
 * Purely presentational — matches the "Agent Info" panel from the design
 * reference. Static content, no props, no wiring needed.
 */
const CAPABILITIES = [
    "Leave management",
    "Attendance & policy",
    "Employee information",
    "General support",
];

export default function AgentInfoCard() {
    return (
        <div className="agent-info-card">
            <div className="agent-info-title">Agent Info</div>
            <div className="agent-info-header">
                <div className="agent-info-avatar">🤖</div>
                <div>
                    <div className="agent-info-name">HR Buddy</div>
                    <div className="agent-info-role">Agentic HR assistant</div>
                </div>
            </div>
            <div className="agent-info-caps">
                {CAPABILITIES.map((c) => (
                    <div className="agent-info-cap" key={c}>
                        <span className="cap-dot" />
                        {c}
                    </div>
                ))}
            </div>
        </div>
    );
}
