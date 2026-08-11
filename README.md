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

Create

```
touch .env
```

Example

```env
###################################################
# LiveKit
###################################################

LIVEKIT_URL=wss://xxxxxxxx.livekit.cloud

LIVEKIT_API_KEY=xxxxxxxx

LIVEKIT_API_SECRET=xxxxxxxx

###################################################
# Ollama
###################################################

OLLAMA_BASE_URL=http://localhost:11434/v1

OLLAMA_MODEL=qwen2.5:1.5b

###################################################
# Deepgram
###################################################

DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxx

###################################################
# Database
###################################################

DATABASE_PATH=database/hr.db

###################################################
# Logging
###################################################

LOG_LEVEL=INFO
```

---

# Configure config.py

Should contain

```
OLLAMA_BASE_URL

OLLAMA_MODEL

DEEPGRAM_API_KEY

LIVEKIT_URL

LIVEKIT_API_KEY

LIVEKIT_API_SECRET

DATABASE_PATH
```

---

# Configure main.py

The agent uses

```
Deepgram STT

↓

Ollama LLM

↓

Deepgram TTS
```

LLM configuration

```python
LLM(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    api_key="ollama"
)
```

---

# Database

SQLite database

```
database/hr.db
```

Contains

```
Employees

Departments

Managers

Leaves

Policies
```

---

# Run Application

Activate environment

```
source .venv/bin/activate
```

Run

```
python main.py dev
```

Expected

```
starting worker

registered worker

received job request

Connected to LiveKit

Participant joined

Agent session started
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