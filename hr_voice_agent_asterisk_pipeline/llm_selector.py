"""llm_selector.py - the single place that decides which LLM the voice agent uses.

Decided ONCE per process (before the first call) and reused by every call:

  LLM_PROVIDER=auto        (default) cloud LLM if a key is set (OPENROUTER_API_KEY, then GROQ_API_KEY),
                           otherwise the local Ollama model
  LLM_PROVIDER=local       always local Ollama, even if cloud keys exist
  LLM_PROVIDER=openrouter  OpenRouter (falls back to local Ollama if the key is missing)
  LLM_PROVIDER=groq        Groq       (falls back to local Ollama if the key is missing)

When a cloud LLM is chosen, local Ollama is still wired in as the automatic fallback for outages.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger("llm_selector")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class LLMChoice:
    provider: str          # "ollama" | "openrouter" | "groq"
    model: str
    base_url: str
    api_key: str           # "" for local Ollama
    reason: str            # why this was picked (logged once at startup)
    ollama_model: str
    ollama_base_url: str

    @property
    def is_cloud(self) -> bool:
        return self.provider != "ollama"

    def describe(self) -> str:
        if self.is_cloud:
            return f"{self.provider} ({self.model}) with local Ollama ({self.ollama_model}) as outage fallback - {self.reason}"
        return f"local Ollama ({self.ollama_model}) - {self.reason}"


def _setting(name: str, default: str = "") -> str:
    import config
    return str(getattr(config, name, None) or os.getenv(name, default) or default).strip()


@lru_cache(maxsize=1)
def get_llm_choice() -> LLMChoice:
    """Resolve the LLM once and cache it, so every call in this process uses the same one."""
    ollama_model = _setting("OLLAMA_MODEL", "qwen3:8b")
    ollama_url = _setting("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    requested = (_setting("LLM_PROVIDER", "auto") or "auto").lower()

    def local(reason: str) -> LLMChoice:
        return LLMChoice("ollama", ollama_model, ollama_url, "", reason, ollama_model, ollama_url)

    def cloud(provider: str, reason: str) -> LLMChoice | None:
        key = _setting(f"{provider.upper()}_API_KEY")
        if not key:
            return None
        if provider == "openrouter":
            model = _setting("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
            return LLMChoice(provider, model, OPENROUTER_BASE_URL, key, reason, ollama_model, ollama_url)
        model = _setting("GROQ_MODEL", "llama-3.3-70b-versatile")
        return LLMChoice(provider, model, GROQ_BASE_URL, key, reason, ollama_model, ollama_url)

    if requested == "local":
        return local("LLM_PROVIDER=local")
    if requested in ("openrouter", "groq"):
        choice = cloud(requested, f"LLM_PROVIDER={requested}")
        return choice or local(f"LLM_PROVIDER={requested} but {requested.upper()}_API_KEY is not set")
    if requested != "auto":
        logger.warning("Unknown LLM_PROVIDER=%r - treating as 'auto'", requested)
    for provider in ("openrouter", "groq"):
        choice = cloud(provider, f"auto: {provider.upper()}_API_KEY is set")
        if choice:
            return choice
    return local("auto: no cloud LLM key set")


def build_llm(choice: LLMChoice | None = None):
    """Create the LLM object for one call from the (cached) choice."""
    from ollama_llm import FallbackLLM, OllamaLLM

    choice = choice or get_llm_choice()
    local = OllamaLLM(model=choice.ollama_model, base_url=choice.ollama_base_url, think=False)
    if not choice.is_cloud:
        return local
    from asterisk.llm_types import OpenAICompatibleLLM

    cloud_llm = OpenAICompatibleLLM(model=choice.model, api_key=choice.api_key, base_url=choice.base_url)
    return FallbackLLM(primary_llm=cloud_llm, fallback_llm=local)
