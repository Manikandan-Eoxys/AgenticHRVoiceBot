"""
config/__init__.py

Re-exports typed Config and module-level constants from config.py
so that both `from config import LIVEKIT_URL` and `import config` work seamlessly
whether Python resolves config as a package directory or module.
"""

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Optional

_cfg_path = Path(__file__).resolve().parent.parent / "config.py"
if _cfg_path.exists():
    _spec = importlib.util.spec_from_file_location("_config_module", str(_cfg_path))
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        for _attr in dir(_mod):
            if not _attr.startswith("__"):
                globals()[_attr] = getattr(_mod, _attr)

__all__ = [
    "Config",
    "BASE_DIR",
    "PROMPT_FILE",
    "SYSTEM_PROMPT",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "MYSQL_HOST",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DATABASE",
    "DATABASE_PATH",
    "LOG_LEVEL",
    "SIP_TRUNK_USERNAME",
    "SIP_TRUNK_PASSWORD",
    "SIP_ROOM_PREFIX",
    "WORKER_AGENT_NAME",
    "RECORDING_COORDINATOR_MODE",
    "AST_BRIDGE_HOST",
    "AST_BRIDGE_ADVERTISE_HOST",
    "AST_BRIDGE_PORT",
    "AST_SAMPLE_RATE",
    "AST_ROOM_PREFIX",
    "ARI_URL",
    "ARI_WS_URL",
    "ARI_USER",
    "ARI_PASSWORD",
    "ARI_APP_NAME",
]

if TYPE_CHECKING:
    class Config:
        MYSQL_HOST: str
        MYSQL_USER: str
        MYSQL_PASSWORD: str
        MYSQL_DATABASE: str
        DATABASE_PATH: str
        OLLAMA_BASE_URL: str
        OLLAMA_MODEL: str
        OPENAI_API_KEY: Optional[str]
        OPENAI_MODEL: str
        GROQ_API_KEY: Optional[str]
        GROQ_MODEL: str
        DEEPGRAM_API_KEY: Optional[str]
        ELEVENLABS_API_KEY: Optional[str]
        ELEVENLABS_VOICE_ID: Optional[str]
        LIVEKIT_URL: Optional[str]
        LIVEKIT_API_KEY: Optional[str]
        LIVEKIT_API_SECRET: Optional[str]
        LOG_LEVEL: str
        COVERAGE_THRESHOLD: float
        IFS_CLOUD_BASE_URL: Optional[str]
        IFS_CLOUD_CLIENT_ID: Optional[str]
        IFS_CLOUD_CLIENT_SECRET: Optional[str]
        IFS_CLOUD_TENANT: Optional[str]
        SQL_SERVER_CONN_STR: Optional[str]
        SYNC_INTERVAL_MINUTES: int
        SMTP_HOST: Optional[str]
        SMTP_PORT: int
        SMTP_USER: Optional[str]
        SMTP_PASS: Optional[str]
        HR_EMAIL: Optional[str]
        TEAMS_WEBHOOK_URL: Optional[str]
        SIP_TRUNK_USERNAME: str
        SIP_TRUNK_PASSWORD: str
        SIP_ROOM_PREFIX: str
        WORKER_AGENT_NAME: str
        RECORDING_COORDINATOR_MODE: str
        AST_BRIDGE_HOST: str
        AST_BRIDGE_ADVERTISE_HOST: str
        AST_BRIDGE_PORT: int
        AST_SAMPLE_RATE: int
        AST_ROOM_PREFIX: str
        ARI_URL: str
        ARI_WS_URL: str
        ARI_USER: str
        ARI_PASSWORD: str
        ARI_APP_NAME: str

    BASE_DIR: Path
    PROMPT_FILE: Path
    SYSTEM_PROMPT: str
    LIVEKIT_URL: Optional[str]
    LIVEKIT_API_KEY: Optional[str]
    LIVEKIT_API_SECRET: Optional[str]
    OLLAMA_BASE_URL: str
    OLLAMA_MODEL: str
    OPENAI_API_KEY: Optional[str]
    OPENAI_MODEL: str
    GROQ_API_KEY: Optional[str]
    GROQ_MODEL: str
    DEEPGRAM_API_KEY: Optional[str]
    ELEVENLABS_API_KEY: Optional[str]
    ELEVENLABS_VOICE_ID: Optional[str]
    MYSQL_HOST: str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DATABASE: str
    DATABASE_PATH: str
    LOG_LEVEL: str
    SIP_TRUNK_USERNAME: str
    SIP_TRUNK_PASSWORD: str
    SIP_ROOM_PREFIX: str
    WORKER_AGENT_NAME: str
    RECORDING_COORDINATOR_MODE: str
    AST_BRIDGE_HOST: str
    AST_BRIDGE_ADVERTISE_HOST: str
    AST_BRIDGE_PORT: int
    AST_SAMPLE_RATE: int
    AST_ROOM_PREFIX: str
    ARI_URL: str
    ARI_WS_URL: str
    ARI_USER: str
    ARI_PASSWORD: str
    ARI_APP_NAME: str
