import { useState, useEffect } from "react";
import { RoomEvent, Track } from "livekit-client";
import Header from "./Header";
import Controls from "./Controls";
import StatusBar from "./StatusBar";
import Transcript from "./Transcript";
import AgentVisualizer from "./AgentVisualizer";
import AgentInfoCard from "./AgentInfoCard";
import MetricsPanel from "./MetricsPanel";
import ToolCalls from "./ToolCalls";
import ConversationFlow from "./ConversationFlow";
import {
    connectToRoom,
    disconnectRoom,
    setCallbacks,
    getRoom,
    toggleMicrophone,
} from "../services/livekitService";

function formatDuration(totalSeconds) {
    const m = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
    const s = Math.floor(totalSeconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
}

function capitalize(str) {
    if (!str) return "--";
    return str.charAt(0).toUpperCase() + str.slice(1);
}

export default function VoiceAssistant() {
    const [connected, setConnected] = useState(false);
    const [status, setStatus] = useState("Disconnected");
    const [messages, setMessages] = useState([]);
    const [agentState, setAgentState] = useState("idle");
    const [micState, setMicState] = useState({ enabled: false, isMuted: true, error: null });
    const [errorDetail, setErrorDetail] = useState(null);
    // SIP mode: when true the browser joins "hr-sip-live" — the same fixed room
    // that the MicroSIP dispatch rule routes calls into, so transcript is shared.
    const [sipMode, setSipMode] = useState(false);

    // Real client-side session timer, starts/stops with `connected`.
    const [durationSec, setDurationSec] = useState(0);
    useEffect(() => {
        if (!connected) {
            setDurationSec(0);
            return;
        }
        const id = setInterval(() => setDurationSec((d) => d + 1), 1000);
        return () => clearInterval(id);
    }, [connected]);

    // Real room name + participant count, read from the live LiveKit Room
    // object via the getRoom() you already export from livekitService.js.
    const [roomName, setRoomName] = useState("--");
    const [participants, setParticipants] = useState(0);
    useEffect(() => {
        if (!connected) {
            setRoomName("--");
            setParticipants(0);
            return;
        }
        const room = getRoom();
        if (!room) return;

        setRoomName(room.name || "--");
        const updateCount = () => setParticipants(room.numParticipants + 1); // +1 for local participant
        updateCount();

        room.on(RoomEvent.ParticipantConnected, updateCount);
        room.on(RoomEvent.ParticipantDisconnected, updateCount);
        return () => {
            room.off(RoomEvent.ParticipantConnected, updateCount);
            room.off(RoomEvent.ParticipantDisconnected, updateCount);
        };
    }, [connected]);

    // Real connection quality, pushed by LiveKit's own server-side heuristic.
    // Values are the string enum LiveKit sends: 'excellent' | 'good' | 'poor' | 'unknown'.
    const [connectionQuality, setConnectionQuality] = useState("unknown");
    useEffect(() => {
        if (!connected) {
            setConnectionQuality("unknown");
            return;
        }
        const room = getRoom();
        if (!room) return;

        const onQualityChanged = (quality, participant) => {
            // Only care about our own connection, not remote participants'.
            if (participant === room.localParticipant) {
                setConnectionQuality(quality);
            }
        };
        room.on(RoomEvent.ConnectionQualityChanged, onQualityChanged);
        return () => room.off(RoomEvent.ConnectionQualityChanged, onQualityChanged);
    }, [connected]);

    // Real round-trip latency, sampled every 3s from the local mic track's
    // WebRTC stats (candidate-pair.currentRoundTripTime). This depends on
    // getRTCStatsReport() being available on your installed livekit-client
    // version/browser — falls back to "--" instead of crashing if not.
    const [latencyMs, setLatencyMs] = useState(null);
    useEffect(() => {
        if (!connected) {
            setLatencyMs(null);
            return;
        }
        const room = getRoom();
        if (!room) return;

        async function sampleLatency() {
            try {
                const micPub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
                const track = micPub?.track;
                if (!track || typeof track.getRTCStatsReport !== "function") {
                    setLatencyMs(null);
                    return;
                }
                const report = await track.getRTCStatsReport();
                let rtt = null;
                report.forEach((stat) => {
                    if (
                        stat.type === "candidate-pair" &&
                        stat.state === "succeeded" &&
                        typeof stat.currentRoundTripTime === "number"
                    ) {
                        rtt = Math.round(stat.currentRoundTripTime * 1000);
                    }
                });
                setLatencyMs(rtt);
            } catch (err) {
                console.warn("Latency sampling unavailable:", err);
                setLatencyMs(null);
            }
        }

        sampleLatency();
        const id = setInterval(sampleLatency, 3000);
        return () => clearInterval(id);
    }, [connected]);

    // Register callbacks once so livekitService can push updates into React state
    useEffect(() => {
        setCallbacks({
            onMessage: (update) => {
                setMessages((prev) => {
                    const idx = prev.findIndex((m) => m.id === update.id);
                    if (idx === -1) {
                        return [
                            ...prev,
                            { ...update, timestamp: new Date().toISOString() },
                        ];
                    }
                    const next = [...prev];
                    next[idx] = { ...next[idx], text: update.text, final: update.final };
                    return next;
                });
            },
            onAgentState: (state) => {
                setAgentState(state);
                setStatus(
                    state.charAt(0).toUpperCase() + state.slice(1)
                );
            },
            onMicState: (micInfo) => {
                setMicState((prev) => ({ ...prev, ...micInfo }));
            },
        });
    }, []);

    async function connect() {
        setErrorDetail(null);
        try {
            setStatus("Connecting...");
            setMessages([]);
            setMicState({ enabled: false, isMuted: true, error: null });
            // Pass mode so livekitService fetches the right token endpoint:
            // "sip"        → /getSipToken → joins hr-sip-live (same room as MicroSIP)
            // "standalone" → /getToken    → joins fresh random room (original behaviour)
            await connectToRoom("Employee", sipMode ? "sip" : "standalone");
            setConnected(true);
            setStatus(sipMode ? "Connected (SIP mode)" : "Connected");
        } catch (err) {
            console.error("[Connect Error]", err);
            setStatus("Connection Failed");
            setErrorDetail(err.message || String(err));
            setMicState((prev) => ({ ...prev, error: err.message || "Failed to connect audio" }));
        }
    }

    async function disconnect() {
        await disconnectRoom();
        setConnected(false);
        setStatus("Disconnected");
        setAgentState("idle");
        setMicState({ enabled: false, isMuted: true, error: null });
    }

    async function handleToggleMic() {
        await toggleMicrophone();
    }

    function clearMessages() {
        setMessages([]);
    }

    return (
        <div className="dashboard">
            {errorDetail && (
                <div style={{
                    background: "#ff3333",
                    color: "#fff",
                    padding: "10px 18px",
                    fontFamily: "monospace",
                    fontSize: "13px",
                    wordBreak: "break-all",
                    position: "fixed",
                    bottom: 0,
                    left: 0,
                    right: 0,
                    zIndex: 9999,
                }}>
                    ❌ Error: {errorDetail}
                </div>
            )}
            <Header connected={connected} status={status} />

            <div className="dashboard-body">
                <div className="dashboard-main">
                    <Transcript messages={messages} onClear={clearMessages} />

                    {/* SIP Mode toggle — shown only when NOT connected */}
                    {!connected && (
                        <div style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "10px",
                            padding: "8px 16px",
                            marginBottom: "4px",
                            background: sipMode
                                ? "linear-gradient(90deg,#1a3a2a 0%,#0d2518 100%)"
                                : "rgba(255,255,255,0.04)",
                            borderRadius: "10px",
                            border: sipMode
                                ? "1px solid #2ecc71"
                                : "1px solid rgba(255,255,255,0.1)",
                            transition: "all 0.25s ease",
                        }}>
                            <span style={{
                                fontSize: "13px",
                                color: sipMode ? "#2ecc71" : "#888",
                                fontWeight: 600,
                                letterSpacing: "0.03em",
                                transition: "color 0.25s",
                            }}>
                                📞 SIP Mode
                            </span>
                            <button
                                id="sip-mode-toggle"
                                onClick={() => setSipMode((v) => !v)}
                                title={sipMode
                                    ? "SIP mode ON — browser will join hr-sip-live (same room as MicroSIP)"
                                    : "SIP mode OFF — browser will join its own private room"}
                                style={{
                                    position: "relative",
                                    width: "44px",
                                    height: "24px",
                                    borderRadius: "12px",
                                    border: "none",
                                    cursor: "pointer",
                                    background: sipMode
                                        ? "linear-gradient(90deg,#27ae60,#2ecc71)"
                                        : "#333",
                                    transition: "background 0.25s",
                                    padding: 0,
                                    flexShrink: 0,
                                }}
                            >
                                <span style={{
                                    position: "absolute",
                                    top: "3px",
                                    left: sipMode ? "23px" : "3px",
                                    width: "18px",
                                    height: "18px",
                                    borderRadius: "50%",
                                    background: "#fff",
                                    transition: "left 0.2s ease",
                                    display: "block",
                                }} />
                            </button>
                            <span style={{
                                fontSize: "11px",
                                color: sipMode ? "#2ecc71" : "#555",
                                transition: "color 0.25s",
                            }}>
                                {sipMode
                                    ? "ON — joins hr-sip-live (MicroSIP room)"
                                    : "OFF — private browser session"}
                            </span>
                        </div>
                    )}

                    <Controls
                        connected={connected}
                        onConnect={connect}
                        onDisconnect={disconnect}
                        micState={micState}
                        onToggleMic={handleToggleMic}
                    />
                </div>

                <div className="dashboard-side">
                    <StatusBar
                        connected={connected}
                        status={status}
                        room={roomName}
                        participants={participants}
                        duration={formatDuration(durationSec)}
                    />
                    <AgentVisualizer state={connected ? agentState : "disconnected"} />
                    <AgentInfoCard />
                    {/* Placeholder data: nothing in this file computes metrics/tools yet.
                        Wire real values in here once your backend exposes them. */}
                    <MetricsPanel />
                    <ToolCalls />
                    <ConversationFlow />
                </div>
            </div>

            <div className="metric-strip">
                <div className="metric">
                    <div className="metric-icon metric-icon--accent">👥</div>
                    <div>
                        <div className="metric-val">{connected ? participants : "--"}</div>
                        <div className="metric-label">Participants</div>
                    </div>
                </div>
                <div className="metric">
                    <div className="metric-icon metric-icon--accent">⏱️</div>
                    <div>
                        <div className="metric-val">{formatDuration(durationSec)}</div>
                        <div className="metric-label">Session duration</div>
                    </div>
                </div>
                <div className="metric">
                    <div className="metric-icon metric-icon--accent-2">⚡</div>
                    <div>
                        <div className="metric-val">{latencyMs !== null ? `${latencyMs} ms` : "--"}</div>
                        <div className="metric-label">Latency</div>
                    </div>
                </div>
                <div className="metric">
                    <div className="metric-icon metric-icon--accent">🛡️</div>
                    <div>
                        <div className="metric-val">{connected ? capitalize(connectionQuality) : "--"}</div>
                        <div className="metric-label">Connection quality</div>
                    </div>
                </div>
            </div>
        </div>
    );
}
