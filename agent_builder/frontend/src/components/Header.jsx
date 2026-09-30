import "./Header.css";
export default function Header({ connected, status }) {
    return (
        <header className="header">
            <div className="header-left">
                <div className="logo">🤖</div>
                <div>
                    <h1>HR Buddy</h1>
                    <p>Agentic HR Voice Assistant</p>
                </div>
            </div>
            <div className="header-right">
                <div className="conn-pill">
                    <span className={`dot ${connected ? "dot--on" : ""}`} />
                    {status}
                </div>
                <span className="badge">LiveKit</span>
                <span className="badge">GPT-5.5</span>
                <span className="badge">Deepgram</span>
                <span className="badge">ElevenLabs</span>
            </div>
        </header>
    );
}
