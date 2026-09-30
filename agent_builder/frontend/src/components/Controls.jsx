import "./Controls.css";

export default function Controls({
    connected,
    onConnect,
    onDisconnect,
    micState,
    onToggleMic,
}) {
    const isMuted = micState?.isMuted;
    const micError = micState?.error;

    return (
        <div className="controls-wrapper">
            <div className="controls">
                {!connected ? (
                    <button className="btn btn-connect" onClick={onConnect}>
                        🎙️ Connect & Talk
                    </button>
                ) : (
                    <div className="controls-active">
                        <button
                            className={`btn ${isMuted ? "btn-mic-off" : "btn-mic-on"}`}
                            onClick={onToggleMic}
                            title={isMuted ? "Unmute Microphone" : "Mute Microphone"}
                        >
                            {isMuted ? "🔇 Mic Muted" : "🎙️ Mic Active"}
                        </button>

                        <button className="btn btn-disconnect" onClick={onDisconnect}>
                            Disconnect
                        </button>
                    </div>
                )}
            </div>

            {connected && micError && (
                <div className="mic-error-alert">
                    ⚠️ {micError} — Check browser microphone permissions.
                </div>
            )}
        </div>
    );
}

