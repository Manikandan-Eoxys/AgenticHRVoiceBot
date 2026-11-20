"""webcall/settings.py - environment-driven settings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def webcall_enabled() -> bool:
    return _flag("WEBCALL_ENABLED")


@dataclass(frozen=True)
class WebCallSettings:
    host: str = "0.0.0.0"
    port: int = 8443
    cert_file: str = ""
    key_file: str = ""
    static_dir: Path = PIPELINE_DIR / "web"
    audiosocket_host: str = "127.0.0.1"
    audiosocket_port: int = 9090
    max_calls: int = 2
    per_ip_max: int = 1
    offers_per_minute: int = 6
    max_seconds: int = 300
    connect_timeout: int = 20
    allowed_origins: tuple = ()
    trust_proxy: bool = False
    ice_servers: tuple = ()

    @classmethod
    def from_env(cls) -> "WebCallSettings":
        try:
            raw = json.loads(os.getenv("WEBCALL_ICE_SERVERS", "[]") or "[]")
        except ValueError:
            raw = []
        ice = tuple(
            {k: s[k] for k in ("urls", "username", "credential") if k in s}
            for s in raw if isinstance(s, dict) and "urls" in s
        )
        origins = tuple(o.strip() for o in os.getenv("WEBCALL_ALLOWED_ORIGINS", "").split(",") if o.strip())
        return cls(
            host=os.getenv("WEBCALL_HOST", "0.0.0.0"),
            port=_int("WEBCALL_PORT", 8443),
            cert_file=os.getenv("WEBCALL_CERT_FILE", ""),
            key_file=os.getenv("WEBCALL_KEY_FILE", ""),
            static_dir=Path(os.getenv("WEBCALL_STATIC_DIR", str(PIPELINE_DIR / "web"))),
            audiosocket_host=os.getenv("WEBCALL_AUDIOSOCKET_HOST", "127.0.0.1"),
            audiosocket_port=_int("AST_BRIDGE_PORT", 9090),
            max_calls=_int("WEBCALL_MAX_CALLS", 2),
            per_ip_max=_int("WEBCALL_PER_IP_MAX", 1),
            offers_per_minute=_int("WEBCALL_OFFERS_PER_MINUTE", 6),
            max_seconds=_int("WEBCALL_MAX_SECONDS", 300),
            connect_timeout=_int("WEBCALL_CONNECT_TIMEOUT", 20),
            allowed_origins=origins,
            trust_proxy=_flag("WEBCALL_TRUST_PROXY"),
            ice_servers=ice,
        )
