import "./ConversationFlow.css";
const STEPS = [
    {
        id: "user",
        title: "User",
        icon: "👤",
    },
    {
        id: "stt",
        title: "Speech Recognition",
        icon: "🎤",
    },
    {
        id: "llm",
        title: "GPT-5.5",
        icon: "🧠",
    },
    {
        id: "tool",
        title: "Python Tools",
        icon: "⚙️",
    },
    {
        id: "db",
        title: "SQLite",
        icon: "🗄️",
    },
    {
        id: "tts",
        title: "ElevenLabs",
        icon: "🔊",
    },
    {
        id: "response",
        title: "Voice Response",
        icon: "✅",
    }
];
export default function ConversationFlow({
    activeStep = "user"
}) {
    return (
        <div className="flow-panel">
            <h3>Conversation Flow</h3>
            {
                STEPS.map((step, index) => (
                    <div key={step.id}>
                        <div
                            className={
                                step.id === activeStep
                                    ? "flow-step active"
                                    : "flow-step"
                            }
                        >
                            <div className="flow-icon">
                                {step.icon}
                            </div>
                            <span>
                                {step.title}
                            </span>
                        </div>
                        {
                            index < STEPS.length - 1 &&
                            <div className="flow-line" />
                        }
                    </div>
                ))
            }
        </div>
    );
}
