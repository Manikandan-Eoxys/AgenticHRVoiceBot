"""
agentConfigController.py

Runtime configuration loader and validator for the HR Voice Agent.

Responsibilities:
  - Loads and validates all environment variables required at agent start-up
  - Fails fast with clear, actionable error messages if critical config is missing
  - Provides a typed, singleton Config object that the rest of the agent reads
  - Separates optional vs. required config so the agent degrades gracefully
    when non-critical integrations (IFS Cloud, S3, SMTP) are not configured

This is the single source of truth for runtime configuration.
agent.py / services / controllers should import AgentConfig instead of
reading os.environ directly.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("controllers.agent_config")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS = {
    # LiveKit
    "LIVEKIT_URL": None,
    "LIVEKIT_API_KEY": None,
    "LIVEKIT_API_SECRET": None,
    # STT / LLM / TTS
    "DEEPGRAM_API_KEY": None,
    "DEEPGRAM_LANGUAGE": "en",
    "LIVEKIT_LLM_MODEL": "google/gemma-4-31b-it",
    "TTS_PROVIDER": "cartesia",
    "TTS_MODEL": "cartesia/sonic-3",
    "TTS_VOICE": "ec1e269e-9ca0-402f-8a18-58e0e022355a",
    # Ollama fallback
    "OLLAMA_BASE_URL": "http://localhost:11434/v1",
    "OLLAMA_MODEL": "qwen2.5:3b",
    # Telephony
    "COORDINATOR_PHONE_NUMBER": "+918925355704",
    "LIVEKIT_OUTBOUND_TRUNK_ID": "ST_ZoFAnNLxoPce",
    "SIP_CALLER_ID": "+918031451001",
    "COORDINATOR_DIAL_TIMEOUT_SECONDS": "30",
    # Recording
    "RECORDINGS_LOCAL_DIR": None,          # computed from __file__ if absent
    "RECORDINGS_S3_BUCKET": "",
    "RECORDINGS_S3_REGION": "us-east-1",
    "RECORDINGS_S3_ENDPOINT": "",
    "RECORDINGS_S3_ACCESS_KEY": "",
    "RECORDINGS_S3_SECRET_KEY": "",
    "RECORDING_COORDINATOR_MODE": "record_all",
    # Call behaviour
    "IDLE_HANGUP_SECONDS": "60.0",
    "WORKER_AGENT_NAME": "hr-voice-agent",
    # Database
    "MYSQL_HOST": "localhost",
    "MYSQL_USER": "hrbot",
    "MYSQL_PASSWORD": "",
    "MYSQL_DATABASE": "hr_voicebot",
}

# Config keys that are REQUIRED — the agent cannot function without them
_REQUIRED_KEYS = [
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
]


# ---------------------------------------------------------------------------
# Typed Config Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentRuntimeConfig:
    """Typed snapshot of all resolved configuration values."""

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # STT / LLM / TTS
    deepgram_api_key: str = ""
    deepgram_language: str = "en"
    llm_model: str = "google/gemma-4-31b-it"
    tts_provider: str = "cartesia"
    tts_model: str = "cartesia/sonic-3"
    tts_voice: str = "ec1e269e-9ca0-402f-8a18-58e0e022355a"

    # Ollama
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:3b"

    # Telephony
    coordinator_phone: str = "+918925355704"
    outbound_trunk_id: str = "ST_ZoFAnNLxoPce"
    sip_caller_id: str = "+918031451001"
    coordinator_dial_timeout: int = 30

    # Recordings
    recordings_local_dir: str = ""
    recordings_s3_bucket: str = ""
    recordings_s3_region: str = "us-east-1"
    recordings_s3_endpoint: str = ""
    recordings_s3_access_key: str = ""
    recordings_s3_secret_key: str = ""
    recording_coordinator_mode: str = "record_all"

    # Call behaviour
    idle_hangup_seconds: float = 60.0
    worker_agent_name: str = "hr-voice-agent"

    # Database
    mysql_host: str = "localhost"
    mysql_user: str = "hrbot"
    mysql_password: str = ""
    mysql_database: str = "hr_voicebot"

    # Status flags
    is_valid: bool = True
    missing_required: list = field(default_factory=list)

    def as_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# AgentConfigController
# ---------------------------------------------------------------------------

class AgentConfigController:
    """
    Loads, validates, and exposes agent runtime and builder configuration.

    Usage:
        cfg_ctrl = AgentConfigController.instance()
        runtime_config = cfg_ctrl.load()
        agent_config = cfg_ctrl.load_agent_config()

        if not runtime_config.is_valid:
            raise RuntimeError(f"Missing config: {runtime_config.missing_required}")
    """

    _instance: Optional["AgentConfigController"] = None
    _config: Optional[AgentRuntimeConfig] = None
    _agent_config: Optional[Any] = None

    @classmethod
    def instance(cls) -> "AgentConfigController":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API - Runtime Config (Environment Variables)
    # ------------------------------------------------------------------

    def load(self, env_path: Optional[Path] = None) -> AgentRuntimeConfig:
        """
        Loads config from environment (and optionally a .env file).
        Caches the result — subsequent calls return the same object.
        """
        if self._config is not None:
            return self._config

        # Load .env if a path was specified (and exists)
        if env_path and env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_path)
                logger.info(f"⚙️ [AgentConfig] Loaded .env from {env_path}")
            except ImportError:
                logger.warning("python-dotenv not installed — skipping .env load.")

        env = {k: os.environ.get(k, v) for k, v in _DEFAULTS.items()}

        missing = [k for k in _REQUIRED_KEYS if not env.get(k)]
        if missing:
            for key in missing:
                logger.error(
                    f"❌ [AgentConfig] REQUIRED environment variable '{key}' is not set."
                )

        # Compute default local recordings directory
        recordings_dir = env["RECORDINGS_LOCAL_DIR"] or str(
            Path(__file__).resolve().parents[1] / "Recordings"
        )

        config = AgentRuntimeConfig(
            livekit_url=env["LIVEKIT_URL"] or "",
            livekit_api_key=env["LIVEKIT_API_KEY"] or "",
            livekit_api_secret=env["LIVEKIT_API_SECRET"] or "",
            deepgram_api_key=env["DEEPGRAM_API_KEY"] or "",
            deepgram_language=env["DEEPGRAM_LANGUAGE"],
            llm_model=env["LIVEKIT_LLM_MODEL"],
            tts_provider=env["TTS_PROVIDER"],
            tts_model=env["TTS_MODEL"],
            tts_voice=env["TTS_VOICE"],
            ollama_base_url=env["OLLAMA_BASE_URL"],
            ollama_model=env["OLLAMA_MODEL"],
            coordinator_phone=env["COORDINATOR_PHONE_NUMBER"],
            outbound_trunk_id=env["LIVEKIT_OUTBOUND_TRUNK_ID"],
            sip_caller_id=env["SIP_CALLER_ID"],
            coordinator_dial_timeout=int(env["COORDINATOR_DIAL_TIMEOUT_SECONDS"]),
            recordings_local_dir=recordings_dir,
            recordings_s3_bucket=env["RECORDINGS_S3_BUCKET"],
            recordings_s3_region=env["RECORDINGS_S3_REGION"],
            recordings_s3_endpoint=env["RECORDINGS_S3_ENDPOINT"],
            recordings_s3_access_key=env["RECORDINGS_S3_ACCESS_KEY"],
            recordings_s3_secret_key=env["RECORDINGS_S3_SECRET_KEY"],
            recording_coordinator_mode=env["RECORDING_COORDINATOR_MODE"],
            idle_hangup_seconds=float(env["IDLE_HANGUP_SECONDS"]),
            worker_agent_name=env["WORKER_AGENT_NAME"],
            mysql_host=env["MYSQL_HOST"],
            mysql_user=env["MYSQL_USER"],
            mysql_password=env["MYSQL_PASSWORD"],
            mysql_database=env["MYSQL_DATABASE"],
            is_valid=len(missing) == 0,
            missing_required=missing,
        )

        self._config = config

        if config.is_valid:
            logger.info(
                "✅ [AgentConfig] Configuration loaded and validated. "
                f"worker_name={config.worker_agent_name}, "
                f"llm={config.llm_model}, tts={config.tts_provider}:{config.tts_model}"
            )
        else:
            logger.error(
                f"❌ [AgentConfig] Configuration INVALID. Missing: {missing}"
            )

        return config

    def get(self) -> AgentRuntimeConfig:
        """Returns the cached config. Must call load() first."""
        if self._config is None:
            return self.load()
        return self._config

    # ------------------------------------------------------------------
    # Public API - Builder Config Contract (agent_config.json)
    # ------------------------------------------------------------------

    def load_agent_config(self, config_path: Optional[Path] = None) -> Any:
        """
        Loads, validates, and parses agent_config.json into typed AgentConfig model.
        Fails fast if the file is missing or invalid against config_schema.json.
        """
        if self._agent_config is not None:
            return self._agent_config

        import json
        from models.config_models import AgentConfig

        target_path = config_path or (Path(__file__).resolve().parents[1] / "config" / "agent_config.json")
        if not target_path.exists():
            target_path = Path(__file__).resolve().parents[1] / "agent_config.json"
        schema_path = Path(__file__).resolve().parents[1] / "models" / "config_schema.json"
        if not schema_path.exists():
            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "config_schema.json"

        if not target_path.exists():
            logger.warning(f"⚠️ [AgentConfig] {target_path} not found. Creating default runtime agent config.")
            self._agent_config = AgentConfig()
            return self._agent_config

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"❌ [AgentConfig] Failed to parse {target_path}: {e}")
            raise RuntimeError(f"Invalid JSON in {target_path}: {e}")

        # Validate against schema if jsonschema is available
        if schema_path.exists():
            try:
                import jsonschema
                with open(schema_path, "r", encoding="utf-8") as sf:
                    schema = json.load(sf)
                jsonschema.validate(instance=data, schema=schema)
                logger.info(f"✅ [AgentConfig] Validated {target_path.name} against schema.")
            except ImportError:
                logger.debug("jsonschema not installed — skipping strict validation.")
            except Exception as ve:
                logger.error(f"❌ [AgentConfig] Schema validation error on {target_path.name}: {ve}")
                raise RuntimeError(f"Config schema validation failed: {ve}")

        agent_cfg = AgentConfig.from_dict(data)
        self._agent_config = agent_cfg
        logger.info(
            f"🎯 [AgentConfig] Loaded AgentConfig: '{agent_cfg.agent_name}' ({agent_cfg.agent_id}) "
            f"Goals={len(agent_cfg.goals)}, Enabled={len(agent_cfg.get_enabled_goals())}"
        )
        return agent_cfg

    def get_agent_config(self) -> Any:
        """Returns the loaded AgentConfig model."""
        if self._agent_config is None:
            return self.load_agent_config()
        return self._agent_config

    def save_agent_config_to_db(self, agent_config: Any) -> bool:
        """Mirrors active agent config to MySQL agent_config table."""
        import json
        try:
            from database.db import get_db
            with get_db() as db:
                # Ensure table exists
                db.cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_config (
                        config_id INT AUTO_INCREMENT PRIMARY KEY,
                        agent_id VARCHAR(64) NOT NULL,
                        agent_name VARCHAR(150) NOT NULL,
                        version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
                        config_json LONGTEXT NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        KEY idx_agent_id (agent_id),
                        KEY idx_is_active (is_active)
                    )
                    """
                )
                # Deactivate previous active configs for this agent
                db.cursor.execute(
                    "UPDATE agent_config SET is_active = FALSE WHERE agent_id = %s",
                    (agent_config.agent_id,)
                )
                # Insert new active version
                db.cursor.execute(
                    """
                    INSERT INTO agent_config (agent_id, agent_name, version, config_json, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    """,
                    (
                        agent_config.agent_id,
                        agent_config.agent_name,
                        agent_config.schema_version,
                        json.dumps(agent_config.to_dict(), indent=2),
                    )
                )
                db.conn.commit()
                logger.info(f"💾 [AgentConfig] Successfully mirrored config to DB for {agent_config.agent_id}")
                return True
        except Exception as e:
            logger.warning(f"⚠️ [AgentConfig] Could not mirror config to DB: {e}")
            return False

    def reload(self) -> AgentRuntimeConfig:
        """Clears cache and reloads config from environment and file."""
        self._config = None
        self._agent_config = None
        AgentConfigController._instance = None
        return self.load()

    def print_summary(self) -> None:
        """Prints a human-readable config summary (redacts secrets)."""
        cfg = self.get()
        logger.info("📋 [AgentConfig] Runtime configuration summary:")
        logger.info(f"  LiveKit URL    : {cfg.livekit_url}")
        logger.info(f"  LLM Model      : {cfg.llm_model}")
        logger.info(f"  TTS Provider   : {cfg.tts_provider} / {cfg.tts_model}")
        logger.info(f"  STT Language   : {cfg.deepgram_language}")
        logger.info(f"  Ollama Fallback: {cfg.ollama_model} @ {cfg.ollama_base_url}")
        logger.info(f"  Coordinator    : {cfg.coordinator_phone}")
        logger.info(f"  Trunk ID       : {cfg.outbound_trunk_id}")
        logger.info(f"  Recordings Dir : {cfg.recordings_local_dir}")
        logger.info(f"  S3 Bucket      : {cfg.recordings_s3_bucket or '(disabled)'}")
        logger.info(f"  Idle Hangup    : {cfg.idle_hangup_seconds}s")
        logger.info(f"  Worker Name    : {cfg.worker_agent_name}")

