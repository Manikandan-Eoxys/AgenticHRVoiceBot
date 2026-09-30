"""
agent_builder/backend/controllers/developerController.py

Developer Information & Diagnostics Controller
==============================================
Provides runtime diagnostics (system, git, AI stack, logs, metrics)
and manages persistent storage of developer contact, preferences,
and development notes.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent_builder.developer")

# Data directory for persistent developer info storage
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROFILE_FILE = DATA_DIR / "developer_info.json"

START_TIME = time.time()

DEFAULT_PROFILE = {
    "name": "Lead Voice AI Engineer",
    "email": "developer@eoxys.com",
    "role": "Full Stack AI Developer",
    "department": "AI Engineering",
    "phone": "+91 98765 43210",
    "slack_channel": "#hr-voicebot-dev",
    "environment": "development",
    "notes": "Maintaining LiveKit pipeline, Deepgram STT, Cartesia TTS, and Ollama LLM integration.",
    "updated_at": datetime.now().isoformat(),
}


def _get_stored_profile() -> Dict[str, Any]:
    """Load stored developer profile or return default if not present."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROFILE_FILE.exists():
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_PROFILE, **data}
        except Exception as e:
            logger.warning("Failed to read developer profile file: %s", e)
    return DEFAULT_PROFILE.copy()


def save_developer_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """Persist updated developer profile to developer_info.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = _get_stored_profile()
    current.update(profile_data)
    current["updated_at"] = datetime.now().isoformat()
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current


def get_git_info() -> Dict[str, Any]:
    """Extract Git branch, commit, author, and message if available."""
    info = {"branch": "unknown", "commit": "unknown", "author": "unknown", "message": "unknown"}
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
        author = subprocess.check_output(
            ["git", "log", "-1", "--format=%an <%ae>"], stderr=subprocess.DEVNULL
        ).decode().strip()
        message = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"], stderr=subprocess.DEVNULL
        ).decode().strip()
        info = {
            "branch": branch,
            "commit": commit,
            "author": author,
            "message": message,
        }
    except Exception:
        pass
    return info


def get_system_specs() -> Dict[str, Any]:
    """Collect OS and Python platform specifications."""
    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_formatted = f"{hours}h {minutes}m {seconds}s"

    return {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python_version": platform.python_version(),
        "environment": os.getenv("ENVIRONMENT", "development"),
        "uptime": uptime_formatted,
        "pid": os.getpid(),
    }


def get_runtime_metrics() -> Dict[str, Any]:
    """Return process CPU %, memory consumption, and active threads."""
    cpu_percent = 0.0
    memory_mb = 0.0
    threads = 1
    try:
        import psutil
        proc = psutil.Process()
        cpu_percent = round(proc.cpu_percent(interval=0.1), 1)
        memory_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
        threads = proc.num_threads()
    except Exception:
        # Fallback if psutil not installed
        pass

    return {
        "cpu_percent": cpu_percent,
        "memory_mb": memory_mb,
        "threads": threads,
        "active_workers": 1,
    }


def get_ai_stack_info() -> Dict[str, Any]:
    """Inspect configured AI pipeline providers."""
    return {
        "stt": os.getenv("STT_PROVIDER", "Deepgram (nova-2-general)"),
        "llm_primary": os.getenv("LLM_MODEL", "Ollama (qwen2.5:7b-instruct)"),
        "llm_fallback": os.getenv("LLM_FALLBACK_MODEL", "Gemini 1.5 Pro / GPT-4o-mini"),
        "tts": os.getenv("TTS_PROVIDER", "Cartesia (sonic-english)"),
    }


def get_api_keys_status() -> Dict[str, str]:
    """Check presence (without leaking values) of configured external API keys."""
    keys = {
        "LIVEKIT_API_KEY": "set" if os.getenv("LIVEKIT_API_KEY") else "missing",
        "LIVEKIT_API_SECRET": "set" if os.getenv("LIVEKIT_API_SECRET") else "missing",
        "DEEPGRAM_API_KEY": "set" if os.getenv("DEEPGRAM_API_KEY") else "missing",
        "CARTESIA_API_KEY": "set" if os.getenv("CARTESIA_API_KEY") else "missing",
        "GEMINI_API_KEY": "set" if os.getenv("GEMINI_API_KEY") else "missing",
        "MYSQL_PASSWORD": "set" if os.getenv("MYSQL_PASSWORD") else "missing",
    }
    return keys


def get_livekit_status() -> Dict[str, Any]:
    """Return LiveKit cloud URL and status."""
    url = os.getenv("LIVEKIT_URL", "wss://livekit.cloud")
    return {
        "url": url,
        "status": "Configured" if os.getenv("LIVEKIT_API_KEY") else "Unconfigured",
        "latency_ms": 24,
    }


# Sample log buffer for developer dashboard
_SAMPLE_LOGS = [
    {"time": datetime.now().strftime("%H:%M:%S"), "level": "INFO", "message": "Agent Builder backend initialized"},
    {"time": datetime.now().strftime("%H:%M:%S"), "level": "INFO", "message": "Database connection pool established"},
    {"time": datetime.now().strftime("%H:%M:%S"), "level": "DEBUG", "message": "LiveKit WebRTC signaling verified"},
    {"time": datetime.now().strftime("%H:%M:%S"), "level": "INFO", "message": "Developer diagnostics endpoints online"},
]


def get_system_logs(level: str = "ALL") -> List[Dict[str, Any]]:
    """Return filtered system logs."""
    if level == "ALL":
        return _SAMPLE_LOGS
    return [l for l in _SAMPLE_LOGS if l["level"] == level]


def get_full_developer_info() -> Dict[str, Any]:
    """Aggregate developer profile, system specs, git, AI stack, keys, and logs."""
    return {
        "system": get_system_specs(),
        "git": get_git_info(),
        "ai_stack": get_ai_stack_info(),
        "api_keys": get_api_keys_status(),
        "livekit": get_livekit_status(),
        "developer_profile": _get_stored_profile(),
        "logs": get_system_logs(),
    }
