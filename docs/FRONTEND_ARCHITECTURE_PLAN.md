# AI Agentic HR VoiceBot — Frontend Architecture & Implementation Plan

> **Audience**: Full-Stack / React Development Team (with zero prior AI voicebot knowledge).  
> **Objective**: Provide a complete architectural blueprint, conceptual primer, integration guide, and implementation roadmap to build a production-grade React frontend for the AI HR Voicebot.

---

## 1. Executive Summary & Architecture Overview

The **Agentic HR VoiceBot** is a real-time conversational voice assistant designed for enterprise HR operations (leave balance, leave application, attendance, payroll inquiries, company policies, and manager escalation).

Unlike traditional text chatbots (which use simple REST request/response), this is an **agentic voice system** that uses **WebRTC bidirectional audio streaming** and **LLM reasoning with automated tool execution**.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 FRONTEND (React SPA)                                   │
│                                                                                        │
│   ┌───────────────────────────────┐           ┌────────────────────────────────────┐   │
│   │   Employee Voice Portal       │           │   Admin / Supervisor Dashboard     │   │
│   │   - Mic Audio Streaming       │           │   - Real-time Fleet Monitoring     │   │
│   │   - Live Transcript Bubbles   │           │   - Tool Execution Inspector       │   │
│   │   - Audio Waveform Visualizer │           │   - Dynamic Agent Config Studio    │   │
│   │   - DTMF Keypad (DataChannel) │           │   - Call Recordings & DB Console   │   │
│   └──────────────┬────────────────┘           └─────────────────┬──────────────────┘   │
└──────────────────┼──────────────────────────────────────────────┼──────────────────────┘
                   │ WebRTC Audio + Data                          │ REST / SSE / WS
                   ▼                                              ▼
┌──────────────────────────────────────┐        ┌────────────────────────────────────────┐
│     LiveKit Server (WebRTC SFU)      │        │ FastAPI Backends                       │
│     - Room audio mixing              │        │ 1. Token/Monitor Server (Port 8000)    │
│     - Low latency (<100ms) transport │        │    - /getToken, /monitor/*             │
│     - WebRTC DataChannel             │        │ 2. Agent Builder Backend (Port 8571)   │
└──────────────────┬───────────────────┘        │    - /api/auth, /api/pages, /api/db    │
                   │ WebRTC Session             └────────────────────────────────────────┘
                   ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          PYTHON AGENT WORKER (agent.py)                                │
│                                                                                        │
│   ┌───────────────┐     ┌──────────────────────┐     ┌─────────────────────────────┐   │
│   │ Deepgram STT  │ ──► │ LLM (Ollama / Gemma) │ ──► │ Cartesia / Deepgram TTS     │   │
│   │ Speech → Text │     │ Decision Engine      │     │ Text → Synthetic Speech     │   │
│   └───────────────┘     └──────────┬───────────┘     └─────────────────────────────┘   │
│                                    │                                                   │
│                                    ▼ Function Tool Calls                               │
│                         ┌──────────────────────┐                                       │
│                         │  12 HR Goal Services │                                       │
│                         │  (Leave, Attendance, │                                       │
│                         │   Payroll, Policies) │                                       │
│                         └──────────┬───────────┘                                       │
└────────────────────────────────────┼───────────────────────────────────────────────────┘
                                     ▼
                   ┌───────────────────────────────────┐
                   │ Enterprise Backend Systems        │
                   │ - SQLite / MySQL DB               │
                   │ - IFS Cloud ERP (OData REST)      │
                   │ - SMTP Email Notifications        │
                   │ - S3 / Local Audio Storage        │
                   └───────────────────────────────────┘
```

---

## 2. Voicebot Primer for Frontend Developers

If you haven't worked with voicebots before, here are the essential concepts:

### A. How Voice Streaming Works (WebRTC vs HTTP)
- **Traditional Chatbots**: React sends `POST /api/chat`, waits 2-5 seconds, renders text.
- **Voicebot**: Continuous real-time WebRTC media stream.
  1. The user speaks into their microphone.
  2. The browser captures raw audio and transmits it via WebRTC to **LiveKit**.
  3. The **Python Agent Worker** (listening inside the same LiveKit room) feeds the audio stream to **Deepgram STT** (Speech-to-Text).
  4. Deepgram outputs interim text snippets, and emits a final transcription when the user pauses.
  5. The **LLM** (Ollama/Gemma) receives the text, decides if it needs data (e.g. checking vacation balance), calls a Python tool, and produces response tokens.
  6. The response tokens are streamed directly to **TTS** (Text-to-Speech: Cartesia/Deepgram), which generates synthetic audio frames.
  7. LiveKit streams that audio back into the browser over WebRTC, playing through the speaker with sub-second latency.

### B. What is LiveKit?
LiveKit is an open-source WebRTC media server (SFU). Your React app uses the official client library (`livekit-client`).
- It manages room connections, audio track subscription/publishing, and WebSockets.
- It provides a **DataChannel** (a low-latency data pipeline) to exchange real-time JSON packets (like DTMF keypresses, live status, or tool triggers) between the browser and the Python agent.

### C. What is DTMF?
**Dual-Tone Multi-Frequency** is telephone keypad signaling (digits `0-9`, `*`, `#`).
- In this HR bot, callers can either speak their 4-digit employee ID or dial it on a keypad.
- From the web browser, pressing a key on the UI dialpad sends a JSON packet `{ "digit": "7" }` across the WebRTC Data Channel to the agent.

---

## 3. Current Project Status: What is ALREADY DONE

The backend and agent core are fully developed and functioning. Here is what exists right now:

### 1. Python Agent Core (`hr_voice_agent/agent.py`)
- **Speech-to-Text (STT)**: Deepgram streaming STT (Nova-2 model) with interim transcript updates.
- **LLM Reasoning**: Supports local Ollama models (`qwen2.5:3b`, `qwen2.5:1.5b`) and OpenAI-compatible endpoints (`google/gemma-4-31b-it`).
- **Text-to-Speech (TTS)**: Cartesia Sonic / Deepgram TTS streaming synthesized audio back into WebRTC.
- **DTMF Keypad Engine**: In-band audio frequency detection (Goertzel algorithm) + WebRTC Data Channel packet consumer for employee ID input.
- **Audio Call Recorder**: Records room audio to local storage and S3 via LiveKit Egress.
- **Hangup & Ringback Audio**: Audio synthesized tones played into WebRTC for phone-like dialing and call termination.

### 2. Goal-Based HR Service Architecture (`hr_voice_agent/goals/`)
Twelve modular, registered enterprise HR services:
| Goal ID | Service Name | Capabilities |
| :--- | :--- | :--- |
| **G01** | `IdentityVerificationService` | Spoken or keypad 4-digit employee ID lookup, voice readback, session identity locking. |
| **G02** | `LeaveBalanceService` | Fetch annual, sick, casual, maternity/paternity balances. |
| **G03** | `LeaveApplicationService` | Two-step validation: availability check, conflict check, and leave booking. |
| **G04** | `LeaveStatusService` | Check status of submitted leave requests; send reminder to manager. |
| **G05** | `AttendanceService` | Clock-in/out timestamps, check attendance status, late marks. |
| **G06** | `WorkFromHomeService` | Check WFH eligibility and submit WFH requests. |
| **G07** | `PayrollService` | Inquire salary credit date, pay slip status, basic compensation structure. |
| **G08** | `HRPolicyService` | Query company policies, dress code, holidays, insurance, or raise an HR ticket. |
| **G09** | `CalendarService` | Company holiday list, working Saturdays check. |
| **G10** | `RemindersService` | Schedule personal reminders for HR tasks. |
| **G11** | `EmployeeBenefitsService` | Query medical coverage, wellness programs, and insurance schemes. |
| **G12** | `LeaveVerificationOutboundService` | Outbound automated verification calls to supervisors. |

### 3. Backend REST & WebSocket Services
The backend runs across two ports:
- **Port 8000** (`hr_voice_agent/token_server.py`):
  - `GET /getToken?name=EmployeeName`: Generates a secure LiveKit JWT token and dynamic room name with worker auto-dispatch metadata.
  - `GET /getSipToken?name=EmployeeName`: Generates a token for the shared `hr-sip-live` room (for monitoring MicroSIP/PSTN calls).
  - `GET /monitor/agents`: Full FSM snapshot of all active agent workers and calls.
  - `GET /monitor/global-state`: High-frequency polling endpoint for active calls count and uptime.
  - `GET /monitor/health`: Health/liveness probe.
- **Port 8571** (`agent_builder/backend/main.py`):
  - `POST /api/auth/login`, `GET /api/auth/me`: Builder authentication.
  - `GET /api/pages/config`, `PUT /api/pages/config`: Read/update `agent_config.json`.
  - `GET /api/pages/goals`: List all 12 goals and their tool definitions.
  - `GET /api/db/tables`, `GET /api/db/schema/{table}`, `POST /api/db/query`: Safe read-only SQL query runner and DB explorer.
  - `GET /api/developer/info`, `/metrics`, `/logs`, `/profile`: Server CPU/memory diagnostics and logging.
  - `GET /dashboard/overview`, `/calls`, `/logs/stream`: SSE log streaming.

### 4. Prototype Frontends (Proof of Concepts)
- `hr_voice_agent/frontend/`: Simple test caller with WebRTC mic, waveform visualizer, dialpad, and basic transcript feed.
- `agent_builder/frontend/`: React + Vite shell with `VoiceAssistant`, `DbManager`, and `DeveloperInfo` components.

---

## 4. What NEEDS TO BE DONE (Frontend Team Roadmap)

The existing prototype code is unpolished and split across separate folders. The full-stack team needs to build a **unified, cohesive, production-grade React Application** with two primary user interfaces:

```
                          ┌───────────────────────────┐
                          │   Agentic HR Voice App    │
                          └─────────────┬─────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
┌─────────────────────────────────┐           ┌─────────────────────────────────┐
│  PORTAL 1: Employee Voice Call  │           │   PORTAL 2: HR Admin & Monitor  │
│  (End-User / Caller View)       │           │   (Supervisor / Developer View) │
├─────────────────────────────────┤           ├─────────────────────────────────┤
│ • One-Click Voice Call Connect  │           │ • Live Agent Fleet Dashboard    │
│ • Real-time Audio Waveform      │           │ • Real-time Call Eavesdrop      │
│ • Live Streaming Transcript     │           │ • Tool Call Inspector           │
│ • Interactive DTMF Dialpad      │           │ • Agent Prompt & Voice Studio   │
│ • Live Action Cards (e.g. Leave)│           │ • DB & Employee Data Explorer   │
│ • Call Quality & Latency Badge  │           │ • Call Recordings Playback Hub  │
└─────────────────────────────────┘           └─────────────────────────────────┘
```

---

## 5. Portal 1: Employee Voice Caller (Technical Spec)

This is the interface employees use to talk with the HR bot from their web browser.

### Key Components to Build:
1. **Call Control Bar**:
   - `Connect Call` / `End Call` buttons with smooth state transitions (`Idle` -> `Connecting` -> `Connected` -> `Disconnecting`).
   - Call duration timer (`00:00`).
   - Mic Mute / Unmute toggle button with visual feedback.
   - Mode Toggle: `WebRTC Direct Call` vs. `SIP Room Monitor` (joins `hr-sip-live` to listen to SIP phone calls).
2. **Audio Waveform Visualizer**:
   - Use the browser's Native `AudioContext` and `AnalyserNode` connected to the LiveKit Audio Track.
   - Distinct visual state pulses:
     - 🟢 **Listening**: Pulsing cyan/green wave as the user speaks.
     - 🟡 **Thinking**: Smooth shimmer/glowing animation when the agent is querying tools or the LLM.
     - 🟣 **Speaking**: Dynamic multi-bar frequency visualizer matching the agent's synthetic voice output.
3. **Live Streaming Transcript Feed**:
   - Auto-scrolling chat-style transcript.
   - **Interim speech rendering**: Updates words in real-time as the user or agent speaks (using segment IDs), transitioning to final bubbles once speech ceases.
   - Bubble color-coding: User (right, blue/white) vs Agent (left, purple/dark-card).
4. **Interactive DTMF Dialpad**:
   - 12-key phone pad (`1-9`, `*`, `0`, `#`).
   - Keyboard listening (`0-9`, `*`, `#` keypresses trigger the pad).
   - Generates authentic dual-tone audio beep locally via Web Audio API on click.
   - Transmits packet `{ "digit": key }` across the WebRTC Data Channel to `agent.py`.
5. **Interactive HR Action Cards**:
   - When the agent verifies identity or submits a leave request, display a live summary card next to the transcript:
     - *Verified Employee*: Name, ID, Department, Manager.
     - *Leave Summary*: Type (Casual/Sick), Dates, Remaining Balance.

---

## 6. Portal 2: Admin, Monitor & Agent Builder (Technical Spec)

This is the control center for HR managers, supervisors, and developers.

### Key Components to Build:
1. **Agent Fleet & Telephony Monitor**:
   - Connects to `GET /monitor/agents` and `GET /monitor/global-state` (via polling or WebSocket `/ws/monitor`).
   - Shows active call count, total calls handled, uptime, and system status badge (`OPTIMAL`, `BUSY`, `DEGRADED`).
   - List of active rooms with participant identities and call durations.
2. **Live Tool Execution Inspector**:
   - Shows which tools the agent triggers in real-time during a call.
   - Displays tool name (e.g. `g03_leave_application.check_leave_availability`), input arguments, execution status (`running` -> `success`), and execution duration (ms).
3. **Agent Prompt & Voice Studio**:
   - Form interfaced with `GET/PUT /api/pages/config`.
   - Allows changing:
     - System prompt & personality instructions.
     - LLM Model selection (`qwen2.5:3b`, `llama3.2`, `gemma`).
     - Voice provider & Voice ID (Cartesia Sonic, Deepgram Aura).
     - Temperature & response token limits.
     - Goal toggle switches (enable/disable specific HR capabilities with one click).
4. **Database & Records Explorer**:
   - Interfaced with `/api/db/tables`, `/api/db/schema/{table}`, `/api/db/query`.
   - Table viewer for `employees`, `leave_balances`, `attendance_records`, `leave_requests`.
   - Read-only SQL query sandbox with execution time metrics and CSV export.
5. **Call Recordings & History**:
   - List of completed calls with audio playback widget.
   - Time-synced transcript viewing alongside audio playback.
6. **Developer Diagnostics & Live Logs**:
   - Interfaced with `GET /api/developer/metrics` and SSE `/dashboard/logs/stream`.
   - Real-time CPU, RAM, active threads, and streaming colorized console logs.

---

## 7. WebRTC & LiveKit Client Integration Guide

### Step 1: Install Dependencies
```bash
npm install livekit-client @tanstack/react-query lucide-react zustand
```

### Step 2: LiveKit Connection Flow (React Service)
Here is the exact code pattern to connect the browser to the voice agent:

```javascript
// src/services/voiceAgentService.js
import { Room, RoomEvent, Track } from "livekit-client";

let room = null;

export async function startVoiceSession({
  userName = "Employee",
  isSipMode = false,
  onTranscript,
  onAgentState,
  onDataPacket,
  onMicState,
}) {
  // 1. Fetch JWT token from Python Token Server
  const endpoint = isSipMode ? "getSipToken" : "getToken";
  const res = await fetch(`http://localhost:8000/${endpoint}?name=${encodeURIComponent(userName)}`);
  if (!res.ok) throw new Error(`Token fetch failed: ${res.statusText}`);
  const { token, ws_url } = await res.json();

  // 2. Instantiate LiveKit Room
  room = new Room({
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  // 3. Listen for Agent Audio Track and auto-play
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    if (track.kind === Track.Kind.Audio) {
      const audioElement = track.attach();
      audioElement.id = `remote-audio-${participant.identity}`;
      document.body.appendChild(audioElement);
    }
  });

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((el) => el.remove());
  });

  // 4. Live Speech Transcription (User + Agent)
  room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
    for (const seg of segments) {
      onTranscript({
        id: seg.id,
        role: participant?.isAgent ? "agent" : "user",
        text: seg.text,
        final: seg.final,
        timestamp: new Date().toLocaleTimeString(),
      });
    }
  });

  // 5. Agent State & Metadata updates
  room.on(RoomEvent.RoomMetadataChanged, (metadataStr) => {
    try {
      const meta = JSON.parse(metadataStr);
      if (meta.agent_state && onAgentState) {
        onAgentState(meta.agent_state); // "listening" | "thinking" | "speaking"
      }
    } catch (_) {}
  });

  // 6. Custom DataChannel Packets (Tool calls, active goals, system alerts)
  room.on(RoomEvent.DataReceived, (payload, participant) => {
    try {
      const decoded = new TextDecoder().decode(payload);
      const data = JSON.parse(decoded);
      if (onDataPacket) onDataPacket(data);
    } catch (_) {}
  });

  // 7. Connect to the room
  await room.connect(ws_url || "ws://localhost:7880", token);

  // 8. Publish local microphone
  try {
    await room.localParticipant.setMicrophoneEnabled(true);
    if (onMicState) onMicState({ enabled: true, isMuted: false });
  } catch (micErr) {
    if (onMicState) onMicState({ enabled: false, error: micErr.message });
  }

  return room;
}

export async function endVoiceSession() {
  if (room) {
    await room.localParticipant.setMicrophoneEnabled(false);
    await room.disconnect();
    room = null;
  }
}

export function sendDtmfKey(digit) {
  if (!room || !room.localParticipant) return;
  const payload = JSON.stringify({ digit: String(digit) });
  const encoder = new TextEncoder();
  room.localParticipant.publishData(encoder.encode(payload), { reliable: true });
}

export function toggleMute() {
  if (!room || !room.localParticipant) return false;
  const isEnabled = room.localParticipant.isMicrophoneEnabled;
  room.localParticipant.setMicrophoneEnabled(!isEnabled);
  return !isEnabled;
}
```

---

## 8. Data Channel Communication Protocol

The frontend and backend communicate asynchronously over the WebRTC Data Channel using JSON payloads:

### Inbound to Agent (Browser ➔ Python Agent)
| Action | JSON Payload | Description |
| :--- | :--- | :--- |
| **DTMF Digit** | `{"digit": "1"}` | Sent when user clicks or presses a key on the dialpad. |
| **Clear Buffer** | `{"digit": "*"}` | Clears the 4-digit ID input buffer. |
| **Confirm ID** | `{"digit": "#"}` | Submits the entered ID buffer. |

### Outbound from Agent (Python Agent ➔ Browser)
| Event | JSON Payload | Description |
| :--- | :--- | :--- |
| **Transcription** | `{"type": "transcript", "role": "agent", "text": "Hello, how can I help?", "final": true}` | Speech-to-text transcript turn. |
| **Tool Execution** | `{"type": "tool_call", "tool": "get_leave_balance", "status": "completed", "result": {"casual": 4, "sick": 7}}` | Fired when an agent tool executes. |
| **Goal Transition** | `{"type": "metadata", "activeGoal": "g03_leave_application"}` | Fired when the agent switches conversation goal. |
| **Call Transferred** | `{"type": "event", "event": "call_transferred", "target": "Coordinator"}` | Notification of agent call transfer. |

---

## 9. Backend Endpoints Quick Reference

| Port | Prefix / Path | Method | Description |
| :--- | :--- | :--- | :--- |
| **8000** | `/getToken?name={name}` | `GET` | Fetches LiveKit JWT token + room for WebRTC browser calls. |
| **8000** | `/getSipToken?name={name}`| `GET` | Fetches token for the shared `hr-sip-live` monitoring room. |
| **8000** | `/monitor/agents` | `GET` | Full snapshot of active agents, call states, and uptime. |
| **8000** | `/monitor/global-state` | `GET` | Lightweight polling endpoint (active call count, health). |
| **8000** | `/monitor/health` | `GET` | System health check (`200 OK` or `503`). |
| **8571** | `/api/auth/login` | `POST` | Builder Admin authentication. |
| **8571** | `/api/pages/config` | `GET/PUT`| Read or update agent configuration (`agent_config.json`). |
| **8571** | `/api/pages/goals` | `GET` | List all 12 registered HR goal services and descriptions. |
| **8571** | `/api/db/tables` | `GET` | List all database tables and record counts. |
| **8571** | `/api/db/schema/{table}`| `GET` | Inspect columns and primary keys of a table. |
| **8571** | `/api/db/query` | `POST` | Execute a read-only SQL query safely. |
| **8571** | `/api/developer/metrics`| `GET` | Real-time CPU, RAM, and thread metrics. |
| **8571** | `/dashboard/logs/stream`| `GET` | Server-Sent Events (SSE) streaming live application logs. |

---

## 10. Suggested React Project Structure

```
frontend/
├── public/
│   ├── favicon.ico
│   └── sounds/              # dtmf-tones audio files (optional fallback)
├── src/
│   ├── assets/              # Logos, brand assets
│   ├── components/
│   │   ├── caller/          # Portal 1: Employee Voice Caller
│   │   │   ├── CallControls.jsx
│   │   │   ├── AudioVisualizer.jsx
│   │   │   ├── LiveTranscript.jsx
│   │   │   ├── DtmfDialpad.jsx
│   │   │   └── HrActionCard.jsx
│   │   ├── dashboard/       # Portal 2: Admin & Supervisor Monitor
│   │   │   ├── FleetMonitor.jsx
│   │   │   ├── LiveCallEavesdrop.jsx
│   │   │   ├── ToolExecutionFeed.jsx
│   │   │   ├── AgentConfigStudio.jsx
│   │   │   ├── DatabaseExplorer.jsx
│   │   │   └── DeveloperDiagnostics.jsx
│   │   ├── common/          # Shared UI (Cards, Badges, Modals, Tabs)
│   │   │   ├── Header.jsx
│   │   │   ├── Sidebar.jsx
│   │   │   ├── Badge.jsx
│   │   │   └── Modal.jsx
│   ├── hooks/
│   │   ├── useLiveKitVoice.js    # Custom hook encapsulating LiveKit audio & events
│   │   ├── useAudioVisualizer.js # Web Audio API FFT analyser hook
│   │   └── useAgentMonitor.js    # Polling hook for /monitor/agents
│   ├── services/
│   │   ├── livekitService.js     # Low-level LiveKit client wrapper
│   │   ├── apiService.js         # Axios / Fetch wrapper for Port 8000 & 8571
│   │   └── dtmfAudio.js          # Web Audio synth for dialpad touch-tones
│   ├── store/
│   │   └── callStore.js          # Zustand store for call duration, logs, active state
│   ├── styles/
│   │   ├── globals.css           # Modern theme variables (dark mode, glassmorphism)
│   │   └── animations.css        # Wave pulse and ripple keyframes
│   ├── App.jsx                   # Main layout and tab / view routing
│   └── main.jsx
├── package.json
└── vite.config.js
```

---

## 11. Common Gotchas & Team Tips

1. **Microphone Permissions (HTTPS / Localhost)**:
   WebRTC audio capture requires a secure context. It works on `http://localhost`, but if testing across machines on the local network (e.g. `http://192.168.x.x`), browsers will block microphone access unless served over **HTTPS** or added to `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. **Audio Autoplay Policies**:
   Browsers often block audio playback if no user interaction occurred on the page. Always trigger `room.connect()` in response to a direct user click (e.g., clicking "Start Call").
3. **Data Channel Encoding**:
   All messages sent via `room.localParticipant.publishData()` must be `Uint8Array` byte buffers. Use `new TextEncoder().encode(JSON.stringify(payload))`.
4. **SIP Room vs Standalone Room**:
   - `GET /getToken` creates a fresh, unique room (e.g., `hr-room-3f8a9b1c`). Use this for direct browser-to-agent voice calls.
   - `GET /getSipToken` returns a token for `hr-sip-live`. Use this when you want the browser to monitor or eavesdrop on a phone call coming in from MicroSIP or telephone trunks.
5. **No Local Echo**:
   Never attach the local participant's microphone track to an `<audio>` tag in the browser, or the user will hear their own voice with an echo. Only attach remote audio tracks (`participant.isAgent === true`).
