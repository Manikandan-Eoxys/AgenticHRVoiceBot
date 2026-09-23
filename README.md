# HR Voice Agent

A real-time AI-powered HR Voice Assistant built using:

- LiveKit Agents v1.6.5
- Ollama (Local LLM)
- Deepgram Speech-to-Text (STT)
- Deepgram Text-to-Speech (TTS)
- SQLite HR Database
- Python 3.10+

The assistant joins a LiveKit room, listens to user speech, converts speech to text, queries the HR database when needed, generates responses using a local Ollama model, and speaks the response back to the user.

---

# Architecture

```
                 Browser / LiveKit UI
                         │
                         │
                  User Microphone
                         │
                         ▼
                 LiveKit Cloud Room
                         │
                         ▼
              LiveKit Python Agent
                         │
     ┌───────────────────┼───────────────────┐
     │                   │                   │
     ▼                   ▼                   ▼
 Deepgram STT      Ollama LLM         Deepgram TTS
 Speech → Text    Text → Response    Text → Speech
                         │
                         ▼
                  SQLite HR Database
```

---

# Software Requirements

Ubuntu 22.04 or later

Python

```
Python 3.10+
```

Git

```
sudo apt install git
```

SQLite

```
sudo apt install sqlite3
```

curl

```
sudo apt install curl
```

---

# Create Python Virtual Environment

```
python3 -m venv .venv
```

Activate

```
source .venv/bin/activate
```

Upgrade pip

```
pip install --upgrade pip
```

---

# Clone Repository

```
git clone <repository-url>

cd AgenticHRVoiceBot
```

---

# Install Python Dependencies

```
pip install -r requirements.txt
```

or

```
pip install \
livekit-agents==1.6.5 \
livekit==1.1.13 \
livekit-api \
livekit-plugins-openai \
livekit-plugins-deepgram \
python-dotenv \
aiosqlite
```

Verify

```
pip list | grep livekit
```

Expected

```
livekit
livekit-agents
livekit-api
livekit-plugins-openai
livekit-plugins-deepgram
```

---

# Install Ollama

Install

```
curl -fsSL https://ollama.com/install.sh | sh
```

Verify

```
ollama --version
```

Example

```
ollama version is 0.32.1
```

---

# Start Ollama

```
systemctl status ollama
```

Expected

```
Active: active (running)
```

If not running

```
sudo systemctl start ollama
```

Enable on boot

```
sudo systemctl enable ollama
```

---

# Download Local LLM

Recommended model

```
ollama pull qwen2.5:1.5b
```

Alternative

```
ollama pull llama3.2:3b
```

Verify installed models

```
ollama list
```

Expected

```
qwen2.5:1.5b
llama3.2:3b
```

---

# Verify Ollama API

```
curl http://localhost:11434/api/tags
```

Expected

```
{
   "models":[...]
}
```

Verify OpenAI compatible endpoint

```
curl http://localhost:11434/v1/models
```

Expected

```
{
    "data":[
        {
            "id":"qwen2.5:1.5b"
        }
    ]
}
```

---

# Test Ollama

```
curl http://localhost:11434/v1/chat/completions \
-H "Content-Type: application/json" \
-d '{
"model":"qwen2.5:1.5b",
"messages":[
{
"role":"user",
"content":"Hello"
}
]
}'
```

Expected

```
Hello!
```

---

# LiveKit Cloud Setup

Create account

https://cloud.livekit.io

Create Project

Example

```
HR Voice Agent
```

Copy

```
LIVEKIT_URL

LIVEKIT_API_KEY

LIVEKIT_API_SECRET
```

---

# Deepgram Setup

Create account

https://console.deepgram.com

Generate API Key

Copy

```
DEEPGRAM_API_KEY
```

---

# Project Structure

```
AgenticHRVoiceBot

│
├── agent.py
├── config.py
├── database/
│      └── hr.db
├── prompts/
│      └── system_prompt.txt
├── tools.py
├── main.py
├── requirements.txt
├── .env
└── README.md
```

---

# Configure .env

Copy the provided template:

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```bash
nano .env  # or code .env
```

Key environment settings:
- **LiveKit**: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- **STT**: `DEEPGRAM_API_KEY` (model: `nova-3`)
- **TTS**: `TTS_PROVIDER=cartesia` (or `deepgram`, `elevenlabs`)
- **LLM**: `OLLAMA_BASE_URL=http://localhost:11434/v1`, `OLLAMA_MODEL=qwen2.5:3b`
- **Database**: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
- **Email Notifications**: `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `HR_EMAIL`
- **Telephony Escalation**: `COORDINATOR_PHONE_NUMBER`, `LIVEKIT_OUTBOUND_TRUNK_ID`

---

# Database Setup

The agent connects to a MySQL database for employees, leave balances, policies, acknowledgements, grievances, and tickets (with SQLite fallback).

### MySQL Schema & Seed Data Import
```bash
# 1. Create database and user (if not already created):
mysql -u root -p -e "
CREATE DATABASE IF NOT EXISTS hr_voicebot;
CREATE USER IF NOT EXISTS 'hrbot'@'localhost' IDENTIFIED BY 'Eoxys@110';
GRANT ALL PRIVILEGES ON hr_voicebot.* TO 'hrbot'@'localhost';
FLUSH PRIVILEGES;
"

# 2. Import schema, seed employees, and HR policies:
mysql -u hrbot -p hr_voicebot < database/hr-voicebot-schema-mysql.sql
```

Verify tables:
```bash
python check_db.py
```

---

# Run Application

### Development Mode (Interactive Terminal)
Activate your virtual environment and run the agent in dev mode:
```bash
source .venv/bin/activate
python main.py dev
```

Expected output:
```
[hr_tools startup check] Connected to localhost/hr_voicebot as hrbot — 15 employee(s) ready.
🚀 [EmailApproval] Daemon thread started (polling every 15s).
starting worker
registered worker
received job request
Agent session started
```

---

# 24/7 Production Background Service (Systemd)

For production deployment on Linux / Ubuntu, run the agent as a resilient 24/7 systemd service that automatically restarts on crash or reboot and logs directly to `journalctl`.

### Option 1: Automated Turnkey Setup with `setup_service.sh` (Recommended)

The included `setup_service.sh` script automatically detects your active cloned directory, your system username, and virtualenv python path:

```bash
# 1. Install, daemon-reload, and enable service
sudo ./setup_service.sh install

# 2. Start the service
sudo ./setup_service.sh start

# 3. Check live status
./setup_service.sh status

# 4. Stream real-time logs
./setup_service.sh logs
```

**Service management shortcuts:**
| Command | Description |
|---|---|
| `sudo ./setup_service.sh start` | Starts the voice bot background service |
| `sudo ./setup_service.sh stop` | Gracefully stops the service |
| `sudo ./setup_service.sh restart` | Restarts the service |
| `./setup_service.sh status` | Displays `systemctl status hr-voicebot.service` |
| `./setup_service.sh logs` | Tails live service logs (`journalctl -u hr-voicebot.service -f`) |
| `sudo ./setup_service.sh uninstall` | Stops, disables, and deletes the service file |

### Option 2: Manual Systemd Setup (for any Git Clone user)

If you prefer to configure systemd manually:

1. **Edit [hr-voicebot.service](file:///home/eoxys/Documents/AgenticHRVoiceBot/hr-voicebot.service):**
   Update `User`, `WorkingDirectory`, and `ExecStart` to match your local installation:
   ```ini
   [Unit]
   Description=Agentic HR Voice Bot Service (24/7)
   After=network.target mysql.service

   [Service]
   Type=simple
   User=<YOUR_UBUNTU_USERNAME>
   WorkingDirectory=/path/to/AgenticHRVoiceBot
   ExecStart=/path/to/AgenticHRVoiceBot/.venv/bin/python main.py start
   Restart=always
   RestartSec=5s
   Environment=PYTHONUNBUFFERED=1
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```

2. **Copy the service unit file to systemd:**
   ```bash
   sudo cp hr-voicebot.service /etc/systemd/system/hr-voicebot.service
   ```

3. **Reload systemd daemon:**
   ```bash
   sudo systemctl daemon-reload
   ```

4. **Enable auto-start on boot & start the service:**
   ```bash
   sudo systemctl enable hr-voicebot.service
   sudo systemctl start hr-voicebot.service
   ```

5. **Verify status & view logs:**
   ```bash
   systemctl status hr-voicebot.service
   journalctl -u hr-voicebot.service -f
   ```

---

### Alternative: Background Nohup Execution

If you do not have sudo privileges to manage systemd, you can also run in background mode using:
```bash
./run_production.sh
# Monitor logs:
tail -f voicebot.log
```


---

# Open LiveKit Playground

Open

```
https://agents-playground.livekit.io/
```

Fill

```
LiveKit URL

Token

Room Name
```

Join room

Start speaking

Example

```
Hello

Who is the manager of John?

How many leave days do I have?

What is the work from home policy?
```

---

# Verify Ollama is Running

```
systemctl status ollama
```

or

```
curl http://localhost:11434/api/tags
```

---

# Useful Commands

Activate environment

```
source .venv/bin/activate
```

Deactivate

```
deactivate
```

List installed models

```
ollama list
```

Download new model

```
ollama pull qwen2.5:7b
```

Remove model

```
ollama rm qwen2.5:1.5b
```

Restart Ollama

```
sudo systemctl restart ollama
```

Check logs

```
journalctl -u ollama -f
```

---

# Troubleshooting

## Ollama not running

```
sudo systemctl start ollama
```

---

## Model not found

```
ollama pull qwen2.5:1.5b
```

---

## LiveKit worker not receiving jobs

Verify

```
LIVEKIT_URL

LIVEKIT_API_KEY

LIVEKIT_API_SECRET
```

---

## No speech recognition

Verify

```
DEEPGRAM_API_KEY
```

---

## No voice response

Verify

```
Ollama running

Deepgram API Key

Participant microphone enabled

Room token valid
```

---

# Technology Stack

| Component | Technology |
|------------|------------|
| Voice Transport | LiveKit |
| Speech-to-Text | Deepgram Nova-3 |
| Large Language Model | Ollama Qwen2.5 3B |
| Text-to-Speech | Deepgram Aura |
| Database | SQLite |
| Language | Python 3.10 |
| Agent Framework | LiveKit Agents v1.6.5 |

---

---

# Call Rejection & Number Blocklist

You can reject incoming (SIP / WebRTC) or outgoing calls from specific phone numbers or caller IDs until they are removed.

### 1. Using the CLI Script (`manage_blocked_numbers.py`)
```bash
# List all blocked numbers
python manage_blocked_numbers.py list

# Block one or more numbers / SIP IDs
python manage_blocked_numbers.py add +919876543210 101

# Check if a number is blocked
python manage_blocked_numbers.py check +919876543210

# Remove / unblock a number
python manage_blocked_numbers.py remove +919876543210

# Clear all blocked numbers
python manage_blocked_numbers.py clear
```

### 2. In Python Code (`security/call_blocklist.py`)
Add numbers directly to `BLOCKED_NUMBERS`:
```python
BLOCKED_NUMBERS: set[str] = {
    "+919876543210",
    "101",
}
```

### 3. In JSON Storage (`data/blocked_numbers.json`)
```json
[
  "+919876543210",
  "101"
]
```

### 4. Via Environment Variable (`.env`)
```bash
BLOCKED_PHONE_NUMBERS=+919876543210,+1234567890,101
```

When a blocked number calls in via SIP or WebRTC, LiveKit immediately rejects the job request / hangs up the room and logs:
`🚫 [CALL REJECTED] Rejecting call in room ... Blocked number/caller: ...`

---

# Future Improvements

- Function calling with Ollama
- Conversation memory
- Employee authentication
- Leave management
- Attendance management
- Payroll queries
- Email integration
- Calendar integration
- RAG using company policies
- Multi-language support
- Streaming LLM responses
- Docker deployment
- Kubernetes deployment
- Monitoring and metrics