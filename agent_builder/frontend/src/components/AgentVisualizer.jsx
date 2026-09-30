import "./AgentVisualizer.css";
const STATES = {
    disconnected: {
        color: "#545C6B",
        label: "Disconnected",
        icon: "⚫",
    },
    connected: {
        color: "#34D8B0",
        label: "Connected",
        icon: "🟢",
    },
    listening: {
        color: "#34D8B0",
        label: "Listening...",
        icon: "🎤",
    },
    thinking: {
        color: "#FFB454",
        label: "Thinking...",
        icon: "🧠",
    },
    tool: {
        color: "#FFB454",
        label: "Calling Tool...",
        icon: "⚙️",
    },
    speaking: {
        color: "#34D8B0",
        label: "Speaking...",
        icon: "🔊",
    },
};
export default function AgentVisualizer({
    state = "connected",
}) {
    const current =
        STATES[state] || STATES.connected;
    return (
        <div className="agent-card">
            <div
                className={`voice-orb ${state}`}
                style={{
                    borderColor: current.color,
                }}
            >
                <span>{current.icon}</span>
            </div>
            <h2>{current.label}</h2>
            <p>
                HR Buddy is ready to assist you.
            </p>
        </div>
    );
}
