import {
    Room,
    RoomEvent,
    Track,
} from "livekit-client";
let room = null;
// Callbacks set by VoiceAssistant
let _onMessage = null;
let _onAgentState = null;
let _onMicState = null;

export function setCallbacks({ onMessage, onAgentState, onMicState }) {
    _onMessage = onMessage;
    _onAgentState = onAgentState;
    _onMicState = onMicState;
}

export async function connectToRoom(userName, mode = "standalone") {
    // 1. Fetch token from backend
    //    - "standalone" mode: fresh random room (existing behaviour)
    //    - "sip" mode:       fixed hr-sip-live room so browser joins the
    //                        same room MicroSIP calls land in
    let response;
    const tokenHost = typeof window !== "undefined" ? window.location.hostname : "localhost";
    const endpoint = mode === "sip" ? "getSipToken" : "getToken";
    try {
        response = await fetch(
            `http://${tokenHost}:8000/${endpoint}?name=${encodeURIComponent(userName)}`
        );
    } catch (fetchErr) {
        throw new Error(`Cannot reach token server at ${tokenHost}:8000. Is it running? (${fetchErr.message})`);
    }
    if (!response.ok) {
        throw new Error(`Token server returned status ${response.status}`);
    }
    const data = await response.json();

    // 2. Create room
    room = new Room({
        adaptiveStream: true,
        dynacast: true,
        reconnectPolicy: {
            nextRetryDelayInMs: (context) => {
                if (context.retryCount >= 3) return null; // stop after 3
                return 2000;
            },
        },
        webSocketTimeout: 20000,    // 20s timeout for signal connection
    });
    console.log("[LiveKit] Room object created");

    // ----------------------------------------------------------------
    // 3. Subscribe to agent audio track so we can HEAR the agent speak
    // ----------------------------------------------------------------
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind === Track.Kind.Audio) {
            // Attach agent audio to a hidden <audio> element so the browser plays it
            const audioEl = track.attach();
            audioEl.style.display = "none";
            document.body.appendChild(audioEl);
        }
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach();
    });

    // ----------------------------------------------------------------
    // 4. Listen to transcription events (agent + user speech-to-text)
    //    Every segment is forwarded now — interim (final === false) AND
    //    final — so the UI can stream text live instead of waiting for
    //    the whole phrase to finish. VoiceAssistant.jsx uses seg.id to
    //    tell "update this bubble" apart from "start a new bubble".
    // ----------------------------------------------------------------
    room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        if (!_onMessage) return;
        for (const seg of segments) {
            const role = participant?.isAgent ? "agent" : "user";
            _onMessage({
                id: seg.id,
                role,
                text: seg.text,
                final: seg.final,
            });
        }
    });

    // ----------------------------------------------------------------
    // 5. Track agent state (listening / thinking / speaking)
    // ----------------------------------------------------------------
    room.on(RoomEvent.RoomMetadataChanged, (metadata) => {
        try {
            const parsed = JSON.parse(metadata);
            if (_onAgentState && parsed?.agent_state) {
                _onAgentState(parsed.agent_state);
            }
        } catch (_) {}
    });

    // ----------------------------------------------------------------
    // Local track mute/unmute events
    // ----------------------------------------------------------------
    room.on(RoomEvent.LocalTrackPublished, (publication) => {
        if (publication.kind === Track.Kind.Audio && _onMicState) {
            _onMicState({ enabled: true, isMuted: publication.isMuted });
        }
    });

    room.on(RoomEvent.TrackMuted, (publication, participant) => {
        if (participant === room?.localParticipant && publication.kind === Track.Kind.Audio) {
            if (_onMicState) _onMicState({ enabled: true, isMuted: true });
        }
    });

    room.on(RoomEvent.TrackUnmuted, (publication, participant) => {
        if (participant === room?.localParticipant && publication.kind === Track.Kind.Audio) {
            if (_onMicState) _onMicState({ enabled: true, isMuted: false });
        }
    });

    // 6. Connect to LiveKit cloud — this is the actual connection step.
    //    If this throws, it's a real connection failure (bad token, network, etc.)
    const livekitHost = typeof window !== "undefined" ? window.location.hostname : "localhost";
    const livekitUrl = `ws://${livekitHost}:7880`;
    console.log("[LiveKit] Connecting to room at", livekitUrl);
    try {
        await room.connect(
            livekitUrl,
            data.token
        );
    } catch (connErr) {
        console.error("[LiveKit] room.connect() FAILED:", connErr);
        throw new Error(`LiveKit room connection failed: ${connErr.message || connErr}`);
    }
    console.log("[LiveKit] Room connected successfully. Room name:", room.name);

    // 7. Enable local microphone so the agent can hear us.
    //    NOTE: We do NOT throw here — a mic permission error should NOT cause
    //    "Connection Failed". The room is already connected. We report the mic
    //    error via the callback so the UI can show a warning without disconnecting.
    try {
        await room.localParticipant.setMicrophoneEnabled(true, {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        });
        if (_onMicState) _onMicState({ enabled: true, isMuted: false, error: null });
    } catch (err) {
        console.warn("[LiveKit] Microphone not available — room still connected:", err);
        // Report mic issue but DO NOT throw — connection stays alive
        if (_onMicState) _onMicState({
            enabled: false,
            isMuted: true,
            error: err.name === "NotAllowedError"
                ? "Microphone permission denied — click the 🔒 lock icon in your browser address bar to allow it."
                : (err.message || "Microphone unavailable"),
        });
    }

    return room;
}

export function isMicrophoneEnabled() {
    return room?.localParticipant?.isMicrophoneEnabled ?? false;
}

export async function toggleMicrophone() {
    if (!room || !room.localParticipant) return false;
    const currentStatus = room.localParticipant.isMicrophoneEnabled;
    const targetStatus = !currentStatus;
    try {
        await room.localParticipant.setMicrophoneEnabled(targetStatus);
        if (_onMicState) {
            _onMicState({
                enabled: targetStatus,
                isMuted: !targetStatus,
                error: null,
            });
        }
        return targetStatus;
    } catch (err) {
        console.error("[LiveKit] Failed to toggle microphone:", err);
        if (_onMicState) {
            _onMicState({
                enabled: currentStatus,
                isMuted: !currentStatus,
                error: err.message,
            });
        }
        return currentStatus;
    }
}

export function getRoom() {
    return room;
}

export async function disconnectRoom() {
    if (room) {
        await room.disconnect();
        room = null;
        if (_onMicState) _onMicState({ enabled: false, isMuted: true, error: null });
    }
}

