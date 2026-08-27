"""
config.py
 
Loads all application configuration from the .env file.
All integration keys (IFS Cloud, SQL Server, SMTP, Teams) are defined here
so getattr() calls in service layers resolve correctly.
"""
 
import os
from pathlib import Path
from dotenv import load_dotenv
 
load_dotenv()
 
 
BASE_DIR = Path(__file__).resolve().parent
 
 
class Config:
 
    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_USER     = os.getenv("MYSQL_USER", "hrbot")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "Eoxys@110")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "hr_voicebot")
    DATABASE_PATH  = os.getenv("DATABASE_PATH", "database/hr.db")
 
    # ------------------------------------------------------------------
    # LLM / Voice
    # ------------------------------------------------------------------
    OLLAMA_BASE_URL = os.getenv(
        "OLLAMA_BASE_URL",
        "http://localhost:11434/v1"
    )
 
    OLLAMA_MODEL = os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5:3b"
    )
 
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)
    OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
 
    GROQ_API_KEY   = os.getenv("GROQ_API_KEY", None)
    GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
 
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
 
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
 
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
 
    # ------------------------------------------------------------------
    # LiveKit
    # ------------------------------------------------------------------
    LIVEKIT_URL = os.getenv("LIVEKIT_URL")
 
    LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
 
    LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
 
    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
 
    # ------------------------------------------------------------------
    # Attendance / Coverage Threshold
    # Business requirement: minimum operational coverage = 8%
    # Override via .env:  COVERAGE_THRESHOLD=0.08
    # ------------------------------------------------------------------
    COVERAGE_THRESHOLD = float(os.getenv("COVERAGE_THRESHOLD", "0.08"))
 
    # ------------------------------------------------------------------
    # IFS Cloud Integration
    # Set these in .env when real credentials are available.
    # Without them the client operates in STUB MODE (safe for dev/test).
    # ------------------------------------------------------------------
    IFS_CLOUD_BASE_URL     = os.getenv("IFS_CLOUD_BASE_URL",     None)
    IFS_CLOUD_CLIENT_ID    = os.getenv("IFS_CLOUD_CLIENT_ID",    None)
    IFS_CLOUD_CLIENT_SECRET = os.getenv("IFS_CLOUD_CLIENT_SECRET", None)
    IFS_CLOUD_TENANT       = os.getenv("IFS_CLOUD_TENANT",       None)
 
    # ------------------------------------------------------------------
    # Microsoft SQL Server Integration
    # Example conn strings documented in integrations/sql_server_client.py
    # Without this, the client operates in STUB MODE.
    # ------------------------------------------------------------------
    SQL_SERVER_CONN_STR = os.getenv("SQL_SERVER_CONN_STR", None)
 
    # ------------------------------------------------------------------
    # Sync Scheduler
    # How often (minutes) the background job syncs pending records
    # to IFS Cloud and SQL Server.
    # ------------------------------------------------------------------
    SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
 
    # ------------------------------------------------------------------
    # Notification Channels
    # Leave blank to run in demo/log-only mode.
    # SMTP: set SMTP_HOST + SMTP_USER + SMTP_PASS to send real emails.
    # Teams: set TEAMS_WEBHOOK_URL to post to a Teams channel.
    # ------------------------------------------------------------------
    SMTP_HOST = os.getenv("SMTP_HOST", None)
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", None)
    SMTP_PASS = os.getenv("SMTP_PASS", None)
 
    # HR inbox — receives every leave request submitted via the voice bot
    HR_EMAIL  = os.getenv("HR_EMAIL", None)
 
    TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", None)
 
    # ------------------------------------------------------------------
    # Plivo / SIP Trunk
    # Must match the credentials you entered when creating the
    # LiveKit Cloud SIP URI (Authentication needed toggle).
    # ------------------------------------------------------------------
    SIP_TRUNK_USERNAME = os.getenv("SIP_TRUNK_USERNAME", "AgenticHRVoicebot")
    SIP_TRUNK_PASSWORD = os.getenv("SIP_TRUNK_PASSWORD", "Agent@110")
    SIP_ROOM_PREFIX    = os.getenv("SIP_ROOM_PREFIX",    "plivo-hr-call")
 
    # ------------------------------------------------------------------
    # Worker / Agent dispatch identity
    # Each machine running this worker should set its own unique value
    # in .env so LiveKit Cloud can route jobs to a specific machine
    # instead of load-balancing across every running worker.
    # ------------------------------------------------------------------
    WORKER_AGENT_NAME = os.getenv("WORKER_AGENT_NAME", "hr-voice-agent")

    # ------------------------------------------------------------------
    # Call recording behavior during coordinator transfer
    # Options: "record_all" (default, Approach 2) or "cutoff" (Approach 1)
    # ------------------------------------------------------------------
    RECORDING_COORDINATOR_MODE = os.getenv("RECORDING_COORDINATOR_MODE", "record_all")
 
 
# ---------------------------------------------------------
# Module-level aliases (imported directly by main.py etc.)
# ---------------------------------------------------------
PROMPT_FILE = BASE_DIR / "prompts" / "system_prompt.txt"
 
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()
 
LIVEKIT_URL        = Config.LIVEKIT_URL
LIVEKIT_API_KEY    = Config.LIVEKIT_API_KEY
LIVEKIT_API_SECRET = Config.LIVEKIT_API_SECRET
 
OLLAMA_BASE_URL = Config.OLLAMA_BASE_URL
OLLAMA_MODEL    = Config.OLLAMA_MODEL
 
OPENAI_API_KEY = Config.OPENAI_API_KEY
OPENAI_MODEL   = Config.OPENAI_MODEL
 
GROQ_API_KEY = Config.GROQ_API_KEY
GROQ_MODEL   = Config.GROQ_MODEL
 
DEEPGRAM_API_KEY = Config.DEEPGRAM_API_KEY
 
ELEVENLABS_API_KEY  = Config.ELEVENLABS_API_KEY
ELEVENLABS_VOICE_ID = Config.ELEVENLABS_VOICE_ID
 
MYSQL_HOST     = Config.MYSQL_HOST
MYSQL_USER     = Config.MYSQL_USER
MYSQL_PASSWORD = Config.MYSQL_PASSWORD
MYSQL_DATABASE = Config.MYSQL_DATABASE
DATABASE_PATH  = Config.DATABASE_PATH
LOG_LEVEL      = Config.LOG_LEVEL
 
# SIP / Plivo
SIP_TRUNK_USERNAME = Config.SIP_TRUNK_USERNAME
SIP_TRUNK_PASSWORD = Config.SIP_TRUNK_PASSWORD
SIP_ROOM_PREFIX    = Config.SIP_ROOM_PREFIX
 
# Worker dispatch identity
WORKER_AGENT_NAME = Config.WORKER_AGENT_NAME

# Recording coordinator mode
RECORDING_COORDINATOR_MODE = Config.RECORDING_COORDINATOR_MODE