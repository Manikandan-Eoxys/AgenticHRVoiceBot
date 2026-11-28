"""
config/__init__.py

Re-exports typed Config and module-level constants from config.py
so that both `from config import OLLAMA_BASE_URL` and `import config` work seamlessly
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

# Explicit fallback module-level bindings for static type analyzers (Pyrefly/mypy)
OPENROUTER_API_KEY: Optional[str] = globals().get("OPENROUTER_API_KEY", None)
OPENROUTER_MODEL: str = globals().get("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
MULTI_STT_API_KEY: Optional[str] = globals().get("MULTI_STT_API_KEY", None)
MULTI_STT_MODEL: str = globals().get("MULTI_STT_MODEL", "nova-3")
MULTI_STT_LANGUAGE: str = globals().get("MULTI_STT_LANGUAGE", "ta")
STT_PROVIDER: str = globals().get("STT_PROVIDER", "deepgram")
MULTI_TTS_API_KEY: Optional[str] = globals().get("MULTI_TTS_API_KEY", None)
MULTI_TTS_MODEL: str = globals().get("MULTI_TTS_MODEL", "bulbul:v4-flash")
MULTI_TTS_SPEAKER: str = globals().get("MULTI_TTS_SPEAKER", "ishita_en_customer")
MULTI_TTS_LANGUAGE_CODE: str = globals().get("MULTI_TTS_LANGUAGE_CODE", "ta-IN")
MULTI_TTS_PACE: float = globals().get("MULTI_TTS_PACE", 1.0)
TRANSLATOR_API_KEY: Optional[str] = globals().get("TRANSLATOR_API_KEY", None)
TRANSLATOR_MODEL: str = globals().get("TRANSLATOR_MODEL", "mayura:v1")
TRANSLATION_MODE: str = globals().get("TRANSLATION_MODE", "modern-colloquial")
TRANSLATION_NUMERALS_FORMAT: str = globals().get("TRANSLATION_NUMERALS_FORMAT", "international")
TTS_PROVIDER: str = globals().get("TTS_PROVIDER", "deepgram")
SARVAM_API_KEY: Optional[str] = globals().get("SARVAM_API_KEY", None)
SARVAM_SPEAKER: str = globals().get("SARVAM_SPEAKER", "vijay_ta_narration")
SARVAM_MODEL: str = globals().get("SARVAM_MODEL", "bulbul:v4-flash")
SARVAM_LANGUAGE_CODE: str = globals().get("SARVAM_LANGUAGE_CODE", "ta-IN")
TRANSLATION_ENABLED: bool = globals().get("TRANSLATION_ENABLED", True)
PRIMARY_LANGUAGE: str = globals().get("PRIMARY_LANGUAGE", "kn")
DEEPGRAM_MODEL: str = globals().get("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_LANGUAGE: str = globals().get("DEEPGRAM_LANGUAGE", "en")

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
    "MULTI_STT_API_KEY",
    "MULTI_STT_MODEL",
    "MULTI_STT_LANGUAGE",
    "MULTI_TTS_API_KEY",
    "MULTI_TTS_MODEL",
    "MULTI_TTS_SPEAKER",
    "MULTI_TTS_LANGUAGE_CODE",
    "MULTI_TTS_PACE",
    "TRANSLATOR_API_KEY",
    "TRANSLATOR_MODEL",
    "TRANSLATION_MODE",
    "TRANSLATION_NUMERALS_FORMAT",
    "TRANSLATION_ENABLED",
    "PRIMARY_LANGUAGE",
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_MODEL",
    "DEEPGRAM_LANGUAGE",
    "TTS_PROVIDER",
    "SARVAM_API_KEY",
    "SARVAM_SPEAKER",
    "SARVAM_MODEL",
    "SARVAM_LANGUAGE_CODE",
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
    "RAG_TIMEOUT",
    "RAG_FILLER_DELAY",
    "RAG_FILLER_MODE",
    "RAG_MAX_CONTEXT_CHARS",
    "RAG_ALLOWED_HOSTS",
    "RAG_ALLOW_INSECURE_DEV",
    "RAG_ALLOW_METADATA_CONFIG",
    "RAG_SECRET_KEY",
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
        DEEPGRAM_API_KEY: Optional[str]
        DEEPGRAM_MODEL: str
        DEEPGRAM_LANGUAGE: str
        TTS_PROVIDER: str
        SARVAM_API_KEY: Optional[str]
        SARVAM_SPEAKER: str
        SARVAM_MODEL: str
        SARVAM_LANGUAGE_CODE: str
        ELEVENLABS_API_KEY: Optional[str]
        ELEVENLABS_VOICE_ID: Optional[str]
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
    DEEPGRAM_API_KEY: Optional[str]
    DEEPGRAM_MODEL: str
    DEEPGRAM_LANGUAGE: str
    TTS_PROVIDER: str
    SARVAM_API_KEY: Optional[str]
    SARVAM_SPEAKER: str
    SARVAM_MODEL: str
    SARVAM_LANGUAGE_CODE: str
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
    RAG_TIMEOUT: float
    RAG_FILLER_DELAY: float
    RAG_FILLER_MODE: str
    RAG_MAX_CONTEXT_CHARS: int
    RAG_ALLOWED_HOSTS: str
    RAG_ALLOW_INSECURE_DEV: str
    RAG_ALLOW_METADATA_CONFIG: str
    RAG_SECRET_KEY: Optional[str]
