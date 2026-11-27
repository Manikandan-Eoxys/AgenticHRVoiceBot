"""
config/__init__.py

Re-exports typed Config and module-level constants from config.settings
so that both `from config import OLLAMA_BASE_URL` and `import config` work cleanly.
"""

from typing import TYPE_CHECKING, Optional
from .settings import *  # noqa: F401, F403

# Explicit fallback module-level bindings for static type analyzers (Pyrefly/mypy)
OPENROUTER_API_KEY: Optional[str] = globals().get("OPENROUTER_API_KEY", None)
OPENROUTER_MODEL: str = globals().get("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
STT_API_KEY: Optional[str] = globals().get("STT_API_KEY", None)
TTS_API_KEY: Optional[str] = globals().get("TTS_API_KEY", None)

__all__ = [
    "Config",
    "BASE_DIR",
    "PROMPT_FILE",
    "SYSTEM_PROMPT",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "STT_API_KEY",
    "STT_MODEL",
    "STT_LANGUAGE",
    "TTS_API_KEY",
    "LLM_PROVIDER",
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
    "RAG_TIMEOUT",
    "RAG_FILLER_DELAY",
    "RAG_FILLER_MODE",
    "RAG_MAX_CONTEXT_CHARS",
    "RAG_ALLOWED_HOSTS",
    "RAG_ALLOW_INSECURE_DEV",
    "RAG_ALLOW_METADATA_CONFIG",
    "RAG_SECRET_KEY",
    "OUTBOUND_ENABLED",
    "OUTBOUND_CALLER_ID",
    "OUTBOUND_TRUNK_ENDPOINT",
    "OUTBOUND_DEFAULT_COUNTRY_CODE",
    "OUTBOUND_ALLOWED_PREFIXES",
    "OUTBOUND_MAX_CALLS",
    "OUTBOUND_RING_TIMEOUT",
    "OUTBOUND_DIAL_PLUS",
    "OUTBOUND_API_HOST",
    "OUTBOUND_API_PORT",
    "OUTBOUND_API_KEY",
    "COORDINATOR_PHONE_NUMBER",
    "COORDINATOR_DIAL_TIMEOUT_SECONDS",
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
        OPENROUTER_API_KEY: Optional[str]
        OPENROUTER_MODEL: str
        GROQ_API_KEY: Optional[str]
        GROQ_MODEL: str
        STT_API_KEY: Optional[str]
        STT_MODEL: str
        STT_LANGUAGE: str
        TTS_API_KEY: Optional[str]
        LLM_PROVIDER: str
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
        RAG_TIMEOUT: float
        RAG_FILLER_DELAY: float
        RAG_FILLER_MODE: str
        RAG_MAX_CONTEXT_CHARS: int
        RAG_ALLOWED_HOSTS: str
        RAG_ALLOW_INSECURE_DEV: str
        RAG_ALLOW_METADATA_CONFIG: str
        RAG_SECRET_KEY: Optional[str]

    BASE_DIR: Path
    PROMPT_FILE: Path
    SYSTEM_PROMPT: str
    OLLAMA_BASE_URL: str
    OLLAMA_MODEL: str
    OPENAI_API_KEY: Optional[str]
    OPENAI_MODEL: str
    OPENROUTER_API_KEY: Optional[str]
    OPENROUTER_MODEL: str
    GROQ_API_KEY: Optional[str]
    GROQ_MODEL: str
    STT_API_KEY: Optional[str]
    STT_MODEL: str
    STT_LANGUAGE: str
    TTS_API_KEY: Optional[str]
    LLM_PROVIDER: str
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
    RAG_TIMEOUT: float
    RAG_FILLER_DELAY: float
    RAG_FILLER_MODE: str
    RAG_MAX_CONTEXT_CHARS: int
    RAG_ALLOWED_HOSTS: str
    RAG_ALLOW_INSECURE_DEV: str
    RAG_ALLOW_METADATA_CONFIG: str
    RAG_SECRET_KEY: Optional[str]
