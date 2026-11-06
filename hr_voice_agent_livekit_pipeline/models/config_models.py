"""
models/config_models.py

Strongly-typed data models for AgentBuilder configuration contract.
Matches config_schema.json version 1.0.0.
"""

from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _resolve_secret(value: Optional[str]) -> str:
    """
    Resolves secret references like ${ENV_VAR} or env:ENV_VAR.
    Never stores plaintext credentials in configuration files.
    """
    if not value or not isinstance(value, str):
        return ""
    # Matches ${VAR_NAME}
    match = re.match(r"^\$\{([A-Za-z0-9_]+)\}$", value.strip())
    if match:
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    # Matches env:VAR_NAME or ref:VAR_NAME
    if value.startswith("env:") or value.startswith("ref:"):
        var_name = value.split(":", 1)[1].strip()
        return os.environ.get(var_name, "")
    return value


@dataclass
class DataSourceConfig:
    source_type: str = "none"  # "sql_table" | "rest_api" | "none"
    table_name: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    filter_column: Optional[str] = None
    api_url: Optional[str] = None
    method: str = "GET"
    auth_type: str = "none"  # "none" | "bearer" | "api_key" | "basic"
    auth_header: Optional[str] = None
    secret_ref: Optional[str] = None

    @property
    def resolved_secret(self) -> str:
        return _resolve_secret(self.secret_ref)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DataSourceConfig":
        if not data:
            return cls()
        return cls(
            source_type=data.get("sourceType", "none"),
            table_name=data.get("tableName"),
            columns=data.get("columns", []) or [],
            filter_column=data.get("filterColumn"),
            api_url=data.get("apiUrl"),
            method=data.get("method", "GET"),
            auth_type=data.get("authType", "none"),
            auth_header=data.get("authHeader"),
            secret_ref=data.get("secretRef"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sourceType": self.source_type,
            "tableName": self.table_name,
            "columns": self.columns,
            "filterColumn": self.filter_column,
            "apiUrl": self.api_url,
            "method": self.method,
            "authType": self.auth_type,
            "authHeader": self.auth_header,
            "secretRef": self.secret_ref,
        }


@dataclass
class ActionConfig:
    id: str
    goal_id: str
    function_name: str
    trigger: str = ""
    instruction: str = ""
    parameters: Any = None
    enabled: bool = True
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionConfig":
        return cls(
            id=data.get("id", ""),
            goal_id=data.get("goalId", ""),
            function_name=data.get("functionName", ""),
            trigger=data.get("trigger", ""),
            instruction=data.get("instruction", ""),
            parameters=data.get("parameters"),
            enabled=data.get("enabled", True),
            data_source=DataSourceConfig.from_dict(data.get("dataSource")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goalId": self.goal_id,
            "functionName": self.function_name,
            "trigger": self.trigger,
            "instruction": self.instruction,
            "parameters": self.parameters,
            "enabled": self.enabled,
            "dataSource": self.data_source.to_dict(),
        }


@dataclass
class GoalConfig:
    id: str
    name: str
    description: str = ""
    trigger_phrases: List[str] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalConfig":
        return cls(
            id=data.get("id", ""),
            name=data.get("name") or data.get("title", ""),
            description=data.get("description", "") or data.get("desc", ""),
            trigger_phrases=data.get("trigger_phrases", []) or [],
            enabled=data.get("enabled", True),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trigger_phrases": self.trigger_phrases,
            "enabled": self.enabled,
        }


@dataclass
class VoiceConfig:
    llm_provider: str = "ollama"
    llm_model: str = "google/gemma-4-31b-it"
    llm_endpoint: str = ""
    stt_provider: str = "deepgram"
    stt_language: str = "en"
    tts_provider: str = "cartesia"
    tts_model: str = "cartesia/sonic-3"
    tts_voice: str = "ec1e269e-9ca0-402f-8a18-58e0e022355a"
    temperature: float = 0.7

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "VoiceConfig":
        if not data:
            return cls()
        return cls(
            llm_provider=data.get("llmProvider", "ollama"),
            llm_model=data.get("llmModel", "google/gemma-4-31b-it"),
            llm_endpoint=data.get("llmEndpoint", ""),
            stt_provider=data.get("sttProvider", "deepgram"),
            stt_language=data.get("sttLanguage", "en"),
            tts_provider=data.get("ttsProvider", "cartesia"),
            tts_model=data.get("ttsModel", "cartesia/sonic-3"),
            tts_voice=data.get("ttsVoice", "ec1e269e-9ca0-402f-8a18-58e0e022355a"),
            temperature=float(data.get("temperature", 0.7)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llmProvider": self.llm_provider,
            "llmModel": self.llm_model,
            "llmEndpoint": self.llm_endpoint,
            "sttProvider": self.stt_provider,
            "sttLanguage": self.stt_language,
            "ttsProvider": self.tts_provider,
            "ttsModel": self.tts_model,
            "ttsVoice": self.tts_voice,
            "temperature": self.temperature,
        }


@dataclass
class TelephonyConfig:
    outbound_trunk_id: str = "ST_ZoFAnNLxoPce"
    coordinator_phone: str = "+918925355704"
    sip_caller_id: str = "+918031451001"
    inactivity_timeout_sec: float = 60.0
    dtmf_fallback: bool = True

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TelephonyConfig":
        if not data:
            return cls()
        return cls(
            outbound_trunk_id=data.get("outboundTrunkId", "ST_ZoFAnNLxoPce"),
            coordinator_phone=data.get("coordinatorPhone", "+918925355704"),
            sip_caller_id=data.get("sipCallerId", "+918031451001"),
            inactivity_timeout_sec=float(data.get("inactivityTimeoutSec", 60.0)),
            dtmf_fallback=bool(data.get("dtmfFallback", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outboundTrunkId": self.outbound_trunk_id,
            "coordinatorPhone": self.coordinator_phone,
            "sipCallerId": self.sip_caller_id,
            "inactivityTimeoutSec": self.inactivity_timeout_sec,
            "dtmfFallback": self.dtmf_fallback,
        }


@dataclass
class AgentConfig:
    schema_version: str = "1.0.0"
    agent_id: str = "hr_voice_agent"
    agent_name: str = "HR Voice Assistant"
    description: str = "Automated voice agent for employee HR services"
    status: str = "active"
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    telephony: TelephonyConfig = field(default_factory=TelephonyConfig)
    goals: List[GoalConfig] = field(default_factory=list)
    actions: List[ActionConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        return cls(
            schema_version=data.get("schemaVersion", "1.0.0"),
            agent_id=data.get("agentId") or data.get("id", "hr_voice_agent"),
            agent_name=data.get("agentName") or data.get("name", "HR Voice Assistant"),
            description=data.get("description", ""),
            status=data.get("status", "active"),
            voice=VoiceConfig.from_dict(data.get("voice")),
            telephony=TelephonyConfig.from_dict(data.get("telephony")),
            goals=[GoalConfig.from_dict(g) for g in data.get("goals", [])],
            actions=[ActionConfig.from_dict(a) for a in data.get("actions", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "agentId": self.agent_id,
            "agentName": self.agent_name,
            "description": self.description,
            "status": self.status,
            "voice": self.voice.to_dict(),
            "telephony": self.telephony.to_dict(),
            "goals": [g.to_dict() for g in self.goals],
            "actions": [a.to_dict() for a in self.actions],
        }

    def get_enabled_goals(self) -> List[GoalConfig]:
        return [g for g in self.goals if g.enabled]

    def get_actions_for_goal(self, goal_id: str, enabled_only: bool = True) -> List[ActionConfig]:
        return [
            a for a in self.actions
            if a.goal_id == goal_id and (not enabled_only or a.enabled)
        ]
